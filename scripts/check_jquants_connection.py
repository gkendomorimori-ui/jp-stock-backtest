"""J-Quants API V2 connection check (small data only).

This is a verification tool, NOT the data pipeline. It makes a small number of
requests and prints a report to paste back for review. Raw responses are saved
under ``data/raw/jquants/connection_check/<timestamp>/`` (git-ignored).

Checks:
    1. Trading calendar  (/v2/markets/calendar)
    2. Daily bars sample (/v2/equities/bars/daily)
    3. Listed master     (/v2/equities/master) -> Mkt x ProdCat counts, classification
       of securities into "common stock candidate / excluded / unclassified"
    4. Delisted security (--delisted-code / --listed-date): master on a date when it was
       listed, and daily bars around that date. Also lists candidates found by comparing
       two master snapshots (candidates only; confirm against an official delisting list).
    5. TOPIX              (/v2/indices/bars/daily/topix)

Usage (PowerShell):
    python scripts/check_jquants_connection.py --date 2026-06-01
    python scripts/check_jquants_connection.py --date 2026-06-01 `
        --delisted-code 12340 --listed-date 2025-03-03
    python scripts/check_jquants_connection.py --only delisted `
        --delisted-code 12340 --listed-date 2025-03-03

Free plan: data is delayed ~12 weeks and limited to ~2 years; the default request
interval (13 s) respects the Free plan limit of 5 requests/minute. Results of a
Free-plan check are a smoke test, not an evaluation.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.data.providers.jquants import load_api_key as _load_api_key  # noqa: E402

BASE_URL = "https://api.jquants.com/v2"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

TARGET_MARKETS = {"0111", "0112", "0113", "0101", "0102", "0104", "0106", "0107"}
COMMON_STOCK_PRODCAT = "011"  # domestic equities (also contains preferred shares)
KNOWN_EXCLUDED_PRODCAT = {"012", "013", "014", "021", "022", "023", "024"}
BUSINESS_DAY_HOLDIV = {"1", "2"}


class Client:
    """Minimal J-Quants V2 client with pagination and request spacing."""

    def __init__(
        self,
        api_key: str | None,
        min_interval: float,
        out_dir: Path,
        retry_wait: float = 65.0,
        max_retries: int = 3,
    ) -> None:
        """Store key, request spacing, raw-response directory and 429 retry policy."""
        self.api_key = api_key
        self.min_interval = min_interval
        self.out_dir = out_dir
        self.retry_wait = retry_wait
        self.max_retries = max_retries
        self._last = 0.0
        self.requests = 0
        self._sleep = time.sleep

    def get_all(self, path: str, params: dict[str, str], save_as: str) -> list[dict[str, Any]]:
        """GET ``path`` following ``pagination_key``; save each page as JSON."""
        rows: list[dict[str, Any]] = []
        page = 0
        query = dict(params)
        while True:
            body = self._get(path, query)
            (self.out_dir / f"{save_as}_{page}.json").write_text(
                json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            rows.extend(body.get("data", []))
            key = body.get("pagination_key")
            if not key:
                return rows
            query["pagination_key"] = key
            page += 1

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        """GET one page; on HTTP 429 wait and retry up to ``max_retries`` times."""
        for attempt in range(self.max_retries + 1):
            try:
                return self._request(path, params)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")[:500]
                if e.code == 429 and attempt < self.max_retries:
                    wait = _retry_after(e) or self.retry_wait
                    print(f"  (rate limited; waiting {wait:.0f}s, retry {attempt + 1})")
                    self._sleep(wait)
                    continue
                raise SystemExit(f"HTTP {e.code} for {path} {params}: {detail}") from e
        raise AssertionError("unreachable")

    def _request(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0 and self.requests > 0:
            self._sleep(wait)
        url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
        headers = {} if self.api_key is None else {"x-api-key": self.api_key}
        req = urllib.request.Request(url, headers=headers)
        self._last = time.monotonic()
        self.requests += 1
        with urllib.request.urlopen(req, timeout=60) as resp:
            result: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
            return result


def _retry_after(error: urllib.error.HTTPError) -> float | None:
    """Return the Retry-After header in seconds, if present and numeric."""
    value = error.headers.get("Retry-After") if error.headers else None
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def load_api_key() -> str | None:
    """API key, or None when an outbound proxy attaches it (see docs/CLOUD_SETUP.md)."""
    return _load_api_key(PROJECT_ROOT)


def classify(row: dict[str, Any]) -> str:
    """Classify a master row for the universe.

    Returns one of ``out_of_market``, ``excluded_prodcat``, ``common_stock``,
    ``unclassified``.

    Rule (docs/DATA_SOURCES.md): common stock = ProdCat 011 AND 5th code digit "0".
    Under the JPX securities code rules common stock has no suffix code (J-Quants shows
    it as "0"); preferred shares use 5/6 and other share classes 1-4/7-9. Rows that do
    not match (including unknown ProdCat values) are ``unclassified`` and are never
    included as common stock.
    """
    if str(row.get("Mkt")) not in TARGET_MARKETS:
        return "out_of_market"
    prod = str(row.get("ProdCat"))
    code = str(row.get("Code", ""))
    if prod in KNOWN_EXCLUDED_PRODCAT:
        return "excluded_prodcat"
    if prod == COMMON_STOCK_PRODCAT and len(code) == 5 and code.endswith("0"):
        return "common_stock"
    return "unclassified"


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    default_date = (date.today() - timedelta(days=100)).isoformat()
    parser.add_argument(
        "--date", default=default_date, help="check date (Free plan: >12 weeks ago)"
    )
    parser.add_argument("--code", default="72030", help="sample code for daily bars")
    parser.add_argument("--delisted-code", help="5-digit code of a KNOWN delisted security")
    parser.add_argument("--listed-date", help="a date when --delisted-code was still listed")
    parser.add_argument(
        "--compare-days", type=int, default=365, help="days back for delisting-candidate search"
    )
    parser.add_argument("--min-interval", type=float, default=13.0, help="seconds between requests")
    parser.add_argument(
        "--only",
        choices=["all", "delisted"],
        default="all",
        help="'delisted' runs only the calendar, delisted-security and TOPIX checks",
    )
    return parser.parse_args()


def section(title: str) -> None:
    """Print a section header."""
    print(f"\n=== {title} ===")


def main() -> int:
    """Run all checks and print a report."""
    args = parse_args()
    api_key = load_api_key()
    check_date = date.fromisoformat(args.date)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = PROJECT_ROOT / "data" / "raw" / "jquants" / "connection_check" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    client = Client(api_key, args.min_interval, out_dir)

    section("1. trading calendar")
    cal = client.get_all(
        "/markets/calendar",
        {"from": (check_date - timedelta(days=40)).isoformat(), "to": check_date.isoformat()},
        "calendar",
    )
    print("HolDiv counts:", dict(Counter(str(r.get("HolDiv")) for r in cal)))
    business = sorted(r["Date"] for r in cal if str(r.get("HolDiv")) in BUSINESS_DAY_HOLDIV)
    if not business:
        raise SystemExit("no business day found in calendar; try another --date")
    snap = business[-1]
    print(f"business days: {len(business)}; master snapshot date = {snap}")

    if args.only == "all":
        check_bars_and_master(client, args, business, snap)

    section("4. delisted securities")
    if args.only == "all":
        list_delisting_candidates(client, args, check_date, snap)
    if args.delisted_code and args.listed_date:
        check_delisted(client, args.delisted_code, args.listed_date, snap)
    else:
        print("(pass --delisted-code and --listed-date to test a KNOWN delisted security)")

    section("5. TOPIX")
    topix = client.get_all(
        "/indices/bars/daily/topix",
        {"from": business[max(0, len(business) - 5)], "to": snap},
        "topix",
    )
    print(f"rows: {len(topix)}")
    for r in topix[-3:]:
        print({k: r.get(k) for k in ("Date", "O", "H", "L", "C")})

    section("done")
    print(
        f"requests: {client.requests}; raw responses saved to {out_dir.relative_to(PROJECT_ROOT)}"
    )
    print("This is a smoke test (connection check), not an evaluation result.")
    return 0


def check_bars_and_master(
    client: Client, args: argparse.Namespace, business: list[str], snap: str
) -> None:
    """Sections 2 and 3: daily bars sample and master classification."""
    section(f"2. daily bars sample ({args.code})")
    bars = client.get_all(
        "/equities/bars/daily",
        {"code": args.code, "from": business[max(0, len(business) - 10)], "to": snap},
        "bars",
    )
    print(f"rows: {len(bars)}")
    if bars:
        print("fields:", sorted(bars[0].keys()))
        for r in bars[-3:]:
            print(
                {
                    k: r.get(k)
                    for k in ("Date", "Code", "O", "H", "L", "C", "Vo", "Va", "AdjC", "AdjFactor")
                }
            )
    null_rows = [r["Date"] for r in bars if r.get("C") is None]
    print("rows with null close:", null_rows or "none")

    section(f"3. listed master on {snap}")
    master = client.get_all("/equities/master", {"date": snap}, "master")
    print(f"rows: {len(master)}")
    print("Mkt x ProdCat:")
    for (mkt, prod), n in sorted(
        Counter((f"{r.get('Mkt')} {r.get('MktNm')}", str(r.get("ProdCat"))) for r in master).items()
    ):
        print(f"  {mkt:<24} ProdCat={prod}: {n}")
    classes = Counter(classify(r) for r in master)
    print("classification:", dict(classes))
    unclassified = [r for r in master if classify(r) == "unclassified"]
    print(f"UNCLASSIFIED (not included as common stock): {len(unclassified)}")
    for r in unclassified[:30]:
        print(f"  {r.get('Code')} {r.get('CoName')} Mkt={r.get('Mkt')} ProdCat={r.get('ProdCat')}")

    common = [r for r in master if classify(r) == "common_stock"]
    named_pref = [r for r in common if "優先" in str(r.get("CoName", ""))]
    print(f"sanity: common_stock rows whose name contains 優先: {len(named_pref)}")
    for r in named_pref[:10]:
        print(f"  {r.get('Code')} {r.get('CoName')}")
    fifth = Counter(
        str(r.get("Code", ""))[4:5]
        for r in master
        if str(r.get("Mkt")) in TARGET_MARKETS and str(r.get("ProdCat")) == COMMON_STOCK_PRODCAT
    )
    print("5th code digit among ProdCat 011 in target markets:", dict(sorted(fifth.items())))


def list_delisting_candidates(
    client: Client, args: argparse.Namespace, check_date: date, snap: str
) -> None:
    """Compare two master snapshots and list codes that disappeared (candidates only)."""
    old_date = (check_date - timedelta(days=args.compare_days)).isoformat()
    old_cal = client.get_all(
        "/markets/calendar",
        {"from": (date.fromisoformat(old_date) - timedelta(days=10)).isoformat(), "to": old_date},
        "calendar_old",
    )
    old_business = sorted(r["Date"] for r in old_cal if str(r.get("HolDiv")) in BUSINESS_DAY_HOLDIV)
    if not old_business:
        return
    master = client.get_all("/equities/master", {"date": snap}, "master_for_compare")
    old_master = client.get_all("/equities/master", {"date": old_business[-1]}, "master_old")
    now_codes = {r["Code"] for r in master}
    gone = [r for r in old_master if r["Code"] not in now_codes and classify(r) != "out_of_market"]
    print(
        f"in master on {old_business[-1]} but not on {snap}: {len(gone)} "
        "(CANDIDATES only - delisting, code change, merger etc.; confirm officially)"
    )
    for r in gone[:15]:
        print(f"  {r.get('Code')} {r.get('CoName')} Mkt={r.get('Mkt')}")


def check_delisted(client: Client, code: str, listed_date: str, snap: str) -> None:
    """Check master and daily bars of a KNOWN delisted security."""
    listed = client.get_all(
        "/equities/master", {"date": listed_date, "code": code}, "delisted_master"
    )
    print(f"master on {listed_date} for {code}: {len(listed)} row(s)")
    for r in listed:
        print({k: r.get(k) for k in ("Date", "Code", "CoName", "Mkt", "MktNm", "ProdCat")})
    start = (date.fromisoformat(listed_date) - timedelta(days=14)).isoformat()
    dbars = client.get_all(
        "/equities/bars/daily", {"code": code, "from": start, "to": snap}, "delisted_bars"
    )
    valid = [r for r in dbars if r.get("C") is not None]
    print(f"daily bars {start}..{snap}: {len(dbars)} rows, {len(valid)} with close")
    if valid:
        print(f"  first: {valid[0]['Date']}  last: {valid[-1]['Date']} close={valid[-1]['C']}")
    now = client.get_all("/equities/master", {"date": snap, "code": code}, "delisted_master_now")
    print(f"present in master on {snap}: {bool(now)}")


if __name__ == "__main__":
    sys.exit(main())
