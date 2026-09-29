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
    # the +100% / -50% moves are also in the API's adjusted series -> source data, INFO
    assert f["large_moves"].level == "INFO"
    assert "matches_api_in_universe" in f["large_moves"].message
    assert f["large_moves"].details["in_universe"][0]["symbol"] == "10010"
    # API AdjC = 1.0 vs recomputed 1000: not explainable by rounding
    assert f["adjustment_vs_api"].level == "WARN"
    assert "1 unexplained" in f["adjustment_vs_api"].message
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


def test_rounding_differences_are_explained() -> None:
    data = clean()
    bars = data.bars  # type: ignore[attr-defined]
    bars["api_adj_close"] = bars["api_adj_close"].astype(float)
    bars.loc[bars.index[:5], "api_adj_close"] = bars.loc[bars.index[:5], "adj_close"] + 0.04
    f = by_check(check_quality(data, RULES, KNOWN))  # type: ignore[arg-type]
    assert f["adjustment_vs_api"].level == "INFO"
    assert "5 are within 0.05 yen" in f["adjustment_vs_api"].message


def test_move_where_api_disagrees_is_a_warning() -> None:
    s = flat(N, 1000, 200_000)
    for k in ("open", "high", "low", "close"):
        s[k] = [1000.0] * 25 + [2500.0] * 5  # +150% jump on day 25 with no adj_factor
    data = make_data(DAYS, {"10010": s, "20020": flat(N, 500, 400_000)})
    b = data.bars
    b.loc[(b["symbol"] == "10010") & (b["date"] >= pd.Timestamp(DAYS[25])), "api_adj_close"] = (
        1000.0
    )
    f = by_check(check_quality(data, RULES, KNOWN))
    assert f["large_moves"].level == "WARN"
    assert "api_adjusted_disagrees" in f["large_moves"].message


# ------------------------------------------------------------------ execution model v2 inputs

from src.data.quality import execution_inputs_findings  # noqa: E402

REGIME_DAYS = weekdays(date(2023, 5, 22), 20)  # spans 2023-06-05


def v2_findings(series: dict[str, dict[str, object]]) -> dict[str, Finding]:
    data = make_data(REGIME_DAYS, series)  # type: ignore[arg-type]
    return by_check(execution_inputs_findings(data.bars, data.master, RULES))


def mid400(price_before: float, price_after: float) -> dict[str, object]:
    s = flat(20, price_before)
    k = REGIME_DAYS.index(date(2023, 6, 5))
    for col in ("open", "high", "low", "close"):
        s[col][k:] = [price_after] * (20 - k)
    s["scale"] = "TOPIX Mid400"
    return s  # type: ignore[return-value]


def test_tick_grid_follows_the_2023_06_05_regime() -> None:
    # integer prices before, 0.1-yen prices after: consistent with the regimes
    f = v2_findings({"10010": mid400(1000.0, 999.9)})
    assert f["tick_grid"].level == "INFO"
    assert "before / from 2023-06-05: 0 / " in f["tick_grid"].message
    assert f["limit_flags"].level == "INFO" and f["scale_category"].level == "INFO"


def test_fractional_price_before_2023_06_05_is_off_grid() -> None:
    f = v2_findings({"10010": mid400(999.9, 999.9)})
    assert f["tick_grid"].level == "WARN"
    assert f["tick_grid"].details["examples"][0]["symbol"] == "10010"


def test_missing_flags_and_unknown_category_are_reported() -> None:
    s = flat(20, 1000)
    s["ul"] = ["0"] * 19 + [None]
    s["scale"] = ["-"] * 19 + [None]
    f = v2_findings({"10010": s})
    assert f["limit_flags"].level == "ERROR"
    assert f["limit_flags"].details["upper_limit"]["missing_on_traded_rows"] == 1
    assert f["scale_category"].level == "WARN"


def test_old_processed_data_is_an_error() -> None:
    data = make_data(REGIME_DAYS, {"10010": flat(20, 1000)})
    bars = data.bars.drop(columns=["upper_limit", "lower_limit"])
    f = by_check(execution_inputs_findings(bars, data.master, RULES))
    assert f["execution_inputs"].level == "ERROR"
