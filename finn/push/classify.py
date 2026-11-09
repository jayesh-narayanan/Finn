"""Announcement classification: rules first, model only for the leftovers.

Exchange subjects are semi-standardised, so ordered regex rules over subject + exchange
text classify the large majority for free. Only items that land in `unclassified` while
carrying substantive text are sent, batched, to the fast model.
"""
from __future__ import annotations

import logging
import re

from .. import llm

log = logging.getLogger("finn.classify")

CATEGORIES = ["results", "M&A", "fund_raise", "management_change", "litigation", "insider_trading",
              "earnings_call", "investor_meetings", "credit_rating", "dividend", "bonus_split", "capex",
              "press_release", "postal_ballot", "agm_egm", "pledging", "unclassified"]

# (category, subtype, pattern) — evaluated in order against "subject || text", lower-cased.
RULES: list[tuple[str, str, str]] = [
    ("unclassified", "routine", r"trading window|loss of share certificate|duplicate share|reg(ulation)?\.? ?74 ?\(5\)|"
                                r"certificate under sebi \(depositories|compliance certificate|esg|business responsibility|"
                                r"newspaper publication(?!.*result)|investor complaints|statement of deviation|"
                                r"related party transactions? (disclosure|for the half)|closure of trading"),
    ("unclassified", "esop", r"allotment of esop|esos|esps|employee stock option|esop"),
    ("unclassified", "auditor", r"(appointment|resignation|change) of (statutory |secretarial |internal |cost )?auditor"),
    ("pledging", "pledge", r"pledg|encumbrance|creation of encumbrance|release of encumbrance"),
    ("insider_trading", "pit", r"insider trading|sebi \(pit\)|prohibition of insider|regulation 7\(2\)|"
                               r"disclosure under sebi takeover|substantial acquisition|regulation 29"),
    ("results", "results", r"financial results?|integrated filing.{0,10}financial|un-?audited results|audited results|"
                           r"quarterly results|results for the (quarter|year|period)"),
    ("earnings_call", "transcript", r"transcript"),
    ("earnings_call", "recording", r"audio recording|video recording|recording of (the )?(earnings|conference)"),
    ("earnings_call", "call", r"earnings call|conference call|con\. call|concall"),
    ("investor_meetings", "meet", r"analysts?/institutional investor|investor meet|analyst meet|investor conference|"
                                  r"one[- ]on[- ]one|group meeting"),
    ("credit_rating", "rating", r"credit rating|\brating\b.*(crisil|icra|care|india ratings|brickwork|acuite)"),
    ("M&A", "deal", r"acquisition|acquire|amalgamation|merger|demerger|scheme of arrangement|joint venture|"
                    r"disinvestment|divest|sale of (stake|shares|business|undertaking)|slump sale|"
                    r"incorporation of (a )?(wholly owned )?subsidiary|investment in (wholly owned )?subsidiary"),
    ("fund_raise", "raise", r"\bqip\b|qualified institutions? placement|rights issue|preferential (issue|allotment)|"
                            r"buy-?back|raising of funds|fund raising|commercial paper|non-convertible debenture|"
                            r"\bncds?\b|allotment of (equity|securities|debentures)"),
    ("management_change", "mgmt", r"change in (directors|key managerial|management|senior management)|"
                                  r"appointment of|resignation|cessation|re-?appointment|retire|"
                                  r"chief executive|chief financial|managing director|whole[- ]time director|\bkmp\b"),
    ("litigation", "legal", r"litigation|penalty|show cause|tax demand|demand order|\bgst\b|income tax|"
                            r"\border\b.*(sebi|tribunal|court|authority)|nclt|insolvency|\bcirp\b|"
                            r"warning letter|regulatory action|search and seizure|fine imposed|dispute"),
    ("bonus_split", "corp_action", r"bonus|sub-?division|stock split|split of"),
    ("dividend", "dividend", r"dividend|record date"),
    ("capex", "capex", r"capacity expansion|capex|new (plant|facility|unit)|greenfield|brownfield|"
                       r"commissioning|commercial production|commence(ment of)? (commercial )?operations"),
    ("postal_ballot", "ballot", r"postal ballot|e-?voting result"),
    ("agm_egm", "meeting", r"annual general meeting|\bagm\b|extra-?ordinary general meeting|\begm\b|"
                           r"shareholders meeting|scrutini[sz]er"),
    ("press_release", "order", r"bagging|order (win|received|inflow)|receipt of (an )?order|letter of award|"
                               r"contract (award|win)|awarded"),
    ("press_release", "pr", r"press release|clarification|launch|partnership|collaboration|"
                            r"memorandum of understanding|\bmou\b|general updates?|^updates?$"),
    ("results", "intimation", r"board meeting intimation|outcome of board meeting"),
    ("unclassified", "routine", r"newspaper publication|intimation"),
]
_COMPILED = [(c, s, re.compile(p)) for c, s, p in RULES]


