"""Re-summarize an existing sensitivity analysis WITHOUT re-running it.

    python scripts/summarize_sensitivity.py results/runs/<timestamp>_sensitivity_all

Reads the base run and the variant runs listed in its sensitivity_summary.json, re-runs the
consistency checks, and rewrites sensitivity_summary.json / sensitivity_runs.csv. Runs that
did not complete are listed with the reason and excluded from the random-seed statistics.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.evaluation.sensitivity import summarize  # noqa: E402
from src.evaluation.verify import verify_run  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__)
        return 2
    out = Path(args[0])
    old = json.loads((out / "sensitivity_summary.json").read_text(encoding="utf-8"))
    runs_root = out.parent
    base_dir = runs_root / old["base_run"]
    variant_dirs = [runs_root / r["run"] for r in old["runs"] if r["run"] != old["base_run"]]
    for d in [base_dir, *variant_dirs]:
        checks, _ = verify_run(d)
        bad = [c.name for c in checks if not c.ok]
        if bad:
            print(f"{d.name}: FAILED {bad}")
    summary, table = summarize(base_dir, variant_dirs)
    (out / "sensitivity_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    table.to_csv(out / "sensitivity_runs.csv", index=False)
    print(
        json.dumps(
            {
                "not_completed_runs": summary["not_completed_runs"],
                "take_profit_first": summary["take_profit_first"],
                "random_ranking_all_seeds": summary["random_ranking_all_seeds"],
                "runs": [
                    {
                        k: r[k]
                        for k in (
                            "variant",
                            "status",
                            "last_valued_date",
                            "intraday_both_touched",
                            "take_profit_priority_applied",
                        )
                    }
                    for r in summary["runs"]
                ],
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )
    print(f"saved: {out / 'sensitivity_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
