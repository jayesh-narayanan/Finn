from finn.push import classify, enrich
from finn.push.score import score, tier


def cls(subject, text=""):
    return classify.classify_rules(subject, text)[0]


def test_classifier_on_common_nse_subjects():
    assert cls("Trading Window") == "unclassified"
    assert cls("Credit Rating", "ICRA has reaffirmed") == "credit_rating"
    assert cls("Acquisition", "acquire 51% stake") == "M&A"
    assert cls("Allotment of ESOP / ESPS") == "unclassified"
    assert cls("Outcome of Board Meeting", "XYZ Limited has informed the Exchange regarding the Financial Results for the quarter ended 30-Sep-2026") == "results"
    assert cls("Change in Directors/ Key Managerial Personnel/ Auditor/ Compliance Officer/ Share Transfer Agent",
               "Resignation of Mr. A as Chief Financial Officer") == "management_change"
    assert cls("Analysts/Institutional Investor Meet/Con. Call Updates", "Transcript of the earnings call") == "earnings_call"
    assert cls("Disclosure of reasons for encumbrance", "promoter pledge created") == "pledging"


def test_results_intimation_is_not_results():
    cat, sub, _ = classify.classify_rules("Board Meeting Intimation", "Board meeting to be held on 7 Nov to consider financial results")
    assert classify.refine_results(cat, sub, "Board Meeting Intimation", "to be held to consider financial results") == "intimation"


def test_cfo_change_ranks_above_independent_director():
    cfo = score({"category": "management_change", "subtype": "mgmt", "subject": "Change in Directors/ KMP/ Compliance Officer",
                 "text": "Resignation of Mr. A as Chief Financial Officer of the company effective today", "ts": "2026-11-06"})
    ind = score({"category": "management_change", "subtype": "mgmt", "subject": "Change in Directors",
                 "text": "Appointment of Ms. B as an Independent Director for five years", "ts": "2026-11-06"})
    assert tier(cfo["score"]) == "act" and cfo["score"] > ind["score"] + 25
    assert all("secretarial" not in w for w in cfo["why"])


def test_rating_downgrade_vs_reaffirm():
    down = score({"category": "credit_rating", "subtype": "rating", "subject": "Credit Rating", "text": "downgraded to A- with negative outlook", "ts": ""})
    re = score({"category": "credit_rating", "subtype": "rating", "subject": "Credit Rating", "text": "rating reaffirmed at AA", "ts": ""})
    assert tier(down["score"]) == "act" and tier(re["score"]) == "routine"


def test_deal_size_relative_to_market_cap():
    big = score({"category": "M&A", "subtype": "deal", "subject": "Acquisition", "text": "consideration of Rs. 900 crore", "ts": ""}, None, 10000)
    assert any("market cap" in w for w in big["why"])


RESULTS_PAGE = """STATEMENT OF CONSOLIDATED UNAUDITED FINANCIAL RESULTS FOR THE QUARTER ENDED 30.09.2026
(Rs. in crore)
Quarter ended 30.09.2026 30.06.2026 30.09.2025 Half year
1 Revenue from operations 2,862.40 2,803.10 2,640.00 5,665.50
2 Other income 41.00 38.00 35.50 79.00
4 Finance costs 12.00 11.50 10.00 23.50
5 Depreciation and amortisation expense 75.00 74.00 70.00 149.00
7 Profit before tax (5-6) 552.00 571.00 540.00 1,123.00
9 Net Profit for the period 408.00 421.00 402.00 829.00
"""


def test_parse_results_page_and_ebitda_derivation():
    parsed = enrich.parse_results_page(RESULTS_PAGE)
    assert parsed and parsed["periods"][:3] == ["Q2 FY27", "Q1 FY27", "Q2 FY26"]
    card = enrich._card_from(parsed["values"], parsed["periods"], "filing", "parsed", "consolidated")
    rows = {r["metric"]: r for r in card["rows"]}
    assert rows["Revenue"]["cur"] == 2862.4
    assert rows["EBITDA"]["cur"] == round(552 + 12 + 75 - 41, 1)
    assert rows["PAT"]["yoy"] == round((408 - 402) / 402 * 100, 1)
    assert card["note"].startswith("EBITDA = PBT")


def test_lakh_units_are_converted_to_crore():
    page = RESULTS_PAGE.replace("(Rs. in crore)", "(Rs. in lakhs)")
    parsed = enrich.parse_results_page(page)
    assert abs(parsed["values"]["revenue"][0] - 28.624) < 1e-6


def test_exchange_boilerplate_is_stripped():
    t = "Orbit Electronics Limited has informed the Exchange about signing of an agreement to acquire 76%"
    assert enrich.clean_text(t).startswith("Signing of an agreement")
    assert enrich.is_generic("XYZ Limited has informed the Exchange regarding Press Release")
