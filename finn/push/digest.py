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


class KeptPrevious(RuntimeError):
    """The refresh couldn't reach a core source, so the last good feed was left in place."""


def build(days: int = 30, enrich_items: bool = True) -> dict:
    cfg = portfolio_config()
    end = date.today()
    start = end - timedelta(days=days)
    holdings, all_items = [], []
    syms = [h["symbol"] for h in cfg["portfolio"]]
    log.info("prices for %d holdings", len(syms))
    full, notes = prices.eod_many(syms)
    series = {s: {k: v[k] for k in ("d", "c", "v")} for s, v in full.items()}
    price_src = sorted({v["src"] for v in full.values()})
    nse_fail = scr_fail = 0
    nse_ok = False
    with llm.ledger() as led:
        for h in cfg["portfolio"]:
            sym = h["symbol"]
            log.info("· %s", sym)
            p = series.get(sym)
            scr = None
            if scr_fail < 3:
                try:
                    scr = screener.company(sym)
                except Exception as e:  # noqa: BLE001
                    scr_fail += 1
                    log.warning("screener %s: %s", sym, e)
                    if scr_fail == 3:
                        notes.append(f"screener.in: skipped after 3 failures ({str(e)[:120]}). "
                                     "Results cards fall back to the filing PDF only.")
            mcap = (scr or {}).get("top", {}).get("Market Cap")
            items = []
            if nse_ok or nse_fail < 3:  # if NSE blocks the first three, don't spend minutes retrying the rest
                try:
                    items = nse.announcements(sym, start, end)
                    nse_ok = True
                except Exception as e:  # noqa: BLE001
                    nse_fail += 1
                    log.warning("NSE filings %s: %s", sym, e)
                    if nse_fail == 3 and not nse_ok:
                        notes.append(f"NSE filings: blocked on this network ({str(e)[:140]}). "
                                     "The brief shows prices only.")
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
        "cost": cost, "price_sources": price_src, "notes": notes,
    }
    prev = DATA / "feed.json"
    if prev.exists():
        try:
            old_feed = json.loads(prev.read_text())
        except ValueError:
            old_feed = None
        # A cloud server that NSE blocks would otherwise publish an empty brief over a good one.
        if old_feed and old_feed.get("mode") == "live" and old_feed.get("announcements"):
            if not nse_ok:
                raise KeptPrevious("NSE didn't respond to this server, so the brief from "
                                   f"{old_feed.get('generated_at', 'the last refresh')} is still shown.")
            if not series and old_feed.get("prices"):
                raise KeptPrevious("No price source responded, so the previous brief is still shown.")
    if nse_fail and nse_ok:
        notes.append(f"NSE filings: failed for {nse_fail} of {len(syms)} holdings; run refresh again to retry.")
    (DATA / "feed.json").write_text(json.dumps(feed, indent=1, default=str))
    log.info("feed: %d holdings (%d priced via %s), %d filings, cost %s",
             len(holdings), len(series), ", ".join(price_src) or "none", len(all_items), cost)
    for n in notes:
        log.warning("note: %s", n)
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
