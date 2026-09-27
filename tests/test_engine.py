"""Rule-by-rule tests of the execution model (strategies/high_price_breakout.md v0.2.0).

Costs: slippage 0.1%, commission 0.05%. Expected numbers are computed by hand.
"""

from dataclasses import replace
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine, EngineResult, ExecutionParams
from tests.synthetic import flat, make_md, weekdays

N = 30
DAYS = weekdays(date(2026, 1, 5), N)
S, C = 0.001, 0.0005
P = ExecutionParams(
    initial_capital=1_000_000,
    commission_rate=C,
    slippage_rate=S,
    lot_size=100,
    max_positions=5,
    max_position_pct=0.20,
    take_profit_pct=0.10,
    stop_loss_pct=0.05,
    max_holding_days=20,
)


def set_bar(s: dict[str, list[Any]], i: int, o: Any, h: Any, lo: Any, c: Any) -> None:
    s["open"][i], s["high"][i], s["low"][i], s["close"][i] = o, h, lo, c
    if o is None:
        s["volume"][i] = None


def series_from(day0_close: float, price: float) -> dict[str, list[Any]]:
    """Close ``day0_close`` on day 0, then a flat ``price``."""
    s = flat(N, price)
    set_bar(s, 0, day0_close, day0_close, day0_close, day0_close)
    return s


def run(
    series: dict[str, dict[str, list[Any]]],
    signals: dict[tuple[int, str], float],
    params: ExecutionParams = P,
    start: int = 0,
    end: int = N - 1,
) -> EngineResult:
    md = make_md(DAYS, series)
    scores = pd.DataFrame(np.nan, index=md.dates, columns=md["close"].columns)
    for (i, sym), v in signals.items():
        scores.iloc[i, scores.columns.get_loc(sym)] = v
    eligible = pd.DataFrame(True, index=md.dates, columns=md["close"].columns)
    return BacktestEngine(params).run(md, scores, eligible, md.dates[start], md.dates[end])


def only_trade(r: EngineResult) -> pd.Series:
    assert len(r.trades) == 1, r.trades
    return r.trades.iloc[0]


# ------------------------------------------------------------------ entry and sizing


def test_entry_at_next_open_sizing_and_time_exit_at_close() -> None:
    r = run({"10010": flat(N, 900)}, {(0, "10010"): 2.0})
    t = only_trade(r)
    # q0 = floor(200000 / (900*1.001*1.0005) / 100) * 100 = 200
    assert t["quantity"] == 200
    assert t["entry_date"] == pd.Timestamp(DAYS[1])
    assert t["entry_price"] == pytest.approx(900.9)
    # entry day = day 1 -> 20th holding day is DAYS[20]; sold at that close
    assert t["exit_date"] == pd.Timestamp(DAYS[20])
    assert t["exit_reason"] == "time_exit" and t["holding_days"] == 20
    assert t["exit_price"] == pytest.approx(899.1)
    cost = 200 * 900.9 * 1.0005  # 180,270.09
    proceeds = 200 * 899.1 * (1 - C)  # 179,730.09
    assert t["pnl"] == pytest.approx(proceeds - cost)
    assert r.equity_curve["equity"].iloc[-1] == pytest.approx(1_000_000 + proceeds - cost)


def test_quantity_is_only_reduced_at_the_open() -> None:
    # T close 900 -> planned 200; T+1 opens at 1000: 200*1001*1.0005 = 200,300.1 > 200,000
    r = run({"10010": series_from(900, 1000)}, {(0, "10010"): 2.0})
    o = r.orders.iloc[0]
    assert (o["planned_quantity"], o["filled_quantity"], o["status"]) == (200, 100, "filled")


def test_quantity_is_never_increased() -> None:
    # T close 1000 -> planned 100; T+1 opens at 500 (could afford more) -> still 100
    r = run({"10010": series_from(1000, 500)}, {(0, "10010"): 2.0})
    assert r.orders.iloc[0]["filled_quantity"] == 100


def test_unaffordable_signal_is_not_ordered() -> None:
    r = run({"10010": flat(N, 3000)}, {(0, "10010"): 2.0})  # 100 shares > 200,000
    assert r.orders.iloc[0]["status"] == "not_placed_unaffordable"
    assert r.trades.empty


