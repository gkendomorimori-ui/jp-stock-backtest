import pandas as pd
import pytest

from src.data.schema import OHLCVValidationError, validate_ohlcv


def _frame(**overrides: list[object]) -> pd.DataFrame:
    data: dict[str, list[object]] = {
        "date": ["2024-01-05", "2024-01-04"],
        "symbol": ["7203", "7203"],
        "open": [100.0, 99.0],
        "high": [105.0, 101.0],
        "low": [98.0, 97.0],
        "close": [102.0, 100.0],
        "volume": [1000, 1200],
    }
    data.update(overrides)
    return pd.DataFrame(data)


def test_valid_frame_is_sorted() -> None:
    out = validate_ohlcv(_frame())
    assert list(out["date"].dt.day) == [4, 5]


def test_missing_column() -> None:
    with pytest.raises(OHLCVValidationError):
        validate_ohlcv(_frame().drop(columns=["volume"]))


def test_high_below_close() -> None:
    with pytest.raises(OHLCVValidationError):
        validate_ohlcv(_frame(high=[101.0, 101.0]))


def test_duplicate_rows() -> None:
    with pytest.raises(OHLCVValidationError):
        validate_ohlcv(_frame(date=["2024-01-04", "2024-01-04"]))
