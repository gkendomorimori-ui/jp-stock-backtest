"""Event-driven daily backtest engine (long only, daily bars).

Implements the execution model of strategies/high_price_breakout.md (v0.2.0) and the
common rules of docs/BACKTEST_RULES.md. Processing order for each trading day D:

    0. before the open: split / reverse-split adjustment; delisting check of holdings
    1. open sells  (positions held before D): carried sell requests, gap stop loss /
       take profit
    2. open buys   (orders created at the close of D-1), with cash and slots after step 1
    3. intraday stop loss / take profit (stop loss first when both are touched)
    4. close sells: time exit on holding day ``max_holding_days``; end of test
    5. valuation at the close; orders for D+1

Cash and slots freed in steps 3-4 are never used by step 2 of the same day.
Prices used here are ACTUAL prices; share counts are actual share counts.

Execution model versions (docs/EXECUTION_MODEL.md):

* ``v1`` -- intraday exits fill at the stop-loss / take-profit LEVEL itself; every day with
  OHLC is tradable. Kept for the diagnostic v1/v2 comparison only.
* ``v2`` -- base model. Intraday take profit fills at the level rounded UP to a valid tick
  (limit order price); intraday stop loss fills at the level rounded DOWN to a valid tick
  (approximation of the first trade after the stop is triggered; a fill further below it
  cannot be seen in daily bars). Tick tables are point in time (src/backtest/ticks.py).
  With the J-Quants limit flags (a strict daily-bar assumption, not a confirmed non-fill):
  no buy when the stock opens at the upper limit (UL=1 and open == high); no sell at the
  open when it opens at the lower limit (LL=1 and open == low); no intraday stop when the
  whole day is at the lower limit (LL=1 and high == low); no close sell when it closes at
  the lower limit (LL=1 and close == low). A triggered stop loss or a reached time exit
  becomes a persistent SELL REQUEST: it is executed at the next tradable open even if the
  price recovers, and cash / the slot are released only when it is actually sold. After an
  unfilled open sell nothing more is tried for that position on the same day (same-day
  re-execution is not reproduced).

Price fields of an exit (trades.csv):

* ``trigger_level`` -- stop-loss / take-profit level that triggered the exit (not a price)
* ``order_price``   -- limit price of the take-profit order (v2 only; stops are market)
* ``base_price``    -- modelled traded price before slippage (open, close, or tick price)
* ``exit_price``    -- ``base_price`` after slippage: a MODEL effective price, not an
  observed fill
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from src.backtest.ticks import ceil_to_tick, floor_to_tick, tick_class, tick_size
from src.data.market_data import MarketData
from src.evaluation.report import UNRESOLVED_EVENT_COLUMNS

EXECUTION_MODEL_VERSIONS: tuple[str, ...] = ("v1", "v2")

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
    "trigger_date",
    "trigger_phase",
    "trigger_level",
    "order_price",
    "base_price",
    "tick_class",
    "tick_size",
    "tick_rounded",
    "carried_days",
    "last_unfilled_cause",
]
"""Diagnostic columns (they do not affect execution):

* ``exit_phase``: ``open`` / ``intraday`` / ``close`` -- when the exit was executed.
* ``intraday_both_touched``: on the TRIGGER day, did the intraday range (low <= stop level
  AND high >= take-profit level) reach BOTH levels? Empty when the exit was triggered at the
  open. ``False`` for close exits whose intraday check ran and touched neither level.
* ``stop_priority_applied``: ``True`` only when the stop-loss-first rule actually decided the
  outcome (both levels touched intraday -> exited by stop loss). Empty for open triggers.
* ``trigger_date`` / ``trigger_phase``: when the exit condition was met (sell request
  created). Differs from exit_date / exit_phase when the sell was carried forward.
* ``trigger_level``, ``order_price``, ``base_price``: see the module docstring.
* ``tick_class`` / ``tick_size``: tick table used to round a level (v2 intraday exits);
  ``unknown`` when the scale category could not be determined (recorded as needing review).
* ``tick_rounded``: base_price differs from trigger_level (v2 intraday exits).
* ``carried_days``: trading days from trigger_date to exit_date (0 = same day).
* ``last_unfilled_cause``: why the last attempt before the fill did not execute
  (``limit_down_open``, ``limit_down_all_day``, ``limit_down_close``, ``no_trade``).
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
    "base_price",
]

