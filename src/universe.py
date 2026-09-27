"""Point-in-time buy eligibility (config/universe.yaml).

All tables are date x symbol booleans aligned to the exchange trading calendar. They are
used ONLY to decide new entries; they never remove or close a held position.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from src.data.market_data import MarketData

#: Fields that must be valid on every day of the fixed window T-w .. T-1.
WINDOW_FIELDS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "adj_high",
    "adj_volume",
)
#: Fields that must be valid on the signal day T itself.
SIGNAL_DAY_FIELDS: tuple[str, ...] = ("close", "adj_close", "adj_volume")
#: Fields that must be strictly positive when valid.
POSITIVE_FIELDS: frozenset[str] = frozenset({"volume", "turnover", "adj_volume"})


@dataclass(frozen=True)
class UniverseRules:
    """Rules loaded from ``config/universe.yaml``."""

    market_codes: frozenset[str]
    product_categories: frozenset[str]
    common_stock_code_suffix: str
    min_avg_turnover: float
    window_days: int

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> UniverseRules:
        """Build from the parsed YAML (top-level ``universe`` mapping)."""
        u = raw["universe"]
        markets = {m["code"] for m in u["markets"]["current"]} | {
            m["code"] for m in u["markets"]["legacy"]
        }
        f = u["filters"]
        if f.get("turnover_window_includes_signal_day"):
            raise ValueError("turnover window must exclude the signal day")
        if f.get("history_window") != "fixed_exchange_calendar":
            raise ValueError("only history_window: fixed_exchange_calendar is supported")
        if int(f["turnover_window_days"]) != int(f["min_history_days"]):
            raise ValueError("turnover window and history window must be the same length")
        return cls(
            market_codes=frozenset(str(c) for c in markets),
            product_categories=frozenset(str(c) for c in u["product_category"]["include"]),
            common_stock_code_suffix=str(u["common_stock_code_suffix"]),
            min_avg_turnover=float(f["min_avg_turnover"]),
            window_days=int(f["min_history_days"]),
        )


def security_eligible(md: MarketData, rules: UniverseRules) -> pd.DataFrame:
    """Listed on T in a target market, ProdCat included, and common stock by code suffix."""
    in_market = md.market_code.isin(rules.market_codes)
    in_prod = md.product_category.isin(rules.product_categories)
    suffix_ok = pd.Series(
        [len(s) == 5 and s[4] == rules.common_stock_code_suffix for s in md.market_code.columns],
        index=md.market_code.columns,
    )
    return in_market & in_prod & suffix_ok


def _valid(md: MarketData, field: str) -> pd.DataFrame:
    table = md[field]
    ok = table.notna()
    if field in POSITIVE_FIELDS:
        ok &= table > 0
    return ok


def data_condition(md: MarketData, window: int) -> pd.DataFrame:
    """Every field valid on ALL of the fixed trading days T-window .. T-1, and T valid.

    Missing days are not skipped: one invalid day inside the window makes T ineligible.
    """
    ok = pd.DataFrame(True, index=md.dates, columns=md["close"].columns)
    for field in WINDOW_FIELDS:
        full = _valid(md, field).astype(int).rolling(window, min_periods=window).sum() == window
        ok &= full.shift(1, fill_value=False)
    for field in SIGNAL_DAY_FIELDS:
        ok &= _valid(md, field)
    return ok


def liquidity(md: MarketData, rules: UniverseRules) -> pd.DataFrame:
    """Mean turnover over T-window .. T-1 is at least ``min_avg_turnover``."""
    w = rules.window_days
    avg = md["turnover"].rolling(w, min_periods=w).mean().shift(1)
    return avg >= rules.min_avg_turnover


def eligibility(md: MarketData, rules: UniverseRules) -> pd.DataFrame:
    """Combined buy eligibility for each (T, symbol)."""
    return (
        security_eligible(md, rules) & data_condition(md, rules.window_days) & liquidity(md, rules)
    )
