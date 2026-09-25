"""Collect ``summary.json`` from runs and write a comparison table.

Reads every ``results/runs/*/summary.json`` and writes
``results/comparison/comparison.csv`` with one row per run.

Usage:
    python scripts/compare_strategies.py [--runs results/runs] [--out results/comparison]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", default="results/runs")
    parser.add_argument("--out", default="results/comparison")
    return parser.parse_args()


def collect_summaries(runs_dir: Path) -> pd.DataFrame:
    """Flatten each run's metadata and metrics into one row."""
    rows: list[dict[str, Any]] = []
    for path in sorted(runs_dir.glob("*/summary.json")):
        summary = json.loads(path.read_text(encoding="utf-8"))
        meta = summary.get("metadata", {})
        row: dict[str, Any] = {
            "run_id": path.parent.name,
            "strategy_name": meta.get("strategy_name"),
            "strategy_version": meta.get("strategy_version"),
            "start_date": meta.get("start_date"),
            "end_date": meta.get("end_date"),
            "git_commit": meta.get("git_commit"),
        }
        row.update(summary.get("metrics", {}))
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> int:
    """Entry point."""
    args = parse_args()
    table = collect_summaries(Path(args.runs))
    if table.empty:
        print(f"no summary.json found under {args.runs}")
        return 0
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "comparison.csv"
    table.to_csv(out, index=False)
    print(f"wrote {len(table)} runs to {out}")
    # TODO: yearly breakdown, per-symbol contribution, regime split, parameter sensitivity.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
