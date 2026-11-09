"""On-demand research for any NSE symbol screener.in covers.

Pipeline (cost grows only where judgment is needed):
  1. screener tables  -> metrics.py           deterministic, free
  2. last 4 transcripts -> extract.py          fast model, ~10 chunks each, quotes verified
  3. latest annual report -> business snapshot fast model, BM25-selected chunks only
  4. reconcile.py                               one smart-model call, citations validated
Each stage degrades gracefully: no API key still yields stages 1 + document links.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Callable

from .. import cache, llm
from ..config import RESEARCH
from ..sources import documents, screener
from . import extract, metrics, reconcile

log = logging.getLogger("finn.dossier")

SNAPSHOT_SYS = """From these annual-report excerpts, describe the business for an investor in plain English.
Use ONLY the excerpts. Return JSON:
{"what_it_does":"<2 sentences>","segments":[{"name":"","share_pct":<number or null>,"note":"<=12 words","page":<int>}],
 "geography":"<one line or empty>","customers":"<one line or empty>"}
share_pct only if the excerpt states revenue share or the segment revenue to compute it."""

# Annual reports run to 300-400 pages. The business overview, segments and MD&A sit in the first
# ~200; parsing the rest costs minutes and memory on a small server for little gain.
AR_MAX_PAGES = int(os.environ.get("FINN_AR_MAX_PAGES", "200"))

SNAPSHOT_Q = "segment revenue business overview products customers geography share of revenue segment results"


def build(symbol: str, progress: Callable[[str, int], None] = lambda s, p: None, n_calls: int = 4) -> dict:
    symbol = symbol.upper().strip()
    with llm.ledger() as led:
        progress("Reading financial statements", 5)
        scr = screener.company(symbol)
        m = metrics.compute(scr)
        sources = [{"id": sid, "type": "statements", "label": label, "url": scr["url"]} for sid, label in (
            ("SCR-TOP", "Key ratios"), ("SCR-PL", "Profit & loss, annual"), ("SCR-Q", "Quarterly results"),
            ("SCR-BS", "Balance sheet"), ("SCR-CF", "Cash flow statement"), ("SCR-RATIOS", "Efficiency ratios"))]
        all_chunks: list[dict] = []
        extracts: list[dict] = []
        calls = [c for c in scr["docs"]["concalls"] if c.get("transcript")][:n_calls]
        for i, c in enumerate(calls, start=1):
            sid = f"T{i}"
            q = extract.call_quarter(c["date"])
            sources.append({"id": sid, "type": "transcript", "label": f"{q} earnings call", "date": c["date"],
                            "url": c["transcript"]})
            progress(f"Reading {q} call transcript", 10 + i * 15)
            try:
                pages = documents.fetch_pages(c["transcript"])
                all_chunks += documents.chunks(pages, sid)
            except Exception as e:  # noqa: BLE001
                log.warning("transcript %s: couldn't download or read: %s", c["transcript"], e)
                sources[-1]["error"] = str(e)[:120]
                continue
            if llm.available():
                try:
                    extracts.append(extract.extract_transcript(c["transcript"], sid, q))
                except Exception as e:  # noqa: BLE001
                    log.warning("transcript %s: model step failed: %s", sid, e)

        snapshot = {"what_it_does": scr.get("about", ""), "segments": [], "source": "SCR-TOP"}
        ars = sorted([a for a in scr["docs"]["annual_reports"] if a.get("url")],
                     key=lambda a: a.get("year") or 0, reverse=True)
        if ars:
            ar = ars[0]
            sources.append({"id": "A1", "type": "annual_report", "label": f"Annual report {ar.get('year') or ''}".strip(),
                            "url": ar["url"]})
            progress("Scanning the annual report", 75)
            ar_chunks = []
            try:
                pages = documents.fetch_pages(ar["url"], max_pages=AR_MAX_PAGES)
                ar_chunks = documents.chunks(pages, "A1")
                all_chunks += ar_chunks
            except Exception as e:  # noqa: BLE001
                log.warning("annual report: couldn't download or read: %s", e)
                sources[-1]["error"] = str(e)[:120]
            try:
                if ar_chunks and llm.available():
                    top = documents.BM25(ar_chunks).search(SNAPSHOT_Q, k=8)
                    ex = "\n\n".join(f"[p{c['page']}] {c['text']}" for c in top)
                    snap = llm.obj(llm.complete("fast", SNAPSHOT_SYS, ex, max_tokens=900))
                    snapshot = {**snap, "source": "A1"}
            except Exception as e:  # noqa: BLE001
                log.warning("annual report: model step failed: %s", e)

        analysis = None
        if llm.available() and any(ex.get("guidance") or ex.get("claims") for ex in extracts):
            progress("Checking promises against delivery", 88)
            try:
                analysis = reconcile.run(extracts, m)
            except Exception as e:  # noqa: BLE001
                log.warning("reconcile failed: %s", e)

        guidance = [g for ex in extracts for g in ex.get("guidance", [])]
        cache.put("chunks", symbol, all_chunks)
        cost = led.as_dict()

    dossier = {
        "symbol": symbol, "name": scr["name"], "basis": scr["basis"], "mode": "live",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sources": sources, "snapshot": snapshot, "metrics": m, "guidance": guidance,
        "extracts": [{k: ex.get(k) for k in ("source", "quarter", "tone", "dropped_quotes")} for ex in extracts],
        "claims": [c for ex in extracts for c in ex.get("claims", [])],
        "pushback": [c for ex in extracts for c in ex.get("pushback", [])],
        "analysis": analysis, "llm": llm.available(), "cost": cost,
    }
    (RESEARCH / f"{symbol}.json").write_text(json.dumps(dossier, indent=1, default=str))
    _update_index(dossier)
    progress("Done", 100)
    return dossier


def _update_index(d: dict) -> None:
    p = RESEARCH / "index.json"
    idx = json.loads(p.read_text()) if p.exists() else []
    idx = [x for x in idx if x["symbol"] != d["symbol"]]
    a = d.get("analysis") or {}
    sc = a.get("scorecard", [])
    idx.insert(0, {"symbol": d["symbol"], "name": d["name"], "generated_at": d["generated_at"], "mode": d["mode"],
                   "kept": sum(1 for s in sc if s.get("verdict") in ("delivered", "on_track")), "promises": len(sc)})
    p.write_text(json.dumps(idx, indent=1))


ROUTE_SMART = ("why", "compare", "should", "risk", "versus", " vs", "credib", "consistent", "explain", "reconcile")

ASK_SYS = """Answer an investor's question about {name} using ONLY the excerpts and numbers given.
If the excerpts don't contain the answer, say so plainly in one sentence. Be concise (<=120 words), name numbers.
Cite every factual sentence with markers like [T2 p4] or [SCR-PL] matching the provided labels.
Return JSON {{"answer":"","cites":[{{"src":"T2","page":4}}]}}"""


def _rebuild_chunks(d: dict) -> list[dict]:
    """A report built on another machine (or before a server restart) has no text index here yet.
    Re-read its source documents once; later questions hit the cache."""
    out = []
    for s in d.get("sources", []):
        if s.get("type") not in ("transcript", "annual_report") or not s.get("url"):
            continue
        try:
            mp = AR_MAX_PAGES if s["type"] == "annual_report" else None
            out += documents.chunks(documents.fetch_pages(s["url"], max_pages=mp), s["id"])
        except Exception as e:  # noqa: BLE001
            log.warning("ask: couldn't re-read %s: %s", s.get("label"), e)
    if out:
        cache.put("chunks", d["symbol"], out)
    return out


def ask(symbol: str, question: str) -> dict:
    symbol = symbol.upper()
    p = RESEARCH / f"{symbol}.json"
    if not p.exists():
        raise FileNotFoundError(f"No dossier for {symbol} yet; build it first.")
    d = json.loads(p.read_text())
    chunks = cache.get("chunks", symbol) or _rebuild_chunks(d)
    top = documents.BM25(chunks).search(question, k=7) if chunks else []
    nums = metrics.actuals_table(d["metrics"])
    ctx = "\n\n".join(f"[{c['src']} p{c['page']}] {c['text']}" for c in top)
    tier = "smart" if any(w in question.lower() for w in ROUTE_SMART) else "fast"
    with llm.ledger() as led:
        out = llm.complete(tier, ASK_SYS.format(name=d["name"]),
                           f"QUESTION: {question}\n\nNUMBERS [SCR-PL / SCR-Q]:\n{nums}\n\nEXCERPTS:\n{ctx}",
                           max_tokens=700)
        cost = led.as_dict()
    valid = {(c["src"], c["page"]) for c in top}
    out = llm.obj(out)
    cites = [c for c in out.get("cites", []) if isinstance(c, dict) if (c.get("src"), c.get("page")) in valid or
             str(c.get("src", "")).startswith("SCR")]
    srcs = {s["id"]: s for s in d["sources"]}
    for c in cites:
        s = srcs.get(c["src"]) or srcs.get("SCR-PL")
        c["label"], c["url"] = s.get("label"), s.get("url")
    return {"answer": out.get("answer", ""), "cites": cites, "model_tier": tier, "cost": cost}
