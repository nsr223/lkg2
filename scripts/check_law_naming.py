"""How often does the model name the right law before the search, and does the search then find the right article?

    python scripts/check_law_naming.py QUESTIONS.jsonl [--model qwen3.5:9b] [--limit 50]

QUESTIONS.jsonl has one question per line with:
    "question": the question,
    "law": the law that answers it, as a BOE id ("BOE-A-2015-11430") or a name LKG 2.0 knows ("Estatuto de los
           Trabajadores", "Código Civil", "LEC" ...),
    "article": optional, the article that answers it ("38" or "Artículo 38").
The 2020 dataset's format (legal-qa-benchmark/data/laborlaw/qa.jsonl: question_es, norm "ET", article_es) is read too.

For each question it runs the same first step as the RAG scenarios (llm.REWRITE_PROMPT: search terms and up to three
law names, with the model in config.yaml or --model), then the search with and without those names. It prints how
often the right law was among the names and how often the right article was among the 6 results. Use questions that
are not part of any test set you report.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import yaml  # noqa: E402
import llm  # noqa: E402
import retrieve  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("questions")
ap.add_argument("--model", help="Ollama model (default: local.model in config.yaml)")
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--no-vectors", action="store_true", help="search by words only")
a = ap.parse_args()

cfg_path = next(p for p in (ROOT / "config.yaml", ROOT / "config.example.yaml") if p.exists())
config = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
rag = config.get("rag", {})
ix = retrieve.Index(str(ROOT / rag.get("database", "search.db")), str(ROOT / "data" / "laws.json"),
                    str(ROOT / "data" / "refs.json"))
sem = rag.get("semantic") or {}
use_sem = ix.vectors is not None and sem.get("enabled", True) and not a.no_vectors
vec = (lambda t: llm.embed(sem, t)) if use_sem else (lambda t: None)
SHORT = {"ET": "Estatuto de los Trabajadores"}

rows = []
for line in open(a.questions, encoding="utf-8"):
    if not line.strip():
        continue
    r = json.loads(line)
    q = r.get("question") or r.get("question_es")
    law = r.get("law") or SHORT.get(r.get("norm"), r.get("norm"))
    art = str(r.get("article") or "")
    if not art and r.get("article_es"):
        m = re.match(r"Artículo (\d+(?: bis| ter)?)\.", r["article_es"])
        art = m.group(1) if m else ""
    lid = law if law and law.startswith("BOE-") else ix.resolve(law or "")
    if q and lid:
        rows.append((q, lid, ("Artículo " + re.sub(r"^art[ií]culo\s+", "", art, flags=re.I)) if art else None))
if a.limit:
    rows = rows[:a.limit]
print(f"{len(rows)} questions; model {a.model or config['local']['model']}; {'words + meaning' if use_sem else 'words only'}")

named_ok = found_with = found_without = with_art = 0
for i, (q, lid, unit) in enumerate(rows, 1):
    reply, _, _ = llm.ask(config, q, "local", system=llm.REWRITE_PROMPT, model=a.model)
    extra, hints = llm.parse_rewrite(reply)
    named = {ix.resolve(h) for h in hints} - {None}
    named_ok += lid in named
    qv = vec(q)
    hit = lambda units: any(u["law"] == lid and (unit is None or u["unit"] == unit) for u in units)
    found_with += hit(ix.search(q, extra, k=rag.get("results", 6), hints=hints, qvec=qv)[0])
    found_without += hit(ix.search(q, extra, k=rag.get("results", 6), hints=(), qvec=qv)[0])
    if i % 10 == 0 or i == len(rows):
        n = i
        print(f"  {n:4d}: right law named {named_ok}/{n} ({100*named_ok/n:.0f}%) | right {'article' if unit else 'law'} "
              f"in top results: with the names {found_with}/{n} ({100*found_with/n:.0f}%), "
              f"without {found_without}/{n} ({100*found_without/n:.0f}%)")
