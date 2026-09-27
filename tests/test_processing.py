from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from src.data.processing import (
    BAR_COLUMNS,
    ProcessingError,
    bars_from_rows,
    build_processed,
    load_processed,
    save_processed,
)
from src.data.providers.jquants import write_raw_atomic


def bar(
    d: str, code: str, c: float | None, vo: float | None, f: float = 1.0, **kw: Any
) -> dict[str, Any]:
    row = {
        "Date": d,
        "Code": code,
        "O": c,
        "H": c,
        "L": c,
        "C": c,
        "Vo": vo,
        "Va": None if c is None else c * (vo or 0),
        "AdjFactor": f,
        "AdjC": c,
    }
    row.update(kw)
    return row


def test_split_adjustment_direction_and_ex_date() -> None:
    # 1:2 split effective (ex-date) on 01-07: factor 0.5 on that date; no real price move
    rows = [
        bar("2026-01-05", "10010", 100.0, 1000),
        bar("2026-01-06", "10010", 100.0, 1000),
        bar("2026-01-07", "10010", 50.0, 2000, f=0.5),
        bar("2026-01-08", "10010", 50.0, 2000),
    ]
    b = bars_from_rows(rows)
    assert list(b.columns) == BAR_COLUMNS
    assert b["close"].tolist() == [100, 100, 50, 50]  # actual values untouched
    assert b["adj_close"].tolist() == [50, 50, 50, 50]  # past rescaled to the last basis
    assert b["adj_volume"].tolist() == [2000, 2000, 2000, 2000]
    assert b["adj_factor"].tolist() == [1, 1, 0.5, 1]


def test_reverse_split_and_multiple_events() -> None:
    rows = [
        bar("2026-01-05", "10010", 100.0, 1000),
        bar("2026-01-06", "10010", 500.0, 200, f=5.0),  # 5:1 reverse split
        bar("2026-01-07", "10010", 250.0, 400, f=0.5),  # then 1:2 split
    ]
    b = bars_from_rows(rows)
    assert b["adj_close"].tolist() == pytest.approx([250, 250, 250])
    assert b["adj_volume"].tolist() == pytest.approx([400, 400, 400])


def test_symbols_are_adjusted_independently() -> None:
    rows = [
        bar("2026-01-05", "10010", 100.0, 1000),
        bar("2026-01-06", "10010", 50.0, 2000, f=0.5),
        bar("2026-01-05", "20020", 300.0, 10),
        bar("2026-01-06", "20020", 300.0, 10),
    ]
    b = bars_from_rows(rows).set_index(["symbol", "date"])
    assert b.loc[("20020", pd.Timestamp("2026-01-05")), "adj_close"] == 300


def test_no_trade_day_is_kept_as_nan() -> None:
    rows = [bar("2026-01-05", "10010", 100.0, 1000), bar("2026-01-06", "10010", None, None)]
    b = bars_from_rows(rows)
    assert np.isnan(b.loc[1, "close"]) and np.isnan(b.loc[1, "adj_close"])
    assert b.loc[1, "adj_factor"] == 1.0


def test_duplicate_rows_rejected() -> None:
    rows = [bar("2026-01-05", "10010", 100.0, 1), bar("2026-01-05", "10010", 100.0, 1)]
    with pytest.raises(ProcessingError):
        bars_from_rows(rows)


# ------------------------------------------------------------------ build from raw files


def _write_day(raw: Path, kind: str, d: str, rows: list[dict[str, Any]]) -> None:
    ep = "/equities/bars/daily" if kind == "bars_daily" else "/equities/master"
    write_raw_atomic(raw / "jquants" / kind / f"{d}.json.gz", ep, {"date": d}, [{"data": rows}])


def _setup(raw: Path, days: list[str], skip: tuple[str, str] | None = None) -> Path:
    cal = [{"Date": d, "HolDiv": "1"} for d in days] + [{"Date": "2026-01-10", "HolDiv": "0"}]
    cal_path = raw / "jquants" / "calendar" / "2026-01-05_2026-01-10.json.gz"
    write_raw_atomic(cal_path, "/markets/calendar", {}, [{"data": cal}])
    for d in days:
        if skip != ("bars_daily", d):
            _write_day(raw, "bars_daily", d, [bar(d, "10010", 100.0, 1000)])
        if skip != ("master", d):
            _write_day(
                raw,
                "master",
                d,
                [
                    {
                        "Date": d,
                        "Code": "10010",
                        "CoName": "A",
                        "Mkt": "0111",
                        "MktNm": "P",
                        "ProdCat": "011",
                    }
                ],
            )
    return cal_path


def test_build_and_roundtrip(tmp_path: Path) -> None:
    days = ["2026-01-05", "2026-01-06", "2026-01-07"]
    cal = _setup(tmp_path, days)
    data = build_processed(tmp_path, cal, date(2026, 1, 5), date(2026, 1, 10))
    assert len(data.bars) == 3 and len(data.master) == 3
    assert data.calendar["is_trading_day"].sum() == 3
    out = save_processed(data, tmp_path / "processed")
    loaded = load_processed(out)
    pd.testing.assert_frame_equal(loaded.bars, data.bars)


def test_missing_trading_day_is_an_error(tmp_path: Path) -> None:
    days = ["2026-01-05", "2026-01-06", "2026-01-07"]
    cal = _setup(tmp_path, days, skip=("master", "2026-01-06"))
    with pytest.raises(ProcessingError, match="master: 1 trading day"):
        build_processed(tmp_path, cal, date(2026, 1, 5), date(2026, 1, 10))


def test_rows_with_wrong_date_rejected(tmp_path: Path) -> None:
    days = ["2026-01-05"]
    cal = _setup(tmp_path, days)
    _write_day(tmp_path, "bars_daily", "2026-01-05", [bar("2026-01-06", "10010", 1.0, 1)])
    with pytest.raises(ProcessingError, match="different Date"):
        build_processed(tmp_path, cal, date(2026, 1, 5), date(2026, 1, 10))
