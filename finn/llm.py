"""Model router.

Two tiers, any OpenAI-compatible endpoint (Gemini, Groq, OpenRouter, Together, Ollama...):
  fast  -> bulk extraction: one-liners, results-table reading, transcript guidance
  smart -> the one reasoning-heavy step per dossier: promised vs delivered, bull/bear

Every call is cached on (model, prompt). A ledger records calls, cache hits, tokens and
estimated cost so the app can show what each output cost to produce.
"""
from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
import time

import requests

from . import cache
from .config import env

log = logging.getLogger("finn.llm")

# USD per 1M tokens (input, output). Override with FINN_PRICE_<TIER>="in,out".
_DEFAULT_PRICES = {"fast": (0.10, 0.40), "smart": (0.30, 2.50)}


class Ledger:
    def __init__(self):
        self.calls = self.cached = self.tin = self.tout = 0
        self.usd = 0.0
        self.by_tier: dict[str, int] = {}

    def as_dict(self):
        return {"calls": self.calls, "cached": self.cached, "tokens_in": self.tin,
                "tokens_out": self.tout, "usd": round(self.usd, 5), "by_tier": self.by_tier}


_local = threading.local()


@contextlib.contextmanager
def ledger():
    prev = getattr(_local, "ledger", None)
    _local.ledger = Ledger()
    try:
        yield _local.ledger
    finally:
        _local.ledger = prev


def _led() -> Ledger | None:
    return getattr(_local, "ledger", None)


class LLMUnavailable(RuntimeError):
    pass


def available() -> bool:
    return bool(env("FINN_LLM_API_KEY") or "localhost" in (env("FINN_LLM_BASE_URL") or ""))


def _cfg(tier: str):
    base = env("FINN_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/").rstrip("/")
    key = env("FINN_LLM_API_KEY", "")
    model = env("FINN_FAST_MODEL", "gemini-3.5-flash-lite") if tier == "fast" else \
        env("FINN_SMART_MODEL", "gemini-3.5-flash")
    p = env(f"FINN_PRICE_{tier.upper()}")
    price = tuple(float(x) for x in p.split(",")) if p else _DEFAULT_PRICES[tier]
    return base, key, model, price


def parse_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            return json.loads(m.group(1))
        raise


class LLMConfigError(RuntimeError):
    """Wrong key, model or URL. Retrying won't help, so fail at once with a clear message."""


_resolved: dict[str, str] = {}  # tier -> model actually used, when the configured one isn't served


def _err_text(r) -> str:
    try:
        e = r.json()
        e = e[0] if isinstance(e, list) else e
        return str((e.get("error") or {}).get("message") or e)[:300]
    except Exception:  # noqa: BLE001
        return r.text[:300]


def list_models() -> list[str]:
    base, key, _, _ = _cfg("fast")
    r = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    if r.status_code != 200:
        raise LLMConfigError(f"Couldn't list models at {base} (HTTP {r.status_code}): {_err_text(r)}")
    data = r.json().get("data", [])
    return [m["id"].removeprefix("models/") for m in data if m.get("id")]


_SKIP = re.compile(r"embed|tts|audio|image|vision|live|realtime|transcri|aqa|imagen|veo|learnlm|gemma|guard|whisper|moderation")


def _version(mid: str) -> tuple:
    return tuple(float(x) for x in re.findall(r"\d+(?:\.\d+)?", mid)[:2]) or (0,)


def pick_model(tier: str, ids: list[str]) -> str | None:
    """Best available chat model for a tier: newest stable 'flash-lite' for fast, newest stable 'flash' for smart."""
    chat = [m for m in ids if not _SKIP.search(m.lower())]

    def best(cands):
        stable = [m for m in cands if not re.search(r"preview|exp|latest|\d{2}-\d{2}|\d{3,4}$", m)]
        pool = stable or cands
        return max(pool, key=lambda m: (_version(m), -len(m))) if pool else None

    lite = [m for m in chat if "flash-lite" in m or "flash-8b" in m]
    flash = [m for m in chat if "flash" in m and m not in lite]
    if tier == "fast":
        return best(lite) or best(flash) or best(chat)
    return best(flash) or best([m for m in chat if "pro" in m]) or best(chat)


