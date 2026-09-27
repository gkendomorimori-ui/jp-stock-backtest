"""Event-driven daily backtest engine (long only, daily bars).

Implements the execution model of strategies/high_price_breakout.md (v0.2.0) and the
common rules of docs/BACKTEST_RULES.md. Processing order for each trading day D:

    0. before the open: split / reverse-split adjustment; delisting check of holdings
    1. open sells  (positions held before D): carried time exits, gap stop loss / take profit
    2. open buys   (orders created at the close of D-1), with cash and slots after step 1
    3. intraday stop loss / take profit (stop loss first when both are touched)
    4. close sells: time exit on holding day ``max_holding_days``; end of test
    5. valuation at the close; orders for D+1

Cash and slots freed in steps 3-4 are never used by step 2 of the same day.
Prices used here are ACTUAL prices; share counts are actual share counts.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from src.data.market_data import MarketData
from src.evaluation.report import UNRESOLVED_EVENT_COLUMNS

#: Columns of trades.csv.
TRADE_COLUMNS: list[str] = [
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
    "exit_phase",
    "intraday_both_touched",
    "stop_priority_applied",
]
"""Diagnostic columns (added after the first smoke test; they do not affect execution):

* ``exit_phase``: ``open`` / ``intraday`` / ``close`` -- when the exit was executed.
* ``intraday_both_touched``: on the exit day, did the intraday range (low <= stop level AND
  high >= take-profit level) reach BOTH levels? Empty for exits at the open, because the
  position was already closed before the intraday range could matter. ``False`` for close
  exits whose intraday check ran and touched neither level.
* ``stop_priority_applied``: ``True`` only when the stop-loss-first rule actually decided the
  outcome (both levels touched intraday -> exited by stop loss). Empty for open exits.
