"""Every model call in Nearhand goes through this file.

- One OpenAI-compatible client per provider (Baseten, Fireworks, Meta): switching is one argument.
- A disk cache: the same request twice costs nothing the second time.
- A cost log in SQLite (table llm_calls): tokens, seconds and dollars per call.
- Structured output: replies are validated against a Pydantic model, with one retry on bad JSON.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.getenv("NEARHAND_CACHE_DIR", ROOT / ".cache" / "llm"))
DB_PATH = Path(os.getenv("NEARHAND_DB", ROOT / "data" / "nearhand.db"))
DEFAULT_PROVIDER = os.getenv("NEARHAND_PROVIDER", "baseten")  # flip to "fireworks" during an outage

PROVIDERS = {  # name: (base_url, env var with the key, model id)
    "baseten":   ("https://inference.baseten.co/v1", "BASETEN_API_KEY", "meta-models/muse-spark-open-1026"),
    "fireworks": ("https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY",
                  "accounts/fireworks/models/muse-spark-open"),
    "glimmer":   ("https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY",
                  "accounts/fireworks/models/muse-glimmer-30b"),
    "meta":      ("https://api.meta.ai/v1", "MODEL_API_KEY", "muse-spark-1.3"),
}

# USD per 1M tokens: (input, cached input, output). Reasoning tokens bill as output.
PRICES = {
    "baseten":   (1.25, 0.15, 4.25),
    "fireworks": (1.25, 0.15, 4.25),
    "glimmer":   (0.35, 0.04, 1.50),
    "meta":      (1.25, 0.15, 4.25),
}

_clients: dict = {}


def _client(provider: str):
    if provider not in _clients:
        from openai import OpenAI
        url, key_var, _ = PROVIDERS[provider]
        key = os.getenv(key_var)
        if not key:
            raise RuntimeError(f"{key_var} is not set (check your .env file)")
        _clients[provider] = OpenAI(base_url=url, api_key=key, timeout=120, max_retries=2)
    return _clients[provider]


# ---------------------------------------------------------------- cost log

def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS llm_calls (
        id INTEGER PRIMARY KEY, ts REAL, task TEXT, provider TEXT, model TEXT,
        prompt_tokens INTEGER, cached_tokens INTEGER, completion_tokens INTEGER,
        reasoning_tokens INTEGER, seconds REAL, cost_usd REAL, cache_hit INTEGER,
        ok INTEGER, error TEXT)""")
    return con


def _usage(usage) -> dict:
    if usage is None:
        return {"prompt_tokens": 0, "cached_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
    u = usage if isinstance(usage, dict) else usage.model_dump()
    pd = u.get("prompt_tokens_details") or {}
    cd = u.get("completion_tokens_details") or {}
    return {
        "prompt_tokens": u.get("prompt_tokens") or 0,
        "cached_tokens": pd.get("cached_tokens") or 0,
        "completion_tokens": u.get("completion_tokens") or 0,
        "reasoning_tokens": cd.get("reasoning_tokens") or 0,
    }


def cost_usd(provider: str, usage: dict) -> float:
    p_in, p_cached, p_out = PRICES[provider]
    uncached = max(0, usage["prompt_tokens"] - usage["cached_tokens"])
    return (uncached * p_in + usage["cached_tokens"] * p_cached + usage["completion_tokens"] * p_out) / 1e6


def _log(task, provider, usage, seconds, cache_hit, ok=True, error=None):
    cost = 0.0 if cache_hit else cost_usd(provider, usage)
    with _db() as con:
        con.execute(
            "INSERT INTO llm_calls (ts, task, provider, model, prompt_tokens, cached_tokens, "
            "completion_tokens, reasoning_tokens, seconds, cost_usd, cache_hit, ok, error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (time.time(), task, provider, PROVIDERS[provider][2], usage["prompt_tokens"],
             usage["cached_tokens"], usage["completion_tokens"], usage["reasoning_tokens"],
             seconds, cost, int(cache_hit), int(ok), error))
    return cost


def spend_summary() -> list[dict]:
    """Dollars and calls per task and provider, for the Evals tab and the evening check."""
    with _db() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT provider, task, COUNT(*) AS calls, SUM(cache_hit) AS cache_hits, "
            "ROUND(SUM(cost_usd), 4) AS cost_usd, ROUND(AVG(seconds), 2) AS avg_seconds "
            "FROM llm_calls GROUP BY provider, task ORDER BY cost_usd DESC").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- cache

def _key(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]


def _cache_get(key: str):
    path = CACHE_DIR / f"{key}.json"
    return json.loads(path.read_text()) if path.exists() else None


def _cache_put(key: str, value) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{key}.json").write_text(json.dumps(value))


