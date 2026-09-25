"""Performance metrics.

All functions are pure and depend only on an equity curve and/or per-trade PnL,
not on how they were produced. Annualization constants are passed in explicitly
because their values are still TODO in ``config/backtest.yaml``.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def total_return(equity: pd.Series) -> float:
    """Return ``equity[-1] / equity[0] - 1``."""
    _check_equity(equity)
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def cagr(equity: pd.Series) -> float:
    """Compound annual growth rate using calendar days (365.25 per year).

    ``equity`` must be indexed by date.
    """
    _check_equity(equity)
    index = pd.DatetimeIndex(equity.index)
    days = (index[-1] - index[0]).days
    if days <= 0:
        return math.nan
    return float((equity.iloc[-1] / equity.iloc[0]) ** (365.25 / days) - 1.0)


def max_drawdown(equity: pd.Series) -> float:
    """Return the maximum drawdown as a non-positive fraction (e.g. ``-0.25``)."""
    _check_equity(equity)
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def sharpe_ratio(returns: pd.Series, periods_per_year: int, risk_free_rate: float) -> float:
    """Annualized Sharpe ratio of periodic returns (sample std, ddof=1)."""
    excess = returns.dropna() - risk_free_rate / periods_per_year
    if len(excess) < 2:
        return math.nan
    std = excess.std(ddof=1)
    if std == 0 or np.isnan(std):
        return math.nan
    return float(excess.mean() / std * math.sqrt(periods_per_year))


def sortino_ratio(returns: pd.Series, periods_per_year: int, risk_free_rate: float) -> float:
    """Annualized Sortino ratio (downside deviation over all periods, target = rf)."""
    excess = returns.dropna() - risk_free_rate / periods_per_year
    if len(excess) < 2:
        return math.nan
    downside = np.sqrt((np.minimum(excess, 0.0) ** 2).mean())
    if downside == 0:
        return math.nan
    return float(excess.mean() / downside * math.sqrt(periods_per_year))


def trade_stats(pnl: pd.Series) -> dict[str, float]:
    """Per-trade statistics from a Series of net PnL (after costs), one row per trade.

    Returns:
        ``profit_factor``, ``win_rate``, ``number_of_trades``, ``average_profit``
        (mean of winning trades), ``average_loss`` (mean of losing trades, negative)
        and ``expectancy`` (mean PnL per trade).
    """
    pnl = pnl.dropna()
    n = len(pnl)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    if n == 0:
        profit_factor = math.nan
    elif gross_loss == 0:
        profit_factor = math.inf if gross_profit > 0 else math.nan
    else:
        profit_factor = gross_profit / gross_loss
    return {
        "profit_factor": profit_factor,
        "win_rate": float(len(wins) / n) if n else math.nan,
        "number_of_trades": float(n),
        "average_profit": float(wins.mean()) if len(wins) else math.nan,
        "average_loss": float(losses.mean()) if len(losses) else math.nan,
        "expectancy": float(pnl.mean()) if n else math.nan,
    }


def compute_metrics(
    equity: pd.Series,
    trade_pnl: pd.Series,
    periods_per_year: int,
    risk_free_rate: float,
) -> dict[str, Any]:
    """Compute every metric required by PROJECT_CONTEXT.md.

    Args:
        equity: Equity curve indexed by date.
        trade_pnl: Net PnL per closed trade.
        periods_per_year: Trading days per year used for annualization.
        risk_free_rate: Annual risk-free rate.
    """
    returns = equity.pct_change().dropna()
    result: dict[str, Any] = {
        "total_return": total_return(equity),
        "cagr": cagr(equity),
        "max_drawdown": max_drawdown(equity),
        "sharpe_ratio": sharpe_ratio(returns, periods_per_year, risk_free_rate),
        "sortino_ratio": sortino_ratio(returns, periods_per_year, risk_free_rate),
    }
    result.update(trade_stats(trade_pnl))
    result["number_of_trades"] = int(result["number_of_trades"])
    return result


def _check_equity(equity: pd.Series) -> None:
    if len(equity) < 1:
        raise ValueError("equity curve is empty")
    if (equity <= 0).any():
        raise ValueError("equity must be strictly positive")
