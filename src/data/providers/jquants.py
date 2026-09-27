"""J-Quants API V2 client and raw-data downloader.

Guarantees (docs/DATA_SOURCES.md):
    * A day counts as downloaded only when ALL pages were fetched and the file was written
      atomically. Partial downloads never leave a completed file behind.
    * Failures (HTTP errors, network errors, invalid JSON) are raised, never turned into
      empty data. An empty response for a trading day is also an error.
    * HTTP 429 / 5xx / network errors are retried with a wait; other HTTP errors are not.
    * Already-downloaded days are skipped, so a download can be resumed at any time.
    * The trading calendar is fetched once per range, not per day.
"""

from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

BASE_URL = "https://api.jquants.com/v2"

#: Dataset kind -> API path.
ENDPOINTS: dict[str, str] = {
    "bars_daily": "/equities/bars/daily",
    "master": "/equities/master",
    "calendar": "/markets/calendar",
}

#: HolDiv values that are TSE trading days (1 = business day, 2 = half-day session).
TRADING_HOLDIV: frozenset[str] = frozenset({"1", "2"})

#: (status, headers, body). Status 0 is never returned; network failures raise OSError.
Transport = Callable[[str, dict[str, str], float], tuple[int, dict[str, str], bytes]]


class JQuantsError(Exception):
    """Base error for J-Quants access."""


class JQuantsHTTPError(JQuantsError):
    """Non-success HTTP status (after retries where applicable)."""

    def __init__(self, status: int, path: str, params: dict[str, str], body: str) -> None:
        """Store status and context."""
        super().__init__(f"HTTP {status} for {path} {params}: {body[:300]}")
        self.status = status
        self.body = body


class EmptyDataError(JQuantsError):
    """A request that must return rows returned none."""


