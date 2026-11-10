"""Build a compact metadata index of legalize-es for the law explorer page.

Usage: python3 -I build_index.py <legalize-es checkout> <out dir>

Writes laws.json (one row per law), refs.json (all links between laws, loaded
by the page only when a law is opened) and excerpts/<folder>.json (the start
of each law's text and its first article, used to write summaries on request).

Reads only the YAML front matter of each law file (title, rank, dates, status,
ELI link, subjects, references to other laws) and writes one JSON file:
  {"meta": {...}, "dict": {rank: [...], dept: [...], subj: [...], rel: [...]}, "laws": [[...], ...]}
Repeated strings (ranks, departments, subjects, relation types) are stored once
in "dict" and referenced by index, to keep the file small.
"""
import glob, json, os, re, sys
from collections import Counter

root, outdir = sys.argv[1], sys.argv[2]
ELI = "https://www.boe.es/eli/"
FM = re.compile(r"\A---\n(.*?)\n---", re.S)
REF = re.compile(r"^\s*(.*?)\s*\[(\d+)\]\s*([A-Z0-9][A-Za-z0-9-]+)\s*:?\s*(.*)$")

def val(v):
    v = v.strip()
    if not v:
        return None
    try:
        return json.loads(v)
    except Exception:
        return v.strip('"')

def refs(s):
    outl = []
    for part in (s or "").split(" | "):
        m = REF.match(part)
        if m:
            outl.append((m.group(1).strip().upper() or "REF", m.group(3)))
    return outl

dicts = {k: {} for k in ("rank", "dept", "subj", "rel", "jur")}
def idx(kind, s):
    d = dicts[kind]
    if s not in d:
        d[s] = len(d)
    return d[s]

laws, links, excerpts, skipped = [], {}, {}, 0
subj_code = {}
HEAD_CHARS, ART1_CHARS = 1400, 900

def clean(lines):
    out = []
    for ln in lines:
        if ln.lstrip().startswith(">"):  # legalize-es notes, not part of the law
            continue
        ln = re.sub(r"^#+\s*", "", ln).replace("\\.", ".").replace("**", "").strip()
        if ln:
            out.append(ln)
    return "\n".join(out)

def excerpt(body):
    lines = body.split("\n")
    head = clean(lines[1:])[:HEAD_CHARS]  # line 0 is the title
    art1 = ""
    for i, ln in enumerate(lines):
        if re.match(r"^#+\s*Art[ií]culo (1|primero)\b", ln):
            art1 = clean(lines[i:])[:ART1_CHARS]
            break
    if art1 and art1[:200] in head:
        art1 = ""
    return [head, art1]
for path in glob.glob(os.path.join(root, "es*", "**", "*.md"), recursive=True):
    with open(path, encoding="utf-8") as f:
        head = f.read(400_000)
    m = FM.match(head)
    if not m:
        skipped += 1
        continue
    d = {}
    for line in m.group(1).split("\n"):
        k, sep, v = line.partition(":")
        if sep and k and not k.startswith(" "):
            d[k.strip()] = val(v)
    lid = d.get("identifier")
    if not lid or not d.get("title"):
        skipped += 1
        continue
    jur = d.get("jurisdiction") or os.path.relpath(path, root).split(os.sep)[0]
    prev, nxt = refs(d.get("references_previous")), refs(d.get("references_subsequent"))
    subj = d.get("subjects") if isinstance(d.get("subjects"), list) else []
    try:  # BOE's own code for each subject, used for its official subject IRI (https://www.boe.es/legislacion/eli/materias/<code>)
        for sc in json.loads(d.get("subject_codes") or "[]"):
            if sc.get("text") and sc.get("codigo"): subj_code.setdefault(sc["text"], sc["codigo"])
    except Exception:
        pass
    laws.append([
        lid,                                        # 0 BOE identifier
        d.get("title"),                             # 1 title
        idx("rank", d.get("rank") or "otro"),       # 2 rank
        idx("jur", jur),                            # 3 jurisdiction (es = national, es-xx = region)
        {"in_force": 0, "repealed": 1, "expired": 2}.get(d.get("status"), 3),  # 4 status
        d.get("publication_date"),                  # 5
        d.get("entry_into_force"),                  # 6
        d.get("last_updated"),                      # 7
        (d.get("url_eli") or "").replace(ELI, "") or None,  # 8 ELI link, without the https://www.boe.es/eli/ prefix
        idx("dept", d.get("department") or ""),     # 9 department
        [idx("subj", s) for s in subj],             # 10 subjects
        d.get("official_number"),                   # 11
        int(d["article_count"]) if str(d.get("article_count") or "").isdigit() else None,  # 12
        len(prev), len(nxt),                        # 13, 14 number of links (the links themselves are in refs.json)
        d.get("repeal_date"),                       # 15
        os.path.basename(os.path.dirname(path)),    # 16 folder in legalize-es: <jurisdiction>/<folder>/<identifier>.md
        1 if d.get("text_state") == "as_enacted" else 0,  # 17 1 = original text without later amendments, 0 = consolidated
        (d.get("pdf_url") or "").replace("https://www.boe.es/boe/dias/", "") or None,  # 18 PDF of the original BOE issue
        d.get("enactment_date"),                    # 19 date the law was signed
    ])
    excerpts.setdefault(laws[-1][16], {})[lid] = excerpt(head[m.end():])
    if prev or nxt:
        links[lid] = [[[idx("rel", r), t] for r, t in prev], [[idx("rel", r), t] for r, t in nxt]]

rev = {k: [s for s, _ in sorted(v.items(), key=lambda kv: kv[1])] for k, v in dicts.items()}
rev["subjcode"] = [subj_code.get(x) for x in rev["subj"]]  # parallel to rev["subj"]
data = {"meta": {"source": "legalize-es (BOE consolidated texts)", "count": len(laws), "built": __import__("datetime").date.today().strftime("%d/%m/%Y")}, "dict": rev, "laws": laws}
os.makedirs(os.path.join(outdir, "excerpts"), exist_ok=True)
for b, ex in excerpts.items():
    with open(os.path.join(outdir, "excerpts", b + ".json"), "w", encoding="utf-8") as f:
        json.dump(ex, f, ensure_ascii=False, separators=(",", ":"))
print("excerpts:", len(excerpts), "files,", round(sum(os.path.getsize(os.path.join(outdir, "excerpts", b + ".json")) for b in excerpts) / 1e6, 1), "MB")
for name, obj in (("laws.json", data), ("refs.json", links)):
    out = os.path.join(outdir, name)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    print(name, round(os.path.getsize(out) / 1e6, 1), "MB")
print(len(laws), "laws,", skipped, "skipped,", sum(l[13] + l[14] for l in laws), "links")
print("ranks:", Counter(rev["rank"][l[2]] for l in laws).most_common())
print("jurisdictions:", len(rev["jur"]), "| status:", Counter(l[4] for l in laws))
