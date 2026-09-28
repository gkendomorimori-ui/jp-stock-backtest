"""Evaluation period plan (config/backtest.yaml ``period``) and run guard.

Rules (docs/EVALUATION_PLAN.md):
    * All dates are inclusive exchange trading days; segments are contiguous and disjoint.
    * Each segment starts fresh (initial capital, no positions, no carried-over orders).
    * A run must lie inside ONE segment; results of different segments are never merged.
    * ``sealed`` segments (final evaluation, holdout) cannot be run unless explicitly opened
      with the matching run type; ``viewed`` only for re-running the smoke test; the
      initial warm-up never.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

#: Segment status -> run types allowed without opening anything.
_ALLOWED: dict[str, set[str]] = {
    "open": {"development", "smoke_test"},
    "viewed": {"smoke_test"},
    "sealed": set(),
    "warmup": set(),
}
#: Sealed segment name -> run type required when it is explicitly opened.
_SEALED_RUN_TYPE: dict[str, str] = {"final_evaluation": "final_evaluation", "holdout": "holdout"}


class PeriodError(ValueError):
    """The period plan is inconsistent or a run is not allowed."""


@dataclass(frozen=True)
class Segment:
    """One evaluation segment (inclusive dates)."""

    name: str
    start: date
    end: date
    trading_days: int
    status: str


@dataclass(frozen=True)
class PeriodPlan:
    """Parsed ``period`` section."""

    data_start: date
    data_end: date
    warmup: int
    segments: tuple[Segment, ...]

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> PeriodPlan:
        """Build from the parsed config/backtest.yaml."""
        p = raw["period"]
        segs = [
            Segment(
                name=name,
                start=_d(v["start_date"]),
                end=_d(v["end_date"]),
                trading_days=int(v["trading_days"]),
                status=str(v["status"]),
            )
            for name, v in p["split"].items()
            if isinstance(v, dict)
        ]
        segs.sort(key=lambda s: s.start)
        return cls(
            _d(p["data_start"]), _d(p["data_end"]), int(p["warmup_trading_days"]), tuple(segs)
        )

    def segment(self, name: str) -> Segment:
        """Segment by name."""
        for s in self.segments:
            if s.name == name:
                return s
        raise PeriodError(f"unknown segment {name!r}")

    # ------------------------------------------------------------------ validation

    def validate(self, trading_days: list[date]) -> list[str]:
        """Check the plan against exchange trading days. Returns a list of problems."""
        days = sorted(d for d in trading_days if self.data_start <= d <= self.data_end)
        problems: list[str] = []
        if not days or days[0] != self.data_start or days[-1] != self.data_end:
            problems.append("data_start / data_end are not the first / last trading days")
        index = {d: i for i, d in enumerate(days)}
        covered = 0
        prev_end: int | None = None
        for s in self.segments:
            if s.start not in index or s.end not in index:
                problems.append(f"{s.name}: start or end is not a trading day")
                continue
            a, b = index[s.start], index[s.end]
            n = b - a + 1
            covered += n
            if n != s.trading_days:
                problems.append(f"{s.name}: {n} trading days, config says {s.trading_days}")
            if prev_end is not None and a != prev_end + 1:
                problems.append(f"{s.name}: not contiguous with the previous segment")
            prev_end = b
            if s.status == "warmup":
                if n != self.warmup:
                    problems.append(f"{s.name}: warm-up is {n} days, expected {self.warmup}")
            elif a < self.warmup:
                problems.append(f"{s.name}: fewer than {self.warmup} trading days before start")
        if covered != len(days):
            problems.append(f"segments cover {covered} of {len(days)} trading days")
        return problems

    def indicator_window(self, name: str, trading_days: list[date]) -> tuple[date, date]:
        """The ``warmup`` trading days immediately before a segment (indicators only)."""
        days = sorted(trading_days)
        i = days.index(self.segment(name).start)
        return days[i - self.warmup], days[i - 1]

    # ------------------------------------------------------------------ guard

    def check_run(
        self, start: date, end: date, run_type: str, open_sealed: bool = False
    ) -> Segment:
        """Return the segment of a run, or raise :class:`PeriodError` if it is not allowed."""
        seg = next((s for s in self.segments if s.start <= start and end <= s.end), None)
        if seg is None:
            raise PeriodError(
                f"{start}..{end} is not inside a single segment; results of different "
                "segments must not be merged"
            )
        if run_type in _ALLOWED.get(seg.status, set()):
            return seg
        if seg.status == "sealed":
            required = _SEALED_RUN_TYPE.get(seg.name)
            if not open_sealed:
                raise PeriodError(
                    f"{seg.name} ({seg.start}..{seg.end}) is sealed: its results must not be "
                    "computed yet (see docs/EVALUATION_PLAN.md)"
                )
            if run_type != required:
                raise PeriodError(f"{seg.name} can only be opened with run_type {required!r}")
            return seg
        raise PeriodError(f"run_type {run_type!r} is not allowed on {seg.name} ({seg.status})")


def _d(v: Any) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))
