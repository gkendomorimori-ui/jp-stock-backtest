import pytest

from src.backtest.costs import CostModel


def test_slippage_is_adverse() -> None:
    m = CostModel(slippage_rate=0.01)
    assert m.execution_price(100.0, "buy") == pytest.approx(101.0)
    assert m.execution_price(100.0, "sell") == pytest.approx(99.0)


def test_commission_rate_with_minimum() -> None:
    m = CostModel(commission_rate=0.001, commission_minimum=50.0)
    assert m.commission(10_000) == 50.0
    assert m.commission(1_000_000) == pytest.approx(1000.0)


def test_commission_fixed() -> None:
    assert CostModel(commission_fixed=100.0).commission(1_000_000) == 100.0


def test_zero_cost_default() -> None:
    m = CostModel()
    assert m.commission(1_000_000) == 0.0
    assert m.execution_price(100.0, "buy") == 100.0
