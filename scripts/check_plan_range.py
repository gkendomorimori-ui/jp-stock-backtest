"""Check which dates the current J-Quants plan can return (no strategy results involved).

    python scripts/check_plan_range.py

Steps (a few dozen requests):
    1. TOPIX: find the latest available window (stepping back from today), then the
       earliest available start date by bisection (requests outside the plan's range are
       refused with HTTP 400/403, so no single request spans the unknown boundary), then
       fetch the whole available range once (TOPIX records exist only on trading days).
    2. Daily bars: probe forward from the first TOPIX date to the first date with data,
       and backward from the last TOPIX date to the latest date with data.
    3. Listed master on the first / last bar dates.
    4. Trading calendar over the found range (one request).

The report is printed and saved to data/raw/jquants/plan_range.json (git-ignored).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.providers.jquants import (  # noqa: E402
    ENDPOINTS,
    TRADING_HOLDIV,
    JQuantsClient,
    JQuantsError,
    JQuantsHTTPError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_FILE = PROJECT_ROOT / "data" / "raw" / "jquants" / "plan_range.json"
TOPIX_PATH = "/indices/bars/daily/topix"


def load_api_key() -> str:
    """Read JQUANTS_API_KEY from the environment or the project's .env file."""
    key = os.environ.get("JQUANTS_API_KEY", "").strip()
    env_file = PROJECT_ROOT / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("JQUANTS_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        raise SystemExit("JQUANTS_API_KEY is not set (.env or environment variable)")
    return key


def rows(client: JQuantsClient, path: str, params: dict[str, str]) -> list[dict[str, Any]] | str:
    """All rows, or a short string describing why the request was refused (400/403)."""
    try:
        return [r for p in client.get_pages(path, params) for r in p["data"]]
    except JQuantsHTTPError as e:
        if e.status in (400, 403):
            return f"HTTP {e.status}: {_message(e.body)}"
        raise


def _message(body: str) -> str:
    try:
        return str(json.loads(body).get("message", body))[:200]
    except (ValueError, AttributeError):
        return body[:200]


def _ok(got: list[dict[str, Any]] | str) -> bool:
    return isinstance(got, list) and len(got) > 0


def topix_range(client: JQuantsClient, today: date, log: list[str]) -> tuple[date, date]:
    """(earliest, latest) TOPIX dates available, found without crossing the boundary."""
    latest: date | None = None
    for k in range(20):
        to = today - timedelta(days=7 * k)
        got = rows(
            client,
            TOPIX_PATH,
            {"from": (to - timedelta(days=20)).isoformat(), "to": to.isoformat()},
        )
        log.append(f"latest window to {to}: {len(got) if isinstance(got, list) else got}")
        if _ok(got):
            assert isinstance(got, list)
            latest = max(date.fromisoformat(r["Date"]) for r in got)
            break
    if latest is None:
        raise SystemExit("TOPIX: no available window in the last ~20 weeks\n" + "\n".join(log))

    def valid(x: date) -> bool:
        got = rows(
            client, TOPIX_PATH, {"from": x.isoformat(), "to": (x + timedelta(days=14)).isoformat()}
        )
        log.append(f"start {x}: {len(got) if isinstance(got, list) else got}")
        return _ok(got)

    lo, hi = today - timedelta(days=7 * 365), latest - timedelta(days=30)
    if valid(lo):
        hi = lo
    else:
        while (hi - lo).days > 1:
            mid = lo + (hi - lo) / 2
            if valid(mid):
                hi = mid
            else:
                lo = mid
    return hi, latest


def probe(client: JQuantsClient, days: list[str], max_tries: int) -> tuple[str | None, list[str]]:
    """First day in ``days`` whose daily bars are non-empty; also returns the probe log."""
    log: list[str] = []
    for d in days[:max_tries]:
        got = rows(client, ENDPOINTS["bars_daily"], {"date": d})
        if isinstance(got, list) and got:
            log.append(f"{d}: {len(got)} rows")
            return d, log
        log.append(f"{d}: {'0 rows' if isinstance(got, list) else got}")
    return None, log


def run(client: JQuantsClient, today: date, max_tries: int = 15) -> dict[str, Any]:
    """Collect the availability report."""
    report: dict[str, Any] = {"checked_on": today.isoformat()}
    search_log: list[str] = []
    start, end = topix_range(client, today, search_log)
    topix = rows(client, TOPIX_PATH, {"from": start.isoformat(), "to": end.isoformat()})
    if isinstance(topix, str) or not topix:
        raise SystemExit(f"TOPIX full range refused: {topix}")
    tdays = sorted(r["Date"] for r in topix)
    report["topix"] = {
        "first": tdays[0],
        "last": tdays[-1],
        "rows": len(tdays),
        "search_log": search_log,
    }

    first, log_first = probe(client, tdays, max_tries)
    last, log_last = probe(client, list(reversed(tdays)), max_tries)
    before = (date.fromisoformat(tdays[0]) - timedelta(days=7)).isoformat()
    outside = rows(client, ENDPOINTS["bars_daily"], {"date": before})
    report["bars_daily"] = {
        "first_available": first,
        "last_available": last,
        "probe_first": log_first,
        "probe_last": log_last,
        "one_week_before_first_topix": {
            "date": before,
            "result": f"{len(outside)} rows" if isinstance(outside, list) else outside,
        },
    }
    for label, d in (("first", first), ("last", last)):
        if d:
            m = rows(client, ENDPOINTS["master"], {"date": d})
            report.setdefault("master", {})[label] = {
                "date": d,
                "result": f"{len(m)} rows" if isinstance(m, list) else m,
            }
    if first and last:
        cal = rows(client, ENDPOINTS["calendar"], {"from": first, "to": last})
        if isinstance(cal, list):
            trading = [r["Date"] for r in cal if str(r.get("HolDiv")) in TRADING_HOLDIV]
            report["calendar"] = {
                "first": cal[0]["Date"] if cal else None,
                "last": cal[-1]["Date"] if cal else None,
                "trading_days": len(trading),
                "trading_days_match_topix": sorted(trading)
                == [d for d in tdays if first <= d <= last],
            }
        else:
            report["calendar"] = {"result": cal}
    report["requests"] = client.request_count
    return report


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    client = JQuantsClient(load_api_key(), min_interval=1.2)
    try:
        report = run(client, date.today())
    except JQuantsError as e:
        print(f"ERROR: {e}")
        return 1
    report["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"saved: {OUT_FILE.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
