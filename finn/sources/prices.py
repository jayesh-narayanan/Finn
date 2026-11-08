"""End-of-day price & volume.

Primary: Yahoo's public chart endpoint (no crumb needed for daily bars).
Fallback: the yfinance library, if installed. Output is compact column arrays,
which the app evaluates alert rules against on the device.
"""
from __future__ import annotations

import json
import logging

from .http import get_text

log = logging.getLogger("finn.prices")


def _yahoo_chart(symbol: str, rng: str) -> dict:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.NS?range={rng}&interval=1d"
    data = json.loads(get_text(url, ttl=3 * 3600))
    res = data["chart"]["result"][0]
    ts = res["timestamp"]
    q = res["indicators"]["quote"][0]
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    out = {"d": [], "c": [], "v": []}
    for t, c, v in zip(ts, q["close"], q["volume"]):
        if c is None:
            continue
        out["d"].append(datetime.fromtimestamp(t, ist).strftime("%Y-%m-%d"))
        out["c"].append(round(float(c), 2))
        out["v"].append(int(v or 0))
    return out


def _yfinance(symbol: str, rng: str) -> dict:
    import yfinance as yf  # optional dependency
    df = yf.Ticker(f"{symbol}.NS").history(period=rng, interval="1d", auto_adjust=False)
    return {"d": [d.strftime("%Y-%m-%d") for d in df.index],
            "c": [round(float(x), 2) for x in df["Close"]],
            "v": [int(x) for x in df["Volume"]]}


def eod(symbol: str, rng: str = "6mo") -> dict | None:
    for fn in (_yahoo_chart, _yfinance):
        try:
            out = fn(symbol, rng)
            if out["c"]:
                return out
        except Exception as e:  # noqa: BLE001 - try the next source
            log.warning("prices %s via %s failed: %s", symbol, fn.__name__, e)
    return None
