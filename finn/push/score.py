"""Materiality scoring.

score = category base + content modifiers + market reaction. Every point added is
explained in `why`, so the investor can see why something is ranked where it is.
Tiers (applied in the app, after the user's own category preferences):
  >= 70 act · 40-69 know · < 40 routine (collapsed)
"""
from __future__ import annotations

import re
import statistics

BASE = {
    "results": 78, "M&A": 80, "management_change": 62, "litigation": 60, "fund_raise": 66,
    "credit_rating": 48, "pledging": 64, "insider_trading": 44, "capex": 58, "dividend": 34,
    "bonus_split": 46, "press_release": 36, "earnings_call": 28, "investor_meetings": 12,
    "postal_ballot": 18, "agm_egm": 18, "unclassified": 4,
}

SUBTYPE = {("results", "intimation"): -50, ("results", "ad"): -70,
           ("earnings_call", "transcript"): +14, ("unclassified", "esop"): 0}

MODIFIERS: list[tuple[str, str, int, str]] = [
    # (category or *, regex on text, delta, reason)
    ("management_change", r"chief executive|\bceo\b|managing director|\bmd\b", 22, "Top job changes hands"),
    ("management_change", r"chief financial|\bcfo\b", 20, "CFO change"),
    ("management_change", r"resign", 8, "Resignation, not a planned rotation"),
    ("management_change", r"independent director|company secretary|compliance officer", -22,
     "Board or secretarial role, not operating management"),
    ("credit_rating", r"downgrad|negative|watch with negative", 28, "Rating downgrade or negative outlook"),
    ("credit_rating", r"upgrad|positive", 10, "Rating upgrade"),
    ("credit_rating", r"reaffirm", -18, "Rating reaffirmed, no change"),
    ("litigation", r"insolvency|nclt|cirp|fraud|sebi order|search and seizure", 22, "Severe legal/regulatory action"),
    ("litigation", r"tax demand|gst|income tax", -6, "Tax demand: usually contested for years"),
    ("pledging", r"creation|invoked|invocation", 12, "Fresh pledge or invocation"),
    ("pledging", r"release|revocation", -20, "Pledge released"),
    ("insider_trading", r"promoter", 10, "Promoter is the counterparty"),
    ("fund_raise", r"buy-?back", 6, "Buyback returns cash"),
    ("fund_raise", r"commercial paper|ncd|non-convertible", -24, "Routine debt rollover"),
    ("press_release", r"order|contract|letter of award", 14, "Order win"),
    ("M&A", r"incorporation of (a )?(wholly owned )?subsidiary", -40, "Shell subsidiary set-up"),
]
_MODS = [(c, re.compile(p), d, why) for c, p, d, why in MODIFIERS]

_CRORE = re.compile(r"(?:rs\.?|inr|₹)\s?([\d,]+(?:\.\d+)?)\s?(crore|cr\b|lakh|million|bn|billion)", re.I)


def _amount_cr(text: str) -> float | None:
    best = None
    for m in _CRORE.finditer(text):
        v = float(m.group(1).replace(",", ""))
        unit = m.group(2).lower()
        v = v / 100 if unit == "lakh" else v / 10 if unit == "million" else v * 100 if unit in ("bn", "billion") else v
        best = max(best or 0, v)
    return best


def reaction(prices: dict | None, day: str) -> tuple[float, float] | None:
    """Return (move %, z-score vs 60d daily moves) on the first session on/after `day`."""
    if not prices or not prices.get("d"):
        return None
    d, c = prices["d"], prices["c"]
    idx = next((i for i, x in enumerate(d) if x >= day[:10]), None)
    if idx is None or idx == 0:
        return None
    rets = [(c[i] / c[i - 1] - 1) * 100 for i in range(max(1, idx - 60), idx)]
    move = (c[idx] / c[idx - 1] - 1) * 100
    sd = statistics.pstdev(rets) if len(rets) > 5 else 2.0
    return move, (move / sd if sd else 0.0)


def score(item: dict, prices: dict | None = None, market_cap_cr: float | None = None) -> dict:
    cat, sub = item["category"], item.get("subtype", "")
    body = item.get("text", "") or ""
    # The exchange subject is often a catch-all ("Change in Directors/ KMP/ Auditor/ Compliance Officer..."),
    # so modifiers read the filing text when there is enough of it.
    text = (body if len(body) > 40 else f"{item.get('subject', '')} {body}").lower()
    s = BASE.get(cat, 10)
    why = [_label(cat, sub)]
    s += SUBTYPE.get((cat, sub), 0)
    for c, rx, d, reason in _MODS:
        if (c == cat or c == "*") and rx.search(text):
            s += d
            why.append(reason)
    amt = _amount_cr(text)
    if amt and market_cap_cr:
        pct = amt / market_cap_cr * 100
        if pct >= 2:
            s += min(18, int(pct * 2))
            why.append(f"Size: ₹{amt:,.0f} cr ≈ {pct:.1f}% of market cap")
    r = reaction(prices, item.get("ts", "")) if cat not in NO_REACTION else None
    if r:
        move, z = r
        if abs(z) >= 2 and abs(move) >= 2:
            s += 12
            why.append(f"Stock moved {move:+.1f}% that session ({abs(z):.1f}σ)")
        item["reaction_pct"] = round(move, 2)
    item["score"] = max(0, min(100, int(s)))
    item["why"] = why
    return item


NO_REACTION = {"unclassified", "investor_meetings", "agm_egm", "postal_ballot", "earnings_call"}
LABEL = {"M&A": "M&A", "management_change": "Management change", "fund_raise": "Fund raise",
         "insider_trading": "Insider trade", "earnings_call": "Earnings call", "investor_meetings": "Investor meeting",
         "credit_rating": "Credit rating", "bonus_split": "Bonus or split", "press_release": "Company update",
         "postal_ballot": "Postal ballot", "agm_egm": "Shareholder meeting", "unclassified": "Routine filing",
         "results": "Results", "litigation": "Legal or regulatory", "dividend": "Dividend", "capex": "Capex",
         "pledging": "Promoter pledge"}


def _label(cat: str, sub: str) -> str:
    return {
        ("results", "results"): "Results filing",
        ("results", "intimation"): "Results date notice only",
        ("results", "ad"): "Newspaper ad of results",
        ("earnings_call", "transcript"): "Concall transcript available",
    }.get((cat, sub), LABEL.get(cat, cat))


def tier(s: int) -> str:
    return "act" if s >= 70 else "know" if s >= 40 else "routine"
