"""Point-in-time TSE tick sizes (src/backtest/ticks.py)."""

import pandas as pd
import pytest

from src.backtest.ticks import (
    FINE,
    STANDARD,
    ceil_to_tick,
    floor_to_tick,
    is_on_tick,
    tick_class,
    tick_size,
)

BEFORE = pd.Timestamp("2023-06-02")  # last trading day before the TOPIX500 extension
AFTER = pd.Timestamp("2023-06-05")


# ------------------------------------------------------------------ regime / class


@pytest.mark.parametrize(
    ("day", "cat", "expected"),
    [
        (BEFORE, "TOPIX Core30", FINE),
        (BEFORE, "TOPIX Large70", FINE),
        (BEFORE, "TOPIX Mid400", STANDARD),  # TOPIX100 only before 2023-06-05
        (AFTER, "TOPIX Mid400", FINE),  # TOPIX500 from 2023-06-05
        (AFTER, "TOPIX Core30", FINE),
        (BEFORE, "TOPIX Small 1", STANDARD),
        (AFTER, "TOPIX Small 1", STANDARD),
        (AFTER, "TOPIX Small 2", STANDARD),
        (AFTER, "-", STANDARD),
        (pd.Timestamp("2021-09-29"), "TOPIX Large70", FINE),
        (pd.Timestamp("2027-02-26"), "TOPIX Mid400", FINE),
    ],
)
def test_tick_class_point_in_time(day: pd.Timestamp, cat: str, expected: str) -> None:
    assert tick_class(day, cat) == expected


@pytest.mark.parametrize("cat", [None, float("nan"), "", "TOPIX Small", "Mid400"])
def test_unknown_category_is_not_guessed(cat: object) -> None:
    assert tick_class(AFTER, cat) is None


@pytest.mark.parametrize("day", ["2015-09-23", "2027-03-01"])
def test_dates_outside_implemented_regimes_are_unknown(day: str) -> None:
    assert tick_class(pd.Timestamp(day), "TOPIX Core30") is None


# ------------------------------------------------------------------ tables


@pytest.mark.parametrize(
    ("price", "fine", "standard"),
    [
        (1, 0.1, 1),
        (1_000, 0.1, 1),
        (1_000.5, 0.5, 1),
        (3_000, 0.5, 1),
        (3_001, 1, 5),
        (5_000, 1, 5),
        (5_010, 1, 10),
        (10_000, 1, 10),
        (10_005, 5, 10),
        (30_000, 5, 10),
        (30_050, 10, 50),
        (50_000, 10, 50),
        (50_100, 10, 100),
        (100_000, 10, 100),
        (100_100, 50, 100),
        (300_000, 50, 100),
        (300_500, 100, 500),
    ],
)
def test_tables_match_jpx(price: float, fine: float, standard: float) -> None:
    """JPX 呼値の単位 (checked 2026-09-29)."""
    assert tick_size(price, FINE) == fine
    assert tick_size(price, STANDARD) == standard


def test_price_above_the_table_is_refused() -> None:
    with pytest.raises(ValueError):
        tick_size(60_000_000, STANDARD)


# ------------------------------------------------------------------ rounding across bands


@pytest.mark.parametrize(
    ("price", "cls", "up", "down"),
    [
        # fine table, 1,000-yen boundary (0.1 below, 0.5 above)
        (999.95, FINE, 1000.0, 999.9),
        (1000.01, FINE, 1000.5, 1000.0),
        (1000.3, FINE, 1000.5, 1000.0),
        # fine table, 3,000-yen boundary (0.5 below, 1 above)
        (2999.7, FINE, 3000.0, 2999.5),
        (3000.2, FINE, 3001.0, 3000.0),
        # standard table, 3,000-yen boundary (1 below, 5 above)
        (2999.5, STANDARD, 3000.0, 2999.0),
        (3000.2, STANDARD, 3005.0, 3000.0),
        (3002.0, STANDARD, 3005.0, 3000.0),
        # standard table, 5,000-yen boundary (5 below, 10 above)
        (4998.0, STANDARD, 5000.0, 4995.0),
        (5004.0, STANDARD, 5010.0, 5000.0),
        # low-priced stocks: one tick is a large share of the price
        (6.6066, STANDARD, 7.0, 6.0),
        (5.7057, STANDARD, 6.0, 5.0),
        (4.75, STANDARD, 5.0, 4.0),
        (4.75, FINE, 4.8, 4.7),
        # already on the grid (and float noise around it) stays put
        (1113.0, STANDARD, 1113.0, 1113.0),
        (1112.9999999999, STANDARD, 1113.0, 1113.0),
        (1113.0000000001, STANDARD, 1113.0, 1113.0),
        (1000.0000000001, FINE, 1000.0, 1000.0),
    ],
)
def test_ceil_and_floor(price: float, cls: str, up: float, down: float) -> None:
    assert ceil_to_tick(price, cls) == pytest.approx(up)
    assert floor_to_tick(price, cls) == pytest.approx(down)
    assert is_on_tick(ceil_to_tick(price, cls), cls)
    assert is_on_tick(floor_to_tick(price, cls), cls)


def test_is_on_tick() -> None:
    assert is_on_tick(999.9, FINE) and not is_on_tick(999.95, FINE)
    assert is_on_tick(1000.5, FINE) and not is_on_tick(1000.1, FINE)
    assert is_on_tick(3005, STANDARD) and not is_on_tick(3001, STANDARD)
    assert not is_on_tick(1000.5, STANDARD)
