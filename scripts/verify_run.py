"""Check a saved run for internal consistency (not profitability).

python scripts/verify_run.py                 # latest run in results/runs/
python scripts/verify_run.py results/runs/<run_id>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.verify import verify_run  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    """Entry point. Exit code 1 if any check fails."""
    args = sys.argv[1:] if argv is None else argv
    if args:
        run_dir = Path(args[0])
    else:
        runs = sorted((PROJECT_ROOT / "results" / "runs").glob("*/summary.json"))
        if not runs:
            print("no runs found in results/runs/")
            return 1
        run_dir = runs[-1].parent
    checks, info = verify_run(run_dir)
    print(f"run: {run_dir.name}")
    for c in checks:
        print(
            f"  [{'PASS' if c.ok else 'FAIL'}] {c.name}" + (f"  ({c.detail})" if c.detail else "")
        )
    print(json.dumps(info, indent=2, ensure_ascii=False, default=str))
    failed = [c for c in checks if not c.ok]
    print(f"{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
