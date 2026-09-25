"""Base class for all strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd


class Strategy(ABC):
    """Generate trading signals from normalized OHLCV data.

    A strategy only decides *what* it wants to hold. Execution timing (T+1),
    costs and position accounting belong to the backtest engine.

    Subclasses must set :attr:`name` and :attr:`version` to match the spec in
    ``strategies/<name>.md`` and receive all parameters via ``params``.
    """

    name: str = "base"
    version: str = "0.0.0"

    def __init__(self, params: dict[str, Any]) -> None:
        """Store strategy parameters (injected from config, never hard-coded)."""
        self.params: dict[str, Any] = dict(params)

    @abstractmethod
    def generate_signals(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """Return signals computed at the close of each day T.

        Args:
            ohlcv: Normalized OHLCV (see :mod:`src.data.schema`).

        Returns:
            DataFrame with at least ``date``, ``symbol`` and ``signal`` columns.
            The row for date T must use only information available at T's close.
            The exact signal encoding is TODO and will be fixed together with the
            backtest engine design.
        """

    def metadata(self) -> dict[str, Any]:
        """Return name, version and parameters for run metadata."""
        return {"name": self.name, "version": self.version, "parameters": self.params}
