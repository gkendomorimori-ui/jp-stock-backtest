from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.strategies.high_price_breakout import HighPriceBreakout
from src.universe import UniverseRules, data_condition, eligibility, liquidity, security_eligible
from src.utils.config_loader import load_yaml
from tests.synthetic import flat, make_md, weekdays

N = 30
DAYS = weekdays(date(2026, 1, 5), N)
IDX = pd.DatetimeIndex(pd.to_datetime(DAYS))
RULES = UniverseRules(
    market_codes=frozenset({"0111", "0112", "0113"}),
    product_categories=frozenset({"011"}),
    common_stock_code_suffix="0",
    min_avg_turnover=100_000_000,
    window_days=20,
)
PARAMS = load_yaml("strategies/high_price_breakout.yaml")["parameters"]


def test_rules_from_repository_config() -> None:
    rules = UniverseRules.from_config(load_yaml("config/universe.yaml"))
    assert rules.market_codes == {"0111", "0112", "0113", "0101", "0102", "0104", "0106", "0107"}
    assert rules.product_categories == {"011"}
    assert rules.common_stock_code_suffix == "0"
    assert rules.min_avg_turnover == 100_000_000
    assert rules.window_days == 20


# ------------------------------------------------------------------ data condition


def test_needs_twenty_full_prior_days() -> None:
    md = make_md(DAYS, {"10010": flat(N, 1000)})
    ok = data_condition(md, 20)["10010"]
    assert not ok.iloc[19]  # only 19 prior days
    assert ok.iloc[20]


def test_gap_in_window_is_not_backfilled() -> None:
    s = flat(N, 1000)
    for k in ("open", "high", "low", "close", "volume"):
        s[k][5] = None  # no trade on day 5
    md = make_md(DAYS, {"10010": s})
    ok = data_condition(md, 20)["10010"]
    # day 5 is inside T-20..T-1 for T = 6 .. 25 -> ineligible; T = 26 is the first clean one
    assert not ok.iloc[6:26].any()
    assert ok.iloc[26]


def test_missing_bar_row_counts_as_invalid_day() -> None:
    s = flat(N, 1000)
    s["listed"] = [i != 10 for i in range(N)]  # row absent on day 10
    md = make_md(DAYS, {"10010": s})
    assert not data_condition(md, 20)["10010"].iloc[11:31].any()


def test_signal_day_itself_must_be_valid() -> None:
    s = flat(N, 1000)
    s["close"][25] = None
    md = make_md(DAYS, {"10010": s})
    assert not data_condition(md, 20)["10010"].iloc[25]


# ------------------------------------------------------------------ liquidity / security


def test_liquidity_threshold_uses_prior_window_only() -> None:
    s = flat(N, 1000, volume=100_000)  # turnover exactly 1e8
    md = make_md(DAYS, {"10010": s})
    assert liquidity(md, RULES)["10010"].iloc[20]
    s2 = flat(N, 1000, volume=99_999)
    s2["volume"][20] = 10_000_000  # huge volume ON T must not help
    md2 = make_md(DAYS, {"10010": s2})
    assert not liquidity(md2, RULES)["10010"].iloc[20]


def test_security_rules() -> None:
    series = {
        "10010": flat(N, 1000),
        "25935": flat(N, 1000),  # preferred share (suffix 5)
        "13060": flat(N, 1000),  # ETF
        "20020": flat(N, 1000),  # TOKYO PRO MARKET
    }
    md = make_md(DAYS, series, prodcat={"13060": "014"}, market={"20020": "0105"})
    sec = security_eligible(md, RULES).iloc[0]
    assert sec.to_dict() == {"10010": True, "13060": False, "20020": False, "25935": False}


def test_not_listed_on_t_is_not_eligible() -> None:
    s = flat(N, 1000, volume=200_000)
    s["listed"] = [True] * 25 + [False] * 5
    md = make_md(DAYS, {"10010": s})
    assert not eligibility(md, RULES)["10010"].iloc[26:].any()


# ------------------------------------------------------------------ strategy


def _breakout_series() -> dict[str, list[float | None]]:
    s = flat(N, 1000)
    s["high"] = [1010.0] * N
    s["close"][22] = 1011.0  # > prior 20-day high 1010
    s["high"][22] = 1015.0
    s["volume"][22] = 2_000_000  # exactly 2x
    return s  # type: ignore[return-value]


def test_breakout_signal_and_score() -> None:
    md = make_md(DAYS, {"10010": _breakout_series()})
    score = HighPriceBreakout(PARAMS).generate_signals(md)["10010"]
    assert score.notna().sum() == 1
    assert score.iloc[22] == pytest.approx(2.0)


def test_close_equal_to_prior_high_is_not_a_breakout() -> None:
    s = _breakout_series()
    s["close"][22] = 1010.0
    md = make_md(DAYS, {"10010": s})
    assert HighPriceBreakout(PARAMS).generate_signals(md)["10010"].isna().all()


def test_volume_below_multiplier_is_not_a_signal() -> None:
    s = _breakout_series()
    s["volume"][22] = 1_999_999
    md = make_md(DAYS, {"10010": s})
    assert HighPriceBreakout(PARAMS).generate_signals(md)["10010"].isna().all()


def test_split_alone_never_creates_a_signal() -> None:
    # 1:2 split on day 22: actual volume doubles (2x) - would satisfy the volume rule on raw data
    s = flat(N, 1000)
    for k in ("open", "high", "low", "close"):
        s[k] = [1000.0] * 22 + [500.0] * 8
    s["volume"] = [1_000_000.0] * 22 + [2_000_000.0] * 8
    s["adj_factor"] = [1.0] * N
    s["adj_factor"][22] = 0.5
    md = make_md(DAYS, {"10010": s})
    assert HighPriceBreakout(PARAMS).generate_signals(md)["10010"].isna().all()
    assert np.allclose(md["adj_close"]["10010"], 500.0)


def test_reverse_split_alone_never_creates_a_signal() -> None:
    # 5:1 reverse split: actual price jumps 5x - would be a "breakout" on raw prices
    s = flat(N, 200)
    for k in ("open", "high", "low", "close"):
        s[k] = [200.0] * 22 + [1000.0] * 8
    s["volume"] = [5_000_000.0] * 22 + [1_000_000.0] * 8
    s["adj_factor"] = [1.0] * N
    s["adj_factor"][22] = 5.0
    md = make_md(DAYS, {"10010": s})
    assert HighPriceBreakout(PARAMS).generate_signals(md)["10010"].isna().all()
