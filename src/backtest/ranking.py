"""Order of buy candidates at the close of the signal day (base rule and sensitivity B).

Every ranking sorts the SAME candidate set (eligible signals of the day, excluding symbols
already held); held-symbol exclusion, budget and slot checks happen in the engine exactly as
for the base rule. Ties are broken by symbol code ascending.

* :class:`VolumeRatioRanking` -- base rule: volume ratio (the signal score) descending.
* :class:`TurnoverRanking`    -- sensitivity B-1: mean turnover of T-20 .. T-1 descending
  (the same window as the liquidity filter).
* :class:`RandomRanking`      -- sensitivity B-2: a random order that changes every day and is
  reproducible: key = SHA-256 of ``"<seed>|<YYYY-MM-DD>|<symbol>"`` (UTF-8), ascending. It does
  not depend on Python's ``hash()``, on row order or on the environment.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol

import pandas as pd


class Ranking(Protocol):
    """Sort key for one candidate."""

    name: str

    def sort_key(self, day: pd.Timestamp, symbol: str, score: float) -> tuple[object, str]:
        """Smaller keys are bought first."""
        ...

    def value(self, day: pd.Timestamp, symbol: str, score: float) -> object:
        """The ranking value written to orders.csv (``rank_value``)."""
        ...


class VolumeRatioRanking:
    """Base rule: volume ratio (signal score) descending."""

    name = "volume_ratio"

    def sort_key(self, day: pd.Timestamp, symbol: str, score: float) -> tuple[object, str]:
        """(-score, symbol)."""
        return (-score, symbol)

    def value(self, day: pd.Timestamp, symbol: str, score: float) -> object:
        """The score itself."""
        return score


class TurnoverRanking:
    """Mean turnover over T-20 .. T-1, descending (``avg_turnover`` is date x symbol)."""

    name = "avg_turnover"

    def __init__(self, avg_turnover: pd.DataFrame) -> None:
        """Store the date x symbol table of the mean turnover of the prior window."""
        self.avg = avg_turnover

    def value(self, day: pd.Timestamp, symbol: str, score: float) -> object:
        """Mean turnover of the prior window (JPY)."""
        v = self.avg.at[day, symbol]
        if pd.isna(v):
            raise ValueError(f"mean turnover missing for eligible candidate {symbol} on {day}")
        return float(v)

    def sort_key(self, day: pd.Timestamp, symbol: str, score: float) -> tuple[object, str]:
        """(-mean turnover, symbol)."""
        v = self.value(day, symbol, score)
        assert isinstance(v, float) and not math.isnan(v)
        return (-v, symbol)


def random_key(seed: int, day: pd.Timestamp, symbol: str) -> str:
    """Hex SHA-256 of ``"<seed>|<YYYY-MM-DD>|<symbol>"``."""
    text = f"{int(seed)}|{pd.Timestamp(day):%Y-%m-%d}|{symbol}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RandomRanking:
    """Reproducible random order that changes every day (seed fixed per run)."""

    name = "random"

    def __init__(self, seed: int) -> None:
        """Store the seed (0-19 in the pre-registered analysis)."""
        self.seed = int(seed)

    def sort_key(self, day: pd.Timestamp, symbol: str, score: float) -> tuple[object, str]:
        """(hash key, symbol)."""
        return (random_key(self.seed, day, symbol), symbol)

    def value(self, day: pd.Timestamp, symbol: str, score: float) -> object:
        """The hash key."""
        return random_key(self.seed, day, symbol)
