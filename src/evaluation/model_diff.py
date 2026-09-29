"""Describe how two runs that differ only in the execution model differ (v1 vs v2).

This is a DESCRIPTION, not an attribution. Once one exit changes (a different price, a
carried sell, a cancelled buy), cash, free slots and every later order can change too, so
the difference in total PnL is not split into causes. Trades are matched by
(symbol, entry_date) and classified; the application counts of the v2 rules come from the
v2 run's own log.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

#: Settings that must be identical for the comparison to be meaningful.
SAME_KEYS: tuple[str, ...] = (
    "strategy_name",
    "strategy_version",
    "parameters",
    "universe",
    "start_date",
    "end_date",
    "commission",
    "slippage",
    "data_source",
    "run_type",
)

NOTE = (
    "Descriptive comparison only. After the first difference, cash, free slots and later "
    "orders can change, so the total PnL difference is NOT attributed to individual causes "
    "(tick rounding, limit locks, carried sells). v1 is diagnostic; v2 is the base model."
)


PRICE_ONLY: tuple[str, ...] = ("same_exit_price_changed", "same_exit_pnl_changed")


class ComparisonError(Exception):
    """The two runs cannot be compared (different settings or data)."""


def _load(run_dir: Path) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    trades = pd.read_csv(run_dir / "trades.csv", dtype={"symbol": str})
    orders = pd.read_csv(run_dir / "orders.csv", dtype={"symbol": str})
    return summary, trades, orders


def compare_models(v1_dir: Path, v2_dir: Path) -> tuple[dict[str, Any], pd.DataFrame]:
    """Compare a v1 run and a v2 run of the same settings.

    Returns:
        (summary dict, trade-level table)

    Raises:
        ComparisonError: If the runs are not v1 / v2 or their settings / data differ.
    """
    s1, t1, o1 = _load(v1_dir)
    s2, t2, o2 = _load(v2_dir)
    versions = (s1.get("execution_model_version"), s2.get("execution_model_version"))
    if versions != ("v1", "v2"):
        raise ComparisonError(f"expected a v1 run and a v2 run, got {versions}")
    diff_keys = [k for k in SAME_KEYS if s1["metadata"].get(k) != s2["metadata"].get(k)]
    exec1 = {k: v for k, v in s1["execution"].items() if k != "model_version"}
    exec2 = {k: v for k, v in s2["execution"].items() if k != "model_version"}
    if exec1 != exec2:
        diff_keys.append("execution")
    if s1.get("data") != s2.get("data"):
        diff_keys.append("data")
    if diff_keys:
        raise ComparisonError(f"settings or data differ: {diff_keys}")

    key = ["symbol", "entry_date"]
    cols = ["exit_date", "exit_reason", "exit_phase", "base_price", "exit_price", "quantity", "pnl"]
    m = t1[key + cols].merge(
        t2[key + cols + ["tick_rounded", "carried_days", "last_unfilled_cause"]],
        on=key,
        how="outer",
        suffixes=("_v1", "_v2"),
        indicator=True,
    )
    m["category"] = m.apply(_category, axis=1)
    m["pnl_diff"] = m["pnl_v2"].fillna(0.0) - m["pnl_v1"].fillna(0.0)
    m = m.drop(columns="_merge").sort_values(key).reset_index(drop=True)

    changed = m[m["category"] != "same"]
    first_divergence = None
    if len(changed):
        matched = changed["category"].isin(["exit_changed", *PRICE_ONLY])
        dates = pd.concat(
            [
                changed.loc[~matched, "entry_date"],  # entered in one model only
                changed.loc[matched, "exit_date_v1"],
                changed.loc[matched, "exit_date_v2"],
            ]
        ).dropna()
        first_divergence = str(min(dates))[:10] if len(dates) else None

    def status_counts(o: pd.DataFrame) -> dict[str, int]:
        return {str(k): int(v) for k, v in o["status"].value_counts().items()}

    okey = ["signal_date", "symbol"]
    om = o1[okey + ["status"]].merge(
        o2[okey + ["status"]], on=okey, how="outer", suffixes=("_v1", "_v2")
    )
    order_changes = om[om["status_v1"].fillna("-") != om["status_v2"].fillna("-")]
    stats2 = s2.get("stats", {})
    summary = {
        "note": NOTE,
        "v1_run": v1_dir.name,
        "v2_run": v2_dir.name,
        "period": [s1["metadata"]["start_date"], s1["metadata"]["end_date"]],
        "run_type": s1["metadata"]["run_type"],
        "status": {"v1": s1["metadata"]["status"], "v2": s2["metadata"]["status"]},
        "trades": {"v1": len(t1), "v2": len(t2)},
        "total_pnl": {"v1": float(t1["pnl"].sum()), "v2": float(t2["pnl"].sum())},
        "final_equity": {
            "v1": s1.get("metrics", {}).get("final_equity"),
            "v2": s2.get("metrics", {}).get("final_equity"),
        },
        "trade_categories": {str(k): int(v) for k, v in m["category"].value_counts().items()},
        "first_divergence_date": first_divergence,
        "order_status": {"v1": status_counts(o1), "v2": status_counts(o2)},
        "orders_with_different_status": len(order_changes),
        "v2_rule_applications": {
            "tick_rounded": stats2.get("tick_rounded"),
            "tick_class_unknown": stats2.get("tick_class_unknown"),
            "buy_unfilled_limit_up": stats2.get("buy_unfilled_limit_up"),
            "sell_unfilled": stats2.get("sell_unfilled"),
            "sells_executed_after_carry": stats2.get("sells_executed_after_carry"),
            "sell_requests_open_at_end": stats2.get("sell_requests_open_at_end"),
            "limit_flag_missing": stats2.get("limit_flag_missing"),
        },
        "same_trades_price_only": _same_trade_price_effect(m),
    }
    return summary, m


def _category(row: pd.Series) -> str:
    if row["_merge"] == "left_only":
        return "entry_only_v1"
    if row["_merge"] == "right_only":
        return "entry_only_v2"
    same_exit = (
        row["exit_date_v1"] == row["exit_date_v2"]
        and row["exit_reason_v1"] == row["exit_reason_v2"]
        and row["quantity_v1"] == row["quantity_v2"]
    )
    if not same_exit:
        return "exit_changed"
    if abs(float(row["exit_price_v1"]) - float(row["exit_price_v2"])) > 1e-9:
        return "same_exit_price_changed"
    if abs(float(row["pnl_v1"]) - float(row["pnl_v2"])) > 1e-6:
        return "same_exit_pnl_changed"
    return "same"


def _same_trade_price_effect(m: pd.DataFrame) -> dict[str, Any]:
    """PnL difference on trades whose entry, exit date, reason and quantity are unchanged.

    Only these trades have a direct price-only difference; it is reported by exit reason
    and is NOT a decomposition of the total difference.
    """
    sub = m[m["category"] == "same_exit_price_changed"]
    return {
        "trades": len(sub),
        "pnl_diff_sum": float(sub["pnl_diff"].sum()),
        "by_exit_reason": {
            str(k): {"trades": int(len(g)), "pnl_diff_sum": float(g["pnl_diff"].sum())}
            for k, g in sub.groupby("exit_reason_v1")
        },
    }
