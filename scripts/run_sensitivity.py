"""Pre-registered sensitivity analyses A / B / C on the DEVELOPMENT period only.

    python scripts/run_sensitivity.py --analysis all      # base + A + B (turnover, seeds 0-19) + C
    python scripts/run_sensitivity.py --analysis A        # base + A only

The base condition is re-run in the same invocation (same code and data). Each variant
changes ONE condition (docs/EVALUATION_PLAN.md 5) and is saved as its own run under
results/runs/. The comparison is written to results/runs/<timestamp>_sensitivity/.
A better result is NOT adopted as the base; for random ranking all 20 seeds are reported.

Other periods are refused (final_evaluation / holdout stay sealed; viewed_reference is not
used for sensitivity analyses).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.backtest.engine import DataError  # noqa: E402
from src.backtest.periods import PeriodError, PeriodPlan  # noqa: E402
from src.backtest.runner import Variant, execute, prepare  # noqa: E402
from src.data.processing import ProcessingError, load_processed  # noqa: E402
from src.evaluation.sensitivity import summarize  # noqa: E402
from src.evaluation.verify import verify_run  # noqa: E402
from src.utils.config_loader import load_yaml  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED = PROJECT_ROOT / "data" / "processed" / "jquants"
RANDOM_SEEDS = tuple(range(20))  # fixed before running (docs/EVALUATION_PLAN.md 5)


def variants_for(analysis: str) -> list[Variant]:
    """The pre-registered variants (the base is always run separately)."""
    out: list[Variant] = []
    if analysis in ("A", "all"):
        out.append(
            Variant(name="A_take_profit_first", analysis="A", intraday_priority="take_profit")
        )
    if analysis in ("B", "all"):
        out.append(Variant(name="B_avg_turnover", analysis="B", ranking="avg_turnover"))
        out += [
            Variant(name=f"B_random_seed{s:02d}", analysis="B", ranking="random", seed=s)
            for s in RANDOM_SEEDS
        ]
    if analysis in ("C", "all"):
        out.append(Variant(name="C_max_rate_or_tick", analysis="C", slippage_model="max_rate_tick"))
    return out


def load_period_plan() -> PeriodPlan:
    """The period plan from config/backtest.yaml."""
    return PeriodPlan.from_config(load_yaml("config/backtest.yaml"))


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--analysis", choices=["A", "B", "C", "all"], required=True)
    args = p.parse_args(argv)

    plan = load_period_plan()
    try:
        seg = plan.segment("development")
        plan.check_run(seg.start, seg.end, "development", False)
    except PeriodError as e:
        print(f"REFUSED: {e}")
        return 1
    try:
        data = load_processed(PROCESSED)
    except ProcessingError as e:
        print(f"ERROR: {e}")
        return 1
    prep = prepare(
        data,
        load_yaml("config/backtest.yaml"),
        load_yaml("config/universe.yaml"),
        load_yaml("strategies/high_price_breakout.yaml"),
        seg.start,
        seg.end,
        load_yaml("config/tick_exceptions.yaml"),
    )
    runs_root = PROJECT_ROOT / "results" / "runs"
    note = [f"Period segment: development (open), {seg.start}..{seg.end} inclusive."]
    try:
        failed_checks = 0

        def report(name: str, d: Path, r: object) -> None:
            nonlocal failed_checks
            checks, _ = verify_run(d)
            bad = [c.name for c in checks if not c.ok]
            failed_checks += len(bad)
            halted = getattr(r, "halted", None)
            stop = f"  STOPPED {halted['date'].date()}" if halted else ""
            print(
                f"{name}: {d.name}  status {getattr(r, 'status', '?')}  trades "
                f"{len(getattr(r, 'trades', []))}  verify {len(checks) - len(bad)}/{len(checks)}"
                f"{stop}" + (f"  FAILED: {bad}" if bad else "")
            )

        base_dir, base = execute(prep, "development", runs_root, Variant(), notes=note)
        report("base", base_dir, base)
        dirs = []
        for v in variants_for(args.analysis):
            d, r = execute(prep, "development", runs_root, v, notes=note)
            dirs.append(d)
            report(v.name, d, r)
    except (ValueError, DataError) as e:
        print(f"ERROR: {e}")
        return 1

    summary, table = summarize(base_dir, dirs)
    out = runs_root / f"{datetime.now():%Y%m%dT%H%M%S}_sensitivity_{args.analysis}"
    out.mkdir(parents=True, exist_ok=False)
    (out / "sensitivity_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    table.to_csv(out / "sensitivity_runs.csv", index=False)
    cols = [
        "variant",
        "status",
        "total_return",
        "cagr",
        "max_drawdown",
        "sharpe_ratio",
        "profit_factor",
        "win_rate",
        "number_of_trades",
        "final_equity",
        "cancelled_no_slot",
    ]
    print(table[cols].to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    shown = {k: v for k, v in summary["comparison_with_base"].items() if "_sensitivity" not in k}
    names = dict(zip(table["run"], table["variant"], strict=True))
    printed = {
        f"{names[k]} ({k})": v for k, v in shown.items() if not str(names[k]).startswith("B_random")
    }
    print(
        json.dumps(
            {
                "comparison_with_base": printed,
                "random_ranking_all_seeds": summary["random_ranking_all_seeds"],
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )
    print(f"saved: {out.relative_to(PROJECT_ROOT)}")
    if failed_checks:
        print(f"WARNING: {failed_checks} consistency check(s) failed -- see above")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
