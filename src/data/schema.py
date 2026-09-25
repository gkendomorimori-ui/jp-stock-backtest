"""Normalized OHLCV schema shared by all layers."""

from __future__ import annotations

import pandas as pd

#: Column order of the normalized long-format OHLCV frame (1 row = 1 symbol x 1 day).
OHLCV_COLUMNS: list[str] = ["date", "symbol", "open", "high", "low", "close", "volume"]

PRICE_COLUMNS: list[str] = ["open", "high", "low", "close"]


class OHLCVValidationError(ValueError):
    """Raised when a DataFrame does not conform to the normalized OHLCV schema."""


def validate_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Validate a normalized OHLCV DataFrame and return it sorted by (symbol, date).

    Checks performed:
        * all required columns exist
        * no duplicated (symbol, date) rows
        * no missing prices
        * ``low <= open, close <= high``
        * non-negative volume

    Args:
        df: DataFrame to validate.

    Returns:
        A copy sorted by ``symbol`` then ``date`` with columns in canonical order.

    Raises:
        OHLCVValidationError: If any check fails.
    """
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise OHLCVValidationError(f"missing columns: {missing}")

    out = df[OHLCV_COLUMNS].copy()
    out["date"] = pd.to_datetime(out["date"])
    out["symbol"] = out["symbol"].astype(str)

    if out.duplicated(subset=["symbol", "date"]).any():
        raise OHLCVValidationError("duplicated (symbol, date) rows")
    if out[PRICE_COLUMNS].isna().any().any():
        raise OHLCVValidationError("missing price values")
    bad_range = (out["low"] > out[["open", "close"]].min(axis=1)) | (
        out["high"] < out[["open", "close"]].max(axis=1)
    )
    if bad_range.any():
        raise OHLCVValidationError(f"{int(bad_range.sum())} rows violate low <= open/close <= high")
    if (out["volume"] < 0).any():
        raise OHLCVValidationError("negative volume")

    return out.sort_values(["symbol", "date"]).reset_index(drop=True)
