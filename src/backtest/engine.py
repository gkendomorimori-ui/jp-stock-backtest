"""Backtest engine interface.

The engine implementation (in-house event loop vs. vectorized vs. an external
library) has NOT been decided yet. This module only fixes the contract between
layers so that strategies and evaluation can be developed independently.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from src.backtest.config import BacktestConfig
from src.backtest.costs import CostModel
from src.strategies.base import Strategy

#: Columns of the trades table (see docs/BACKTEST_RULES.md).
TRADE_COLUMNS: list[str] = [
    "symbol",
    "side",
    "entry_date",
    "entry_price",
    "exit_date",
    "exit_price",
    "quantity",
    "commission",
    "pnl",
    "return_pct",
    "exit_reason",
]

#: Columns of the equity curve table.
EQUITY_COLUMNS: list[str] = ["date", "equity", "cash", "position_value"]


@dataclass
class BacktestResult:
    """Output of one backtest run, consumed by the evaluation layer."""

    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    metadata: dict[str, Any] = field(default_factory=dict)


class BacktestEngine(ABC):
    """Run a strategy over normalized OHLCV with T+1 execution and costs."""

    def __init__(self, config: BacktestConfig, cost_model: CostModel) -> None:
        """Store configuration and cost model."""
        self.config = config
        self.cost_model = cost_model

    @abstractmethod
    def run(self, strategy: Strategy, ohlcv: pd.DataFrame) -> BacktestResult:
        """Execute ``strategy`` on ``ohlcv`` and return trades and equity curve."""
