"""Cross-comparison of open-cpap-parser output against OSCAR reference data.

This module aligns parsed daily summaries with OSCAR CSV export rows by date
and computes per-day deltas for each metric.  Out-of-tolerance days are flagged
so they appear prominently in the generated reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from cpap_parser.schema import CPAPDirectory, CPAPSessionSummary
from cpap_parser.validation.oscar_reader import OscarDaySummary


@dataclass(frozen=True)
class Tolerances:
    """Acceptable deviation thresholds between parser and OSCAR values.

    Attributes:
        ahi: Maximum allowed AHI delta (events / hour).
        pressure: Maximum allowed pressure delta (cmH₂O).
        usage_hours: Maximum allowed usage time delta (hours).
        leak: Maximum allowed leak delta (L/min).
    """

    ahi: float = 0.1
    pressure: float = 0.2
    usage_hours: float = 0.1
    leak: float = 5.0


DEFAULT_TOLERANCES = Tolerances()


@dataclass
class DayDiff:
    """Per-day comparison result between parser output and OSCAR.

    Attributes:
        date: ISO date string (``YYYY-MM-DD``).
        parser_ahi: AHI from open-cpap-parser.
        oscar_ahi: AHI from OSCAR CSV export.
        ahi_delta: Absolute difference (parser − OSCAR), or ``None`` if either
            value is unavailable.
        parser_pressure_95: 95th-percentile pressure from parser.
        oscar_pressure_95: 95th-percentile pressure from OSCAR.
        pressure_95_delta: Absolute difference, or ``None``.
        parser_usage_hours: Usage hours from parser.
        oscar_usage_hours: Usage hours from OSCAR.
        usage_hours_delta: Absolute difference, or ``None``.
        parser_leak_95: 95th-percentile leak from parser.
        oscar_leak_95: 95th-percentile leak from OSCAR.
        leak_95_delta: Absolute difference, or ``None``.
        within_tolerance: ``True`` when every available metric delta is within
            the configured tolerance.
    """

    date: str
    parser_ahi: float
    oscar_ahi: float
    ahi_delta: Optional[float]
    parser_pressure_95: Optional[float]
    oscar_pressure_95: Optional[float]
    pressure_95_delta: Optional[float]
    parser_usage_hours: float
    oscar_usage_hours: float
    usage_hours_delta: Optional[float]
    parser_leak_95: Optional[float]
    oscar_leak_95: Optional[float]
    leak_95_delta: Optional[float]
    within_tolerance: bool


@dataclass
class CompareResult:
    """Complete comparison result for one sample (one SD card directory).

    Attributes:
        sample_name: Human-readable label for this data sample.
        diffs: Per-day comparison records, sorted by date.
        total_days: Number of days present in both parser and OSCAR output.
        days_within_tolerance: Days where all available metrics are in tolerance.
        parser_only_dates: Dates present in parser output but not in OSCAR.
        oscar_only_dates: Dates present in OSCAR export but not in parser output.
        tolerances: Tolerance thresholds used for this comparison.
    """

    sample_name: str
    diffs: list[DayDiff]
    total_days: int
    days_within_tolerance: int
    parser_only_dates: list[str]
    oscar_only_dates: list[str]
    tolerances: Tolerances = field(default_factory=Tolerances)

    @property
    def pass_rate(self) -> float:
        """Fraction of matched days that are within tolerance (0.0–1.0)."""
        return self.days_within_tolerance / self.total_days if self.total_days else 0.0

    @property
    def passed(self) -> bool:
        """``True`` when all matched days are within tolerance."""
        return self.days_within_tolerance == self.total_days and self.total_days > 0


def _abs_delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return abs(a - b)


def compare(
    parsed: CPAPDirectory,
    oscar_summaries: list[OscarDaySummary],
    sample_name: str,
    tolerances: Tolerances = DEFAULT_TOLERANCES,
) -> CompareResult:
    """Compare open-cpap-parser output against OSCAR daily summaries.

    Aligns records by date, computes per-metric absolute deltas, and flags
    days that exceed the configured tolerances.

    Args:
        parsed: Output from open-cpap-parser (a :class:`CPAPDirectory`).
        oscar_summaries: Reference data read from an OSCAR CSV export.
        sample_name: Label used in reports and log messages.
        tolerances: Per-metric deviation thresholds.

    Returns:
        A :class:`CompareResult` with per-day diffs and aggregate statistics.
    """
    # Normalize dates to ISO strings — CPAPSessionSummary.date is datetime.date
    # while OscarDaySummary.date is already a str.
    parsed_by_date: dict[str, CPAPSessionSummary] = {
        str(s.date): s for s in parsed.daily_summaries
    }
    oscar_by_date: dict[str, OscarDaySummary] = {
        s.date: s for s in oscar_summaries
    }

    all_dates = sorted(set(parsed_by_date) | set(oscar_by_date))
    parser_only = sorted(set(parsed_by_date) - set(oscar_by_date))
    oscar_only = sorted(set(oscar_by_date) - set(parsed_by_date))
    matched_dates = sorted(set(parsed_by_date) & set(oscar_by_date))

    diffs: list[DayDiff] = []
    days_ok = 0

    for date in matched_dates:
        p = parsed_by_date[date]
        o = oscar_by_date[date]

        ahi_delta = _abs_delta(p.ahi, o.ahi)
        p95_delta = _abs_delta(p.pressure_95, o.pressure_95)
        usage_delta = _abs_delta(p.usage_hours, o.usage_hours)
        leak95_delta = _abs_delta(p.leak_95, o.leak_95)

        within = True
        if ahi_delta is not None and ahi_delta > tolerances.ahi:
            within = False
        if p95_delta is not None and p95_delta > tolerances.pressure:
            within = False
        if usage_delta is not None and usage_delta > tolerances.usage_hours:
            within = False
        if leak95_delta is not None and leak95_delta > tolerances.leak:
            within = False

        if within:
            days_ok += 1

        diffs.append(
            DayDiff(
                date=date,
                parser_ahi=p.ahi,
                oscar_ahi=o.ahi,
                ahi_delta=ahi_delta,
                parser_pressure_95=p.pressure_95,
                oscar_pressure_95=o.pressure_95,
                pressure_95_delta=p95_delta,
                parser_usage_hours=p.usage_hours,
                oscar_usage_hours=o.usage_hours,
                usage_hours_delta=usage_delta,
                parser_leak_95=p.leak_95,
                oscar_leak_95=o.leak_95,
                leak_95_delta=leak95_delta,
                within_tolerance=within,
            )
        )

    return CompareResult(
        sample_name=sample_name,
        diffs=diffs,
        total_days=len(matched_dates),
        days_within_tolerance=days_ok,
        parser_only_dates=parser_only,
        oscar_only_dates=oscar_only,
        tolerances=tolerances,
    )
