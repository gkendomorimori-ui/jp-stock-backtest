"""Run high_price_breakout on the processed dataset and save results to results/runs/.

    python scripts/run_backtest.py --smoke                      # viewed smoke-test period
    python scripts/run_backtest.py --period development         # development segment

Every run must lie inside ONE segment of config/backtest.yaml ``period.split``.
Sealed segments (final_evaluation, holdout) are refused unless ``--open-sealed-period`` is
given with the matching ``--run-type``; do that only after the freeze record described in
docs/EVALUATION_PLAN.md. Each run starts with the initial capital, no positions and no
carried-over orders; the first signal is judged at the close of the first day.

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

from src.backtest.engine import DataError  # noqa: E402
from src.backtest.periods import PeriodError, PeriodPlan  # noqa: E402
from src.backtest.runner import run_high_price_breakout  # noqa: E402
from src.data.processing import ProcessingError, load_processed  # noqa: E402
from src.evaluation.report import RUN_TYPES  # noqa: E402
from src.utils.config_loader import load_yaml  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED = PROJECT_ROOT / "data" / "processed" / "jquants"


def load_period_plan() -> PeriodPlan:
    """The period plan from config/backtest.yaml."""
    return PeriodPlan.from_config(load_yaml("config/backtest.yaml"))


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--period", help="segment name in config/backtest.yaml period.split")
    mode.add_argument("--start", type=date.fromisoformat)
    p.add_argument("--end", type=date.fromisoformat)
    p.add_argument("--run-type", choices=RUN_TYPES)
    p.add_argument(
        "--open-sealed-period",
        action="store_true",
        help="allow a sealed segment (only after the freeze record)",
    )
    p.add_argument(
        "--execution-model",
        choices=["v1", "v2"],
        help="override execution.model_version (v1 only for the diagnostic comparison)",
    )
    p.add_argument("--strategy", default="high_price_breakout", choices=["high_price_breakout"])
    args = p.parse_args(argv)

    backtest_cfg = load_yaml("config/backtest.yaml")
    notes: list[str] = []
    if args.smoke:
        manifest_file = PROCESSED / "manifest.json"
        if not manifest_file.exists():
            print("ERROR: processed data not found -- run scripts/process_data.py --smoke")
            return 1
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
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
    elif args.period:
        plan = load_period_plan()
        try:
            seg = plan.segment(args.period)
        except PeriodError as e:
            print(f"ERROR: {e}")
            return 1
        start, end = seg.start, seg.end
        default_type = {
            "development": "development",
            "final_evaluation": "final_evaluation",
            "holdout": "holdout",
            "viewed_reference": "smoke_test",
        }
        run_type = args.run_type or default_type.get(args.period, "development")
    else:
        if not args.end:
            p.error("--end is required with --start")
        start, end = args.start, args.end
        run_type = args.run_type or "development"

    try:
        segment = load_period_plan().check_run(start, end, run_type, args.open_sealed_period)
    except PeriodError as e:
        print(f"REFUSED: {e}")
        return 1
    notes.append(f"Period segment: {segment.name} ({segment.status}), {start}..{end} inclusive.")

    try:
        data = load_processed(PROCESSED)
    except ProcessingError as e:
        print(f"ERROR: {e}")
        return 1

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
            execution_model=args.execution_model,
        )
    except (ValueError, DataError) as e:
        print(f"ERROR: {e}")
        return 1

    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    print(
        f"run_type: {run_type}   status: {result.status}   period: {start}..{end}   "
        f"execution model: {summary['execution_model_version']}"
    )
    print(f"simulated days: {len(result.equity_curve)}   trades: {len(result.trades)}")
    print(f"orders: {summary['stats'].get('orders', {})}")
    if result.status != "complete":
        print("NEEDS REVIEW: see unresolved_events.csv -- results are not final")
    print(f"saved: {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
