"""Map step: read each concall transcript once with the fast model.

Only forward-looking passages are sent (BM25 pre-filter, ~10 chunks out of ~60),
and every extracted quote is checked against the transcript text; anything the model
paraphrased or invented is dropped before it can reach the analysis.
"""
from __future__ import annotations

import logging
import re

from .. import llm
from ..sources import documents

log = logging.getLogger("finn.extract")

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                         "nov", "dec"], start=1)}


def call_quarter(label: str) -> str:
    """Screener concall date ('Aug 2025') -> quarter discussed ('Q1 FY26')."""
    m = re.match(r"([A-Za-z]{3})\w*\s+(\d{4})", label.strip())
    if not m:
        return label
    mon, yr = _MONTHS.get(m.group(1).lower(), 0), int(m.group(2))
    if mon in (4, 5, 6):
        q, fy = 4, yr
    elif mon in (7, 8, 9):
        q, fy = 1, yr + 1
    elif mon in (10, 11, 12):
        q, fy = 2, yr + 1
    else:
        q, fy = 3, yr
    return f"Q{q} FY{str(fy)[-2:]}"


GUIDANCE_QUERY = ("guidance expect outlook target growth margin ebitda capex next year going forward fy "
                  "double digit percent revenue volume pipeline order book sustain improve aspire")

SYSTEM = """You read an Indian listed company's earnings-call transcript excerpt.
Extract ONLY what management (not analysts) explicitly said. Never use outside knowledge.
Return JSON:
{"guidance":[{"metric":"revenue_growth|margin|capex|volume|order_book|debt|other","statement":"<plain English, <=20 words>",
  "low":<number or null>,"high":<number or null>,"unit":"%|cr|x|other","period":"<e.g. FY27, H2 FY26, next 2-3 years>",
  "quote":"<verbatim sentence from the text, <=40 words>"}],
 "claims":[{"topic":"demand|margins|competition|pricing|costs|execution|capital_allocation|other",
  "claim":"<what management asserted about the business, <=20 words>","quote":"<verbatim, <=40 words>"}],
 "pushback":[{"topic":"","question":"<what an analyst pressed on, <=20 words>","quote":"<verbatim, <=40 words>"}],
 "tone":"<one of: confident, cautious, defensive, mixed>"}
Only include guidance that is a forward-looking commitment or expectation with a direction or number.
At most 6 guidance, 5 claims, 3 pushback items. Quotes must be copied exactly."""


def extract_transcript(url: str, source_id: str, quarter: str) -> dict:
    pages = documents.fetch_pages(url)
    ch = documents.chunks(pages, source_id)
    if not ch:
        return {"source": source_id, "quarter": quarter, "guidance": [], "claims": [], "pushback": [], "pages": 0}
    top = documents.BM25(ch).search(GUIDANCE_QUERY, k=10)
    top.sort(key=lambda c: (c["page"]))
    excerpt = "\n\n".join(f"[p{c['page']}] {c['text']}" for c in top)
    out = llm.complete("fast", SYSTEM, f"Quarter: {quarter}\n\n{excerpt}", max_tokens=1800)
    kept, dropped = {}, 0
    for kind in ("guidance", "claims", "pushback"):
        kept[kind] = []
        for i, it in enumerate(out.get(kind, []) or []):
            page = documents.quote_in(it.get("quote", ""), pages)
            if page is None:
                dropped += 1
                continue
            it.update(page=page, src=source_id, quarter=quarter,
                      id=f"{source_id}-{kind[0].upper()}{i + 1}")
            kept[kind].append(it)
    log.info("%s %s: kept %d guidance, %d claims; dropped %d unverifiable quotes",
             source_id, quarter, len(kept["guidance"]), len(kept["claims"]), dropped)
    return {"source": source_id, "quarter": quarter, "tone": out.get("tone"), "pages": len(pages),
            "dropped_quotes": dropped, **kept}
