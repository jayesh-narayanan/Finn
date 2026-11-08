"""Deterministic analytics from the standardised statements.

Snapshot, trajectory, balance sheet and cash quality are arithmetic, not judgment,
so no model is involved: numbers here cannot be hallucinated, cost nothing, and every
note carries the statement it came from.
"""
from __future__ import annotations

import re

from ..sources.screener import row


def _fy(periods: list[str]) -> list[str]:
    out = []
    for p in periods:
        m = re.match(r"[A-Za-z]{3}\s+(\d{4})", p)
        out.append(f"FY{m.group(1)[-2:]}" if m else p)
    return out


def _cagr(a: float | None, b: float | None, yrs: int) -> float | None:
    if not a or not b or a <= 0 or b <= 0 or yrs <= 0:
        return None
    return round(((b / a) ** (1 / yrs) - 1) * 100, 1)


def _strip_ttm(t: dict | None) -> tuple[list[str], dict]:
    """Annual columns only (drop TTM), keep last 7 years."""
    if not t:
        return [], {}
    idx = [i for i, p in enumerate(t["periods"]) if not p.upper().startswith("TTM")][-7:]
    return [t["periods"][i] for i in idx], {k: [v[i] if i < len(v) else None for i in idx] for k, v in t["rows"].items()}


def _r(vals, *names):
    return row({"rows": vals}, *names)


