import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.test_jquants_download import FakeTransport, err, make_client, ok

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "download_data.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("download_data_script", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _weekdays(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    mod = _load()
    t = FakeTransport()
    cal_start, cal_end = date(2026, 1, 5), date(2026, 7, 3)
    days = _weekdays(cal_start, cal_end)
    cal_rows = [{"Date": d.isoformat(), "HolDiv": "1"} for d in days]
    # the first calendar request (end=cal_end+3) is refused, the shrunk one succeeds
    t.add("/markets/calendar", {"from": "2026-01-05", "to": "2026-07-06"}, err(403))
    t.add("/markets/calendar", {"from": "2026-01-05", "to": "2026-07-03"}, ok(cal_rows))
    # latest available: 07-03 (refused) and 07-02 (empty) unavailable, 07-01 available
    probe: dict[date, list[Any]] = {
        date(2026, 7, 3): [err(403)],
        date(2026, 7, 2): [ok([])],
        date(2026, 7, 1): [ok([{"Code": "10010"}])],
    }
    for d in days:
        p = {"date": d.isoformat()}
        row = ok([{"Code": "10010", "Date": d.isoformat()}])
        t.add("/equities/bars/daily", p, *probe.get(d, []), row)
        t.add("/equities/master", p, ok([{"Code": "10010", "Date": d.isoformat()}]))
    raw = tmp_path / "data" / "raw"
    monkeypatch.setattr(mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(mod, "RAW_ROOT", raw)
    monkeypatch.setattr(mod, "SMOKE_PERIOD_FILE", raw / "jquants" / "smoke_period.json")
    monkeypatch.setattr(mod, "free_plan_calendar_window", lambda _t: (cal_start, date(2026, 7, 6)))
    monkeypatch.setattr(mod, "JQuantsClient", lambda _k, **_kw: make_client(t, []))
    monkeypatch.setenv("JQUANTS_API_KEY", "key")
    return {"mod": mod, "t": t, "raw": raw}


def test_smoke_download_determines_period_and_resumes(env: dict[str, Any]) -> None:
    mod, t, raw = env["mod"], env["t"], env["raw"]
    assert mod.main(["--smoke"]) == 0
    period = json.loads((raw / "jquants" / "smoke_period.json").read_text())
    assert period["latest_available"] == "2026-07-01"
    assert period["eval_end"] == "2026-07-01"
    assert period["eval_start"] == "2026-04-01"
    days = _weekdays(date(2026, 1, 5), date(2026, 7, 1))
    assert (
        days.index(date(2026, 4, 1)) - days.index(date.fromisoformat(period["download_start"]))
        == 20
    )
    bars = sorted((raw / "jquants" / "bars_daily").glob("*.json.gz"))
    assert bars[0].name.startswith(period["download_start"])
    assert bars[-1].name.startswith("2026-07-01")
    # second run reuses the saved period and makes no requests
    n = len(t.calls)
    assert mod.main(["--smoke"]) == 0
    assert len(t.calls) == n


def test_failure_returns_error_and_keeps_completed_days(env: dict[str, Any]) -> None:
    mod, t, raw = env["mod"], env["t"], env["raw"]
    t.routes[("/equities/master", json.dumps({"date": "2026-04-01"}))] = [err(401)]
    assert mod.main(["--smoke"]) == 1
    assert (raw / "jquants" / "bars_daily" / "2026-04-01.json.gz").exists()
    assert not (raw / "jquants" / "master" / "2026-04-01.json.gz").exists()
    assert not (raw / "jquants" / "bars_daily" / "2026-04-02.json.gz").exists()
