"""Shared HTTP session with browser-like headers, retries and on-disk caching."""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import requests

from .. import cache
from ..config import CACHE

log = logging.getLogger("finn.http")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
DOCS = CACHE / "docs"
DOCS.mkdir(parents=True, exist_ok=True)


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Accept": "*/*"})
    return s


_S = session()


class RateLimited(RuntimeError):
    """The site said 'too many requests'. Retrying quickly only extends the block, so callers move on."""


def get_text(url: str, ttl: float = 6 * 3600, headers: dict | None = None,
             sess: requests.Session | None = None, retries: int = 3) -> str:
    k = cache.key(url)
    hit = cache.get("http", k, ttl)
    if hit is not None:
        return hit
    s = sess or _S
    last: Exception | None = None
    for i in range(retries):
        try:
            r = s.get(url, headers=headers, timeout=30)
            if r.status_code == 200:
                cache.put("http", k, r.text)
                return r.text
            if r.status_code == 429:
                wait = _retry_after(r)
                if i == 0 and wait <= 15:  # one polite retry if the site asks for a short pause
                    time.sleep(wait)
                    continue
                raise RateLimited(f"HTTP 429 (rate limited) for {url}")
            last = RuntimeError(f"HTTP {r.status_code} for {url}")
        except requests.RequestException as e:  # network hiccup
            last = e
        time.sleep(1.5 * (i + 1))
    raise last or RuntimeError(url)


def _retry_after(r) -> float:
    try:
        return float(r.headers.get("Retry-After", 5))
    except ValueError:
        return 5.0


def download(url: str, headers: dict | None = None, sess: requests.Session | None = None) -> Path:
    """Download a binary (PDF) once; later calls return the cached file."""
    ext = Path(url.split("?")[0]).suffix.lower() or ".bin"
    path = DOCS / (hashlib.sha1(url.encode()).hexdigest() + ext)
    if path.exists() and path.stat().st_size > 0:
        return path
    s = sess or _S
    h = {"Referer": "https://www.nseindia.com/" if "nse" in url else "https://www.bseindia.com/"}
    h.update(headers or {})
    r = s.get(url, headers=h, timeout=90)
    r.raise_for_status()
    path.write_bytes(r.content)
    return path