def compute(scr: dict) -> dict:
    is_fin = bool(row(scr.get("pl"), "Financing Profit") or row(scr.get("quarters"), "Financing Profit"))
    periods, pl = _strip_ttm(scr.get("pl"))
    years = _fy(periods)
    sales = _r(pl, "Sales", "Revenue") or []
    op = _r(pl, "Operating Profit", "Financing Profit") or []
    opm = _r(pl, "OPM", "Financing Margin") or []
    pat = _r(pl, "Net Profit") or []
    top = scr.get("top", {})

    # ---------------- financial snapshot
    head = []
    def add(label, value, unit="", cite="SCR-TOP", hint=None):
        if value is not None:
            head.append({"label": label, "value": value, "unit": unit, "cite": cite, "hint": hint})
    add("Market cap", top.get("Market Cap"), "₹ cr")
    add("P/E", top.get("Stock P/E"), "×")
    if sales:
        add(f"Revenue {years[-1]}", sales[-1], "₹ cr", "SCR-PL",
            f"{_pct(sales[-1], sales[-2])} YoY" if len(sales) > 1 else None)
    if opm and opm[-1] is not None:
        add("Financing margin" if is_fin else "EBITDA margin", opm[-1], "%", "SCR-PL")
    if pat:
        add(f"PAT {years[-1]}", pat[-1], "₹ cr", "SCR-PL", f"{_pct(pat[-1], pat[-2])} YoY" if len(pat) > 1 else None)
    add("ROCE", top.get("ROCE"), "%")
    add("ROE", top.get("ROE"), "%")

    # ---------------- trajectory
    traj_notes = []
    n = len(sales)
    if n >= 4:
        c3 = _cagr(sales[-4], sales[-1], 3)
        p3 = _cagr(pat[-4], pat[-1], 3) if len(pat) >= 4 else None
        traj_notes.append({"text": f"Revenue compounded {c3}% a year over 3 years"
                           + (f"; profit {p3}%" if p3 is not None else "")
                           + (" — profit outgrowing sales" if p3 and c3 and p3 > c3 + 3 else
                              " — profit lagging sales" if p3 is not None and c3 and p3 < c3 - 3 else ""),
                           "cite": "SCR-PL", "tone": "neutral"})
    if len(opm) >= 4 and opm[-1] is not None and opm[-4] is not None:
        d = opm[-1] - opm[-4]
        traj_notes.append({"text": f"{'Margin' if not is_fin else 'Financing margin'} "
                           f"{'expanded' if d > 0 else 'compressed'} {abs(d):.1f} pts in 3 years "
                           f"({opm[-4]:.0f}% → {opm[-1]:.0f}%)", "cite": "SCR-PL",
                           "tone": "good" if d > 1 else "bad" if d < -1 else "neutral"})
    qtr = _quarterly(scr)
    if qtr and len(qtr["rev_yoy"]) >= 4:
        last2 = [x for x in qtr["rev_yoy"][-2:] if x is not None]
        prev2 = [x for x in qtr["rev_yoy"][-4:-2] if x is not None]
        if last2 and prev2:
            a, b = sum(last2) / len(last2), sum(prev2) / len(prev2)
            word = "accelerating" if a > b + 2 else "decelerating" if a < b - 2 else "steady"
            traj_notes.append({"text": f"Quarterly revenue growth {word}: {a:.0f}% YoY over the last two quarters "
                               f"vs {b:.0f}% in the two before", "cite": "SCR-Q",
                               "tone": "good" if word == "accelerating" else "bad" if word == "decelerating" else "neutral"})

    # ---------------- balance sheet
    bper, bs = _strip_ttm(scr.get("bs"))
    byears = _fy(bper)
    eq = [(a or 0) + (b or 0) for a, b in zip(_r(bs, "Equity Capital") or [], _r(bs, "Reserves") or [])]
    debt = _r(bs, "Borrowings", "Borrowing") or []
    de = [round(d / e, 2) if d is not None and e else None for d, e in zip(debt, eq)]
    _, ratios = _strip_ttm(scr.get("ratios"))
    ccc = _r(ratios, "Cash Conversion Cycle")
    wc_days = _r(ratios, "Working Capital Days")
    debtor = _r(ratios, "Debtor Days")
    bs_notes = []
    if is_fin:
        bs_notes.append({"text": "Lender: borrowing is the raw material, so debt-to-equity is not a risk signal here. "
                         "Watch asset quality and capital adequacy in the filings instead.", "cite": "SCR-BS", "tone": "neutral"})
    elif de and de[-1] is not None:
        trend = f" (from {de[-4]:.2f}× in {byears[-4]})" if len(de) >= 4 and de[-4] is not None else ""
        bs_notes.append({"text": f"Effectively debt-free: debt is {de[-1]:.2f}× equity" if de[-1] < 0.1 else
                         f"Debt-to-equity {de[-1]:.2f}×{trend}" + (": leveraged" if de[-1] > 1 else ""),
                         "cite": "SCR-BS", "tone": "good" if de[-1] < 0.3 else "bad" if de[-1] > 1 else "neutral"})
    if ccc and len(ccc) >= 4 and ccc[-1] is not None and ccc[-4] is not None and not is_fin:
        d = ccc[-1] - ccc[-4]
        bs_notes.append({"text": f"Cash conversion cycle {ccc[-1]:.0f} days, {'up' if d > 0 else 'down'} "
                         f"{abs(d):.0f} days in 3 years" + (" — more cash getting stuck in operations" if d > 15 else ""),
                         "cite": "SCR-RATIOS", "tone": "bad" if d > 15 else "good" if d < -10 else "neutral"})
    if debtor and len(debtor) >= 2 and debtor[-1] and debtor[-2] and debtor[-1] > debtor[-2] * 1.25 and not is_fin:
        bs_notes.append({"text": f"Receivable days jumped {debtor[-2]:.0f} → {debtor[-1]:.0f}: customers paying slower",
                         "cite": "SCR-RATIOS", "tone": "bad"})

    # ---------------- cash quality
    cper, cf = _strip_ttm(scr.get("cf"))
    cyears = _fy(cper)
    cfo = _r(cf, "Cash from Operating") or []
    cfi = _r(cf, "Cash from Investing") or []
    pat_by_year = dict(zip(years, pat))
    conv, pat_aligned = [], []
    for y, c in zip(cyears, cfo):
        p = pat_by_year.get(y)
        pat_aligned.append(p)
        conv.append(round(c / p * 100) if c is not None and p and p > 0 else None)
    fcf = [round(a + b, 1) if a is not None and b is not None else None for a, b in zip(cfo, cfi)]
    cash_notes = []
    if not is_fin and len(cfo) >= 3:
        s_cfo = sum(x for x in cfo[-5:] if x is not None)
        s_pat = sum(x for x in pat_aligned[-5:] if x is not None)
        if s_pat > 0:
            r5 = s_cfo / s_pat * 100
            cash_notes.append({"text": f"Over {min(5, len(cfo))} years, operating cash flow was {r5:.0f}% of reported profit"
                               + (" — profits are turning into cash" if r5 >= 85 else
                                  " — a meaningful share of profit is not showing up as cash" if r5 < 70 else ""),
                               "cite": "SCR-CF", "tone": "good" if r5 >= 85 else "bad" if r5 < 70 else "neutral"})
        neg = sum(1 for x in fcf[-5:] if x is not None and x < 0)
        if neg >= 3:
            cash_notes.append({"text": f"Free cash flow (CFO + investing) negative in {neg} of the last 5 years: "
                               "growth is being funded, not self-financed", "cite": "SCR-CF", "tone": "bad"})
    elif is_fin:
        cash_notes.append({"text": "For lenders, operating cash flow swings with loan disbursal and is not a quality signal.",
                           "cite": "SCR-CF", "tone": "neutral"})

    return {
        "is_financial": is_fin,
        "headline": head,
        "trajectory": {"years": years, "revenue": sales, "op_profit": op, "margin": opm, "pat": pat,
                       "notes": traj_notes, "quarterly": qtr},
        "balance_sheet": {"years": byears, "debt": debt, "equity": eq, "de": de,
                          "ccc": ccc, "wc_days": wc_days, "notes": bs_notes},
        "cash": {"years": cyears, "cfo": cfo, "pat": pat_aligned, "conversion": conv, "fcf": fcf,
                 "notes": cash_notes},
    }