_RETIRED = re.compile(r"not found|no longer available|deprecated|not supported|retired|discontinued", re.I)


def _resolve(tier: str, configured: str, why: str) -> str:
    """The configured model was refused. Use the replacement the API suggests, else the newest stable one."""
    ids = list_models()
    suggested = re.search(r"use (?:models/)?([\w.\-]+[\w])", why)
    m = suggested.group(1) if suggested and suggested.group(1) in ids else None
    m = m or pick_model(tier, [i for i in ids if i != configured])
    if not m:
        raise LLMConfigError(f"Model {configured} was refused ({why[:160]}) and no other chat model was found. "
                             f"Available: {', '.join(ids[:20])}")
    env_name = "FINN_FAST_MODEL" if tier == "fast" else "FINN_SMART_MODEL"
    log.warning("Model %s was refused (%s). Using %s instead; set %s=%s in .env to make this permanent.",
                configured, why[:120], m, env_name, m)
    _resolved[tier] = m
    return m


def model_for(tier: str) -> str:
    return _resolved.get(tier) or _cfg(tier)[2]


def complete(tier: str, system: str, user: str, *, as_json: bool = True,
             max_tokens: int = 2000, temperature: float = 0.1):
    if not available():
        raise LLMUnavailable("Set FINN_LLM_API_KEY (see .env.example)")
    base, key, _, (pin, pout) = _cfg(tier)
    model = model_for(tier)
    k = cache.key(model, system, user, as_json, max_tokens)
    led = _led()
    hit = cache.get("llm", k)
    if hit is not None:
        if led:
            led.cached += 1
        return parse_json(hit) if as_json else hit
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = None
    resolved_once = False
    for attempt in range(5):
        body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if as_json:
            body["response_format"] = {"type": "json_object"}
        try:
            r = requests.post(f"{base}/chat/completions", json=body, headers=headers, timeout=(15, 90))
        except requests.RequestException as e:
            last = e
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code in (400, 404) and not resolved_once and _RETIRED.search(_err_text(r)) \
                and not re.search(r"api key", _err_text(r), re.I):  # model retired or renamed: switch once
            resolved_once = True
            model = _resolve(tier, model, _err_text(r))
            k = cache.key(model, system, user, as_json, max_tokens)
            continue
        if r.status_code in (400, 401, 403, 404):
            msg = _err_text(r)
            hint = " Check FINN_LLM_API_KEY in .env." if re.search(r"key|auth|permission", msg, re.I) else ""
            raise LLMConfigError(f"{r.status_code} from {model}: {msg}{hint}")
        if r.status_code == 429 or r.status_code >= 500:  # free tiers rate-limit: back off
            last = RuntimeError(f"{r.status_code}: {_err_text(r)}")
            time.sleep(min(30, 5 * (attempt + 1)))
            continue
        try:
            data = r.json()
            text = data["choices"][0]["message"]["content"] or ""
            out = parse_json(text) if as_json else text
        except (ValueError, KeyError, IndexError, TypeError) as e:
            last = e
            time.sleep(2)
            continue
        usage = data.get("usage") or {}
        tin = usage.get("prompt_tokens") or len(system + user) // 4
        tout = usage.get("completion_tokens") or len(text) // 4
        if led:
            led.calls += 1
            led.tin += tin
            led.tout += tout
            led.usd += tin / 1e6 * pin + tout / 1e6 * pout
            led.by_tier[tier] = led.by_tier.get(tier, 0) + 1
        cache.put("llm", k, text)
        return out
    raise RuntimeError(f"LLM call failed after retries: {last}")
