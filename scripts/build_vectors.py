"""Add search by meaning to the Q&A service: one vector per article of search.db, made by a local embedding model.

    ollama pull bge-m3
    python3 scripts/build_vectors.py search.db                 # every article (several hours on a laptop)
    python3 scripts/build_vectors.py search.db --jur es        # only state-level laws (about 60 % of the articles)
    python3 scripts/build_vectors.py search.db --max-pieces 1  # only the beginning of each article (about half the time)

Long articles are split into pieces of whole paragraphs and each piece gets its own vector, so a paragraph deep
inside a long article can be found by its own meaning.

The vectors are written next to the database (search.vec, search.vec.ids, search.vec.json). The server uses them
when they are there: a question is then matched by meaning as well as by words, so "me pagan tarde el sueldo"
finds "retrasos continuados en el abono del salario". Nothing leaves this computer.

The run can be stopped and started again: it continues where it stopped. Use the same model for building and
for the server (rag.semantic.model in config.yaml); to change model, delete the three search.vec* files.
Some models expect a prefix before each text (for example "passage: " and "query: " for the e5 family):
give it with --passage-prefix here and rag.semantic.query_prefix in config.yaml. bge-m3 needs none.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import zlib

import httpx
import numpy as np


def pieces(text, size, max_pieces):
    """A long article as several pieces of whole paragraphs, about `size` characters each, so that a paragraph
    deep inside it (art. 37.4 of the Workers' Statute, say) can be found by its own meaning."""
    out, cur = [], ""
    for par in text.split("\n\n"):
        if cur and len(cur) + len(par) > size:
            out.append(cur); cur = ""
        cur += ("\n\n" if cur else "") + par
    if cur:
        out.append(cur)
    return [p[:size * 2] for p in out[:max_pieces]] or [""]


def unit_texts(title, context, unit, text, size, max_pieces):
    """What is embedded for one article: for each piece, the law, the heading above it, the article, and the piece."""
    parts = pieces(text, size, max_pieces)
    return [f"{title[:110]}. {context[:60]}\n{p}" if i == 0 else f"{title[:110]}. {context[:60]}. {unit}\n{p}" for i, p in enumerate(parts)]


def embed(url, model, texts, timeout=600):
    r = httpx.post(f"{url}/api/embed", json={"model": model, "input": texts}, timeout=timeout)
    if r.status_code >= 400:
        sys.exit(f"Ollama answered {r.status_code}: {r.text[:300]}\nIs the model there? Run: ollama pull {model}")
    v = np.asarray(r.json()["embeddings"], dtype=np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("db")
    ap.add_argument("--model", default="bge-m3")
    ap.add_argument("--url", default="http://localhost:11434")
    ap.add_argument("--jur", help='only laws of this jurisdiction, e.g. "es" for state-level laws')
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--piece-chars", type=int, default=1200, help="long articles are split into pieces of about this many characters")
    ap.add_argument("--max-pieces", type=int, default=12, help="pieces embedded per article; 1 = only the beginning (about half the time)")
    ap.add_argument("--passage-prefix", default="")
    a = ap.parse_args()
    url = a.url.rstrip("/")
    base = os.path.splitext(a.db)[0] + ".vec"
    db = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    titles = dict(db.execute("SELECT id, title FROM laws" + (" WHERE jur = ?" if a.jur else ""), (a.jur,) if a.jur else ()))
    todo = [r[0] for r in db.execute("SELECT id, law, unit FROM units ORDER BY id")
            if r[1] in titles and not r[2].startswith("_text_after_")]
    settings = {"model": a.model, "piece_chars": a.piece_chars, "max_pieces": a.max_pieces, "passage_prefix": a.passage_prefix, "jur": a.jur}
    start = 0
    if os.path.exists(base + ".json"):
        meta = json.load(open(base + ".json", encoding="utf-8"))
        if {k: meta.get(k) for k in settings} != settings:
            sys.exit(f"{base}.json was made with other settings ({meta}). Delete the search.vec* files to start again.")
        rows = min(os.path.getsize(base + ".ids") // 4, os.path.getsize(base) // (2 * meta["dim"]))
        owners = np.fromfile(base + ".ids", dtype=np.int32, count=rows)
        if rows:   # drop the last article written (it may be incomplete) and continue from it
            last = int(owners[-1])
            rows = int(np.searchsorted(owners, last))
            start = todo.index(last)
        for path, size in ((base + ".ids", rows * 4), (base, rows * 2 * meta["dim"])):
            with open(path, "r+b") as f:
                f.truncate(size)
        print(f"continuing: {start} of {len(todo)} articles already done", file=sys.stderr)
    try:
        dim = embed(url, a.model, ["prueba"]).shape[1]
    except httpx.ConnectError:
        sys.exit(f"Could not reach Ollama at {url}. Is it running (ollama serve)?")
    json.dump(dict(settings, dim=dim, built=time.strftime("%Y-%m-%d")), open(base + ".json", "w", encoding="utf-8"))
    t0, n_vec = time.time(), 0
    with open(base, "ab") as fv, open(base + ".ids", "ab") as fi:
        for i in range(start, len(todo), a.batch):   # whole articles per step, so a restart never splits one
            ids = todo[i:i + a.batch]
            rows = {r[0]: r for r in db.execute(
                f"SELECT id, law, context, unit, text FROM units WHERE id IN ({','.join('?' * len(ids))})", ids)}
            texts, owner = [], []
            for j in ids:
                _, law, ctx, unit, blob = rows[j]
                for t in unit_texts(titles[law], ctx or "", unit, zlib.decompress(blob).decode("utf-8"), a.piece_chars, a.max_pieces):
                    texts.append(a.passage_prefix + t); owner.append(j)
            vecs = np.concatenate([embed(url, a.model, texts[k:k + a.batch]) for k in range(0, len(texts), a.batch)])
            fv.write(vecs.astype(np.float16).tobytes())
            fi.write(np.asarray(owner, dtype=np.int32).tobytes())
            fv.flush(); fi.flush()
            n, n_vec = i + len(ids), n_vec + len(owner)
            if (n // a.batch) % 50 == 0 or n == len(todo):
                rate = (n - start) / max(1e-9, time.time() - t0)
                print(f"  {n} / {len(todo)} articles, {rate:.0f} per second, about {(len(todo) - n) / max(rate, 1e-9) / 60:.0f} min left",
                      file=sys.stderr)
    print(f"{os.path.getsize(base + '.ids') // 4} vectors of {dim} numbers for {len(todo)} articles in {base} "
          f"({os.path.getsize(base) / 1e6:.0f} MB)", file=sys.stderr)


if __name__ == "__main__":
    main()
