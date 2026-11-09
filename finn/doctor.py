"""`python -m finn doctor`: try each data source once, live, and say plainly what works here.

Run this first on a new machine or network. NSE, Yahoo and screener.in each block or rate-limit
some networks; this shows which ones, without waiting for a full refresh to fail.
"""
from __future__ import annotations

import importlib.metadata as md
import time
from datetime import date, timedelta

from . import cache, llm


def _check(name: str, fn) -> bool:
    t = time.time()
    try:
        msg = fn()
        print(f"  ok    {name}: {msg}  ({time.time() - t:.1f}s)")
        return True
    except ImportError as e:
        print(f"  skip  {name}: not installed ({e.name}). pip install -r requirements.txt")
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL  {name}: {str(e)[:220]}")
    return False


def _version(pkg: str) -> str:
    try:
        return md.version(pkg)
    except md.PackageNotFoundError:
        return "not installed"


def run() -> int:
    from .sources import documents, nse, prices, screener
    cache.DISABLED = True
    print(f"FINN doctor   yfinance {_version('yfinance')}, curl_cffi {_version('curl_cffi')}\n")
    res = {}

    print("Prices (any one is enough)")
    def yf():
        got = prices._yfinance(["RELIANCE", "TCS"])
        if not got:
            raise RuntimeError("no data returned (Yahoo rate limit). Try: pip install -U yfinance curl_cffi")
        s = got.get("RELIANCE") or next(iter(got.values()))
        return f"{len(s['c'])} sessions, last close {s['c'][-1]} on {s['d'][-1]}"
    res["yfinance"] = _check("Yahoo via yfinance", yf)

    def chart():
        s = prices._yahoo_chart(["RELIANCE"]).get("RELIANCE")
        if not s:
            raise RuntimeError("no data returned")
        return f"{len(s['c'])} sessions, last close {s['c'][-1]}"
    res["yahoo"] = _check("Yahoo chart endpoint", chart)

    def bhav():
        sess = nse._nse()
        d = date.today()
        for _ in range(8):
            d -= timedelta(days=1)
            if d.weekday() >= 5:
                continue
            rows = prices.bhav_day(d, sess)
            if rows:
                r = rows.get("RELIANCE")
                return f"{len(rows)} stocks on {d:%d %b}" + (f", RELIANCE close {r[0]}" if r else "")
        raise RuntimeError("no bhavcopy file found for the last week")
    res["bhav"] = _check("NSE bhavcopy archive", bhav)

    print("\nFilings")
    def ann():
        items = nse.announcements("RELIANCE", date.today() - timedelta(days=21), date.today())
        return f"{len(items)} RELIANCE filings in the last 3 weeks" + (f"; latest: {items[0]['subject'][:50]}" if items else "")
    res["nse"] = _check("NSE announcements API", ann)

    print("\nResearch")
    holder = {}
    def scr():
        c = screener.company("TCS")
        holder["c"] = c
        q = c.get("quarters") or {"periods": []}
        return (f"{len(q['periods'])} quarters, {len(c['docs']['concalls'])} concalls, "
                f"{len(c['docs']['annual_reports'])} annual reports")
    res["screener"] = _check("screener.in", scr)

    def pdf():
        c = holder.get("c")
        if not c:
            raise RuntimeError("needs screener.in")
        t = next((x for x in c["docs"]["concalls"] if x.get("transcript")), None)
        if not t:
            raise RuntimeError("no transcript link found on the screener page")
        pages = documents.fetch_pages(t["transcript"])
        words = sum(len(p.split()) for p in pages)
        return f"{t['date']} transcript: {len(pages)} pages, {words:,} words of text"
    res["pdf"] = _check("Transcript PDF download + text", pdf)

    def model():
        if not llm.available():
            raise RuntimeError("no FINN_LLM_API_KEY in .env (optional: rules, prices and metrics work without it)")
        out = llm.complete("fast", 'Reply with JSON {"ok": true}.', "ping", max_tokens=20)
        return f"{llm._cfg('fast')[2]} replied {out}"
    res["model"] = _check("Language model", model)

    print()
    if not (res["yfinance"] or res["yahoo"] or res["bhav"]):
        print("No price source works from this network. Try another network (e.g. a phone hotspot);\n"
              "NSE and Yahoo both block some ISPs and most cloud servers.")
    elif not (res["yfinance"] or res["yahoo"]):
        print("Yahoo is blocked here, but NSE's archive works: refresh will use it automatically.\n"
              "The first refresh downloads ~125 daily files (a minute or two); later runs only fetch new days.")
    if not res["nse"]:
        print("NSE's filings API is blocked here. The brief needs it; try another network.")
    if res["nse"] and (res["yfinance"] or res["yahoo"] or res["bhav"]):
        print("Ready: run  python -m finn refresh")
    return 0
