"""raw files -> scripts/process_data.py --smoke -> scripts/run_backtest.py --smoke."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from src.backtest.periods import PeriodPlan, Segment
from src.data.providers.jquants import write_raw_atomic
from tests.synthetic import raw_rows
from tests.test_end_to_end import DAYS, EVAL_END, EVAL_START, build, hand_calculation

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_process_and_run_scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # raw files exactly as the downloader writes them
    raw = tmp_path / "data" / "raw"
    data = build()
    series_rows = raw_rows(DAYS, _series_from_processed(data))
    by_day: dict[str, dict[str, list[dict[str, object]]]] = {}
    for kind, rows in zip(("bars_daily", "master"), series_rows, strict=True):
        for r in rows:
            by_day.setdefault(str(r["Date"]), {}).setdefault(kind, []).append(r)
    for d, kinds in by_day.items():
        for kind, rows in kinds.items():
            write_raw_atomic(
                raw / "jquants" / kind / f"{d}.json.gz", "x", {"date": d}, [{"data": rows}]
            )
    cal_name = f"{DAYS[0]}_{DAYS[-1]}.json.gz"
    cal = [{"Date": d.isoformat(), "HolDiv": "1"} for d in DAYS]
    write_raw_atomic(raw / "jquants" / "calendar" / cal_name, "x", {}, [{"data": cal}])
    period = {
        "download_start": DAYS[0].isoformat(),
        "eval_start": DAYS[EVAL_START].isoformat(),
        "eval_end": DAYS[EVAL_END].isoformat(),
        "warmup_days": 20,
        "calendar_file": f"data/raw/jquants/calendar/{cal_name}",
    }
    (raw / "jquants" / "smoke_period.json").write_text(json.dumps(period))

    process = _load("process_data")
    monkeypatch.setattr(process, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(process, "RAW_ROOT", raw)
    monkeypatch.setattr(process, "OUT_DIR", tmp_path / "data" / "processed" / "jquants")
    assert process.main(["--smoke"]) == 0

    run = _load("run_backtest")
    monkeypatch.setattr(run, "PROJECT_ROOT", tmp_path)
    # synthetic dates: treat the smoke period as the viewed segment of a test plan
    test_plan = PeriodPlan(
        DAYS[0],
        DAYS[-1],
        20,
        (
            Segment("initial_warmup", DAYS[0], DAYS[EVAL_START - 1], 20, "warmup"),
            Segment(
                "viewed_reference",
                DAYS[EVAL_START],
                DAYS[EVAL_END],
                EVAL_END - EVAL_START + 1,
                "viewed",
            ),
        ),
    )
    monkeypatch.setattr(run, "load_period_plan", lambda: test_plan)
    monkeypatch.setattr(run, "PROCESSED", tmp_path / "data" / "processed" / "jquants")
    assert run.main(["--smoke"]) == 0

    runs = list((tmp_path / "results" / "runs").iterdir())
    assert len(runs) == 1
    summary = json.loads((runs[0] / "summary.json").read_text(encoding="utf-8"))
    assert summary["metadata"]["run_type"] == "smoke_test"
    assert summary["metrics"]["final_equity"] == pytest.approx(hand_calculation()["final"])
    assert any("Smoke test" in n for n in summary["metadata"]["notes"])


def test_process_fails_when_a_day_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "data" / "raw"
    cal_name = f"{DAYS[0]}_{DAYS[-1]}.json.gz"
    cal = [{"Date": d.isoformat(), "HolDiv": "1"} for d in DAYS]
    write_raw_atomic(raw / "jquants" / "calendar" / cal_name, "x", {}, [{"data": cal}])
    period = {
        "download_start": DAYS[0].isoformat(),
        "eval_start": DAYS[EVAL_START].isoformat(),
        "eval_end": DAYS[EVAL_END].isoformat(),
        "warmup_days": 20,
        "calendar_file": f"data/raw/jquants/calendar/{cal_name}",
    }
    (raw / "jquants" / "smoke_period.json").write_text(json.dumps(period))
    process = _load("process_data")
    monkeypatch.setattr(process, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(process, "RAW_ROOT", raw)
    monkeypatch.setattr(process, "OUT_DIR", tmp_path / "out")
    assert process.main(["--smoke"]) == 1
    assert not (tmp_path / "out").exists()


def _series_from_processed(data: object) -> dict[str, dict[str, list[object]]]:
    bars = data.bars  # type: ignore[attr-defined]
    out: dict[str, dict[str, list[object]]] = {}
    for sym, g in bars.groupby("symbol"):
        g = g.sort_values("date")
        out[str(sym)] = {
            "open": g["open"].tolist(),
            "high": g["high"].tolist(),
            "low": g["low"].tolist(),
            "close": g["close"].tolist(),
            "volume": g["volume"].tolist(),
        }
    return out


@pytest.mark.parametrize(
    "args",
    [
        ["--period", "final_evaluation"],
        ["--period", "holdout"],
        ["--period", "final_evaluation", "--run-type", "development", "--open-sealed-period"],
        ["--start", "2024-01-04", "--end", "2024-06-28"],
        ["--period", "initial_warmup"],
    ],
)
def test_run_script_refuses_sealed_or_mixed_periods(
    args: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run = _load("run_backtest")
    monkeypatch.setattr(run, "PROCESSED", tmp_path / "nothing")  # data must not even be loaded
    assert run.main(args) == 1
    assert "REFUSED" in capsys.readouterr().out
