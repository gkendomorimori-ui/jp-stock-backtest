"""Execution model v2 (docs/EXECUTION_MODEL.md): tick rounding, limit locks, sell carry.

Unless noted: signal on day 0, bought on day 1 at open 1,000 -> entry 1,001 (0.1% slippage),
take-profit level 1,101.1, stop-loss level 950.95, ScaleCat "-" (standard table: 1-yen tick
below 3,000 yen). Expected numbers are computed by hand.
"""

from dataclasses import replace
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine, DataError, EngineResult, ExecutionParams
from src.data.market_data import MarketData
from src.strategies.high_price_breakout import HighPriceBreakout
from tests.synthetic import flat, make_data, weekdays

N = 30
DAYS = weekdays(date(2026, 1, 5), N)
S, C = 0.001, 0.0005
P2 = ExecutionParams(
    initial_capital=1_000_000,
    commission_rate=C,
    slippage_rate=S,
    lot_size=100,
    max_positions=5,
    max_position_pct=0.20,
    take_profit_pct=0.10,
    stop_loss_pct=0.05,
    max_holding_days=20,
    model_version="v2",
)
P1 = replace(P2, model_version="v1")


def stock(price: float = 1000, scale: Any = "-") -> dict[str, Any]:
    s: dict[str, Any] = flat(N, price)
    s["ul"] = ["0"] * N
    s["ll"] = ["0"] * N
    s["scale"] = scale
    return s


def bar(s: dict[str, Any], i: int, o: Any, h: Any, lo: Any, c: Any) -> None:
    s["open"][i], s["high"][i], s["low"][i], s["close"][i] = o, h, lo, c


def run(
    series: dict[str, dict[str, Any]],
    signals: dict[tuple[int, str], float],
    params: ExecutionParams = P2,
    days: list[date] = DAYS,
    start: int = 0,
    end: int | None = None,
) -> EngineResult:
    md = MarketData.from_processed(make_data(days, series))
    return run_md(md, signals, params, start, len(days) - 1 if end is None else end)


def run_md(
    md: MarketData,
    signals: dict[tuple[int, str], float],
    params: ExecutionParams,
    start: int,
    end: int,
) -> EngineResult:
    scores = pd.DataFrame(np.nan, index=md.dates, columns=md["close"].columns)
    for (i, sym), v in signals.items():
        scores.iloc[i, scores.columns.get_loc(sym)] = v
    eligible = pd.DataFrame(True, index=md.dates, columns=md["close"].columns)
    return BacktestEngine(params).run(md, scores, eligible, md.dates[start], md.dates[end])


def only_trade(r: EngineResult) -> pd.Series:
    assert len(r.trades) == 1, r.trades
    return r.trades.iloc[0]


SIG = {(0, "10010"): 2.0}


# ------------------------------------------------------------------ tick rounding


def test_intraday_take_profit_rounded_up_to_tick() -> None:
    s = stock()
    bar(s, 2, 1000, 1110, 1000, 1000)
    r = run({"10010": s}, SIG)
    t = only_trade(r)
    assert t["exit_reason"] == "take_profit" and t["exit_phase"] == "intraday"
    assert t["trigger_level"] == pytest.approx(1101.1)
    assert t["order_price"] == 1102 and t["base_price"] == 1102
    assert t["exit_price"] == pytest.approx(1102 * (1 - S))  # model effective price
    assert (t["tick_class"], t["tick_size"], bool(t["tick_rounded"])) == ("standard", 1, True)
    assert r.stats["tick_rounded"] == {"take_profit": 1, "stop_loss": 0}
    assert r.status == "complete"


def test_intraday_stop_rounded_down_to_tick() -> None:
    s = stock()
    bar(s, 2, 1000, 1000, 940, 990)
    t = only_trade(run({"10010": s}, SIG))
    assert t["exit_reason"] == "stop_loss"
    assert t["trigger_level"] == pytest.approx(950.95)
    assert t["base_price"] == 950 and pd.isna(t["order_price"])  # stop = market order
    assert t["exit_price"] == pytest.approx(950 * (1 - S))


def test_v1_keeps_the_level_itself() -> None:
    s = stock()
    bar(s, 2, 1000, 1000, 940, 990)
    t = only_trade(run({"10010": s}, SIG, params=P1))
    assert t["base_price"] == pytest.approx(950.95)
    assert t["exit_price"] == pytest.approx(950.95 * (1 - S))


