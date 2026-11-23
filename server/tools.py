"""LKG 2.0 as tools: what a model (or any MCP client) can ask the knowledge graph.

The same four functions serve two uses:
  - scenario 3b ("graph-tools" in app.py): a local model calls them itself, through Ollama's tool calling;
  - server/mcp_server.py: they are offered to any MCP client (Claude Desktop, an IDE, another agent).

    search_articles(query, law=None)              articles that match a question (words + meaning, like scenario 2)
    get_article(law, article, date=None, ...)     an article's text, today or as it read on a given date, with its versions
    get_law(law)                                  a law's record: rank, scope, status, amended or repealed by, versions
    get_links(law, article)                       articles an article refers to, and laws officially linked to its law

Laws can be named the way people name them ("Código Civil", "ET", "Ley 29/1994") or by BOE id (BOE-A-1889-4763).
Every function returns plain Spanish text for the model, plus a list of the articles it showed (for the record).
"""
import gzip
import json
import re
from collections import OrderedDict

import retrieve
from retrieve import JUR_NAMES, STATUS_ES, best_window, norm

MAX_CHARS = 3500          # longest article text returned by one call
SNIPPET = 500             # characters of each search result

ORD_UNITS = ["", "primera", "segunda", "tercera", "cuarta", "quinta", "sexta", "séptima", "octava", "novena"]
ORD_TENS = {10: "décima", 20: "vigésima", 30: "trigésima", 40: "cuadragésima", 50: "quincuagésima"}
ORD_TEENS = {11: "undécima", 12: "duodécima"}
# Extra short names for the tools (the search's own list is in retrieve.ALIASES and is not changed here,
# so scenarios 2 and 3a behave exactly as before).
EXTRA_ALIASES = {"lsv": "BOE-A-2015-11722", "ley de trafico": "BOE-A-2015-11722", "rgc": "BOE-A-2003-23514",
                 "reglamento general de circulacion": "BOE-A-2003-23514", "lsc": "BOE-A-2010-10544",
                 "ley de sociedades de capital": "BOE-A-2010-10544", "lgdcu": "BOE-A-2007-20555", "let": "BOE-A-2015-11430",
                 "trlet": "BOE-A-2015-11430", "lopdgdd": "BOE-A-2018-16673", "lrjs": "BOE-A-2011-15936"}
RANK_LABEL = {"ley": "Ley", "ley_organica": "Ley Orgánica", "real_decreto_ley": "Real Decreto-ley", "real_decreto": "Real Decreto",
              "real_decreto_legislativo": "Real Decreto Legislativo", "orden": "Orden", "decreto": "Decreto", "decreto_ley": "Decreto-ley"}
KINDS = {"da": "Disposición adicional", "dt": "Disposición transitoria", "df": "Disposición final", "dd": "Disposición derogatoria"}

TOOL_SPECS = [
    {"name": "search_articles",
     "description": "Busca artículos de la legislación española que respondan a una pregunta. Devuelve los mejores "
                    "artículos con un extracto. Úsalo primero si no sabes qué norma o artículo aplica. Con «date» los "
                    "extractos muestran el texto vigente en esa fecha.",
     "parameters": {"type": "object", "properties": {
         "query": {"type": "string", "description": "La pregunta o los términos jurídicos a buscar, en español."},
         "law": {"type": "string", "description": "Opcional: limitar a una norma, por nombre o número (p. ej. «Estatuto de los Trabajadores», «Ley 29/1994»)."},
         "date": {"type": "string", "description": "Opcional: fecha AAAA-MM-DD; los extractos muestran la versión vigente en esa fecha."}},
         "required": ["query"]}},
    {"name": "get_article",
     "description": "Devuelve el texto de un artículo de una norma, con la lista de sus versiones (fechas y normas que lo modificaron). "
                    "Con «date» devuelve el texto tal como estaba en esa fecha: úsalo si la pregunta se refiere a otra época "
                    "(«antes de la reforma de 2015», «en 2018»...).",
     "parameters": {"type": "object", "properties": {
         "law": {"type": "string", "description": "La norma, por nombre, abreviatura o número (p. ej. «Código Civil», «LAU», «Ley 39/2015»)."},
         "article": {"type": "string", "description": "El artículo: «1964», «9 bis», «disposición adicional vigésima octava», «anexo II»."},
         "date": {"type": "string", "description": "Opcional: fecha AAAA-MM-DD para obtener la versión vigente en esa fecha."},
         "apartado": {"type": "string", "description": "Opcional: número de apartado, para artículos largos (p. ej. «2»)."}},
         "required": ["law", "article"]}},
    {"name": "get_law",
     "description": "Ficha de una norma: rango, ámbito (estatal o autonómica), si está vigente o derogada, qué normas la "
                    "modificaron o derogaron, y desde cuándo hay versiones.",
     "parameters": {"type": "object", "properties": {
         "law": {"type": "string", "description": "La norma, por nombre, abreviatura o número."}},
         "required": ["law"]}},
    {"name": "get_links",
     "description": "Artículos a los que remite el texto de un artículo (p. ej. «en los términos del artículo 41»), y normas "
                    "oficialmente relacionadas con su norma. Útil cuando la respuesta depende de otro artículo.",
     "parameters": {"type": "object", "properties": {
         "law": {"type": "string", "description": "La norma del artículo."},
         "article": {"type": "string", "description": "El artículo."}},
         "required": ["law", "article"]}},
]


