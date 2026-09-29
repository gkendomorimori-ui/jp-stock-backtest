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
    add(
        "run_type recorded",
        meta["run_type"] in ("smoke_test", "development", "final_evaluation", "holdout"),
    )

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
    if "carried_days" in trades.columns:
        # a carried stop request is executed even if the price recovered (v2)
        sl_same_day = sl[sl["carried_days"] == 0]
        add(
            "stop-loss exits executed on the trigger day are losses",
            (sl_same_day["pnl"] < 0).all(),
            f"{len(sl_same_day)} trades ({len(sl) - len(sl_same_day)} carried not checked)",
        )
    else:
        add("stop-loss exits are losses", (sl["pnl"] < 0).all(), f"{len(sl)} trades")
    long_hold = trades[
        (trades["exit_reason"] == "time_exit")
        & (trades["holding_days"] < int(execution["max_holding_days"]))
    ]
    add("time exits happen on/after the holding limit", long_hold.empty, f"{len(long_hold)} early")

    # --- orders vs trades
    filled = orders[orders["status"] == "filled"]
    op_path = run_dir / "open_positions.csv"
    if "pnl" in summary:  # runs that save open_positions.csv (also correct after a stop)
        open_at_end = len(pd.read_csv(op_path)) if op_path.exists() else 0
    else:
        open_at_end = int(equity["positions"].iloc[-1]) if len(equity) else 0
    add(
        "filled orders = closed trades + positions still open",
        len(filled) == len(trades) + open_at_end,
        f"filled {len(filled)}, trades {len(trades)}, open {open_at_end}",
    )
    dates = list(equity["date"])
    halted = (summary.get("pnl") or {}).get("halted")
    if halted is not None:  # the stop day was not valued but its open buys happened
        dates.append(halted["date"])
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

    # --- signal accounting (runs that log ignored held-symbol signals)
    stats = summary.get("stats", {})
    if "signals_ignored_already_held" in stats:
        add(
            "every signal has exactly one orders.csv row",
            int(stats["signals"]) == len(orders),
            f"signals {stats['signals']}, rows {len(orders)}",
        )
        add(
            "ignored-held count matches orders.csv",
            int(stats["signals_ignored_already_held"])
            == int((orders["status"] == "ignored_already_held").sum()),
        )

    # --- diagnostic columns (runs after the logging update)
    if "exit_phase" in trades.columns:
        both = trades["intraday_both_touched"]
        applied = trades["stop_priority_applied"]
        phase_col = "trigger_phase" if "trigger_phase" in trades.columns else "exit_phase"
        opened = trades[phase_col] == "open"
        add(
            "open exits are not counted as intraday both-touched",
            both[opened].isna().all() and applied[opened].isna().all(),
        )
        app = trades[applied.astype("boolean").fillna(False)]
        add(
            "stop priority applied only to stop-loss exits that touched both levels",
            (app["exit_reason"] == "stop_loss").all()
            and app["intraday_both_touched"].astype(bool).all(),
            f"{len(app)} trades",
        )
        n_both = int(both.astype("boolean").fillna(False).sum())
        add(
            "both-touched / priority-applied counts match summary",
            stats.get("intraday_both_touched") == n_both
            and stats.get("stop_priority_applied") == len(app),
            f"both {n_both}, applied {len(app)}",
        )

    # --- execution model price fields (runs with execution_model_version)
    version = summary.get("execution_model_version")
    if version is not None:
        s = float(execution["slippage_rate"])
        add("execution model version recorded", version in ("v1", "v2"), str(version))
        slip_model = execution.get("slippage_model", "rate")
        if slip_model == "rate":
            add(
                "exit price = base price after slippage",
                ((trades["exit_price"] - trades["base_price"] * (1 - s)).abs() < 1e-6).all(),
            )
        else:
            add(
                "exit price <= base price x (1 - rate) (slippage at least the rate)",
                (trades["exit_price"] <= trades["base_price"] * (1 - s) + 1e-6).all(),
                str(slip_model),
            )
        intra = trades[(trades["exit_phase"] == "intraday")]
        itp = intra[intra["exit_reason"] == "take_profit"]
        isl = intra[intra["exit_reason"] == "stop_loss"]
        if version == "v2":
            tp_k = itp[itp["tick_class"] != "unknown"]
            sl_k = isl[isl["tick_class"] != "unknown"]
            add(
                "v2 intraday take profit: level <= base < level + 1 tick",
                (
                    (tp_k["base_price"] >= tp_k["trigger_level"] - 1e-6)
                    & (tp_k["base_price"] < tp_k["trigger_level"] + tp_k["tick_size"] + 1e-6)
                ).all(),
                f"{len(tp_k)} trades",
            )
            add(
                "v2 intraday stop loss: level - 1 tick < base <= level",
                (
                    (sl_k["base_price"] <= sl_k["trigger_level"] + 1e-6)
                    & (sl_k["base_price"] > sl_k["trigger_level"] - sl_k["tick_size"] - 1e-6)
                ).all(),
                f"{len(sl_k)} trades",
            )
            rounded = trades[trades["tick_rounded"].astype("boolean").fillna(False)]
            tr = stats.get("tick_rounded", {})
            add(
                "tick-rounded counts match summary",
                int((rounded["exit_reason"] == "take_profit").sum()) == tr.get("take_profit")
                and int((rounded["exit_reason"] == "stop_loss").sum()) == tr.get("stop_loss"),
                str(tr),
            )
        else:
            add(
                "v1 intraday exits at the level itself",
                ((intra["base_price"] - intra["trigger_level"]).abs() < 1e-6).all(),
            )
        carried = trades["carried_days"] > 0
        add(
            "carried exits: trigger date before exit date, same-day exits equal",
            (trades.loc[carried, "trigger_date"] < trades.loc[carried, "exit_date"]).all()
            and (trades.loc[~carried, "trigger_date"] == trades.loc[~carried, "exit_date"]).all(),
            f"{int(carried.sum())} carried",
        )
        unfilled_file = run_dir / "sell_unfilled.csv"
        if unfilled_file.exists():
            unfilled = pd.read_csv(unfilled_file, dtype={"symbol": str})
            counts = {str(k): int(v) for k, v in unfilled["cause"].value_counts().items()}
            add(
                "sell_unfilled.csv matches summary",
                counts == stats.get("sell_unfilled", {}),
                str(counts),
            )
        add(
            "limit-up cancellations match summary",
            int((orders["status"] == "cancelled_limit_up").sum())
            == stats.get("buy_unfilled_limit_up", 0),
        )

    # --- sensitivity A: take-profit-first only where both levels were touched intraday
    prio = execution.get("intraday_priority", "stop_loss")
    if "exit_phase" in trades.columns and "trigger_phase" in trades.columns:
        tp_both = trades[
            (trades["exit_reason"] == "take_profit")
            & trades["intraday_both_touched"].astype("boolean").fillna(False)
        ]
        add(
            "take-profit exits with both levels touched only under take-profit priority",
            prio == "take_profit" or tp_both.empty,
            f"{len(tp_both)} ({prio})",
        )
        if prio == "take_profit":
            add(
                "take-profit-priority count matches summary",
                len(tp_both) == stats.get("take_profit_priority_applied"),
            )

    # --- realized / unrealized
    pnl = summary.get("pnl")
    if pnl is not None:
        add(
            "realized PnL = sum of trade PnL",
            abs(pnl["realized_pnl"] - float(trades["pnl"].sum())) < 1e-3,
        )
        op_file = run_dir / "open_positions.csv"
        n_open = len(pd.read_csv(op_file)) if op_file.exists() else 0
        add(
            "open_positions.csv matches the summary",
            n_open == pnl["open_positions"],
            f"{n_open} open",
        )
        if pnl.get("halted") is None and len(equity):
            d = abs(
                float(execution["initial_capital"])
                + pnl["realized_pnl"]
                + pnl["unrealized_pnl"]
                - equity["equity"].iloc[-1]
            )
            add("final equity = initial + realized + unrealized", d < 1e-3, f"diff {d:.6f}")

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
        "signals_ignored_already_held": stats.get("signals_ignored_already_held"),
        "intraday_both_touched": stats.get("intraday_both_touched"),
        "stop_priority_applied": stats.get("stop_priority_applied"),
        "max_positions_held": int(equity["positions"].max()) if len(equity) else 0,
        "execution_model_version": summary.get("execution_model_version"),
        "tick_rounded": stats.get("tick_rounded"),
        "sell_unfilled": stats.get("sell_unfilled"),
        "buy_unfilled_limit_up": stats.get("buy_unfilled_limit_up"),
        "sells_executed_after_carry": stats.get("sells_executed_after_carry"),
        "tick_class_unknown": stats.get("tick_class_unknown"),
        "limit_flag_missing": stats.get("limit_flag_missing"),
    }
    return checks, info
