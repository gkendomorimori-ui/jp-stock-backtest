from datetime import date
from typing import Any

import pytest

from src.backtest.config import BacktestConfig
from src.utils.config_loader import load_yaml


def _complete_raw() -> dict[str, Any]:
    return {
        "capital": {"initial_capital": 1_000_000},
        "costs": {"commission": {"model": "rate", "rate": 0.0}, "slippage": {"rate": 0.0}},
        "period": {"start_date": "2020-01-01", "end_date": "2024-12-31"},
        "position": {"maximum_positions": 5, "lot_size": 100},
        "evaluation": {"trading_days_per_year": 252, "risk_free_rate": 0.0},
    }


# --- repository config/backtest.yaml (decided values, 2026-09-27) ---


def test_repository_backtest_config_values() -> None:
    config = BacktestConfig.from_dict(load_yaml("config/backtest.yaml"))
    assert config.initial_capital == 1_500_000
    assert config.commission_model == "rate"
    assert config.commission_rate == pytest.approx(0.0005)
    assert config.commission_minimum == 0
    assert config.slippage_model == "rate"
    assert config.slippage_rate == pytest.approx(0.001)
    assert config.trading_days_per_year == 252
    assert config.risk_free_rate == 0.0
    assert config.maximum_positions == 5
    assert config.lot_size == 100
    assert config.long_only is True
    assert config.execution_lag_days == 1
    assert config.execution_price == "open"
    assert config.run_type == "smoke_test"
    assert config.dividends_included is False


def test_repository_split_and_benchmark() -> None:
    raw = load_yaml("config/backtest.yaml")
    split = raw["period"]["split"]
    assert set(split) >= {"development", "final_evaluation"}
    assert split["indicator_only_days_before_final"] == 20
    bench = raw["benchmark"]
    assert bench["name"] == "TOPIX"
    assert bench["dividends_included"] is False


def test_repository_backtest_config_only_period_is_todo() -> None:
    config = BacktestConfig.from_dict(load_yaml("config/backtest.yaml"))
    assert config.missing_fields() == ["start_date", "end_date"]
    with pytest.raises(ValueError):
        config.require_complete()


# --- BacktestConfig behaviour ---


def test_complete_config() -> None:
    config = BacktestConfig.from_dict(_complete_raw())
    assert config.start_date == date(2020, 1, 1)
    config.require_complete()


def test_missing_lot_size_is_reported() -> None:
    raw = _complete_raw()
    raw["position"] = {"maximum_positions": 5}
    assert "lot_size" in BacktestConfig.from_dict(raw).missing_fields()


def test_zero_lag_rejected() -> None:
    raw = _complete_raw()
    raw["execution"] = {"execution_lag_days": 0}
    with pytest.raises(ValueError, match="look-ahead"):
        BacktestConfig.from_dict(raw).require_complete()


# --- repository config/universe.yaml ---


def test_repository_universe_config() -> None:
    u = load_yaml("config/universe.yaml")["universe"]
    assert u["mode"] == "market"
    current = {m["code"] for m in u["markets"]["current"]}
    legacy = {m["code"] for m in u["markets"]["legacy"]}
    excluded = {m["code"] for m in u["markets"]["excluded"]}
    assert current == {"0111", "0112", "0113"}
    assert legacy == {"0101", "0102", "0104", "0106", "0107"}
    assert not (current | legacy) & excluded
    pc = u["product_category"]
    assert pc["include"] == ["011"]
    assert {e["code"] for e in pc["exclude"]} == {"012", "013", "014", "021", "022", "023", "024"}
    assert u["unclassified_policy"] == "exclude"
    assert u["common_stock_code_suffix"] == "0"
    f = u["filters"]
    assert f["min_avg_turnover"] == 100_000_000
    assert f["turnover_window_days"] == 20
    assert f["turnover_window_includes_signal_day"] is False
    assert f["min_history_days"] == 20
    assert f["history_window"] == "fixed_exchange_calendar"


# --- strategies/high_price_breakout.yaml ---


def test_high_price_breakout_parameters() -> None:
    spec = load_yaml("strategies/high_price_breakout.yaml")
    assert spec["name"] == "high_price_breakout"
    assert spec["version"] == "0.2.0"
    p = spec["parameters"]
    assert p == {
        "lookback_days": 20,
        "volume_multiplier": 2.0,
        "max_position_pct": 0.20,
        "take_profit_pct": 0.10,
        "stop_loss_pct": 0.05,
        "max_holding_days": 20,
    }


def test_high_price_breakout_position_cap_is_consistent_with_max_positions() -> None:
    config = BacktestConfig.from_dict(load_yaml("config/backtest.yaml"))
    pct = load_yaml("strategies/high_price_breakout.yaml")["parameters"]["max_position_pct"]
    assert config.maximum_positions is not None
    assert pct * config.maximum_positions <= 1.0 + 1e-9
