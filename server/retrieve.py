"""Retrieval over LKG 2.0 for the RAG scenario.

Finds the articles most likely to answer a question in the search database built by
scripts/build_search.py, and adds a short record (status, repealed by, ...) for any law the
question names, from data/laws.json and data/refs.json.
"""
import bisect
import json
import re
import sqlite3
import unicodedata
import zlib

STOP = set("""
a al algo algun alguna alguno ante antes aqui asi aun cada como con contra cual cuales cualquier cuando cuanta cuantas cuanto
cuantos de debe deben debo del desde donde dos durante e el ella ellas ellos en entre era eres es esa esas ese eso esos esta
estan estar estas este esto estos fue fueron ha haber hace hacer han has hasta hay la las le les lo los mas me mi mis mucho
muy nada ni no nos nosotros o os otra otras otro otros para pero poco por porque puede pueden puedo que quien quienes se sea
segun ser si sido sin sobre son su sus tal tambien tanto te tener tengo tiene tienen todo todos tu un una uno unos usted y ya yo
ley leyes norma normas articulo articulos espana espanol espanola espanoles cosa caso tipo forma manera dar dice decir
what which who when how does the of in and or for to is are law laws spain spanish article
""".split())

# Common names and abbreviations for the laws people ask about most.
ALIASES = {
    "constitucion": "BOE-A-1978-31229", "ce": "BOE-A-1978-31229",
    "codigo civil": "BOE-A-1889-4763", "cc": "BOE-A-1889-4763",
    "codigo penal": "BOE-A-1995-25444", "cp": "BOE-A-1995-25444",
    "estatuto de los trabajadores": "BOE-A-2015-11430", "et": "BOE-A-2015-11430",
    "enjuiciamiento civil": "BOE-A-2000-323", "lec": "BOE-A-2000-323",
    "enjuiciamiento criminal": "BOE-A-1882-6036", "lecrim": "BOE-A-1882-6036",
    "poder judicial": "BOE-A-1985-12666", "lopj": "BOE-A-1985-12666",
    "arrendamientos urbanos": "BOE-A-1994-26003", "lau": "BOE-A-1994-26003",
    "proteccion de datos": "BOE-A-2018-16673", "lopdgdd": "BOE-A-2018-16673", "lopd": "BOE-A-2018-16673",
    "procedimiento administrativo comun": "BOE-A-2015-10565", "lpac": "BOE-A-2015-10565",
    "regimen juridico del sector publico": "BOE-A-2015-10566", "lrjsp": "BOE-A-2015-10566",
    "seguridad social": "BOE-A-2015-11724", "lgss": "BOE-A-2015-11724",
    "jurisdiccion social": "BOE-A-2011-15936", "lrjs": "BOE-A-2011-15936",
    "contencioso-administrativa": "BOE-A-1998-16718", "ljca": "BOE-A-1998-16718",
    "libertad sindical": "BOE-A-1985-16660", "lols": "BOE-A-1985-16660",
    "prevencion de riesgos laborales": "BOE-A-1995-24292", "lprl": "BOE-A-1995-24292",
    "trabajo a distancia": "BOE-A-2021-11472",
}
RANK_WORDS = [("ley organica", "ley_organica"), ("real decreto legislativo", "real_decreto_legislativo"),
              ("real decreto-ley", "real_decreto_ley"), ("real decreto ley", "real_decreto_ley"), ("real decreto", "real_decreto"),
              ("decreto legislativo", "decreto_legislativo"), ("decreto-ley", "decreto_ley"), ("decreto", "decreto"),
              ("orden", "orden"), ("ley foral", "ley_foral"), ("ley", "ley")]
RANK_BOOST = {"constitucion": 1.0, "ley_organica": 0.9, "ley": 0.8, "ley_foral": 0.7, "real_decreto_legislativo": 0.8,
              "decreto_legislativo": 0.7, "real_decreto_ley": 0.6, "decreto_ley": 0.5, "decreto_ley_foral": 0.5,
              "decreto_foral_legislativo": 0.6, "real_decreto": 0.4, "decreto": 0.3, "decreto_foral": 0.3}
