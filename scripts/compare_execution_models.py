"""Describe the differences between a v1 run and a v2 run of the same period and settings.

    python scripts/run_backtest.py --period development --execution-model v1
    python scripts/run_backtest.py --period development            # v2 (config default)
    python scripts/compare_execution_models.py results/runs/<v1_run> results/runs/<v2_run>

Writes ``model_comparison.json`` and ``model_comparison_trades.csv`` into the v2 run
directory. The total PnL difference is NOT attributed to single causes (later trades change
once one exit changes); v1 is diagnostic, v2 is the base model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.model_diff import ComparisonError, compare_models  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(__doc__)
        return 2
    v1_dir, v2_dir = Path(args[0]), Path(args[1])
    try:
        summary, table = compare_models(v1_dir, v2_dir)
    except ComparisonError as e:
        print(f"ERROR: {e}")
        return 1
    (v2_dir / "model_comparison.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    table.to_csv(v2_dir / "model_comparison_trades.csv", index=False)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"saved: {v2_dir / 'model_comparison.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
