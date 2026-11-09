"""End-of-day close and volume for a list of NSE symbols, from the first source that works.

1. yfinance batch download. yfinance >= 0.2.54 talks to Yahoo through curl_cffi, which presents
   a real browser's TLS fingerprint. That is what gets past the 429s Yahoo gives plain scripts.
2. Yahoo's chart endpoint directly, through curl_cffi when installed.
3. NSE's own daily bhavcopy archive: one file per trading day covering every listed stock.
   The first run downloads one file per session (a minute or two); each day is cached for good,
   so later runs fetch only the new days. Needs nothing from Yahoo.

Symbols one source misses fall through to the next. Every failure is reported back so the app
can say which source was used and what went wrong.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

from .. import cache
from .http import UA, RateLimited

log = logging.getLogger("finn.prices")
IST = timezone(timedelta(hours=5, minutes=30))
MIN_SESSIONS = 25  # fewer than this and the volume baseline / σ are meaningless


def sessions_wanted() -> int:
    return int(os.environ.get("FINN_PRICE_SESSIONS", "120"))


def _ok(s: dict | None) -> bool:
    return bool(s) and len(s.get("c", [])) >= MIN_SESSIONS


# ------------------------------------------------------------------ 1. yfinance
def frame_to_series(sub) -> dict | None:
    """One ticker's OHLCV DataFrame -> compact {d, c, v}."""
    if sub is None or "Close" not in sub:
        return None
    sub = sub.dropna(subset=["Close"])
    if sub.empty:
        return None
    vol = sub["Volume"].fillna(0) if "Volume" in sub else [0] * len(sub)
    return {"d": [ix.strftime("%Y-%m-%d") for ix in sub.index],
            "c": [round(float(x), 2) for x in sub["Close"]],
            "v": [int(x) for x in vol]}


def split_download(df, tickers: list[str]) -> dict[str, dict]:
    """yf.download returns (ticker, field) or (field, ticker) columns depending on version."""
    import pandas as pd
    out = {}
    if df is None or df.empty:
        return out
    for t in tickers:
        sub = None
        if isinstance(df.columns, pd.MultiIndex):
            if t in df.columns.get_level_values(0):
                sub = df[t]
            elif t in df.columns.get_level_values(1):
                sub = df.xs(t, axis=1, level=1)
        elif len(tickers) == 1:
            sub = df
        s = frame_to_series(sub)
        if s:
            out[t] = s
    return out


def _yfinance(symbols: list[str]) -> dict[str, dict]:
    import yfinance as yf  # ImportError -> source skipped
    tickers = [f"{s}.NS" for s in symbols]
    df = yf.download(tickers=tickers, period="9mo", interval="1d", group_by="ticker",
                     auto_adjust=False, progress=False, threads=False)
    got = split_download(df, tickers)
    if not got and len(tickers) > 1:
        raise RuntimeError("yfinance returned no data (Yahoo is likely rate limiting this network; "
                           "try: pip install -U yfinance curl_cffi)")
    return {t[:-3]: s for t, s in got.items()}


# ------------------------------------------------------------------ 2. Yahoo chart
def _browser_session():
    try:
        from curl_cffi import requests as creq
        return creq.Session(impersonate="chrome")
    except ImportError:
        import requests
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept": "application/json"})
        return s


