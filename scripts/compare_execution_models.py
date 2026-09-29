"""Describe the differences between a v1 run and a v2 run of the same period and settings.

    python scripts/run_backtest.py --period development --execution-model v1
    python scripts/run_backtest.py --period development            # v2 (config default)
    python scripts/compare_execution_models.py results/runs/<v1_run> results/runs/<v2_run>
    python scripts/compare_execution_models.py --latest     # newest v2 run and the newest
                                                             # v1 run of the same period

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

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def latest_pair(runs_root: Path) -> tuple[Path, Path]:
    """Newest v2 run, and the newest v1 run with the same period and run type."""
    runs: list[tuple[Path, dict[str, object]]] = []
    for f in sorted(runs_root.glob("*/summary.json")):
        s = json.loads(f.read_text(encoding="utf-8"))
        runs.append((f.parent, s))
    v2 = [(d, s) for d, s in runs if s.get("execution_model_version") == "v2"]
    if not v2:
        raise ComparisonError("no v2 run found")
    v2_dir, s2 = v2[-1]
    m2 = s2["metadata"]
    assert isinstance(m2, dict)
    key = (m2["start_date"], m2["end_date"], m2["run_type"])
    v1 = [
        d
        for d, s in runs
        if s.get("execution_model_version") == "v1"
        and isinstance(s["metadata"], dict)
        and (s["metadata"]["start_date"], s["metadata"]["end_date"], s["metadata"]["run_type"])
        == key
    ]
    if not v1:
        raise ComparisonError(f"no v1 run for {key}")
    return v1[-1], v2_dir


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = sys.argv[1:] if argv is None else argv
    try:
        if args == ["--latest"]:
            v1_dir, v2_dir = latest_pair(PROJECT_ROOT / "results" / "runs")
        elif len(args) == 2:
            v1_dir, v2_dir = Path(args[0]), Path(args[1])
        else:
            print(__doc__)
            return 2
        for d in (v1_dir, v2_dir):
            if not (d / "summary.json").exists():
                raise ComparisonError(f"{d / 'summary.json'} not found")
        print(f"v1: {v1_dir.name}\nv2: {v2_dir.name}")
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
