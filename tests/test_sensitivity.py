"""Dated tick exceptions, sensitivity analyses A / B / C, the sensitivity and analysis scripts.

All on synthetic data (no sealed period is computed). Unless noted: signal on day 0, bought
on day 1 at open 1,000 -> entry 1,001, take profit 1,101.1, stop 950.95, ScaleCat "-".
"""

import hashlib
import importlib.util
import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import BacktestEngine, EngineResult, ExecutionParams
from src.backtest.periods import PeriodPlan, Segment
from src.backtest.ranking import RandomRanking, TurnoverRanking, random_key
from src.backtest.ticks import FINE, STANDARD, TickRules
from src.data.market_data import MarketData
from src.evaluation.analysis import price_band
from src.universe import average_turnover
from src.utils.config_loader import load_yaml
from tests.synthetic import make_data, weekdays
from tests.test_engine_v2 import DAYS, P2, SIG, bar, locked_stop_at_open, only_trade, stock

ROOT = Path(__file__).resolve().parents[1]
N = 30


def run(
    series: dict[str, dict[str, Any]],
    signals: dict[tuple[int, str], float],
    params: ExecutionParams = P2,
    days: list[date] = DAYS,
    tick_rules: TickRules | None = None,
    ranking: Any = None,
    columns_reversed: bool = False,
) -> EngineResult:
    md = MarketData.from_processed(make_data(days, series))
    cols = list(md["close"].columns)
    if columns_reversed:
        cols = cols[::-1]
    scores = pd.DataFrame(np.nan, index=md.dates, columns=cols)
    for (i, sym), v in signals.items():
        scores.iloc[i, scores.columns.get_loc(sym)] = v
    eligible = pd.DataFrame(True, index=md.dates, columns=cols)
    engine = BacktestEngine(params, tick_rules=tick_rules, ranking=ranking)
    return engine.run(md, scores, eligible, md.dates[0], md.dates[len(days) - 1])


def rules(*entries: tuple[str, str, str]) -> TickRules:
    return TickRules.from_config(
        {
            "exceptions": [
                {"code": c, "action": a, "effective": e, "notice": e, "url": "x"}
                for c, a, e in entries
            ]
        }
    )


# ------------------------------------------------------------------ dated tick exceptions


def test_exception_overrides_scale_category_from_its_effective_date() -> None:
    r = rules(("6502", STANDARD, "2023-11-29"))
    large = "TOPIX Large70"
    assert r.classify(pd.Timestamp("2023-11-28"), "65020", large) == FINE
    assert r.classify(pd.Timestamp("2023-11-29"), "65020", large) == STANDARD
    assert r.classify(pd.Timestamp("2024-03-01"), "65020", large) == STANDARD
    assert r.classify(pd.Timestamp("2024-03-01"), "72030", large) == FINE  # other codes


def test_topix100_era_exception_ends_with_its_regime() -> None:
    """Removed from TOPIX100 in 2021 -> Mid400: standard until 2023-06-04, fine from 06-05."""
    r = rules(("9502", STANDARD, "2021-10-29"))
    mid = "TOPIX Mid400"
    assert r.classify(pd.Timestamp("2021-10-28"), "95020", "TOPIX Large70") == FINE
    assert r.classify(pd.Timestamp("2023-06-02"), "95020", "TOPIX Large70") == STANDARD
    assert r.classify(pd.Timestamp("2023-06-05"), "95020", mid) == FINE


def test_latest_exception_wins_and_alphanumeric_codes() -> None:
    r = rules(
        ("6966", FINE, "2023-10-31"), ("6966", STANDARD, "2024-10-31"), ("285A", FINE, "2025-10-31")
    )
    assert r.classify(pd.Timestamp("2024-01-10"), "69660", "-") == FINE
    assert r.classify(pd.Timestamp("2024-10-31"), "69660", "TOPIX Mid400") == STANDARD
    assert r.classify(pd.Timestamp("2025-10-31"), "285A0", None) == FINE
    assert r.classify(pd.Timestamp("2025-10-30"), "285A0", None) is None  # still unknown


