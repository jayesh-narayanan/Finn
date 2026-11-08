"""Paths, settings and a tiny .env loader (no python-dotenv dependency)."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("FINN_DATA_DIR", ROOT / "data"))
CACHE = DATA / "cache"
RESEARCH = DATA / "research"
WEB = ROOT / "web"


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

for p in (DATA, CACHE, RESEARCH):
    p.mkdir(parents=True, exist_ok=True)


def portfolio_config() -> dict:
    return json.loads((ROOT / "config" / "portfolio.json").read_text())


def env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default
