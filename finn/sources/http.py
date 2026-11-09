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


def _browser():
    try:
        from curl_cffi import requests as creq
        return creq.Session(impersonate="chrome")
    except ImportError:
        return None


def download(url: str, headers: dict | None = None, sess: requests.Session | None = None) -> Path:
    """Download a binary (PDF) once; later calls return the cached file.

    Plain requests first (fast); if the site stalls, refuses or serves an HTML block page, retry once
    through curl_cffi's browser impersonation. BSE and some company sites do this to scripts.
    """
    ext = Path(url.split("?")[0]).suffix.lower()
    ext = ext if ext in (".pdf", ".zip", ".csv") else ".pdf"
    path = DOCS / (hashlib.sha1(url.encode()).hexdigest() + ext)
    if path.exists() and path.stat().st_size > 0:
        return path
    h = {"Referer": "https://www.nseindia.com/" if "nse" in url else "https://www.bseindia.com/",
         "Accept": "application/pdf,*/*"}
    h.update(headers or {})
    errors = []
    for name, s in (("requests", sess or _S), ("browser", _browser())):
        if s is None:
            continue
        try:
            r = s.get(url, headers=h, timeout=(15, 60) if name == "requests" else 75)
            body = r.content or b""
            if r.status_code == 200 and body and (b"%PDF" in body[:1024] or ext != ".pdf"):
                path.write_bytes(body)
                return path
            errors.append(f"{name}: HTTP {r.status_code}" + ("" if r.status_code != 200 else " (not a PDF)"))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {type(e).__name__}")
            log.warning("download %s via %s failed: %s", url, name, e)
    raise RuntimeError(f"Couldn't download {url} ({'; '.join(errors)})")
