"""Data quality checks on the processed dataset (no strategy results are computed).

Each check yields a finding with a level:
    ERROR -- the data cannot be used as is (processing / download problem)
    WARN  -- needs a human look before evaluation
    INFO  -- descriptive statistics
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from src.data.market_data import MarketData
from src.data.processing import ProcessedData
from src.universe import UniverseRules, eligibility, security_eligible

PRICE = ["open", "high", "low", "close"]


@dataclass
class Finding:
    """One quality finding."""

    level: str
    check: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


def _pct(a: float, b: float) -> float:
    return round(100.0 * a / b, 4) if b else 0.0


def check_quality(
    data: ProcessedData,
    rules: UniverseRules,
    known_markets: set[str],
    segments: list[tuple[str, date, date]] | None = None,
    jump_threshold: float = 0.5,
) -> list[Finding]:
    """Run every check. ``segments`` = (name, start, end) for per-segment coverage stats."""
    out: list[Finding] = []
    bars, master, cal = data.bars, data.master, data.calendar
    tdays = pd.DatetimeIndex(cal.loc[cal["is_trading_day"], "date"]).sort_values()

    # 1. coverage per trading day ------------------------------------------------------
    per_bar = bars.groupby("date").size().reindex(tdays, fill_value=0)
    per_master = master.groupby("date").size().reindex(tdays, fill_value=0)
    empty = [d.date().isoformat() for d in tdays[(per_bar == 0) | (per_master == 0)]]
    out.append(
        Finding(
            "ERROR" if empty else "INFO",
            "coverage",
            f"{len(tdays)} trading days; bar rows per day min {per_bar.min()} / median "
            f"{int(per_bar.median())} / max {per_bar.max()}",
            {"days_without_rows": empty[:20], "min_day": str(per_bar.idxmin())[:10]},
        )
    )
    extra_days = sorted(set(bars["date"]) - set(tdays))
    if extra_days:
        out.append(
            Finding(
                "ERROR",
                "coverage",
                "bars on non-trading days",
                {"dates": [str(d.date()) for d in extra_days[:20]]},
            )
        )
    keys_b = bars[["date", "symbol"]].assign(_b=1)
    keys_m = master[["date", "symbol"]].assign(_m=1)
    j = keys_b.merge(keys_m, on=["date", "symbol"], how="outer")
    only_b, only_m = int(j["_m"].isna().sum()), int(j["_b"].isna().sum())
    out.append(
        Finding(
            "ERROR" if only_b or only_m else "INFO",
            "bars_vs_master",
            f"(date, symbol) only in bars: {only_b}; only in master: {only_m}",
        )
    )

    # 2. no-trade rows -----------------------------------------------------------------
    nt = bars["close"].isna()
    nt_share = nt.astype(float).groupby(bars["date"]).mean().reindex(tdays, fill_value=0.0)
    worst_day = pd.Timestamp(str(nt_share.idxmax()))
    out.append(
        Finding(
            "WARN" if (nt_share > 0.2).any() else "INFO",
            "no_trade_rows",
            f"{int(nt.sum())} rows without trades ({_pct(nt.sum(), len(bars))}%); worst day "
            f"{worst_day.date()} ({round(100 * float(nt_share.max()), 2)}%)",
            {
                "days_over_20pct": [
                    d.date().isoformat() for d in tdays[(nt_share > 0.2).to_numpy()]
                ][:20]
            },
        )
    )
    partial = bars[bars[PRICE].isna().any(axis=1) & bars[PRICE].notna().any(axis=1)]
    if len(partial):
        out.append(
            Finding("WARN", "no_trade_rows", f"{len(partial)} rows with only some OHLC values")
        )

    # 3. price / volume sanity ---------------------------------------------------------
    traded = bars[~nt]
    bad_price = int((traded[PRICE] <= 0).any(axis=1).sum())
    inconsistent = int(
        (
            (traded["low"] > traded[["open", "close"]].min(axis=1))
            | (traded["high"] < traded[["open", "close"]].max(axis=1))
        ).sum()
    )
    no_vol = int((traded["volume"].fillna(0) <= 0).sum())
    no_val = int((traded["turnover"].fillna(0) <= 0).sum())
    level = "ERROR" if bad_price or inconsistent else ("WARN" if no_vol or no_val else "INFO")
    out.append(
        Finding(
            level,
            "price_sanity",
            f"traded rows {len(traded)}: non-positive price {bad_price}, OHLC inconsistent "
            f"{inconsistent}, volume<=0 {no_vol}, turnover<=0 {no_val}",
        )
    )

    # 4. adjustment ---------------------------------------------------------------------
    ev = bars[bars["adj_factor"] != 1.0]
    out.append(
        Finding(
            "INFO",
            "adjustment",
            f"{len(ev)} split / reverse-split events on {ev['symbol'].nunique()} symbols",
            {
                "factor_counts": {
                    str(k): int(v) for k, v in ev["adj_factor"].value_counts().head(10).items()
                }
            },
        )
    )
    md = MarketData.from_processed(data)
    sec_tbl = security_eligible(md, rules)
    elig_tbl = eligibility(md, rules)

    cmp = traded.dropna(subset=["api_adj_close"])
    diff = (cmp["adj_close"] - cmp["api_adj_close"]).abs()
    rel = (diff / cmp["api_adj_close"]).fillna(0)
    mism = rel > 1e-6
    by_01 = mism & (diff <= 0.05 + 1e-6)  # API rounds adjusted prices to 0.1 yen
    by_1 = mism & ~by_01 & (diff <= 0.5 + 1e-6)
    unexplained = mism & ~by_01 & ~by_1
    api_on_01_grid = (
        ((cmp.loc[mism, "api_adj_close"] * 10).round(6) % 1 == 0).mean() if mism.any() else 1.0
    )
    worst = cmp.assign(rel=rel, abs_diff=diff)[unexplained if unexplained.any() else mism]
    worst = worst.nlargest(10, "rel")[
        ["date", "symbol", "adj_close", "api_adj_close", "abs_diff", "rel"]
    ]
    out.append(
        Finding(
            "WARN" if unexplained.any() else "INFO",
            "adjustment_vs_api",
            "recomputed adj_close vs API AdjC: "
            f"{_pct((~mism).sum(), len(rel))}% equal (within 1e-6); of {int(mism.sum())} "
            f"differing rows, {int(by_01.sum())} are within 0.05 yen (0.1-yen rounding), "
            f"{int(by_1.sum())} within 0.5 yen, {int(unexplained.sum())} unexplained; "
            f"max relative diff {rel.max():.3g}",
            {
                "api_values_on_0.1_yen_grid_among_differing_rows": round(float(api_on_01_grid), 4),
                "examples": _records(worst),
            },
        )
    )

    # 5. large moves not explained by a split ---------------------------------------------
    s = traded.sort_values(["symbol", "date"]).copy()
    g = s.groupby("symbol")
    s["prev_adj_close"] = g["adj_close"].shift(1)
    s["prev_close"] = g["close"].shift(1)
    s["prev_api"] = g["api_adj_close"].shift(1)
    s["prev_date"] = g["date"].shift(1)
    s["ret"] = s["adj_close"] / s["prev_adj_close"] - 1
    jumps = s[s["ret"].abs() > jump_threshold].copy()
    pos = pd.Series(np.arange(len(tdays)), index=tdays)
    first_seen_all = master.groupby("symbol")["date"].min()
    near_split_tbl = (
        (md["adj_factor"].fillna(1.0) != 1.0)
        .astype(int)
        .rolling(11, center=True, min_periods=1)
        .max()
        .astype(bool)
    )
    names = master.drop_duplicates("symbol", keep="last").set_index("symbol")["name"]
    cats: list[str] = []
    rows_out: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = jumps.to_dict("records")  # type: ignore[assignment]
    for r in records:
        d, sym = r["date"], str(r["symbol"])
        gap = int(pos[d] - pos[r["prev_date"]]) if pd.notna(r["prev_date"]) else 0
        api_ratio = float(r["api_adj_close"]) / float(r["prev_api"]) if r["prev_api"] else np.nan
        api_ok = (
            bool(abs(api_ratio / (1 + float(r["ret"])) - 1) < 0.02)
            if np.isfinite(api_ratio)
            else False
        )
        in_uni = bool(sec_tbl.at[d, sym]) if sym in sec_tbl.columns else False
        new_listing = int(pos[d] - pos.get(first_seen_all.get(sym, d), pos[d])) <= 5
        near_split = bool(near_split_tbl.at[d, sym]) if sym in near_split_tbl.columns else False
        if not in_uni:
            cat = "not_in_universe"
        elif new_listing:
            cat = "within_5_days_of_listing"
        elif gap > 1:
            cat = "after_no_trade_days"
        elif near_split:
            cat = "within_5_days_of_split"
        elif float(r["prev_close"]) < 10:
            cat = "price_below_10_yen"
        elif not api_ok:
            cat = "api_adjusted_disagrees"
        else:
            cat = "matches_api_in_universe"
        cats.append(cat)
        if in_uni:
            rows_out.append(
                {
                    "date": d.date().isoformat(),
                    "symbol": sym,
                    "name": names.get(sym),
                    "prev_close": r["prev_close"],
                    "close": r["close"],
                    "ret": round(float(r["ret"]), 4),
                    "category": cat,
                    "buy_eligible_that_day": bool(elig_tbl.at[d, sym]),
                }
            )
    cat_counts = pd.Series(cats, dtype="object").value_counts().to_dict() if cats else {}
    suspicious = cat_counts.get("api_adjusted_disagrees", 0) + cat_counts.get(
        "within_5_days_of_split", 0
    )
    rows_out.sort(key=lambda x: -abs(x["ret"]))
    out.append(
        Finding(
            "WARN" if suspicious else "INFO",
            "large_moves",
            f"{len(jumps)} day-over-day adjusted moves beyond +/-{int(jump_threshold * 100)}% "
            f"({jumps['symbol'].nunique() if len(jumps) else 0} symbols); by category: "
            f"{ {str(k): int(v) for k, v in cat_counts.items()} }",
            {
                "in_universe": rows_out[:40],
                "in_universe_count": len(rows_out),
                "in_universe_buy_eligible_that_day": sum(
                    x["buy_eligible_that_day"] for x in rows_out
                ),
            },
        )
    )

    # 6. market / product classification -------------------------------------------------
    unknown = sorted(set(master["market_code"]) - known_markets)
    out.append(
        Finding(
            "WARN" if unknown else "INFO",
            "market_codes",
            f"market codes seen: {sorted(set(master['market_code']))}; unknown: {unknown}",
        )
    )
    for label, d in _snapshot_days(tdays):
        snap = master[master["date"] == d]
        counts = snap.groupby(["market_code", "product_category"]).size().reset_index(name="n")
        out.append(
            Finding(
                "INFO",
                "market_snapshot",
                f"{label} ({d.date()}): {len(snap)} listed",
                {
                    f"{m}/{pc}": int(n)
                    for m, pc, n in zip(
                        counts["market_code"], counts["product_category"], counts["n"], strict=True
                    )
                },
            )
        )
    tgt = master[master["market_code"].isin(rules.market_codes)]
    in_prod = tgt["product_category"].isin(rules.product_categories)
    suffix = tgt["symbol"].str.len().eq(5) & tgt["symbol"].str[4].eq(rules.common_stock_code_suffix)
    uncls = tgt[in_prod & ~suffix]
    ucl = (
        uncls.groupby("symbol")
        .agg(name=("name", "first"), first=("date", "min"), last=("date", "max"))
        .reset_index()
    )
    named_pref = tgt[in_prod & suffix & tgt["name"].fillna("").str.contains("優先")]
    out.append(
        Finding(
            "WARN" if len(named_pref) else "INFO",
            "common_stock_rule",
            f"unclassified (ProdCat 011, 5th digit != 0) in target markets: {len(ucl)} symbols; "
            f"'common' rows whose name contains 優先: {named_pref['symbol'].nunique()} symbols",
            {
                "unclassified": _records(ucl),
                "named_preferred": sorted(set(named_pref["symbol"]))[:20],
            },
        )
    )

    # 7. listing changes ----------------------------------------------------------------
    last_seen = master.groupby("symbol")["date"].max()
    first_seen = master.groupby("symbol")["date"].min()
    out.append(
        Finding(
            "INFO",
            "listing_changes",
            "symbols leaving the master before the last day: "
            f"{int((last_seen < tdays[-1]).sum())}; "
            f"joining after the first day: {int((first_seen > tdays[0]).sum())}",
        )
    )

    # 8. universe coverage (eligibility only; no signals, no trades) ----------------------
    sec = sec_tbl.sum(axis=1)
    elig = elig_tbl.sum(axis=1)
    rows = []
    for name, a, b in segments or [("all", tdays[0].date(), tdays[-1].date())]:
        m = (sec.index >= pd.Timestamp(a)) & (sec.index <= pd.Timestamp(b))
        rows.append(
            {
                "segment": name,
                "securities_min": int(sec[m].min()),
                "securities_max": int(sec[m].max()),
                "eligible_min": int(elig[m].min()),
                "eligible_median": int(elig[m].median()),
                "eligible_max": int(elig[m].max()),
            }
        )
    out.append(
        Finding("INFO", "universe_coverage", "buy-eligible securities per day", {"segments": rows})
    )

    # 9. TOPIX ---------------------------------------------------------------------------
    if data.topix is None:
        out.append(Finding("WARN", "topix", "TOPIX not in processed data"))
    else:
        t = data.topix
        match = list(pd.DatetimeIndex(t["date"])) == list(tdays)
        bad = int((t[PRICE] <= 0).any(axis=1).sum() + t[PRICE].isna().any(axis=1).sum())
        out.append(
            Finding(
                "ERROR" if not match or bad else "INFO",
                "topix",
                f"{len(t)} rows; dates match trading days: {match}; invalid rows: {bad}",
                {"first": str(t["date"].min().date()), "last": str(t["date"].max().date())},
            )
        )
    return out


def _snapshot_days(tdays: pd.DatetimeIndex) -> list[tuple[str, pd.Timestamp]]:
    boundary = pd.Timestamp("2022-04-04")
    out = [("first day", tdays[0])]
    before = tdays[tdays < boundary]
    after = tdays[tdays >= boundary]
    if len(before) and len(after):
        out += [("last day before 2022-04-04", before[-1]), ("first day from 2022-04-04", after[0])]
    out.append(("last day", tdays[-1]))
    return out


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    for row in df.to_dict("records"):
        recs.append(
            {
                str(k): (
                    v.date().isoformat()
                    if isinstance(v, pd.Timestamp)
                    else (round(float(v), 6) if isinstance(v, float | np.floating) else v)
                )
                for k, v in row.items()
            }
        )
    return recs
