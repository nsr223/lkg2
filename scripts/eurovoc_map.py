"""Map BOE subject labels (from laws.json) to EuroVoc concepts.
Usage: python3 -I eurovoc_map.py <eurovoc_min.json> <laws.json> <out eurovoc.json> [reviewed matches .tsv]
Tiers: exact (Spanish preferred or alternative label), plural (same after singularising each word),
fuzzy (close spelling, rapidfuzz score >= FUZZY). Unmatched subjects are listed, not guessed."""
import json, os, re, sys, unicodedata
from collections import Counter
from rapidfuzz import process, fuzz
FUZZY = 93
ev = json.load(open(sys.argv[1])); laws = json.load(open(sys.argv[2])); out = sys.argv[3]
subj = laws["dict"]["subj"]
use = Counter(i for l in laws["laws"] for i in l[10])

def norm(s):
    s = unicodedata.normalize("NFD", s.lower()); s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9ñ ]+", " ", s)).strip()
STOP = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "a", "al", "por", "para", "con",
        "derechos"}  # "derechos" (rights) must not become "derecho" (law)
def sing(w):
    if len(w) <= 3 or w in STOP: return w
    if re.search(r"(ion|or|al|ar|er|il|ad|ud|z)es$", w): w = w[:-2]; return w[:-1] + "z" if w.endswith("c") and False else w
    if w.endswith("ces"): return w[:-3] + "z"
    if w.endswith("s") and not w.endswith("ss"): return w[:-1]
    return w
def key(s): return " ".join(sing(w) for w in norm(s).split())

concepts = {k: c for k, c in ev.items() if "Concept" in c["types"] and c["es"] and not c.get("deprecated")}
exact, plural = {}, {}
for cid, c in concepts.items():
    for lab, kind in [(c["es"], "pref")] + [(a, "alt") for a in c["alt_es"]]:
        exact.setdefault(norm(lab), (cid, kind)); plural.setdefault(key(lab), (cid, kind))
pkeys = list(plural)
def same_kind(subject, c):
    """'derechos' (rights) and 'derecho' (law) must not be matched to each other."""
    a = "derechos" in norm(subject).split(); labs = [c["es"]] + c["alt_es"]
    return any(("derechos" in norm(l).split()) == a for l in labs)
res, tiers = {}, Counter()
for i, s in enumerate(subj):
    n, k = norm(s), key(s)
    if n in exact: cid, kind = exact[n]; tier = "exact"
    elif k in plural and same_kind(s, concepts[plural[k][0]]): cid, kind = plural[k]; tier = "plural"
    else:
        m = process.extractOne(k, pkeys, scorer=fuzz.ratio, score_cutoff=FUZZY)
        if m and same_kind(s, concepts[plural[m[0]][0]]): cid, kind = plural[m[0]]; tier = "fuzzy"
        else: tiers["none"] += 1; continue
    tiers[tier] += 1
    c = concepts[cid]
    res[i] = {"id": cid, "es": c["es"], "en": c["en"], "tier": tier, "via": kind}
cov = sum(use[i] for i in res) / sum(use.values())
print("subjects:", len(subj), dict(tiers), f"| matched {len(res)/len(subj):.1%} of labels, {cov:.1%} of uses")
REL = {"exact": "exactMatch", "plural": "exactMatch", "fuzzy": "closeMatch", "e": "exactMatch", "c": "closeMatch", "b": "broadMatch"}
manual = sys.argv[4] if len(sys.argv) > 4 else None
if manual:
    for ln in open(manual, encoding="utf-8"):
        if ln.startswith("#") or not ln.strip(): continue
        i, cid, rel, name = ln.rstrip("\n").split("\t")
        assert subj[int(i)] == name, (i, name)
        c = concepts[cid]; res[int(i)] = {"id": cid, "es": c["es"], "en": c["en"], "tier": "reviewed", "rel": rel}
print("after reviewed matches:", len(res), f"labels ({len(res)/len(subj):.1%}),", f"{sum(use[i] for i in res) / sum(use.values()):.1%} of uses")
cids = sorted({r["id"] for r in res.values()}); cix = {c: n for n, c in enumerate(cids)}
page = {"source": "EuroVoc, EU Publications Office (eurovoc_in_skos_core_concepts)", "relations": ["exactMatch", "closeMatch", "broadMatch"],
        "concepts": [[c, concepts[c]["es"], concepts[c]["en"]] for c in cids],
        "map": {i: [cix[r["id"]], ["exactMatch", "closeMatch", "broadMatch"].index(REL[r.get("rel") or r["tier"]]), r["tier"]] for i, r in res.items()}}
json.dump(page, open(out, "w"), ensure_ascii=False, separators=(",", ":"))
print("wrote", out, round(os.path.getsize(out) / 1e3), "KB")
top_un = [(subj[i], use[i]) for i in sorted(range(len(subj)), key=lambda i: -use[i]) if i not in res][:30]
print("most used unmatched:", top_un)
