"""Run a single backtest and save results to ``results/runs/<run_id>/`` (skeleton).

Usage (planned):
    python scripts/run_backtest.py --strategy <name> --params strategies/<name>.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.backtest.config import BacktestConfig  # noqa: E402
from src.utils.config_loader import load_yaml  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", help="strategy name (TODO: registry)")
    parser.add_argument("--params", help="YAML file with strategy parameters")
    parser.add_argument("--config", default="config/backtest.yaml")
    parser.add_argument("--universe", default="config/universe.yaml")
    return parser.parse_args()


def main() -> int:
    """Entry point."""
    args = parse_args()
    config = BacktestConfig.from_dict(load_yaml(args.config))
    load_yaml(args.universe)

    missing = config.missing_fields()
    if missing:
        print(f"config has undecided (TODO) values: {missing}")

    # TODO: 1) load normalized OHLCV from data/processed
    #       2) instantiate strategy with params (no hard-coded values)
    #       3) run BacktestEngine (implementation undecided)
    #       4) compute_metrics() and save_run() -> results/runs/<run_id>/
    print("run_backtest: not implemented yet -- backtest engine approach is undecided")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
