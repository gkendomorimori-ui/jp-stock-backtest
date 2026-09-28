from datetime import date

import pandas as pd

from src.data.quality import Finding, check_quality
from src.universe import UniverseRules
from tests.synthetic import flat, make_data, weekdays

N = 30
DAYS = weekdays(date(2022, 3, 21), N)  # spans the 2022-04-04 market restructuring
RULES = UniverseRules(
    market_codes=frozenset({"0111", "0112", "0113", "0101", "0102", "0104", "0106", "0107"}),
    product_categories=frozenset({"011"}),
    common_stock_code_suffix="0",
    min_avg_turnover=1e8,
    window_days=20,
)
KNOWN = {"0101", "0102", "0104", "0105", "0106", "0107", "0109", "0111", "0112", "0113"}


def by_check(findings: list[Finding]) -> dict[str, Finding]:
    return {f.check: f for f in findings}


def clean() -> object:
    data = make_data(DAYS, {"10010": flat(N, 1000, 200_000), "20020": flat(N, 500, 400_000)})
    closes = [1000.0] * N
    data.topix = pd.DataFrame(
        {
            "date": pd.to_datetime(DAYS),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
        }
    )
    return data


def test_clean_data_has_no_errors_or_warnings() -> None:
    f = check_quality(clean(), RULES, KNOWN)  # type: ignore[arg-type]
    assert [x for x in f if x.level != "INFO"] == []
    names = {x.check for x in f}
    assert {"coverage", "bars_vs_master", "price_sanity", "adjustment_vs_api", "topix"} <= names
    snaps = [x.message for x in f if x.check == "market_snapshot"]
    assert any("before 2022-04-04" in m for m in snaps) and any(
        "from 2022-04-04" in m for m in snaps
    )


def test_problems_are_detected() -> None:
    s = flat(N, 1000, 200_000)
    s["close"][10] = s["high"][10] = 2000.0  # +100% move without a split
    s["close"][11] = s["high"][11] = s["low"][11] = s["open"][11] = 1000.0
    data = make_data(
        DAYS,
        {"10010": s, "25935": flat(N, 1000), "30030": flat(N, 1000)},
        market={"30030": "0199"},
    )
    data.bars.loc[data.bars.index[3], "api_adj_close"] = 1.0  # API adjusted value disagrees
    data.topix = None
    f = by_check(check_quality(data, RULES, KNOWN))
    assert f["large_moves"].level == "WARN"
    assert f["adjustment_vs_api"].level == "WARN"
    assert f["market_codes"].level == "WARN" and "0199" in f["market_codes"].message
    assert "1 symbols" in f["common_stock_rule"].message
    assert f["common_stock_rule"].details["unclassified"][0]["symbol"] == "25935"
    assert f["topix"].level == "WARN"


def test_missing_day_and_topix_mismatch_are_errors() -> None:
    data = clean()
    data.bars = data.bars[data.bars["date"] != pd.Timestamp(DAYS[5])]  # type: ignore[attr-defined]
    data.topix = data.topix.iloc[1:]  # type: ignore[attr-defined]
    f = by_check(check_quality(data, RULES, KNOWN))  # type: ignore[arg-type]
    assert f["coverage"].level == "ERROR"
    assert f["bars_vs_master"].level == "ERROR"
    assert f["topix"].level == "ERROR"
