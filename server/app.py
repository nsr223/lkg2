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

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = pathlib.Path(os.environ.get("LKG_CONFIG", ROOT / "config.yaml"))
MODES = ("local", "frontier")


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


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    mode: str | None = Field(None, description="local or frontier; overrides config.yaml for this question")


class Answer(BaseModel):
    question: str
    answer: str
    scenario: str
    mode: str
    provider: str
    model: str
    seconds: float


app = FastAPI(title="LKG 2.0 Q&A", version="0.1",
              description="Question answering over Spanish legislation (Legal Knowledge Graph 2.0, OEG-UPM).")


@app.get("/api/qa")
def qa_info():
    """What the service is set up to use. The page calls this to check the server is running."""
    config = load_config()
    provider, model = llm.describe(config, config["mode"])
    return {"service": "lkg2-qa", "scenario": "plain-llm", "mode": config["mode"], "provider": provider, "model": model}


@app.post("/api/qa", response_model=Answer)
def qa(q: Question):
    config = load_config()
    mode = q.mode or config["mode"]
    if mode not in MODES:
        raise HTTPException(422, f"mode must be one of {MODES}.")
    start = time.perf_counter()
    try:
        answer, provider, model = llm.ask(config, q.question.strip(), mode)
    except llm.ModelError as e:
        raise HTTPException(503, str(e)) from None
    return Answer(question=q.question, answer=answer.strip(), scenario="plain-llm", mode=mode,
                  provider=provider, model=model, seconds=round(time.perf_counter() - start, 2))


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
