import json
import math
from pathlib import Path

import pandas as pd

from src.backtest.engine import EQUITY_COLUMNS, TRADE_COLUMNS
from src.evaluation.report import RunMetadata, make_run_id, save_run


def test_save_run_writes_three_files(tmp_path: Path) -> None:
    meta = RunMetadata(
        strategy_name="dummy",
        strategy_version="0.1.0",
        parameters={"window": 20},
        universe={"name": "test"},
        start_date="2020-01-01",
        end_date="2020-12-31",
        commission={"rate": 0.0},
        slippage={"rate": 0.0},
    )
    out = save_run(
        tmp_path / make_run_id("dummy"),
        meta,
        {"sharpe_ratio": math.nan, "profit_factor": math.inf, "total_return": 0.1},
        pd.DataFrame(columns=TRADE_COLUMNS),
        pd.DataFrame(columns=EQUITY_COLUMNS),
    )
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["metadata"]["strategy_name"] == "dummy"
    assert "execution_timestamp" in summary["metadata"]
    assert "git_commit" in summary["metadata"]
    assert summary["metrics"]["sharpe_ratio"] is None
    assert summary["metrics"]["profit_factor"] == "inf"
    assert (out / "trades.csv").exists()
    assert (out / "equity_curve.csv").exists()