def ordinal(n):
    """Feminine Spanish ordinal as used in disposition names: 28 -> 'vigésima octava'."""
    if n < 10:
        return ORD_UNITS[n]
    if n in ORD_TEENS:
        return ORD_TEENS[n]
    if 13 <= n <= 19:
        return "décima " + ORD_UNITS[n - 10]
    tens, unit = n - n % 10, n % 10
    return ORD_TENS.get(tens, str(n)) + (" " + ORD_UNITS[unit] if unit else "")


def unit_candidates(article):
    """Ways an article may be named in the data, normalised: '9 bis' -> ['articulo 9 bis'], 'DA 28' -> [...]."""
    a = norm(article).strip().rstrip(".")
    a = re.sub(r"^(art(iculo)?\.?\s*)", "", a).strip()
    m = re.fullmatch(r"(da|dt|df|dd)\.?\s*(\d+)", a)
    if m:
        a = f"{norm(KINDS[m.group(1)])} {norm(ordinal(int(m.group(2))))}"
    m = re.fullmatch(r"(disposicion \w+)\s+(\d+)", a)
    if m:
        a = f"{m.group(1)} {norm(ordinal(int(m.group(2))))}"
    if re.match(r"disposicion \w+ (vigesima|trigesima|cuadragesima|decima) \w+$", a):   # also 'vigesimoctava', as the BOE writes it
        kind, first, second = a.rsplit(" ", 2)
        joined = (first[:-1] + "o" + second).replace("oo", "o")
        return [a, f"{kind} {joined}"]
    if re.fullmatch(r"\d+(\s*(bis|ter|quater|quinquies|sexies))?", a):
        return ["articulo " + re.sub(r"\s+", " ", a)]
    if re.fullmatch(r"(primero|segundo|tercero|cuarto|quinto|sexto|septimo|octavo|noveno|decimo|unico)", a):
        return ["articulo " + a]
    return [a]


def unit_key(name):
    return re.sub(r"\s+", " ", norm(name).replace(".", " ")).strip()


def clean(text):
    text = re.sub(r"\\([.\-*_()\[\]#+])", r"\1", text)   # markdown escapes in the history texts ("1\\. La acción")
    return re.sub(r"^#+\s*", "", text, flags=re.M).strip()


