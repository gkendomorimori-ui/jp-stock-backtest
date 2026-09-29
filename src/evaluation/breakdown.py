"""Supplementary trade breakdown for a saved run (reporting only).

Classes (a trade can be in both of the first two -- overlaps are reported):
    * low_price   : ACTUAL close on the signal day T < 10 yen
    * jump_100    : ADJUSTED close on T vs. the previous exchange trading day > +100%
    * neither     : in neither class

Sums per class are sums over the trades of THIS run. They are NOT the result of a run that
excludes those securities (removing trades would change cash, slots and later trades).
"""

from __future__ import annotations

import math
from typing import Any

import pandas as pd

from src.data.processing import ProcessedData

LOW_PRICE_YEN = 10.0
JUMP_RATIO = 1.0  # +100%


def classify_trades(
    trades: pd.DataFrame, orders: pd.DataFrame, data: ProcessedData
) -> pd.DataFrame:
    """Add ``signal_date``, ``signal_close``, ``signal_adj_return``, class flags to trades."""
    filled = orders[orders["status"] == "filled"][["symbol", "exec_date", "signal_date"]]
    t = trades.merge(
        filled, left_on=["symbol", "entry_date"], right_on=["symbol", "exec_date"], how="left"
    )
    if t["signal_date"].isna().any():
        raise ValueError("some trades have no matching filled order")
    cal = data.calendar
    tdays = pd.DatetimeIndex(cal.loc[cal["is_trading_day"], "date"]).sort_values()
    prev_day = pd.Series(tdays[:-1], index=tdays[1:])
    bars = data.bars.set_index(["symbol", "date"])[["close", "adj_close"]]
    sig = pd.to_datetime(t["signal_date"])
    prev = sig.map(prev_day)
    cur = bars.reindex(list(zip(t["symbol"], sig, strict=True)))
    before = bars.reindex(list(zip(t["symbol"], prev, strict=True)))
    t["signal_close"] = cur["close"].to_numpy()
    t["signal_adj_return"] = cur["adj_close"].to_numpy() / before["adj_close"].to_numpy() - 1.0
    t["low_price"] = t["signal_close"] < LOW_PRICE_YEN
    t["jump_100"] = t["signal_adj_return"] > JUMP_RATIO
    t["jump_unknown"] = t["signal_adj_return"].isna()
    t["neither"] = ~t["low_price"] & ~t["jump_100"]
    return t.drop(columns=["exec_date"])


def summarize(classified: pd.DataFrame) -> dict[str, Any]:
    """Counts, PnL and both-touched counts per class, with overlaps."""

    def stats(mask: pd.Series) -> dict[str, Any]:
        sub = classified[mask]
        both = (
            sub["intraday_both_touched"]
            if "intraday_both_touched" in sub
            else pd.Series(dtype=bool)
        )
        pnl = float(sub["pnl"].sum()) if len(sub) else 0.0
        return {
            "trades": int(len(sub)),
            "pnl_sum": round(pnl, 2),
            "pnl_mean": round(float(sub["pnl"].mean()), 2) if len(sub) else None,
            "wins": int((sub["pnl"] > 0).sum()),
            "losses": int((sub["pnl"] < 0).sum()),
            "intraday_both_touched": int(both.astype("boolean").fillna(False).sum()),
        }

    low, jump = classified["low_price"], classified["jump_100"]
    total = stats(pd.Series(True, index=classified.index))
    classes: dict[str, dict[str, Any]] = {
        "low_price": stats(low),
        "jump_100": stats(jump),
        "low_price_and_jump_100": stats(low & jump),
        "neither": stats(classified["neither"]),
    }
    n = classes["low_price"]["trades"] + classes["jump_100"]["trades"]
    n += classes["neither"]["trades"] - classes["low_price_and_jump_100"]["trades"]
    pnl = classes["low_price"]["pnl_sum"] + classes["jump_100"]["pnl_sum"]
    pnl += classes["neither"]["pnl_sum"] - classes["low_price_and_jump_100"]["pnl_sum"]
    return {
        "definition": {
            "low_price": f"actual close on the signal day < {LOW_PRICE_YEN:g} yen",
            "jump_100": "adjusted close on the signal day vs previous trading day > +100%",
            "neither": "in neither class",
        },
        "classes": classes,
        "total": total,
        "jump_return_unknown": int(classified["jump_unknown"].sum()),
        "consistency_trades_add_up": n == total["trades"],
        "consistency_pnl_adds_up": math.isclose(pnl, total["pnl_sum"], abs_tol=0.05),
        "notes": [
            "low_price and jump_100 overlap; the overlap is shown separately and is included "
            "in both classes.",
            "Per-class sums are over this run's trades. They are NOT the performance of a run "
            "that excludes those securities.",
        ],
    }