def test_both_touched_stop_first_and_rounded_down() -> None:
    s = stock()
    bar(s, 2, 1000, 1110, 940, 1000)
    t = only_trade(run({"10010": s}, SIG))
    assert t["exit_reason"] == "stop_loss" and t["base_price"] == 950
    assert bool(t["intraday_both_touched"]) and bool(t["stop_priority_applied"])


@pytest.mark.parametrize(
    ("price", "lo", "hi", "reason", "base"),
    [
        # entry 6.006: TP 6.6066 -> 7 yen; SL 5.7057 -> 5 yen (1-yen tick is ~15% here)
        (6, 6, 7, "take_profit", 7.0),
        (6, 5, 6, "stop_loss", 5.0),
    ],
)
def test_low_priced_stock(price: float, lo: float, hi: float, reason: str, base: float) -> None:
    s = stock(price)
    bar(s, 2, price, hi, lo, price)
    t = only_trade(run({"10010": s}, SIG))
    assert t["exit_reason"] == reason and t["base_price"] == base


def weekdays_around_regime_change() -> tuple[list[date], int, int]:
    days = weekdays(date(2023, 5, 8), 30)
    return days, days.index(date(2023, 6, 2)), days.index(date(2023, 6, 5))


@pytest.mark.parametrize(
    ("scale", "day_key", "base", "cls"),
    [
        ("TOPIX Mid400", "before", 950.0, "standard"),  # TOPIX100 only before 2023-06-05
        ("TOPIX Mid400", "after", 950.9, "fine"),  # TOPIX500 from 2023-06-05
        ("TOPIX Core30", "before", 950.9, "fine"),
        ("TOPIX Small 1", "after", 950.0, "standard"),
    ],
)
def test_tick_regime_change_2023_06_05(scale: str, day_key: str, base: float, cls: str) -> None:
    days, before, after = weekdays_around_regime_change()
    k = before if day_key == "before" else after
    s = stock(scale=scale)
    bar(s, k, 1000, 1000, 940, 990)
    t = only_trade(run({"10010": s}, SIG, days=days))
    assert pd.Timestamp(t["exit_date"]) == pd.Timestamp(days[k])
    assert t["base_price"] == pytest.approx(base) and t["tick_class"] == cls


def test_scale_category_is_point_in_time() -> None:
    """The category OF THE EXIT DAY decides (joined TOPIX Mid400 on the exit day)."""
    days, before, after = weekdays_around_regime_change()
    s = stock(scale=["TOPIX Small 1"] * 5 + ["TOPIX Core30"] * 25)
    bar(s, 5, 1000, 1000, 940, 990)
    t = only_trade(run({"10010": s}, SIG, days=days))
    assert t["tick_class"] == "fine" and t["base_price"] == pytest.approx(950.9)


def test_unknown_scale_category_stops_the_run() -> None:
    """Not guessed and not executed without a tick: the run stops as needing review."""
    s = stock(scale=[None] * N)
    bar(s, 2, 1000, 1110, 1000, 1000)
    r = run({"10010": s}, SIG)
    assert r.trades.empty  # the take profit on day 2 was NOT executed
    assert r.status == "needs_review"
    assert r.halted is not None and r.halted["date"] == pd.Timestamp(DAYS[2])
    assert list(r.unresolved_events["event"]) == ["tick_class_unknown"]
    assert r.stats["tick_class_unknown"] == 1 and "halted" in r.stats
    # partial results up to the previous close are kept
    assert r.equity_curve["date"].iloc[-1] == pd.Timestamp(DAYS[1])
    op = r.open_positions.iloc[0]
    assert (op["symbol"], op["quantity"]) == ("10010", 100)
    assert op["valuation_price"] == 1000 and op["valuation_price_date"] == pd.Timestamp(DAYS[1])
    assert op["unrealized_pnl"] == pytest.approx(100 * 1000 - op["entry_cost"])


# ------------------------------------------------------------------ gaps at the open


def test_gap_below_stop_uses_open_not_rounded_stop() -> None:
    s = stock()
    bar(s, 2, 940, 1000, 930, 990)
    t = only_trade(run({"10010": s}, SIG))
    assert t["exit_reason"] == "stop_loss_open" and t["base_price"] == 940
    assert t["trigger_level"] == pytest.approx(950.95)


def test_gap_above_take_profit_fills_at_open() -> None:
    s = stock()
    bar(s, 2, 1110, 1120, 1100, 1105)
    t = only_trade(run({"10010": s}, SIG))
    assert t["exit_reason"] == "take_profit_open" and t["base_price"] == 1110
    assert t["order_price"] == 1102  # the limit order price that the open exceeded


# ------------------------------------------------------------------ limit up / down


