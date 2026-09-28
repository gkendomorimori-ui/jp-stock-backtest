import importlib.util
from datetime import date
from pathlib import Path
from types import ModuleType

from tests.test_jquants_download import FakeTransport, err, make_client, ok

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_plan_range.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_plan_range", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_finds_first_and_last_available_days() -> None:
    mod = _load()
    t = FakeTransport()
    today = date(2026, 9, 29)
    days = ["2021-07-26", "2021-07-27", "2021-07-28", "2026-09-25", "2026-09-28"]
    from datetime import timedelta

    start = (today - timedelta(days=5 * 365 + 60)).isoformat()
    t.add(
        "/indices/bars/daily/topix",
        {"from": start, "to": "2026-09-29"},
        ok([{"Date": d, "C": 1.0} for d in days]),
    )
    t.add("/equities/bars/daily", {"date": "2021-07-26"}, err(403))
    t.add("/equities/bars/daily", {"date": "2021-07-27"}, ok([]))
    t.add("/equities/bars/daily", {"date": "2021-07-28"}, ok([{"Code": "1"}]))
    t.add("/equities/bars/daily", {"date": "2026-09-28"}, ok([]))
    t.add("/equities/bars/daily", {"date": "2026-09-25"}, ok([{"Code": "1"}, {"Code": "2"}]))
    t.add("/equities/bars/daily", {"date": "2021-07-19"}, err(403))
    t.add("/equities/master", {"date": "2021-07-28"}, ok([{"Code": "1"}]))
    t.add("/equities/master", {"date": "2026-09-25"}, ok([{"Code": "1"}]))
    cal = [{"Date": "2021-07-28", "HolDiv": "1"}, {"Date": "2026-09-25", "HolDiv": "1"}]
    t.add("/markets/calendar", {"from": "2021-07-28", "to": "2026-09-25"}, ok(cal))

    report = mod.run(make_client(t, []), today)
    assert report["topix"] == {"first": "2021-07-26", "last": "2026-09-28", "rows": 5}
    bars = report["bars_daily"]
    assert (bars["first_available"], bars["last_available"]) == ("2021-07-28", "2026-09-25")
    assert bars["probe_first"] == [
        "2021-07-26: HTTP 403",
        "2021-07-27: 0 rows",
        "2021-07-28: 1 rows",
    ]
    assert bars["one_week_before_first_topix"]["result"] == "HTTP 403"
    assert report["master"]["last"]["result"] == "1 rows"
    assert report["calendar"]["trading_days_match_topix"] is True
