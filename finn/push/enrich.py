"""Turn a high-priority filing into something readable at a glance.

results            -> Revenue / EBITDA / PBT / PAT, this quarter vs QoQ vs YoY, read from the filing itself
management_change  -> who is coming/going, plus the incoming person's background
everything else    -> a one-line summary, or nothing if the exchange text already says it

Routing: deterministic parsing first, the fast model only when parsing fails, and every
number the model returns must appear in the source page or it is discarded.
"""
from __future__ import annotations

import logging
import re

import requests

from .. import llm
from ..config import env
from ..sources import documents, screener

log = logging.getLogger("finn.enrich")

# ---------------------------------------------------------------- results ---------
NUM = r"\(?-?\d{1,3}(?:,\d{2,3})*(?:\.\d+)?\)?|\(?-?\d+(?:\.\d+)?\)?"
LABELS = {
    "revenue": [r"revenue from operations", r"total income from operations", r"income from operations",
                r"interest earned", r"net sales"],
    "other_income": [r"other income"],
    "finance_costs": [r"finance costs?", r"interest expended", r"interest expense"],
    "depreciation": [r"depreciation(?:,| and) amorti[sz]ation(?: expenses?)?", r"depreciation"],
    "pbt": [r"profit\s*/?\s*\(?loss\)?\s*before tax", r"profit before tax"],
    "pat": [r"net profit\s*/?\s*\(?loss\)?\s*for the (?:period|quarter)", r"profit\s*/?\s*\(?loss\)?\s*for the (?:period|quarter)",
            r"profit after tax", r"net profit for the (?:period|quarter)"],
}


def _to_float(tok: str) -> float:
    neg = tok.startswith("(") and tok.endswith(")") or tok.startswith("-")
    v = float(tok.strip("()-").replace(",", ""))
    return -v if neg else v


def _line_numbers(line: str, label_end: int) -> list[float]:
    rest = line[label_end:]
    rest = re.sub(r"\([^)]*[a-zA-Z\-+][^)]*\)", " ", rest)  # drop "(refer note 4)", "(5-6)"
    return [_to_float(t) for t in re.findall(NUM, rest)]


def _unit_divisor(text: str) -> tuple[float, str]:
    t = text.lower()
    if re.search(r"in\s+lakhs?|₹\s*lakhs?|rs\.?\s*lakhs?|lacs", t):
        return 100.0, "lakh"
    if re.search(r"in\s+millions?|₹\s*millions?|rs\.?\s*millions?|inr\s*mn", t):
        return 10.0, "million"
    return 1.0, "crore"


_DATE = re.compile(r"(\d{1,2})[./-](\d{1,2})[./-](20\d\d)|"
                   r"(june|september|december|march|jun|sep|dec|mar)[a-z]*\.?\s*(\d{1,2}),?\s*(20\d\d)|"
                   r"(\d{1,2})[\s-](jun|sep|dec|mar)[a-z]*[\s-](20\d\d|\d\d)", re.I)
_MON = {"mar": 3, "jun": 6, "sep": 9, "dec": 12}


def _quarter_label(month: int, year: int) -> str:
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(month)
    if not q:
        return f"{month:02d}/{year}"
    fy = year + 1 if month > 3 else year
    return f"Q{q} FY{str(fy)[-2:]}"


def _periods(text: str) -> list[str]:
    out = []
    for m in _DATE.finditer(text[:4000]):
        if m.group(1):
            mon, yr = int(m.group(2)), int(m.group(3))
        elif m.group(4):
            mon, yr = _MON[m.group(4)[:3].lower()], int(m.group(6))
        else:
            mon, yr = _MON[m.group(8)[:3].lower()], int(m.group(9)) % 100 + 2000
        lab = _quarter_label(mon, yr)
        if lab not in out:
            out.append(lab)
        if len(out) == 3:
            break
    return out


