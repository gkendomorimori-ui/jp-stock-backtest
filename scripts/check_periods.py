"""Verify the period plan (config/backtest.yaml) against the exchange trading calendar.

    python scripts/check_periods.py

Uses data/processed/jquants/calendar.parquet if it covers the plan, otherwise a downloaded
raw calendar file under data/raw/jquants/calendar/. Checks inclusive trading-day counts,
contiguity, the 20-day warm-up and that every boundary is a trading day. Computes no
strategy results.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from src.backtest.periods import PeriodPlan  # noqa: E402
from src.data.processing import load_calendar  # noqa: E402
from src.utils.config_loader import load_yaml  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def trading_days(plan: PeriodPlan) -> tuple[list[date], str]:
    """Trading days covering the plan, and where they came from."""
    processed = PROJECT_ROOT / "data" / "processed" / "jquants" / "calendar.parquet"
    frames: list[tuple[pd.DataFrame, str]] = []
    if processed.exists():
        frames.append((pd.read_parquet(processed), str(processed.relative_to(PROJECT_ROOT))))
    for path in sorted((PROJECT_ROOT / "data" / "raw" / "jquants" / "calendar").glob("*.json.gz")):
        frames.append((load_calendar(path), str(path.relative_to(PROJECT_ROOT))))
    for cal, source in frames:
        dates = pd.to_datetime(cal["date"])
        if dates.min().date() <= plan.data_start and plan.data_end <= dates.max().date():
            days = [d.date() for d in dates[cal["is_trading_day"].to_numpy()]]
            return days, source
    raise SystemExit(
        f"no calendar covers {plan.data_start}..{plan.data_end}; "
        "run scripts/download_data.py --start ... --end ... first"
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point. Exit code 1 if the plan does not match the calendar."""
    plan = PeriodPlan.from_config(load_yaml("config/backtest.yaml"))
    days, source = trading_days(plan)
    print(f"calendar: {source}")
    in_range = [d for d in days if plan.data_start <= d <= plan.data_end]
    print(f"data range {plan.data_start}..{plan.data_end}: {len(in_range)} trading days")
    for s in plan.segments:
        n = len([d for d in in_range if s.start <= d <= s.end])
        line = (
            f"  {s.name:<17} {s.start}..{s.end}  {n:>4} days (config {s.trading_days})  {s.status}"
        )
        if s.status != "warmup":
            a, b = plan.indicator_window(s.name, in_range)
            line += f"  indicators-only window {a}..{b}"
        print(line)
    problems = plan.validate(days)
    for p in problems:
        print(f"PROBLEM: {p}")
    print("OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