def test_invalid_exception_is_refused() -> None:
    with pytest.raises(ValueError):
        rules(("6502", "coarse", "2023-11-29"))
    with pytest.raises(ValueError):
        rules(("6502", STANDARD, "2027-03-01"))  # no implemented regime


def test_repository_ledger_is_consistent() -> None:
    raw = load_yaml("config/tick_exceptions.yaml")
    r = TickRules.from_config(raw)
    assert len(r.exceptions) == len(raw["exceptions"]) > 0
    for e in r.exceptions:
        assert e.notice <= e.effective and e.url.startswith("https://www.jpx.co.jp/news/1030/")
        assert len(e.symbol) == 5
    keys = [(e.symbol, e.effective) for e in r.exceptions]
    assert len(keys) == len(set(keys))


def test_engine_uses_the_exception_for_rounding() -> None:
    days = weekdays(date(2023, 11, 20), N)
    k = days.index(date(2023, 11, 29))
    s = stock(scale="TOPIX Large70")
    bar(s, k, 1000, 1000, 940, 990)
    t0 = only_trade(run({"65020": s}, {(0, "65020"): 2.0}, days=days))
    assert t0["base_price"] == pytest.approx(950.9)  # fine table from ScaleCat
    t1 = only_trade(
        run(
            {"65020": s},
            {(0, "65020"): 2.0},
            days=days,
            tick_rules=rules(("6502", STANDARD, "2023-11-29")),
        )
    )
    assert t1["base_price"] == 950 and t1["tick_class"] == "standard"


# ------------------------------------------------------------------ A: take profit first


PA = replace(P2, intraday_priority="take_profit")


def test_a_take_profit_first_only_when_both_touched() -> None:
    s = stock()
    bar(s, 2, 1000, 1110, 940, 1000)
    base = only_trade(run({"10010": s}, SIG))
    a_res = run({"10010": s}, SIG, params=PA)
    a = only_trade(a_res)
    assert base["exit_reason"] == "stop_loss" and base["base_price"] == 950
    assert a["exit_reason"] == "take_profit" and a["base_price"] == 1102  # rounded-up limit
    assert bool(a["intraday_both_touched"]) and not bool(a["stop_priority_applied"])
    assert a["exit_price"] == pytest.approx(1102 * 0.999)  # same slippage as the base
    assert a_res.stats["take_profit_priority_applied"] == 1


def test_a_does_not_change_stop_only_days() -> None:
    s = stock()
    bar(s, 2, 1000, 1000, 940, 990)
    t = only_trade(run({"10010": s}, SIG, params=PA))
    assert t["exit_reason"] == "stop_loss" and t["base_price"] == 950


def test_a_does_not_override_open_exits_or_carried_requests() -> None:
    gap = stock()
    bar(gap, 2, 940, 1110, 930, 1000)  # gap below the stop: sold at the open
    assert only_trade(run({"10010": gap}, SIG, params=PA))["exit_reason"] == "stop_loss_open"
    carried = stock()
    locked_stop_at_open(carried)
    bar(carried, 3, 1000, 1110, 940, 1000)  # both levels touched while the request is carried
    t = only_trade(run({"10010": carried}, SIG, params=PA))
    assert (t["exit_reason"], t["carried_days"], t["base_price"]) == ("stop_loss_open", 1, 1000)


# ------------------------------------------------------------------ B: ranking


def test_random_key_is_sha256_of_a_fixed_string() -> None:
    expected = hashlib.sha256(b"7|2022-01-04|72030").hexdigest()
    assert random_key(7, pd.Timestamp("2022-01-04"), "72030") == expected
    assert random_key(7, pd.Timestamp("2022-01-05"), "72030") != expected  # changes daily
    assert random_key(8, pd.Timestamp("2022-01-04"), "72030") != expected


def many_signals() -> tuple[dict[str, dict[str, Any]], dict[tuple[int, str], float]]:
    series = {f"{10010 + 10 * j}": stock() for j in range(8)}
    signals = {(0, sym): 2.0 + j * 0.1 for j, sym in enumerate(series)}
    return series, signals


