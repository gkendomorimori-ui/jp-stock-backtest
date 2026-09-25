"""Commission and slippage model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class CostModel:
    """Apply commission and slippage to executions.

    All values come from configuration; nothing is hard-coded here.

    Attributes:
        commission_rate: Commission as a fraction of traded value (e.g. 0.001).
        commission_fixed: Fixed commission per order in JPY (used if rate is None).
        commission_minimum: Minimum commission per order in JPY.
        slippage_rate: Adverse price move as a fraction of price.
    """

    commission_rate: float | None = None
    commission_fixed: float | None = None
    commission_minimum: float = 0.0
    slippage_rate: float = 0.0

    def execution_price(self, price: float, side: Side) -> float:
        """Return the fill price after adverse slippage (buy higher, sell lower)."""
        if price <= 0:
            raise ValueError("price must be positive")
        if side == "buy":
            return price * (1.0 + self.slippage_rate)
        if side == "sell":
            return price * (1.0 - self.slippage_rate)
        raise ValueError(f"unknown side: {side}")

    def commission(self, traded_value: float) -> float:
        """Return the commission (JPY) for one order of ``traded_value`` JPY."""
        if traded_value < 0:
            raise ValueError("traded_value must be non-negative")
        if self.commission_rate is not None:
            fee = traded_value * self.commission_rate
        elif self.commission_fixed is not None:
            fee = self.commission_fixed
        else:
            fee = 0.0
        return max(fee, self.commission_minimum)