def parse_results_page(text: str) -> dict | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    found: dict[str, list[float]] = {}
    for key, pats in LABELS.items():
        for pat in pats:
            rx = re.compile(pat, re.I)
            for ln in lines:
                m = rx.search(ln)
                if not m:
                    continue
                nums = _line_numbers(ln, m.end())
                if len(nums) >= 3:
                    found[key] = nums[:3]
                    break
            if key in found:
                break
    if not all(k in found for k in ("revenue", "pbt", "pat")):
        return None
    rev, pbt, pat = found["revenue"], found["pbt"], found["pat"]
    if any(r <= 0 for r in rev) or any(abs(p) > abs(r) * 1.5 for p, r in zip(pat, rev)):
        return None
    div, unit = _unit_divisor(text[:3000])
    return {"values": {k: [v / div for v in vs] for k, vs in found.items()},
            "unit_in_filing": unit, "periods": _periods(text), "method": "parsed"}


def _llm_results(text: str) -> dict | None:
    sys_ = ("Extract quarterly results from an Indian company's results filing. Use the CONSOLIDATED table if "
            "present. Columns are usually: current quarter, previous quarter, same quarter last year. "
            "Copy numbers exactly as printed (no unit conversion). Return JSON: "
            '{"basis":"consolidated|standalone","unit":"crore|lakh|million","periods":["Q? FY??",...3],'
            '"revenue":[3 numbers],"other_income":[3],"finance_costs":[3],"depreciation":[3],"pbt":[3],"pat":[3]} '
            "Use null for anything not present.")
    try:
        out = llm.complete("fast", sys_, text[:9000], max_tokens=600)
    except Exception as e:  # noqa: BLE001
        log.warning("LLM results extraction failed: %s", e)
        return None
    flat = re.sub(r"[,\s]", "", text)
    vals = {}
    for k in LABELS:
        arr = out.get(k)
        if not isinstance(arr, list) or len(arr) < 3 or any(x is None for x in arr[:3]):
            continue
        # every number must literally appear in the filing (grouping commas ignored)
        if all(_appears(float(x), flat) for x in arr[:3]):
            vals[k] = [float(x) for x in arr[:3]]
    if not all(k in vals for k in ("revenue", "pbt", "pat")):
        return None
    div = {"lakh": 100.0, "million": 10.0}.get(out.get("unit", "crore"), 1.0)
    return {"values": {k: [v / div for v in vs] for k, vs in vals.items()},
            "unit_in_filing": out.get("unit", "crore"), "periods": out.get("periods") or [],
            "basis": out.get("basis"), "method": "model, numbers verified against filing"}


def _appears(x: float, flat: str) -> bool:
    a = abs(x)
    return any(s in flat for s in {f"{a:g}", f"{a:.2f}", f"{a:.1f}", f"{a:.0f}"})


def _card_from(vals: dict, periods: list[str], source: str, method: str, basis: str | None) -> dict:
    v = vals
    ebitda_note = None
    if "ebitda" not in v:
        if all(k in v for k in ("finance_costs", "depreciation")):
            oi = v.get("other_income", [0, 0, 0])
            v["ebitda"] = [p + f + d - o for p, f, d, o in zip(v["pbt"], v["finance_costs"], v["depreciation"], oi)]
            ebitda_note = "EBITDA = PBT + finance costs + depreciation − other income (not reported directly)"
    rows = []
    for key, label in (("revenue", "Revenue"), ("ebitda", "EBITDA"), ("pbt", "PBT"), ("pat", "PAT")):
        if key not in v:
            continue
        cur, prev, yoy = v[key][:3]
        rows.append({"metric": label, "cur": round(cur, 1),
                     "qoq": _chg(cur, prev), "yoy": _chg(cur, yoy),
                     "prev": round(prev, 1), "year_ago": round(yoy, 1)})
    margin = None
    if "ebitda" in v:
        m = [e / r * 100 for e, r in zip(v["ebitda"], v["revenue"])]
        margin = {"cur": round(m[0], 1), "qoq_bps": round((m[0] - m[1]) * 100), "yoy_bps": round((m[0] - m[2]) * 100)}
    return {"type": "results", "periods": (periods + ["", "", ""])[:3] if periods else ["This qtr", "Prev qtr", "Year ago"],
            "unit": "₹ cr", "rows": rows, "ebitda_margin": margin, "note": ebitda_note,
            "source": source, "method": method, "basis": basis}


