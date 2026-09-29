"""Compare sensitivity-analysis runs with the base run (development period only).

Each variant changes ONE condition and is a full re-run (cash, sizes and later trades
included). The comparison is descriptive: a better result is never adopted automatically,
and for random ranking all 20 seeds are reported (no best seed is picked).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

METRIC_KEYS: tuple[str, ...] = (
    "total_return",
    "cagr",
    "max_drawdown",
    "sharpe_ratio",
    "sortino_ratio",
    "profit_factor",
    "win_rate",
    "number_of_trades",
    "average_profit",
    "average_loss",
    "expectancy",
    "final_equity",
)

NOTE = (
    "Development period only. Each variant changes one condition from the base and is re-run "
    "from the start. Descriptive comparison: results are not attributed to single trades and a "
    "better result is NOT adopted as the base. Random ranking: all seeds are reported."
)


def load_run(run_dir: Path) -> dict[str, Any]:
    """summary.json, trades and orders of a run."""
    return {
        "dir": run_dir,
        "summary": json.loads((run_dir / "summary.json").read_text(encoding="utf-8")),
        "trades": pd.read_csv(run_dir / "trades.csv", dtype={"symbol": str}),
        "orders": pd.read_csv(run_dir / "orders.csv", dtype={"symbol": str}),
    }


def run_row(run: dict[str, Any]) -> dict[str, Any]:
    """One row of the metrics table."""
    s = run["summary"]
    v = s.get("variant", {})
    stats = s.get("stats", {})
    orders = stats.get("orders", {})
    row: dict[str, Any] = {
        "run": run["dir"].name,
        "variant": v.get("name"),
        "analysis": v.get("analysis"),
        "seed": v.get("seed"),
        "status": s["metadata"]["status"],
    }
    row.update({k: s.get("metrics", {}).get(k) for k in METRIC_KEYS})
    row.update(
        {
            "realized_pnl": s.get("pnl", {}).get("realized_pnl"),
            "unrealized_pnl": s.get("pnl", {}).get("unrealized_pnl"),
            "filled": orders.get("filled", 0),
            "cancelled_no_slot": orders.get("cancelled_no_slot", 0),
            "not_placed_unaffordable": orders.get("not_placed_unaffordable", 0),
            "cancelled_insufficient_funds": orders.get("cancelled_insufficient_funds", 0),
            "cancelled_limit_up": orders.get("cancelled_limit_up", 0),
            "intraday_both_touched": stats.get("intraday_both_touched"),
            "stop_priority_applied": stats.get("stop_priority_applied"),
            "take_profit_priority_applied": stats.get("take_profit_priority_applied"),
        }
    )
    return row


def _jaccard(a: set[Any], b: set[Any]) -> float | None:
    return None if not (a or b) else len(a & b) / len(a | b)


def compare_with_base(base: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """Trade matching (symbol, entry_date), overlaps and order-status changes vs the base."""
    key = ["symbol", "entry_date"]
    cols = ["exit_date", "exit_reason", "exit_price", "quantity", "pnl"]
    tb, to = base["trades"], other["trades"]
    m = tb[key + cols].merge(
        to[key + cols], on=key, how="outer", suffixes=("_base", "_var"), indicator=True
    )

    def cat(r: pd.Series) -> str:
        if r["_merge"] == "left_only":
            return "entry_only_base"
        if r["_merge"] == "right_only":
            return "entry_only_variant"
        if (r["exit_date_base"], r["exit_reason_base"], r["quantity_base"]) != (
            r["exit_date_var"],
            r["exit_reason_var"],
            r["quantity_var"],
        ):
            return "exit_changed"
        if abs(float(r["exit_price_base"]) - float(r["exit_price_var"])) > 1e-9:
            return "same_exit_price_changed"
        if abs(float(r["pnl_base"]) - float(r["pnl_var"])) > 1e-6:
            return "same_exit_pnl_changed"
        return "same"

    cats = m.apply(cat, axis=1) if len(m) else pd.Series(dtype=str)
    buys_b = set(zip(tb["symbol"], tb["entry_date"], strict=True))
    buys_o = set(zip(to["symbol"], to["entry_date"], strict=True))
    sb, so = base["summary"].get("stats", {}), other["summary"].get("stats", {})
    ob, oo = sb.get("orders", {}), so.get("orders", {})
    return {
        "trade_categories": {str(k): int(v) for k, v in cats.value_counts().items()},
        "purchases_same_symbol_and_date": len(buys_b & buys_o),
        "purchases_overlap_jaccard": _jaccard(buys_b, buys_o),
        "symbols_overlap_jaccard": _jaccard(set(tb["symbol"]), set(to["symbol"])),
        "order_status_change": {
            k: int(oo.get(k, 0)) - int(ob.get(k, 0)) for k in sorted(set(ob) | set(oo))
        },
    }


def describe(values: list[float]) -> dict[str, float | int | None]:
    """Mean, median, std (ddof=1), min and max over all values (NaN dropped)."""
    s = pd.Series([v for v in values if v is not None and not math.isnan(v)], dtype=float)
    if s.empty:
        return {"n": 0, "mean": None, "median": None, "std": None, "min": None, "max": None}
    return {
        "n": len(s),
        "mean": float(s.mean()),
        "median": float(s.median()),
        "std": float(s.std(ddof=1)) if len(s) > 1 else None,
        "min": float(s.min()),
        "max": float(s.max()),
    }


def summarize(base_dir: Path, variant_dirs: list[Path]) -> tuple[dict[str, Any], pd.DataFrame]:
    """Summary dict and metrics table (base first, then variants in the given order)."""
    base = load_run(base_dir)
    runs = [load_run(d) for d in variant_dirs]
    table = pd.DataFrame([run_row(base)] + [run_row(r) for r in runs])
    comparisons = {r["dir"].name: compare_with_base(base, r) for r in runs}
    random_rows = table[table["variant"].astype(str).str.startswith("B_random")]
    random_stats = (
        {
            k: describe([float(v) if v is not None else math.nan for v in random_rows[k]])
            for k in METRIC_KEYS + ("cancelled_no_slot", "not_placed_unaffordable", "filled")
        }
        if len(random_rows)
        else None
    )
    if random_stats is not None:
        random_stats["purchases_overlap_jaccard_vs_base"] = describe(
            [comparisons[n]["purchases_overlap_jaccard"] for n in random_rows["run"]]
        )
    summary = {
        "note": NOTE,
        "base_run": base_dir.name,
        "period": [
            base["summary"]["metadata"]["start_date"],
            base["summary"]["metadata"]["end_date"],
        ],
        "runs": table.to_dict("records"),
        "comparison_with_base": comparisons,
        "random_ranking_all_seeds": random_stats,
    }
    return summary, table