# ---------------------------------------------------------------- the raw call

def _create(provider: str, **kwargs) -> tuple[dict, dict, float]:
    """One network call. Returns (assistant message as a dict, usage, seconds)."""
    t0 = time.time()
    resp = _client(provider).chat.completions.create(model=PROVIDERS[provider][2], **kwargs)
    msg = resp.choices[0].message
    out = {"role": "assistant", "content": msg.content}
    if getattr(msg, "tool_calls", None):
        out["tool_calls"] = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls]
    return out, _usage(resp.usage), time.time() - t0


def _strip_fences(text: str) -> str:
    text = (text or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    return m.group(1) if m else text


# ---------------------------------------------------------------- public API

def call_json(task: str, system: str, user: str, schema: type[BaseModel], provider: str | None = None,
              effort: str = "low", max_tokens: int = 4000, use_cache: bool = True) -> BaseModel:
    """Ask for JSON matching `schema`; return a validated instance.

    task: a short label for the cost log ("parse_rules", "parse_team", ...).
    effort: reasoning effort. Keep "low" for parsing; the model's default is the highest and costs far more.
    """
    provider = provider or DEFAULT_PROVIDER
    json_schema = schema.model_json_schema()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    key = _key({"kind": "json", "provider": provider, "model": PROVIDERS[provider][2],
                "messages": messages, "schema": json_schema, "effort": effort, "max_tokens": max_tokens})

    if use_cache and (hit := _cache_get(key)) is not None:
        _log(task, provider, _usage(None), 0.0, cache_hit=True)
        return schema.model_validate_json(hit["content"])

    response_format = {"type": "json_schema",
                       "json_schema": {"name": schema.__name__, "schema": json_schema}}
    kwargs = {"messages": messages, "reasoning_effort": effort, "max_tokens": max_tokens,
              "response_format": response_format}

    last_error = None
    for attempt in range(2):  # one retry, with the validation error shown to the model
        try:
            msg, usage, secs = _create(provider, **kwargs)
        except Exception as e:  # some providers reject json_schema: fall back to schema-in-prompt
            if "response_format" in kwargs and attempt == 0 and _looks_like_format_error(e):
                kwargs.pop("response_format")
                kwargs["messages"] = [{"role": "system", "content": system + _schema_note(json_schema)},
                                      {"role": "user", "content": user}]
                msg, usage, secs = _create(provider, **kwargs)
            else:
                _log(task, provider, _usage(None), 0.0, cache_hit=False, ok=False, error=str(e)[:500])
                raise
        content = _strip_fences(msg["content"])
        try:
            result = schema.model_validate_json(content)
        except ValidationError as e:
            last_error = e
            _log(task, provider, usage, secs, cache_hit=False, ok=False, error=str(e)[:500])
            kwargs["messages"] = kwargs["messages"] + [
                {"role": "assistant", "content": msg["content"] or ""},
                {"role": "user", "content": f"That JSON was invalid:\n{e}\nReturn corrected JSON only."}]
            continue
        _log(task, provider, usage, secs, cache_hit=False)
        if use_cache:
            _cache_put(key, {"content": content})
        return result
    raise ValueError(f"{task}: no valid JSON after retry: {last_error}")


def call_tools(messages: list[dict], tools: list[dict], task: str = "agent", provider: str | None = None,
               effort: str = "medium", max_tokens: int = 6000, use_cache: bool = True) -> dict:
    """One agent turn with tool calling. Returns the assistant message as a dict you can append."""
    provider = provider or DEFAULT_PROVIDER
    key = _key({"kind": "tools", "provider": provider, "model": PROVIDERS[provider][2],
                "messages": messages, "tools": tools, "effort": effort, "max_tokens": max_tokens})
    if use_cache and (hit := _cache_get(key)) is not None:
        _log(task, provider, _usage(None), 0.0, cache_hit=True)
        return hit
    try:
        msg, usage, secs = _create(provider, messages=messages, tools=tools, tool_choice="auto",
                                   reasoning_effort=effort, max_tokens=max_tokens)
    except Exception as e:
        _log(task, provider, _usage(None), 0.0, cache_hit=False, ok=False, error=str(e)[:500])
        raise
    _log(task, provider, usage, secs, cache_hit=False)
    if use_cache:
        _cache_put(key, msg)
    return msg


def _looks_like_format_error(e: Exception) -> bool:
    text = str(e).lower()
    return "response_format" in text or "json_schema" in text or "structured" in text


def _schema_note(json_schema: dict) -> str:
    return ("\n\nReturn only a JSON object matching this JSON schema, with no other text:\n"
            + json.dumps(json_schema))