#: Columns of sell_unfilled.csv (v2: one row per sell attempt that did not execute).
SELL_UNFILLED_COLUMNS: list[str] = [
    "date",
    "symbol",
    "phase",
    "cause",
    "request_reason",
    "trigger_date",
    "quantity",
]

_EPS = 1e-9


def _val(row: pd.Series, symbol: str) -> float:
    """Float value of ``row[symbol]``; NaN if the symbol is absent or the value is null."""
    if symbol not in row.index:
        return math.nan
    value = row[symbol]
    return math.nan if pd.isna(value) else float(value)


def _obj(row: pd.Series, symbol: str) -> object:
    """Raw value of ``row[symbol]``; None if absent or null."""
    if symbol not in row.index:
        return None
    value = row[symbol]
    return None if pd.isna(value) else value


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


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


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
    model_version: str

    def __post_init__(self) -> None:
        """Reject unknown execution model versions."""
        if self.model_version not in EXECUTION_MODEL_VERSIONS:
            raise ValueError(f"unknown execution model version: {self.model_version!r}")


@dataclass
class SellRequest:
    """A triggered exit that has not been executed yet (persists until sold)."""

    reason: str
    trigger_date: pd.Timestamp
    trigger_index: int
    trigger_phase: str
    trigger_level: float
    both: bool | None
    applied: bool | None
    last_unfilled_cause: str = ""


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
    sell_request: SellRequest | None = None


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
    sell_unfilled: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(columns=SELL_UNFILLED_COLUMNS)
    )


