"""Build article-by-article version histories for selected laws from the legalize-es git history.
Usage: python3 -I article_history.py <legalize-es clone with history> <laws.json> <out dir> ID [ID ...]
In legalize-es every reform is a commit on the law's file, with the reforming law ("Disposition") and the
date it takes effect ("Effective-Date") in the commit message. For each version the text is split into units
(articles, additional/transitional/final provisions, annexes); a unit gets a new entry only when its text changes.
Output per law: history/<ID>.json with
  versions: [{d: valid from, pub: publication date, by: reforming law, c: commit}]  (oldest first)
  texts:    unique unit texts
  units:    {unit: [[version index, text index or -1 if removed], ...]}
  order:    [["h", heading text] | ["u", unit], ...]  reading order of the latest version, removed units kept in place"""
import json, os, re, subprocess, sys
repo, laws_path, outdir, ids = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:]
laws = {l[0]: l for l in json.load(open(laws_path))["laws"]}
jur = json.load(open(laws_path))["dict"]["jur"]
git = lambda *a: subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, check=True).stdout
UNIT = re.compile(r"^#{1,6}\s*((?:Art[ií]culo|Disposici[oó]n|Anexo|ANEXO)\b[^.\n]*?)(?:\.\s|\.$|$)")
FIELD = lambda body, k: (re.search(rf"^{k}:\s*(\S+)", body, re.M) or [None, None])[1]

def split_units(md):
    md = re.sub(r"\A---\n.*?\n---\n?", "", md, flags=re.S)
    blocks, cur, buf = [], "_pre", []
    def flush():
        if buf: blocks.append(("u", cur, "\n".join(buf).strip()))
    for ln in md.split("\n"):
        if ln.lstrip().startswith(">"): continue
        m = UNIT.match(ln)
        if m:
            flush(); cur = re.sub(r"\s+", " ", m.group(1)).strip(); buf = [ln]; continue
        if re.match(r"^#{1,5}\s", ln) and cur != "_pre":
            flush(); buf = []; cur = None; blocks.append(("h", re.sub(r"^#+\s*", "", ln).strip())); continue
        if cur is None:
            if ln.strip(): cur = "_text_after_" + blocks[-1][1][:40]; buf = [ln]
            continue
        buf.append(ln)
    flush()
    return blocks

os.makedirs(os.path.join(outdir, "history"), exist_ok=True)
ixf = os.path.join(outdir, "history", "index.json")
index = json.load(open(ixf)) if os.path.exists(ixf) else {}
if ids and ids[0].startswith("@"): ids = open(ids[0][1:]).read().split()   # @file = read the IDs from a file
def build(lid):
    l = laws[lid]; path = f"{jur[l[3]]}/{l[16]}/{lid}.md"
    log = git("log", "--reverse", "--follow", "--format=%H%x1f%ad%x1f%B%x1e", "--date=short", "--", path)
    commits = [c.strip("\n").split("\x1f") for c in log.split("\x1e") if c.strip()]
    versions, texts, tix, units, last = [], [], {}, {}, {}
    order_latest = []
    for n, (h, date, body) in enumerate(commits):
        try: md = git("show", f"{h}:{path}")
        except subprocess.CalledProcessError:   # the file was in another folder at that time
            other = [x for x in git("ls-tree", "-r", "--name-only", h).split() if x.endswith("/" + lid + ".md")]
            if not other: continue
            md = git("show", f"{h}:{other[0]}")
        blocks = split_units(md)
        v = {"d": FIELD(body, "Effective-Date") or date, "pub": FIELD(body, "Source-Date") or date, "by": FIELD(body, "Source-Id") or FIELD(body, "Disposition"), "c": h[:10]}
        if body.startswith("[bootstrap]"): v["by"] = None
        vi = len(versions); versions.append(v)
        seen = set()
        for b in blocks:
            if b[0] != "u": continue
            key, txt = b[1], b[2]
            if key in seen: key = key + " (2)"
            seen.add(key)
            if txt not in tix: tix[txt] = len(texts); texts.append(txt)
            t = tix[txt]
            if last.get(key) != t: units.setdefault(key, []).append([vi, t]); last[key] = t
        for key in list(last):
            if key not in seen and last[key] != -1: units[key].append([vi, -1]); last[key] = -1
        order_latest = blocks
    order, keys_in = [], set()
    for b in order_latest:
        order.append(["h", b[1]] if b[0] == "h" else ["u", b[1]]); keys_in.add(b[1]) if b[0] == "u" else None
    for key in units:   # units that no longer exist: put them after the unit that preceded them
        if key not in keys_in: order.append(["u", key])
    out = {"law": lid, "title": l[1], "versions": versions, "texts": texts, "units": units, "order": order}
    f = os.path.join(outdir, "history", lid + ".json")
    json.dump(out, open(f, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    changed = sum(len(v) for v in units.values()) - len(units)
    index[lid] = {"versions": len(versions), "units": len(units), "changes": changed, "y0": versions[0]["d"][:4], "y1": versions[-1]["d"][:4]}
    print(f"{lid}: {len(versions)} versions, {len(units)} units, {changed} unit changes, {os.path.getsize(f)//1000} KB", file=sys.stderr)

for lid in ids:
    if lid in index and os.path.exists(os.path.join(outdir, "history", lid + ".json")): continue   # already done
    try: build(lid)
    except Exception as e: print(f"{lid}: failed ({e})", file=sys.stderr); continue
    if len(index) % 25 == 0: json.dump(index, open(ixf, "w"), separators=(",", ":"))   # save progress now and then
json.dump(index, open(ixf, "w"), separators=(",", ":"))
