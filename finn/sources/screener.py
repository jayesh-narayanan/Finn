"""screener.in company page: standardised statements + links to primary documents.

We use screener for two things only:
  1. Its standardised multi-year tables (quarters, P&L, balance sheet, cash flow, ratios),
     which are compiled from the company's own filings.
  2. Links to primary documents: annual reports and concall transcripts.
Screener's own AI-written concall "Notes" are deliberately ignored (secondary).
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from .http import get_text

log = logging.getLogger("finn.screener")

BASE = "https://www.screener.in/company/{sym}/{view}"


def _num(s: str) -> float | None:
    s = (s or "").replace(",", "").replace("%", "").replace("\xa0", " ").strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _clean_label(s: str) -> str:
    s = s.replace("\xa0", " ").replace("+", "").strip()
    return re.sub(r"\s+", " ", s)


def _table(soup: BeautifulSoup, section_id: str) -> dict | None:
    sec = soup.find("section", id=section_id)
    if not sec:
        return None
    t = sec.find("table", class_="data-table")
    if not t:
        return None
    head = [th.get_text(" ", strip=True) for th in t.find("thead").find_all("th")][1:]
    rows: dict[str, list] = {}
    for tr in t.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        label = _clean_label(tds[0].get_text(" ", strip=True))
        if not label or label in rows:
            continue
        rows[label] = [_num(td.get_text(strip=True)) for td in tds[1:]]
    return {"periods": head, "rows": rows}


def _documents(soup: BeautifulSoup) -> dict:
    docs = {"annual_reports": [], "concalls": []}
    ar = soup.select_one("div.documents.annual-reports")
    if ar:
        for a in ar.select("li a"):
            label = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            m = re.search(r"(20\d\d)", label)
            docs["annual_reports"].append({"label": label, "url": a.get("href"),
                                           "year": int(m.group(1)) if m else None})
    cc = soup.select_one("div.documents.concalls")
    if cc:
        for li in cc.select("li"):
            first = li.find("div")
            when = first.get_text(" ", strip=True) if first else ""
            links = {}
            for a in li.select("a.concall-link, a"):
                txt = a.get_text(" ", strip=True)
                if a.get("href") and txt in ("Transcript", "PPT", "REC"):
                    links[txt.lower()] = a["href"]
            if when and links:
                docs["concalls"].append({"date": when, **links})
    return docs


def _has_data(t: dict | None) -> bool:
    return bool(t and t["rows"] and any(v is not None for vals in t["rows"].values() for v in vals))


def company(symbol: str) -> dict:
    """Fetch and parse; prefers consolidated, falls back to standalone."""
    symbol = symbol.upper().strip()
    last_err = None
    for view in ("consolidated/", ""):
        url = BASE.format(sym=symbol, view=view)
        try:
            html = get_text(url, ttl=24 * 3600)
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
        soup = BeautifulSoup(html, "lxml")
        quarters = _table(soup, "quarters")
        if not _has_data(quarters) and view:
            continue  # consolidated empty -> try standalone
        h1 = soup.select_one("div.company-info h1") or soup.find("h1")
        about = soup.select_one("div.company-profile div.about") or soup.select_one("div.about")
        top = {}
        for li in soup.select("#top-ratios li"):
            n, v = li.select_one(".name"), li.select_one(".number")
            if n and v:
                top[n.get_text(strip=True)] = _num(v.get_text(strip=True))
        return {
            "symbol": symbol,
            "name": h1.get_text(strip=True) if h1 else symbol,
            "url": url,
            "basis": "consolidated" if view else "standalone",
            "about": about.get_text(" ", strip=True) if about else "",
            "top": top,
            "quarters": quarters,
            "pl": _table(soup, "profit-loss"),
            "bs": _table(soup, "balance-sheet"),
            "cf": _table(soup, "cash-flow"),
            "ratios": _table(soup, "ratios"),
            "docs": _documents(soup),
        }
    raise RuntimeError(f"screener: could not load {symbol}: {last_err}")


def row(t: dict | None, *names: str) -> list | None:
    """First row whose label starts with any of the given names (case-insensitive)."""
    if not t:
        return None
    for n in names:
        for label, vals in t["rows"].items():
            if label.lower().startswith(n.lower()):
                return vals
    return None


# ------------------------------------------------------------------ finding the company
class NotFound(RuntimeError):
    """Nothing on screener.in matches what the investor typed."""


def clean_query(q: str) -> str:
    """'reliance.ns' -> 'RELIANCE'; 'NSE:TCS' -> 'TCS'; keeps names like 'Asian Paints' intact."""
    q = re.sub(r"\s+", " ", (q or "").strip())
    q = re.sub(r"^(NSE|BSE)\s*:\s*", "", q, flags=re.I)
    q = re.sub(r"\.(NS|BO)$", "", q, flags=re.I)
    return q[:60]


def looks_like_symbol(q: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9&\-]{2,20}", q))


def search(q: str) -> list[dict]:
    """screener.in's own company search (what its search box calls). [{name, symbol}]"""
    import json
    from urllib.parse import quote
    raw = get_text(f"https://www.screener.in/api/company/search/?q={quote(q)}&v=3&fts=1", ttl=7 * 86400)
    out = []
    for r in json.loads(raw) or []:
        m = re.match(r"/company/([^/]+)/", r.get("url") or "")
        if m and r.get("name"):
            out.append({"name": r["name"], "symbol": m.group(1).upper()})
    return out


def resolve(q: str) -> str:
    """Best screener symbol for what was typed: the symbol itself if it exists, else the top search hit."""
    q = clean_query(q)
    if not q:
        raise NotFound("Type an NSE symbol or a company name")
    if looks_like_symbol(q):
        try:
            company(q.upper())
            return q.upper()
        except Exception as e:  # noqa: BLE001 - not a symbol screener knows: search by name instead
            log.info("'%s' isn't a screener symbol (%s); searching", q, e)
    try:
        hits = search(q)
    except Exception as e:  # noqa: BLE001
        raise NotFound(f"Couldn't search screener.in for '{q}': {e}") from e
    if not hits:
        raise NotFound(f"No listed company matches '{q}'. Try its NSE symbol, like PIDILITIND.")
    return hits[0]["symbol"]
