"""Additional aggregates of a saved run (no re-run; nothing is changed).

    python scripts/analyze_run.py                          # latest base development run
    python scripts/analyze_run.py results/runs/<run_id>

Writes analysis.json and analysis_*.csv into the run directory and prints a summary:
yearly return / in-year max drawdown / trades, the max-drawdown period, trades and realized
PnL by signal-day market and actual price band, the PnL distribution with the best / worst
trades, and monthly equity / invested ratio / budget and slot shortfalls.
Partial aggregates are NOT results of a strategy re-run on that subset.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.analysis import analyze  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED = PROJECT_ROOT / "data" / "processed" / "jquants"


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = sys.argv[1:] if argv is None else argv
    if args:
        run_dir = Path(args[0])
    else:
        candidates = []
        for f in sorted((PROJECT_ROOT / "results" / "runs").glob("*/summary.json")):
            s = json.loads(f.read_text(encoding="utf-8"))
            variant = s.get("variant", {}).get("name", "base")
            if s["metadata"]["run_type"] == "development" and variant == "base":
                candidates.append(f.parent)
        if not candidates:
            print("ERROR: no base development run found")
            return 1
        run_dir = candidates[-1]
    if not (run_dir / "summary.json").exists():
        print(f"ERROR: {run_dir / 'summary.json'} not found")
        return 1
    bars = pd.read_parquet(PROCESSED / "bars.parquet", columns=["date", "symbol", "close"])
    master = pd.read_parquet(
        PROCESSED / "master.parquet", columns=["date", "symbol", "market_code"]
    )
    result = analyze(run_dir, bars, master)
    print(f"run: {run_dir.name}")
    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "yearly",
                    "drawdown",
                    "by_market",
                    "by_price_band",
                    "distribution",
                    "totals_check",
                )
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )
    monthly = pd.read_csv(run_dir / "analysis_monthly.csv")
    print(monthly.to_string(index=False, float_format=lambda x: f"{x:,.4f}"))
    print(f"saved: {run_dir / 'analysis.json'} and analysis_*.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
