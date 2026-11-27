"""Scenario 3b (graph-tools): the model queries LKG 2.0 itself, with the tools in tools.py.

Instead of receiving a fixed set of articles (scenarios 2 and 3a), the model gets four tools (search_articles,
get_article with an optional date, get_law, get_links) and decides what to look up, through Ollama's tool calling.
The loop stops when the model answers without asking for a tool, or after MAX_STEPS rounds of tool calls (it is
then asked to answer with what it has). Every call is recorded, and every article the tools showed is kept as a source.

Only local models (Ollama) for now; the model must support tool calling (e.g. qwen2.5, llama3.1, mistral-nemo).
"""
import json
import re

import llm
from tools import TOOL_SPECS

MAX_STEPS = 6          # rounds of tool calls before the model must answer
MAX_RESULT = 4000      # characters of one tool result given back to the model

TOOLS_PROMPT = (
    "Eres un asistente sobre legislación española. Tienes herramientas para consultar LKG 2.0, que contiene los textos "
    "consolidados del BOE, el historial de versiones de cada artículo y las relaciones entre normas. Antes de responder, "
    "consulta las herramientas: busca los artículos aplicables (search_articles) y lee el texto del artículo en que te "
    "basas (get_article). Si la pregunta se refiere a una fecha o una época pasada, pide la versión de esa fecha con "
    "«date». Responde usando lo que digan los textos consultados. Nombra siempre con palabras la norma (por ejemplo "
    "«Ley 39/2015» o «Código Civil») y el artículo en que te basas. Fíjate en el ámbito de cada norma (estatal o "
    "autonómica). Si con lo consultado no basta para responder, dilo en una frase y responde igualmente con tu "
    "conocimiento general del Derecho español, indicando qué parte no se apoya en lo consultado; no inventes normas ni "
    "artículos. "
    "Responde de forma breve y en el idioma de la pregunta."
)
FINISH = "Responde ahora a la pregunta con lo que has consultado, sin pedir más herramientas."


def _ollama_tools():
    return [{"type": "function", "function": spec} for spec in TOOL_SPECS]


def _text_calls(content):
    """Tool calls a model wrote as text instead of using the tool-call field (some small models do):
    {"name": ..., "arguments": {...}}, alone or inside <tool_call> tags."""
    calls = []
    for m in re.finditer(r"\{[^{}]*\"name\"\s*:\s*\"(\w+)\"[^{}]*\"arguments\"\s*:\s*(\{[^{}]*\})[^{}]*\}", content or ""):
        try:
            calls.append({"function": {"name": m.group(1), "arguments": json.loads(m.group(2))}})
        except json.JSONDecodeError:
            pass
    return calls


def chat(cfg, messages, tools, temperature, timeout):
    """One round with Ollama's chat API, with tools offered. Returns the assistant message (dict)."""
    body = {"model": cfg["model"], "stream": False, "messages": messages,
            "options": {"num_ctx": cfg.get("num_ctx_tools", max(16384, cfg.get("num_ctx", 8192)))}}
    if tools:
        body["tools"] = tools
    if temperature is not None:
        body["options"]["temperature"] = temperature
    return llm.ollama_chat(cfg, body, timeout)


def run(config, tools, question, history=(), model=None, system_extra=""):
    """Answer a question with the model calling the tools. Returns (answer, provider, model, sources, trace).

    sources: the articles the tools showed, in the order first seen ([{"law", "unit", "via"}]).
    trace: one entry per tool call: {"tool", "args", "chars"} (chars = length of the result).
    """
    cfg = dict(config["local"], **({"model": model} if model else {}))
    temperature = config.get("temperature", 0)
    timeout = config.get("timeout", 120)
    messages = [{"role": "system", "content": TOOLS_PROMPT + system_extra}] + list(history) + [{"role": "user", "content": question}]
    offered = _ollama_tools()
    sources, trace, cache = [], [], {}
    answer = None
    for _ in range(MAX_STEPS):
        msg = chat(cfg, messages, offered, temperature, timeout)
        calls = msg.get("tool_calls") or _text_calls(msg.get("content"))
        if not calls:
            answer = msg.get("content") or ""
            break
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
        for c in calls:
            fn = c.get("function", {})
            name, args = fn.get("name", ""), fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
            if key not in cache:
                cache[key] = tools.call(name, args)
            result, seen = cache[key]
            result = result[:MAX_RESULT]
            trace.append({"tool": name, "args": args, "chars": len(result)})
            for s in seen:
                old = next((o for o in sources if (o["law"], o["unit"]) == (s["law"], s["unit"])), None)
                if old is None:
                    sources.append(dict(s))
                elif s["via"] not in old["via"].split(", "):   # e.g. found by search_articles, then read with get_article
                    old["via"] += ", " + s["via"]
            messages.append({"role": "tool", "content": result, "tool_name": name})
    if answer is None:   # still asking for tools after MAX_STEPS rounds
        messages.append({"role": "user", "content": FINISH})
        answer = chat(cfg, messages, None, temperature, timeout).get("content") or ""
    return answer, "ollama", cfg["model"], sources, trace