class Tools:
    def __init__(self, index, root, embed=None):
        """index: retrieve.Index; root: the lkg2 folder (for data/hist); embed: text -> vector, or None (words only)."""
        self.ix = index
        self.root = root
        self.embed = embed
        raw = json.load(open(root / "data" / "laws.json", encoding="utf-8"))
        self.bucket = {r[0]: r[16] for r in raw["laws"] if len(r) > 16 and r[16]}
        hist_ix = root / "data" / "hist" / "index.json"
        self.hist_index = json.load(open(hist_ix, encoding="utf-8")) if hist_ix.exists() else {}
        self._packs = OrderedDict()   # a few unpacked history files (each holds hundreds of laws)
        self._hist = {}

    # --- helpers ---------------------------------------------------------------------------------
    def law_id(self, name):
        name = (name or "").strip()
        if re.fullmatch(r"BOE-[A-Z]-\d{4}-\d+", name):
            return name if name in self.ix.laws else None
        key = norm(name).strip(" .")
        if key in EXTRA_ALIASES and EXTRA_ALIASES[key] in self.ix.laws:
            return EXTRA_ALIASES[key]
        return self.ix.resolve(name)

    def law_name(self, lid):
        law = self.ix.laws.get(lid, {})
        return law.get("title", lid)

    def history(self, lid):
        """The law's article history (data/hist), or None if it has only its original text."""
        if lid in self._hist:
            return self._hist[lid]
        if lid not in self.hist_index or lid not in self.bucket:
            return None
        k = self.bucket[lid][0]
        if k not in self._packs:
            with gzip.open(self.root / "data" / "hist" / f"{k}.json.gz", "rt", encoding="utf-8") as f:
                self._packs[k] = json.load(f)
            while len(self._packs) > 2:
                self._packs.popitem(last=False)
        self._packs.move_to_end(k)
        self._hist[lid] = self._packs[k].get(lid)
        return self._hist[lid]

    def find_unit(self, names, article):
        """The name, among `names`, of the article asked for (or None)."""
        keyed = {unit_key(n): n for n in names}
        for cand in unit_candidates(article):
            if cand in keyed:
                return keyed[cand]
        for cand in unit_candidates(article):   # 'artículo 9' should not match 'artículo 9 bis', but 'anexo ii' may be 'anexo ii. ...'
            hits = [n for k, n in keyed.items() if k.startswith(cand + " ") and not re.match(r"\s*(bis|ter|quater)\b", k[len(cand):])]
            if len(hits) == 1:
                return hits[0]
        return None

    def version_line(self, v):
        by = v.get("by")
        if not by:
            return f"{v['d']} (texto original)"
        law = self.ix.laws.get(by)
        label = (law.get("num") and f"{RANK_LABEL.get(law['rank'], law['rank'].replace('_', ' ').capitalize())} {law['num']}") if law else None
        return f"{v['d']} ({label or by})"

    def pick_apartado(self, text, apartado):
        paras = [p for p in text.split("\n\n") if p.strip()]
        start = next((i for i, p in enumerate(paras) if re.match(rf"{re.escape(apartado)}\.\s", p.strip())), None)
        if start is None:
            return None
        end = next((j for j in range(start + 1, len(paras)) if re.match(r"\d+\.\s", paras[j].strip())), len(paras))
        return paras[0] + "\n\n" + "\n\n".join(paras[start:end])

    # --- the four tools --------------------------------------------------------------------------
    def text_at(self, lid, unit, date):
        """(text, since) of an article as it read on `date` from its history; (None, None) if there is no history,
        ("", None) if the article did not exist yet."""
        H = self.history(lid)
        if not H:
            return None, None
        name = self.find_unit(H["units"].keys(), unit)
        if not name:
            return None, None
        chosen = ("", None)
        for vi, ti in H["units"][name]:
            v = H["versions"][vi]
            if v["d"] <= date:
                chosen = (H["texts"][ti], v["d"])
        return chosen

    def search_articles(self, query, law=None, date=None):
        hints = [law] if law else []
        qvec = self.embed(query) if self.embed else None
        units, terms = self.ix.search(query, k=6, hints=hints, qvec=qvec)
        if law:
            lid = self.law_id(law)
            if lid:
                units = [u for u in units if u["law"] == lid] or units
        if not units:
            return "No se ha encontrado ningún artículo.", []
        if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            date = None
        lines, seen = [], []
        for u in units:
            L = self.ix.laws.get(u["law"], {})
            text, when = u["text"], ""
            if date:   # the search finds articles by today's text; show each as it read on the date asked
                old, since = self.text_at(u["law"], u["unit"], date)
                if old is None:
                    when = " (sin historial: texto actual)"
                elif old == "":
                    when = f" (no existía el {date})"
                    text = ""
                else:
                    text, when = old, f" (versión vigente el {date}, desde {since})"
            snip = best_window(clean(text), terms, SNIPPET) if text else ""
            lines.append(f"- {L.get('title', u['law'])} ({u['law']}) — {u['unit']}{when} "
                         f"[{JUR_NAMES.get(L.get('jur'), L.get('jur'))}, {STATUS_ES.get(L.get('status'), 'otro')}]\n  {snip}")
            seen.append({"law": u["law"], "unit": u["unit"], "via": "search_articles" + (f" {date}" if date else "")})
        return "Resultados" + (f" (textos vigentes el {date})" if date else "") + ":\n" + "\n".join(lines), seen

    def get_article(self, law, article, date=None, apartado=None):
        lid = self.law_id(law)
        if not lid:
            return f"No encuentro la norma «{law}». Prueba con su número (p. ej. «Ley 29/1994») o usa search_articles.", []
        title = self.law_name(lid)
        H = self.history(lid)
        text, versions_info, unit = None, "", None
        if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            return "La fecha debe tener el formato AAAA-MM-DD.", []
        if H:
            unit = self.find_unit(H["units"].keys(), article)
            if unit:
                changes = H["units"][unit]
                vers = [H["versions"][vi] for vi, _ in changes]
                versions_info = "Versiones del artículo: " + "; ".join(self.version_line(v) for v in vers) + "."
                if date:
                    chosen = None
                    for (vi, ti), v in zip(changes, vers):
                        if v["d"] <= date:
                            chosen = (ti, v)
                    if chosen is None:
                        return (f"{title} ({lid}), {unit}: el artículo no existía todavía el {date}. {versions_info}",
                                [{"law": lid, "unit": unit, "via": f"get_article {date}"}])
                    ti, v = chosen
                    nxt = next((w["d"] for w in vers if w["d"] > v["d"]), None)
                    head = f"{title} ({lid}), {unit} — versión aplicable el {date}: vigente desde {v['d']}" + (f" hasta el día antes de {nxt}" if nxt else " (sigue vigente)")
                else:
                    ti, v = changes[-1][1], vers[-1]
                    head = f"{title} ({lid}), {unit} — versión vigente (desde {v['d']})"
                text = H["texts"][ti]
        if text is None:   # no history: current text from the search database
            if date:
                versions_info = "No hay historial de versiones de esta norma en LKG 2.0; este es el texto actual."
            names = [r[0] for r in self.ix.db.execute("SELECT unit FROM units WHERE law = ?", (lid,))]
            unit = self.find_unit(names, article)
            if not unit:
                return f"{title} ({lid}) no tiene un «{article}». Usa search_articles para encontrar el artículo correcto.", []
            row = self.ix.db.execute("SELECT id FROM units WHERE law = ? AND unit = ?", (lid, unit)).fetchone()
            u = self.ix._unit(row[0])
            text, head = u["text"], f"{title} ({lid}), {unit} — versión vigente" + (f" (desde {u['since']})" if u.get("since") else "")
        if text is None or not unit:
            return f"{title} ({lid}) no tiene un «{article}». Usa search_articles para encontrar el artículo correcto.", []
        text = clean(text)
        if apartado:
            part = self.pick_apartado(text, str(apartado).strip().rstrip("."))
            text = part or text
        if len(text) > MAX_CHARS:
            nums = re.findall(r"(?m)^(\d+)\.\s", text)
            text = text[:MAX_CHARS] + "\n[…] (texto recortado" + (f"; apartados: {', '.join(dict.fromkeys(nums))}; pide uno con «apartado»" if nums else "") + ")"
        L = self.ix.laws.get(lid, {})
        status = f"Norma {STATUS_ES.get(L.get('status'), 'otro')}, ámbito {JUR_NAMES.get(L.get('jur'), L.get('jur'))}."
        return "\n".join(x for x in [head, status, versions_info, "", text] if x is not None), \
            [{"law": lid, "unit": unit, "via": "get_article" + (f" {date}" if date else "")}]

    def get_law(self, law):
        lid = self.law_id(law)
        if not lid:
            return f"No encuentro la norma «{law}».", []
        rec = self.ix.law_record(lid)
        h = self.hist_index.get(lid)
        if h:
            rec += f"\nHistorial en LKG 2.0: {h.get('versions')} versiones consolidadas entre {h.get('y0')} y {h.get('y1')}."
        n = self.ix.db.execute("SELECT count(*) FROM units WHERE law = ?", (lid,)).fetchone()[0]
        if n:
            rec += f"\nUnidades (artículos, disposiciones, anexos) en la base de búsqueda: {n}."
        return rec, []

    def get_links(self, law, article):
        lid = self.law_id(law)
        if not lid:
            return f"No encuentro la norma «{law}».", []
        names = [r[0] for r in self.ix.db.execute("SELECT unit FROM units WHERE law = ?", (lid,))]
        unit = self.find_unit(names, article)
        if not unit:
            return f"{self.law_name(lid)} ({lid}) no tiene un «{article}».", []
        row = self.ix.db.execute("SELECT id FROM units WHERE law = ? AND unit = ?", (lid, unit)).fetchone()
        u = self.ix._unit(row[0])
        lines = []
        for tl, number in self.ix.cited_in(u)[:15]:
            where = "esta misma norma" if tl == lid else f"{self.law_name(tl)} ({tl})"
            lines.append(f"- artículo {number} de {where}")
        out = f"{self.law_name(lid)} ({lid}), {unit}.\nRemite a:\n" + ("\n".join(lines) if lines else "- (ninguna remisión a otros artículos)")
        linked = self.ix.linked_laws(lid)[:8]
        if linked:
            out += "\nNormas relacionadas oficialmente con esta norma:\n" + "\n".join(f"- {label}: {self.law_name(t)} ({t})" for t, label in linked)
        return out, []

    def call(self, name, args):
        """Run a tool by name with a dict of arguments; returns (text for the model, articles shown)."""
        fn = {"search_articles": self.search_articles, "get_article": self.get_article,
              "get_law": self.get_law, "get_links": self.get_links}.get(name)
        if fn is None:
            return f"Herramienta desconocida: {name}. Usa search_articles, get_article, get_law o get_links.", []
        args = {k: (str(v) if v is not None else None) for k, v in (args or {}).items() if k in fn.__code__.co_varnames}
        try:
            return fn(**args)
        except TypeError as e:
            return f"Argumentos incorrectos para {name}: {e}", []
