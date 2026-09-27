"""Compare two saved runs for identical trading results.

Used to confirm that a logging-only change did not alter execution: trade dates, prices,
quantities, exit reasons, equity curve and order outcomes must be identical. Columns and
rows that exist only in the newer run (diagnostics, ignored-held rows) are left out.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

TRADE_KEYS = [
    "symbol",
    "side",
    "entry_date",
    "entry_price",
    "exit_date",
    "exit_price",
    "quantity",
    "commission",
    "pnl",
    "return_pct",
    "exit_reason",
    "holding_days",
]
EQUITY_KEYS = ["date", "equity", "cash", "position_value", "positions"]
ORDER_KEYS = [
    "signal_date",
    "exec_date",
    "symbol",
    "score",
    "rank",
    "planned_quantity",
    "budget",
    "status",
    "filled_quantity",
    "fill_price",
]
NEW_ORDER_STATUSES = {"ignored_already_held"}


@dataclass
class Diff:
    """Comparison result for one file."""

    name: str
    identical: bool
    detail: str


def _compare(a: pd.DataFrame, b: pd.DataFrame, name: str, tol: float) -> Diff:
    if len(a) != len(b):
        return Diff(name, False, f"row count {len(a)} vs {len(b)}")
    a, b = a.reset_index(drop=True), b.reset_index(drop=True)
    worst = 0.0
    for col in a.columns:
        x, y = a[col], b[col]
        if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y):
            both_nan = x.isna() & y.isna()
            d = (x - y).abs().where(~both_nan, 0.0)
            if d.isna().any():
                return Diff(name, False, f"{col}: NaN mismatch")
            worst = max(worst, float(d.max()) if len(d) else 0.0)
            if (d > tol).any():
                i = int(d.to_numpy().argmax())
                return Diff(name, False, f"{col} row {i}: {x[i]} vs {y[i]}")
        else:
            ne = x.fillna("<NA>").astype(str) != y.fillna("<NA>").astype(str)
            if ne.any():
                i = int(ne.to_numpy().argmax())
                return Diff(name, False, f"{col} row {i}: {x[i]!r} vs {y[i]!r}")
    return Diff(name, True, f"{len(a)} rows, max numeric diff {worst:.3g}")


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"symbol": str})


def compare_runs(before: Path, after: Path, tol: float = 1e-9) -> list[Diff]:
    """Compare ``before`` and ``after`` run directories."""
    tb = _read(before / "trades.csv")[TRADE_KEYS]
    ta = _read(after / "trades.csv")[TRADE_KEYS]
    eb = pd.read_csv(before / "equity_curve.csv")[EQUITY_KEYS]
    ea = pd.read_csv(after / "equity_curve.csv")[EQUITY_KEYS]
    ob = _read(before / "orders.csv")
    oa = _read(after / "orders.csv")
    ob = ob[~ob["status"].isin(NEW_ORDER_STATUSES)][ORDER_KEYS]
    oa = oa[~oa["status"].isin(NEW_ORDER_STATUSES)][ORDER_KEYS]
    return [
        _compare(tb, ta, "trades.csv (dates, prices, quantities, reasons, PnL)", tol),
        _compare(eb, ea, "equity_curve.csv (equity, cash, positions)", tol),
        _compare(ob, oa, "orders.csv (excluding new ignored-held rows)", tol),
    ]
