"""Additional aggregates of ONE saved run (no re-run, no change of conditions).

Partial aggregates (by year, market, price band, month) describe that run's trades and
equity. They are NOT the performance of a strategy re-run on that subset only.

Definitions:

* Year: calendar year. Return = last equity of the year / last equity of the previous year
  (initial capital for the first year) - 1. Capital is NOT reset each year. The first and
  last years of the period are partial and flagged. Max drawdown within the year starts
  from the previous year-end equity. Trades are counted by EXIT date (realized PnL is booked
  on exit); entries by entry date are shown separately.
* Drawdown period: the maximum drawdown of the equity curve; peak date, trough date and the
  first later date whose equity is back at the peak (None if not recovered in the period).
* Market / price band: of the SIGNAL day (the close that created the order): the master's
  market code and the ACTUAL close on that day. Price bands are fixed in advance:
  <10, 10-100, 100-500, 500-1,000, 1,000-3,000, >=3,000 yen (lower bound inclusive).
* Month: month-end equity / cash / position value; invested ratio = position value /
  equity (month-end, and the mean of daily values); order outcomes counted by SIGNAL month.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PRICE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("<10", 0.0, 10.0),
    ("10-100", 10.0, 100.0),
    ("100-500", 100.0, 500.0),
    ("500-1000", 500.0, 1000.0),
    ("1000-3000", 1000.0, 3000.0),
    (">=3000", 3000.0, math.inf),
)
RETURN_BINS: tuple[float, ...] = (-math.inf, -0.10, -0.05, -0.02, 0.0, 0.02, 0.05, 0.10, math.inf)
MARKET_NAMES: dict[str, str] = {
    "0101": "東証一部",
    "0102": "東証二部",
    "0104": "マザーズ",
    "0106": "JASDAQ スタンダード",
    "0107": "JASDAQ グロース",
    "0111": "プライム",
    "0112": "スタンダード",
    "0113": "グロース",
}
NOTE = (
    "Aggregates of one run's trades and equity. A subset total is NOT the result of a strategy "
    "run on that subset only. Capital is not reset per year."
)


def price_band(price: float) -> str | None:
    """Fixed band label of an actual price."""
    if price is None or math.isnan(price):
        return None
    for label, lo, hi in PRICE_BANDS:
        if lo <= price < hi:
            return label
    return None


def _group(df: pd.DataFrame, by: str) -> pd.DataFrame:
    g = df.groupby(by, dropna=False)
    out = pd.DataFrame(
        {
            "trades": g.size(),
            "realized_pnl": g["pnl"].sum(),
            "mean_pnl": g["pnl"].mean(),
            "wins": g["pnl"].apply(lambda x: int((x > 0).sum())),
            "losses": g["pnl"].apply(lambda x: int((x <= 0).sum())),
            "mean_return_pct": g["return_pct"].mean(),
        }
    )
    out["win_rate"] = out["wins"] / out["trades"]
    return out.reset_index()


def _max_dd(values: pd.Series) -> float:
    peak = values.cummax()
    return float((values / peak - 1.0).min())


def drawdown_period(equity: pd.Series) -> dict[str, Any]:
    """Peak, trough and recovery of the maximum drawdown (index = dates)."""
    peak = equity.cummax()
    dd = equity / peak - 1.0
    trough = dd.idxmin()
    peak_value = float(peak.loc[trough])
    peak_date = equity.loc[:trough][equity.loc[:trough] >= peak_value - 1e-9].index[0]
    after = equity.loc[trough:]
    rec = after[after >= peak_value - 1e-9]
    return {
        "max_drawdown": float(dd.min()),
        "peak_date": str(pd.Timestamp(peak_date).date()),
        "peak_equity": peak_value,
        "trough_date": str(pd.Timestamp(trough).date()),
        "trough_equity": float(equity.loc[trough]),
        "recovered": len(rec) > 0,
        "recovery_date": str(pd.Timestamp(rec.index[0]).date()) if len(rec) else None,
        "days_peak_to_trough": int(len(equity.loc[peak_date:trough]) - 1),
    }


def yearly(equity: pd.Series, trades: pd.DataFrame, initial: float) -> pd.DataFrame:
    """Per calendar year (see module docstring)."""
    rows = []
    years = sorted({d.year for d in equity.index})
    prev_end = initial
    for i, y in enumerate(years):
        e = equity[equity.index.year == y]
        series = pd.concat([pd.Series([prev_end]), e.reset_index(drop=True)])
        ex = trades[pd.to_datetime(trades["exit_date"]).dt.year == y]
        en = trades[pd.to_datetime(trades["entry_date"]).dt.year == y]
        rows.append(
            {
                "year": y,
                "partial": i == 0 or i == len(years) - 1,
                "first_date": str(e.index[0].date()),
                "last_date": str(e.index[-1].date()),
                "trading_days": len(e),
                "start_equity": prev_end,
                "end_equity": float(e.iloc[-1]),
                "return": float(e.iloc[-1] / prev_end - 1.0),
                "max_drawdown_in_year": _max_dd(series),
                "trades_exited": len(ex),
                "realized_pnl_exited": float(ex["pnl"].sum()),
                "trades_entered": len(en),
            }
        )
        prev_end = float(e.iloc[-1])
    return pd.DataFrame(rows)


def monthly(
    eq: pd.DataFrame, trades: pd.DataFrame, orders: pd.DataFrame, initial: float
) -> pd.DataFrame:
    """Per calendar month (see module docstring)."""
    e = eq.copy()
    e["date"] = pd.to_datetime(e["date"])
    e["month"] = e["date"].dt.to_period("M")
    e["invested_ratio"] = e["position_value"] / e["equity"]
    g = e.groupby("month")
    out = pd.DataFrame(
        {
            "month_end_date": g["date"].last().dt.date.astype(str),
            "equity": g["equity"].last(),
            "cash": g["cash"].last(),
            "position_value": g["position_value"].last(),
            "positions": g["positions"].last(),
            "invested_ratio_month_end": g["invested_ratio"].last(),
            "invested_ratio_mean_daily": g["invested_ratio"].mean(),
        }
    )
    prev = out["equity"].shift(1).fillna(initial)
    out["return"] = out["equity"] / prev - 1.0
    t = trades.copy()
    t["entry_month"] = pd.to_datetime(t["entry_date"]).dt.to_period("M")
    t["exit_month"] = pd.to_datetime(t["exit_date"]).dt.to_period("M")
    out["trades_entered"] = t.groupby("entry_month").size().reindex(out.index, fill_value=0)
    out["trades_exited"] = t.groupby("exit_month").size().reindex(out.index, fill_value=0)
    out["realized_pnl_exited"] = (
        t.groupby("exit_month")["pnl"].sum().reindex(out.index, fill_value=0.0)
    )
    o = orders.copy()
    o["signal_month"] = pd.to_datetime(o["signal_date"]).dt.to_period("M")
    for status in (
        "not_placed_unaffordable",
        "cancelled_no_slot",
        "cancelled_insufficient_funds",
        "filled",
    ):
        out[status] = (
            o[o["status"] == status].groupby("signal_month").size().reindex(out.index, fill_value=0)
        )
    out.index = out.index.astype(str)
    return out.reset_index().rename(columns={"index": "month"})


def distribution(trades: pd.DataFrame, n_extremes: int = 5) -> dict[str, Any]:
    """Quantiles and a fixed histogram of return_pct and PnL; best / worst trades."""
    qs = [0.0, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 1.0]
    labels = []
    for lo, hi in zip(RETURN_BINS[:-1], RETURN_BINS[1:], strict=True):
        a = "-inf" if math.isinf(lo) else f"{lo:+.0%}"
        b = "+inf" if math.isinf(hi) else f"{hi:+.0%}"
        labels.append(f"[{a}, {b})")
    bins = pd.cut(trades["return_pct"], bins=list(RETURN_BINS), right=False, labels=labels)
    cols = ["symbol", "entry_date", "exit_date", "exit_reason", "quantity", "pnl", "return_pct"]
    return {
        "return_pct_quantiles": {f"{q:.2f}": float(trades["return_pct"].quantile(q)) for q in qs},
        "pnl_quantiles": {f"{q:.2f}": float(trades["pnl"].quantile(q)) for q in qs},
        "return_pct_histogram": {str(k): int(v) for k, v in bins.value_counts(sort=False).items()},
        "best_trades": trades.nlargest(n_extremes, "pnl")[cols].to_dict("records"),
        "worst_trades": trades.nsmallest(n_extremes, "pnl")[cols].to_dict("records"),
    }


def signal_day_attributes(
    trades: pd.DataFrame, orders: pd.DataFrame, bars: pd.DataFrame, master: pd.DataFrame
) -> pd.DataFrame:
    """Trades with signal date, signal-day market code and actual close."""
    filled = orders[orders["status"] == "filled"][["symbol", "exec_date", "signal_date"]]
    t = trades.merge(
        filled, left_on=["symbol", "entry_date"], right_on=["symbol", "exec_date"], how="left"
    )
    t["signal_date"] = pd.to_datetime(t["signal_date"])
    b = bars[["date", "symbol", "close"]].rename(columns={"close": "signal_close"})
    m = master[["date", "symbol", "market_code"]]
    t = t.merge(b, left_on=["signal_date", "symbol"], right_on=["date", "symbol"], how="left")
    t = t.drop(columns="date").merge(
        m, left_on=["signal_date", "symbol"], right_on=["date", "symbol"], how="left"
    )
    t = t.drop(columns="date")
    t["market_name"] = t["market_code"].map(MARKET_NAMES)
    t["price_band"] = t["signal_close"].map(price_band)
    return t


def analyze(run_dir: Path, bars: pd.DataFrame, master: pd.DataFrame) -> dict[str, Any]:
    """All aggregates; also writes analysis_*.csv into ``run_dir``."""
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    initial = float(summary["execution"]["initial_capital"])
    trades = pd.read_csv(run_dir / "trades.csv", dtype={"symbol": str})
    orders = pd.read_csv(run_dir / "orders.csv", dtype={"symbol": str})
    eq = pd.read_csv(run_dir / "equity_curve.csv")
    trades["entry_date"] = pd.to_datetime(trades["entry_date"])
    trades["exit_date"] = pd.to_datetime(trades["exit_date"])
    orders["exec_date"] = pd.to_datetime(orders["exec_date"])
    equity = pd.Series(eq["equity"].to_numpy(), index=pd.to_datetime(eq["date"]))

    y = yearly(equity, trades, initial)
    mth = monthly(eq, trades, orders, initial)
    detail = signal_day_attributes(trades, orders, bars, master)
    by_market = _group(
        detail.assign(market=detail["market_code"] + " " + detail["market_name"].fillna("?")),
        "market",
    )
    band_order = {label: i for i, (label, _, _) in enumerate(PRICE_BANDS)}
    by_band = _group(detail, "price_band")
    by_band = (
        by_band.assign(_o=by_band["price_band"].map(band_order))
        .sort_values("_o")
        .drop(columns="_o")
    )
    y.to_csv(run_dir / "analysis_yearly.csv", index=False)
    mth.to_csv(run_dir / "analysis_monthly.csv", index=False)
    by_market.to_csv(run_dir / "analysis_by_market.csv", index=False)
    by_band.to_csv(run_dir / "analysis_by_price_band.csv", index=False)
    detail.to_csv(run_dir / "analysis_trades_detail.csv", index=False)
    result = {
        "note": NOTE,
        "run": run_dir.name,
        "status": summary["metadata"]["status"],
        "execution_model_version": summary.get("execution_model_version"),
        "variant": summary.get("variant", {}).get("name"),
        "yearly": y.to_dict("records"),
        "drawdown": drawdown_period(equity),
        "by_market": by_market.to_dict("records"),
        "by_price_band": by_band.to_dict("records"),
        "trades_without_signal_attributes": int(detail["signal_close"].isna().sum()),
        "distribution": distribution(trades),
        "monthly_file": "analysis_monthly.csv",
        "totals_check": {
            "trades": len(trades),
            "by_market_sum": int(by_market["trades"].sum()),
            "by_band_sum": int(by_band["trades"].sum()),
            "pnl": float(trades["pnl"].sum()),
            "yearly_realized_sum": float(y["realized_pnl_exited"].sum()),
        },
    }
    (run_dir / "analysis.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8"
    )
    return result


def _json_default(v: object) -> object:
    if isinstance(v, pd.Timestamp):
        return str(v.date())
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return None if math.isnan(float(v)) else float(v)
    return str(v)
