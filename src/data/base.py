"""Abstract interface that every data source must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataProvider(ABC):
    """Fetch daily OHLCV data and return it in the normalized format.

    Implementations live in ``src/data/providers/``. They must return a DataFrame
    that passes :func:`src.data.schema.validate_ohlcv`, so that strategies never
    need to know which data source is being used.
    """

    #: Human-readable identifier saved in run metadata.
    name: str = "base"

    #: Whether prices returned are adjusted for splits / reverse splits.
    adjusted: bool = False

    @abstractmethod
    def fetch_ohlcv(self, symbols: list[str], start: date, end: date) -> pd.DataFrame:
        """Return normalized daily OHLCV for ``symbols`` between ``start`` and ``end``.

        Args:
            symbols: Security codes (e.g. ``"7203"``).
            start: First date (inclusive).
            end: Last date (inclusive).

        Returns:
            Long-format DataFrame with columns :data:`src.data.schema.OHLCV_COLUMNS`.
        """
