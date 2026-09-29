"""Build the processed (normalized) dataset from raw J-Quants files.

Output: ``<processed_root>/jquants/{bars,master,calendar}.parquet`` and ``manifest.json``.

Column meanings of ``bars.parquet`` (see docs/DATA_SOURCES.md):

* ``open, high, low, close`` -- ACTUAL traded prices on that date (J-Quants ``O,H,L,C``).
  NaN when there was no trade (suspension / no execution).
* ``volume`` -- actual shares traded (``Vo``). ``turnover`` -- traded value in JPY (``Va``).
* ``adj_factor`` -- J-Quants ``AdjFactor`` of that date. It is set on the ex-date of a
  split / reverse split and is 1.0 otherwise. Example: 1:2 split -> 0.5 on the ex-date.
  Meaning: ``actual price on the ex-date ~= actual price before x adj_factor``.
* ``adj_open .. adj_close, adj_volume`` -- RECOMPUTED here from actual values and
  ``adj_factor`` (the API's own ``Adj*`` columns are not used, because their basis depends
  on when each file was downloaded):

      C(t) = product of adj_factor(s) for all dates s with t < s <= last date of the dataset
      adj_price(t)  = actual_price(t) x C(t)
      adj_volume(t) = actual_volume(t) / C(t)

  Direction: past values are rescaled to the share basis of the LAST date in the dataset,
  so on the last date adjusted == actual. A factor on the ex-date D affects every date
  before D, not D itself. Ratios inside any window (e.g. close vs. 20-day high) do not
  depend on which last date is used, so signals are unaffected by later splits.
* ``api_adj_close`` -- the API's ``AdjC`` kept for reference only.
* ``upper_limit`` / ``lower_limit`` -- J-Quants ``UL`` / ``LL`` ("1" if the day's HIGH / LOW
  reached the daily price limit at least once, "0" otherwise; NOT judged on the close).
  Stored as 1.0 / 0.0; a missing or unexpected value stays NaN (never filled with 0).
  Used only to reproduce executions (execution model v2), never for signals.

Master: ``scale_category`` is J-Quants ``ScaleCat`` of that date (point in time):
``TOPIX Core30``, ``TOPIX Large70``, ``TOPIX Mid400``, ``TOPIX Small 1``, ``TOPIX Small 2``
or ``-``. Missing stays missing (None); it decides the tick-size table (src/backtest/ticks.py).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.data.providers.jquants import TRADING_HOLDIV, read_raw

BAR_FIELD_MAP: dict[str, str] = {
    "O": "open",
    "H": "high",
    "L": "low",
    "C": "close",
    "Vo": "volume",
    "Va": "turnover",
    "AdjFactor": "adj_factor",
    "AdjC": "api_adj_close",
    "UL": "upper_limit",
    "LL": "lower_limit",
}
#: Limit flags: only "0" / "1" (or 0 / 1) are accepted; anything else becomes NaN.
LIMIT_FLAG_COLUMNS: list[str] = ["upper_limit", "lower_limit"]
PRICE_COLUMNS: list[str] = ["open", "high", "low", "close"]
BAR_COLUMNS: list[str] = [
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "adj_factor",
    "adj_open",
    "adj_high",
    "adj_low",
    "adj_close",
    "adj_volume",
    "api_adj_close",
    "upper_limit",
    "lower_limit",
]
MASTER_COLUMNS: list[str] = [
    "date",
    "symbol",
    "name",
    "market_code",
    "market_name",
    "product_category",
    "scale_category",
]


class ProcessingError(Exception):
    """Raw data is missing or inconsistent."""


@dataclass
class ProcessedData:
    """In-memory processed dataset. ``topix`` is None when it was not downloaded."""

    bars: pd.DataFrame
    master: pd.DataFrame
    calendar: pd.DataFrame
    topix: pd.DataFrame | None = None


def load_calendar(path: Path) -> pd.DataFrame:
    """Raw calendar file -> ``date, hol_div, is_trading_day``."""
    rows = read_raw(path)
    cal = pd.DataFrame(
        {
            "date": pd.to_datetime([r["Date"] for r in rows]),
            "hol_div": [str(r.get("HolDiv")) for r in rows],
        }
    )
    cal["is_trading_day"] = cal["hol_div"].isin(TRADING_HOLDIV)
    return cal.sort_values("date").reset_index(drop=True)


def _load_day_files(raw_dir: Path, kind: str, days: list[date]) -> list[dict[str, Any]]:
    missing = [d for d in days if not (raw_dir / kind / f"{d.isoformat()}.json.gz").exists()]
    if missing:
        shown = ", ".join(d.isoformat() for d in missing[:10])
        raise ProcessingError(
            f"{kind}: {len(missing)} trading day(s) not downloaded: {shown}"
            + (" ..." if len(missing) > 10 else "")
            + " -- run scripts/download_data.py again"
        )
    rows: list[dict[str, Any]] = []
    for d in days:
        day_rows = read_raw(raw_dir / kind / f"{d.isoformat()}.json.gz")
        bad = [r for r in day_rows if str(r.get("Date")) != d.isoformat()]
        if bad:
            raise ProcessingError(f"{kind}/{d}: {len(bad)} row(s) with a different Date")
        rows.extend(day_rows)
    return rows


def bars_from_rows(rows: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Raw daily-bar rows -> bars with actual values and recomputed adjusted values."""
    raw = pd.DataFrame(list(rows))
    if raw.empty:
        raise ProcessingError("no daily bars")
    df = pd.DataFrame({"date": pd.to_datetime(raw["Date"]), "symbol": raw["Code"].astype(str)})
    for src, dst in BAR_FIELD_MAP.items():
        if dst in LIMIT_FLAG_COLUMNS:
            df[dst] = _limit_flag(raw[src]) if src in raw else np.nan
        else:
            df[dst] = pd.to_numeric(raw[src], errors="coerce") if src in raw else np.nan
    if df.duplicated(["symbol", "date"]).any():
        raise ProcessingError("duplicated (symbol, date) rows in daily bars")
    df["adj_factor"] = df["adj_factor"].fillna(1.0)
    if (df["adj_factor"] <= 0).any():
        raise ProcessingError("non-positive adj_factor")
    return add_adjusted(df)


