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
    model = env("FINN_FAST_MODEL", "gemini-2.5-flash-lite") if tier == "fast" else \
        env("FINN_SMART_MODEL", "gemini-2.5-flash")
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


def complete(tier: str, system: str, user: str, *, as_json: bool = True,
             max_tokens: int = 2000, temperature: float = 0.1):
    if not available():
        raise LLMUnavailable("Set FINN_LLM_API_KEY (see .env.example)")
    base, key, model, (pin, pout) = _cfg(tier)
    k = cache.key(model, system, user, as_json, max_tokens)
    led = _led()
    hit = cache.get("llm", k)
    if hit is not None:
        if led:
            led.cached += 1
        return parse_json(hit) if as_json else hit
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if as_json:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = None
    for attempt in range(4):
        try:
            r = requests.post(f"{base}/chat/completions", json=body, headers=headers, timeout=120)
            if r.status_code == 429 or r.status_code >= 500:  # free tiers rate-limit: back off
                last = RuntimeError(f"{r.status_code}: {r.text[:200]}")
                time.sleep(4 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage") or {}
            tin = usage.get("prompt_tokens") or len(system + user) // 4
            tout = usage.get("completion_tokens") or len(text) // 4
            if led:
                led.calls += 1
                led.tin += tin
                led.tout += tout
                led.usd += tin / 1e6 * pin + tout / 1e6 * pout
                led.by_tier[tier] = led.by_tier.get(tier, 0) + 1
            out = parse_json(text) if as_json else text
            cache.put("llm", k, text)
            return out
        except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM call failed: {last}")
