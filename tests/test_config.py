from datetime import date

import pytest

from src.backtest.config import BacktestConfig
from src.utils.config_loader import load_yaml


def test_repository_config_loads_and_reports_todos() -> None:
    config = BacktestConfig.from_dict(load_yaml("config/backtest.yaml"))
    assert config.execution_lag_days == 1
    assert "initial_capital" in config.missing_fields()
    with pytest.raises(ValueError):
        config.require_complete()


def test_complete_config() -> None:
    raw = {
        "capital": {"initial_capital": 1_000_000},
        "costs": {"commission": {"model": "rate", "rate": 0.0}, "slippage": {"rate": 0.0}},
        "period": {"start_date": "2020-01-01", "end_date": "2024-12-31"},
        "evaluation": {"trading_days_per_year": 245, "risk_free_rate": 0.0},
    }
    config = BacktestConfig.from_dict(raw)
    assert config.start_date == date(2020, 1, 1)
    config.require_complete()


def test_zero_lag_rejected() -> None:
    raw = {
        "capital": {"initial_capital": 1},
        "costs": {"commission": {"rate": 0.0}, "slippage": {"rate": 0.0}},
        "period": {"start_date": "2020-01-01", "end_date": "2020-12-31"},
        "execution": {"execution_lag_days": 0},
        "evaluation": {"trading_days_per_year": 245, "risk_free_rate": 0.0},
    }
    with pytest.raises(ValueError, match="look-ahead"):
        BacktestConfig.from_dict(raw).require_complete()


def test_universe_config_loads() -> None:
    assert "universe" in load_yaml("config/universe.yaml")