def _limit_flag(values: pd.Series) -> pd.Series:
    """``"0"/"1"`` (or 0/1) -> 0.0/1.0; anything else (None, "", "x") -> NaN, never 0."""

    def one(v: Any) -> float:
        nan = float("nan")
        if v is None or isinstance(v, bool):
            return nan
        if isinstance(v, str):
            return {"0": 0.0, "1": 1.0}.get(v.strip(), nan)
        try:
            f = float(v)
        except (TypeError, ValueError):
            return nan
        return f if f in (0.0, 1.0) else nan

    return pd.Series([one(v) for v in values], index=values.index, dtype="float64")


def add_adjusted(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``adj_*`` columns (see module docstring for the definition)."""
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
    # C(t) = product of factors strictly after t: reverse cumulative product, shifted.
    log_f = np.log(df["adj_factor"].to_numpy(dtype=float))
    rev_cum = df.assign(_lf=log_f).iloc[::-1].groupby("symbol")["_lf"].cumsum().iloc[::-1]
    cum_after = np.exp(rev_cum.to_numpy() - log_f)
    for col in PRICE_COLUMNS:
        df[f"adj_{col}"] = df[col] * cum_after
    df["adj_volume"] = df["volume"] / cum_after
    return df[BAR_COLUMNS]


def master_from_rows(rows: Iterable[dict[str, Any]]) -> pd.DataFrame:
    """Raw master rows -> ``MASTER_COLUMNS``."""
    raw = pd.DataFrame(list(rows))
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(raw["Date"]),
            "symbol": raw["Code"].astype(str),
            "name": raw.get("CoName", pd.Series([None] * len(raw))),
            "market_code": raw["Mkt"].astype(str),
            "market_name": raw.get("MktNm", pd.Series([None] * len(raw))),
            "product_category": raw["ProdCat"].astype(str),
            "scale_category": raw["ScaleCat"].where(raw["ScaleCat"].notna(), None)
            if "ScaleCat" in raw
            else pd.Series([None] * len(raw), dtype="object"),
        }
    )
    if df.duplicated(["symbol", "date"]).any():
        raise ProcessingError("duplicated (symbol, date) rows in master")
    return df.sort_values(["date", "symbol"]).reset_index(drop=True)


def build_processed(raw_root: Path, calendar_file: Path, start: date, end: date) -> ProcessedData:
    """Build bars/master/calendar for all trading days in ``start..end``.

    Raises:
        ProcessingError: If any trading day's bars or master file is missing.
    """
    raw_dir = raw_root / "jquants"
    cal = load_calendar(calendar_file)
    cal = cal[(cal["date"] >= pd.Timestamp(start)) & (cal["date"] <= pd.Timestamp(end))]
    days = [d.date() for d in cal.loc[cal["is_trading_day"], "date"]]
    if not days:
        raise ProcessingError(f"no trading days in {start}..{end}")
    if pd.Timestamp(end) > cal["date"].max() or pd.Timestamp(start) < cal["date"].min():
        raise ProcessingError("calendar file does not cover the requested range")
    bars = bars_from_rows(_load_day_files(raw_dir, "bars_daily", days))
    master = master_from_rows(_load_day_files(raw_dir, "master", days))
    topix = load_topix(raw_dir, start, end)
    return ProcessedData(bars=bars, master=master, calendar=cal.reset_index(drop=True), topix=topix)


def load_topix(raw_dir: Path, start: date, end: date) -> pd.DataFrame | None:
    """TOPIX (price index) ``date, open, high, low, close`` for ``start..end``, if downloaded.

    Uses a raw ``topix/<from>_<to>.json.gz`` file whose range covers ``start..end``.
    """
    for path in sorted((raw_dir / "topix").glob("*_*.json.gz")):
        a, b = path.name.removesuffix(".json.gz").split("_")
        if date.fromisoformat(a) <= start and end <= date.fromisoformat(b):
            rows = read_raw(path)
            df = pd.DataFrame(
                {
                    "date": pd.to_datetime([r["Date"] for r in rows]),
                    "open": [r.get("O") for r in rows],
                    "high": [r.get("H") for r in rows],
                    "low": [r.get("L") for r in rows],
                    "close": [r.get("C") for r in rows],
                }
            )
            df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]
            if df["date"].duplicated().any():
                raise ProcessingError("duplicated TOPIX dates")
            return df.sort_values("date").reset_index(drop=True)
    return None


def save_processed(data: ProcessedData, out_dir: Path, extra: dict[str, Any] | None = None) -> Path:
    """Write parquet files and ``manifest.json`` to ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    data.bars.to_parquet(out_dir / "bars.parquet", index=False)
    data.master.to_parquet(out_dir / "master.parquet", index=False)
    data.calendar.to_parquet(out_dir / "calendar.parquet", index=False)
    topix_file = out_dir / "topix.parquet"
    if data.topix is not None:
        data.topix.to_parquet(topix_file, index=False)
    elif topix_file.exists():
        topix_file.unlink()  # never leave a stale TOPIX from another range
    traded = data.bars.dropna(subset=PRICE_COLUMNS)
    ohlc_violations = int(
        (
            (traded["low"] > traded[["open", "close"]].min(axis=1))
            | (traded["high"] < traded[["open", "close"]].max(axis=1))
        ).sum()
    )
    manifest = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "first_date": data.bars["date"].min().date().isoformat(),
        "last_date": data.bars["date"].max().date().isoformat(),
        "trading_days": int(data.calendar["is_trading_day"].sum()),
        "bar_rows": len(data.bars),
        "bar_rows_without_trade": int(data.bars["close"].isna().sum()),
        "master_rows": len(data.master),
        "symbols": int(data.bars["symbol"].nunique()),
        "split_events": int((data.bars["adj_factor"] != 1.0).sum()),
        "ohlc_inconsistent_rows": ohlc_violations,
        "adjusted_basis": "recomputed from actual values and adj_factor; basis = last_date",
        "limit_flags": limit_flag_summary(data.bars),
        "scale_category_missing_rows": int(data.master["scale_category"].isna().sum())
        if "scale_category" in data.master
        else None,
        "topix_rows": None if data.topix is None else len(data.topix),
        "topix_dates_match_trading_days": None
        if data.topix is None
        else list(data.topix["date"])
        == list(data.calendar.loc[data.calendar["is_trading_day"], "date"]),
    } | (extra or {})
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out_dir


