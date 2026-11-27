"""LKG 2.0 Q&A service.

Serves the explorer page and the question-answering API on this computer only:

    pip install -r server/requirements.txt
    python server/app.py            # then open http://localhost:8000

Settings are in config.yaml; API keys go in a .env file (never committed).
"""
import json
import os
import pathlib
import re
import sys
import time

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import agent  # noqa: E402
import cites  # noqa: E402
import evaluate  # noqa: E402
import llm  # noqa: E402
import retrieve  # noqa: E402
import tools  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = pathlib.Path(os.environ.get("LKG_CONFIG", ROOT / "config.yaml"))
EXAMPLE_CONFIG = ROOT / "config.example.yaml"   # committed defaults; config.yaml is your own copy (not committed)
MODES = ("local", "frontier")
SCENARIOS = ("plain-llm", "rag", "rag-graph", "graph-tools")
# /api/question names the scenarios by number (as in the project plan) or by a short name
METHODS = {"1": "plain-llm", "llm": "plain-llm", "2": "rag", "rag": "rag", "3": "rag-graph", "lkg": "rag-graph",
           "3b": "graph-tools", "lkg-tools": "graph-tools", **{s: s for s in SCENARIOS}}
LANGUAGES = {"es": " Responde en español.", "en": " Answer in English."}
_index = {}
_tools = {}


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


def get_tools(index, config):
    """The LKG 2.0 tools (version history, links) over the search index, made once."""
    if str(index) not in _tools:
        sem = config.get("rag", {}).get("semantic") or {}
        use_sem = index.vectors is not None and sem.get("enabled", True)
        _tools[str(index)] = tools.Tools(index, ROOT, embed=(lambda text: llm.embed(sem, text)) if use_sem else None)
    return _tools[str(index)]


def add_dated_versions(sources, tl, date, terms, max_chars):
    """Scenario 3, for a question about a past date: next to each article's text today, the text it had on that date,
    if it was different. Both are kept because a past date in a question may only be when something happened."""
    for s in sources:
        if s["kind"] != "article":
            continue
        old, since = tl.text_at(s["law"], s["unit"], date)
        if old is None:
            continue
        if old == "":
            s["dated"] = f"no existía el {date}"
            continue
        same = lambda t: re.sub(r"\W+", " ", retrieve.norm(tools.clean(t))).strip()
        if same(old) == same(s["full"]):
            s["dated"] = f"mismo texto el {date}"
            continue
        s["dated"] = f"cambiado después del {date}"
        s["text"] += f"\n\n--- Versión vigente el {date} (desde {since}) ---\n" + retrieve.best_window(tools.clean(old), terms, max_chars)


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    mode: str | None = Field(None, description="local or frontier; overrides config.yaml for this question")
    scenario: str | None = Field(None, description="plain-llm, rag, rag-graph or graph-tools; overrides config.yaml for this question")
    model: str | None = Field(None, max_length=100, description="model name; overrides config.yaml for this question (same provider)")
    provider: str | None = Field(None, description="frontier mode: anthropic, openai, gemini or deepseek; overrides config.yaml")
    language: str | None = Field(None, description="es or en: the language of the answer (default: the question's)")
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
    answer_original: str | None = Field(None, description="the model's answer before bracket numbers were corrected (only if any were)")
    citation_check: list[dict] = Field([], description="bracket numbers that did not match the article named next to them")
    tool_calls: list[dict] = Field([], description="scenario 3b: the tools the model called, in order ({tool, args, chars})")


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
    if q.language and q.language not in LANGUAGES:
        raise HTTPException(422, f"language must be one of {tuple(LANGUAGES)}.")
    cite = (llm.CITATION_PROMPT if q.references else "") + LANGUAGES.get(q.language or "", "")
    provider = q.provider if mode == "frontier" else None
    start = time.perf_counter()
    sources, terms, trace = [], [], []
    try:
        if scenario == "graph-tools":
            # scenario 3b: the model queries LKG 2.0 itself through tools (the same ones server/mcp_server.py offers)
            if mode != "local":
                raise HTTPException(422, "graph-tools runs with local models (Ollama) only, for now.")
            index = get_index(config)
            if index is None:
                raise HTTPException(503, "The search database has not been built: run scripts/build_search.py (see the README).")
            answer, provider, model, seen, trace = agent.run(config, get_tools(index, config), question, history=history, model=q.model,
                                                            system_extra=cite)
            for i, s in enumerate(seen):
                law = index.laws.get(s["law"], {})
                sources.append({"n": i + 1, "kind": "article", "law": s["law"], "title": law.get("title", s["law"]), "unit": s["unit"],
                                "eli": retrieve.eli_url(law.get("eli")), "url": f"https://www.boe.es/buscar/act.php?id={s['law']}",
                                "text": "", "via": s["via"]})
        elif scenario == "plain-llm":
            system = (config.get("system_prompt") or llm.SYSTEM_PROMPT) + cite
            answer, provider, model = llm.ask(config, question, mode, system=system, history=history, model=q.model, provider=provider)
        else:
            index = get_index(config)
            if index is None:
                raise HTTPException(503, "The search database has not been built: run scripts/build_search.py (see the README).")
            rag = config.get("rag", {})
            extra, hints = [], []
            # in a conversation, search with everything the user has said: a follow-up alone ("¿seguro?") names no topic
            asked = " ".join([t["content"] for t in history if t["role"] == "user"] + [question])
            query = retrieve.search_text(asked) if rag.get("strip_options", True) else asked
            if rag.get("rewrite_query", True):
                words, _, _ = llm.ask(config, asked, mode, system=llm.REWRITE_PROMPT, model=q.model, provider=provider)
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
            found, terms = index.context(query, extra, k=rag.get("results", 6), max_chars=rag.get("max_chars", 3000), hints=hints,
                                         qvec=vec(query), sem_weight=sem.get("weight", 1.0),
                                         earlier=(users[0], vec(users[0])) if users else None,
                                         graph=(rag.get("graph") or {}) if kg else None, records=kg,
                                         regional=rag.get("regional", "mentioned"), boilerplate=rag.get("boilerplate", 0.5))
            sources = [dict(s, n=i + 1, url=f"https://www.boe.es/buscar/act.php?id={s['law']}") for i, s in enumerate(found)]
            date = retrieve.question_date(asked, time.strftime("%Y-%m-%d")) if kg and (rag.get("graph") or {}).get("dated", True) else None
            if date:
                add_dated_versions(sources, get_tools(index, config), date, terms, rag.get("max_chars", 3000))
            blocks = []
            for s in sources:
                head = s["title"] + (f" — {s['unit']}" if s["unit"] else " — ficha de la norma (LKG 2.0)")
                if kg:
                    info = [f"ámbito: {s['where']}", s["status"]] + ([f"versión vigente desde {s['since']}"] if s.get("since") else [])
                    if s.get("dated"):
                        info.append(s["dated"])
                    if s.get("via"):
                        info.append("enlazado: " + s["via"])
                    head += " (" + "; ".join(info) + ")"
                blocks.append(f"[{s['n']}] {head}\n{s['text']}")
            prompt = "Fragmentos:\n\n" + "\n\n".join(blocks) + f"\n\nPregunta: {question}"
            system = (config.get("rag_prompt") or llm.RAG_PROMPT) + cite
            answer, provider, model = llm.ask(config, prompt, mode, system=system, history=history, model=q.model, provider=provider)
    except llm.ModelError as e:
        raise HTTPException(503, str(e)) from None
    answer, original, notes = answer.strip(), None, []
    if sources and scenario != "graph-tools" and config.get("rag", {}).get("fix_citations", True):
        fixed, notes = cites.check(answer, sources)
        if fixed != answer:
            answer, original = fixed, answer
    return Answer(question=q.question, answer=answer, answer_original=original, citation_check=notes, scenario=scenario, mode=mode, provider=provider, model=model,
                  seconds=round(time.perf_counter() - start, 2), sources=[Source(**{k: s.get(k) for k in Source.model_fields}) for s in sources],
                  search_terms=terms, tool_calls=trace)


class AskQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    language: str | None = Field(None, description="es or en: the language of the answer (default: the question's)")
    method: str = Field("2", description="1 or llm: the model alone; 2 or rag: with the articles found in LKG 2.0; "
                                          "3 or lkg: with the articles and their links in the graph; "
                                          "3b or lkg-tools: the model searches LKG 2.0 itself with tools (local models only)")
    model: str | None = Field(None, max_length=100, description="e.g. gemma3:12b (Ollama), claude-sonnet-5-5, gemini-3.5-flash, "
                                                                "deepseek-chat; the provider is chosen from the name. "
                                                                "Default: the model in config.yaml")
    provider: str | None = Field(None, description="only if the name does not say it: ollama, anthropic, openai, gemini or deepseek")
    references: bool = Field(False, description="ask the model to end with a «REFERENCIAS:» list")


@app.post("/api/question", response_model=Answer)
def ask_question(a: AskQuestion):
    """Ask one question with a chosen method (scenario) and model."""
    scenario = METHODS.get(a.method.strip().lower())
    if scenario is None:
        raise HTTPException(422, "method must be 1, 2, 3 or 3b (or llm, rag, lkg, lkg-tools).")
    provider = a.provider or (llm.provider_for(a.model) if a.model else None)
    mode = None if provider is None else ("local" if provider == "ollama" else "frontier")
    return qa(Question(question=a.question, mode=mode, scenario=scenario, model=a.model, language=a.language,
                       provider=provider if mode == "frontier" else None, references=a.references))


class EvaluationRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    answer: str = Field(min_length=1, max_length=12000)
    reference: str | None = Field(None, max_length=8000, description="the correct answer, if known (makes the grade much more reliable)")
    history: list[Turn] = Field([], max_length=20, description="earlier turns, if the answer is a reply in a conversation")
    judges: list[str] | None = Field(None, max_length=5, description="judge model names, e.g. [\"claude-sonnet-5-5\", "
                                     "\"gemini-3.5-flash\", \"deepseek-chat\"]; default: evaluation.judges in config.yaml")


class Verdict(BaseModel):
    provider: str
    model: str
    correct: str | None = Field(None, description="yes, partly, no or unsure")
    premise: str | None = Field(None, description="none (no wrong assumption in the question), corrected or accepted")
    reason: str
    error: str | None = None


class Evaluation(BaseModel):
    correct: str | None = Field(None, description="the grade most judges gave (none if they tie)")
    agreeing: int = Field(description="how many judges gave that grade")
    judged: int = Field(description="how many judges answered")
    verdicts: list[Verdict]
    seconds: float


@app.post("/api/evaluation", response_model=Evaluation)
def evaluation(e: EvaluationRequest):
    """Grade an answer with one or more judge models (by default from different providers)."""
    config = load_config()
    judges = e.judges or (config.get("evaluation") or {}).get("judges") or []
    if not judges:
        raise HTTPException(422, "No judges: give judges, or set evaluation.judges in config.yaml.")
    start = time.perf_counter()
    result = evaluate.evaluate(config, judges, e.question.strip(), e.answer.strip(), (e.reference or "").strip() or None,
                               [t.model_dump() for t in e.history])
    return Evaluation(**result, seconds=round(time.perf_counter() - start, 2))


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