# Everyday words and the words the laws use instead, added to the search.
SYNONYMS = {"alquil": "arrendamiento", "inquilin": "arrendatario", "casero": "arrendador", "empleado": "trabajador",
            "empleador": "empresario", "jefe": "empresario", "sueldo": "salario", "paro": "desempleo", "baja": "incapacidad temporal",
            "multa": "sancion", "denuncia": "denuncia", "piso": "vivienda", "herencia": "sucesion", "divorcio": "divorcio",
            "deposito": "fianza", "despedir": "despido", "echar": "despido", "vacaciones": "vacaciones"}
REGIONS = {"andalucia": "es-an", "aragon": "es-ar", "asturias": "es-as", "cantabria": "es-cb", "castilla y leon": "es-cl",
           "castilla-la mancha": "es-cm", "castilla la mancha": "es-cm", "canarias": "es-cn", "cataluna": "es-ct", "catalunya": "es-ct",
           "extremadura": "es-ex", "galicia": "es-ga", "baleares": "es-ib", "illes balears": "es-ib", "murcia": "es-mc",
           "madrid": "es-md", "navarra": "es-nc", "pais vasco": "es-pv", "euskadi": "es-pv", "la rioja": "es-ri",
           "valenciana": "es-vc", "valencia": "es-vc", "ceuta": "es-ce", "melilla": "es-ml"}
JUR_NAMES = {"es": "estatal", "es-an": "Andalucía", "es-ar": "Aragón", "es-as": "Asturias", "es-cb": "Cantabria",
             "es-cl": "Castilla y León", "es-cm": "Castilla-La Mancha", "es-cn": "Canarias", "es-ct": "Cataluña",
             "es-ex": "Extremadura", "es-ga": "Galicia", "es-ib": "Illes Balears", "es-mc": "Región de Murcia",
             "es-md": "Comunidad de Madrid", "es-nc": "Navarra", "es-pv": "País Vasco", "es-ri": "La Rioja",
             "es-vc": "Comunitat Valenciana", "es-ce": "Ceuta", "es-ml": "Melilla"}
STATUS_ES = {0: "vigente", 1: "derogada", 2: "sin efecto (expirada)"}
REL_SHOW = {"SE DEROGA": "derogada por", "SE MODIFICA": "modificada por", "DEROGA": "deroga", "SE DESARROLLA": "desarrollada por"}


def norm(s):
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def eli_url(eli):
    return f"https://www.boe.es/eli/{eli}" if eli else None


