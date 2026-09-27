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

Free plan: data is delayed ~12 weeks and limited to ~2 years; the default request
interval (13 s) respects the Free plan limit of 5 requests/minute. Results of a
Free-plan check are a smoke test, not an evaluation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

BASE_URL = "https://api.jquants.com/v2"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

TARGET_MARKETS = {"0111", "0112", "0113", "0101", "0102", "0104", "0106", "0107"}
COMMON_STOCK_PRODCAT = "011"  # domestic equities (also contains preferred shares)
KNOWN_EXCLUDED_PRODCAT = {"012", "013", "014", "021", "022", "023", "024"}
BUSINESS_DAY_HOLDIV = {"1", "2"}


class Client:
    """Minimal J-Quants V2 client with pagination and request spacing."""

    def __init__(self, api_key: str, min_interval: float, out_dir: Path) -> None:
        """Store key, request interval (seconds) and where to save raw responses."""
        self.api_key = api_key
        self.min_interval = min_interval
        self.out_dir = out_dir
        self._last = 0.0
        self.requests = 0

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
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0 and self.requests > 0:
            time.sleep(wait)
        url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"x-api-key": self.api_key})
        self._last = time.monotonic()
        self.requests += 1
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
                return result
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            raise SystemExit(f"HTTP {e.code} for {path} {params}: {detail}") from e


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


def classify(row: dict[str, Any]) -> str:
    """Classify a master row for the universe.

    Returns one of ``out_of_market``, ``excluded_prodcat``, ``common_candidate``,
    ``unclassified``. Nothing is guessed into ``common_candidate``: ProdCat 011 rows whose
    5-digit code does not end in "0" (possible preferred / other share classes) and
    unknown ProdCat values are ``unclassified``.
    """
    if str(row.get("Mkt")) not in TARGET_MARKETS:
        return "out_of_market"
    prod = str(row.get("ProdCat"))
    code = str(row.get("Code", ""))
    if prod in KNOWN_EXCLUDED_PRODCAT:
        return "excluded_prodcat"
    if prod == COMMON_STOCK_PRODCAT and len(code) == 5 and code.endswith("0"):
        return "common_candidate"
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

    section("4. delisted securities")
    old_date = (check_date - timedelta(days=args.compare_days)).isoformat()
    old_cal = client.get_all(
        "/markets/calendar",
        {"from": (date.fromisoformat(old_date) - timedelta(days=10)).isoformat(), "to": old_date},
        "calendar_old",
    )
    old_business = sorted(r["Date"] for r in old_cal if str(r.get("HolDiv")) in BUSINESS_DAY_HOLDIV)
    if old_business:
        old_master = client.get_all("/equities/master", {"date": old_business[-1]}, "master_old")
        now_codes = {r["Code"] for r in master}
        gone = [
            r for r in old_master if r["Code"] not in now_codes and classify(r) != "out_of_market"
        ]
        print(
            f"in master on {old_business[-1]} but not on {snap}: {len(gone)} "
            "(CANDIDATES only - delisting, code change, merger etc.; confirm officially)"
        )
        for r in gone[:15]:
            print(f"  {r.get('Code')} {r.get('CoName')} Mkt={r.get('Mkt')}")
    if args.delisted_code and args.listed_date:
        code = args.delisted_code
        listed = client.get_all(
            "/equities/master", {"date": args.listed_date, "code": code}, "delisted_master"
        )
        print(f"master on {args.listed_date} for {code}: {len(listed)} row(s)")
        for r in listed:
            print({k: r.get(k) for k in ("Date", "Code", "CoName", "Mkt", "MktNm", "ProdCat")})
        start = (date.fromisoformat(args.listed_date) - timedelta(days=14)).isoformat()
        dbars = client.get_all(
            "/equities/bars/daily", {"code": code, "from": start, "to": snap}, "delisted_bars"
        )
        valid = [r for r in dbars if r.get("C") is not None]
        print(f"daily bars {start}..{snap}: {len(dbars)} rows, {len(valid)} with close")
        if valid:
            print(f"  first: {valid[0]['Date']}  last: {valid[-1]['Date']} close={valid[-1]['C']}")
        in_now = any(r["Code"] == code for r in master)
        print(f"present in master on {snap}: {in_now}")
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


if __name__ == "__main__":
    sys.exit(main())
