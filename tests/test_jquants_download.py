import json
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from src.data.providers.jquants import (
    EmptyDataError,
    JQuantsClient,
    JQuantsDownloader,
    JQuantsError,
    JQuantsHTTPError,
    find_latest_available,
    read_raw,
    smoke_period,
    subtract_months,
    trading_days_from_calendar,
)

Response = tuple[int, dict[str, str], bytes]


class FakeTransport:
    """Scripted HTTP responses keyed by (path, frozen params without pagination_key)."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], list[Any]] = {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def add(self, path: str, params: dict[str, str], *responses: Any) -> None:
        self.routes[(path, json.dumps(params, sort_keys=True))] = list(responses)

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> Response:
        assert headers["x-api-key"] == "key"
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.removeprefix("/v2")
        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.calls.append((path, params))
        base = {k: v for k, v in params.items() if k != "pagination_key"}
        queue = self.routes[(path, json.dumps(base, sort_keys=True))]
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item  # type: ignore[no-any-return]


def ok(data: list[dict[str, Any]], key: str | None = None) -> Response:
    body: dict[str, Any] = {"data": data}
    if key:
        body["pagination_key"] = key
    return 200, {}, json.dumps(body).encode()


def err(status: int, headers: dict[str, str] | None = None) -> Response:
    return status, headers or {}, b'{"message": "error"}'


def make_client(transport: FakeTransport, sleeps: list[float], **kw: Any) -> JQuantsClient:
    return JQuantsClient(
        "key",
        min_interval=0,
        retry_wait=5,
        transport=transport,
        sleep=sleeps.append,
        clock=lambda: 0.0,
        log=lambda _m: None,
        **kw,
    )


D = date(2026, 6, 1)
P = {"date": "2026-06-01"}


# ------------------------------------------------------------------ client


def test_pagination_collects_all_pages() -> None:
    t = FakeTransport()
    t.add("/equities/bars/daily", P, ok([{"a": 1}], "k1"), ok([{"a": 2}], "k2"), ok([{"a": 3}]))
    pages = make_client(t, []).get_pages("/equities/bars/daily", P)
    assert [r["a"] for p in pages for r in p["data"]] == [1, 2, 3]
    assert [c[1].get("pagination_key") for c in t.calls] == [None, "k1", "k2"]


def test_rate_limit_retry_uses_retry_after() -> None:
    t = FakeTransport()
    t.add("/x", {}, err(429, {"Retry-After": "7"}), ok([{"a": 1}]))
    sleeps: list[float] = []
    assert make_client(t, sleeps).get_pages("/x", {})[0]["data"] == [{"a": 1}]
    assert sleeps == [7.0]


def test_server_and_network_errors_are_retried() -> None:
    t = FakeTransport()
    t.add("/x", {}, err(503), TimeoutError("slow"), ok([{"a": 1}]))
    sleeps: list[float] = []
    make_client(t, sleeps).get_pages("/x", {})
    assert sleeps == [5, 5]


def test_gives_up_after_max_retries() -> None:
    t = FakeTransport()
    t.add("/x", {}, err(429), err(429), err(429))
    with pytest.raises(JQuantsError, match="giving up"):
        make_client(t, [], max_retries=2).get_pages("/x", {})
    assert len(t.calls) == 3


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(status: int) -> None:
    t = FakeTransport()
    t.add("/x", {}, err(status))
    with pytest.raises(JQuantsHTTPError) as e:
        make_client(t, []).get_pages("/x", {})
    assert e.value.status == status
    assert len(t.calls) == 1


def test_invalid_json_is_an_error_not_empty_data() -> None:
    t = FakeTransport()
    t.add("/x", {}, (200, {}, b"<html>"))
    with pytest.raises(JQuantsError, match="invalid JSON"):
        make_client(t, []).get_pages("/x", {})


def test_requests_are_spaced() -> None:
    t = FakeTransport()
    t.add("/x", {}, ok([{"a": 1}], "k"), ok([{"a": 2}]))
    sleeps: list[float] = []
    JQuantsClient(
        "key",
        min_interval=13,
        transport=t,
        sleep=sleeps.append,
        clock=lambda: 100.0,
        log=lambda _m: None,
    ).get_pages("/x", {})
    assert sleeps == [13.0]


# ------------------------------------------------------------------ downloader


def test_day_is_complete_only_after_all_pages(tmp_path: Path) -> None:
    t = FakeTransport()
    t.add("/equities/bars/daily", P, ok([{"Code": "10010"}], "k1"), err(403))
    dl = JQuantsDownloader(make_client(t, []), tmp_path, log=lambda _m: None)
    with pytest.raises(JQuantsHTTPError):
        dl.download_day("bars_daily", D)
    assert not dl.day_path("bars_daily", D).exists()


def test_partial_file_is_not_treated_as_complete(tmp_path: Path) -> None:
    t = FakeTransport()
    t.add("/equities/bars/daily", P, ok([{"Code": "10010"}]))
    dl = JQuantsDownloader(make_client(t, []), tmp_path, log=lambda _m: None)
    part = dl.day_path("bars_daily", D).with_name("2026-06-01.json.gz.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(b"garbage from an interrupted run")
    assert dl.download_day("bars_daily", D) is True
    assert read_raw(dl.day_path("bars_daily", D)) == [{"Code": "10010"}]
    assert not part.exists()


def test_empty_trading_day_is_an_error(tmp_path: Path) -> None:
    t = FakeTransport()
    t.add("/equities/master", P, ok([]))
    dl = JQuantsDownloader(make_client(t, []), tmp_path, log=lambda _m: None)
    with pytest.raises(EmptyDataError):
        dl.download_day("master", D)
    assert not dl.day_path("master", D).exists()


def test_resume_skips_completed_days_without_requests(tmp_path: Path) -> None:
    days = [date(2026, 6, 1), date(2026, 6, 2)]
    t = FakeTransport()
    for d in days:
        p = {"date": d.isoformat()}
        t.add("/equities/bars/daily", p, ok([{"Code": "10010"}]))
        t.add("/equities/master", p, ok([{"Code": "10010"}]))
    dl = JQuantsDownloader(make_client(t, []), tmp_path, log=lambda _m: None)
    # first run fails on day 2 master
    t.routes[("/equities/master", json.dumps({"date": "2026-06-02"}))] = [err(500)] * 4
    with pytest.raises(JQuantsError):
        dl.download_days(days)
    assert dl.day_path("bars_daily", days[1]).exists()
    assert not dl.day_path("master", days[1]).exists()
    # resume
    t.routes[("/equities/master", json.dumps({"date": "2026-06-02"}))] = [ok([{"Code": "1"}])]
    n_calls = len(t.calls)
    report = dl.download_days(days)
    assert report.downloaded == ["master/2026-06-02"]
    assert len(report.skipped) == 3
    assert len(t.calls) == n_calls + 1


def test_calendar_is_fetched_once_for_the_range(tmp_path: Path) -> None:
    t = FakeTransport()
    rows = [
        {"Date": "2026-05-29", "HolDiv": "1"},
        {"Date": "2026-05-30", "HolDiv": "0"},
        {"Date": "2026-06-01", "HolDiv": "1"},
        {"Date": "2026-06-02", "HolDiv": "2"},
        {"Date": "2026-06-03", "HolDiv": "3"},
    ]
    t.add("/markets/calendar", {"from": "2026-05-29", "to": "2026-06-03"}, ok(rows))
    dl = JQuantsDownloader(make_client(t, []), tmp_path, log=lambda _m: None)
    p1 = dl.download_calendar(date(2026, 5, 29), date(2026, 6, 3))
    p2 = dl.download_calendar(date(2026, 5, 29), date(2026, 6, 3))
    assert p1 == p2 and len(t.calls) == 1
    assert trading_days_from_calendar(p1) == [date(2026, 5, 29), date(2026, 6, 1), date(2026, 6, 2)]


# ------------------------------------------------------------------ period


def test_subtract_months_clamps_month_end() -> None:
    assert subtract_months(date(2026, 5, 31), 3) == date(2026, 2, 28)
    assert subtract_months(date(2026, 1, 15), 3) == date(2025, 10, 15)


def test_smoke_period_is_three_months_to_latest_plus_warmup() -> None:
    days = [date(2026, 1, 1) + (date(2026, 1, 2) - date(2026, 1, 1)) * i for i in range(200)]
    latest = date(2026, 6, 15)
    period = smoke_period(days, latest, months=3, warmup=20)
    assert period.eval_end == latest
    assert period.eval_start == date(2026, 3, 15)
    assert days.index(period.eval_start) - days.index(period.download_start) == 20


def test_smoke_period_requires_warmup_coverage() -> None:
    days = [date(2026, 3, 1), date(2026, 6, 15)]
    with pytest.raises(ValueError):
        smoke_period(days, date(2026, 6, 15))


def test_latest_available_skips_unavailable_dates() -> None:
    t = FakeTransport()
    t.add("/equities/bars/daily", {"date": "2026-07-03"}, err(403))
    t.add("/equities/bars/daily", {"date": "2026-07-02"}, ok([]))
    t.add("/equities/bars/daily", {"date": "2026-07-01"}, ok([{"Code": "10010"}]))
    days = [date(2026, 7, 3), date(2026, 7, 2), date(2026, 7, 1)]
    assert find_latest_available(make_client(t, []), days) == date(2026, 7, 1)


def test_latest_available_raises_on_other_errors() -> None:
    t = FakeTransport()
    t.add("/equities/bars/daily", {"date": "2026-07-03"}, err(401))
    with pytest.raises(JQuantsHTTPError):
        find_latest_available(make_client(t, []), [date(2026, 7, 3)])


def test_topix_range_download(tmp_path: Path) -> None:
    t = FakeTransport()
    params = {"from": "2021-09-29", "to": "2026-09-28"}
    t.add(
        "/indices/bars/daily/topix",
        params,
        ok([{"Date": "2021-09-29", "C": 1.0}], "k"),
        ok([{"Date": "2026-09-28", "C": 2.0}]),
    )
    dl = JQuantsDownloader(make_client(t, []), tmp_path, log=lambda _m: None)
    path = dl.download_topix(date(2021, 9, 29), date(2026, 9, 28))
    assert path.name == "2021-09-29_2026-09-28.json.gz" and path.parent.name == "topix"
    assert [r["C"] for r in read_raw(path)] == [1.0, 2.0]
    dl.download_topix(date(2021, 9, 29), date(2026, 9, 28))
    assert len(t.calls) == 2  # second call skipped (already complete)