def _yahoo_chart(symbols: list[str]) -> dict[str, dict]:
    s = _browser_session()
    out = {}
    for i, sym in enumerate(symbols):
        k = cache.key("yahoo-chart", sym, date.today().isoformat())
        hit = cache.get("prices", k, ttl=4 * 3600)
        if hit:
            out[sym] = hit
            continue
        r = s.get(f"https://query2.finance.yahoo.com/v8/finance/chart/{sym}.NS",
                  params={"range": "9mo", "interval": "1d"}, timeout=20)
        if r.status_code == 429:
            raise RateLimited("Yahoo chart returned 429 (rate limited)")
        if r.status_code != 200:
            log.warning("yahoo chart %s: HTTP %s", sym, r.status_code)
            continue
        try:
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            ser = {"d": [], "c": [], "v": []}
            for t, c, v in zip(res["timestamp"], q["close"], q["volume"]):
                if c is None:
                    continue
                ser["d"].append(datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d"))
                ser["c"].append(round(float(c), 2))
                ser["v"].append(int(v or 0))
        except (KeyError, IndexError, TypeError, ValueError) as e:
            log.warning("yahoo chart %s: unexpected response (%s)", sym, e)
            continue
        out[sym] = ser
        cache.put("prices", k, ser)
        if i < len(symbols) - 1:
            time.sleep(0.5)
    return out


# ------------------------------------------------------------------ 3. NSE bhavcopy
UDIFF = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
LEGACY = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv"
EQUITY_SERIES = ("EQ", "BE", "BZ", "SM", "ST")


def _rows(text: str, sym_col: str, series_col: str, close_col: str, vol_col: str) -> dict[str, list]:
    out: dict[str, list] = {}
    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [f.strip() for f in reader.fieldnames or []]
    for row in reader:
        row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        series = row.get(series_col, "")
        if series not in EQUITY_SERIES:
            continue
        sym = row.get(sym_col, "")
        if not sym or (sym in out and series != "EQ"):
            continue  # prefer the EQ line when a stock trades in more than one series
        try:
            out[sym] = [float(row[close_col]), int(float(row[vol_col] or 0))]
        except (KeyError, ValueError):
            continue
    return out


def parse_udiff(content: bytes) -> dict[str, list]:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        text = z.read(name).decode("utf-8", "replace")
    return _rows(text, "TckrSymb", "SctySrs", "ClsPric", "TtlTradgVol")


def parse_legacy(content: bytes) -> dict[str, list]:
    return _rows(content.decode("utf-8", "replace"), "SYMBOL", "SERIES", "CLOSE_PRICE", "TTL_TRD_QNTY")


def bhav_day(d: date, sess) -> dict[str, list] | None:
    """{symbol: [close, volume]} for one trading day, or None if there was no session (or not yet published)."""
    k = d.isoformat()
    hit = cache.get("bhav", k)
    if hit is not None:
        return hit or None
    blocked = None
    for url, parse in ((UDIFF.format(d=d), parse_udiff), (LEGACY.format(d=d), parse_legacy)):
        try:
            r = sess.get(url, timeout=30)
        except Exception as e:  # noqa: BLE001
            blocked = e
            continue
        if r.status_code == 200 and r.content:
            try:
                rows = parse(r.content)
            except Exception as e:  # noqa: BLE001 - corrupt or HTML error page
                log.warning("bhavcopy %s unreadable: %s", d, e)
                continue
            if rows:
                cache.put("bhav", k, rows)
                return rows
        elif r.status_code in (403, 429):
            blocked = RateLimited(f"NSE archive returned {r.status_code} for {d}")
    if blocked:
        raise blocked
    if (date.today() - d).days > 4:
        cache.put("bhav", k, {})  # an old weekday with no file was a market holiday
    return None


def _weekdays_back(n: int, today: date | None = None) -> list[date]:
    d, out = (today or date.today()), []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def _bhavcopy(symbols: list[str]) -> dict[str, dict]:
    from . import nse
    sess = nse._nse()  # primed session: NSE's archive host accepts it reliably
    want = sessions_wanted()
    days = _weekdays_back(want + 8)  # a few extra for holidays
    todo = [d for d in days if cache.get("bhav", d.isoformat()) is None]
    if todo:
        log.info("prices: downloading %d NSE bhavcopy files (cached after this run)", len(todo))
    # fail fast if the archive is blocked, before queueing a hundred requests
    probe = next((d for d in reversed(days) if (date.today() - d).days >= 1), days[-1])
    bhav_day(probe, sess)
    with ThreadPoolExecutor(max_workers=4) as ex:
        per_day = dict(zip(days, ex.map(lambda d: _safe_day(d, sess), days)))
    out = {}
    for sym in symbols:
        ser = {"d": [], "c": [], "v": []}
        for d in days:
            row = (per_day.get(d) or {}).get(sym)
            if row:
                ser["d"].append(d.isoformat())
                ser["c"].append(round(row[0], 2))
                ser["v"].append(row[1])
        if ser["c"]:
            out[sym] = ser
    return out


def _safe_day(d, sess):
    try:
        return bhav_day(d, sess)
    except Exception as e:  # noqa: BLE001
        log.warning("bhavcopy %s: %s", d, e)
        return None


# ------------------------------------------------------------------ public
SOURCES = [("yfinance", _yfinance), ("yahoo", _yahoo_chart), ("nse-bhavcopy", _bhavcopy)]


def eod_many(symbols: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Returns ({symbol: {d, c, v, src}}, [notes about sources that failed])."""
    out: dict[str, dict] = {}
    notes: list[str] = []
    todo = list(dict.fromkeys(symbols))
    want = sessions_wanted()
    for name, fn in SOURCES:
        if not todo:
            break
        try:
            got = fn(todo)
        except ImportError as e:
            notes.append(f"{name}: not installed ({e.name})")
            continue
        except Exception as e:  # noqa: BLE001
            msg = f"{name}: {e}"[:220]
            notes.append(msg)
            log.warning("prices via %s failed: %s", name, e)
            continue
        good = {s: v for s, v in got.items() if _ok(v)}
        for s, v in good.items():
            out[s] = {"d": v["d"][-want:], "c": v["c"][-want:], "v": v["v"][-want:], "src": name}
        todo = [s for s in todo if s not in out]
        log.info("prices: %d of %d symbols from %s", len(good), len(symbols), name)
    if not todo:
        return out, []  # a fallback covered everything: earlier failures were logged, nothing is missing
    notes.append(f"No prices for {', '.join(todo)}")
    return out, notes


def eod(symbol: str) -> dict | None:
    return eod_many([symbol])[0].get(symbol)
