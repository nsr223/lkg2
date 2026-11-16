"""Send a set of questions to the running /api/qa service and save the answers.

Works with the demo questions and with a benchmark file kept elsewhere (for example a
private repository), so the questions never have to be added to this repository.

    python server/ask_all.py                                    # demo questions in private/questions.json
    python server/ask_all.py --scenario rag --out answers_rag.jsonl   # the same, with LKG 2.0 retrieval
    python server/ask_all.py --questions ../legal-qa-benchmark/data/noah/questions1.jsonl \\
                             --out ../legal-qa-benchmark/results/lkg2_local.jsonl

Input: a .jsonl file with one question per line ("id", "question", optional "variant"),
or a .json file {"questions": [...]} in the demo format ("id", "es"/"en").
Output: .jsonl, one answer per line, in the same format as the benchmark's run_models.py
("model", "model_id", "id", "variant", "answer", ...), so its score.py can read it.
Already answered items are skipped, so an interrupted run can be resumed.
Two-turn items (variant "pressure": "question", "scripted_answer", "pushback") are sent as a conversation:
the question and the scripted answer as history, the pushback as the new message; the saved answer is the
model's reply to the pushback. With a .jsonl file the model is asked to end with a «REFERENCIAS:» list,
as in the benchmark's own prompts (--no-references turns this off).
"""
import argparse
import json
import pathlib
import sys
from datetime import datetime, timezone

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent

p = argparse.ArgumentParser()
p.add_argument("--questions", default=str(ROOT / "private" / "questions.json"))
p.add_argument("--url", default="http://localhost:8000")
p.add_argument("--mode", choices=["local", "frontier"], help="override config.yaml for this run")
p.add_argument("--scenario", choices=["plain-llm", "rag"], help="override config.yaml for this run")
p.add_argument("--lang", choices=["es", "en"], default="es", help="language of demo questions")
p.add_argument("--model", help="model name; overrides config.yaml for this run (same provider)")
p.add_argument("--label", help='name for this system in the results (default: "lkg2_<mode>", or the --model name)')
p.add_argument("--no-references", action="store_true", help="do not ask for the «REFERENCIAS:» list (benchmark files only)")
p.add_argument("--out", default="answers.jsonl")
a = p.parse_args()

path = pathlib.Path(a.questions)
if not path.exists():
    sys.exit(f"No question file at {path}")
if path.suffix == ".jsonl":
    items = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
else:
    items = json.loads(path.read_text(encoding="utf-8"))["questions"]

try:
    info = httpx.get(f"{a.url}/api/qa", timeout=10).json()
except httpx.HTTPError:
    sys.exit(f"The Q&A server is not running at {a.url}. Start it with: python server/app.py")
mode = a.mode or info["mode"]
scenario = a.scenario or info["scenario"]
label = a.label or (a.model.replace(":", "_").replace("/", "_") if a.model else f"lkg2_{mode}") + ("_rag" if scenario == "rag" else "")
references = path.suffix == ".jsonl" and not a.no_references

out = pathlib.Path(a.out)
done = set()
if out.exists():
    for l in out.read_text(encoding="utf-8").splitlines():
        r = json.loads(l)
        if r.get("answer"):
            done.add((r["model"], r["id"], r["variant"]))

n = 0
for x in items:
    variant = x.get("variant", "plain")
    if (label, x["id"], variant) in done:
        continue
    question = x.get("question") or x.get(a.lang)
    rec = {"model": label, "id": x["id"], "variant": variant, "prompt_file": f"lkg2-{scenario}",
           "prompt": question, "date": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    body = {"question": question, "mode": mode, "scenario": scenario, "references": references}
    if a.model:
        body["model"] = a.model
    if variant == "pressure":   # turn 1 and its scripted answer are history; the pushback is the message to answer
        body["history"] = [{"role": "user", "content": question}, {"role": "assistant", "content": x["scripted_answer"]}]
        body["question"] = x["pushback"]
        rec.update(turns=2, scripted_answer=x["scripted_answer"], pushback=x["pushback"])
    try:
        r = httpx.post(f"{a.url}/api/qa", json=body, timeout=900)
        j = r.json()
        if r.status_code == 200:
            rec.update(provider=j["provider"], model_id=j["model"], scenario=j["scenario"], mode=j["mode"],
                       answer=j["answer"], seconds=j["seconds"])
            if j.get("sources"):
                rec["sources"] = [{"n": s["n"], "law": s["law"], "unit": s["unit"], "eli": s.get("eli")} for s in j["sources"]]
                rec["search_terms"] = j.get("search_terms", [])
        else:
            rec.update(answer="", error=j.get("detail", r.text))
    except httpx.HTTPError as e:
        rec.update(answer="", error=str(e))
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    n += 1
    print(f"  {x['id']} [{scenario}]: {rec.get('seconds', '-')} s  {(rec['answer'] or 'ERROR ' + rec.get('error', ''))[:80]!r}")
    if rec.get("error") and ("API key" in rec["error"] or "Could not reach" in rec["error"]):
        sys.exit("Stopping: fix the problem above and run again to continue.")

print(f"Saved {n} answers to {out}")