class Index:
    def __init__(self, db_path, laws_path, refs_path):
        self.db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        raw = json.load(open(laws_path, encoding="utf-8"))
        D = raw["dict"]
        self.rel = D["rel"]
        self.laws = {r[0]: {"id": r[0], "title": r[1], "num": r[11], "rank": D["rank"][r[2]], "jur": D["jur"][r[3]],
                            "status": r[4], "eli": r[8]} for r in raw["laws"]}
        self.by_num = {}
        for law in self.laws.values():
            if law["num"]:
                self.by_num.setdefault(law["num"], []).append(law)
        self.refs = json.load(open(refs_path, encoding="utf-8"))
        self.in_db = {row[0] for row in self.db.execute("SELECT id FROM laws")}
        # each law's articles have consecutive ids: keep the ranges to find a row's law quickly
        self.ranges = sorted(self.db.execute("SELECT min(id), max(id), law FROM units GROUP BY law").fetchall())
        self.starts = [r[0] for r in self.ranges]

    # --- understanding the question -------------------------------------------------------------
    @staticmethod
    def terms(text):
        """Search terms: content words, cut to a stem so that 'prescriben' also finds 'prescripción'."""
        out = []
        for w in re.findall(r"[a-z0-9ñ]+", norm(text)):
            if w in STOP or (len(w) < 4 and not w.isdigit()) or (w.isdigit() and len(w) < 2):
                continue
            stem = w if len(w) <= 4 or w.isdigit() else w[:max(5, len(w) - 3)]
            if stem not in out:
                out.append(stem)
            for k, v in SYNONYMS.items():
                if w.startswith(k):
                    for x in v.split():
                        x = x[:max(5, len(x) - 3)]
                        if x not in out:
                            out.append(x)
        return out

    def mentioned(self, question):
        """Laws the question names, by number (Ley 30/1992) or by a common name (LAU, Código Civil)."""
        q = norm(question); found = []
        for m in re.finditer(r"(\d{1,4}/\d{4})", q):
            cands = self.by_num.get(m.group(1), [])
            before = q[max(0, m.start() - 30):m.start()]
            rank = next((r for w, r in RANK_WORDS if w in before), None)
            best = sorted(cands, key=lambda l: (l["rank"] != rank, l["jur"] != "es", l["status"] != 0))
            if best:
                found.append(best[0]["id"])
        for name, lid in ALIASES.items():
            if re.search(rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])", q) and lid not in found:
                found.append(lid)
        return found

    # --- search -------------------------------------------------------------------------------
    def _fts(self, terms, law=None, limit=40):
        if not terms:
            return []
        query = " OR ".join(f'"{t}"' if (len(t) <= 4 or t.isdigit()) else f'"{t}"*' for t in terms)
        sql = "SELECT rowid, bm25(fts, 3.0, 1.0) FROM fts WHERE fts MATCH ?"
        args = [query]
        if law:   # a law's articles have consecutive ids, so a range keeps this fast
            lo, hi = self.db.execute("SELECT min(id), max(id) FROM units WHERE law = ?", (law,)).fetchone()
            if lo is None:
                return []
            sql += " AND rowid BETWEEN ? AND ?"
            args += [lo, hi]
        sql += " ORDER BY bm25(fts, 3.0, 1.0) LIMIT ?"
        args.append(limit)
        return self.db.execute(sql, args).fetchall()

    def _unit(self, rowid):
        law, unit, ctx, since, blob = self.db.execute(
            "SELECT law, unit, context, since, text FROM units WHERE id = ?", (rowid,)).fetchone()
        return {"rowid": rowid, "law": law, "unit": unit, "context": ctx, "since": since,
                "text": zlib.decompress(blob).decode("utf-8")}

    def _law_of(self, rowid):
        i = bisect.bisect_right(self.starts, rowid) - 1
        return self.laws.get(self.ranges[i][2], {}) if i >= 0 and rowid <= self.ranges[i][1] else {}

    def article(self, law, number):
        row = self.db.execute("SELECT id FROM units WHERE law = ? AND (unit = ? OR unit LIKE ?)",
                              (law, f"Artículo {number}", f"Artículo {number} %")).fetchone()
        return row[0] if row else None

    def search(self, question, extra_terms=(), k=6, per_law=3):
        terms = self.terms(question)
        for t in self.terms(" ".join(extra_terms)):
            if t not in terms:
                terms.append(t)
        named = [l for l in self.mentioned(question) if l in self.in_db]
        q = norm(question)
        regions = sorted({j for name, j in REGIONS.items() if re.search(rf"(?<![a-z]){name}(?![a-z])", q)})
        region_words = {w for name in REGIONS for w in name.split()}
        terms = [t for t in terms if not regions or not any(w.startswith(t) for w in region_words)] or terms
        cand = {}
        for rowid, score in self._fts(terms, limit=400):
            cand[rowid] = -score
        # laws with many matching articles are probably the main law on the subject
        per_law_hits = {}
        for rowid in cand:
            lid = self._law_of(rowid).get("id")
            per_law_hits[lid] = per_law_hits.get(lid, 0) + 1
        if regions:   # make sure each region the question names gets its own results
            rows = self._fts(terms, limit=-1)
            for j in regions:
                for rowid, score in [r for r in rows if self._law_of(r[0]).get("jur") == j][:15]:
                    cand[rowid] = max(cand.get(rowid, 0), -score)
        for law in named:
            for rowid, score in self._fts(terms, law=law, limit=15):
                cand[rowid] = max(cand.get(rowid, 0), -score)
        pinned = []
        for m in re.finditer(r"art[ií]culo\s+(\d+(?:\s*(?:bis|ter))?)", norm(question)):
            for law in named:
                rid = self.article(law, m.group(1).replace(" ", " "))
                if rid:
                    pinned.append(rid)
        if not cand and not pinned:
            return [], terms
        top = max(cand.values()) if cand else 1.0
        scored = []
        for rowid, s in cand.items():
            u = self._unit(rowid)
            law = self.laws.get(u["law"], {})
            junk = u["unit"].startswith("_text_after_") or "presupuestos generales" in norm(law.get("title", ""))
            title = norm(law.get("title", ""))
            in_title = sum(1 for t in terms if re.search(rf"(?<![a-z]){re.escape(t)}", title)) / max(1, len(terms))
            place = (0.4 if law.get("jur") in regions else 0) if regions else (0.25 if law.get("jur") == "es" else 0)
            score = (s / top) + 0.15 * RANK_BOOST.get(law.get("rank"), 0) + (0.5 if u["law"] in named else 0) \
                + 0.2 * in_title + 0.3 * min(1.0, per_law_hits.get(u["law"], 0) / 15) + place - (0.3 if junk else 0)
            scored.append((score, u))
        scored.sort(key=lambda x: -x[0])
        out, per = [], {}
        if regions:   # at least one result for each region named
            for j in regions:
                best = next((u for _, u in scored if self.laws.get(u["law"], {}).get("jur") == j), None)
                if best and len(out) < k:
                    out.append(best); per[best["law"]] = per.get(best["law"], 0) + 1
        for rid in pinned:
            u = self._unit(rid)
            out.append(u); per[u["law"]] = per.get(u["law"], 0) + 1
        for score, u in scored:
            if len(out) >= k:
                break
            if any(o["rowid"] == u["rowid"] for o in out) or per.get(u["law"], 0) >= per_law:
                continue
            out.append(u); per[u["law"]] = per.get(u["law"], 0) + 1
        return out, terms

    # --- records for the model ------------------------------------------------------------------
    def law_record(self, lid):
        """A short record of a named law: status and its most important links (from the graph)."""
        law = self.laws[lid]
        lines = [f"{law['title']} ({lid}). Rango: {law['rank'].replace('_', ' ')}. "
                 f"Ámbito: {JUR_NAMES.get(law['jur'], law['jur'])}. Estado: {STATUS_ES.get(law['status'], 'otro')}."]
        later = (self.refs.get(lid) or [[], []])[1]
        for rel, label in REL_SHOW.items():
            if rel not in self.rel or rel in ("DEROGA",):
                continue
            ids = [to for r, to in later if self.rel[r] == rel]
            if ids:
                names = [f"{self.laws[t]['title']} ({t})" for t in ids[:5] if t in self.laws]
                more = f" y {len(ids) - 5} más" if len(ids) > 5 else ""
                lines.append(f"{label.capitalize()}: " + "; ".join(names) + more + ".")
        return "\n".join(lines)

    def context(self, question, extra_terms=(), k=6, max_chars=1800):
        """The sources to give the model: records of named laws first, then the articles found."""
        units, terms = self.search(question, extra_terms, k=k)
        sources = []
        for lid in self.mentioned(question)[:3]:
            law = self.laws[lid]
            sources.append({"kind": "law", "law": lid, "title": law["title"], "unit": "", "where": JUR_NAMES.get(law["jur"], law["jur"]),
                            "status": STATUS_ES.get(law["status"], "otro"),
                            "eli": eli_url(law["eli"]), "text": self.law_record(lid)})
        for u in units:
            law = self.laws.get(u["law"], {"title": u["law"], "eli": None})
            text = u["text"]
            if len(text) > max_chars:
                body = norm(text)
                pos = min([body.find(t) for t in terms if body.find(t) > 0] or [0])
                start = max(0, pos - max_chars // 3)
                text = ("…" if start else "") + text[start:start + max_chars] + "…"
            sources.append({"kind": "article", "law": u["law"], "title": law["title"], "unit": u["unit"],
                            "where": JUR_NAMES.get(law.get("jur"), law.get("jur")), "status": STATUS_ES.get(law.get("status"), "otro"),
                            "context": u["context"], "since": u["since"], "eli": eli_url(law.get("eli")), "text": text})
        return sources, terms
