"""Sample dataset for running the app with no network or API key.

All companies, people, numbers and quotes here are FICTIONAL, so no invented facts get
attached to real listed companies. Run `python -m finn refresh` for live data.
The research dossiers are produced by the real metrics code from synthetic statements.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from datetime import date, datetime, timedelta

from .config import DATA, RESEARCH
from .pull import metrics
from .push.score import score

HOLDINGS = [
    ("KESTRELIT", "Kestrel Infotech", "IT services", "large", 62000),
    ("SAFFRONFIN", "Saffron Finance", "NBFC", "large", 48000),
    ("ORBITEMS", "Orbit Electronics", "Electronics mfg", "mid", 7100),
    ("MERIDPHAR", "Meridian Pharma", "Pharma", "large", 38000),
    ("TIDEWATER", "Tidewater Ports", "Infrastructure", "large", 52000),
    ("HARBORBNK", "Harbor Bank", "Banks", "large", 140000),
    ("LOTUSRET", "Lotus Retail", "Retail", "large", 41000),
    ("GANGAPWR", "Ganga Power", "Power", "large", 33000),
    ("NILGIRIBEV", "Nilgiri Beverages", "Consumer", "mid", 9800),
    ("VAYUMOTOR", "Vayu Motors", "Autos", "large", 77000),
    ("QUILLCHEM", "Quill Chemicals", "Chemicals", "mid", 11200),
    ("PEAKCEM", "Peak Cement", "Cement", "mid", 8600),
    ("INDIGOTEL", "Indigo Telecom", "Telecom", "large", 91000),
    ("CEDARHOME", "Cedar Homes", "Real estate", "small", 2300),
    ("SUTRATEX", "Sutra Textiles", "Textiles", "small", 1600),
    ("ALPINEINS", "Alpine Insurance", "Insurance", "large", 45000),
]

# Last-session shocks that tell the demo's story: (return %, volume multiple)
SHOCKS = {"KESTRELIT": (-5.8, 3.1), "ORBITEMS": (7.2, 4.2), "CEDARHOME": (-3.1, 2.4), "PEAKCEM": (-2.2, 1.6)}


def _sessions(n: int) -> list[str]:
    d, out = date.today(), []
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.isoformat())
    return out[::-1]


def _prices(sym: str, days: list[str]) -> dict:
    rnd = random.Random(sym)
    px = rnd.uniform(300, 4000)
    vol0 = rnd.uniform(2e5, 4e6)
    c, v = [], []
    for i in range(len(days)):
        r = rnd.gauss(0.0005, 0.013)
        if rnd.random() < 0.03:
            r += rnd.choice([-1, 1]) * rnd.uniform(0.03, 0.06)
        vm = math.exp(rnd.gauss(0, 0.28)) * (1 + 14 * abs(r))
        if i == len(days) - 1 and sym in SHOCKS:
            r, vm = SHOCKS[sym][0] / 100, SHOCKS[sym][1]
        px *= 1 + r
        c.append(round(px, 2))
        v.append(int(vol0 * vm))
    return {"d": days, "c": c, "v": v}


def _ann(h_ago: float, sym, subject, text, cat=None, **kw):
    ts = (datetime.now() - timedelta(hours=h_ago)).replace(microsecond=0)
    uid = hashlib.sha1(f"{sym}{subject}{h_ago}".encode()).hexdigest()[:12]
    return {"id": uid, "symbol": sym, "ts": ts.isoformat(), "subject": subject, "text": text,
            "url": None, "source": "SAMPLE", **({"category": cat} if cat else {}), **kw}


def _results_card(periods, rev, ebitda, pbt, pat):
    def chg(a, b):
        return round((a - b) / abs(b) * 100, 1)
    rows = [{"metric": m, "cur": v[0], "prev": v[1], "year_ago": v[2], "qoq": chg(v[0], v[1]), "yoy": chg(v[0], v[2])}
            for m, v in (("Revenue", rev), ("EBITDA", ebitda), ("PBT", pbt), ("PAT", pat))]
    mg = [e / r * 100 for e, r in zip(ebitda, rev)]
    return {"type": "results", "periods": periods, "unit": "₹ cr", "rows": rows,
            "ebitda_margin": {"cur": round(mg[0], 1), "qoq_bps": round((mg[0] - mg[1]) * 100),
                              "yoy_bps": round((mg[0] - mg[2]) * 100)},
            "note": "EBITDA = PBT + finance costs + depreciation − other income (not reported directly)",
            "source": "filing", "method": "parsed", "basis": "consolidated"}


def announcements() -> list[dict]:
    P = ["Q2 FY27", "Q1 FY27", "Q2 FY26"]
    A = [
        _ann(15, "KESTRELIT", "Financial Result Updates",
             "Kestrel Infotech Limited has informed the Exchange regarding the Unaudited Financial Results for the "
             "quarter ended September 30, 2026.", "results", subtype="results",
             card=_results_card(P, [2862, 2803, 2640], [598, 611, 581], [552, 571, 540], [408, 421, 402])),
        _ann(14.5, "KESTRELIT", "Analysts/Institutional Investor Meet/Con. Call Updates",
             "Kestrel Infotech Limited has informed the Exchange about the earnings call scheduled on November 10, 2026.",
             "earnings_call", subtype="call"),
        _ann(9, "SAFFRONFIN", "Change in Directors/ Key Managerial Personnel/ Auditor/ Compliance Officer/ Share Transfer Agent",
             "Resignation of Mr. Vikram Sethi as Chief Financial Officer and appointment of Ms. Anita Rao as Chief "
             "Financial Officer with effect from December 1, 2026.", "management_change", subtype="mgmt",
             card={"type": "mgmt", "bio_source": "filing", "changes": [
                 {"person": "Vikram Sethi", "role": "Chief Financial Officer", "change": "resigned",
                  "effective": "30 Nov 2026", "reason": "To pursue opportunities outside the company", "bio": ""},
                 {"person": "Anita Rao", "role": "Chief Financial Officer", "change": "appointed", "effective": "1 Dec 2026",
                  "bio": "Chartered accountant with 19 years in lending finance. Joins from Northstar Housing Finance, "
                         "where she was Treasurer and led its first overseas bond issue; earlier 11 years at Coral Bank's "
                         "retail-credit finance team.",
                  "previous": ["Northstar Housing Finance — Treasurer", "Coral Bank — Head, retail credit finance"]}]}),
        _ann(6, "ORBITEMS", "Acquisition",
             "Orbit Electronics Limited has informed the Exchange about signing of definitive agreements to acquire a "
             "76% stake in Prism Components Private Limited for a consideration of Rs. 420 crore.", "M&A", subtype="deal",
             summary="Buying 76% of Prism Components for ₹420 cr cash; adds PCB assembly for EV chargers. Closing expected in Q4 FY27."),
        _ann(20, "CEDARHOME", "Disclosure of reasons for encumbrance",
             "Promoter group has created a pledge on 12.4% of total shares of the company in favour of a lender.",
             "pledging", subtype="pledge"),
        _ann(22, "MERIDPHAR", "Updates",
             "Meridian Pharma Limited has informed the Exchange that the USFDA has issued a warning letter to its "
             "Unit-3 oral solids facility following the inspection conducted in July 2026.", "litigation", subtype="legal",
             summary="Warning letter to the Unit-3 oral solids plant, which the company says is ~9% of US sales; existing supplies continue."),
        _ann(30, "PEAKCEM", "Credit Rating",
             "Crest Ratings has downgraded the long-term rating of the company's bank facilities to A- with negative "
             "outlook from A, citing elevated debt after the Rajasthan line expansion.", "credit_rating", subtype="rating"),
        _ann(18, "TIDEWATER", "Bagging/Receiving of orders/contracts",
             "Receipt of letter of award worth Rs. 1,150 crore for development of a container berth on a DBFOT basis.",
             "press_release", subtype="order"),
        _ann(19, "GANGAPWR", "Dividend", "Board declared interim dividend of Rs. 2.50 per share; record date November 20, 2026.",
             "dividend", subtype="dividend"),
        _ann(21, "QUILLCHEM", "Updates",
             "Board approved capex of Rs. 600 crore for a new specialty-amines plant at Dahej, to be commissioned by Q2 FY29.",
             "capex", subtype="capex"),
        _ann(23, "VAYUMOTOR", "Press Release", "Vayu Motors Limited has informed the Exchange regarding a press release.",
             "press_release", subtype="pr",
             summary="October wholesales 1.42 lakh units, up 6% YoY; exports up 21%, EVs at 7% of mix."),
        _ann(40, "HARBORBNK", "Financial Result Updates",
             "Harbor Bank Limited has informed the Exchange regarding the financial results for the quarter ended September 30, 2026.",
             "results", subtype="results",
             card=_results_card(P, [21840, 21302, 19655], [9120, 9010, 8450], [6610, 6702, 6050], [4980, 5050, 4540])),
        _ann(44, "INDIGOTEL", "Analysts/Institutional Investor Meet/Con. Call Updates",
             "Transcript of the earnings conference call held on November 4, 2026.", "earnings_call", subtype="transcript"),
        _ann(52, "SUTRATEX", "Insider Trading - SEBI (PIT) Regulations",
             "Disclosure by promoter under Regulation 7(2): sale of 1,20,000 shares.", "insider_trading", subtype="pit"),
        _ann(70, "NILGIRIBEV", "Financial Result Updates",
             "Nilgiri Beverages Limited has informed the Exchange regarding the financial results for the quarter ended September 30, 2026.",
             "results", subtype="results",
             card=_results_card(P, [1186, 1310, 1012], [215, 251, 168], [176, 214, 131], [131, 160, 97])),
        _ann(96, "LOTUSRET", "Press Release", "Opened 14 new stores in October, taking the count to 612 across 190 cities.",
             "press_release", subtype="pr"),
        _ann(120, "ALPINEINS", "Change in Directors/ Key Managerial Personnel/ Auditor/ Compliance Officer/ Share Transfer Agent",
             "Appointment of Mr. Rohan Mehta as an Independent Director for a term of five years.", "management_change",
             subtype="mgmt"),
        _ann(150, "TIDEWATER", "Financial Result Updates", "Financial results for the quarter ended September 30, 2026.",
             "results", subtype="results",
             card=_results_card(P, [7420, 7135, 6380], [4310, 4170, 3640], [2650, 2580, 2210], [2105, 2050, 1760])),
        _ann(200, "ORBITEMS", "Board Meeting Intimation", "Board meeting to be held on November 7, 2026 to consider financial results.",
             "results", subtype="intimation"),
        _ann(260, "SAFFRONFIN", "Credit Rating", "Crest Ratings has reaffirmed AAA/Stable on the company's NCD programme.",
             "credit_rating", subtype="rating"),
        _ann(320, "PEAKCEM", "Allotment of Securities", "Allotment of non-convertible debentures aggregating Rs. 250 crore.",
             "fund_raise", subtype="raise"),
    ]
    routine = [
        ("Trading Window", "Closure of trading window from October 1, 2026.", "unclassified"),
        ("Copy of Newspaper Publication", "Newspaper publication of notice to shareholders.", "unclassified"),
        ("Certificate under SEBI (Depositories and Participants) Regulations, 2018", "Certificate under Regulation 74(5).", "unclassified"),
        ("Allotment of ESOP / ESPS", "Allotment of 12,450 equity shares under ESOP 2021.", "unclassified"),
        ("Analysts/Institutional Investor Meet/Con. Call Updates", "Schedule of meeting with institutional investors.", "investor_meetings"),
        ("Loss of Share Certificates", "Intimation of loss of share certificates.", "unclassified"),
        ("Copy of Newspaper Publication", "Newspaper publication of financial results.", "results"),
        ("Shareholders meeting", "Scrutinizer's report on e-voting.", "agm_egm"),
    ]
    rnd = random.Random(7)
    syms = [h[0] for h in HOLDINGS]
    for i in range(46):
        subj, text, cat = rnd.choice(routine)
        sub = "ad" if cat == "results" else "routine" if cat == "unclassified" else "meet"
        A.append(_ann(rnd.uniform(1, 30 * 24), rnd.choice(syms), subj, text, cat, subtype=sub))
    return A


# ------------------------------------------------------------------ dossiers -------
def _scr(name, about, years, sales, opm, pat, qp, qsales, qopm, qpat, eq, res, debt, cfo, cfi, ccc, debtor, top):
    def T(periods, rows):
        return {"periods": periods, "rows": rows}
    op = [round(s * m / 100) for s, m in zip(sales, opm)]
    qop = [round(s * m / 100) for s, m in zip(qsales, qopm)]
    pbt = [round(p / 0.75) for p in pat]
    qpbt = [round(p / 0.75) for p in qpat]
    return {
        "name": name, "about": about, "basis": "consolidated", "url": None, "top": top,
        "pl": T(years + ["TTM"], {"Sales": sales + [None], "Operating Profit": op + [None], "OPM %": opm + [None],
                                  "Profit before tax": pbt + [None], "Net Profit": pat + [None]}),
        "quarters": T(qp, {"Sales": qsales, "Operating Profit": qop, "OPM %": qopm, "Profit before tax": qpbt,
                           "Net Profit": qpat}),
        "bs": T(years, {"Equity Capital": eq, "Reserves": res, "Borrowings": debt}),
        "cf": T(years, {"Cash from Operating Activity": cfo, "Cash from Investing Activity": cfi}),
        "ratios": T(years, {"Cash Conversion Cycle": ccc, "Debtor Days": debtor}),
        "docs": {"annual_reports": [], "concalls": []},
    }


YEARS = [f"Mar {y}" for y in range(2019, 2027)]
QP = ["Sep 2023", "Dec 2023", "Mar 2024", "Jun 2024", "Sep 2024", "Dec 2024", "Mar 2025", "Jun 2025",
      "Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026", "Sep 2026"]


def _kestrel():
    scr = _scr(
        "Kestrel Infotech", "IT services for banks, insurers and retailers.", YEARS,
        [4210, 4780, 5320, 6410, 7550, 8460, 9740, 10870], [24.1, 24.6, 25.0, 23.8, 23.2, 22.9, 22.6, 22.1],
        [705, 812, 905, 1060, 1210, 1342, 1470, 1610],
        QP, [2105, 2162, 2240, 2320, 2410, 2470, 2540, 2612, 2640, 2770, 2845, 2803, 2862],
        [22.8, 23.0, 22.7, 22.4, 22.6, 22.9, 22.2, 22.0, 22.0, 22.1, 21.9, 21.8, 20.9],
        [330, 344, 352, 360, 372, 380, 392, 398, 402, 409, 412, 421, 408],
        [100] * 8, [2900, 3420, 4010, 4720, 5480, 6230, 7110, 8020], [0, 0, 0, 40, 60, 55, 30, 20],
        [690, 760, 860, 990, 1120, 1240, 1330, 1460], [-260, -310, -280, -420, -480, -520, -610, -690],
        [62, 64, 63, 66, 68, 71, 74, 79], [64, 66, 65, 68, 70, 73, 76, 81],
        {"Market Cap": 62000, "Stock P/E": 37.4, "ROCE": 26.8, "ROE": 20.1})
    T = lambda i, q: {"id": f"T{i}", "type": "transcript", "label": f"{q} earnings call", "url": None}
    sources = [{"id": "SCR-PL", "type": "statements", "label": "Profit & loss, annual", "url": None},
               {"id": "SCR-Q", "type": "statements", "label": "Quarterly results", "url": None},
               {"id": "SCR-BS", "type": "statements", "label": "Balance sheet", "url": None},
               {"id": "SCR-CF", "type": "statements", "label": "Cash flow statement", "url": None},
               {"id": "SCR-RATIOS", "type": "statements", "label": "Efficiency ratios", "url": None},
               {"id": "SCR-TOP", "type": "statements", "label": "Key ratios", "url": None},
               T(1, "Q2 FY27"), T(2, "Q1 FY27"), T(3, "Q4 FY26"), T(4, "Q3 FY26"),
               {"id": "A1", "type": "annual_report", "label": "Annual report 2026", "url": None}]
    g = [
        {"id": "T3-G1", "src": "T3", "quarter": "Q4 FY26", "metric": "revenue_growth", "low": 10, "high": None, "unit": "%",
         "period": "FY27", "page": 4, "statement": "Double-digit constant-currency revenue growth in FY27",
         "quote": "we are confident of delivering double-digit growth in FY27 given the deal wins we have already booked"},
        {"id": "T3-G2", "src": "T3", "quarter": "Q4 FY26", "metric": "margin", "low": 21, "high": 23, "unit": "%",
         "period": "FY27", "page": 6, "statement": "EBITDA margin held in a 21–23% band",
         "quote": "we will hold our operating margin in the 21 to 23 percent band through the year"},
        {"id": "T4-G1", "src": "T4", "quarter": "Q3 FY26", "metric": "capex", "low": 400, "high": None, "unit": "cr",
         "period": "FY27", "page": 9, "statement": "₹400 cr capex on the Pune delivery campus",
         "quote": "the Pune campus will need about 400 crores of capex largely in FY27"},
        {"id": "T2-G1", "src": "T2", "quarter": "Q1 FY27", "metric": "other", "low": None, "high": None, "unit": "other",
         "period": "H2 FY27", "page": 3, "statement": "Deal ramp-ups to lift growth in the second half",
         "quote": "the ramp-up of the two large BFSI deals should show up in revenue from the second half"},
        {"id": "T1-G1", "src": "T1", "quarter": "Q2 FY27", "metric": "revenue_growth", "low": 8, "high": 9, "unit": "%",
         "period": "FY27", "page": 2, "statement": "FY27 growth now seen at high single digits",
         "quote": "we now see the year closer to high single digit growth as two clients deferred programmes"},
    ]
    claims = [
        {"id": "T2-C1", "src": "T2", "quarter": "Q1 FY27", "topic": "demand", "page": 2,
         "claim": "Deal pipeline at an all-time high", "quote": "our pipeline is at an all time high and win rates are improving"},
        {"id": "T1-C1", "src": "T1", "quarter": "Q2 FY27", "topic": "margins", "page": 5,
         "claim": "Margin dip is one-off from wage hikes", "quote": "the margin dip this quarter is one off and reflects the wage cycle"},
    ]
    push = [{"id": "T1-P1", "src": "T1", "quarter": "Q2 FY27", "topic": "receivables", "page": 11,
             "question": "Why have receivable days risen for three straight years?",
             "quote": "can you explain why receivable days have gone up again this quarter"}]
    analysis = {
        "one_line": "Guided double digits, is tracking 8–9% and has quietly lowered the bar; margin promise slipped below its own band in Q2.",
        "scorecard": [
            {"promise": "Double-digit revenue growth in FY27", "made_in": "Q4 FY26", "period": "FY27",
             "delivered": "7.3% YoY in Q1 and 8.4% in Q2. On the Q2 call the outlook was cut to high single digits.",
             "verdict": "walked_back", "evidence": ["T3-G1", "T1-G1", "F:Q7", "F:Q6"]},
            {"promise": "EBITDA margin within 21–23%", "made_in": "Q4 FY26", "period": "FY27",
             "delivered": "21.8% in Q1, then 20.9% in Q2: below the band's floor.",
             "verdict": "missed", "evidence": ["T3-G2", "F:Q6", "F:Q7"]},
            {"promise": "Large-deal ramp-up lifts H2 revenue", "made_in": "Q1 FY27", "period": "H2 FY27",
             "delivered": "H2 not reported yet; Q2 growth slowed to 2.1% QoQ.", "verdict": "too_early",
             "evidence": ["T2-G1", "F:Q7"]},
            {"promise": "₹400 cr Pune campus capex", "made_in": "Q3 FY26", "period": "FY27",
             "delivered": "Investing outflow of ₹690 cr in FY26 suggests spend began early; FY27 not reported.",
             "verdict": "on_track", "evidence": ["T4-G1", "F:FY26"]},
        ],
        "narrative_gaps": [
            {"says": "Pipeline at an all-time high (Q1 FY27)",
             "numbers_show": "Revenue growth slowed from ~12% YoY to ~8% in the two quarters after the claim.",
             "verdict": "diverges", "evidence": ["T2-C1", "F:Q7", "M3"]},
            {"says": "Q2 margin dip is one-off",
             "numbers_show": "Margin has slipped every year since FY21 (25.0% → 22.1%); Q2 extends a trend rather than breaking one.",
             "verdict": "diverges", "evidence": ["T1-C1", "M2"]},
            {"says": "Capital-light growth",
             "numbers_show": "Operating cash flow covered ~91% of profit over 5 years.",
             "verdict": "agrees", "evidence": ["M6"]},
        ],
        "bull": [
            {"text": "Still compounding: revenue up ~13% a year over FY23–26 with ROCE near 27%.", "evidence": ["M1", "F:FY26"]},
            {"text": "Profits convert to cash (~91% over 5 years), so the dividend capacity is real.", "evidence": ["M6"]},
            {"text": "If the two large BFSI deals ramp as described, H2 can recover growth.", "evidence": ["T2-G1"]},
        ],
        "bear": [
            {"text": "Guidance already cut once this year; the H2 recovery is the second promise on the same deals.",
             "evidence": ["T1-G1", "T2-G1"]},
            {"text": "Margins below the stated band with a wage cycle still to absorb.", "evidence": ["T3-G2", "F:Q7"]},
            {"text": "Cash cycle up 11 days in three years as clients pay slower, which analysts flagged on the call.",
             "evidence": ["M5", "T1-P1"]},
        ],
        "dropped_ungrounded": 1,
    }
    snapshot = {"what_it_does": "Kestrel builds and runs software for banks, insurers and retailers, mostly under "
                                "multi-year managed-services contracts. About two-thirds of revenue comes from North America.",
                "segments": [{"name": "Banking & financial services", "share_pct": 46, "note": "Core banking, payments", "page": 38},
                             {"name": "Insurance", "share_pct": 22, "note": "Policy admin platforms", "page": 38},
                             {"name": "Retail & CPG", "share_pct": 19, "note": "Commerce, supply chain", "page": 39},
                             {"name": "Others", "share_pct": 13, "note": "Healthcare, travel", "page": 39}],
                "geography": "North America 64%, Europe 24%, India and rest 12%", "customers": "Top 10 clients ≈ 41% of revenue",
                "source": "A1"}
    return scr, sources, snapshot, g, claims, push, analysis


def _nilgiri():
    scr = _scr(
        "Nilgiri Beverages", "Packaged teas, ready-to-drink beverages and mineral water.", YEARS,
        [1820, 1950, 2010, 2390, 2860, 3420, 4060, 4790], [12.0, 12.6, 11.4, 12.1, 13.8, 15.2, 16.4, 17.6],
        [118, 131, 120, 152, 205, 268, 340, 418],
        QP, [760, 690, 812, 1001, 908, 830, 975, 1176, 1012, 1020, 1185, 1310, 1186],
        [14.6, 13.8, 15.2, 16.4, 15.9, 15.0, 16.8, 18.3, 16.6, 16.9, 17.4, 19.2, 18.1],
        [50, 41, 58, 88, 70, 58, 75, 104, 97, 92, 119, 160, 131],
        [24] * 8, [610, 700, 790, 920, 1100, 1340, 1650, 2030], [220, 240, 310, 260, 210, 240, 380, 520],
        [96, 120, 70, 118, 140, 150, 168, 190], [-120, -95, -110, -180, -260, -330, -540, -610],
        [38, 40, 47, 44, 49, 55, 63, 72], [21, 22, 25, 24, 27, 30, 36, 42],
        {"Market Cap": 9800, "Stock P/E": 23.4, "ROCE": 22.5, "ROE": 19.6})
    sources = [{"id": s, "type": "statements", "label": l, "url": None} for s, l in (
        ("SCR-PL", "Profit & loss, annual"), ("SCR-Q", "Quarterly results"), ("SCR-BS", "Balance sheet"),
        ("SCR-CF", "Cash flow statement"), ("SCR-RATIOS", "Efficiency ratios"), ("SCR-TOP", "Key ratios"))] + \
        [{"id": f"T{i}", "type": "transcript", "label": f"{q} earnings call", "url": None}
         for i, q in enumerate(["Q2 FY27", "Q1 FY27", "Q4 FY26", "Q3 FY26"], start=1)] + \
        [{"id": "A1", "type": "annual_report", "label": "Annual report 2026", "url": None}]
    g = [
        {"id": "T4-G1", "src": "T4", "quarter": "Q3 FY26", "metric": "volume", "low": 15, "high": None, "unit": "%",
         "period": "FY26–FY27", "page": 3, "statement": "Mid-teens volume growth for the next two years",
         "quote": "we expect mid teens volume growth for the next two years as the south expansion matures"},
        {"id": "T3-G1", "src": "T3", "quarter": "Q4 FY26", "metric": "margin", "low": 18, "high": None, "unit": "%",
         "period": "FY27", "page": 5, "statement": "EBITDA margin crossing 18% in FY27",
         "quote": "we should cross eighteen percent EBITDA margin in FY27 on mix and lower packaging costs"},
        {"id": "T2-G1", "src": "T2", "quarter": "Q1 FY27", "metric": "debt", "low": None, "high": None, "unit": "other",
         "period": "FY27", "page": 8, "statement": "Bring debt down from operating cash in FY27",
         "quote": "we will use operating cash to bring the term debt down meaningfully this year"},
    ]
    claims = [{"id": "T1-C1", "src": "T1", "quarter": "Q2 FY27", "topic": "costs", "page": 4,
               "claim": "Working capital build is seasonal stocking", "quote": "the inventory build is seasonal stocking ahead of winter"}]
    push = [{"id": "T1-P1", "src": "T1", "quarter": "Q2 FY27", "topic": "working capital", "page": 12,
             "question": "Distributor credit days are rising; is growth being bought?",
             "quote": "are you extending credit to distributors to push volumes"}]
    analysis = {
        "one_line": "Delivers on growth and margins; the cash story is weaker than the P&L, and the debt promise is slipping.",
        "scorecard": [
            {"promise": "Mid-teens volume growth", "made_in": "Q3 FY26", "period": "FY26–FY27",
             "delivered": "Revenue up 18% in FY26 and 11–23% YoY over the last four quarters (volumes not reported separately).", "verdict": "delivered",
             "evidence": ["T4-G1", "F:FY26", "F:Q7", "F:Q6"]},
            {"promise": "EBITDA margin above 18% in FY27", "made_in": "Q4 FY26", "period": "FY27",
             "delivered": "19.2% in Q1 and 18.1% in Q2.", "verdict": "on_track", "evidence": ["T3-G1", "F:Q6", "F:Q7"]},
            {"promise": "Pay down term debt from operating cash", "made_in": "Q1 FY27", "period": "FY27",
             "delivered": "Borrowings rose to ₹520 cr in FY26 (0.25× equity) with free cash flow negative; no reduction visible yet.",
             "verdict": "behind", "evidence": ["T2-G1", "M4", "M7"]},
        ],
        "narrative_gaps": [
            {"says": "Inventory build is seasonal", "numbers_show": "Cash conversion cycle up 23 days in three years — not a one-season effect.",
             "verdict": "diverges", "evidence": ["T1-C1", "M5"]},
            {"says": "Margin expansion from mix", "numbers_show": "Margin up 3.8 pts in three years alongside ~19% revenue CAGR.",
             "verdict": "agrees", "evidence": ["M2", "M1"]},
        ],
        "bull": [{"text": "Rare combination: ~19% revenue CAGR, 3.8 pts of margin expansion, profit compounding ~27%.", "evidence": ["M1", "M2"]},
                 {"text": "Margin guidance already met two quarters running.", "evidence": ["T3-G1", "F:Q7"]},
                 {"text": "ROCE above 22% at 23× earnings.", "evidence": ["F:FY26"]}],
        "bear": [{"text": "Profit is not turning into cash: operating cash flow only 55% of PAT over 5 years.", "evidence": ["M6"]},
                 {"text": "Debt reduction promise behind while working capital stretches.", "evidence": ["T2-G1", "M4", "M5"]},
                 {"text": "Analysts suspect channel-stuffing via distributor credit.", "evidence": ["T1-P1"]}],
        "dropped_ungrounded": 0,
    }
    snapshot = {"what_it_does": "Nilgiri sells packaged tea, ready-to-drink beverages and bottled water, mainly in South "
                                "and West India, through 2,100 distributors.",
                "segments": [{"name": "Packaged tea", "share_pct": 58, "note": "Premium blends growing fastest", "page": 22},
                             {"name": "Ready-to-drink", "share_pct": 27, "note": "Iced tea, lemonade", "page": 22},
                             {"name": "Water", "share_pct": 15, "note": "Low margin, volume driver", "page": 23}],
                "geography": "South India 61%, West 24%, rest 15%", "customers": "General trade 64%, modern trade 21%, e-com 15%",
                "source": "A1"}
    return scr, sources, snapshot, g, claims, push, analysis


def _dossier(sym, builder):
    scr, sources, snapshot, g, claims, push, analysis = builder()
    m = metrics.compute(scr)
    return {"symbol": sym, "name": scr["name"], "basis": "consolidated", "mode": "sample",
            "generated_at": datetime.now().isoformat(timespec="seconds"), "sources": sources,
            "snapshot": snapshot, "metrics": m, "guidance": g, "claims": claims, "pushback": push,
            "extracts": [], "analysis": analysis, "llm": True,
            "cost": {"calls": 6, "cached": 0, "tokens_in": 41200, "tokens_out": 5300, "usd": 0.0118,
                     "by_tier": {"fast": 5, "smart": 1}}}


def write():
    days = _sessions(120)
    series = {h[0]: _prices(h[0], days) for h in HOLDINGS}
    mcap = {h[0]: h[4] for h in HOLDINGS}
    names = {h[0]: h[1] for h in HOLDINGS}
    items = announcements()
    for it in items:
        it["name"] = names[it["symbol"]]
        score(it, series[it["symbol"]], mcap[it["symbol"]])
    for it in items:  # exchange text is informative in the sample: use it as the one-liner
        if not it.get("summary") and not it.get("card") and it["score"] >= 30:
            it["summary"] = it["text"].split(" has informed the Exchange regarding ")[-1].split(" has informed the Exchange about ")[-1]
    items.sort(key=lambda x: x["ts"], reverse=True)
    holdings = []
    for sym, name, sector, size, mc in HOLDINGS:
        p = series[sym]
        c, v = p["c"], p["v"]
        holdings.append({"symbol": sym, "name": name, "sector": sector, "size": size, "market_cap_cr": mc,
                         "close": c[-1], "chg_pct": round((c[-1] / c[-2] - 1) * 100, 2),
                         "chg_1m": round((c[-1] / c[-22] - 1) * 100, 2),
                         "vol_x": round(v[-1] / (sum(v[-21:-1]) / 20), 2), "asof": days[-1], "spark": c[-30:]})
    feed = {"mode": "sample", "generated_at": datetime.now().isoformat(timespec="seconds"), "window_days": 30,
            "holdings": holdings, "announcements": items, "prices": series,
            "default_rules": {"volume_multiple": 2.0, "volume_lookback": 20, "price_move_pct": 4.0,
                              "alert_categories": ["results", "M&A", "management_change", "litigation", "fund_raise",
                                                   "credit_rating", "pledging"],
                              "muted_categories": ["investor_meetings", "unclassified"]},
            "cost": {"calls": 9, "cached": 3, "tokens_in": 38100, "tokens_out": 2900, "usd": 0.0049,
                     "by_tier": {"fast": 9}}}
    (DATA / "feed.json").write_text(json.dumps(feed, indent=1))
    idx = []
    for sym, b in (("KESTRELIT", _kestrel), ("NILGIRIBEV", _nilgiri)):
        d = _dossier(sym, b)
        (RESEARCH / f"{sym}.json").write_text(json.dumps(d, indent=1))
        sc = d["analysis"]["scorecard"]
        idx.append({"symbol": sym, "name": d["name"], "generated_at": d["generated_at"], "mode": "sample",
                    "kept": sum(1 for s in sc if s["verdict"] in ("delivered", "on_track")), "promises": len(sc)})
    (RESEARCH / "index.json").write_text(json.dumps(idx, indent=1))
    print(f"Sample data written: {len(items)} filings, {len(holdings)} holdings, {len(idx)} dossiers")
