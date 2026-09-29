"""Supplementary trade breakdown for a saved run (reporting only).

    python scripts/breakdown_trades.py                  # latest run
    python scripts/breakdown_trades.py results/runs/<run_id>

Writes <run_dir>/breakdown.json and <run_dir>/trades_classified.csv.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from src.data.processing import ProcessingError, load_processed  # noqa: E402
from src.evaluation.breakdown import classify_trades, summarize  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED = PROJECT_ROOT / "data" / "processed" / "jquants"


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = sys.argv[1:] if argv is None else argv
    if args:
        run_dir = Path(args[0])
    else:
        runs = sorted((PROJECT_ROOT / "results" / "runs").glob("*/summary.json"))
        if not runs:
            print("no runs found")
            return 1
        run_dir = runs[-1].parent
    try:
        data = load_processed(PROCESSED)
    except ProcessingError as e:
        print(f"ERROR: {e}")
        return 1
    read = {"dtype": {"symbol": str}, "parse_dates": ["entry_date", "exit_date"]}
    trades = pd.read_csv(run_dir / "trades.csv", **read)  # type: ignore[call-overload]
    orders = pd.read_csv(
        run_dir / "orders.csv", dtype={"symbol": str}, parse_dates=["signal_date", "exec_date"]
    )
    classified = classify_trades(trades, orders, data)
    summary = summarize(classified)
    classified.to_csv(run_dir / "trades_classified.csv", index=False)
    (run_dir / "breakdown.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"run: {run_dir.name}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
