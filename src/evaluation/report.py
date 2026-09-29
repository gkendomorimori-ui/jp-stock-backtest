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

#: Allowed run types (docs/BACKTEST_RULES.md "実行の種類").
RUN_TYPES: tuple[str, ...] = ("smoke_test", "development", "final_evaluation", "holdout")

#: Allowed run statuses. ``needs_review`` means results are NOT final.
RUN_STATUSES: tuple[str, ...] = ("complete", "needs_review")

#: Columns of ``unresolved_events.csv`` (e.g. a held security was delisted and its
#: settlement could not be determined). See docs/BACKTEST_RULES.md.
UNRESOLVED_EVENT_COLUMNS: list[str] = [
    "date",
    "symbol",
    "event",
    "quantity",
    "entry_price",
    "last_valid_close",
    "last_valid_close_date",
    "reason",
]


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
    run_type: str = "smoke_test"
    status: str = "complete"
    dividends_included: bool = False
    benchmark: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate ``run_type`` and ``status``."""
        if self.run_type not in RUN_TYPES:
            raise ValueError(f"run_type must be one of {RUN_TYPES}, got {self.run_type!r}")
        if self.status not in RUN_STATUSES:
            raise ValueError(f"status must be one of {RUN_STATUSES}, got {self.status!r}")


def make_run_id(strategy_name: str, timestamp: datetime | None = None) -> str:
    """Return a sortable run id such as ``20260925T231400_my_strategy``."""
    ts = (timestamp or datetime.now()).strftime("%Y%m%dT%H%M%S")
    return f"{ts}_{strategy_name}"


def unique_run_dir(root: Path, run_id: str) -> Path:
    """``root / run_id``, with ``_2``, ``_3`` ... appended if it already exists.

    Two runs started within the same second must never overwrite each other.
    """
    out = root / run_id
    n = 2
    while out.exists():
        out = root / f"{run_id}_{n}"
        n += 1
    return out


def save_run(
    out_dir: Path,
    metadata: RunMetadata,
    metrics: dict[str, Any],
    trades: pd.DataFrame,
    equity_curve: pd.DataFrame,
    unresolved_events: pd.DataFrame | None = None,
    extra_sections: dict[str, Any] | None = None,
    extra_tables: dict[str, pd.DataFrame] | None = None,
) -> Path:
    """Write ``summary.json``, ``trades.csv`` and ``equity_curve.csv`` to ``out_dir``.

    If ``unresolved_events`` is non-empty, it is written to ``unresolved_events.csv`` and
    ``metadata.status`` must be ``needs_review``; the summary then marks metrics as not final.
    ``extra_sections`` are added to ``summary.json`` as top-level keys and
    ``extra_tables`` are written as ``<name>.csv``.

    Returns:
        The output directory.

    Raises:
        ValueError: If unresolved events exist but the status is not ``needs_review``.
    """
    has_unresolved = unresolved_events is not None and not unresolved_events.empty
    if has_unresolved and metadata.status != "needs_review":
        raise ValueError("unresolved events exist; metadata.status must be 'needs_review'")

    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "metadata": asdict(metadata),
        "metrics_final": metadata.status == "complete",
        "metrics": _json_safe(metrics),
    }
    for key, value in (extra_sections or {}).items():
        if key in summary:
            raise ValueError(f"extra section {key!r} clashes with a built-in key")
        summary[key] = _json_safe({key: value})[key]
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    trades.to_csv(out_dir / "trades.csv", index=False)
    equity_curve.to_csv(out_dir / "equity_curve.csv", index=False)
    if has_unresolved:
        assert unresolved_events is not None
        unresolved_events.to_csv(out_dir / "unresolved_events.csv", index=False)
    for name, table in (extra_tables or {}).items():
        table.to_csv(out_dir / f"{name}.csv", index=False)
    return out_dir


def _json_safe(values: dict[str, Any]) -> dict[str, Any]:
    """Recursively replace NaN/inf (invalid in strict JSON) with None / string markers."""
    return {key: _safe_value(value) for key, value in values.items()}


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _safe_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_safe_value(v) for v in value]
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, float) and math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return value
