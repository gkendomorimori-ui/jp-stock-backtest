"""Download daily OHLCV data into ``data/raw/`` (skeleton).

The data source has not been decided yet (see docs/DATA_SOURCES.md).
Once decided, implement a ``DataProvider`` in ``src/data/providers/`` and wire it here.

Usage (planned):
    python scripts/download_data.py --provider <name> --start 2020-01-01 --end 2024-12-31
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=False, help="data provider name (TODO)")
    parser.add_argument("--universe", default="config/universe.yaml")
    parser.add_argument("--start", help="YYYY-MM-DD")
    parser.add_argument("--end", help="YYYY-MM-DD")
    parser.add_argument("--out", default="data/raw")
    return parser.parse_args()


def main() -> int:
    """Entry point."""
    parse_args()
    # TODO: select DataProvider by name, fetch OHLCV, validate_ohlcv(), save to data/raw/.
    print("download_data: not implemented yet -- data source is undecided (docs/DATA_SOURCES.md)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
