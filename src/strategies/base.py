"""Base class for all strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from src.data.market_data import MarketData


class Strategy(ABC):
    """Generate entry signals from market data.

    A strategy only decides *which* securities it wants to buy after the close of T and
    how to rank them. Eligibility (universe), execution timing (T+1), costs and position
    accounting belong to other layers.

    Subclasses set :attr:`name` and :attr:`version` to match ``strategies/<name>.md`` and
    receive all parameters via ``params`` (never hard-coded).
    """

    name: str = "base"
    version: str = "0.0.0"

    def __init__(self, params: dict[str, Any]) -> None:
        """Store strategy parameters (injected from the strategy YAML)."""
        self.params: dict[str, Any] = dict(params)

    @abstractmethod
    def generate_signals(self, md: MarketData) -> pd.DataFrame:
        """Return a date x symbol table of ranking scores.

        A non-NaN value at (T, symbol) means "buy signal at the close of T"; higher scores
        rank first (ties are broken by symbol ascending by the engine). The value for T
        must use only information available at T's close.
        """

    def metadata(self) -> dict[str, Any]:
        """Return name, version and parameters for run metadata."""
        return {"name": self.name, "version": self.version, "parameters": self.params}
