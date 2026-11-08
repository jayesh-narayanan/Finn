"""A small SQLite key-value cache with TTL.

Everything expensive goes through here: HTTP fetches, PDF text, and every LLM call
(keyed on model + prompt hash). Re-running a refresh or a dossier is close to free.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from typing import Any

from .config import CACHE

_DB = CACHE / "finn_cache.sqlite"
_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS kv (ns TEXT, k TEXT, v BLOB, ts REAL, PRIMARY KEY (ns, k))")
    return c


def key(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str).encode())
        h.update(b"\x1f")
    return h.hexdigest()


def get(ns: str, k: str, ttl: float | None = None) -> Any | None:
    with _lock, _conn() as c:
        row = c.execute("SELECT v, ts FROM kv WHERE ns=? AND k=?", (ns, k)).fetchone()
    if not row:
        return None
    v, ts = row
    if ttl is not None and time.time() - ts > ttl:
        return None
    return json.loads(v)


def put(ns: str, k: str, v: Any) -> None:
    with _lock, _conn() as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?,?)", (ns, k, json.dumps(v, default=str), time.time()))
