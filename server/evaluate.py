"""Grading an answer to a Spanish-law question with one or more judge models (POST /api/evaluation).

Each judge gets the question, the answer and, if given, a reference answer, and returns a verdict as JSON.
With several judges from different providers, their agreement shows how far a grade can be trusted.
"""
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import llm

CORRECT = ("yes", "partly", "no", "unsure")
PREMISE = ("none", "corrected", "accepted")

JUDGE_PROMPT = """You grade answers written by an AI system to questions about Spanish law.

You get the user's question, the answer to grade and, if available, a reference answer.
- If there is a reference answer, treat it as the ground truth: it was written or checked by experts. Do not use your
  own memory of Spanish law to overrule it.
- If there is none, judge by Spanish law as it stands today, as far as you know it, and use "unsure" when you are not
  confident.

Reply with one JSON object and nothing else:
{"correct": "yes|partly|no|unsure", "premise": "none|corrected|accepted", "reason": "one short sentence in English"}

- correct: does the answer's legal conclusion match the reference (or the law)?
  yes: the main conclusion is right, and any number, deadline or condition it states agrees.
  partly: the main conclusion is right but an important condition, number or exception is wrong or missing, or the
  answer hedges without committing to the right conclusion.
  no: the main conclusion is wrong, contradicts the reference, or the answer does not answer the question.
  Grade the legal substance, not the citation format: a wrong article number alone does not make an answer wrong.
- premise: does the question itself contain a wrong assumption or cite the wrong article?
  none: it does not. corrected: it does, and the answer points it out. accepted: it does, and the answer goes along
  with it or ignores it.
- reason: why, in one short sentence.

Be consistent and equally strict for every answer."""


def judge_message(question, answer, reference=None, history=None):
    parts = []
    if history:
        convo = "\n".join(f"{t['role'].upper()}: {t['content']}" for t in history)
        parts.append(f"Earlier conversation:\n{convo}")
    parts.append(f"Question:\n{question}")
    if reference:
        parts.append(f"Reference answer:\n{reference}")
    parts.append(f"Answer to grade:\n{answer}")
    return "\n\n".join(parts)


def parse_verdict(text):
    """The judge's JSON verdict; values outside the allowed ones become None."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("the judge did not reply with JSON")
    data = json.loads(m.group(0))
    correct = str(data.get("correct", "")).strip().lower()
    premise = str(data.get("premise", "")).strip().lower()
    return {"correct": correct if correct in CORRECT else None, "premise": premise if premise in PREMISE else None,
            "reason": str(data.get("reason", "")).strip()}


def run_judge(config, name, message):
    provider = llm.provider_for(name)
    mode = "local" if provider == "ollama" else "frontier"
    try:
        text, provider, model = llm.ask(config, message, mode, system=JUDGE_PROMPT, model=name,
                                        provider=None if mode == "local" else provider)
        return dict(parse_verdict(text), provider=provider, model=model, error=None)
    except (llm.ModelError, ValueError) as e:
        return {"correct": None, "premise": None, "reason": "", "provider": provider, "model": name, "error": str(e)}


def evaluate(config, judges, question, answer, reference=None, history=None):
    """Ask every judge at once. Returns the verdicts, the majority grade and how many judges gave it."""
    message = judge_message(question, answer, reference, history)
    with ThreadPoolExecutor(max_workers=max(1, len(judges))) as pool:
        verdicts = list(pool.map(lambda name: run_judge(config, name, message), judges))
    grades = Counter(v["correct"] for v in verdicts if v["correct"])
    top = grades.most_common()
    # a tie between grades has no majority
    majority = top[0][0] if top and (len(top) == 1 or top[0][1] > top[1][1]) else None
    return {"verdicts": verdicts, "correct": majority, "agreeing": top[0][1] if majority else 0,
            "judged": sum(grades.values())}
