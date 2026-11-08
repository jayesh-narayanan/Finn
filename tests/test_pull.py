"""End-to-end dossier build with the network and model mocked: proves the grounding contract
(unverifiable quotes and uncited claims are dropped) and that the output has the shape the app reads."""
import json

from finn import llm, sample
from finn.pull import dossier, extract, reconcile
from finn.sources import documents

TRANSCRIPT = [
    "Moderator: welcome to the Q4 FY26 call.",
    "CEO: we are confident of delivering double-digit growth in FY27 given the deal wins we have already booked. "
    "We will hold our operating margin in the 21 to 23 percent band through the year.",
]


def test_call_quarter_mapping():
    assert extract.call_quarter("Aug 2025") == "Q1 FY26"
    assert extract.call_quarter("Nov 2026") == "Q2 FY27"
    assert extract.call_quarter("Feb 2026") == "Q3 FY26"
    assert extract.call_quarter("May 2026") == "Q4 FY26"


def test_quote_verification():
    assert documents.quote_in("we will hold our operating margin in the 21 to 23 percent band", TRANSCRIPT) == 2
    assert documents.quote_in("we expect 30 percent growth next year from new markets", TRANSCRIPT) is None


def test_bm25_ranks_relevant_chunk_first():
    pages = ["The weather was fine and the board met to approve routine items for the year ahead. " * 2,
             "Revenue guidance for FY27 is 15 percent growth, with margins steady at current levels. " * 2] * 3
    ch = documents.chunks(pages, "T1", size=400, overlap=0)
    assert "guidance" in documents.BM25(ch).search("revenue growth guidance", k=1)[0]["text"]


def test_reconcile_drops_uncited_items():
    out = reconcile.validate({
        "scorecard": [{"promise": "x", "evidence": ["T1-G1", "F:Q7"]}, {"promise": "made up", "evidence": ["T9-G9"]}],
        "bull": [{"text": "ok", "evidence": ["M1"]}, {"text": "no cite", "evidence": []}],
        "bear": [], "narrative_gaps": [], "one_line": "v"}, {"T1-G1", "F:Q7", "M1"})
    assert len(out["scorecard"]) == 1 and len(out["bull"]) == 1 and out["dropped_ungrounded"] == 2


def test_dossier_build_end_to_end(monkeypatch, tmp_path):
    scr = sample._kestrel()[0]
    scr.update(symbol="TESTCO", url="https://example.invalid/TESTCO",
               docs={"annual_reports": [], "concalls": [{"date": "May 2026", "transcript": "https://example.invalid/t.pdf"}]})
    monkeypatch.setattr(dossier.screener, "company", lambda s: scr)
    monkeypatch.setattr(documents, "fetch_pages", lambda url, max_pages=None: TRANSCRIPT)
    monkeypatch.setattr(dossier, "RESEARCH", tmp_path)
    monkeypatch.setattr(llm, "available", lambda: True)

    def fake_complete(tier, system, user, **kw):
        if "earnings-call transcript" in system:
            return {"guidance": [
                {"metric": "revenue_growth", "statement": "Double-digit growth FY27", "period": "FY27",
                 "quote": "we are confident of delivering double-digit growth in FY27"},
                {"metric": "margin", "statement": "Invented", "period": "FY27", "quote": "margins will reach thirty percent next year easily"}],
                "claims": [], "pushback": [], "tone": "confident"}
        return {"scorecard": [{"promise": "Double digits", "made_in": "Q4 FY26", "period": "FY27", "delivered": "8%",
                               "verdict": "behind", "evidence": ["T1-G1", "F:Q7"]}],
                "narrative_gaps": [], "bull": [{"text": "x", "evidence": ["M1"]}], "bear": [{"text": "y", "evidence": ["nope"]}],
                "one_line": "Behind on growth."}
    monkeypatch.setattr(llm, "complete", fake_complete)

    d = dossier.build("TESTCO")
    assert [g["statement"] for g in d["guidance"]] == ["Double-digit growth FY27"]  # invented quote dropped
    assert d["analysis"]["scorecard"][0]["verdict"] == "behind"
    assert d["analysis"]["bear"] == [] and d["analysis"]["dropped_ungrounded"] == 1
    saved = json.loads((tmp_path / "TESTCO.json").read_text())
    assert saved["sources"][-1]["type"] == "transcript" and saved["metrics"]["headline"]
