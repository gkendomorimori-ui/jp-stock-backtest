"""Verify and extract data archives made by scripts/pack_data.py.

    python scripts/restore_data.py docs/data_archives/<label>.json \
        data/archive/jquants_raw_<label>.zip data/archive/jquants_processed_<label>.zip

Each zip must match the SHA-256 and file count in the record; otherwise nothing is
extracted from it. Existing files are not overwritten unless --overwrite is given.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.archive import ArchiveError, restore  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("record", type=Path)
    p.add_argument("zips", type=Path, nargs="+")
    p.add_argument("--overwrite", action="store_true")
    args = p.parse_args(argv)
    record = json.loads(args.record.read_text(encoding="utf-8"))
    failed = 0
    for z in args.zips:
        try:
            r = restore(z, record, PROJECT_ROOT, overwrite=args.overwrite)
            print(f"OK   {r['file']}  {r['members']} files  sha256 {r['sha256']}")
        except ArchiveError as e:
            failed += 1
            print(f"FAIL {z.name}: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
