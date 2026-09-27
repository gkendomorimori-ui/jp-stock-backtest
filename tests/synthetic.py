"""Synthetic J-Quants-shaped data for tests (goes through the real processing code)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from src.data.market_data import MarketData
from src.data.processing import ProcessedData, bars_from_rows, master_from_rows


def weekdays(start: date, n: int) -> list[date]:
    """``n`` consecutive weekdays from ``start``."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def flat(n: int, price: float, volume: float = 1_000_000) -> dict[str, list[Any]]:
    """A flat series: O=H=L=C=price, constant volume."""
    return {
        "open": [price] * n,
        "high": [price] * n,
        "low": [price] * n,
        "close": [price] * n,
        "volume": [volume] * n,
    }


def make_data(
    days: list[date],
    series: dict[str, dict[str, list[Any]]],
    *,
    market: dict[str, str] | None = None,
    prodcat: dict[str, str] | None = None,
) -> ProcessedData:
    """Build processed data.

    ``series[symbol]`` has lists (len == len(days)) for open/high/low/close/volume and
    optionally ``adj_factor``, ``turnover`` and ``listed`` (bool). ``None`` = no trade.
    Turnover defaults to close x volume.
    """
    bar_rows, master_rows = raw_rows(days, series, market=market, prodcat=prodcat)
    calendar = pd.DataFrame({"date": pd.to_datetime(days), "hol_div": "1", "is_trading_day": True})
    return ProcessedData(
        bars=bars_from_rows(bar_rows), master=master_from_rows(master_rows), calendar=calendar
    )


def raw_rows(
    days: list[date],
    series: dict[str, dict[str, list[Any]]],
    *,
    market: dict[str, str] | None = None,
    prodcat: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """J-Quants-shaped (bar rows, master rows) for :func:`make_data`."""
    market = market or {}
    prodcat = prodcat or {}
    bar_rows: list[dict[str, Any]] = []
    master_rows: list[dict[str, Any]] = []
    for sym, s in series.items():
        listed = s.get("listed", [True] * len(days))
        for i, d in enumerate(days):
            if not listed[i]:
                continue
            c, v = s["close"][i], s["volume"][i]
            turnover = s.get("turnover", [None] * len(days))[i]
            if turnover is None and c is not None and v is not None:
                turnover = c * v
            bar_rows.append(
                {
                    "Date": d.isoformat(),
                    "Code": sym,
                    "O": s["open"][i],
                    "H": s["high"][i],
                    "L": s["low"][i],
                    "C": c,
                    "Vo": v,
                    "Va": turnover,
                    "AdjFactor": s.get("adj_factor", [1.0] * len(days))[i],
                    "AdjC": c,
                }
            )
            master_rows.append(
                {
                    "Date": d.isoformat(),
                    "Code": sym,
                    "CoName": sym,
                    "Mkt": market.get(sym, "0111"),
                    "MktNm": "x",
                    "ProdCat": prodcat.get(sym, "011"),
                }
            )
    return bar_rows, master_rows


def make_md(days: list[date], series: dict[str, dict[str, list[Any]]], **kw: Any) -> MarketData:
    """MarketData from :func:`make_data`."""
    return MarketData.from_processed(make_data(days, series, **kw))
