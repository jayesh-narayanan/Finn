"""Reduce step: the one call that needs real reasoning.

The smart model sees only (a) management's verified quotes, tagged with ids, and
(b) the delivered numbers, tagged with ids. It must cite ids for every line; lines
citing nothing that exists are removed. That is the grounding contract.
"""
from __future__ import annotations

import logging

from .. import llm
from . import metrics as M

log = logging.getLogger("finn.reconcile")

SYSTEM = """You are a sceptical buy-side analyst. You get (1) what management said on recent earnings calls,
each with an id like T2-G1, and (2) the company's reported numbers, each line with an id like F:Q5 or F:FY25,
and (3) computed observations with ids like M1.
Use ONLY this evidence. Do not use anything you know about the company from elsewhere.
Tasks:
A. "scorecard": for each material piece of guidance (merge repeats across quarters), say what was promised,
   when, and what the numbers since show. verdict ∈ delivered | on_track | behind | missed | walked_back | too_early.
   'walked_back' = management itself lowered/dropped it on a later call. 'too_early' = the period has not been reported.
B. "narrative_gaps": up to 4 places where management's story and the numbers agree or diverge. Prefer divergences.
C. "bull" and "bear": 3 tight points each, each grounded in evidence ids. No generic statements.
D. "one_line": one sentence verdict on management credibility, <=25 words.
Every item MUST include "evidence": [ids]. Be concrete: name numbers.
JSON shape:
{"scorecard":[{"promise":"","made_in":"Q? FY??","period":"","delivered":"","verdict":"","evidence":[]}],
 "narrative_gaps":[{"says":"","numbers_show":"","verdict":"agrees|diverges","evidence":[]}],
 "bull":[{"text":"","evidence":[]}],"bear":[{"text":"","evidence":[]}],"one_line":""}"""


def run(extracts: list[dict], m: dict) -> dict:
    ev_ids: set[str] = set()
    said = []
    for ex in extracts:
        for kind in ("guidance", "claims", "pushback"):
            for it in ex.get(kind, []):
                ev_ids.add(it["id"])
                text = it.get("statement") or it.get("claim") or it.get("question")
                said.append(f"{it['id']} [{ex['quarter']}, {kind}] {text} | \"{it['quote']}\"")
    actuals = M.actuals_table(m)
    q = m["trajectory"].get("quarterly") or {}
    ev_ids |= {f"F:Q{i}" for i in range(len(q.get("periods", [])))}
    ev_ids |= {f"F:{y}" for y in m["trajectory"]["years"]}
    obs = []
    for i, n in enumerate(m["trajectory"]["notes"] + m["balance_sheet"]["notes"] + m["cash"]["notes"], start=1):
        obs.append(f"M{i}: {n['text']}")
        ev_ids.add(f"M{i}")
    user = ("MANAGEMENT SAID:\n" + "\n".join(said) + "\n\nREPORTED NUMBERS:\n" + actuals +
            "\n\nOBSERVATIONS:\n" + "\n".join(obs))
    out = llm.obj(llm.complete("smart", SYSTEM, user, max_tokens=3000, temperature=0.2))
    return validate(out, ev_ids)


def validate(out: dict, ev_ids: set[str]) -> dict:
    dropped = 0
    clean = {}
    for k in ("scorecard", "narrative_gaps", "bull", "bear"):
        keep = []
        for it in (x for x in (out.get(k) or []) if isinstance(x, dict)):
            ev = [e for e in (it.get("evidence") or []) if e in ev_ids]
            if not ev or (k == "scorecard" and not any("-G" in e or "-C" in e for e in ev)):
                dropped += 1
                continue
            it["evidence"] = ev
            keep.append(it)
        clean[k] = keep
    clean["one_line"] = out.get("one_line", "")
    clean["dropped_ungrounded"] = dropped
    if dropped:
        log.info("dropped %d ungrounded items", dropped)
    return clean
