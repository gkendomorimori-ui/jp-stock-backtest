from datetime import date, timedelta
from typing import Any

import pytest

from src.backtest.periods import PeriodError, PeriodPlan
from src.utils.config_loader import load_yaml

REPO_PLAN = PeriodPlan.from_config(load_yaml("config/backtest.yaml"))


def jpx_trading_days(start: date, end: date) -> list[date]:
    jpholiday = pytest.importorskip("jpholiday")
    out, d = [], start
    while d <= end:
        closed = (
            d.weekday() >= 5
            or jpholiday.is_holiday(d)
            or (d.month, d.day) in {(12, 31), (1, 1), (1, 2), (1, 3)}
        )
        if not closed:
            out.append(d)
        d += timedelta(days=1)
    return out


# ------------------------------------------------------------------ the repository plan


def test_repository_plan_dates() -> None:
    got = {s.name: (s.start.isoformat(), s.end.isoformat(), s.status) for s in REPO_PLAN.segments}
    assert got == {
        "initial_warmup": ("2021-09-29", "2021-10-26", "warmup"),
        "development": ("2021-10-27", "2024-04-02", "open"),
        "final_evaluation": ("2024-04-03", "2026-04-02", "sealed"),
        "viewed_reference": ("2026-04-03", "2026-07-03", "viewed"),
        "holdout": ("2026-07-06", "2026-09-28", "sealed"),
    }


def test_repository_plan_matches_jpx_holiday_calendar() -> None:
    days = jpx_trading_days(date(2021, 9, 1), date(2026, 10, 30))
    assert REPO_PLAN.validate(days) == []
    in_range = [d for d in days if date(2021, 9, 29) <= d <= date(2026, 9, 28)]
    assert len(in_range) == 1221  # = Light-plan calendar total checked on 2026-09-29
    assert REPO_PLAN.indicator_window("final_evaluation", in_range) == (
        date(2024, 3, 5),
        date(2024, 4, 2),
    )
    assert REPO_PLAN.indicator_window("viewed_reference", in_range) == (
        date(2026, 3, 5),
        date(2026, 4, 2),
    )  # = the smoke test's warm-up


# ------------------------------------------------------------------ validation


def weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DAYS = weekdays(date(2026, 1, 5), 70)


def plan(**override: Any) -> PeriodPlan:
    split = {
        "initial_warmup": [DAYS[0], DAYS[19], 20, "warmup"],
        "development": [DAYS[20], DAYS[39], 20, "open"],
        "final_evaluation": [DAYS[40], DAYS[59], 20, "sealed"],
        "holdout": [DAYS[60], DAYS[69], 10, "sealed"],
    }
    split.update(override)
    raw = {
        "period": {
            "data_start": DAYS[0],
            "data_end": DAYS[69],
            "warmup_trading_days": 20,
            "split": {
                k: {"start_date": a, "end_date": b, "trading_days": n, "status": st}
                for k, (a, b, n, st) in split.items()
            },
        }
    }
    return PeriodPlan.from_config(raw)


def test_valid_plan() -> None:
    assert plan().validate(DAYS) == []


def test_wrong_count_detected() -> None:
    assert any(
        "config says 21" in p
        for p in plan(development=[DAYS[20], DAYS[39], 21, "open"]).validate(DAYS)
    )


def test_gap_between_segments_detected() -> None:
    problems = plan(final_evaluation=[DAYS[41], DAYS[59], 19, "sealed"]).validate(DAYS)
    assert any("not contiguous" in p for p in problems)


def test_non_trading_boundary_detected() -> None:
    saturday = DAYS[39] + timedelta(days=1)
    problems = plan(development=[DAYS[20], saturday, 20, "open"]).validate(DAYS)
    assert any("not a trading day" in p for p in problems)


def test_short_warmup_detected() -> None:
    p = plan(
        initial_warmup=[DAYS[0], DAYS[18], 19, "warmup"],
        development=[DAYS[19], DAYS[39], 21, "open"],
    )
    assert any("warm-up is 19 days" in x for x in p.validate(DAYS))


# ------------------------------------------------------------------ run guard


def test_development_runs_allowed() -> None:
    assert (
        REPO_PLAN.check_run(date(2021, 10, 27), date(2024, 4, 2), "development").name
        == "development"
    )


def test_viewed_period_only_for_smoke_test() -> None:
    assert (
        REPO_PLAN.check_run(date(2026, 4, 3), date(2026, 7, 3), "smoke_test").name
        == "viewed_reference"
    )
    with pytest.raises(PeriodError):
        REPO_PLAN.check_run(date(2026, 4, 3), date(2026, 7, 3), "development")


@pytest.mark.parametrize(
    "start,end",
    [(date(2024, 4, 3), date(2026, 4, 2)), (date(2026, 7, 6), date(2026, 9, 28))],
)
def test_sealed_periods_refused(start: date, end: date) -> None:
    for run_type in ("development", "smoke_test", "final_evaluation", "holdout"):
        with pytest.raises(PeriodError, match="sealed"):
            REPO_PLAN.check_run(start, end, run_type)


def test_sealed_period_needs_flag_and_matching_run_type() -> None:
    with pytest.raises(PeriodError, match="final_evaluation"):
        REPO_PLAN.check_run(date(2024, 4, 3), date(2026, 4, 2), "development", open_sealed=True)
    seg = REPO_PLAN.check_run(
        date(2024, 4, 3), date(2026, 4, 2), "final_evaluation", open_sealed=True
    )
    assert seg.name == "final_evaluation"
    with pytest.raises(PeriodError, match="holdout"):
        REPO_PLAN.check_run(
            date(2026, 7, 6), date(2026, 9, 28), "final_evaluation", open_sealed=True
        )


def test_runs_spanning_segments_are_refused() -> None:
    with pytest.raises(PeriodError, match="single segment"):
        REPO_PLAN.check_run(date(2024, 1, 4), date(2024, 6, 28), "development")
    with pytest.raises(PeriodError, match="single segment"):
        REPO_PLAN.check_run(date(2026, 4, 3), date(2026, 9, 28), "smoke_test")


def test_warmup_never_runs() -> None:
    with pytest.raises(PeriodError):
        REPO_PLAN.check_run(date(2021, 9, 29), date(2021, 10, 26), "development")