def _pct(a, b):
    if a is None or not b:
        return "—"
    return f"{(a - b) / abs(b) * 100:+.0f}%"


def _quarterly(scr: dict) -> dict | None:
    q = scr.get("quarters")
    if not q or len(q["periods"]) < 6:
        return None
    rev = row(q, "Sales", "Revenue") or []
    pat = row(q, "Net Profit") or []
    opm = row(q, "OPM", "Financing Margin") or []
    per = q["periods"]
    def yoy(arr):
        return [round((arr[i] / arr[i - 4] - 1) * 100, 1) if i >= 4 and arr[i] is not None and arr[i - 4] else None
                for i in range(len(arr))]
    k = 8
    return {"periods": per[-k:], "revenue": rev[-k:], "pat": pat[-k:], "margin": opm[-k:],
            "rev_yoy": yoy(rev)[-k:], "pat_yoy": yoy(pat)[-k:]}


def actuals_table(m: dict) -> str:
    """Compact text of delivered numbers for the reconciliation prompt."""
    q = m["trajectory"].get("quarterly") or {}
    lines = ["QUARTERLY (₹ cr; YoY %):"]
    for i, p in enumerate(q.get("periods", [])):
        lines.append(f"  F:Q{i} {p}: revenue {q['revenue'][i]} ({q['rev_yoy'][i]}%), margin {q['margin'][i]}%, "
                     f"PAT {q['pat'][i]} ({q['pat_yoy'][i]}%)")
    t = m["trajectory"]
    lines.append("ANNUAL:")
    for i, y in enumerate(t["years"]):
        lines.append(f"  F:{y}: revenue {t['revenue'][i]}, margin {t['margin'][i] if i < len(t['margin']) else None}%, "
                     f"PAT {t['pat'][i] if i < len(t['pat']) else None}")
    return "\n".join(lines)
