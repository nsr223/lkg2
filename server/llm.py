"""Model back ends for the LKG 2.0 Q&A service.

Local models run in Ollama; hosted ("frontier") models are Anthropic, OpenAI, Gemini and DeepSeek, with API keys
in the .env file.
"""
import os
import re

import httpx

SYSTEM_PROMPT = (
    "Eres un asistente sobre legislación española. Responde de forma breve y precisa. "
    "Indica la norma (nombre y número) y el artículo en que te basas. "
    "Si no estás seguro, dilo. Responde en el idioma de la pregunta."
)

KEY_NAMES = {
    "anthropic": ["ANTHROPIC_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
}


class ModelError(Exception):
    """A problem the person running the server can fix (shown in the page)."""


def _key(provider):
    for name in KEY_NAMES[provider]:
        if os.environ.get(name):
            return os.environ[name]
    raise ModelError(f"No API key for {provider}: add {KEY_NAMES[provider][0]}=... to the .env file.")


def _post(url, body, headers, timeout):
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=timeout)
    except httpx.ConnectError as e:
        raise ModelError(f"Could not connect to {url}.") from e
    except httpx.TimeoutException as e:
        raise ModelError(f"The model took longer than {timeout} s to answer.") from e
    if r.status_code >= 400:
        raise ModelError(f"The model service answered {r.status_code}: {r.text[:300]}")
    return r.json()


_NO_THINK_FIELD = set()   # models that reject Ollama's "think" setting (they do not think anyway)
THINK_TAGS = re.compile(r"<think>.*?</think>\s*", re.S)


def ollama_chat(cfg, body, timeout):
    """POST to Ollama's chat API. Thinking models (e.g. qwen3) think before answering unless told not to, which is
    slow and can leak into the answer; local.think (default false) is sent as Ollama's "think" setting. Models
    without thinking reject the setting, so it is dropped for them. Returns the assistant message, without any
    <think>...</think> text."""
    url = cfg.get("url", "http://localhost:11434").rstrip("/")
    model = body["model"]
    if model not in _NO_THINK_FIELD:
        body = dict(body, think=bool(cfg.get("think", False)))
    try:
        data = _post(f"{url}/api/chat", body, {}, timeout)
    except ModelError as e:
        msg = str(e)
        if "think" in body and "does not support thinking" in msg:
            _NO_THINK_FIELD.add(model)
            return ollama_chat(cfg, {k: v for k, v in body.items() if k != "think"}, timeout)
        if msg.startswith("Could not connect"):
            msg = f"Could not reach Ollama at {url}. Is it running (ollama serve)?"
        elif "does not support tools" in msg:
            msg = f"The model {model} does not support tool calling in Ollama; scenario 3b needs one that does (e.g. qwen2.5)."
        elif "not found" in msg:
            msg = f"Ollama does not have the model {model}. Run: ollama pull {model}"
        raise ModelError(msg) from None
    message = data["message"]
    if message.get("content"):
        message["content"] = THINK_TAGS.sub("", message["content"])
    return message


def ask_ollama(cfg, turns, system, temperature, timeout):
    model = cfg["model"]
    body = {"model": model, "stream": False, "messages": [{"role": "system", "content": system}] + turns}
    body["options"] = {"num_ctx": cfg.get("num_ctx", 8192)}   # Ollama's default context is too short for RAG prompts
    if temperature is not None:
        body["options"]["temperature"] = temperature
    return ollama_chat(cfg, body, timeout)["content"], "ollama", model


def ask_anthropic(cfg, turns, system, temperature, timeout):
    model = cfg["model"]
    body = {"model": model, "max_tokens": cfg.get("max_tokens", 1024), "system": system, "messages": turns}
    if temperature is not None:
        body["temperature"] = temperature
    headers = {"x-api-key": _key("anthropic"), "anthropic-version": "2023-06-01"}
    data = _post("https://api.anthropic.com/v1/messages", body, headers, timeout)
    return "".join(b.get("text", "") for b in data.get("content", [])), "anthropic", model


def _chat_completions(provider, url, cfg, turns, system, temperature, timeout):
    """OpenAI's chat format, which DeepSeek's API also uses."""
    model = cfg["model"]
    body = {"model": model, "messages": [{"role": "system", "content": system}] + turns}
    if temperature is not None:
        body["temperature"] = temperature
    headers = {"Authorization": f"Bearer {_key(provider)}"}
    data = _post(url, body, headers, timeout)
    return data["choices"][0]["message"]["content"], provider, model


def ask_openai(cfg, turns, system, temperature, timeout):
    return _chat_completions("openai", "https://api.openai.com/v1/chat/completions", cfg, turns, system, temperature, timeout)


def ask_deepseek(cfg, turns, system, temperature, timeout):
    return _chat_completions("deepseek", "https://api.deepseek.com/chat/completions", cfg, turns, system, temperature, timeout)


def ask_gemini(cfg, turns, system, temperature, timeout):
    model = cfg["model"]
    body = {"systemInstruction": {"parts": [{"text": system}]},   # Gemini calls the assistant role "model"
            "contents": [{"role": "model" if t["role"] == "assistant" else "user", "parts": [{"text": t["content"]}]} for t in turns]}
    if temperature is not None:
        body["generationConfig"] = {"temperature": temperature}
    headers = {"x-goog-api-key": _key("gemini")}
    data = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", body, headers, timeout)
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts), "gemini", model


