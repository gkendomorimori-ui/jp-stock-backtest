"""End to end on synthetic data: signals -> orders -> fills -> cash/valuation -> saved files.

Uses the repository's config/backtest.yaml, config/universe.yaml and the strategy YAML.
Every expected number below is computed by hand from the spec.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.backtest.runner import run_high_price_breakout
from src.utils.config_loader import load_yaml
from tests.synthetic import make_data, weekdays

N = 36
DAYS = weekdays(date(2026, 1, 5), N)
EVAL_START, EVAL_END = 20, 35  # 20 warm-up trading days before the evaluation
S, C = 0.001, 0.0005  # from config/backtest.yaml


def series(price: float, volume: float) -> dict[str, list[Any]]:
    return {k: [price] * N for k in ("open", "high", "low", "close")} | {"volume": [volume] * N}


def bar(s: dict[str, list[Any]], i: int, o: float, h: float, lo: float, c: float, v: float) -> None:
    s["open"][i], s["high"][i], s["low"][i], s["close"][i], s["volume"][i] = o, h, lo, c, v


def build() -> Any:
    # A: breakout on day 22 (close 1010 > 20-day high 1000, volume 2x) -> buy day 23 open,
    #    take profit intraday on day 25.
    a = series(1000, 200_000)
    bar(a, 22, 1000, 1010, 1000, 1010, 400_000)
    for i in range(23, N):
        bar(a, i, 1010, 1010, 1010, 1010, 200_000)
    bar(a, 25, 1010, 1120, 1010, 1010, 200_000)
    # B: breakout on day 24 (close 505 > 500, volume 2.5x) -> buy day 25 open, held to the end.
    b = series(500, 400_000)
    bar(b, 24, 500, 505, 500, 505, 1_000_000)
    for i in range(25, N):
        bar(b, i, 505, 505, 505, 505, 400_000)
    # C: same breakout as A but turnover 1e7 < 1e8 -> not eligible.
    c = series(1000, 10_000)
    bar(c, 22, 1000, 1010, 1000, 1010, 20_000)
    # D: preferred share code (suffix 5) with a breakout -> not eligible.
    d = series(1000, 200_000)
    bar(d, 22, 1000, 1010, 1000, 1010, 400_000)
    return make_data(DAYS, {"10010": a, "20020": b, "30030": c, "25935": d})


def hand_calculation() -> dict[str, float]:
    e0 = 1_500_000.0
    # A: planned at day-22 close 1010 with budget 0.2*1.5M
    qa = int(0.2 * e0 / (1010 * (1 + S) * (1 + C)) // 100) * 100  # 200
    pa = 1010 * (1 + S)  # entry 1011.01
    cost_a = qa * pa * (1 + C)
    tp_a = pa * 1.10
    proceeds_a = qa * tp_a * (1 - S) * (1 - C)
    # B: planned at day-24 close; equity = cash + A valued at 1010
    e24 = e0 - cost_a + qa * 1010
    qb = int(0.2 * e24 / (505 * (1 + S) * (1 + C)) // 100) * 100  # 500
    pb = 505 * (1 + S)
    cost_b = qb * pb * (1 + C)
    proceeds_b = qb * 505 * (1 - S) * (1 - C)  # end of test at close 505
    return {
        "qa": qa,
        "qb": qb,
        "pnl_a": proceeds_a - cost_a,
        "pnl_b": proceeds_b - cost_b,
        "final": e0 + (proceeds_a - cost_a) + (proceeds_b - cost_b),
        "exit_a": tp_a * (1 - S),
    }


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    out, _ = run_high_price_breakout(
        build(),
        load_yaml("config/backtest.yaml"),
        load_yaml("config/universe.yaml"),
        load_yaml("strategies/high_price_breakout.yaml"),
        eval_start=DAYS[EVAL_START],
        eval_end=DAYS[EVAL_END],
        run_type="smoke_test",
        results_root=tmp_path,
    )
    return out


def test_output_files(run_dir: Path) -> None:
    names = {p.name for p in run_dir.iterdir()}
    assert names == {"summary.json", "trades.csv", "equity_curve.csv", "orders.csv"}


def test_trades_match_hand_calculation(run_dir: Path) -> None:
    h = hand_calculation()
    trades = pd.read_csv(run_dir / "trades.csv", dtype={"symbol": str})
    assert trades["symbol"].tolist() == ["10010", "20020"]
    assert trades["exit_reason"].tolist() == ["take_profit", "end_of_test"]
    assert trades["quantity"].tolist() == [h["qa"], h["qb"]]
    assert trades["entry_date"].tolist() == [str(DAYS[23]), str(DAYS[25])]
    assert trades["exit_date"].tolist() == [str(DAYS[25]), str(DAYS[35])]
    assert trades.loc[0, "exit_price"] == pytest.approx(h["exit_a"])
    assert trades["pnl"].tolist() == pytest.approx([h["pnl_a"], h["pnl_b"]])


def test_cash_and_valuation_match_hand_calculation(run_dir: Path) -> None:
    h = hand_calculation()
    eq = pd.read_csv(run_dir / "equity_curve.csv")
    assert len(eq) == EVAL_END - EVAL_START + 1
    assert eq["date"].iloc[0] == str(DAYS[EVAL_START])
    assert eq["equity"].iloc[0] == pytest.approx(1_500_000)
    assert eq["equity"].iloc[-1] == pytest.approx(h["final"])
    assert eq["cash"].iloc[-1] == pytest.approx(h["final"])  # all positions closed
    assert eq["positions"].iloc[-1] == 0
    assert eq["benchmark_equity"].isna().all()
    # day 25: A was sold intraday, B (bought at the open) is valued at the close 505
    day25 = eq[eq["date"] == str(DAYS[25])].iloc[0]
    assert day25["position_value"] == pytest.approx(h["qb"] * 505)
    assert day25["equity"] == pytest.approx(day25["cash"] + day25["position_value"])


def test_summary(run_dir: Path) -> None:
    h = hand_calculation()
    s = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    meta = s["metadata"]
    assert meta["run_type"] == "smoke_test"
    assert meta["status"] == "complete" and s["metrics_final"] is True
    assert meta["start_date"] == DAYS[EVAL_START].isoformat()
    assert meta["strategy_version"] == "0.2.0"
    bench = meta["benchmark"]
    for key in ("total_return", "cagr", "max_drawdown", "excess_total_return"):
        assert bench[key] is None  # null, never 0
    assert bench["status"] == "unavailable" and "Free plan" in bench["unavailable_reason"]
    m = s["metrics"]
    assert m["final_equity"] == pytest.approx(h["final"])
    assert m["total_return"] == pytest.approx(h["final"] / 1_500_000 - 1)
    assert m["number_of_trades"] == 2
    # only A and B were eligible signals; C (turnover) and D (preferred) were not
    assert s["stats"]["signals"] == 2
    assert s["stats"]["orders"] == {"filled": 2}
    assert s["data"]["warmup_trading_days_before_start"] == 20


def test_warmup_shorter_than_window_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="warm-up"):
        run_high_price_breakout(
            build(),
            load_yaml("config/backtest.yaml"),
            load_yaml("config/universe.yaml"),
            load_yaml("strategies/high_price_breakout.yaml"),
            eval_start=DAYS[19],
            eval_end=DAYS[EVAL_END],
            run_type="smoke_test",
            results_root=tmp_path,
        )


def test_verify_run_passes_on_hand_checked_run(run_dir: Path) -> None:
    from src.evaluation.verify import verify_run

    checks, info = verify_run(run_dir)
    failed = [c for c in checks if not c.ok]
    assert not failed, failed
    assert info["exit_reasons"] == {"take_profit": 1, "end_of_test": 1}


def test_verify_run_detects_inconsistency(run_dir: Path) -> None:
    from src.evaluation.verify import verify_run

    eq = pd.read_csv(run_dir / "equity_curve.csv")
    eq.loc[3, "cash"] -= 1000  # break equity = cash + positions
    eq.to_csv(run_dir / "equity_curve.csv", index=False)
    checks, _ = verify_run(run_dir)
    bad = {c.name for c in checks if not c.ok}
    assert "equity = cash + position value (every day)" in bad