# Exchange subjects that say nothing about the content: classify these from the text.
GENERIC_SUBJECT = re.compile(r"outcome of board meeting|board meeting intimation|^general updates?$|^updates?$|"
                             r"press release|change in directors/ ?key managerial|^intimation|^disclosure|"
                             r"^other|^announcement")


def classify_rules(subject: str, text: str) -> tuple[str, str, float]:
    subj, txt = subject.lower().strip(), text.lower()
    order = [txt, subj] if GENERIC_SUBJECT.search(subj) and len(txt) > 30 else [subj, txt]
    for conf, hay in zip((0.9, 0.7), order):
        for cat, sub, rx in _COMPILED:
            if rx.search(hay):
                return cat, sub, conf
    return "unclassified", "unknown", 0.3


def refine_results(cat: str, sub: str, subject: str, text: str) -> str:
    """Distinguish an actual results filing from intimations and newspaper ads."""
    if cat != "results":
        return sub
    hay = f"{subject} {text}".lower()
    if "newspaper" in hay or "advertisement" in hay:
        return "ad"
    if "intimation" in hay or re.search(r"to be held|(to|will)( inter[- ]?alia)? (consider|approve)", hay):
        return "intimation"
    return "results"


def classify_all(items: list[dict], use_llm: bool = True) -> list[dict]:
    leftovers = []
    for it in items:
        cat, sub, conf = classify_rules(it["subject"], it.get("text", ""))
        sub = refine_results(cat, sub, it["subject"], it.get("text", ""))
        it.update(category=cat, subtype=sub, cls_conf=conf, cls_by="rules")
        if cat == "unclassified" and sub == "unknown" and len(it.get("text", "")) > 60:
            leftovers.append(it)
    if leftovers and use_llm and llm.available():
        _llm_batch(leftovers)
    return items


def _llm_batch(items: list[dict], batch: int = 25) -> None:
    sys_ = ("You classify Indian stock-exchange announcements. Categories: " + ", ".join(CATEGORIES) +
            ". 'unclassified' means routine paperwork that would not move the stock. "
            'Return JSON {"labels": [{"i": <index>, "category": <one of the categories>}]}')
    for start in range(0, len(items), batch):
        chunk = items[start:start + batch]
        lines = "\n".join(f"{i}. [{x['subject']}] {x.get('text', '')[:300]}" for i, x in enumerate(chunk))
        try:
            out = llm.obj(llm.complete("fast", sys_, lines, max_tokens=800), "labels")
            for lab in out.get("labels", []):
                i, cat = llm.num(lab.get("i")), lab.get("category")
                if i is None:
                    continue
                i = int(i)
                if 0 <= i < len(chunk) and cat in CATEGORIES:
                    chunk[i].update(category=cat, subtype="llm", cls_conf=0.6, cls_by="llm")
        except Exception as e:  # noqa: BLE001
            log.warning("LLM classification batch failed: %s", e)
