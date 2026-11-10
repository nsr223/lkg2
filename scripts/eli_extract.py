"""Extract the BOE's own ELI metadata (field eli_metadata in legalize-es) into compact, lossless shards.
Usage: python3 -I eli_extract.py <legalize-es checkout> <out dir>
Each law becomes {"work": IRI, "n": {node: {predicate: [[value, kind], ...]}}} where kind is
"i" (IRI), "s" (string), "d" (date) or "l:<lang>". Long, repeated IRI starts are shortened:
"~" = the law's own ELI IRI, "E:" = http://www.elidata.es/mdr/authority/, "M:" = BOE subject (materia) IRI,
"T:" = https://www.iana.org/assignments/media-types/ (as in the source, http). The page expands them again."""
import glob, json, os, re, sys
root, outdir = sys.argv[1], sys.argv[2]
XSD = "http://www.w3.org/2001/XMLSchema#"
PFX = [("E:", "http://www.elidata.es/mdr/authority/"), ("M:", "https://www.boe.es/legislacion/eli/materias/"), ("T:", "http://www.iana.org/assignments/media-types/")]
def short(iri, work):
    if iri == work or iri.startswith(work + "/"): return "~" + iri[len(work):]
    for p, full in PFX:
        if iri.startswith(full): return p + iri[len(full):]
    return iri
shards, n, skipped = {}, 0, 0
for path in glob.glob(os.path.join(root, "es*", "*", "*.md")):
    head = open(path, encoding="utf-8").read(3_000_000)
    m = re.search(r"^eli_metadata:\s*(.*)$", head, re.M)
    lid = re.search(r'^identifier:\s*"?([^"\n]+)', head, re.M)
    if not m or not lid: skipped += 1; continue
    data = json.loads(json.loads(m.group(1)))
    works = [s for s, p in data.items() if "has_member" in p] or [min(data, key=len)]
    work = works[0]
    # Repetition is removed without losing information; the page rebuilds the full graph:
    # - version nodes that repeat the work's jurisdiction, type, id, date, number and subjects get "inh": 1
    # - has_member, is_realized_by and is_embodied_by are dropped: they are the inverse of
    #   is_member_of, realizes and embodies, which are kept
    # - format nodes (only format + embodies) become a list of formats on their expression
    INH = ("jurisdiction", "type_document", "id_local", "date_document", "number", "is_about")
    INVERSE = ("has_member", "is_realized_by", "is_embodied_by")
    def conv(v):
        val, dt, lang = v.get("value"), v.get("datatype"), v.get("lang") or v.get("xml:lang")
        if dt == XSD + "date": return [val, "d"]
        if dt or lang: return [val, "l:" + lang] if lang else [val, "s"]
        if re.match(r"https?://", str(val)): return [short(val, work), "i"]
        return [val, "s"]
    wprops = {p: sorted(json.dumps(conv(v)) for v in vals) for p, vals in data[work].items() if p in INH}
    nodes = {}
    for subj, props in data.items():
        if set(props) <= {"format", "embodies"} and "embodies" in props:
            exp = short(props["embodies"][0]["value"], work)
            nodes.setdefault(exp, {}).setdefault("_formats", []).append(short(props["format"][0]["value"], work) if "format" in props else None)
            continue
        node = nodes.setdefault(short(subj, work), {})
        if subj != work and all(sorted(json.dumps(conv(v)) for v in props.get(p, [])) == wprops.get(p, []) for p in INH if p in wprops or p in props):
            node["inh"] = 1; props = {p: v for p, v in props.items() if p not in INH}
        for p, vals in props.items():
            if p in INVERSE: continue
            node.setdefault(p, []).extend(conv(v) for v in vals)
    entry = {"work": work, "n": nodes}
    bucket = os.path.basename(os.path.dirname(path))[0]   # 16 files, by the first character of the legalize-es folder
    shards.setdefault(bucket, {})[lid.group(1)] = entry; n += 1
os.makedirs(os.path.join(outdir, "eli"), exist_ok=True)
size = 0
for b, d in shards.items():
    f = os.path.join(outdir, "eli", b + ".json")
    json.dump(d, open(f, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":")); size += os.path.getsize(f)
print(n, "laws,", skipped, "without ELI metadata,", len(shards), "files,", round(size / 1e6, 1), "MB")
