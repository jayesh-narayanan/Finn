"""NSE corporate announcements.

NSE's JSON API needs the cookies a browser gets from the home page, so we prime a
session first. Each announcement carries a subject ("desc"), a short exchange-written
text ("attchmntText") and a PDF link ("attchmntFile").
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, datetime

from .http import session

log = logging.getLogger("finn.nse")

_sess = None
_primed_at = 0.0


def _nse():
    global _sess, _primed_at
    if _sess is None or time.time() - _primed_at > 600:
        _sess = session()
        _sess.headers.update({"Accept": "application/json, text/plain, */*",
                              "Referer": "https://www.nseindia.com/"})
        try:
            _sess.get("https://www.nseindia.com/", timeout=20)
            _sess.get("https://www.nseindia.com/companies-listing/corporate-filings-announcements", timeout=20)
        except Exception as e:  # noqa: BLE001
            log.warning("NSE priming failed: %s", e)
        _primed_at = time.time()
    return _sess


def _parse_dt(s: str) -> str:
    for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return datetime.strptime(s.strip(), fmt).isoformat()
        except ValueError:
            continue
    return s


def announcements(symbol: str, start: date, end: date) -> list[dict]:
    url = ("https://www.nseindia.com/api/corporate-announcements?index=equities"
           f"&symbol={symbol}&from_date={start:%d-%m-%Y}&to_date={end:%d-%m-%Y}")
    s = _nse()
    raw = None
    for attempt in range(3):
        try:
            r = s.get(url, timeout=30)
            if r.status_code == 200 and r.text.strip().startswith(("[", "{")):
                raw = r.json()
                break
            log.warning("NSE %s: HTTP %s (attempt %d)", symbol, r.status_code, attempt + 1)
        except (ValueError, json.JSONDecodeError, Exception) as e:  # noqa: BLE001
            log.warning("NSE %s: %s", symbol, e)
        global _primed_at
        _primed_at = 0  # force re-prime
        s = _nse()
        time.sleep(2 * (attempt + 1))
    if raw is None:
        return []
    rows = raw.get("data", raw) if isinstance(raw, dict) else raw
    out = []
    for a in rows:
        ts = _parse_dt(a.get("an_dt") or a.get("sort_date") or "")
        subject = (a.get("desc") or "").strip()
        url_pdf = (a.get("attchmntFile") or "").strip() or None
        text = (a.get("attchmntText") or "").strip()
        uid = hashlib.sha1(f"{symbol}|{ts}|{subject}|{url_pdf}".encode()).hexdigest()[:12]
        out.append({"id": uid, "symbol": symbol, "ts": ts, "subject": subject,
                    "text": text, "url": url_pdf, "source": "NSE"})
    return out
