"""LKG 2.0 Q&A service.

Serves the explorer page and the question-answering API on this computer only:

    pip install -r server/requirements.txt
    python server/app.py            # then open http://localhost:8000

Settings are in config.yaml; API keys go in a .env file (never committed).
"""
import json
import os
import pathlib
import sys
import time

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import llm  # noqa: E402
import retrieve  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = pathlib.Path(os.environ.get("LKG_CONFIG", ROOT / "config.yaml"))
MODES = ("local", "frontier")
SCENARIOS = ("plain-llm", "rag")
_index = {}


def load_env(path=ROOT / ".env"):
    """Read KEY=value lines from .env into the environment (existing variables win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_config():
    """Read config.yaml on every request, so edits apply without a restart."""
    with open(CONFIG, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    if config.get("mode") not in MODES:
        raise HTTPException(500, f"config.yaml: mode must be one of {MODES}.")
    return config


def get_index(config):
    """The RAG search index (scripts/build_search.py), opened once; None if it has not been built."""
    path = ROOT / config.get("rag", {}).get("database", "search.db")
    if not path.exists():
        return None
    if str(path) not in _index:
        _index[str(path)] = retrieve.Index(path, ROOT / "data" / "laws.json", ROOT / "data" / "refs.json")
    return _index[str(path)]


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    mode: str | None = Field(None, description="local or frontier; overrides config.yaml for this question")
    scenario: str | None = Field(None, description="plain-llm or rag; overrides config.yaml for this question")


class Source(BaseModel):
    n: int
    kind: str
    law: str
    title: str
    unit: str
    eli: str | None = None
    url: str
    text: str


class Answer(BaseModel):
    question: str
    answer: str
    scenario: str
    mode: str
    provider: str
    model: str
    seconds: float
    sources: list[Source] = []
    search_terms: list[str] = []


app = FastAPI(title="LKG 2.0 Q&A", version="0.1",
              description="Question answering over Spanish legislation (Legal Knowledge Graph 2.0, OEG-UPM).")


@app.get("/api/qa")
def qa_info():
    """What the service is set up to use. The page calls this to check the server is running."""
    config = load_config()
    provider, model = llm.describe(config, config["mode"])
    rag = get_index(config) is not None
    scenario = config.get("scenario", "plain-llm")
    return {"service": "lkg2-qa", "scenario": scenario if rag or scenario != "rag" else "plain-llm",
            "rag_available": rag, "mode": config["mode"], "provider": provider, "model": model}


@app.post("/api/qa", response_model=Answer)
def qa(q: Question):
    config = load_config()
    mode = q.mode or config["mode"]
    if mode not in MODES:
        raise HTTPException(422, f"mode must be one of {MODES}.")
    scenario = q.scenario or config.get("scenario", "plain-llm")
    if scenario not in SCENARIOS:
        raise HTTPException(422, f"scenario must be one of {SCENARIOS}.")
    question = q.question.strip()
    start = time.perf_counter()
    sources, terms = [], []
    try:
        if scenario == "plain-llm":
            answer, provider, model = llm.ask(config, question, mode)
        else:
            index = get_index(config)
            if index is None:
                raise HTTPException(503, "The search database has not been built: run scripts/build_search.py (see the README).")
            rag = config.get("rag", {})
            extra = []
            if rag.get("rewrite_query", True):
                words, _, _ = llm.ask(config, question, mode, system=llm.REWRITE_PROMPT)
                extra = [w.strip(" .-*\n") for w in words.replace("\n", ",").split(",") if w.strip()][:12]
            found, terms = index.context(question, extra, k=rag.get("results", 6))
            sources = [dict(s, n=i + 1, url=f"https://www.boe.es/buscar/act.php?id={s['law']}") for i, s in enumerate(found)]
            blocks = []
            for s in sources:
                head = s["title"] + (f" — {s['unit']}" if s["unit"] else " — ficha de la norma (LKG 2.0)")
                if s.get("since"):
                    head += f" (versión vigente desde {s['since']})"
                blocks.append(f"[{s['n']}] {head}\n{s['text']}")
            prompt = "Fragmentos:\n\n" + "\n\n".join(blocks) + f"\n\nPregunta: {question}"
            answer, provider, model = llm.ask(config, prompt, mode, system=config.get("rag_prompt") or llm.RAG_PROMPT)
    except llm.ModelError as e:
        raise HTTPException(503, str(e)) from None
    return Answer(question=q.question, answer=answer.strip(), scenario=scenario, mode=mode, provider=provider, model=model,
                  seconds=round(time.perf_counter() - start, 2), sources=[Source(**{k: s.get(k) for k in Source.model_fields}) for s in sources],
                  search_terms=terms)


@app.get("/api/questions")
def questions():
    """Demo questions, from a local file that is never committed (questions_file in config.yaml)."""
    path = ROOT / load_config().get("questions_file", "private/questions.json")
    if not path.exists():
        return {"questions": []}
    with open(path, encoding="utf-8") as f:
        return {"questions": json.load(f).get("questions", [])}


# The explorer page and its data (only these, so .env and config.yaml are never served).
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(ROOT / "index.html")


app.mount("/data", StaticFiles(directory=ROOT / "data"), name="data")


if __name__ == "__main__":
    load_env()
    port = int(os.environ.get("PORT") or load_config().get("port", 8000))
    print(f"LKG 2.0: open http://localhost:{port}  (API docs: http://localhost:{port}/docs)")
    uvicorn.run(app, host="127.0.0.1", port=port)
