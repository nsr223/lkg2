"""Pack the per-law history files into 16 gzip files (by the first character of each law's legalize-es folder),
so the page loads one small compressed file instead of thousands.
Usage: python3 -I history_pack.py <laws.json> <history dir> <out dir>"""
import glob, gzip, json, os, sys
laws = {l[0]: l for l in json.load(open(sys.argv[1]))["laws"]}
src, out = sys.argv[2], sys.argv[3]; os.makedirs(out, exist_ok=True)
packs = {}
for f in (f for f in glob.glob(os.path.join(src, "*.json")) if not f.endswith("index.json")):
    lid = os.path.basename(f)[:-5]
    packs.setdefault(laws[lid][16][0], {})[lid] = json.load(open(f))
total = 0
for k, d in packs.items():
    p = os.path.join(out, k + ".json.gz")
    with gzip.open(p, "wt", encoding="utf-8", compresslevel=9) as g: json.dump(d, g, ensure_ascii=False, separators=(",", ":"))
    total += os.path.getsize(p)
ix = json.load(open(os.path.join(src, "index.json")))
json.dump(ix, open(os.path.join(out, "index.json"), "w"), separators=(",", ":"))
print(len(packs), "files,", sum(len(d) for d in packs.values()), "laws,", round(total / 1e6, 1), "MB compressed")