def urllib_transport(
    url: str, headers: dict[str, str], timeout: float
) -> tuple[int, dict[str, str], bytes]:
    """Default transport using urllib. HTTP errors are returned, network errors raise."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as e:
        return int(e.code), dict(e.headers.items()) if e.headers else {}, e.read()


class JQuantsClient:
    """Minimal J-Quants V2 client: request spacing, retries and pagination."""

    def __init__(
        self,
        api_key: str,
        *,
        min_interval: float = 13.0,
        retry_wait: float = 65.0,
        max_retries: int = 3,
        timeout: float = 60.0,
        transport: Transport = urllib_transport,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        log: Callable[[str], None] = print,
    ) -> None:
        """Configure the client.

        Args:
            api_key: J-Quants API key (sent as ``x-api-key``).
            min_interval: Minimum seconds between requests (Free plan: 5 requests/minute).
            retry_wait: Seconds to wait before retrying 429 / 5xx / network errors
                (a numeric ``Retry-After`` header takes precedence).
            max_retries: Retries per request before giving up.
            timeout: Socket timeout in seconds.
            transport: Injected HTTP function (tests use a fake).
            sleep: Injected sleep function.
            clock: Injected monotonic clock.
            log: Progress/diagnostic output.
        """
        if not api_key:
            raise JQuantsError("API key is empty")
        self._api_key = api_key
        self.min_interval = min_interval
        self.retry_wait = retry_wait
        self.max_retries = max_retries
        self.timeout = timeout
        self._transport = transport
        self._sleep = sleep
        self._clock = clock
        self._log = log
        self._last: float | None = None
        self.request_count = 0

    def get_pages(self, path: str, params: dict[str, str]) -> list[dict[str, Any]]:
        """Return every page of ``path`` (following ``pagination_key``).

        Raises:
            JQuantsError: On any failure. Nothing partial is returned.
        """
        pages: list[dict[str, Any]] = []
        query = dict(params)
        seen_keys: set[str] = set()
        while True:
            body = self._request_json(path, query)
            pages.append(body)
            key = body.get("pagination_key")
            if not key:
                return pages
            if key in seen_keys:
                raise JQuantsError(f"pagination_key repeated for {path} {params}")
            seen_keys.add(str(key))
            query["pagination_key"] = str(key)

    def _request_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
        headers = {"x-api-key": self._api_key}
        last_error = ""
        for attempt in range(self.max_retries + 1):
            self._space()
            self.request_count += 1
            try:
                status, resp_headers, body = self._transport(url, headers, self.timeout)
            except OSError as e:  # network error / timeout
                last_error = f"network error: {e}"
                wait = self.retry_wait
            else:
                if status == 200:
                    return _parse_body(body, path, params)
                text = body.decode("utf-8", errors="replace")
                if status == 429 or status >= 500:
                    last_error = f"HTTP {status}: {text[:200]}"
                    wait = _retry_after(resp_headers) or self.retry_wait
                else:
                    raise JQuantsHTTPError(status, path, params, text)
            if attempt < self.max_retries:
                self._log(f"  retry {attempt + 1}/{self.max_retries} in {wait:.0f}s ({last_error})")
                self._sleep(wait)
        raise JQuantsError(f"giving up on {path} {params} after retries: {last_error}")

    def _space(self) -> None:
        if self._last is not None:
            wait = self.min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()


def _parse_body(body: bytes, path: str, params: dict[str, str]) -> dict[str, Any]:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise JQuantsError(f"invalid JSON from {path} {params}: {e}") from e
    if not isinstance(parsed, dict) or not isinstance(parsed.get("data"), list):
        raise JQuantsError(f"unexpected response shape from {path} {params}")
    return parsed


def _retry_after(headers: dict[str, str]) -> float | None:
    for k, v in headers.items():
        if k.lower() == "retry-after":
            try:
                return float(v)
            except ValueError:
                return None
    return None


# --------------------------------------------------------------------------- raw files


def write_raw_atomic(
    path: Path, endpoint: str, params: dict[str, str], pages: list[dict[str, Any]]
) -> int:
    """Write all pages to ``path`` atomically (``.part`` then rename). Returns the row count."""
    rows = sum(len(p["data"]) for p in pages)
    payload = {
        "endpoint": endpoint,
        "params": params,
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "page_count": len(pages),
        "row_count": rows,
        "pages": pages,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)
    return rows


def read_raw(path: Path) -> list[dict[str, Any]]:
    """Read a raw file and return its rows, verifying the stored row count."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        payload = json.load(f)
    rows: list[dict[str, Any]] = [r for p in payload["pages"] for r in p["data"]]
    if len(rows) != payload["row_count"]:
        raise JQuantsError(f"row count mismatch in {path}")
    return rows


# --------------------------------------------------------------------------- downloader


@dataclass
class DownloadReport:
    """Summary of a download run."""

    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class JQuantsDownloader:
    """Download raw J-Quants data into ``<raw_root>/jquants/`` with resume support."""

    def __init__(
        self, client: JQuantsClient, raw_root: Path, log: Callable[[str], None] = print
    ) -> None:
        """Store client and target directory."""
        self.client = client
        self.root = raw_root / "jquants"
        self._log = log

    def day_path(self, kind: str, day: date) -> Path:
        """Path of the completed file for ``kind`` on ``day``."""
        return self.root / kind / f"{day.isoformat()}.json.gz"

    def calendar_path(self, start: date, end: date) -> Path:
        """Path of the completed calendar file for a range."""
        return self.root / "calendar" / f"{start.isoformat()}_{end.isoformat()}.json.gz"

    def download_calendar(self, start: date, end: date) -> Path:
        """Fetch the trading calendar for ``start..end`` in one request (skipped if present)."""
        path = self.calendar_path(start, end)
        if path.exists():
            return path
        params = {"from": start.isoformat(), "to": end.isoformat()}
        pages = self.client.get_pages(ENDPOINTS["calendar"], params)
        if sum(len(p["data"]) for p in pages) == 0:
            raise EmptyDataError(f"calendar {start}..{end} returned no rows")
        write_raw_atomic(path, ENDPOINTS["calendar"], params, pages)
        return path

    def download_day(self, kind: str, day: date) -> bool:
        """Download ``kind`` for one trading day. Returns False if it was already complete.

        Raises:
            EmptyDataError: If the API returned no rows for a trading day.
            JQuantsError: On any request failure. No completed file is written.
        """
        path = self.day_path(kind, day)
        if path.exists():
            return False
        params = {"date": day.isoformat()}
        pages = self.client.get_pages(ENDPOINTS[kind], params)
        if sum(len(p["data"]) for p in pages) == 0:
            raise EmptyDataError(f"{kind} on trading day {day} returned no rows")
        write_raw_atomic(path, ENDPOINTS[kind], params, pages)
        return True

    def download_days(
        self, days: Iterable[date], kinds: Iterable[str] = ("bars_daily", "master")
    ) -> DownloadReport:
        """Download ``kinds`` for each day in order; stops at the first error."""
        report = DownloadReport()
        days = list(days)
        for i, day in enumerate(days, start=1):
            for kind in kinds:
                label = f"{kind}/{day}"
                if self.download_day(kind, day):
                    report.downloaded.append(label)
                    self._log(f"[{i}/{len(days)}] downloaded {label}")
                else:
                    report.skipped.append(label)
        return report


