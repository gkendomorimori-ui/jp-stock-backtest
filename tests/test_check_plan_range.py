import importlib.util
import json
import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType

from tests.test_jquants_download import make_client

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_plan_range.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_plan_range", _PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlanApi:
    """Fake API with a plan window [first, last]; requests starting before `first` -> 400."""

    def __init__(self, first: date, last: date) -> None:
        self.first, self.last = first, last
        self.trading = [
            first + timedelta(days=i)
            for i in range((last - first).days + 1)
            if (first + timedelta(days=i)).weekday() < 5
        ]
        self.calls: list[str] = []

    def __call__(
        self, url: str, headers: dict[str, str], timeout: float
    ) -> tuple[int, dict[str, str], bytes]:
        u = urllib.parse.urlparse(url)
        path, q = u.path.removeprefix("/v2"), dict(urllib.parse.parse_qsl(u.query))
        self.calls.append(path)
        refused = (400, {}, json.dumps({"message": "out of subscription range"}).encode())

        def ok(rows: list[dict[str, object]]) -> tuple[int, dict[str, str], bytes]:
            return 200, {}, json.dumps({"data": rows}).encode()

        if "from" in q:
            a, b = date.fromisoformat(q["from"]), date.fromisoformat(q["to"])
            if a < self.first:
                return refused
            days = [d for d in self.trading if a <= d <= b]
            if path == "/markets/calendar":
                return ok([{"Date": d.isoformat(), "HolDiv": "1"} for d in days])
            return ok([{"Date": d.isoformat(), "C": 1.0} for d in days])
        d = date.fromisoformat(q["date"])
        if d < self.first:
            return refused
        return ok([{"Code": "10010", "Date": q["date"]}] if d in self.trading else [])


def test_finds_plan_window_without_crossing_the_boundary() -> None:
    mod = _load()
    api = PlanApi(date(2021, 9, 29), date(2026, 9, 28))
    report = mod.run(make_client(api, []), date(2026, 9, 29))  # type: ignore[arg-type]
    assert report["topix"]["first"] == "2021-09-29"
    assert report["topix"]["last"] == "2026-09-28"
    bars = report["bars_daily"]
    assert (bars["first_available"], bars["last_available"]) == ("2021-09-29", "2026-09-28")
    assert "out of subscription range" in bars["one_week_before_first_topix"]["result"]
    assert report["master"]["first"]["result"] == "1 rows"
    assert report["calendar"]["trading_days_match_topix"] is True
    assert report["requests"] < 40
