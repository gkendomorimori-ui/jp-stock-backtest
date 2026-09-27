"""Build the processed dataset (``data/processed/jquants/``) from raw J-Quants files.

    python scripts/process_data.py --smoke                      # uses smoke_period.json
    python scripts/process_data.py --start 2021-09-01 --end 2026-09-25

Stops with an error if any trading day in the range has not been fully downloaded.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.processing import ProcessingError, build_processed, save_processed  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = PROJECT_ROOT / "data" / "raw"
OUT_DIR = PROJECT_ROOT / "data" / "processed" / "jquants"


def find_calendar_file(start: date, end: date) -> Path:
    """A downloaded calendar file whose range covers ``start..end``."""
    for path in sorted((RAW_ROOT / "jquants" / "calendar").glob("*_*.json.gz")):
        a, b = path.name.removesuffix(".json.gz").split("_")
        if date.fromisoformat(a) <= start and end <= date.fromisoformat(b):
            return path
    raise SystemExit(f"no calendar file covers {start}..{end}; run scripts/download_data.py")


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--start", type=date.fromisoformat)
    p.add_argument("--end", type=date.fromisoformat)
    args = p.parse_args(argv)

    extra: dict[str, object] = {}
    if args.smoke:
        period_file = RAW_ROOT / "jquants" / "smoke_period.json"
        if not period_file.exists():
            raise SystemExit("smoke_period.json not found; run scripts/download_data.py --smoke")
        period = json.loads(period_file.read_text(encoding="utf-8"))
        start = date.fromisoformat(period["download_start"])
        end = date.fromisoformat(period["eval_end"])
        cal_file = PROJECT_ROOT / period["calendar_file"]
        extra["smoke_period"] = period
    else:
        if not args.end:
            p.error("--end is required with --start")
        start, end = args.start, args.end
        cal_file = find_calendar_file(start, end)

    try:
        data = build_processed(RAW_ROOT, cal_file, start, end)
    except ProcessingError as e:
        print(f"ERROR: {e}")
        return 1
    out = save_processed(data, OUT_DIR, extra)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    print(json.dumps({k: v for k, v in manifest.items() if k != "smoke_period"}, indent=2))
    print(f"saved to {out.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