def trading_days_from_calendar(path: Path) -> list[date]:
    """Trading days (HolDiv 1 or 2) in a raw calendar file, ascending."""
    return sorted(
        date.fromisoformat(r["Date"])
        for r in read_raw(path)
        if str(r.get("HolDiv")) in TRADING_HOLDIV
    )


# --------------------------------------------------------------------------- smoke period


@dataclass(frozen=True)
class Period:
    """Download and evaluation dates for a run."""

    download_start: date
    eval_start: date
    eval_end: date
    warmup_days: int

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form."""
        return {
            "download_start": self.download_start.isoformat(),
            "eval_start": self.eval_start.isoformat(),
            "eval_end": self.eval_end.isoformat(),
            "warmup_days": self.warmup_days,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Period:
        """Inverse of :meth:`to_dict`."""
        return cls(
            date.fromisoformat(d["download_start"]),
            date.fromisoformat(d["eval_start"]),
            date.fromisoformat(d["eval_end"]),
            int(d["warmup_days"]),
        )


def subtract_months(d: date, months: int) -> date:
    """Same day ``months`` earlier, clamped to the month's last day."""
    y, m = divmod(d.year * 12 + (d.month - 1) - months, 12)
    m += 1
    for day in range(d.day, 0, -1):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError("unreachable")


def smoke_period(
    trading_days: list[date], latest: date, months: int = 3, warmup: int = 20
) -> Period:
    """Evaluation = about ``months`` ending at ``latest``; plus ``warmup`` trading days before it.

    ``eval_start`` is the first trading day on/after ``latest - months``. ``download_start`` is
    the trading day ``warmup`` days before ``eval_start``.

    Raises:
        ValueError: If the calendar does not cover the warm-up or ``latest``.
    """
    if latest not in trading_days:
        raise ValueError(f"latest {latest} is not a trading day in the calendar")
    target = subtract_months(latest, months)
    eval_idx = next((i for i, d in enumerate(trading_days) if d >= target), None)
    if eval_idx is None or eval_idx < warmup:
        raise ValueError("calendar does not cover the warm-up period")
    return Period(trading_days[eval_idx - warmup], trading_days[eval_idx], latest, warmup)


def find_latest_available(
    client: JQuantsClient, trading_days_desc: list[date], max_probe: int = 10
) -> date:
    """Latest trading day whose daily bars are available (probing backwards).

    Only in this probe, an empty response or HTTP 400/403 means "not available for this
    date"; any other error is raised.
    """
    for day in trading_days_desc[:max_probe]:
        try:
            pages = client.get_pages(ENDPOINTS["bars_daily"], {"date": day.isoformat()})
        except JQuantsHTTPError as e:
            if e.status in (400, 403):
                continue
            raise
        if sum(len(p["data"]) for p in pages) > 0:
            return day
    raise JQuantsError(f"no available daily bars in the latest {max_probe} trading days")


def free_plan_calendar_window(today: date) -> tuple[date, date]:
    """Calendar range to request on the Free plan (data is 12 weeks delayed, ~2 years deep).

    A few days of margin are kept on both ends of the documented window.
    """
    end = today - timedelta(weeks=12)
    start = end - timedelta(days=200)
    return start, end
