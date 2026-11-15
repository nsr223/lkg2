"""Model back ends for the LKG 2.0 Q&A service.

Scenario 1 (plain LLM): the model answers from what it already knows, with no
retrieval. Retrieval over LKG 2.0 (RAG / MCP) will be added as other scenarios.
"""
import os

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


def ask_ollama(cfg, question, system, temperature, timeout):
    url = cfg.get("url", "http://localhost:11434").rstrip("/")
    model = cfg["model"]
    body = {"model": model, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": question}]}
    body["options"] = {"num_ctx": cfg.get("num_ctx", 8192)}   # Ollama's default context is too short for RAG prompts
    if temperature is not None:
        body["options"]["temperature"] = temperature
    try:
        data = _post(f"{url}/api/chat", body, {}, timeout)
    except ModelError as e:
        msg = str(e)
        if msg.startswith("Could not connect"):
            msg = f"Could not reach Ollama at {url}. Is it running (ollama serve)?"
        elif "not found" in msg:
            msg = f"Ollama does not have the model {model}. Run: ollama pull {model}"
        raise ModelError(msg) from None
    return data["message"]["content"], "ollama", model


def ask_anthropic(cfg, question, system, temperature, timeout):
    model = cfg["model"]
    body = {"model": model, "max_tokens": cfg.get("max_tokens", 1024), "system": system,
            "messages": [{"role": "user", "content": question}]}
    if temperature is not None:
        body["temperature"] = temperature
    headers = {"x-api-key": _key("anthropic"), "anthropic-version": "2023-06-01"}
    data = _post("https://api.anthropic.com/v1/messages", body, headers, timeout)
    return "".join(b.get("text", "") for b in data.get("content", [])), "anthropic", model


def ask_openai(cfg, question, system, temperature, timeout):
    model = cfg["model"]
    body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": question}]}
    if temperature is not None:
        body["temperature"] = temperature
    headers = {"Authorization": f"Bearer {_key('openai')}"}
    data = _post("https://api.openai.com/v1/chat/completions", body, headers, timeout)
    return data["choices"][0]["message"]["content"], "openai", model


def ask_gemini(cfg, question, system, temperature, timeout):
    model = cfg["model"]
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": question}]}]}
    if temperature is not None:
        body["generationConfig"] = {"temperature": temperature}
    headers = {"x-goog-api-key": _key("gemini")}
    data = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", body, headers, timeout)
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts), "gemini", model


FRONTIER = {"anthropic": ask_anthropic, "openai": ask_openai, "gemini": ask_gemini}


def describe(config, mode):
    """Which provider and model a question would go to, without calling it."""
    if mode == "local":
        return "ollama", config["local"]["model"]
    f = config["frontier"]
    return f["provider"], f["model"]


RAG_PROMPT = (
    "Eres un asistente sobre legislación española. Responde a la pregunta usando los fragmentos de legislación "
    "que se te dan (de LKG 2.0, a partir de los textos del BOE). Indica la norma y el artículo en que te basas y "
    "el número del fragmento entre corchetes, p. ej. [2]. Si los fragmentos no bastan para responder, dilo "
    "claramente y no inventes normas ni artículos. Responde de forma breve y en el idioma de la pregunta."
)

REWRITE_PROMPT = (
    "Devuelve entre 4 y 10 términos de búsqueda en español, separados por comas, que probablemente aparezcan "
    "en el texto del artículo de ley que responde a la pregunta. Usa el vocabulario de las leyes (por ejemplo "
    "«arrendamiento» en vez de «alquiler», «trabajador» en vez de «empleado»). No incluyas nombres ni números "
    "de leyes. Escribe solo los términos."
)


def ask(config, question, mode, system=None):
    system = system or config.get("system_prompt") or SYSTEM_PROMPT
    temperature = config.get("temperature", 0)
    timeout = config.get("timeout", 120)
    if mode == "local":
        return ask_ollama(config["local"], question, system, temperature, timeout)
    provider = config["frontier"]["provider"]
    if provider not in FRONTIER:
        raise ModelError(f"Unknown frontier provider {provider!r}: use anthropic, openai or gemini.")
    return FRONTIER[provider](config["frontier"], question, system, temperature, timeout)
