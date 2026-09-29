from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.backtest.runner import run_high_price_breakout
from src.evaluation.breakdown import classify_trades, summarize
from src.utils.config_loader import load_yaml
from tests.synthetic import make_data, weekdays

N = 30
DAYS = weekdays(date(2026, 1, 5), N)


def series(price: float, volume: float) -> dict[str, list[Any]]:
    return {k: [price] * N for k in ("open", "high", "low", "close")} | {"volume": [volume] * N}


def breakout(price: float, new: float, volume: float, day: int) -> dict[str, list[Any]]:
    s = series(price, volume)
    for i in range(day, N):
        for k in ("open", "high", "low", "close"):
            s[k][i] = new
    s["open"][day] = s["low"][day] = price  # breakout happens during the signal day
    s["volume"][day] = volume * 3
    return s


def run(tmp_path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    data = make_data(
        DAYS,
        {
            "10010": breakout(5.0, 6.0, 30_000_000, 22),  # low price (6 yen), +20%
            "20020": breakout(500.0, 1100.0, 400_000, 22),  # +120% jump
            "30030": breakout(4.0, 9.0, 40_000_000, 23),  # low price AND +125% jump
            "40040": breakout(1000.0, 1050.0, 200_000, 23),  # neither
        },
    )
    out, _ = run_high_price_breakout(
        data,
        load_yaml("config/backtest.yaml"),
        load_yaml("config/universe.yaml"),
        load_yaml("strategies/high_price_breakout.yaml"),
        eval_start=DAYS[20],
        eval_end=DAYS[N - 1],
        run_type="smoke_test",
        results_root=tmp_path,
    )
    trades = pd.read_csv(
        out / "trades.csv", dtype={"symbol": str}, parse_dates=["entry_date", "exit_date"]
    )
    orders = pd.read_csv(
        out / "orders.csv", dtype={"symbol": str}, parse_dates=["signal_date", "exec_date"]
    )
    c = classify_trades(trades, orders, data)
    return c, summarize(c)


def test_classes_and_overlap(tmp_path: Path) -> None:
    c, s = run(tmp_path)
    flags = c.set_index("symbol")[["low_price", "jump_100", "neither"]].to_dict("index")
    assert flags["10010"] == {"low_price": True, "jump_100": False, "neither": False}
    assert flags["20020"] == {"low_price": False, "jump_100": True, "neither": False}
    assert flags["30030"] == {"low_price": True, "jump_100": True, "neither": False}
    assert flags["40040"] == {"low_price": False, "jump_100": False, "neither": True}
    cls = s["classes"]
    assert (cls["low_price"]["trades"], cls["jump_100"]["trades"]) == (2, 2)
    assert cls["low_price_and_jump_100"]["trades"] == 1
    assert cls["neither"]["trades"] == 1
    assert s["total"]["trades"] == 4
    assert s["consistency_trades_add_up"] and s["consistency_pnl_adds_up"]
    assert c.loc[c["symbol"] == "20020", "signal_adj_return"].iloc[0] == pytest.approx(1.2)
    assert any("NOT the performance" in n for n in s["notes"])
