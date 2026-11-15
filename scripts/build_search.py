"""Build the search database used by the Q&A service (retrieval / RAG scenario).

    python3 scripts/build_search.py data/laws.json data/hist legalize-es search.db          # laws in force
    python3 scripts/build_search.py data/laws.json data/hist legalize-es search.db --all    # every law

One row per article (or provision or annex) in its current version:
- laws with a consolidated text: from the article history in data/hist (already in this repository);
- laws with only the original text: split into articles from a legalize-es clone (pass "-" to leave them out).
Preambles are not indexed. The text is stored compressed; the search index is SQLite FTS5 without
accents, so "prescripcion" finds "prescripción". The database is not committed (it is large).
"""
import argparse
import glob
import gzip
import json
import os
import re
import sqlite3
import sys
import time
import zlib

UNIT = re.compile(r"^#{1,6}\s*((?:Art[ií]culo|Disposici[oó]n|Anexo|ANEXO)\b[^.\n]*?)(?:\.\s|\.$|$)")
STATUS = {0: "in_force", 1: "repealed", 2: "expired"}


def split_units(md):
    """Same splitting as scripts/article_history.py: (heading context, unit name, text) for each unit."""
    md = re.sub(r"\A---\n.*?\n---\n?", "", md, flags=re.S)
    out, cur, buf, ctx = [], "_pre", [], ""

    def flush():
        if buf and cur:
            out.append((ctx, cur, "\n".join(buf).strip()))
    for ln in md.split("\n"):
        if ln.lstrip().startswith(">"):
            continue
        m = UNIT.match(ln)
        if m:
            flush(); cur = re.sub(r"\s+", " ", m.group(1)).strip(); buf = [ln]; continue
        if re.match(r"^#{1,5}\s", ln) and cur != "_pre":
            flush(); buf = []; cur = None; ctx = re.sub(r"^#+\s*", "", ln).strip(); continue
        if cur is None:
            if ln.strip():
                cur = "_text_after_" + ctx[:40]; buf = [ln]
            continue
        buf.append(ln)
    flush()
    return out


def plain(md):
    """Markdown to plain text for the model and the index."""
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", md)
    t = re.sub(r"^#+\s*", "", t, flags=re.M)
    t = t.replace("\\.", ".").replace("\\-", "-").replace("\\*", "*")
    t = re.sub(r"[*_]{1,3}([^*_\n]+)[*_]{1,3}", r"\1", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("laws"); ap.add_argument("hist"); ap.add_argument("legalize"); ap.add_argument("out")
    ap.add_argument("--all", action="store_true", help="include repealed and expired laws")
    a = ap.parse_args()

    raw = json.load(open(a.laws, encoding="utf-8")); D = raw["dict"]
    rows = {r[0]: r for r in raw["laws"] if a.all or r[4] == 0}
    if os.path.exists(a.out):
        os.remove(a.out)
    db = sqlite3.connect(a.out)
    db.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE laws(id TEXT PRIMARY KEY, title TEXT, num TEXT, rank TEXT, jur TEXT, status TEXT, eli TEXT,
                          pub TEXT, consolidated INTEGER);
        CREATE TABLE units(id INTEGER PRIMARY KEY, law TEXT, unit TEXT, context TEXT, since TEXT, text BLOB);
        CREATE INDEX units_law ON units(law, unit);
        CREATE VIRTUAL TABLE fts USING fts5(name, text, content='', tokenize='unicode61 remove_diacritics 2');
        CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
    """)
    n_units = 0

    def add(law_id, ctx, unit, text, since):
        nonlocal n_units
        if unit == "_pre" or not text.strip():
            return
        t = plain(text)
        r = rows[law_id]
        cur = db.execute("INSERT INTO units(law, unit, context, since, text) VALUES (?,?,?,?,?)",
                         (law_id, unit, ctx, since, zlib.compress(t.encode("utf-8"), 6)))
        name = f"{r[1]} {r[11] or ''} {ctx} {unit}"
        db.execute("INSERT INTO fts(rowid, name, text) VALUES (?,?,?)", (cur.lastrowid, name, t))
        n_units += 1

    def add_law(r, consolidated):
        db.execute("INSERT INTO laws VALUES (?,?,?,?,?,?,?,?,?)",
                   (r[0], r[1], r[11], D["rank"][r[2]], D["jur"][r[3]], STATUS.get(r[4], "other"), r[8], r[5], consolidated))

    t0 = time.time(); done = set()
    for pack in sorted(glob.glob(os.path.join(a.hist, "*.json.gz"))):
        for law_id, H in json.load(gzip.open(pack, "rt", encoding="utf-8")).items():
            if law_id not in rows:
                continue
            add_law(rows[law_id], 1); done.add(law_id); ctx = ""
            for kind, x in H["order"]:
                if kind == "h":
                    ctx = x; continue
                v, ti = H["units"].get(x, [[0, -1]])[-1]
                if ti >= 0:
                    add(law_id, ctx, x, H["texts"][ti], H["versions"][v]["d"])
        db.commit()
        print(f"  {os.path.basename(pack)}: {len(done)} consolidated laws, {n_units} units, {time.time() - t0:.0f} s", file=sys.stderr)

    n_orig = 0
    if a.legalize != "-":
        for law_id, r in rows.items():
            if law_id in done:
                continue
            path = os.path.join(a.legalize, D["jur"][r[3]], r[16], law_id + ".md")
            if not os.path.exists(path):
                continue
            add_law(r, 0); n_orig += 1
            for ctx, unit, text in split_units(open(path, encoding="utf-8").read()):
                add(law_id, ctx, unit, text, r[5])
            if n_orig % 2000 == 0:
                db.commit(); print(f"  original texts: {n_orig} laws, {n_units} units, {time.time() - t0:.0f} s", file=sys.stderr)

    db.execute("INSERT INTO fts(fts) VALUES('optimize')")
    db.executemany("INSERT INTO meta VALUES (?,?)", [("built", time.strftime("%Y-%m-%d")), ("source", raw["meta"].get("built", "")),
                                                     ("scope", "all" if a.all else "in_force")])
    db.commit(); db.execute("VACUUM"); db.close()
    print(f"{len(done)} consolidated + {n_orig} original-text laws, {n_units} units, "
          f"{os.path.getsize(a.out) / 1e6:.0f} MB, {time.time() - t0:.0f} s", file=sys.stderr)


if __name__ == "__main__":
    main()
