"""high_price_breakout (strategies/high_price_breakout.md, v0.2.0) -- entry signals."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.data.market_data import MarketData
from src.strategies.base import Strategy


class HighPriceBreakout(Strategy):
    """Close breaks the prior N-day high on at least ``volume_multiplier`` x average volume.

    Uses ADJUSTED high/close/volume only, so splits do not create signals. Windows are
    T-N .. T-1 (T excluded) over fixed exchange trading days; a NaN inside the window
    yields no signal (the universe data condition excludes such cases anyway).

    Score = volume ratio ``adj_volume[T] / mean(adj_volume[T-N .. T-1])``.
    """

    name = "high_price_breakout"
    version = "0.2.0"

    def generate_signals(self, md: MarketData) -> pd.DataFrame:
        """See class docstring."""
        n = int(self.params["lookback_days"])
        mult = float(self.params["volume_multiplier"])
        prior_high = md["adj_high"].rolling(n, min_periods=n).max().shift(1)
        prior_vol = md["adj_volume"].rolling(n, min_periods=n).mean().shift(1)
        ratio = md["adj_volume"] / prior_vol
        signal = (md["adj_close"] > prior_high) & (ratio >= mult)
        return ratio.where(signal, np.nan)