def _chg(a: float, b: float) -> float | None:
    if not b:
        return None
    return round((a - b) / abs(b) * 100, 1)


def results_card(item: dict, scr: dict | None) -> dict | None:
    if (item.get("url") or "").lower().endswith(".pdf"):
        try:
            pages = documents.fetch_pages(item["url"], max_pages=14)
            order = sorted(range(len(pages)), key=lambda i: ("consolidated" not in pages[i].lower(), i))
            for i in order:
                if not re.search(r"revenue from operations|interest earned|income from operations", pages[i], re.I):
                    continue
                parsed = parse_results_page(pages[i])
                if parsed:
                    basis = "consolidated" if "consolidated" in pages[i].lower() else "standalone"
                    return _card_from(parsed["values"], parsed["periods"], "filing", parsed["method"], basis)
            best = next((pages[i] for i in order if re.search(r"profit before tax", pages[i], re.I)), None)
            if best and llm.available():
                got = _llm_results(best)
                if got:
                    return _card_from(got["values"], got["periods"], "filing", got["method"], got.get("basis"))
        except Exception as e:  # noqa: BLE001
            log.warning("results filing parse failed for %s: %s", item["symbol"], e)
    return results_from_screener(scr)


def results_from_screener(scr: dict | None) -> dict | None:
    q = scr and scr.get("quarters")
    if not q or len(q["periods"]) < 5:
        return None
    rev = screener.row(q, "Sales", "Revenue")
    op = screener.row(q, "Operating Profit", "Financing Profit")
    pbt = screener.row(q, "Profit before tax")
    pat = screener.row(q, "Net Profit")
    if not (rev and pbt and pat):
        return None
    idx = [-1, -2, -5]
    vals = {"revenue": [rev[i] for i in idx], "pbt": [pbt[i] for i in idx], "pat": [pat[i] for i in idx]}
    if op:
        vals["ebitda"] = [op[i] for i in idx]
    if any(x is None for vs in vals.values() for x in vs):
        return None
    labels = []
    for i in idx:
        p = q["periods"][i]
        m = re.match(r"([A-Za-z]{3})\s+(\d{4})", p)
        labels.append(_quarter_label(_MON.get(m.group(1).lower(), 0), int(m.group(2))) if m else p)
    card = _card_from(vals, labels, "screener", "standardised quarterly table (filing PDF not parsed)",
                      scr.get("basis"))
    if op:
        card["note"] = "EBITDA = screener 'Operating profit' (revenue − operating expenses)"
    return card


# ------------------------------------------------------- management change ---------
_NAME = re.compile(r"\b(?:Mr|Ms|Mrs|Dr|Shri|Smt)\.?\s+([A-Z][a-zA-Z.]+(?:\s+[A-Z][a-zA-Z.]+){0,3})")


def mgmt_card(item: dict) -> dict | None:
    text = item.get("text", "")
    if (item.get("url") or "").lower().endswith(".pdf"):
        try:
            text = text + "\n" + "\n".join(documents.fetch_pages(item["url"], max_pages=5))
        except Exception as e:  # noqa: BLE001
            log.warning("mgmt filing fetch failed: %s", e)
    card = None
    if llm.available():
        sys_ = ("From this Indian stock-exchange filing about a management change, extract each change. "
                "Use ONLY the filing text. Listed companies must include a 'brief profile' of appointees; "
                "summarise it in at most 2 sentences (prior employers, years, qualifications). Return JSON "
                '{"changes":[{"person":"","role":"","change":"appointed|resigned|retired|redesignated|ceased",'
                '"effective":"","reason":"","bio":"","previous":["Company — role"]}]}')
        try:
            out = llm.complete("fast", sys_, text[:9000], max_tokens=700)
            ch = [c for c in out.get("changes", []) if c.get("person")]
            if ch:
                card = {"type": "mgmt", "changes": ch, "bio_source": "filing"}
        except Exception as e:  # noqa: BLE001
            log.warning("mgmt LLM failed: %s", e)
    if not card:
        names = list(dict.fromkeys(m.group(1).strip() for m in _NAME.finditer(text)))[:3]
        prof = re.search(r"brief profile[^:]*:?\s*(.{40,600}?)(?:\n\s*\n|$)", text, re.I | re.S)
        if not names:
            return None
        card = {"type": "mgmt", "bio_source": "filing",
                "changes": [{"person": n, "role": "", "change": "resigned" if "resign" in text.lower() else "appointed",
                             "bio": re.sub(r"\s+", " ", prof.group(1)).strip() if prof and i == 0 else ""}
                            for i, n in enumerate(names)]}
    _web_enrich(card, item)
    return card


