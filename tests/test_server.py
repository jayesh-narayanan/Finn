"""What an evaluator might do to the hosted app, with the network mocked."""
import json
import threading
import time

import pytest

from finn import server
from finn.config import DATA, RESEARCH
from finn.push import digest
from finn.sources import screener


@pytest.fixture
def client(monkeypatch):
    server.JOBS.clear()
    server._ACTIVE.clear()
    server._hits.clear()
    return server.app.test_client()


def wait(client, jid, timeout=5):
    t = time.time()
    while time.time() - t < timeout:
        j = client.get(f"/api/jobs/{jid}").get_json()
        if j["status"] != "running":
            return j
        time.sleep(0.02)
    raise AssertionError("job never finished")


def fake_build(sym, progress=lambda s, p: None):
    progress("Reading", 50)
    RESEARCH.mkdir(parents=True, exist_ok=True)
    (RESEARCH / f"{sym}.json").write_text(json.dumps({"symbol": sym}))
    return {"symbol": sym}


def test_company_name_and_yahoo_ticker_resolve(client, monkeypatch):
    monkeypatch.setattr(server.dossier, "build", fake_build)
    monkeypatch.setattr(screener, "company", lambda s: (_ for _ in ()).throw(RuntimeError("404")) if s != "PIDILITIND" else {})
    monkeypatch.setattr(screener, "search", lambda q: [{"name": "Pidilite Industries Ltd", "symbol": "PIDILITIND"}])
    for q in ("Pidilite", "pidilitind.ns", "NSE:PIDILITIND"):
        r = client.post("/api/research", json={"query": q})
        assert r.status_code == 200, r.get_json()
        assert wait(client, r.get_json()["job"])["symbol"] == "PIDILITIND"


def test_unknown_company_gives_a_useful_message(client, monkeypatch):
    monkeypatch.setattr(screener, "company", lambda s: (_ for _ in ()).throw(RuntimeError("404")))
    monkeypatch.setattr(screener, "search", lambda q: [])
    j = wait(client, client.post("/api/research", json={"query": "zzqx"}).get_json()["job"])
    assert j["status"] == "error" and "No listed company matches" in j["error"]


def test_blank_and_junk_input_rejected(client):
    for body in ({}, {"query": " "}, {"query": "x"}, None):
        r = client.post("/api/research", json=body) if body is not None else client.post("/api/research", data="nope")
        assert r.status_code == 400


def test_same_company_twice_shares_one_job(client, monkeypatch):
    gate = threading.Event()

    def slow(sym, progress=lambda s, p: None):
        gate.wait(2)
        return fake_build(sym, progress)
    monkeypatch.setattr(server.dossier, "build", slow)
    monkeypatch.setattr(screener, "resolve", lambda q: "HAL")
    a = client.post("/api/research", json={"query": "HAL"}).get_json()
    b = client.post("/api/research", json={"query": "hal"}).get_json()
    assert a["job"] == b["job"] and b.get("joined")
    gate.set()
    assert wait(client, a["job"])["status"] == "done"


def test_hourly_limit_per_visitor(client, monkeypatch):
    monkeypatch.setattr(server.dossier, "build", fake_build)
    monkeypatch.setattr(screener, "resolve", lambda q: q.upper())
    monkeypatch.setitem(server.LIMITS, "research", 2)
    codes = [client.post("/api/research", json={"query": f"CO{i}"}).status_code for i in range(3)]
    assert codes == [200, 200, 429]
    other = client.post("/api/research", json={"query": "CO9"}, headers={"X-Forwarded-For": "10.0.0.9"})
    assert other.status_code == 200  # a different visitor isn't affected


def test_unknown_job_and_cache_folder_are_handled(client):
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/data/cache/finn_cache.sqlite").status_code == 404


def test_refresh_blocked_by_nse_keeps_the_good_brief(client, monkeypatch, tmp_path):
    good = {"mode": "live", "generated_at": "2026-11-09T17:00", "announcements": [{"id": "a"}], "prices": {"X": {}}}
    monkeypatch.setattr(digest, "DATA", tmp_path)
    (tmp_path / "feed.json").write_text(json.dumps(good))
    ser = {"d": ["d"] * 30, "c": [1.0] * 30, "v": [1] * 30, "src": "yfinance"}
    monkeypatch.setattr(digest.prices, "eod_many", lambda syms: ({s: dict(ser) for s in syms}, []))
    monkeypatch.setattr(digest.screener, "company", lambda s: {})
    monkeypatch.setattr(digest.nse, "announcements", lambda *a: (_ for _ in ()).throw(RuntimeError("HTTP 403")))
    monkeypatch.setattr(digest.time, "sleep", lambda s: None)
    j = wait(client, client.post("/api/refresh").get_json()["job"])
    assert j["status"] == "error" and "still shown" in j["error"]
    assert json.loads((tmp_path / "feed.json").read_text()) == good


def test_ask_rebuilds_its_index_for_a_report_built_elsewhere(monkeypatch):
    from finn import cache, llm
    from finn.pull import dossier
    d = {"symbol": "ASKCO", "name": "AskCo", "metrics": json.loads(json.dumps(_metrics())),
         "sources": [{"id": "T1", "type": "transcript", "label": "Q2 FY27 call", "url": "https://x/t.pdf"}]}
    RESEARCH.mkdir(parents=True, exist_ok=True)
    (RESEARCH / "ASKCO.json").write_text(json.dumps(d))
    with cache._conn() as c:
        c.execute("DELETE FROM kv WHERE ns='chunks' AND k='ASKCO'")
    monkeypatch.setattr(dossier.documents, "fetch_pages",
                        lambda url, max_pages=None: ["Management said capex will be 500 crore next year for the new plant. " * 3])
    seen = {}

    def fake(tier, system, user, **k):
        seen["user"] = user
        return {"answer": "Capex of ₹500 cr [T1 p1]", "cites": [{"src": "T1", "page": 1}]}
    monkeypatch.setattr(llm, "complete", fake)
    out = dossier.ask("ASKCO", "What capex did they guide?")
    assert "[T1 p1]" in seen["user"] and out["cites"][0]["label"] == "Q2 FY27 call"
    assert cache.get("chunks", "ASKCO")  # cached for the next question


def _metrics():
    from finn import sample
    from finn.pull import metrics
    return metrics.compute(sample._kestrel()[0])


def test_screener_query_cleaning():
    assert screener.clean_query(" reliance.NS ") == "reliance"
    assert screener.clean_query("BSE: 500325") == "500325"
    assert screener.looks_like_symbol("M&M") and not screener.looks_like_symbol("Asian Paints")
