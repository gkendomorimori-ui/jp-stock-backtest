import math

import pandas as pd
import pytest

from src.evaluation.metrics import (
    cagr,
    compute_metrics,
    max_drawdown,
    total_return,
    trade_stats,
)


def _equity(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2024-01-01", periods=len(values), freq="D"))


def test_total_return() -> None:
    assert total_return(_equity([100, 110, 121])) == pytest.approx(0.21)


def test_max_drawdown() -> None:
    assert max_drawdown(_equity([100, 120, 90, 130])) == pytest.approx(-0.25)


def test_cagr_one_year_doubling() -> None:
    eq = pd.Series([100.0, 200.0], index=pd.to_datetime(["2020-01-01", "2021-01-01"]))
    assert cagr(eq) == pytest.approx(1.0, rel=1e-2)


def test_trade_stats() -> None:
    s = trade_stats(pd.Series([100.0, -50.0, 200.0, -50.0]))
    assert s["profit_factor"] == pytest.approx(3.0)
    assert s["win_rate"] == pytest.approx(0.5)
    assert s["number_of_trades"] == 4
    assert s["average_profit"] == pytest.approx(150.0)
    assert s["average_loss"] == pytest.approx(-50.0)
    assert s["expectancy"] == pytest.approx(50.0)


def test_trade_stats_empty() -> None:
    s = trade_stats(pd.Series([], dtype=float))
    assert math.isnan(s["profit_factor"])
    assert s["number_of_trades"] == 0


def test_compute_metrics_has_all_required_keys() -> None:
    m = compute_metrics(
        _equity([100, 101, 99, 103, 104]),
        pd.Series([10.0, -5.0]),
        periods_per_year=245,
        risk_free_rate=0.0,
    )
    required = {
        "total_return",
        "cagr",
        "max_drawdown",
        "sharpe_ratio",
        "sortino_ratio",
        "profit_factor",
        "win_rate",
        "number_of_trades",
        "average_profit",
        "average_loss",
        "expectancy",
    }
    assert required <= m.keys()
    assert isinstance(m["number_of_trades"], int)