def _web_enrich(card: dict, item: dict) -> None:
    """Optional: background from the open web (Tavily) for appointees with a thin filing profile.

    We never scrape LinkedIn directly (login wall, terms of use); a search API returns public snippets,
    which the fast model condenses and the card labels as 'web'.
    """
    key = env("FINN_SEARCH_API_KEY")
    if not key or not llm.available():
        return
    for ch in card["changes"]:
        if ch.get("change") not in ("appointed", "redesignated") or len(ch.get("bio") or "") > 120:
            continue
        try:
            r = requests.post("https://api.tavily.com/search", timeout=30,
                              headers={"Authorization": f"Bearer {key}"},
                              json={"query": f"{ch['person']} {ch.get('role', '')} linkedin career", "max_results": 4})
            r.raise_for_status()
            snippets = "\n".join(f"- {x.get('title')}: {x.get('content', '')[:400]} ({x.get('url')})"
                                 for x in r.json().get("results", []))
            out = llm.complete("fast", "Write a 2-sentence professional background for this person using only the "
                               "snippets. If snippets look like a different person, return empty bio. "
                               'JSON {"bio":"","sources":["url"]}',
                               f"Person: {ch['person']}, joining {item.get('name', item['symbol'])} as {ch.get('role')}\n"
                               f"{snippets}", max_tokens=250)
            if out.get("bio"):
                ch["web_bio"] = out["bio"]
                ch["web_sources"] = out.get("sources", [])[:3]
        except Exception as e:  # noqa: BLE001
            log.warning("web enrichment failed for %s: %s", ch.get("person"), e)


# ------------------------------------------------------------- one-liners ---------
_BOILER = re.compile(r"^.*?\bha(?:s|ve) informed the exchange (?:about|regarding|that|of)\s*", re.I)


def clean_text(text: str) -> str:
    t = _BOILER.sub("", text or "").strip()
    t = re.sub(r"\s+", " ", t)
    return (t[:1].upper() + t[1:]) if t else ""


def is_generic(text: str) -> bool:
    t = clean_text(text).lower()
    return len(t) < 45 or bool(re.fullmatch(r"(the )?(press release|updates?|general updates?|"
                                            r"change in (director|management)\(?s?\)?.*|disclosure.*)\.?", t))


def one_liners(items: list[dict], limit: int = 30) -> None:
    """Fill `summary`. Exchange text is used as-is when it is informative; otherwise the first two
    pages of the PDF go to the fast model in batches of 10."""
    need = []
    for it in items:
        ct = clean_text(it.get("text", ""))
        if not is_generic(it.get("text", "")):
            it["summary"] = ct[:220]
        elif it.get("url") and len(need) < limit:
            need.append(it)
    if not need or not llm.available():
        return
    for start in range(0, len(need), 10):
        batch = need[start:start + 10]
        blocks = []
        for i, it in enumerate(batch):
            try:
                body = "\n".join(documents.fetch_pages(it["url"], max_pages=2))[:2500]
            except Exception:  # noqa: BLE001
                body = it.get("text", "")
            blocks.append(f"### {i} | {it['symbol']} | {it['subject']}\n{body}")
        try:
            out = llm.complete("fast", "For each filing write ONE plain-English line (max 22 words) saying what "
                               "actually happened, with the key number if any. No hype. "
                               'JSON {"lines":[{"i":0,"line":""}]}', "\n\n".join(blocks), max_tokens=900)
            for row in out.get("lines", []):
                i = int(row.get("i", -1))
                if 0 <= i < len(batch) and row.get("line"):
                    batch[i]["summary"] = row["line"].strip()
                    batch[i]["summary_by"] = "model"
        except Exception as e:  # noqa: BLE001
            log.warning("one-liner batch failed: %s", e)