def embed(cfg, text, timeout=60):
    """The text as a vector, from a local embedding model in Ollama (search by meaning). cfg: rag.semantic in config.yaml."""
    url = cfg.get("url", "http://localhost:11434").rstrip("/")
    model = cfg.get("model", "bge-m3")
    try:
        data = _post(f"{url}/api/embed", {"model": model, "input": [cfg.get("query_prefix", "") + text]}, {}, timeout)
    except ModelError as e:
        msg = str(e)
        if msg.startswith("Could not connect"):
            msg = f"Could not reach Ollama at {url} for search by meaning. Is it running (ollama serve)?"
        elif "not found" in msg:
            msg = f"Ollama does not have the embedding model {model}. Run: ollama pull {model}"
        raise ModelError(msg) from None
    return data["embeddings"][0]


FRONTIER = {"anthropic": ask_anthropic, "openai": ask_openai, "gemini": ask_gemini, "deepseek": ask_deepseek}

# Hosted models by the start of their name. Anything else (gemma3:12b, qwen2.5:7b, deepseek-r1:8b...) is an Ollama model.
NAME_PREFIXES = (("claude", "anthropic"), ("gemini", "gemini"), ("gemma-", "gemini"), ("gpt-", "openai"), ("o1", "openai"),
                 ("o3", "openai"), ("o4", "openai"), ("deepseek-chat", "deepseek"), ("deepseek-reasoner", "deepseek"))


def provider_for(model):
    """Which provider serves a model, from its name: "claude-sonnet-5-5" -> anthropic, "gemini-3.5-flash" -> gemini,
    "deepseek-chat" -> deepseek, "gpt-..." -> openai. Names with a tag ("gemma3:12b") and any others -> ollama."""
    name = model.strip().lower()
    if ":" not in name:
        for prefix, provider in NAME_PREFIXES:
            if name.startswith(prefix):
                return provider
    return "ollama"


def describe(config, mode, model=None, provider=None):
    """Which provider and model a question would go to, without calling it."""
    if mode == "local":
        return "ollama", model or config["local"]["model"]
    f = config["frontier"]
    return provider or f["provider"], model or f["model"]


RAG_PROMPT = (
    "Eres un asistente sobre legislación española. Responde a la pregunta usando los fragmentos de legislación "
    "que se te dan (de LKG 2.0, a partir de los textos del BOE). Nombra siempre con palabras la norma (por ejemplo "
    "«Ley 39/2015» o «Código Civil») y el artículo en que te basas, y añade el número del fragmento entre corchetes, "
    "p. ej. «artículo 30 de la Ley 39/2015 [2]». Fíjate en el ámbito de cada norma (estatal o "
    "autonómica). Si los fragmentos no bastan para responder, dilo en una frase y responde igualmente con tu "
    "conocimiento general del Derecho español, indicando qué parte no se apoya en los fragmentos; no inventes normas ni "
    "artículos. Responde de forma breve y en el idioma de la pregunta."
)

REWRITE_PROMPT = (
    "Prepara una búsqueda en una base de datos de legislación española. Responde solo con estas dos líneas:\n"
    "TÉRMINOS: entre 4 y 10 términos en español, separados por comas, que probablemente aparezcan en el texto del "
    "artículo de ley que responde a la pregunta. Usa el vocabulario de las leyes (por ejemplo «arrendamiento» en vez "
    "de «alquiler», «trabajador» en vez de «empleado»).\n"
    "NORMAS: hasta tres normas españolas que probablemente regulan la cuestión, separadas por punto y coma, por su "
    "nombre habitual o su número (por ejemplo «Estatuto de los Trabajadores; Ley 29/1994»). Si no lo sabes, deja "
    "esta línea vacía. No indiques artículos."
)


def parse_rewrite(text):
    """The model's reply to REWRITE_PROMPT as (search terms, law names). A reply without the two labels is all terms."""
    terms, laws = text, ""
    m = re.search(r"NORMAS\s*:(.*)", text, re.I | re.S)
    if m:
        laws, terms = m.group(1), text[:m.start()]
    terms = re.sub(r"T[ÉE]RMINOS\s*:", "", terms, flags=re.I)
    clean = lambda parts: [w.strip(" .-*«»\"\n") for w in parts if w.strip(" .-*«»\"\n")]
    return clean(terms.replace("\n", ",").split(","))[:12], clean(re.split(r"[;\n]", laws))[:3]


CITATION_PROMPT = (
    " Al final de tu respuesta, añade una sección que empiece exactamente por «REFERENCIAS:» y enumere, una por "
    "línea, cada norma y artículo en que te basas, con este formato:\n- [nombre de la norma], art. [número y apartado]"
)


def ask(config, question, mode, system=None, history=None, model=None, provider=None):
    """Send one question to the configured model. Returns (answer, provider, model).

    history: earlier turns of the conversation, [{"role": "user" | "assistant", "content": "..."}, ...].
    model: use this model instead of the one in config.yaml (same provider, unless provider is given).
    provider: for mode "frontier", use this provider instead of the one in config.yaml.
    """
    system = system or config.get("system_prompt") or SYSTEM_PROMPT
    temperature = config.get("temperature", 0)
    timeout = config.get("timeout", 120)
    turns = list(history or []) + [{"role": "user", "content": question}]
    if mode == "local":
        return ask_ollama(dict(config["local"], **({"model": model} if model else {})), turns, system, temperature, timeout)
    provider = provider or config["frontier"]["provider"]
    if provider not in FRONTIER:
        raise ModelError(f"Unknown frontier provider {provider!r}: use anthropic, openai, gemini or deepseek.")
    return FRONTIER[provider](dict(config["frontier"], **({"model": model} if model else {})), turns, system, temperature, timeout)
