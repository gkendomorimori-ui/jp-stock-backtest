import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from src.backtest.engine import EQUITY_COLUMNS, TRADE_COLUMNS
from src.evaluation.report import UNRESOLVED_EVENT_COLUMNS, RunMetadata, make_run_id, save_run


def _meta(**overrides: Any) -> RunMetadata:
    kwargs: dict[str, Any] = {
        "strategy_name": "dummy",
        "strategy_version": "0.1.0",
        "parameters": {"window": 20},
        "universe": {"name": "test"},
        "start_date": "2020-01-01",
        "end_date": "2020-12-31",
        "commission": {"rate": 0.0},
        "slippage": {"rate": 0.0},
    }
    kwargs.update(overrides)
    return RunMetadata(**kwargs)


def _empty() -> tuple[pd.DataFrame, pd.DataFrame]:
    return pd.DataFrame(columns=TRADE_COLUMNS), pd.DataFrame(columns=EQUITY_COLUMNS)


def test_save_run_writes_three_files(tmp_path: Path) -> None:
    trades, equity = _empty()
    out = save_run(
        tmp_path / make_run_id("dummy"),
        _meta(),
        {"sharpe_ratio": math.nan, "profit_factor": math.inf, "total_return": 0.1},
        trades,
        equity,
    )
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    meta = summary["metadata"]
    assert meta["strategy_name"] == "dummy"
    assert "execution_timestamp" in meta
    assert "git_commit" in meta
    assert meta["run_type"] == "smoke_test"
    assert meta["status"] == "complete"
    assert meta["dividends_included"] is False
    assert summary["metrics_final"] is True
    assert summary["metrics"]["sharpe_ratio"] is None
    assert summary["metrics"]["profit_factor"] == "inf"
    assert (out / "trades.csv").exists()
    assert (out / "equity_curve.csv").exists()
    assert not (out / "unresolved_events.csv").exists()


def test_unresolved_events_mark_run_as_not_final(tmp_path: Path) -> None:
    trades, equity = _empty()
    events = pd.DataFrame(
        [
            [
                "2024-05-01",
                "12340",
                "delisted",
                100,
                1000.0,
                990.0,
                "2024-04-30",
                "settlement unknown",
            ]
        ],
        columns=UNRESOLVED_EVENT_COLUMNS,
    )
    out = save_run(tmp_path / "run", _meta(status="needs_review"), {}, trades, equity, events)
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["metadata"]["status"] == "needs_review"
    assert summary["metrics_final"] is False
    saved = pd.read_csv(out / "unresolved_events.csv", dtype=str)
    assert list(saved.columns) == UNRESOLVED_EVENT_COLUMNS
    assert saved.loc[0, "symbol"] == "12340"


def test_unresolved_events_require_needs_review_status(tmp_path: Path) -> None:
    trades, equity = _empty()
    events = pd.DataFrame([["2024-05-01"] + [None] * 7], columns=UNRESOLVED_EVENT_COLUMNS)
    with pytest.raises(ValueError, match="needs_review"):
        save_run(tmp_path / "run", _meta(), {}, trades, equity, events)


@pytest.mark.parametrize("field,value", [("run_type", "backtest"), ("status", "done")])
def test_invalid_run_type_or_status_rejected(field: str, value: str) -> None:
    with pytest.raises(ValueError):
        _meta(**{field: value})
