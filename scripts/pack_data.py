"""Back up the J-Quants data as zip archives and record their SHA-256 hashes.

    python scripts/pack_data.py                 # label = today's date

Writes data/archive/jquants_raw_<label>.zip and jquants_processed_<label>.zip (git-ignored)
and docs/data_archives/<label>.json (sizes, hashes, file counts, processed manifest; commit
this record). Upload the zip files to your OWN private storage (e.g. Google Drive, not
shared): J-Quants terms prohibit distributing or sharing the data itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.archive import ArchiveError, pack  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--label", default=date.today().isoformat())
    args = p.parse_args(argv)
    try:
        record = pack(PROJECT_ROOT, PROJECT_ROOT / "data" / "archive", args.label)
    except ArchiveError as e:
        print(f"ERROR: {e}")
        return 1
    out = PROJECT_ROOT / "docs" / "data_archives" / f"{args.label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    for name, a in record["archives"].items():
        size, source = a["bytes"] / 1e6, a["source_bytes"] / 1e6
        print(
            f"{name:10s} {a['file']}  {size:,.1f} MB (from {source:,.1f} MB, "
            f"{a['members']} files)  sha256 {a['sha256']}"
        )
    print(f"record: {out.relative_to(PROJECT_ROOT)} (commit this; keep the zip files private)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
