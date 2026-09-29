"""Typed view of ``config/backtest.yaml``."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class BacktestConfig:
    """Common backtest settings.

    Fields default to ``None`` because many values are still TODO. Call
    :meth:`missing_fields` / :meth:`require_complete` before running a backtest
    so that undecided values are never silently replaced by guesses.
    """

    initial_capital: float | None = None
    commission_model: str = "rate"
    commission_rate: float | None = None
    commission_fixed: float | None = None
    commission_minimum: float | None = None
    slippage_model: str = "rate"
    slippage_rate: float | None = None
    slippage_ticks: float | None = None
    start_date: date | None = None
    end_date: date | None = None
    maximum_positions: int | None = None
    lot_size: int | None = None
    long_only: bool = True
    run_type: str = "smoke_test"
    dividends_included: bool = False
    execution_lag_days: int = 1
    execution_price: str | None = None
    execution_model_version: str | None = None
    trading_days_per_year: int | None = None
    risk_free_rate: float | None = None
    random_seed: int | None = None
    results_dir: str = "results/runs"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BacktestConfig:
        """Build a config from the nested structure of ``backtest.yaml``."""
        capital = raw.get("capital") or {}
        costs = raw.get("costs") or {}
        commission = costs.get("commission") or {}
        slippage = costs.get("slippage") or {}
        period = raw.get("period") or {}
        position = raw.get("position") or {}
        execution = raw.get("execution") or {}
        evaluation = raw.get("evaluation") or {}
        repro = raw.get("reproducibility") or {}
        output = raw.get("output") or {}
        run = raw.get("run") or {}
        return cls(
            initial_capital=capital.get("initial_capital"),
            commission_model=commission.get("model", "rate"),
            commission_rate=commission.get("rate"),
            commission_fixed=commission.get("fixed"),
            commission_minimum=commission.get("minimum"),
            slippage_model=slippage.get("model", "rate"),
            slippage_rate=slippage.get("rate"),
            slippage_ticks=slippage.get("ticks"),
            start_date=_to_date(period.get("start_date")),
            end_date=_to_date(period.get("end_date")),
            maximum_positions=position.get("maximum_positions"),
            lot_size=position.get("lot_size"),
            long_only=position.get("long_only", True),
            run_type=run.get("run_type", "smoke_test"),
            dividends_included=evaluation.get("dividends_included", False),
            execution_lag_days=execution.get("execution_lag_days", 1),
            execution_price=execution.get("execution_price"),
            execution_model_version=execution.get("model_version"),
            trading_days_per_year=evaluation.get("trading_days_per_year"),
            risk_free_rate=evaluation.get("risk_free_rate"),
            random_seed=repro.get("random_seed"),
            results_dir=output.get("results_dir", "results/runs"),
        )

    def missing_fields(self) -> list[str]:
        """Return names of settings that must be decided before a real run."""
        required = [
            "initial_capital",
            "maximum_positions",
            "lot_size",
            "start_date",
            "end_date",
            "trading_days_per_year",
            "risk_free_rate",
        ]
        missing = [name for name in required if getattr(self, name) is None]
        if self.commission_model == "rate" and self.commission_rate is None:
            missing.append("commission_rate")
        if self.commission_model == "fixed" and self.commission_fixed is None:
            missing.append("commission_fixed")
        if self.slippage_model == "rate" and self.slippage_rate is None:
            missing.append("slippage_rate")
        if self.slippage_model == "ticks" and self.slippage_ticks is None:
            missing.append("slippage_ticks")
        return missing

    def require_complete(self) -> None:
        """Raise ``ValueError`` listing every undecided (TODO) setting."""
        missing = self.missing_fields()
        if missing:
            raise ValueError(f"backtest.yaml has undecided (TODO) values: {missing}")
        if self.execution_lag_days < 1:
            raise ValueError("execution_lag_days must be >= 1 to avoid look-ahead bias")


def _to_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
