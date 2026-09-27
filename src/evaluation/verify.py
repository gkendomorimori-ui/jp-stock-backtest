"""Consistency checks for a saved run (results/runs/<run_id>/).

These checks do not judge profitability. They verify that the saved files are internally
consistent with the execution rules (cash, valuation, sizing, timing, counts).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

EXIT_REASONS = {
    "stop_loss_open",
    "take_profit_open",
    "stop_loss",
    "take_profit",
    "time_exit",
    "end_of_test",
}
TOL = 1e-6


@dataclass
class Check:
    """Result of one check."""

    name: str
    ok: bool
    detail: str = ""


def verify_run(run_dir: Path) -> tuple[list[Check], dict[str, Any]]:
    """Run all checks. Returns (checks, informational breakdowns)."""
    checks: list[Check] = []
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    meta, execution = summary["metadata"], summary["execution"]
    trades = pd.read_csv(run_dir / "trades.csv", dtype={"symbol": str})
    equity = pd.read_csv(run_dir / "equity_curve.csv")
    orders = pd.read_csv(run_dir / "orders.csv", dtype={"symbol": str})
    c, lot = float(execution["commission_rate"]), int(execution["lot_size"])

    def add(name: str, ok: Any, detail: str = "") -> None:
        checks.append(Check(name, bool(ok), detail))

    # --- files / status
    needs_review = meta["status"] == "needs_review"
    add(
        "status and unresolved_events.csv agree",
        needs_review == (run_dir / "unresolved_events.csv").exists(),
        meta["status"],
    )
    add("run_type recorded", meta["run_type"] in ("smoke_test", "development", "final_evaluation"))

    # --- equity curve
    add(
        "equity curve covers the period",
        len(equity) > 0
        and equity["date"].iloc[0] == meta["start_date"]
        and (needs_review or equity["date"].iloc[-1] == meta["end_date"]),
        f"{equity['date'].iloc[0] if len(equity) else '-'} .. "
        f"{equity['date'].iloc[-1] if len(equity) else '-'}",
    )
    diff = (equity["equity"] - equity["cash"] - equity["position_value"]).abs().max()
    add("equity = cash + position value (every day)", diff < 1e-3, f"max diff {diff:.6f}")
    add("cash never negative", (equity["cash"] >= -TOL).all(), f"min {equity['cash'].min():,.2f}")
    add(
        "positions never exceed the maximum",
        (equity["positions"] <= int(execution["max_positions"])).all(),
        f"max {equity['positions'].max()}",
    )
    add(
        "first day starts with initial capital and no positions",
        abs(equity["equity"].iloc[0] - float(execution["initial_capital"])) < TOL
        or len(trades[trades["entry_date"] == meta["start_date"]]) > 0,
    )
    add(
        "benchmark values are null, not 0",
        all(meta["benchmark"].get(k) is None for k in ("total_return", "cagr", "max_drawdown"))
        or meta["benchmark"].get("status") != "unavailable",
    )

    # --- trades
    add(
        "exit reasons are known",
        set(trades["exit_reason"]) <= EXIT_REASONS,
        str(sorted(set(trades["exit_reason"]) - EXIT_REASONS)),
    )
    add("entry date <= exit date", (trades["entry_date"] <= trades["exit_date"]).all())
    add("holding days >= 1", (trades["holding_days"] >= 1).all())
    add(
        "quantity is a positive multiple of the lot",
        ((trades["quantity"] > 0) & (trades["quantity"] % lot == 0)).all(),
        "(may legitimately fail after a split; check the symbol)",
    )
    tp = trades[trades["exit_reason"].isin(["take_profit", "take_profit_open"])]
    sl = trades[trades["exit_reason"].isin(["stop_loss", "stop_loss_open"])]
    add("take-profit exits are gains", (tp["pnl"] > 0).all(), f"{len(tp)} trades")
    add("stop-loss exits are losses", (sl["pnl"] < 0).all(), f"{len(sl)} trades")
    long_hold = trades[
        (trades["exit_reason"] == "time_exit")
        & (trades["holding_days"] < int(execution["max_holding_days"]))
    ]
    add("time exits happen on/after the holding limit", long_hold.empty, f"{len(long_hold)} early")

    # --- orders vs trades
    filled = orders[orders["status"] == "filled"]
    open_at_end = int(equity["positions"].iloc[-1]) if len(equity) else 0
    add(
        "filled orders = closed trades + positions still open",
        len(filled) == len(trades) + open_at_end,
        f"filled {len(filled)}, trades {len(trades)}, open {open_at_end}",
    )
    dates = list(equity["date"])
    nxt = {d: dates[i + 1] for i, d in enumerate(dates[:-1])}
    late = filled[
        [nxt.get(s) != e for s, e in zip(filled["signal_date"], filled["exec_date"], strict=True)]
    ]
    add("buys execute on the next trading day after the signal", late.empty, f"{len(late)} not T+1")
    paid = filled["filled_quantity"] * filled["fill_price"] * (1 + c)
    over = filled[paid > filled["budget"] + 1e-6]
    add("buy payment incl. costs <= 20% budget", over.empty, f"{len(over)} over budget")
    shrunk = filled[filled["filled_quantity"] > filled["planned_quantity"]]
    add("filled quantity never exceeds the planned quantity", shrunk.empty, f"{len(shrunk)}")
    merged = trades.merge(
        filled[["symbol", "exec_date"]],
        left_on=["symbol", "entry_date"],
        right_on=["symbol", "exec_date"],
        how="left",
        indicator=True,
    )
    add("every trade has a filled order", (merged["_merge"] == "both").all())

    # --- final equity
    if open_at_end == 0 and not needs_review and len(equity):
        final = float(execution["initial_capital"]) + trades["pnl"].sum()
        d = abs(final - equity["equity"].iloc[-1])
        add("final equity = initial capital + sum of trade PnL", d < 1e-3, f"diff {d:.6f}")

    info = {
        "exit_reasons": trades["exit_reason"].value_counts().to_dict(),
        "order_status": orders["status"].value_counts().to_dict(),
        "holding_days": trades["holding_days"].describe().round(2).to_dict() if len(trades) else {},
        "symbols_traded": int(trades["symbol"].nunique()),
        "max_positions_held": int(equity["positions"].max()) if len(equity) else 0,
    }
    return checks, info
