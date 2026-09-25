"""Persist backtest results in machine-readable form."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.utils.git import get_commit_hash


@dataclass
class RunMetadata:
    """Metadata required for reproducibility (docs/BACKTEST_RULES.md)."""

    strategy_name: str
    strategy_version: str
    parameters: dict[str, Any]
    universe: dict[str, Any]
    start_date: str
    end_date: str
    commission: dict[str, Any]
    slippage: dict[str, Any]
    execution_timestamp: str = field(
        default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds")
    )
    git_commit: str | None = field(default_factory=get_commit_hash)
    data_source: str | None = None
    random_seed: int | None = None


def make_run_id(strategy_name: str, timestamp: datetime | None = None) -> str:
    """Return a sortable run id such as ``20260925T231400_my_strategy``."""
    ts = (timestamp or datetime.now()).strftime("%Y%m%dT%H%M%S")
    return f"{ts}_{strategy_name}"


def save_run(
    out_dir: Path,
    metadata: RunMetadata,
    metrics: dict[str, Any],
    trades: pd.DataFrame,
    equity_curve: pd.DataFrame,
) -> Path:
    """Write ``summary.json``, ``trades.csv`` and ``equity_curve.csv`` to ``out_dir``.

    Returns:
        The output directory.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {"metadata": asdict(metadata), "metrics": _json_safe(metrics)}
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trades.to_csv(out_dir / "trades.csv", index=False)
    equity_curve.to_csv(out_dir / "equity_curve.csv", index=False)
    return out_dir


def _json_safe(values: dict[str, Any]) -> dict[str, Any]:
    """Replace NaN/inf (invalid in strict JSON) with None / string markers."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, float) and math.isnan(value):
            out[key] = None
        elif isinstance(value, float) and math.isinf(value):
            out[key] = "inf" if value > 0 else "-inf"
        else:
            out[key] = value
    return out