def test_buy_not_filled_when_opening_at_limit_up() -> None:
    s = stock()
    s["ul"][1] = "1"  # open == high == 1000 on day 1
    r = run({"10010": s}, SIG)
    assert r.orders.iloc[0]["status"] == "cancelled_limit_up"
    assert r.trades.empty and r.stats["buy_unfilled_limit_up"] == 1
    assert len(r.orders) == 1  # not carried to the next day
    # v1 ignores the flag
    assert run({"10010": s}, SIG, params=P1).orders.iloc[0]["status"] == "filled"


def test_buy_filled_when_limit_up_only_touched_later() -> None:
    s = stock()
    s["ul"][1] = "1"
    bar(s, 1, 1000, 1100, 990, 1050)  # opened below the limit, touched it later
    r = run({"10010": s}, SIG)
    assert r.orders.iloc[0]["status"] == "filled"


def locked_stop_at_open(s: dict[str, Any], i: int = 2) -> None:
    """Opens at the lower limit (open == low, LL=1) below the stop level."""
    s["ll"][i] = "1"
    bar(s, i, 900, 950, 900, 920)


def test_limit_down_open_carries_sell_request_to_next_open() -> None:
    s = stock()
    locked_stop_at_open(s)
    bar(s, 3, 1000, 1000, 1000, 1000)  # recovered above the stop: the request still executes
    r = run({"10010": s}, SIG)
    t = only_trade(r)
    assert t["exit_reason"] == "stop_loss_open"
    assert pd.Timestamp(t["trigger_date"]) == pd.Timestamp(DAYS[2])
    assert pd.Timestamp(t["exit_date"]) == pd.Timestamp(DAYS[3])
    assert (t["exit_phase"], t["carried_days"], t["base_price"]) == ("open", 1, 1000)
    assert t["last_unfilled_cause"] == "limit_down_open"
    # not sold intraday on day 2 even though the high (950) was above the lower limit
    eq = r.equity_curve.set_index("date")
    assert eq.loc[pd.Timestamp(DAYS[2]), "positions"] == 1
    assert eq.loc[pd.Timestamp(DAYS[2]), "position_value"] == pytest.approx(100 * 920)
    assert list(r.sell_unfilled["cause"]) == ["limit_down_open"]
    assert r.stats["sell_unfilled"] == {"limit_down_open": 1}
    assert r.stats["sells_executed_after_carry"] == 1


def test_sell_request_persists_over_several_locked_days() -> None:
    s = stock()
    locked_stop_at_open(s, 2)
    locked_stop_at_open(s, 3)
    set_no_trade = [4]
    for i in set_no_trade:
        bar(s, i, None, None, None, None)
        s["volume"][i] = None
    bar(s, 5, 990, 1000, 980, 1000)
    t = only_trade(run({"10010": s}, SIG))
    assert pd.Timestamp(t["exit_date"]) == pd.Timestamp(DAYS[5])
    assert t["carried_days"] == 3 and t["last_unfilled_cause"] == "no_trade"
    assert t["base_price"] == 990


def test_slot_and_cash_not_released_while_request_is_unfilled() -> None:
    a, b = stock(), stock()
    locked_stop_at_open(a)
    params = replace(P2, max_positions=1)
    r = run({"10010": a, "20020": b}, {(0, "10010"): 2.0, (1, "20020"): 2.0}, params=params)
    order_b = r.orders[r.orders["symbol"] == "20020"].iloc[0]
    assert order_b["status"] == "cancelled_no_slot"
    # v1: the gap stop sells at the day-2 open, which frees the slot for B the same morning
    r1 = run(
        {"10010": a, "20020": b},
        {(0, "10010"): 2.0, (1, "20020"): 2.0},
        params=replace(P1, max_positions=1),
    )
    assert r1.orders[r1.orders["symbol"] == "20020"].iloc[0]["status"] == "filled"


def test_time_exit_closing_at_limit_down_is_carried() -> None:
    s = stock()
    s["ll"][20] = "1"
    bar(s, 20, 1000, 1000, 990, 990)  # 20th holding day closes at the lower limit
    bar(s, 21, 995, 1000, 990, 995)
    t = only_trade(run({"10010": s}, SIG))
    assert t["exit_reason"] == "time_exit"
    assert (t["trigger_phase"], t["exit_phase"]) == ("close", "open")
    assert pd.Timestamp(t["exit_date"]) == pd.Timestamp(DAYS[21])
    assert (t["base_price"], t["carried_days"]) == (995, 1)
    assert t["last_unfilled_cause"] == "limit_down_close"


