"""Check that two runs produced identical trading results.

python scripts/compare_runs.py results/runs/<before> results/runs/<after>
python scripts/compare_runs.py results/runs/<before>        # after = latest run
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.compare import compare_runs  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    """Entry point. Exit code 1 if anything differs."""
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__)
        return 2
    before = Path(args[0])
    if len(args) > 1:
        after = Path(args[1])
    else:
        runs = sorted((PROJECT_ROOT / "results" / "runs").glob("*/summary.json"))
        after = runs[-1].parent
    print(f"before: {before.name}\nafter:  {after.name}")
    diffs = compare_runs(before, after)
    for d in diffs:
        print(f"  [{'SAME' if d.identical else 'DIFF'}] {d.name}  ({d.detail})")
    return 0 if all(d.identical for d in diffs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
