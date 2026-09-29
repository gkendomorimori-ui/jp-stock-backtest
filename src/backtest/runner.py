"""Run high_price_breakout end to end: data -> universe -> signals -> engine -> results."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest.config import BacktestConfig
from src.backtest.engine import BacktestEngine, EngineResult, ExecutionParams
from src.data.market_data import MarketData
from src.data.processing import ProcessedData
from src.evaluation.metrics import cagr, compute_metrics, max_drawdown
from src.evaluation.report import RunMetadata, make_run_id, save_run, unique_run_dir
from src.strategies.high_price_breakout import HighPriceBreakout
from src.universe import UniverseRules, eligibility

#: Recorded when the benchmark series was not loaded.
BENCHMARK_UNAVAILABLE_REASON = (
    "TOPIX (price index) is not in the processed data. /v2/indices/bars/daily/topix is not "
    "available on the J-Quants Free plan (HTTP 403, checked 2026-09-27); with the Light plan "
    "or above, download it (scripts/download_data.py --start ... --end ...) and re-run "
    "scripts/process_data.py."
)

BASE_NOTES: list[str] = [
    "Position size is reduced at the T+1 open using the open price: a backtest capital-"
    "constraint model, not an exact reproduction of a market-on-open order.",
    "Dividends are not included (price and costs only).",
    "Universe is point-in-time (master per day); delisted securities are included while "
    "they were listed.",
    "Intraday order of high/low is unknown; when both stop loss and take profit are "
    "touched on the same day, stop loss is assumed.",
]


#: Notes per execution model (docs/EXECUTION_MODEL.md).
MODEL_NOTES: dict[str, list[str]] = {
    "v1": [
        "Execution model v1 (diagnostic): intraday exits fill at the stop / take-profit level "
        "itself; any day with OHLC is treated as tradable.",
    ],
    "v2": [
        "Execution model v2: intraday take profit at the level rounded up to a valid tick; "
        "intraday stop at the level rounded down to a valid tick (optimistic: a fill further "
        "below cannot be seen in daily bars). Tick tables are point in time (TOPIX100 fine "
        "table before 2023-06-05, TOPIX500 from 2023-06-05, by the day's ScaleCat).",
        "Limit-up / limit-down non-fills are a strict daily-bar ASSUMPTION from the J-Quants "
        "UL/LL flags and OHLC, not confirmed non-fills; partial (pro-rata) fills are not "
        "modelled. UL/LL are used only for execution, never for signals, ranking or size.",
        "Triggered stop-loss and time-exit sell requests persist until executed; after an "
        "unfilled open sell the request is carried to the next trading day (same-day "
        "re-execution is not reproduced).",
    ],
}


def execution_params(
    config: BacktestConfig,
    strategy_params: dict[str, Any],
    model_version: str | None = None,
) -> ExecutionParams:
    """Combine common config and strategy parameters (nothing hard-coded).

    ``model_version`` overrides ``execution.model_version`` of the config (diagnostic runs).
    """
    missing = [
        n
        for n in (
            "initial_capital",
            "commission_rate",
            "slippage_rate",
            "lot_size",
            "maximum_positions",
        )
        if getattr(config, n) is None
    ]
    if missing:
        raise ValueError(f"config/backtest.yaml has undecided values: {missing}")
    if config.commission_model != "rate" or config.slippage_model != "rate":
        raise ValueError("only rate-based commission and slippage are implemented")
    if not config.long_only:
        raise ValueError("only long_only is implemented")
    assert config.initial_capital is not None and config.commission_rate is not None
    assert config.slippage_rate is not None and config.lot_size is not None
    assert config.maximum_positions is not None
    version = model_version or config.execution_model_version
    if version is None:
        raise ValueError("config/backtest.yaml execution.model_version is not set")
    return ExecutionParams(
        initial_capital=float(config.initial_capital),
        commission_rate=float(config.commission_rate),
        slippage_rate=float(config.slippage_rate),
        lot_size=int(config.lot_size),
        max_positions=int(config.maximum_positions),
        max_position_pct=float(strategy_params["max_position_pct"]),
        take_profit_pct=float(strategy_params["take_profit_pct"]),
        stop_loss_pct=float(strategy_params["stop_loss_pct"]),
        max_holding_days=int(strategy_params["max_holding_days"]),
        model_version=str(version),
    )


def run_high_price_breakout(
    data: ProcessedData,
    backtest_cfg: dict[str, Any],
    universe_cfg: dict[str, Any],
    strategy_spec: dict[str, Any],
    eval_start: date,
    eval_end: date,
    run_type: str,
    results_root: Path,
    data_source: str = "jquants_v2",
    notes: list[str] | None = None,
    execution_model: str | None = None,
) -> tuple[Path, EngineResult]:
    """Run the strategy on ``eval_start .. eval_end`` and save the results.

    Returns:
        (output directory, engine result)
    """
    config = BacktestConfig.from_dict(backtest_cfg)
    strategy = HighPriceBreakout(strategy_spec["parameters"])
    if (strategy_spec["name"], str(strategy_spec["version"])) != (strategy.name, strategy.version):
        raise ValueError("strategy YAML name/version does not match the implementation")
    rules = UniverseRules.from_config(universe_cfg)
    params = execution_params(config, strategy.params, execution_model)

    md = MarketData.from_processed(data)
    start, end = pd.Timestamp(eval_start), pd.Timestamp(eval_end)
    if start not in md.dates or end not in md.dates:
        raise ValueError("evaluation start/end must be trading days in the processed data")
    warmup_available = int(md.dates.get_loc(start))  # type: ignore[arg-type]
    if warmup_available < rules.window_days:
        raise ValueError(
            f"only {warmup_available} trading days before {eval_start}; "
            f"{rules.window_days} are needed for the warm-up"
        )

    scores = strategy.generate_signals(md)
    eligible = eligibility(md, rules)
    result = BacktestEngine(params).run(md, scores, eligible, start, end)

    equity = result.equity_curve.set_index("date")["equity"]
    if len(equity) >= 1:
        metrics: dict[str, Any] = compute_metrics(
            equity,
            result.trades["pnl"],
            periods_per_year=int(config.trading_days_per_year or 252),
            risk_free_rate=float(config.risk_free_rate or 0.0),
        )
        metrics["final_equity"] = float(equity.iloc[-1])
    else:
        metrics = {}

    benchmark, bench_equity = topix_benchmark(
        data.topix, result.equity_curve, params.initial_capital, metrics.get("total_return")
    )
    equity_curve = result.equity_curve.assign(
        benchmark_equity=pd.Series(bench_equity, index=result.equity_curve.index, dtype="float64")
    )

    run_notes = list(BASE_NOTES) + list(MODEL_NOTES[params.model_version]) + (notes or [])
    if result.status == "needs_review":
        run_notes.append("Run stopped or ended with unresolved events: results are NOT final.")
    metadata = RunMetadata(
        strategy_name=strategy.name,
        strategy_version=strategy.version,
        parameters=strategy.params,
        universe={"name": universe_cfg["universe"]["name"], "rules": _rules_dict(rules)},
        start_date=eval_start.isoformat(),
        end_date=eval_end.isoformat(),
        commission={"model": config.commission_model, "rate": config.commission_rate},
        slippage={"model": config.slippage_model, "rate": config.slippage_rate},
        data_source=data_source,
        run_type=run_type,
        status=result.status,
        dividends_included=False,
        benchmark=benchmark,
        notes=run_notes,
    )
    out_dir = unique_run_dir(results_root, make_run_id(strategy.name))
    sections: dict[str, Any] = {
        "execution_model_version": params.model_version,
        "stats": result.stats,
        "execution": params.__dict__,
        "data": {
            "first_date": md.dates[0].date().isoformat(),
            "last_date": md.dates[-1].date().isoformat(),
            "warmup_trading_days_before_start": warmup_available,
            "simulated_trading_days": len(result.equity_curve),
        },
    }
    save_run(
        out_dir,
        metadata,
        metrics,
        result.trades,
        equity_curve,
        result.unresolved_events,
        extra_sections=sections,
        extra_tables={"orders": result.orders, "sell_unfilled": result.sell_unfilled},
    )
    return out_dir, result


def topix_benchmark(
    topix: pd.DataFrame | None,
    equity_curve: pd.DataFrame,
    initial_capital: float,
    strategy_total_return: float | None,
) -> tuple[dict[str, Any], list[float | None]]:
    """TOPIX (price index) rebased to ``initial_capital`` on the first simulated day.

    Returns the benchmark section and the ``benchmark_equity`` column. When TOPIX is missing
    for any simulated day, every value is None and the reason is recorded.
    """
    section: dict[str, Any] = {
        "name": "TOPIX",
        "type": "price_index",
        "dividends_included": False,
        "rebased_on": None,
        "status": "unavailable",
        "unavailable_reason": None,
        "total_return": None,
        "cagr": None,
        "max_drawdown": None,
        "excess_total_return": None,
    }
    n = len(equity_curve)
    if n == 0:
        section["unavailable_reason"] = "no simulated days"
        return section, [None] * n
    if topix is None:
        section["unavailable_reason"] = BENCHMARK_UNAVAILABLE_REASON
        return section, [None] * n
    closes = topix.set_index("date")["close"]
    dates = pd.DatetimeIndex(pd.to_datetime(equity_curve["date"]))
    aligned = closes.reindex(dates)
    if aligned.isna().any():
        missing = [d.date().isoformat() for d in dates[aligned.isna().to_numpy()]][:5]
        section["unavailable_reason"] = f"TOPIX close missing on simulated days, e.g. {missing}"
        return section, [None] * n
    series = initial_capital * aligned / float(aligned.iloc[0])
    total = float(series.iloc[-1] / series.iloc[0] - 1.0)
    section.update(
        status="available",
        rebased_on=dates[0].date().isoformat(),
        total_return=total,
        cagr=cagr(series),
        max_drawdown=max_drawdown(series),
        excess_total_return=None
        if strategy_total_return is None
        else float(strategy_total_return) - total,
    )
    return section, [float(v) for v in series]


def _rules_dict(rules: UniverseRules) -> dict[str, Any]:
    return {
        "market_codes": sorted(rules.market_codes),
        "product_categories": sorted(rules.product_categories),
        "common_stock_code_suffix": rules.common_stock_code_suffix,
        "min_avg_turnover": rules.min_avg_turnover,
        "window_days": rules.window_days,
    }
