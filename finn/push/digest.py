"""Build data/feed.json: the portfolio, every filing in the window (classified, scored,
enriched) and EOD series for on-device alert evaluation."""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta

from .. import llm
from ..config import DATA, portfolio_config
from ..sources import nse, prices, screener
from . import alerts, classify, enrich
from .score import score, tier

log = logging.getLogger("finn.digest")


def build(days: int = 30, enrich_items: bool = True) -> dict:
    cfg = portfolio_config()
    end = date.today()
    start = end - timedelta(days=days)
    holdings, all_items, series = [], [], {}
    with llm.ledger() as led:
        for h in cfg["portfolio"]:
            sym = h["symbol"]
            log.info("· %s", sym)
            p = prices.eod(sym, "6mo")
            if p:
                series[sym] = {"d": p["d"][-120:], "c": p["c"][-120:], "v": p["v"][-120:]}
            scr = None
            try:
                scr = screener.company(sym)
            except Exception as e:  # noqa: BLE001
                log.warning("screener %s: %s", sym, e)
            mcap = (scr or {}).get("top", {}).get("Market Cap")
            items = nse.announcements(sym, start, end)
            time.sleep(0.6)  # be polite to NSE
            classify.classify_all(items)
            for it in items:
                it["name"] = h["name"]
                score(it, p, mcap)
                t = tier(it["score"])
                if enrich_items and it["category"] == "results" and it["subtype"] == "results" and t != "routine":
                    it["card"] = enrich.results_card(it, scr)
                elif enrich_items and it["category"] == "management_change" and it["score"] >= 50:
                    it["card"] = enrich.mgmt_card(it)
            all_items.extend(items)
            holdings.append(_holding_row(h, p, mcap))
        if enrich_items:
            enrich.one_liners([i for i in all_items if i["score"] >= 30 and not i.get("card")])
        cost = led.as_dict()

    _dedupe(all_items)
    all_items.sort(key=lambda x: x["ts"], reverse=True)
    rules = cfg["default_rules"]
    feed = {
        "mode": "live", "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window_days": days, "holdings": holdings, "announcements": all_items, "prices": series,
        "default_rules": rules,
        "alerts": [a for s, p in series.items() for a in alerts.evaluate(s, p, rules, days=1)],
        "cost": cost,
    }
    (DATA / "feed.json").write_text(json.dumps(feed, indent=1, default=str))
    log.info("feed: %d holdings, %d filings, cost %s", len(holdings), len(all_items), cost)
    return feed


def _holding_row(h: dict, p: dict | None, mcap: float | None) -> dict:
    row = dict(h, market_cap_cr=mcap)
    if p and len(p["c"]) > 21:
        c, v = p["c"], p["v"]
        avg20 = sum(v[-21:-1]) / 20 or 1
        row.update(close=c[-1], chg_pct=round((c[-1] / c[-2] - 1) * 100, 2),
                   chg_1m=round((c[-1] / c[-22] - 1) * 100, 2), vol_x=round(v[-1] / avg20, 2),
                   asof=p["d"][-1], spark=c[-30:])
    return row


def _dedupe(items: list[dict]) -> None:
    """Exchanges often get the same PDF filed twice (e.g. under two subjects). Keep the higher score."""
    best: dict[tuple, dict] = {}
    for it in items:
        k = (it["symbol"], it.get("url") or it["id"])
        if k not in best or it["score"] > best[k]["score"]:
            best[k] = it
    keep = {id(v) for v in best.values()}
    items[:] = [i for i in items if id(i) in keep]
