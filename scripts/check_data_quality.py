"""Quality checks on data/processed/jquants/ (all periods; no strategy results).

    python scripts/check_data_quality.py

Prints each finding (ERROR / WARN / INFO) and saves the report to
data/processed/jquants/quality_report.json. Exit code 1 if any ERROR.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.backtest.periods import PeriodPlan  # noqa: E402
from src.data.processing import ProcessingError, load_processed  # noqa: E402
from src.data.quality import check_quality  # noqa: E402
from src.universe import UniverseRules  # noqa: E402
from src.utils.config_loader import load_yaml  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED = PROJECT_ROOT / "data" / "processed" / "jquants"


def main(argv: list[str] | None = None) -> int:
    """Entry point. ``--show CHECK`` prints that check's details in full (not truncated)."""
    args = sys.argv[1:] if argv is None else argv
    show = set(args[args.index("--show") + 1 :]) if "--show" in args else set()
    try:
        data = load_processed(PROCESSED)
    except ProcessingError as e:
        print(f"ERROR: {e}")
        return 1
    uni = load_yaml("config/universe.yaml")
    rules = UniverseRules.from_config(uni)
    known = {m["code"] for group in uni["universe"]["markets"].values() for m in group}
    plan = PeriodPlan.from_config(load_yaml("config/backtest.yaml"))
    segments = [(s.name, s.start, s.end) for s in plan.segments if s.status != "warmup"]
    findings = check_quality(data, rules, known, segments)
    for f in findings:
        print(f"[{f.level:<5}] {f.check}: {f.message}")
        if f.details:
            text = json.dumps(f.details, ensure_ascii=False, default=str)
            if f.check in show:
                print(json.dumps(f.details, indent=2, ensure_ascii=False, default=str))
            else:
                more = " ...(--show で全体)" if len(text) > 1500 else ""
                print("        " + text[:1500] + more)
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "findings": [f.__dict__ for f in findings],
    }
    (PROCESSED / "quality_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    errors = sum(f.level == "ERROR" for f in findings)
    warns = sum(f.level == "WARN" for f in findings)
    saved = PROCESSED.relative_to(PROJECT_ROOT) / "quality_report.json"
    print(f"{errors} error(s), {warns} warning(s); saved {saved}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
