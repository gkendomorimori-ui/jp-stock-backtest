"""Download raw J-Quants data into ``data/raw/jquants/`` (resumable).

Smoke test on the Free plan (about 3 months ending at the latest date the Free plan can
return, plus a 20-trading-day warm-up before it):

    python scripts/download_data.py --smoke

The smoke period is determined once and saved to ``data/raw/jquants/smoke_period.json``;
later runs reuse it, so an interrupted download resumes with the same dates. Only days
whose every page was fetched and saved count as downloaded.

Explicit range (e.g. Light plan, 5 years):

    python scripts/download_data.py --start 2021-09-01 --end 2026-09-25
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.providers.jquants import (  # noqa: E402
    JQuantsClient,
    JQuantsDownloader,
    JQuantsError,
    JQuantsHTTPError,
    Period,
    find_latest_available,
    free_plan_calendar_window,
    smoke_period,
    trading_days_from_calendar,
)
from src.data.providers.jquants import load_api_key as _load_api_key  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = PROJECT_ROOT / "data" / "raw"
SMOKE_PERIOD_FILE = RAW_ROOT / "jquants" / "smoke_period.json"


def load_api_key() -> str | None:
    """API key, or None when an outbound proxy attaches it (see docs/CLOUD_SETUP.md)."""
    return _load_api_key(PROJECT_ROOT)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true", help="Free-plan smoke-test period")
    mode.add_argument("--start", type=date.fromisoformat, help="first date (YYYY-MM-DD)")
    p.add_argument("--end", type=date.fromisoformat, help="last date (with --start)")
    p.add_argument("--months", type=int, default=3, help="smoke: evaluation length in months")
    p.add_argument("--warmup", type=int, default=20, help="smoke: warm-up trading days")
    p.add_argument("--redetermine", action="store_true", help="smoke: recompute the period")
    p.add_argument("--min-interval", type=float, default=13.0, help="seconds between requests")
    p.add_argument("--no-topix", action="store_true", help="with --start: skip TOPIX")
    args = p.parse_args(argv)
    if args.start and not args.end:
        p.error("--end is required with --start")
    return args


def download_free_calendar(downloader: JQuantsDownloader, today: date) -> Path:
    """Fetch the Free-plan calendar window, shrinking the end date if it is not yet available."""
    start, end = free_plan_calendar_window(today)
    for _ in range(4):
        try:
            return downloader.download_calendar(start, end)
        except JQuantsHTTPError as e:
            if e.status not in (400, 403):
                raise
            print(f"calendar up to {end} not available (HTTP {e.status}); trying 3 days earlier")
            end -= timedelta(days=3)
    raise SystemExit("could not fetch the trading calendar for the Free-plan window")


def resolve_smoke_period(
    client: JQuantsClient, downloader: JQuantsDownloader, args: argparse.Namespace
) -> tuple[Period, Path]:
    """Load the saved smoke period, or determine and save it."""
    if SMOKE_PERIOD_FILE.exists() and not args.redetermine:
        saved = json.loads(SMOKE_PERIOD_FILE.read_text(encoding="utf-8"))
        print(f"reusing smoke period from {SMOKE_PERIOD_FILE.relative_to(PROJECT_ROOT)}")
        return Period.from_dict(saved), PROJECT_ROOT / saved["calendar_file"]
    cal_path = download_free_calendar(downloader, date.today())
    days = trading_days_from_calendar(cal_path)
    print(f"calendar: {days[0]}..{days[-1]} ({len(days)} trading days); probing latest data")
    latest = find_latest_available(client, list(reversed(days)))
    period = smoke_period(days, latest, months=args.months, warmup=args.warmup)
    payload = period.to_dict() | {
        "latest_available": latest.isoformat(),
        "calendar_file": str(cal_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "determined_on": date.today().isoformat(),
        "run_type": "smoke_test",
    }
    SMOKE_PERIOD_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SMOKE_PERIOD_FILE.with_name(SMOKE_PERIOD_FILE.name + ".part")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, SMOKE_PERIOD_FILE)
    return period, cal_path


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)
    client = JQuantsClient(load_api_key(), min_interval=args.min_interval)
    downloader = JQuantsDownloader(client, RAW_ROOT)
    try:
        if args.smoke:
            period, cal_path = resolve_smoke_period(client, downloader, args)
            start, end = period.download_start, period.eval_end
            print(
                f"smoke period: warm-up from {period.download_start}, "
                f"evaluation {period.eval_start}..{period.eval_end}"
            )
        else:
            start, end = args.start, args.end
            cal_path = downloader.download_calendar(start, end)
        days = [d for d in trading_days_from_calendar(cal_path) if start <= d <= end]
        print(f"{len(days)} trading days to cover ({start}..{end})")
        report = downloader.download_days(days)
        if not args.smoke and not args.no_topix:
            topix = downloader.download_topix(start, end)
            print(f"TOPIX: {topix.relative_to(PROJECT_ROOT)}")
    except JQuantsError as e:
        print(f"ERROR: {e}")
        print("Stopped. Completed days are kept; run the same command again to resume.")
        return 1
    print(
        f"done: {len(report.downloaded)} files downloaded, {len(report.skipped)} already present; "
        f"{client.request_count} requests"
    )
    print("next: python scripts/process_data.py" + (" --smoke" if args.smoke else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