def test_order_cancelled_when_next_day_not_tradable() -> None:
    s = flat(N, 900)
    set_bar(s, 1, None, None, None, None)
    r = run({"10010": s}, {(0, "10010"): 2.0})
    assert r.orders.iloc[0]["status"] == "cancelled_not_tradable"
    assert r.trades.empty and len(r.orders) == 1  # not carried to day 2


# ------------------------------------------------------------------ exits


def entry_1000() -> dict[str, list[Any]]:
    """Signal on day 0, bought on day 1 at 1000 -> entry 1001, TP 1101.1, SL 950.95."""
    return flat(N, 1000)


def test_gap_down_below_stop_sells_at_open() -> None:
    s = entry_1000()
    set_bar(s, 2, 940, 1000, 930, 990)
    t = only_trade(run({"10010": s}, {(0, "10010"): 2.0}))
    assert t["exit_reason"] == "stop_loss_open"
    assert t["exit_price"] == pytest.approx(940 * 0.999)  # open, not the stop level


def test_gap_up_above_take_profit_sells_at_open() -> None:
    s = entry_1000()
    set_bar(s, 2, 1110, 1120, 1100, 1105)
    t = only_trade(run({"10010": s}, {(0, "10010"): 2.0}))
    assert t["exit_reason"] == "take_profit_open"
    assert t["exit_price"] == pytest.approx(1110 * 0.999)


def test_stop_loss_first_when_both_touched_same_day() -> None:
    s = entry_1000()
    set_bar(s, 2, 1000, 1110, 940, 1000)
    t = only_trade(run({"10010": s}, {(0, "10010"): 2.0}))
    assert t["exit_reason"] == "stop_loss"
    assert t["exit_price"] == pytest.approx(1001 * 0.95 * 0.999)


def test_intraday_take_profit_at_level() -> None:
    s = entry_1000()
    set_bar(s, 2, 1000, 1110, 1000, 1000)
    t = only_trade(run({"10010": s}, {(0, "10010"): 2.0}))
    assert t["exit_reason"] == "take_profit"
    assert t["exit_price"] == pytest.approx(1001 * 1.10 * 0.999)


def test_stop_can_trigger_on_entry_day() -> None:
    s = entry_1000()
    set_bar(s, 1, 1000, 1000, 940, 950)
    t = only_trade(run({"10010": s}, {(0, "10010"): 2.0}))
    assert (t["exit_reason"], t["holding_days"]) == ("stop_loss", 1)


def test_untradable_day_carries_position_and_rechecks_next_open() -> None:
    s = entry_1000()
    set_bar(s, 2, None, None, None, None)
    set_bar(s, 3, 940, 950, 930, 940)
    r = run({"10010": s}, {(0, "10010"): 2.0})
    t = only_trade(r)
    assert (t["exit_reason"], t["exit_date"]) == ("stop_loss_open", pd.Timestamp(DAYS[3]))
    # valued at the last valid close (1000) on the untradable day
    day2 = r.equity_curve.iloc[2]
    assert day2["position_value"] == pytest.approx(100 * 1000)


def test_time_exit_on_untradable_day_sells_at_next_open() -> None:
    s = entry_1000()
    set_bar(s, 20, None, None, None, None)
    set_bar(s, 21, 1020, 1030, 1010, 1025)
    t = only_trade(run({"10010": s}, {(0, "10010"): 2.0}))
    assert (t["exit_reason"], t["exit_date"]) == ("time_exit", pd.Timestamp(DAYS[21]))
    assert t["exit_price"] == pytest.approx(1020 * 0.999)
    assert t["holding_days"] == 21


def test_end_of_test_closes_at_last_close() -> None:
    t = only_trade(run({"10010": entry_1000()}, {(0, "10010"): 2.0}, end=10))
    assert (t["exit_reason"], t["exit_date"]) == ("end_of_test", pd.Timestamp(DAYS[10]))


