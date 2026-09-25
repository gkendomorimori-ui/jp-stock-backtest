"""Data layer: provider interface and normalized OHLCV schema.

Strategies and the backtest engine depend only on the normalized OHLCV format,
never on a specific data source.
"""

from src.data.base import DataProvider
from src.data.schema import OHLCV_COLUMNS, validate_ohlcv

__all__ = ["DataProvider", "OHLCV_COLUMNS", "validate_ohlcv"]
