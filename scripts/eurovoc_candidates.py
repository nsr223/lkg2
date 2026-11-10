"""List EuroVoc candidates for the most-used BOE subjects that had no automatic match, for review.
Candidates are concepts whose Spanish labels share word stems (first 5 letters) with the subject,
ranked by shared stems, then by shorter label.
Usage: python3 -I eurovoc_candidates.py <eurovoc_min.json> <laws.json> <eurovoc_map.json> <N> <out.tsv>"""
import json, re, sys, unicodedata
from collections import Counter, defaultdict
ev = json.load(open(sys.argv[1])); laws = json.load(open(sys.argv[2])); mp = json.load(open(sys.argv[3]))["map"]; N = int(sys.argv[4])
def norm(s):
    s = unicodedata.normalize("NFD", s.lower()); s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9ñ ]+", " ", s)).strip()
STOP = {"de", "del", "la", "las", "el", "los", "y", "e", "en", "a", "al", "por", "para", "con", "o", "u"}
stems = lambda s: {w[:5] for w in norm(s).split() if w not in STOP and len(w) > 2}
subj = laws["dict"]["subj"]; use = Counter(i for l in laws["laws"] for i in l[10])
index = defaultdict(set); lab = {}
for cid, c in ev.items():
    if "Concept" not in c["types"] or not c["es"] or c.get("deprecated"): continue
    lab[cid] = c["es"]
    for l in [c["es"]] + c["alt_es"]:
        for st in stems(l): index[st].add(cid)
cst = {cid: stems(l) for cid, l in lab.items()}
todo = [i for i in sorted(range(len(subj)), key=lambda i: -use[i]) if str(i) not in mp][:N]
tot = sum(use.values()); print("these cover", round(sum(use[i] for i in todo) / tot * 100, 1), "% of uses")
with open(sys.argv[5], "w") as f:
    for i in todo:
        q = stems(subj[i]); score = Counter()
        for st in q:
            for cid in index.get(st, ()): score[cid] += 1
        ranked = sorted(score, key=lambda c: (-score[c], len(cst[c] - q), len(lab[c])))[:8]
        f.write(f"{i}\t{subj[i]}\t" + " | ".join(f"{c}={lab[c]}" for c in ranked) + "\n")