def test_end_of_test_untradable_needs_review() -> None:
    s = entry_1000()
    set_bar(s, 10, None, None, None, None)
    r = run({"10010": s}, {(0, "10010"): 2.0}, end=10)
    assert r.status == "needs_review"
    assert r.unresolved_events.iloc[0]["event"] == "end_of_test_untradable"
    assert r.trades.empty


# ------------------------------------------------------------------ same-day cash and slots


def two_symbols(a_day2: tuple[Any, Any, Any, Any]) -> dict[str, dict[str, list[Any]]]:
    a = entry_1000()
    set_bar(a, 2, *a_day2)
    return {"10010": a, "20020": flat(N, 1000)}


def test_intraday_sale_does_not_free_a_slot_for_same_day_open_buy() -> None:
    series = two_symbols((1000, 1000, 940, 950))  # A stopped intraday on day 2
    r = run(series, {(0, "10010"): 2.0, (1, "20020"): 2.0}, replace(P, max_positions=1))
    b = r.orders[r.orders["symbol"] == "20020"].iloc[0]
    assert b["status"] == "cancelled_no_slot"


def test_open_sale_frees_a_slot_for_same_day_open_buy() -> None:
    series = two_symbols((940, 950, 930, 940))  # A gap-stopped at the day-2 open
    r = run(series, {(0, "10010"): 2.0, (1, "20020"): 2.0}, replace(P, max_positions=1))
    b = r.orders[r.orders["symbol"] == "20020"].iloc[0]
    assert b["status"] == "filled" and b["exec_date"] == pd.Timestamp(DAYS[2])


def test_intraday_sale_cash_is_not_used_for_same_day_open_buy() -> None:
    params = replace(P, initial_capital=110_000, max_position_pct=1.0)
    series = two_symbols((1000, 1000, 940, 950))  # A stopped intraday on day 2
    r = run(series, {(0, "10010"): 2.0, (1, "20020"): 2.0}, params)
    b = r.orders[r.orders["symbol"] == "20020"].iloc[0]
    assert b["status"] == "cancelled_insufficient_funds"


def test_open_sale_cash_can_be_used_for_same_day_open_buy() -> None:
    params = replace(P, initial_capital=110_000, max_position_pct=1.0)
    series = two_symbols((940, 950, 930, 940))
    r = run(series, {(0, "10010"): 2.0, (1, "20020"): 2.0}, params)
    b = r.orders[r.orders["symbol"] == "20020"].iloc[0]
    assert b["status"] == "filled"


# ------------------------------------------------------------------ ranking


def test_ranking_by_score_then_symbol() -> None:
    series = {"30030": flat(N, 1000), "20020": flat(N, 1000), "10010": flat(N, 1000)}
    signals = {(0, "30030"): 3.0, (0, "20020"): 3.0, (0, "10010"): 2.5}
    r = run(series, signals, replace(P, max_positions=1))
    got = r.orders.sort_values("rank")[["symbol", "rank", "status"]].values.tolist()
    assert got == [
        ["20020", 1, "filled"],
        ["30030", 2, "cancelled_no_slot"],
        ["10010", 3, "cancelled_no_slot"],
    ]


def test_signal_on_held_symbol_is_ignored() -> None:
    r = run({"10010": entry_1000()}, {(0, "10010"): 2.0, (5, "10010"): 2.0})
    assert r.orders["status"].tolist() == ["filled", "ignored_already_held"]
    ignored = r.orders.iloc[1]
    assert ignored["signal_date"] == pd.Timestamp(DAYS[5]) and ignored["filled_quantity"] == 0
    assert r.stats["signals"] == 2
    assert r.stats["signals_ignored_already_held"] == 1
    assert len(r.trades) == 1


# ------------------------------------------------------------------ splits


def split_series(pre: float, post: float, factor: float, day: int) -> dict[str, list[Any]]:
    s = flat(N, pre)
    for i in range(day, N):
        set_bar(s, i, post, post, post, post)
    s["adj_factor"] = [1.0] * N
    s["adj_factor"][day] = factor
    return s


