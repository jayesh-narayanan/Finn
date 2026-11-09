import io
import json
import zipfile
from datetime import date

import pandas as pd

from finn.sources import prices
from finn.sources.http import RateLimited

UDIFF_CSV = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,"
    "FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,"
    "TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n"
    "2026-11-06,2026-11-06,CM,NSE,STK,2885,INE002A01018,RELIANCE,EQ,,,,,RELIANCE INDUSTRIES LTD,1480.1,1495,1471.2,1489.6,1490,"
    "1478.35,,1489.6,,,8123456,12085432100.5,201234,F1,1,,,,,\n"
    "2026-11-06,2026-11-06,CM,NSE,STK,2885,INE002A01018,RELIANCE,BL,,,,,RELIANCE INDUSTRIES LTD,1480,1480,1480,1480,1480,"
    "1478.35,,1480,,,500,740000,1,F1,1,,,,,\n"
    "2026-11-06,2026-11-06,CM,NSE,STK,11536,INE467B01029,TCS,EQ,,,,,TATA CONSULTANCY SERV LT,3050,3088,3041,3072.45,3070,"
    "3049.9,,3072.45,,,2034567,6245678900,98765,F1,1,,,,,\n"
)
LEGACY_CSV = (
    "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, "
    "TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
    "RELIANCE, EQ, 06-Nov-2026, 1478.35, 1480.10, 1495.00, 1471.20, 1490.00, 1489.60, 1487.71, 8123456, 120854.32, 201234, 4012345, 49.39\n"
    "KAYNES, SM, 06-Nov-2026, 5000, 5010, 5100, 4990, 5080, 5075.5, 5050, 120000, 6060, 5000, 60000, 50\n"
    "SOMEBOND, N1, 06-Nov-2026, 100, 100, 100, 100, 100, 100, 100, 10, 0.01, 1, 10, 100\n"
)