class BacktestEngine:
    """Run one strategy's signals through the execution model."""

    def __init__(self, params: ExecutionParams) -> None:
        """Store execution parameters."""
        self.p = params
        self.v2 = params.model_version == "v2"
        self.cash = 0.0
        self.positions: dict[str, Position] = {}
        self.pending: list[PendingOrder] = []
        self.trades: list[list[Any]] = []
        self.equity_rows: list[list[Any]] = []
        self.order_rows: list[list[Any]] = []
        self.unfilled_rows: list[list[Any]] = []
        self.unresolved: list[list[Any]] = []
        self.status = "complete"
        self.stats: dict[str, Any] = {}
        self._i = 0
        self._intraday_checked: set[str] = set()
        self._blocked_today: set[str] = set()

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
        if self.v2 and (
            not md.has_fields("upper_limit", "lower_limit") or md.scale_category is None
        ):
            raise DataError(
                "execution model v2 needs upper_limit / lower_limit (J-Quants UL / LL) and "
                "scale_category (ScaleCat) in the processed data -- re-run "
                "scripts/process_data.py"
            )
        self._reset()
        start_i, end_i = _loc(dates, eval_start), _loc(dates, eval_end)
        for i in range(start_i, end_i + 1):
            day = dates[i]
            self._i = i
            self._blocked_today = set()
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
                if pos.sell_request is not None and not math.isnan(pos.sell_request.trigger_level):
                    pos.sell_request.trigger_level *= f
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
        return not self.unresolved or not any(
            r[2] in ("delisted", "split_fractional_shares") for r in self.unresolved
        )

    def _open_sells(self, md: MarketData, day: pd.Timestamp) -> None:
        opens, lows = _row(md["open"], day), _row(md["low"], day)
        for pos in sorted(self.positions.values(), key=lambda p: p.symbol):
            o = _val(opens, pos.symbol)
            req = pos.sell_request
            if pd.isna(o):
                if req is not None:
                    self._unfilled(day, pos, "open", "no_trade")
                continue

            def locked(pos: Position = pos, o: float = o) -> bool:
                return self.v2 and self._limit_locked(
                    md, day, pos.symbol, "lower_limit", o, _val(lows, pos.symbol), pos.quantity
                )

            if req is not None:
                if locked():
                    self._unfilled(day, pos, "open", "limit_down_open")
                    self._blocked_today.add(pos.symbol)
                    continue
                self._sell(pos, day, o, phase="open")
            elif o <= pos.stop_loss:
                pos.sell_request = SellRequest(
                    "stop_loss_open", day, self._i, "open", pos.stop_loss, None, None
                )
                if locked():
                    self._unfilled(day, pos, "open", "limit_down_open")
                    self._blocked_today.add(pos.symbol)
                    continue
                self._sell(pos, day, o, phase="open")
            elif o >= pos.take_profit:
                pos.sell_request = SellRequest(
                    "take_profit_open", day, self._i, "open", pos.take_profit, None, None
                )
                order_price = self._tp_order_price(md, day, pos) if self.v2 else math.nan
                self._sell(pos, day, o, phase="open", order_price=order_price)

    def _open_buys(self, md: MarketData, day: pd.Timestamp, i: int) -> None:
        opens, highs = _row(md["open"], day), _row(md["high"], day)
        s, c, lot = self.p.slippage_rate, self.p.commission_rate, self.p.lot_size
        for order in sorted(self.pending, key=lambda o: o.rank):
            status, qty, fill, base = "filled", 0, math.nan, math.nan
            o = _val(opens, order.symbol)
            if len(self.positions) >= self.p.max_positions:
                status = "cancelled_no_slot"
            elif order.symbol in self.positions:
                status = "cancelled_already_held"
            elif not md.is_listed(day, order.symbol) or pd.isna(o):
                status = "cancelled_not_tradable"
            elif self.v2 and self._limit_locked(
                md, day, order.symbol, "upper_limit", o, _val(highs, order.symbol), order.quantity
            ):
                status = "cancelled_limit_up"
                self.stats["buy_unfilled_limit_up"] += 1
            else:
                base = o
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
            # as in v1: fill / base are also logged for cancelled_insufficient_funds (the
            # price that was tried); NaN when no price was tried
            self._log_order(order, day, status, qty, fill, base)
        self.pending = []

    def _intraday(self, md: MarketData, day: pd.Timestamp) -> None:
        self._intraday_checked = set()
        lows, highs = _row(md["low"], day), _row(md["high"], day)
        for pos in sorted(self.positions.values(), key=lambda p: p.symbol):
            if pos.sell_request is not None or pos.symbol in self._blocked_today:
                continue
            lo, hi = _val(lows, pos.symbol), _val(highs, pos.symbol)
            if pd.isna(lo) or pd.isna(hi):
                continue
            self._intraday_checked.add(pos.symbol)
            both = bool(lo <= pos.stop_loss and hi >= pos.take_profit)
            if lo <= pos.stop_loss:
                pos.sell_request = SellRequest(
                    "stop_loss", day, self._i, "intraday", pos.stop_loss, both, both
                )
                if not self.v2:
                    self._sell(pos, day, pos.stop_loss, phase="intraday")
                    continue
                if self._limit_locked(md, day, pos.symbol, "lower_limit", hi, lo, pos.quantity):
                    self._unfilled(day, pos, "intraday", "limit_down_all_day")
                    continue
                base, cls, tick = self._tick_price(md, day, pos, pos.stop_loss, "floor")
                self._sell(pos, day, base, phase="intraday", tick=(cls, tick))
            elif hi >= pos.take_profit:
                pos.sell_request = SellRequest(
                    "take_profit", day, self._i, "intraday", pos.take_profit, False, False
                )
                if not self.v2:
                    self._sell(pos, day, pos.take_profit, phase="intraday")
                    continue
                base, cls, tick = self._tick_price(md, day, pos, pos.take_profit, "ceil")
                self._sell(pos, day, base, phase="intraday", order_price=base, tick=(cls, tick))

    def _close(self, md: MarketData, day: pd.Timestamp, i: int, is_last: bool) -> None:
        closes, lows = _row(md["close"], day), _row(md["low"], day)
        for pos in sorted(self.positions.values(), key=lambda p: p.symbol):
            cl = _val(closes, pos.symbol)
            waiting = pos.sell_request is not None or pos.symbol in self._blocked_today
            holding_day = i - pos.entry_index + 1
            if not waiting and holding_day >= self.p.max_holding_days:
                both, applied = self._close_flags(pos)
                pos.sell_request = SellRequest(
                    "time_exit", day, self._i, "close", math.nan, both, applied
                )
                if pd.isna(cl):
                    self._unfilled(day, pos, "close", "no_trade")
                elif self.v2 and self._limit_locked(
                    md, day, pos.symbol, "lower_limit", cl, _val(lows, pos.symbol), pos.quantity
                ):
                    self._unfilled(day, pos, "close", "limit_down_close")
                else:
                    self._sell(pos, day, cl, phase="close")
                    continue
            if not is_last:
                continue
            if pos.sell_request is not None or pos.symbol in self._blocked_today:
                req = pos.sell_request
                self._event(
                    day,
                    pos,
                    "end_of_test_untradable",
                    "sell request not executed by the last day "
                    f"(reason {req.reason if req else '-'}, "
                    f"last cause {req.last_unfilled_cause if req else '-'}); position left open",
                )
            elif pd.isna(cl):
                self._event(
                    day,
                    pos,
                    "end_of_test_untradable",
                    "no trade on the last day; position left open",
                )
            elif self.v2 and self._limit_locked(
                md, day, pos.symbol, "lower_limit", cl, _val(lows, pos.symbol), pos.quantity
            ):
                self._record_unfilled(day, pos, "close", "limit_down_close", "end_of_test", day)
                self._event(
                    day,
                    pos,
                    "end_of_test_untradable",
                    "closed at the lower price limit on the last day; position left open",
                )
            else:
                both, applied = self._close_flags(pos)
                pos.sell_request = SellRequest(
                    "end_of_test", day, self._i, "close", math.nan, both, applied
                )
                self._sell(pos, day, cl, phase="close")

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
                self._log_order(ignored, None, "ignored_already_held", 0, math.nan, math.nan)
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
                self._log_order(order, None, "not_placed_unaffordable", 0, math.nan, math.nan)
                continue
            self.pending.append(order)

    # ------------------------------------------------------------------ v2 helpers

    def _limit_locked(
        self,
        md: MarketData,
        day: pd.Timestamp,
        symbol: str,
        flag_field: str,
        price: float,
        extreme: float,
        quantity: int,
    ) -> bool:
        """Flag == 1 and ``price`` equals the day's extreme (high for UL, low for LL).

        A missing flag is NOT treated as 0: it is recorded as needing review and the
        order is then treated as executable (no evidence of a lock).
        """
        flag = _val(_row(md[flag_field], day), symbol)
        if math.isnan(flag):
            self.stats["limit_flag_missing"] += 1
            self._event_raw(
                day,
                symbol,
                "limit_flag_missing",
                quantity,
                math.nan,
                math.nan,
                None,
                f"{flag_field} is missing on a traded day; lock could not be judged "
                "(treated as not locked)",
            )
            return False
        return flag == 1.0 and not math.isnan(extreme) and _same(price, extreme)

    def _tick_price(
        self, md: MarketData, day: pd.Timestamp, pos: Position, level: float, how: str
    ) -> tuple[float, str, float]:
        """Round ``level`` to the tick grid of ``pos.symbol`` on ``day``.

        Unknown class -> recorded as needing review; the level itself is used (v1 value).
        """
        assert md.scale_category is not None
        cls = tick_class(day, _obj(_row(md.scale_category, day), pos.symbol))
        if cls is None:
            self.stats["tick_class_unknown"] += 1
            self._event(
                day,
                pos,
                "tick_class_unknown",
                "scale category / tick regime unknown; level used without tick rounding",
            )
            return level, "unknown", math.nan
        price = ceil_to_tick(level, cls) if how == "ceil" else floor_to_tick(level, cls)
        return price, cls, tick_size(level, cls)

    def _tp_order_price(self, md: MarketData, day: pd.Timestamp, pos: Position) -> float:
        assert md.scale_category is not None
        cls = tick_class(day, _obj(_row(md.scale_category, day), pos.symbol))
        return math.nan if cls is None else ceil_to_tick(pos.take_profit, cls)

    def _unfilled(self, day: pd.Timestamp, pos: Position, phase: str, cause: str) -> None:
        req = pos.sell_request
        assert req is not None
        req.last_unfilled_cause = cause
        self._record_unfilled(day, pos, phase, cause, req.reason, req.trigger_date)

    def _record_unfilled(
        self,
        day: pd.Timestamp,
        pos: Position,
        phase: str,
        cause: str,
        reason: str,
        trigger_date: pd.Timestamp,
    ) -> None:
        self.unfilled_rows.append(
            [day, pos.symbol, phase, cause, reason, trigger_date, pos.quantity]
        )

    # ------------------------------------------------------------------ helpers

    def _reset(self) -> None:
        self.cash = float(self.p.initial_capital)
        self.positions = {}
        self.pending = []
        self.trades = []
        self.equity_rows = []
        self.order_rows = []
        self.unfilled_rows = []
        self.unresolved = []
        self.status = "complete"
        self._i = 0
        self.stats = {
            "execution_model_version": self.p.model_version,
            "signals": 0,
            "signal_days": 0,
            "signals_ignored_already_held": 0,
            "buy_unfilled_limit_up": 0,
            "limit_flag_missing": 0,
            "tick_class_unknown": 0,
        }
        self._intraday_checked = set()
        self._blocked_today = set()

    def _round_lot(self, shares: float) -> int:
        lot = self.p.lot_size
        return int(math.floor(shares / lot + _EPS)) * lot

    def _close_flags(self, pos: Position) -> tuple[bool | None, bool | None]:
        if pos.symbol in self._intraday_checked:
            return False, False
        return None, None

    def _sell(
        self,
        pos: Position,
        day: pd.Timestamp,
        base_price: float,
        *,
        phase: str,
        order_price: float = math.nan,
        tick: tuple[str, float] | None = None,
    ) -> None:
        req = pos.sell_request
        assert req is not None
        fill = base_price * (1 - self.p.slippage_rate)
        gross = pos.quantity * fill
        commission = gross * self.p.commission_rate
        proceeds = gross - commission
        self.cash += proceeds
        pnl = proceeds - pos.entry_cost
        holding_days = self._i - pos.entry_index + 1
        cls, size = tick if tick is not None else ("", math.nan)
        rounded: bool | None = None
        if tick is not None:
            rounded = not _same(base_price, req.trigger_level)
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
                req.reason,
                holding_days,
                phase,
                req.both,
                req.applied,
                req.trigger_date,
                req.trigger_phase,
                req.trigger_level,
                order_price,
                base_price,
                cls,
                size,
                rounded,
                self._i - req.trigger_index,
                req.last_unfilled_cause,
            ]
        )
        del self.positions[pos.symbol]

    def _event(self, day: pd.Timestamp, pos: Position, event: str, reason: str) -> None:
        self._event_raw(
            day,
            pos.symbol,
            event,
            pos.quantity,
            pos.entry_price,
            pos.last_close,
            pos.last_close_date,
            reason,
        )

    def _event_raw(
        self,
        day: pd.Timestamp,
        symbol: str,
        event: str,
        quantity: int,
        entry_price: float,
        last_close: float,
        last_close_date: pd.Timestamp | None,
        reason: str,
    ) -> None:
        self.unresolved.append(
            [day, symbol, event, quantity, entry_price, last_close, last_close_date, reason]
        )
        self.status = "needs_review"

    def _log_order(
        self,
        order: PendingOrder,
        exec_date: pd.Timestamp | None,
        status: str,
        qty: int,
        fill: float,
        base: float,
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
                base,
            ]
        )

    def _result(self) -> EngineResult:
        orders = pd.DataFrame(self.order_rows, columns=ORDER_COLUMNS)
        trades = pd.DataFrame(self.trades, columns=TRADE_COLUMNS)
        unfilled = pd.DataFrame(self.unfilled_rows, columns=SELL_UNFILLED_COLUMNS)
        counts = orders["status"].value_counts().to_dict() if not orders.empty else {}
        self.stats["orders"] = {str(k): int(v) for k, v in counts.items()}
        self.stats["intraday_both_touched"] = int((trades["intraday_both_touched"] == True).sum())  # noqa: E712
        self.stats["stop_priority_applied"] = int((trades["stop_priority_applied"] == True).sum())  # noqa: E712
        rounded = trades[trades["tick_rounded"] == True]  # noqa: E712
        self.stats["tick_rounded"] = {
            "take_profit": int((rounded["exit_reason"] == "take_profit").sum()),
            "stop_loss": int((rounded["exit_reason"] == "stop_loss").sum()),
        }
        self.stats["sell_unfilled"] = {
            str(k): int(v) for k, v in unfilled["cause"].value_counts().to_dict().items()
        }
        self.stats["sells_executed_after_carry"] = int((trades["carried_days"] > 0).sum())
        self.stats["sell_requests_open_at_end"] = sum(
            1 for p in self.positions.values() if p.sell_request is not None
        )
        return EngineResult(
            trades=trades,
            equity_curve=pd.DataFrame(self.equity_rows, columns=EQUITY_COLUMNS),
            orders=orders,
            unresolved_events=pd.DataFrame(self.unresolved, columns=UNRESOLVED_EVENT_COLUMNS),
            status=self.status,
            stats=self.stats,
            sell_unfilled=unfilled,
        )
