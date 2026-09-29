"""TSE tick sizes (呼値の単位), point in time.

Two tables exist in the data period. Which one applies to a stock depends on the DATE
(the regime in force that day) and on the stock's TOPIX scale category ON THAT DATE
(J-Quants master ``ScaleCat``, point in time):

* 2015-09-24 .. 2023-06-04: the fine table applies to TOPIX100 constituents
  (TOPIX Core30 + TOPIX Large70). Everything else uses the standard table.
* 2023-06-05 .. 2027-02-28: the fine table applies to TOPIX500 constituents
  (Core30 + Large70 + Mid400); JPX extended the TOPIX100 table to TOPIX Mid400.
* From 2027-03-01 JPX switches to STR-based tables per stock: NOT implemented.

Sources (checked 2026-09-29):

* JPX 呼値の単位 https://www.jpx.co.jp/equities/trading/domestic/07.html (current tables)
* JPX 2023-05-26 "6月5日以降の中流動性銘柄（TOPIX Mid400）における呼値の単位について"
  https://www.jpx.co.jp/news/1030/20230526-01.html
* JPX "TOPIX100構成銘柄の呼値の単位が変わります" (effective 2015-09-24)
  https://www.jpx.co.jp/news/1030/nlsgeu0000016dib-att/Japanese1.pdf

Known gap: JPX occasionally removes an individual stock from the fine table by notice
(e.g. 4088 from 2026-05-12, 6201 from 2026-05-18, both outside the development period).
Such notices are not modelled; the scale category of the day is used as is.

An unknown category (missing ``ScaleCat``, an unexpected value, or a date outside the
implemented regimes) is never guessed: :func:`tick_class` returns None and the caller must
record it as needing review.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

FINE = "fine"
STANDARD = "standard"

#: (upper bound inclusive, tick). Bands above the last bound are not implemented.
FINE_TABLE: tuple[tuple[float, float], ...] = (
    (1_000, 0.1),
    (3_000, 0.5),
    (10_000, 1),
    (30_000, 5),
    (100_000, 10),
    (300_000, 50),
    (1_000_000, 100),
    (3_000_000, 500),
    (10_000_000, 1_000),
    (30_000_000, 5_000),
    (50_000_000, 10_000),
)
STANDARD_TABLE: tuple[tuple[float, float], ...] = (
    (3_000, 1),
    (5_000, 5),
    (30_000, 10),
    (50_000, 50),
    (300_000, 100),
    (500_000, 500),
    (3_000_000, 1_000),
    (5_000_000, 5_000),
    (30_000_000, 10_000),
    (50_000_000, 50_000),
)
TABLES: dict[str, tuple[tuple[float, float], ...]] = {FINE: FINE_TABLE, STANDARD: STANDARD_TABLE}

#: Every ScaleCat value J-Quants documents.
KNOWN_SCALE_CATEGORIES: frozenset[str] = frozenset(
    {"TOPIX Core30", "TOPIX Large70", "TOPIX Mid400", "TOPIX Small 1", "TOPIX Small 2", "-"}
)


@dataclass(frozen=True)
class TickRegime:
    """A period in which one set of scale categories uses the fine table."""

    start: pd.Timestamp
    end: pd.Timestamp  # inclusive
    fine_categories: frozenset[str]
    label: str


REGIMES: tuple[TickRegime, ...] = (
    TickRegime(
        pd.Timestamp("2015-09-24"),
        pd.Timestamp("2023-06-04"),
        frozenset({"TOPIX Core30", "TOPIX Large70"}),
        "TOPIX100 fine table (2015-09-24 .. 2023-06-04)",
    ),
    TickRegime(
        pd.Timestamp("2023-06-05"),
        pd.Timestamp("2027-02-28"),
        frozenset({"TOPIX Core30", "TOPIX Large70", "TOPIX Mid400"}),
        "TOPIX500 fine table (2023-06-05 .. 2027-02-28)",
    ),
)

_REL = 1e-9


def regime_for(day: pd.Timestamp) -> TickRegime | None:
    """The regime in force on ``day``; None outside the implemented regimes."""
    for r in REGIMES:
        if r.start <= day <= r.end:
            return r
    return None


def tick_class(day: pd.Timestamp, scale_category: object) -> str | None:
    """``"fine"`` / ``"standard"`` for a stock on ``day``; None when it cannot be determined."""
    regime = regime_for(pd.Timestamp(day))
    if regime is None or not isinstance(scale_category, str):
        return None
    if scale_category not in KNOWN_SCALE_CATEGORIES:
        return None
    return FINE if scale_category in regime.fine_categories else STANDARD


def tick_size(price: float, cls: str) -> float:
    """Tick of the band containing ``price`` (bands are "<= upper bound")."""
    if not price > 0:
        raise ValueError(f"price must be positive: {price}")
    for bound, tick in TABLES[cls]:
        if price <= bound * (1 + _REL):
            return float(tick)
    raise ValueError(f"price {price} is above the implemented tick table")


def _clean(k: float, tick: float) -> float:
    return round(k * tick, 6)


def ceil_to_tick(price: float, cls: str) -> float:
    """Lowest valid price >= ``price``."""
    t = tick_size(price, cls)
    q = price / t
    k = math.ceil(q - _REL * max(1.0, abs(q)))
    return _clean(k, t)


def floor_to_tick(price: float, cls: str) -> float:
    """Highest valid price <= ``price``."""
    t = tick_size(price, cls)
    q = price / t
    k = math.floor(q + _REL * max(1.0, abs(q)))
    return _clean(k, t)


def is_on_tick(price: float, cls: str) -> bool:
    """Whether ``price`` is a valid price under table ``cls``."""
    return abs(floor_to_tick(price, cls) - price) <= 1e-6 * max(1.0, price)