"""

#: Columns of equity_curve.csv.
EQUITY_COLUMNS: list[str] = ["date", "equity", "cash", "position_value", "positions"]

#: Columns of orders.csv.
ORDER_COLUMNS: list[str] = [
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

_EPS = 1e-9


def _val(row: pd.Series, symbol: str) -> float:
    """Float value of ``row[symbol]``; NaN if the symbol is absent or the value is null."""
    if symbol not in row.index:
        return math.nan
    value = row[symbol]
    return math.nan if pd.isna(value) else float(value)


def _row(table: pd.DataFrame, day: pd.Timestamp) -> pd.Series:
    row = table.loc[day]
    if not isinstance(row, pd.Series):
        raise DataError(f"duplicate trading day {day}")
    return row


def _loc(dates: pd.DatetimeIndex, day: pd.Timestamp) -> int:
    loc = dates.get_loc(day)
    if not isinstance(loc, int):
        raise DataError(f"duplicate trading day {day}")
    return loc


class DataError(Exception):
    """Market data required by the engine is missing."""


@dataclass(frozen=True)
class ExecutionParams:
    """All numbers the engine needs (from config/backtest.yaml and the strategy YAML)."""

    initial_capital: float
    commission_rate: float
    slippage_rate: float
    lot_size: int
    max_positions: int
    max_position_pct: float
    take_profit_pct: float
    stop_loss_pct: float
    max_holding_days: int


@dataclass
class Position:
    """An open position. Prices are per share on the CURRENT share basis."""

    symbol: str
    quantity: int
    entry_date: pd.Timestamp
    entry_index: int
    entry_price: float
    entry_cost: float
    entry_commission: float
    take_profit: float
    stop_loss: float
    last_close: float
    last_close_date: pd.Timestamp
    time_exit_pending: bool = False


@dataclass
class PendingOrder:
    """A buy order created at the close of the signal day, executed at the next open."""

    symbol: str
    signal_date: pd.Timestamp
    score: float
    rank: int
    quantity: int
    budget: float


@dataclass
class EngineResult:
    """Everything the engine produced."""

    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    orders: pd.DataFrame
    unresolved_events: pd.DataFrame
    status: str
    stats: dict[str, Any] = field(default_factory=dict)


class BacktestEngine:
    """Run one strategy's signals through the execution model."""

    def __init__(self, params: ExecutionParams) -> None:
        """Store execution parameters."""
        self.p = params
        self.cash = 0.0
        self.positions: dict[str, Position] = {}
        self.pending: list[PendingOrder] = []
        self.trades: list[list[Any]] = []
        self.equity_rows: list[list[Any]] = []
        self.order_rows: list[list[Any]] = []
        self.unresolved: list[list[Any]] = []
        self.status = "complete"
        self.stats: dict[str, Any] = {}
        self._i = 0
        self._intraday_checked: set[str] = set()

    # ------------------------------------------------------------------ public

    def run(
        self,
        md: MarketData,
        scores: pd.DataFrame,
        eligible: pd.DataFrame,
        eval_start: pd.Timestamp,
        eval_end: pd.Timestamp,
    ) -> EngineResult:
        """Simulate ``eval_start .. eval_end`` (both trading days in ``md``).

        Args:
            md: Market data.
            scores: date x symbol signal scores (NaN = no signal); higher ranks first.
            eligible: date x symbol buy eligibility (universe).
            eval_start: First simulated day (cash only, no positions).
            eval_end: Last simulated day; open positions are closed at its close.
        """
        dates = md.dates
        if eval_start not in dates or eval_end not in dates:
            raise DataError("eval_start / eval_end must be trading days in the data")
        self._reset()
        start_i, end_i = _loc(dates, eval_start), _loc(dates, eval_end)
        for i in range(start_i, end_i + 1):
            day = dates[i]
            self._i = i
            if not md.has_master(day):
                raise DataError(f"no master data for {day.date()}")
            if not self._before_open(md, day):
                self.status = "needs_review"
                break
            self._open_sells(md, day)
            self._open_buys(md, day, i)
            self._intraday(md, day)
            self._close(md, day, i, is_last=(i == end_i))
            self._value_and_plan(md, day, scores, eligible, is_last=(i == end_i))
        return self._result()

    # ------------------------------------------------------------------ steps

    def _before_open(self, md: MarketData, day: pd.Timestamp) -> bool:
        factors = _row(md["adj_factor"], day)
        for pos in list(self.positions.values()):
            f = _val(factors, pos.symbol)
            if not math.isnan(f) and f != 1.0:
                new_qty = pos.quantity / f
                if abs(new_qty - round(new_qty)) > 1e-6:
                    self._event(
                        day,
                        pos,
                        "split_fractional_shares",
                        "share count is not an integer after the split",
                    )
                    continue
                pos.quantity = int(round(new_qty))
                pos.entry_price *= f
                pos.take_profit *= f
                pos.stop_loss *= f
                pos.last_close *= f
        for order in self.pending:
            f = _val(factors, order.symbol)
            if not math.isnan(f) and f != 1.0:
                order.quantity = self._round_lot(order.quantity / f)
        for pos in self.positions.values():
            if not md.is_listed(day, pos.symbol):
                self._event(
                    day,
                    pos,
                    "delisted",
                    "held security is not in the master; settlement (sale, cash, share "
                    "exchange) cannot be determined -- not auto-settled",
                )
        return not self.unresolved

    def _open_sells(self, md: MarketData, day: pd.Timestamp) -> None:
        opens = _row(md["open"], day)
        for pos in sorted(self.positions.values(), key=lambda p: p.symbol):
            o = _val(opens, pos.symbol)
            if pd.isna(o):
                continue
            if pos.time_exit_pending:
                self._sell(pos, day, o, "time_exit", phase="open")
            elif o <= pos.stop_loss:
                self._sell(pos, day, o, "stop_loss_open", phase="open")
            elif o >= pos.take_profit:
                self._sell(pos, day, o, "take_profit_open", phase="open")

    def _open_buys(self, md: MarketData, day: pd.Timestamp, i: int) -> None:
        opens = _row(md["open"], day)
        s, c, lot = self.p.slippage_rate, self.p.commission_rate, self.p.lot_size
        for order in sorted(self.pending, key=lambda o: o.rank):
            status, qty, fill = "filled", 0, math.nan
            o = _val(opens, order.symbol)
            if len(self.positions) >= self.p.max_positions:
                status = "cancelled_no_slot"
            elif order.symbol in self.positions:
                status = "cancelled_already_held"
            elif not md.is_listed(day, order.symbol) or pd.isna(o):
                status = "cancelled_not_tradable"
            else:
                fill = o * (1 + s)
                limit = min(order.budget, self.cash)
                qty = order.quantity
                while qty > 0 and qty * fill * (1 + c) > limit + _EPS:
                    qty -= lot
                if qty <= 0:
                    status, qty = "cancelled_insufficient_funds", 0
                else:
                    gross = qty * fill
                    commission = gross * c
                    self.cash -= gross + commission
                    self.positions[order.symbol] = Position(
                        symbol=order.symbol,
                        quantity=qty,
                        entry_date=day,
                        entry_index=i,
                        entry_price=fill,
                        entry_cost=gross + commission,
                        entry_commission=commission,
                        take_profit=fill * (1 + self.p.take_profit_pct),
                        stop_loss=fill * (1 - self.p.stop_loss_pct),
                        last_close=fill,
                        last_close_date=day,
                    )
            self._log_order(order, day, status, qty, fill)
        self.pending = []

    def _intraday(self, md: MarketData, day: pd.Timestamp) -> None:
        self._intraday_checked = set()
        lows, highs = _row(md["low"], day), _row(md["high"], day)
        for pos in sorted(self.positions.values(), key=lambda p: p.symbol):
            lo, hi = _val(lows, pos.symbol), _val(highs, pos.symbol)
            if pd.isna(lo) or pd.isna(hi):
                continue
            self._intraday_checked.add(pos.symbol)
            both = bool(lo <= pos.stop_loss and hi >= pos.take_profit)
            if lo <= pos.stop_loss:
                self._sell(
                    pos, day, pos.stop_loss, "stop_loss", phase="intraday", both=both, applied=both
                )
            elif hi >= pos.take_profit:
                self._sell(
                    pos,
                    day,
                    pos.take_profit,
                    "take_profit",
                    phase="intraday",
                    both=False,
                    applied=False,
                )

    def _close(self, md: MarketData, day: pd.Timestamp, i: int, is_last: bool) -> None:
        closes = _row(md["close"], day)
        for pos in sorted(self.positions.values(), key=lambda p: p.symbol):
            cl = _val(closes, pos.symbol)
            holding_day = i - pos.entry_index + 1
            if holding_day >= self.p.max_holding_days:
                if pd.isna(cl):
                    pos.time_exit_pending = True
                else:
                    self._sell(pos, day, cl, "time_exit", **self._close_flags(pos))
                    continue
            if is_last:
                if pd.isna(cl):
                    self._event(
                        day,
                        pos,
                        "end_of_test_untradable",
                        "no trade on the last day; position left open",
                    )
                else:
                    self._sell(pos, day, cl, "end_of_test", **self._close_flags(pos))

    def _value_and_plan(
        self,
        md: MarketData,
        day: pd.Timestamp,
        scores: pd.DataFrame,
        eligible: pd.DataFrame,
        is_last: bool,
    ) -> None:
        closes = _row(md["close"], day)
        for pos in self.positions.values():
            cl = _val(closes, pos.symbol)
            if pd.notna(cl):
                pos.last_close, pos.last_close_date = float(cl), day
        pos_value = sum(p.quantity * p.last_close for p in self.positions.values())
        equity = self.cash + pos_value
        self.equity_rows.append([day, equity, self.cash, pos_value, len(self.positions)])
        if is_last:
            return
        row = _row(scores, day)
        ok = _row(eligible, day)
        candidates: list[tuple[str, float]] = []
        for sym in row.index[row.notna()]:
            if sym in ok.index and bool(ok[sym]):
                candidates.append((str(sym), float(row[sym])))
        self.stats["signals"] += len(candidates)
        self.stats["signal_days"] += int(len(candidates) > 0)
        for sym, sc in sorted(candidates):
            if sym in self.positions:
                self.stats["signals_ignored_already_held"] += 1
                ignored = PendingOrder(sym, day, sc, 0, 0, math.nan)
                self._log_order(ignored, None, "ignored_already_held", 0, math.nan)
        ranked = sorted(
            ((sym, sc) for sym, sc in candidates if sym not in self.positions),
            key=lambda x: (-x[1], x[0]),
        )
        s, c = self.p.slippage_rate, self.p.commission_rate
        budget = self.p.max_position_pct * equity
        for rank, (sym, sc) in enumerate(ranked, start=1):
            est = _val(closes, sym) * (1 + s) * (1 + c)
            qty = self._round_lot(budget / est)
            order = PendingOrder(sym, day, sc, rank, qty, budget)
            if qty <= 0:
                self._log_order(order, None, "not_placed_unaffordable", 0, math.nan)
                continue
            self.pending.append(order)

    # ------------------------------------------------------------------ helpers

    def _reset(self) -> None:
        self.cash = float(self.p.initial_capital)
        self.positions = {}
        self.pending = []
        self.trades = []
        self.equity_rows = []
        self.order_rows = []
        self.unresolved = []
        self.status = "complete"
        self._i = 0
        self.stats = {"signals": 0, "signal_days": 0, "signals_ignored_already_held": 0}
        self._intraday_checked = set()

    def _round_lot(self, shares: float) -> int:
        lot = self.p.lot_size
        return int(math.floor(shares / lot + _EPS)) * lot

    def _close_flags(self, pos: Position) -> dict[str, Any]:
        if pos.symbol in self._intraday_checked:
            return {"phase": "close", "both": False, "applied": False}
        return {"phase": "close", "both": None, "applied": None}

    def _sell(
        self,
        pos: Position,
        day: pd.Timestamp,
        base_price: float,
        reason: str,
        *,
        phase: str,
        both: bool | None = None,
        applied: bool | None = None,
    ) -> None:
        fill = base_price * (1 - self.p.slippage_rate)
        gross = pos.quantity * fill
        commission = gross * self.p.commission_rate
        proceeds = gross - commission
        self.cash += proceeds
        pnl = proceeds - pos.entry_cost
        holding_days = self._i - pos.entry_index + 1
        self.trades.append(
            [
                pos.symbol,
                "long",
                pos.entry_date,
                pos.entry_price,
                day,
                fill,
                pos.quantity,
                pos.entry_commission + commission,
                pnl,
                pnl / pos.entry_cost,
                reason,
                holding_days,
                phase,
                both,
                applied,
            ]
        )
        del self.positions[pos.symbol]

    def _event(self, day: pd.Timestamp, pos: Position, event: str, reason: str) -> None:
        self.unresolved.append(
            [
                day,
                pos.symbol,
                event,
                pos.quantity,
                pos.entry_price,
                pos.last_close,
                pos.last_close_date,
                reason,
            ]
        )
        self.status = "needs_review"

    def _log_order(
        self,
        order: PendingOrder,
        exec_date: pd.Timestamp | None,
        status: str,
        qty: int,
        fill: float,
    ) -> None:
        self.order_rows.append(
            [
                order.signal_date,
                exec_date,
                order.symbol,
                order.score,
                order.rank,
                order.quantity,
                order.budget,
                status,
                qty,
                fill,
            ]
        )

    def _result(self) -> EngineResult:
        orders = pd.DataFrame(self.order_rows, columns=ORDER_COLUMNS)
        counts = orders["status"].value_counts().to_dict() if not orders.empty else {}
        self.stats["orders"] = {str(k): int(v) for k, v in counts.items()}
        both = [t for t in self.trades if t[TRADE_COLUMNS.index("intraday_both_touched")] is True]
        self.stats["intraday_both_touched"] = len(both)
        self.stats["stop_priority_applied"] = sum(
            1 for t in self.trades if t[TRADE_COLUMNS.index("stop_priority_applied")] is True
        )
        return EngineResult(
            trades=pd.DataFrame(self.trades, columns=TRADE_COLUMNS),
            equity_curve=pd.DataFrame(self.equity_rows, columns=EQUITY_COLUMNS),
            orders=orders,
            unresolved_events=pd.DataFrame(self.unresolved, columns=UNRESOLVED_EVENT_COLUMNS),
            status=self.status,
            stats=self.stats,
        )