def test_split_while_held_keeps_value_and_adjusts_levels() -> None:
    s = split_series(1000, 500, 0.5, day=5)  # 1:2 split, no real price move
    r = run({"10010": s}, {(0, "10010"): 2.0})
    eq = r.equity_curve.set_index("date")["equity"]
    assert eq[pd.Timestamp(DAYS[5])] == pytest.approx(eq[pd.Timestamp(DAYS[4])])
    t = only_trade(r)
    assert t["exit_reason"] == "time_exit"  # the halved price did not trigger the stop
    assert t["quantity"] == 200
    assert t["entry_price"] == pytest.approx(1001 * 0.5)
    cost = 100 * 1001 * 1.0005
    assert t["pnl"] == pytest.approx(200 * 500 * 0.999 * (1 - C) - cost)


def test_split_between_order_and_fill_adjusts_order_quantity() -> None:
    s = split_series(900, 450, 0.5, day=5)
    r = run({"10010": s}, {(4, "10010"): 2.0})
    o = r.orders.iloc[0]
    assert (o["planned_quantity"], o["filled_quantity"]) == (400, 400)


def test_fractional_shares_after_reverse_split_needs_review() -> None:
    s = split_series(1000, 3000, 3.0, day=5)
    r = run({"10010": s}, {(0, "10010"): 2.0})
    assert r.status == "needs_review"
    assert r.unresolved_events.iloc[0]["event"] == "split_fractional_shares"


# ------------------------------------------------------------------ delisting


def test_delisting_while_held_stops_the_run_without_settling() -> None:
    s = entry_1000()
    s["listed"] = [True] * 6 + [False] * (N - 6)
    other = flat(N, 1000)  # the master still has other securities on day 6
    r = run({"10010": s, "20020": other}, {(0, "10010"): 2.0})
    assert r.status == "needs_review"
    assert r.trades.empty  # no fabricated sale
    assert r.equity_curve["date"].iloc[-1] == pd.Timestamp(DAYS[5])  # stops before day 6
    ev = r.unresolved_events.iloc[0]
    assert (ev["event"], ev["symbol"], ev["quantity"]) == ("delisted", "10010", 100)
    assert ev["last_valid_close"] == 1000
    assert ev["last_valid_close_date"] == pd.Timestamp(DAYS[5])
    assert r.equity_curve["cash"].iloc[-1] == pytest.approx(1_000_000 - 100 * 1001 * 1.0005)


# ------------------------------------------------------------------ diagnostic columns


def diag(t: pd.Series) -> tuple[Any, Any, Any]:
    both, applied = t["intraday_both_touched"], t["stop_priority_applied"]
    return (
        t["exit_phase"],
        None if pd.isna(both) else bool(both),
        None if pd.isna(applied) else bool(applied),
    )


def test_both_levels_touched_intraday_and_priority_applied() -> None:
    s = entry_1000()
    set_bar(s, 2, 1000, 1110, 940, 1000)
    r = run({"10010": s}, {(0, "10010"): 2.0})
    assert diag(only_trade(r)) == ("intraday", True, True)
    assert r.stats["intraday_both_touched"] == 1
    assert r.stats["stop_priority_applied"] == 1


def test_open_exit_is_not_counted_as_both_touched() -> None:
    # gap below the stop at the open; the same day's high later reaches the take-profit level
    s = entry_1000()
    set_bar(s, 2, 940, 1120, 930, 1000)
    r = run({"10010": s}, {(0, "10010"): 2.0})
    assert diag(only_trade(r)) == ("open", None, None)
    assert r.stats["intraday_both_touched"] == 0


def test_single_level_intraday_exits() -> None:
    s = entry_1000()
    set_bar(s, 2, 1000, 1000, 940, 950)
    assert diag(only_trade(run({"10010": s}, {(0, "10010"): 2.0}))) == ("intraday", False, False)
    s = entry_1000()
    set_bar(s, 2, 1000, 1110, 1000, 1000)
    assert diag(only_trade(run({"10010": s}, {(0, "10010"): 2.0}))) == ("intraday", False, False)


def test_close_exit_flags() -> None:
    t = only_trade(run({"10010": flat(N, 900)}, {(0, "10010"): 2.0}))
    assert t["exit_reason"] == "time_exit"
    assert diag(t) == ("close", False, False)
