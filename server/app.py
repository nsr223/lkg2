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
EXAMPLE_CONFIG = ROOT / "config.example.yaml"   # committed defaults; config.yaml is your own copy (not committed)
MODES = ("local", "frontier")
SCENARIOS = ("plain-llm", "rag", "rag-graph")
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
    """Read config.yaml on every request, so edits apply without a restart.
    Without a config.yaml, the committed defaults in config.example.yaml are used."""
    path = CONFIG if CONFIG.exists() or "LKG_CONFIG" in os.environ else EXAMPLE_CONFIG
    with open(path, encoding="utf-8") as f:
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


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    mode: str | None = Field(None, description="local or frontier; overrides config.yaml for this question")
    scenario: str | None = Field(None, description="plain-llm, rag or rag-graph; overrides config.yaml for this question")
    model: str | None = Field(None, max_length=100, description="model name; overrides config.yaml for this question (same provider)")
    history: list[Turn] = Field([], max_length=20, description="earlier turns of the conversation, oldest first; "
                                "'question' is then the latest user message")
    references: bool = Field(False, description="ask the model to end with a «REFERENCIAS:» list (law, article and paragraph)")


class Source(BaseModel):
    n: int
    kind: str
    law: str
    title: str
    unit: str
    eli: str | None = None
    url: str
    text: str
    via: str | None = Field(None, description="scenario 3: how this article was reached by following a link")


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
    index = get_index(config)
    return {"service": "lkg2-qa", "scenario": scenario if rag or scenario == "plain-llm" else "plain-llm",
            "rag_available": rag, "search_by_meaning": bool(index and index.vectors and (config.get("rag", {}).get("semantic") or {}).get("enabled", True)), "mode": config["mode"], "provider": provider, "model": model}


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
    history = [t.model_dump() for t in q.history]
    cite = llm.CITATION_PROMPT if q.references else ""
    start = time.perf_counter()
    sources, terms = [], []
    try:
        if scenario == "plain-llm":
            system = (config.get("system_prompt") or llm.SYSTEM_PROMPT) + cite
            answer, provider, model = llm.ask(config, question, mode, system=system, history=history, model=q.model)
        else:
            index = get_index(config)
            if index is None:
                raise HTTPException(503, "The search database has not been built: run scripts/build_search.py (see the README).")
            rag = config.get("rag", {})
            extra, hints = [], []
            # in a conversation, search with everything the user has said: a follow-up alone ("¿seguro?") names no topic
            asked = " ".join([t["content"] for t in history if t["role"] == "user"] + [question])
            if rag.get("rewrite_query", True):
                words, _, _ = llm.ask(config, asked, mode, system=llm.REWRITE_PROMPT, model=q.model)
                extra, hints = llm.parse_rewrite(words)
                if not rag.get("suggest_laws", True):
                    hints = []
            sem = rag.get("semantic") or {}
            use_sem = index.vectors is not None and sem.get("enabled", True)
            if use_sem and sem.get("model", "bge-m3") != index.vectors.meta["model"]:
                raise HTTPException(500, f"config.yaml: rag.semantic.model must be {index.vectors.meta['model']!r}, the model "
                                         "the article vectors were built with (scripts/build_vectors.py).")
            vec = (lambda text: llm.embed(sem, text)) if use_sem else (lambda text: None)
            # scenario 2 (rag) is text only: the articles found, each with its law's title and the article's name.
            # Scenario 3 (rag-graph) adds what comes from the knowledge graph: records of the laws the question names
            # (rank, scope, status, amended or repealed by), each article's scope, status and version date, and linked articles.
            kg = scenario == "rag-graph"
            users = [t["content"] for t in history if t["role"] == "user"]
            found, terms = index.context(asked, extra, k=rag.get("results", 6), max_chars=rag.get("max_chars", 1800), hints=hints,
                                         qvec=vec(asked), sem_weight=sem.get("weight", 1.0),
                                         earlier=(users[0], vec(users[0])) if users else None,
                                         graph=(rag.get("graph") or {}) if kg else None, records=kg)
            sources = [dict(s, n=i + 1, url=f"https://www.boe.es/buscar/act.php?id={s['law']}") for i, s in enumerate(found)]
            blocks = []
            for s in sources:
                head = s["title"] + (f" — {s['unit']}" if s["unit"] else " — ficha de la norma (LKG 2.0)")
                if kg:
                    info = [f"ámbito: {s['where']}", s["status"]] + ([f"versión vigente desde {s['since']}"] if s.get("since") else [])
                    if s.get("via"):
                        info.append("enlazado: " + s["via"])
                    head += " (" + "; ".join(info) + ")"
                blocks.append(f"[{s['n']}] {head}\n{s['text']}")
            prompt = "Fragmentos:\n\n" + "\n\n".join(blocks) + f"\n\nPregunta: {question}"
            system = (config.get("rag_prompt") or llm.RAG_PROMPT) + cite
            answer, provider, model = llm.ask(config, prompt, mode, system=system, history=history, model=q.model)
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
