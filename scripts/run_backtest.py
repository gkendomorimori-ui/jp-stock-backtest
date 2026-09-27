"""Run high_price_breakout on the processed dataset and save results to results/runs/.

    python scripts/run_backtest.py --smoke
    python scripts/run_backtest.py --start 2023-01-04 --end 2025-12-30 --run-type development

``--smoke`` uses the evaluation period stored in data/processed/jquants/manifest.json
(from scripts/download_data.py --smoke) and records run_type = smoke_test.
Success of a smoke test = download, processing, run and saving all complete; the size of
the profit is NOT a success criterion, and parameters must not be changed based on it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.backtest.runner import run_high_price_breakout  # noqa: E402
from src.data.processing import ProcessingError, load_processed  # noqa: E402
from src.evaluation.report import RUN_TYPES  # noqa: E402
from src.utils.config_loader import load_yaml  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED = PROJECT_ROOT / "data" / "processed" / "jquants"


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--start", type=date.fromisoformat)
    p.add_argument("--end", type=date.fromisoformat)
    p.add_argument("--run-type", choices=RUN_TYPES)
    p.add_argument("--strategy", default="high_price_breakout", choices=["high_price_breakout"])
    args = p.parse_args(argv)

    backtest_cfg = load_yaml("config/backtest.yaml")
    try:
        data = load_processed(PROCESSED)
    except ProcessingError as e:
        print(f"ERROR: {e}")
        return 1
    manifest = json.loads((PROCESSED / "manifest.json").read_text(encoding="utf-8"))

    notes: list[str] = []
    if args.smoke:
        period = manifest.get("smoke_period")
        if not period:
            print("ERROR: processed data was not built with --smoke")
            return 1
        start, end = (
            date.fromisoformat(period["eval_start"]),
            date.fromisoformat(period["eval_end"]),
        )
        run_type = "smoke_test"
        notes.append(
            "Smoke test on J-Quants Free-plan data (~3 months): checks that download, "
            "processing, run and saving work. Not an evaluation result."
        )
    else:
        if not args.end:
            p.error("--end is required with --start")
        start, end = args.start, args.end
        run_type = args.run_type or backtest_cfg.get("run", {}).get("run_type", "smoke_test")

    try:
        out, result = run_high_price_breakout(
            data,
            backtest_cfg,
            load_yaml("config/universe.yaml"),
            load_yaml(f"strategies/{args.strategy}.yaml"),
            eval_start=start,
            eval_end=end,
            run_type=run_type,
            results_root=PROJECT_ROOT / "results" / "runs",
            notes=notes,
        )
    except ValueError as e:
        print(f"ERROR: {e}")
        return 1

    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    print(f"run_type: {run_type}   status: {result.status}   period: {start}..{end}")
    print(f"simulated days: {len(result.equity_curve)}   trades: {len(result.trades)}")
    print(f"orders: {summary['stats'].get('orders', {})}")
    if result.status != "complete":
        print("NEEDS REVIEW: see unresolved_events.csv -- results are not final")
    print(f"saved: {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