def test_random_order_is_reproducible_and_independent_of_column_order() -> None:
    series, signals = many_signals()
    params = replace(P2, max_positions=2)
    a = run(series, signals, params=params, ranking=RandomRanking(3))
    b = run(series, signals, params=params, ranking=RandomRanking(3), columns_reversed=True)
    pd.testing.assert_frame_equal(
        a.orders.drop(columns="score"), b.orders.drop(columns="score"), check_like=True
    )
    order = sorted(signals, key=lambda k: (random_key(3, pd.Timestamp(DAYS[0]), k[1]), k[1]))
    ranked = a.orders.sort_values("rank")["symbol"].tolist()
    assert ranked == [sym for _, sym in order]
    assert list(a.orders.sort_values("rank")["rank_value"]) == sorted(a.orders["rank_value"])


def test_turnover_ranking_uses_the_prior_window_mean() -> None:
    series, signals = many_signals()
    for j, sym in enumerate(series):  # turnover grows with j on days before the signal
        series[sym]["turnover"] = [1e8 * (8 - j)] * N
    data = make_data(DAYS, series)
    md = MarketData.from_processed(data)
    avg = average_turnover(md, 1)  # window 1 on this tiny sample: day T-1 only
    params = replace(P2, max_positions=1)
    r = run(
        series,
        {(k[0] + 1, k[1]): v for k, v in signals.items()},
        params=params,
        ranking=TurnoverRanking(avg),
    )
    first = r.orders.sort_values("rank").iloc[0]
    assert first["symbol"] == "10010"  # highest mean turnover, lowest volume ratio
    assert first["status"] == "filled"
    assert set(r.orders["status"]) == {"filled", "cancelled_no_slot"}


def test_held_symbol_is_excluded_under_any_ranking() -> None:
    s = stock()
    sig = {(0, "10010"): 2.0, (1, "10010"): 2.0}
    for ranking in (None, RandomRanking(0)):
        r = run({"10010": s}, sig, ranking=ranking)
        assert list(r.orders["status"]) == ["filled", "ignored_already_held"]


# ------------------------------------------------------------------ C: max(rate, one tick)


PC = replace(P2, slippage_model="max_rate_tick")


def test_c_uses_one_tick_when_larger_than_the_rate() -> None:
    s = stock(500)  # standard table: tick 1 yen > 0.1% (0.5 yen)
    bar(s, 5, 500, 500, 500, 500)
    r = run({"10010": s}, SIG, params=PC)
    t = only_trade(r)
    assert t["entry_price"] == pytest.approx(501)  # 500 + 1 tick
    assert t["exit_price"] == pytest.approx(t["base_price"] - 1)
    assert r.orders.iloc[0]["fill_price"] == pytest.approx(501)


def test_c_uses_the_rate_when_larger_than_one_tick() -> None:
    s = stock(1000, scale="TOPIX Core30")  # fine table: tick 0.1 < 0.1% (1.0 yen)
    t = only_trade(run({"10010": s}, SIG, params=PC))
    assert t["entry_price"] == pytest.approx(1001)
    assert t["exit_price"] == pytest.approx(t["base_price"] - t["base_price"] * 0.001)


def test_c_tick_depends_on_the_price_band_of_the_base_price() -> None:
    s = stock(3001)  # standard table above 3,000 yen: tick 5
    t = only_trade(run({"10010": s}, SIG, params=replace(PC, max_position_pct=0.5)))
    assert t["entry_price"] == pytest.approx(3006)


def test_c_needs_v2_and_stops_on_unknown_class() -> None:
    with pytest.raises(ValueError):
        replace(PC, model_version="v1")
    s = stock(500, scale=[None] * N)
    r = run({"10010": s}, SIG, params=PC)
    assert r.status == "needs_review" and r.halted is not None
    assert r.trades.empty and list(r.unresolved_events["event"]) == ["tick_class_unknown"]


# ------------------------------------------------------------------ analysis helpers


