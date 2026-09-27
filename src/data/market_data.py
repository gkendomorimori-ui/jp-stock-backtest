"""Wide (date x symbol) view of the processed dataset used by universe, strategy and engine."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.data.processing import ProcessedData

#: Bar columns pivoted into wide tables.
WIDE_FIELDS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "adj_factor",
    "adj_high",
    "adj_close",
    "adj_volume",
)


@dataclass
class MarketData:
    """Trading-day-aligned wide tables.

    Every table has index = exchange trading days (from the calendar, not from the bars)
    and the same symbol columns. A missing bar is NaN, so rolling windows over the index
    are windows over FIXED exchange trading days.

    Attributes:
        dates: Exchange trading days.
        fields: Wide tables for :data:`WIDE_FIELDS`.
        market_code: Wide table of master ``market_code`` (None if not listed that day).
        product_category: Wide table of master ``product_category``.
    """

    dates: pd.DatetimeIndex
    fields: dict[str, pd.DataFrame]
    market_code: pd.DataFrame
    product_category: pd.DataFrame

    @classmethod
    def from_processed(cls, data: ProcessedData) -> MarketData:
        """Pivot processed data onto the exchange trading calendar."""
        dates = pd.DatetimeIndex(
            data.calendar.loc[data.calendar["is_trading_day"], "date"].sort_values()
        )
        symbols = sorted(set(data.bars["symbol"]) | set(data.master["symbol"]))
        fields = {
            f: data.bars.pivot(index="date", columns="symbol", values=f).reindex(
                index=dates, columns=symbols
            )
            for f in WIDE_FIELDS
        }
        master = data.master.set_index(["date", "symbol"])
        mkt = master["market_code"].unstack().reindex(index=dates, columns=symbols)
        prod = master["product_category"].unstack().reindex(index=dates, columns=symbols)
        return cls(dates=dates, fields=fields, market_code=mkt, product_category=prod)

    def __getitem__(self, name: str) -> pd.DataFrame:
        """Wide table for a bar field."""
        return self.fields[name]

    def is_listed(self, day: pd.Timestamp, symbol: str) -> bool:
        """Whether ``symbol`` is in the master on ``day``."""
        value = self.market_code.at[day, symbol] if symbol in self.market_code.columns else None
        return value is not None and not pd.isna(value)

    def has_master(self, day: pd.Timestamp) -> bool:
        """Whether any master row exists for ``day`` (i.e. the master was downloaded)."""
        return bool(self.market_code.loc[day].notna().any())
