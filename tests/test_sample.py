import json

from finn import sample
from finn.config import DATA, RESEARCH


def test_sample_data_is_consistent():
    sample.write()
    feed = json.loads((DATA / "feed.json").read_text())
    assert feed["mode"] == "sample" and len(feed["holdings"]) >= 15
    syms = {h["symbol"] for h in feed["holdings"]}
    assert all(a["symbol"] in syms and "score" in a for a in feed["announcements"])
    for sym in ("KESTRELIT", "NILGIRIBEV"):
        d = json.loads((RESEARCH / f"{sym}.json").read_text())
        ids = {g["id"] for g in d["guidance"] + d["claims"] + d["pushback"]}
        m = d["metrics"]
        ids |= {f"M{i}" for i in range(1, 1 + len(m["trajectory"]["notes"] + m["balance_sheet"]["notes"] + m["cash"]["notes"]))}
        ids |= {f"F:Q{i}" for i in range(len(m["trajectory"]["quarterly"]["periods"]))} | {f"F:{y}" for y in m["trajectory"]["years"]}
        for k in ("scorecard", "narrative_gaps", "bull", "bear"):
            for it in d["analysis"][k]:
                assert set(it["evidence"]) <= ids, (sym, k, it["evidence"])