def limit_flag_summary(bars: pd.DataFrame) -> dict[str, Any]:
    """Counts of UL/LL values; ``missing_on_traded_rows`` must be 0 for execution model v2."""
    if not set(LIMIT_FLAG_COLUMNS) <= set(bars.columns):
        return {"present": False}
    traded = bars.dropna(subset=PRICE_COLUMNS)
    out: dict[str, Any] = {"present": True}
    for col in LIMIT_FLAG_COLUMNS:
        out[col] = {
            "ones": int((bars[col] == 1.0).sum()),
            "zeros": int((bars[col] == 0.0).sum()),
            "missing": int(bars[col].isna().sum()),
            "missing_on_traded_rows": int(traded[col].isna().sum()),
        }
    return out


def load_processed(out_dir: Path) -> ProcessedData:
    """Load a dataset written by :func:`save_processed`."""
    for name in ("bars", "master", "calendar"):
        if not (out_dir / f"{name}.parquet").exists():
            raise ProcessingError(
                f"{out_dir / name}.parquet not found -- run scripts/process_data.py"
            )
    topix_file = out_dir / "topix.parquet"
    return ProcessedData(
        bars=pd.read_parquet(out_dir / "bars.parquet"),
        master=pd.read_parquet(out_dir / "master.parquet"),
        calendar=pd.read_parquet(out_dir / "calendar.parquet"),
        topix=pd.read_parquet(topix_file) if topix_file.exists() else None,
    )