def test_close_at_limit_down_without_ll_flag_is_filled() -> None:
    s = stock()
    bar(s, 20, 1000, 1000, 990, 990)  # close == low but LL = 0
    t = only_trade(run({"10010": s}, SIG))
    assert pd.Timestamp(t["exit_date"]) == pd.Timestamp(DAYS[20]) and t["base_price"] == 990


def test_carried_sell_request_is_split_adjusted() -> None:
    s = stock()
    locked_stop_at_open(s)
    s["adj_factor"] = [1.0] * N
    s["adj_factor"][3] = 0.5  # 1:2 split effective day 3
    for i in range(3, N):
        bar(s, i, 500, 500, 500, 500)
    r = run({"10010": s}, SIG)
    t = only_trade(r)
    assert t["quantity"] == 200  # 100 shares before the split
    assert pd.Timestamp(t["exit_date"]) == pd.Timestamp(DAYS[3]) and t["base_price"] == 500
    assert t["trigger_level"] == pytest.approx(950.95 * 0.5)
    assert r.status == "complete"


def test_unfilled_on_the_last_day_needs_review() -> None:
    s = stock()
    locked_stop_at_open(s)
    r = run({"10010": s}, SIG, end=2)
    assert r.trades.empty and r.status == "needs_review"
    assert list(r.unresolved_events["event"]) == ["end_of_test_untradable"]
    assert r.stats["sell_requests_open_at_end"] == 1


# ------------------------------------------------------------------ data handling


def test_missing_limit_flag_stops_the_run() -> None:
    """Neither 0 nor "no limit": the buy that needs the flag is not executed; run stops."""
    s = stock()
    s["ul"][1] = None  # buy day: the lock cannot be judged
    r = run({"10010": s}, SIG)
    assert r.orders.empty and r.trades.empty
    assert list(r.unresolved_events["event"]) == ["limit_flag_missing"]
    assert r.status == "needs_review" and r.stats["limit_flag_missing"] == 1
    assert r.halted is not None and r.halted["date"] == pd.Timestamp(DAYS[1])
    assert len(r.equity_curve) == 1  # day 0 only


def test_missing_flag_where_not_needed_is_ignored() -> None:
    s = stock()
    s["ll"][5] = None  # held, no sell attempt that day: nothing to judge
    s["ul"][7] = None  # no buy order that day
    r = run({"10010": s}, SIG)
    assert r.status == "complete" and r.unresolved_events.empty


def test_limit_down_open_alone_creates_no_sell_request() -> None:
    """LL=1 and open == low only decide whether an EXISTING sell can execute."""
    s = stock()
    s["ll"][3] = "1"
    bar(s, 3, 980, 1000, 980, 990)  # opened at the lower limit, but above the stop 950.95
    r = run({"10010": s}, SIG)
    t = only_trade(r)
    assert t["exit_reason"] == "time_exit"  # held until the holding limit
    assert r.sell_unfilled.empty and r.stats["sell_unfilled"] == {}
    eq = r.equity_curve.set_index("date")
    assert eq.loc[pd.Timestamp(DAYS[3]), "positions"] == 1


def test_v2_refuses_data_without_limit_flags() -> None:
    data = make_data(DAYS, {"10010": stock()})
    data.bars = data.bars.drop(columns=["upper_limit", "lower_limit"])
    md = MarketData.from_processed(data)
    with pytest.raises(DataError, match="process_data"):
        run_md(md, SIG, P2, 0, N - 1)
    assert len(run_md(md, SIG, P1, 0, N - 1).trades) == 1  # v1 does not need them


def test_limit_flags_do_not_change_signals() -> None:
    """UL/LL are execution-only: the strategy's scores ignore them."""
    s = stock()
    for i in range(N):
        bar(s, i, 1000 + i, 1000 + i, 1000 + i, 1000 + i)
    s["volume"] = [1_000_000] * N
    s["volume"][25] = 3_000_000  # breakout with 3x volume on day 25
    flagged = {k: (list(v) if isinstance(v, list) else v) for k, v in s.items()}
    flagged["ul"] = ["1"] * N
    flagged["ll"] = ["1"] * N
    params = {"lookback_days": 20, "volume_multiplier": 2.0}
    strat = HighPriceBreakout(
        {
            **params,
            "max_position_pct": 0.2,
            "take_profit_pct": 0.1,
            "stop_loss_pct": 0.05,
            "max_holding_days": 20,
        }
    )
    a = strat.generate_signals(MarketData.from_processed(make_data(DAYS, {"10010": s})))
    b = strat.generate_signals(MarketData.from_processed(make_data(DAYS, {"10010": flagged})))
    pd.testing.assert_frame_equal(a, b)
    assert a.notna().any().any()