def _zip(text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("BhavCopy_NSE_CM_0_0_0_20261106_F_0000.csv", text)
    return b.getvalue()


def test_parse_udiff_prefers_eq_series():
    rows = prices.parse_udiff(_zip(UDIFF_CSV))
    assert rows["RELIANCE"] == [1489.6, 8123456]  # EQ line, not the block-deal line
    assert rows["TCS"] == [3072.45, 2034567]


def test_parse_legacy_strips_padded_headers_and_skips_non_equity():
    rows = prices.parse_legacy(LEGACY_CSV.encode())
    assert rows["RELIANCE"] == [1489.6, 8123456]
    assert rows["KAYNES"][0] == 5075.5  # SME series kept
    assert "SOMEBOND" not in rows


def _frame(n=40, start=100.0):
    idx = pd.date_range("2026-06-01", periods=n, freq="B")
    return pd.DataFrame({"Open": start, "High": start, "Low": start,
                         "Close": [start + i for i in range(n)], "Volume": [1000 + i for i in range(n)]}, index=idx)


def test_split_download_handles_both_column_layouts():
    a, b = _frame(), _frame(start=200)
    ticker_first = pd.concat({"RELIANCE.NS": a, "TCS.NS": b}, axis=1)
    field_first = ticker_first.swaplevel(0, 1, axis=1)
    for df in (ticker_first, field_first):
        out = prices.split_download(df, ["RELIANCE.NS", "TCS.NS"])
        assert out["TCS.NS"]["c"][0] == 200 and len(out["RELIANCE.NS"]["d"]) == 40
    single = prices.split_download(a, ["RELIANCE.NS"])
    assert single["RELIANCE.NS"]["v"][-1] == 1039


def test_falls_through_to_next_source_and_reports_why(monkeypatch):
    good = {"d": [f"2026-{m:02d}-01" for m in range(1, 13)] * 3, "c": list(range(36)), "v": [1] * 36}

    def yf(symbols):
        raise RuntimeError("yfinance returned no data (Yahoo is likely rate limiting this network)")

    def chart(symbols):
        raise RateLimited("Yahoo chart returned 429 (rate limited)")

    def bhav(symbols):
        return {s: good for s in symbols if s != "MISSING"}

    monkeypatch.setattr(prices, "SOURCES", [("yfinance", yf), ("yahoo", chart), ("nse-bhavcopy", bhav)])
    out, notes = prices.eod_many(["RELIANCE", "TCS", "MISSING"])
    assert set(out) == {"RELIANCE", "TCS"} and out["TCS"]["src"] == "nse-bhavcopy"
    assert any("429" in n for n in notes) and any("MISSING" in n for n in notes)


def test_partial_source_only_fills_gaps(monkeypatch):
    ser = {"d": ["d"] * 30, "c": [1.0] * 30, "v": [1] * 30}
    calls = []

    def first(symbols):
        calls.append(("first", list(symbols)))
        return {"A": ser}

    def second(symbols):
        calls.append(("second", list(symbols)))
        return {"B": ser}

    monkeypatch.setattr(prices, "SOURCES", [("first", first), ("second", second)])
    out, notes = prices.eod_many(["A", "B"])
    assert calls == [("first", ["A", "B"]), ("second", ["B"])] and notes == []
    assert out["A"]["src"] == "first" and out["B"]["src"] == "second"


def test_no_alarm_when_a_fallback_covers_everything(monkeypatch):
    ser = {"d": ["d"] * 30, "c": [1.0] * 30, "v": [1] * 30}

    def blocked(symbols):
        raise RateLimited("429")
    monkeypatch.setattr(prices, "SOURCES", [("yahoo", blocked), ("nse-bhavcopy", lambda syms: {s: ser for s in syms})])
    out, notes = prices.eod_many(["A", "B"])
    assert len(out) == 2 and notes == []


class FakeResp:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content


class FakeSess:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        for frag, resp in self.routes.items():
            if frag in url:
                return resp
        return FakeResp(404)


def test_bhav_day_falls_back_to_legacy_file_and_caches(monkeypatch):
    from finn import cache
    monkeypatch.setattr(cache, "DISABLED", False)
    d = date(2026, 11, 6)
    sess = FakeSess({"sec_bhavdata_full_06112026": FakeResp(200, LEGACY_CSV.encode())})
    k = d.isoformat()
    with cache._conn() as c:
        c.execute("DELETE FROM kv WHERE ns='bhav' AND k=?", (k,))
    rows = prices.bhav_day(d, sess)
    assert rows["RELIANCE"][0] == 1489.6 and len(sess.calls) == 2
    assert prices.bhav_day(d, FakeSess({}))["RELIANCE"][0] == 1489.6  # served from cache


def test_bhav_day_reports_a_block(monkeypatch):
    from finn import cache
    d = date(2026, 11, 5)
    with cache._conn() as c:
        c.execute("DELETE FROM kv WHERE ns='bhav' AND k=?", (d.isoformat(),))
    try:
        prices.bhav_day(d, FakeSess({"nsearchives": FakeResp(403)}))
        raise AssertionError("expected RateLimited")
    except RateLimited:
        pass


def test_refresh_survives_blocked_nse_and_says_so(monkeypatch, tmp_path):
    from finn.push import digest
    ser = {"d": [f"2026-08-{i:02d}" for i in range(1, 31)], "c": [100 + i for i in range(30)], "v": [1000] * 30, "src": "nse-bhavcopy"}
    monkeypatch.setattr(digest.prices, "eod_many", lambda syms: ({s: dict(ser) for s in syms}, []))
    monkeypatch.setattr(digest.screener, "company", lambda s: (_ for _ in ()).throw(RuntimeError("HTTP 429")))
    calls = []

    def blocked(sym, a, b):
        calls.append(sym)
        raise RuntimeError("NSE announcements unavailable: HTTP 401")
    monkeypatch.setattr(digest.nse, "announcements", blocked)
    monkeypatch.setattr(digest.time, "sleep", lambda s: None)
    monkeypatch.setattr(digest, "DATA", tmp_path)
    feed = digest.build(days=7, enrich_items=False)
    assert len(calls) == 3  # gave up after three, didn't spend minutes on all 18
    assert any("NSE filings: blocked" in n for n in feed["notes"])
    assert any("screener.in" in n for n in feed["notes"])
    assert feed["price_sources"] == ["nse-bhavcopy"] and all(h.get("close") for h in feed["holdings"])
    assert json.loads((tmp_path / "feed.json").read_text())["notes"] == feed["notes"]