@pytest.mark.parametrize(
    ("price", "band"),
    [
        (9.9, "<10"),
        (10, "10-100"),
        (99.9, "10-100"),
        (100, "100-500"),
        (500, "500-1000"),
        (999, "500-1000"),
        (1000, "1000-3000"),
        (2999.5, "1000-3000"),
        (3000, ">=3000"),
    ],
)
def test_price_bands_are_fixed_and_lower_inclusive(price: float, band: str) -> None:
    assert price_band(price) == band


# ------------------------------------------------------------------ scripts


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_sensitivity_and_analyze_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_end_to_end import DAYS as E_DAYS
    from tests.test_end_to_end import EVAL_END, EVAL_START, build

    data = build()
    plan = PeriodPlan(
        E_DAYS[0],
        E_DAYS[-1],
        20,
        (
            Segment("initial_warmup", E_DAYS[0], E_DAYS[EVAL_START - 1], 20, "warmup"),
            Segment(
                "development",
                E_DAYS[EVAL_START],
                E_DAYS[EVAL_END],
                EVAL_END - EVAL_START + 1,
                "open",
            ),
        ),
    )
    sens = _load("run_sensitivity")
    monkeypatch.setattr(sens, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sens, "load_period_plan", lambda: plan)
    monkeypatch.setattr(sens, "load_processed", lambda _p: data)
    assert sens.main(["--analysis", "all"]) == 0
    runs = tmp_path / "results" / "runs"
    out = next(runs.glob("*_sensitivity_all"))
    summary = json.loads((out / "sensitivity_summary.json").read_text(encoding="utf-8"))
    names = [r["variant"] for r in summary["runs"]]
    assert names[0] == "base" and "A_take_profit_first" in names and "C_max_rate_or_tick" in names
    assert sum(n.startswith("B_random_seed") for n in names) == 20
    assert summary["random_ranking_all_seeds"]["total_return"]["n"] == 20
    for r in summary["runs"]:
        s = json.loads((runs / r["run"] / "summary.json").read_text(encoding="utf-8"))
        assert s["metadata"]["run_type"] == "development"
        assert len(s["variant"]["changed_fields"]) <= 2  # random: ranking + seed only
    from src.evaluation.verify import verify_run

    for r in summary["runs"]:
        checks, _ = verify_run(runs / r["run"])
        assert [c.name for c in checks if not c.ok] == [], r["variant"]
    # the pre-registered B list is fixed: seeds 0..19
    assert sens.RANDOM_SEEDS == tuple(range(20))

    # analysis of the base run (processed data from the same synthetic set)
    proc = tmp_path / "data" / "processed" / "jquants"
    proc.mkdir(parents=True)
    data.bars.to_parquet(proc / "bars.parquet", index=False)
    data.master.to_parquet(proc / "master.parquet", index=False)
    ana = _load("analyze_run")
    monkeypatch.setattr(ana, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(ana, "PROCESSED", proc)
    assert ana.main([]) == 0
    base_dir = runs / summary["base_run"]
    result = json.loads((base_dir / "analysis.json").read_text(encoding="utf-8"))
    tc = result["totals_check"]
    assert tc["trades"] == tc["by_market_sum"] == tc["by_band_sum"] == 2
    assert tc["yearly_realized_sum"] == pytest.approx(tc["pnl"])
    assert result["yearly"][0]["partial"] is True
    assert {b["price_band"] for b in result["by_price_band"]} == {"500-1000", "1000-3000"}


def test_run_sensitivity_refuses_when_development_is_not_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    d = weekdays(date(2026, 1, 5), 40)
    plan = PeriodPlan(
        d[0],
        d[-1],
        20,
        (
            Segment("initial_warmup", d[0], d[19], 20, "warmup"),
            Segment("development", d[20], d[39], 20, "sealed"),
        ),
    )
    sens = _load("run_sensitivity")
    monkeypatch.setattr(sens, "load_period_plan", lambda: plan)
    monkeypatch.setattr(sens, "load_processed", lambda _p: pytest.fail("data must not be loaded"))
    assert sens.main(["--analysis", "A"]) == 1
