"""OSCAR CSV export reader for open-cpap-parser validation.

OSCAR (Open Source CPAP Analysis Reporter) stores imported device data in a
proprietary QDataStream binary format.  The simplest machine-readable interface
is its **CSV Export** (File → Export → CSV Export Wizard).

Each exported CSV contains one row per therapy night with columns that vary
by OSCAR version but always include at minimum:

    Date, AHI, Pressure (cmH₂O percentiles), Leak (L/min), Usage (hours)

The canonical workflow:

1. Open OSCAR and import an SD card dump.
2. Export a CSV summary for the imported device.
3. Save the CSV to ``validation/oscar_exports/<sample_name>.csv``.
4. Run ``pytest validation/ -m validation --run-validation``.

OSCAR data directory (default): ``~/Documents/OSCAR_Data/``
"""

from __future__ import annotations

import csv
from datetime import datetime, time, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class OscarDaySummary:
    """Aggregated therapy metrics for one calendar day from an OSCAR CSV export.

    Attributes:
        date: ISO date string (``YYYY-MM-DD``).
        ahi: Apnea-Hypopnea Index (events / hour).
        ai: Apnea Index (events / hour).  ``None`` if not exported.
        hi: Hypopnea Index (events / hour).  ``None`` if not exported.
        usage_hours: Total therapy time (hours).
        pressure_50: 50th-percentile pressure (cmH₂O).  ``None`` if not exported.
        pressure_95: 95th-percentile pressure (cmH₂O).  ``None`` if not exported.
        leak_50: 50th-percentile unintentional leak (L/min).  ``None`` if absent.
        leak_95: 95th-percentile unintentional leak (L/min).  ``None`` if absent.
    """

    date: str
    ahi: float
    ai: Optional[float]
    hi: Optional[float]
    usage_hours: float
    pressure_50: Optional[float]
    pressure_95: Optional[float]
    leak_50: Optional[float]
    leak_95: Optional[float]


# ── Session CSV Column name aliases ────────────────────────────────────────────
# OSCAR Sessions CSV export columns
_START_END_COLS = {"Start", "start", "End", "end"}
_SESSION_USAGE_COLS = {"Total Time", "Hours", "Usage", "usage", "hours", "Duration"}
_SESSION_AHI_COLS = {"AHI", "ahi"}
_SESSION_EVENT_COUNT_COLS = {
    "CA Count", "ca count", "Clear Airway",
    "OA Count", "oa count", "Obstructive Apnea",
    "A Count", "a count", "Apnea",
    "H Count", "h count", "Hypopnea",
    "UA Count", "ua count", "Unclassified Apnea",
}
_SESSION_P95_COLS = {
    "95% Pressure", "95% IPAP", "95% EPAP",
    "P95", "pressure_95", "Pressure 95%",
}
_SESSION_LEAK_95_COLS = {
    "95% Flow Limit.", "95% Leak", "L95", "leak_95", "Leak (L/min)"
}

# ── Column name aliases ────────────────────────────────────────────────────────
# OSCAR's CSV header changes between versions and export configurations.  We
# accept a generous set of aliases so the reader works regardless of OSCAR
# version or the exact fields the user chose to export.

_DATE_COLS  = {"Date", "date"}
_AHI_COLS   = {"AHI", "ahi"}
_AI_COLS    = {"AI", "ai", "Apnea Index", "ApneaIndex"}
_HI_COLS    = {"HI", "hi", "Hypopnea Index", "HypopneaIndex"}
# "Total Time" is the OSCAR 1.7.x export header; keep legacy aliases for older versions.
_USAGE_COLS = {"Total Time", "Hours", "Usage", "usage", "hours", "Usage (hrs)", "SleepTime"}
# "Median Pressure" = 50th pct in OSCAR export; "95% Pressure" = 95th pct.
# For BiPAP/Prisma Line devices OSCAR exports pressure via IPAP/EPAP columns
# rather than the generic "Pressure" columns (which are 0).  _pick_nonzero()
# selects the first non-zero match so CPAP devices (where "Pressure" is
# non-zero) take precedence while BiPAP devices fall back to IPAP.
# These must be ordered tuples — Python set iteration order is non-deterministic
# so both "95% Pressure" and "95% EPAP" could be selected for CPAP devices.
_P50_COLS: tuple[str, ...] = (
    "Median Pressure", "Median IPAP", "Median EPAP",
    "Pressure", "P50", "pressure_50", "Pressure 50%",
)
_P95_COLS: tuple[str, ...] = (
    "95% Pressure", "95% IPAP", "95% EPAP",
    "P95", "pressure_95", "Pressure 95%", "Press 95%",
)
_L50_COLS   = {"Leak50", "Leak 50%", "L50", "leak_50"}
_L95_COLS   = {"Leak", "Leak95", "Leak 95%", "L95", "leak_95", "Leak (L/min)"}


def _pick(row: dict[str, str], candidates) -> Optional[float]:
    """Return the first matching column value as a float, or ``None``."""
    for key in candidates:
        val = row.get(key, "").strip()
        if val:
            try:
                return float(val)
            except ValueError:
                pass
    return None


def _pick_nonzero(row: dict[str, str], candidates) -> Optional[float]:
    """Return the first non-zero matching column value as a float, or ``None``.

    Used for pressure columns where OSCAR may export 0.0 for one pressure type
    (e.g. ``"95% Pressure"`` for BiPAP devices) while the actual value appears
    in a sibling column (``"95% IPAP"``).
    """
    for key in candidates:
        val = row.get(key, "").strip()
        if val:
            try:
                f = float(val)
                if f != 0.0:
                    return f
            except ValueError:
                pass
    return None


def _parse_usage(row: dict[str, str], candidates: set[str]) -> Optional[float]:
    """Return usage hours from *row*, handling both float and ``HH:MM:SS`` strings.

    OSCAR 1.7.x exports ``Total Time`` as ``"HH:MM:SS"``.  Older versions and
    alternative export tools may use a plain decimal hours float.  This helper
    accepts both formats.

    Args:
        row: A single CSV row as a dict.
        candidates: Column name aliases to check.

    Returns:
        Usage in decimal hours, or ``None`` if no matching column is found.
    """
    for key in candidates:
        val = row.get(key, "").strip()
        if not val:
            continue
        if ":" in val:
            parts = val.split(":")
            try:
                h = int(parts[0])
                m = int(parts[1])
                s = float(parts[2]) if len(parts) > 2 else 0.0
                return h + m / 60.0 + s / 3600.0
            except (ValueError, IndexError):
                continue
        try:
            return float(val)
        except ValueError:
            continue
    return None


def _parse_date(raw: str) -> str:
    """Normalise an OSCAR date string to ``YYYY-MM-DD``."""
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            from datetime import datetime
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw


def read_oscar_csv(csv_path: Path) -> list[OscarDaySummary]:
    """Parse an OSCAR CSV export file into a list of daily summaries.

    Args:
        csv_path: Path to the ``.csv`` file exported from OSCAR.

    Returns:
        List of :class:`OscarDaySummary`, one per therapy night, sorted
        by date ascending.  Rows with zero or missing usage are skipped.

    Raises:
        FileNotFoundError: If *csv_path* does not exist.
        ValueError: If the file cannot be parsed as an OSCAR CSV export
            (missing Date or AHI column).
    """
    if not csv_path.is_file():
        raise FileNotFoundError(f"OSCAR CSV export not found: {csv_path}")

    summaries: list[OscarDaySummary] = []
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"Empty or non-CSV file: {csv_path}")

        has_date = any(c in _DATE_COLS for c in reader.fieldnames)
        has_ahi = any(c in _AHI_COLS for c in reader.fieldnames)
        if not has_date or not has_ahi:
            raise ValueError(
                f"Cannot find Date/AHI columns in {csv_path}. "
                f"Headers found: {list(reader.fieldnames)}"
            )

        for row in reader:
            date_raw = next((row.get(c, "") for c in _DATE_COLS if c in row), "")
            if not date_raw.strip():
                continue

            ahi_raw = next((row.get(c, "") for c in _AHI_COLS if c in row), "")
            try:
                ahi = float(ahi_raw)
            except (ValueError, TypeError):
                continue

            usage = _parse_usage(row, _USAGE_COLS)
            if not usage or usage <= 0:
                continue

            summaries.append(
                OscarDaySummary(
                    date=_parse_date(date_raw),
                    ahi=ahi,
                    ai=_pick(row, _AI_COLS),
                    hi=_pick(row, _HI_COLS),
                    usage_hours=usage,
                    pressure_50=_pick_nonzero(row, _P50_COLS),
                    pressure_95=_pick_nonzero(row, _P95_COLS),
                    leak_50=_pick(row, _L50_COLS),
                    leak_95=_pick(row, _L95_COLS),
                )
            )

    return sorted(summaries, key=lambda s: s.date)


def _safe_float(value: Optional[str]) -> float:
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0


# ── Non-event types in Details CSV ────────────────────────────────────────────
_DETAILS_NON_EVENTS = {"Pressure", "CPAP", "Flow Limit", "Leak"}


@dataclass(frozen=True)
class OscarSession:
    """Per-session stats from an OSCAR Sessions CSV export."""

    session_id: str
    start: str
    end: str
    ahi: float
    pressure_50: Optional[float]
    pressure_95: Optional[float]
    epap_50: Optional[float]
    epap_95: Optional[float]
    leak_50: Optional[float]
    leak_95: Optional[float]


@dataclass(frozen=True)
class OscarEvent:
    """A single therapy event from an OSCAR Details CSV export."""

    datetime_str: str
    session_id: str
    event_type: str
    duration_sec: float


def read_sessions_csv(path: Path) -> list[OscarSession]:
    """Read an OSCAR Sessions CSV and return one OscarSession per row."""
    sessions: list[OscarSession] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            sessions.append(OscarSession(
                session_id=row.get("Session", "").strip(),
                start=row.get("Start", "").strip(),
                end=row.get("End", "").strip(),
                ahi=_safe_float(row.get("AHI")),
                pressure_50=_pick_nonzero(row, _P50_COLS),
                pressure_95=_pick_nonzero(row, _P95_COLS),
                epap_50=_pick_nonzero(row, ("Median EPAP",)),
                epap_95=_pick_nonzero(row, ("95% EPAP",)),
                leak_50=_pick_nonzero(row, ("Median Flow Limit.", "Median Leak", "Leak 50%")),
                leak_95=_pick_nonzero(row, ("95% Flow Limit.", "95% Leak", "Leak 95%")),
            ))
    return sessions


def read_details_csv(path: Path) -> list[OscarEvent]:
    """Read an OSCAR Details CSV and return therapy events only (skip device-state rows)."""
    events: list[OscarEvent] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            event_type = row.get("Event", "").strip()
            if event_type in _DETAILS_NON_EVENTS:
                continue
            events.append(OscarEvent(
                datetime_str=row.get("DateTime", "").strip(),
                session_id=row.get("Session", "").strip(),
                event_type=event_type,
                duration_sec=_safe_float(row.get("Data/Duration")),
            ))
    return events


def aggregate_sessions_to_noon_periods(
    sessions_csv_path: Path,
    timezone_offset_hours: int,
) -> list[OscarDaySummary]:
    """Read an OSCAR Sessions CSV and aggregate sessions into noon-to-noon periods
    based on device local time.

    Args:
        sessions_csv_path: Path to the ``.csv`` file exported from OSCAR
            (File → Export → CSV Export Wizard → Sessions).
        timezone_offset_hours: Offset from UTC to device local time in hours.
            Positive if device is ahead of UTC (e.g., +7 for UTC+7).

    Returns:
        List of :class:`OscarDaySummary`, one per noon-to-noon period, sorted
        by date ascending. Periods with zero or missing usage are skipped.

    Raises:
        FileNotFoundError: If *sessions_csv_path* does not exist.
        ValueError: If the file cannot be parsed as an OSCAR Sessions CSV.
    """
    if not sessions_csv_path.is_file():
        raise FileNotFoundError(f"OSCAR Sessions CSV not found: {sessions_csv_path}")

    # Map from record date (noon-to-noon period start) to accumulated data
    period_data: dict[str, dict] = {}

    with sessions_csv_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"Empty or non-CSV file: {sessions_csv_path}")

        has_start = any(c in _START_END_COLS for c in reader.fieldnames)
        has_total_time = any(c in _SESSION_USAGE_COLS for c in reader.fieldnames)
        has_ahi = any(c in _SESSION_AHI_COLS for c in reader.fieldnames)
        has_event_counts = any(
            c in _SESSION_EVENT_COUNT_COLS for c in reader.fieldnames
        )
        if not (has_start and has_total_time and has_ahi):
            raise ValueError(
                f"Missing required columns in {sessions_csv_path}. "
                f"Need Start/End, Total Time, and AHI columns. "
                f"Headers found: {list(reader.fieldnames)}"
            )

        for row in reader:
            # Parse start time (assumed UTC in Sessions CSV)
            start_raw = next(
                (row.get(c, "") for c in _START_END_COLS if c in row), ""
            )
            if not start_raw.strip():
                continue

            try:
                # Parse as UTC: 2025-02-20T11:33:00
                utc_dt = datetime.strptime(start_raw.strip(), "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                continue

            # Convert to device local time
            local_dt = utc_dt + timedelta(hours=timezone_offset_hours)

            # Determine which noon-to-noon period this session belongs to
            # If session starts at/after noon local time → belongs to that day's period
            # If session starts before noon local time → belongs to previous day's period
            if local_dt.time() >= time(12, 0, 0):
                record_date = local_dt.date()
            else:
                record_date = local_dt.date() - timedelta(days=1)

            date_str = record_date.isoformat()

            # Parse session duration (Total Time column)
            usage_hours = _parse_session_usage(
                row, _SESSION_USAGE_COLS
            )
            if usage_hours is None or usage_hours <= 0:
                continue  # Skip sessions with no usage

            # Parse AHI and event counts
            ahi = _pick(row, _SESSION_AHI_COLS)
            if ahi is None:
                # Fallback: compute from event counts if available
                total_events = _sum_event_counts(row)
                if total_events is not None and usage_hours > 0:
                    ahi = total_events / usage_hours
                else:
                    continue  # Skip if no AHI or event data

            # Parse optional metrics
            pressure_95 = _pick_nonzero(row, _SESSION_P95_COLS)
            leak_95 = _pick_nonzero(row, _SESSION_LEAK_95_COLS)

            # Initialize period data if needed
            if date_str not in period_data:
                period_data[date_str] = {
                    "total_usage_seconds": 0.0,
                    "total_events": 0.0,
                    "pressure_weighted_sum": 0.0,
                    "leak_weighted_sum": 0.0,
                    "usage_weight_sum": 0.0,
                }

            # Accumulate data for this period
            session_seconds = usage_hours * 3600.0
            period_data[date_str]["total_usage_seconds"] += session_seconds
            period_data[date_str]["total_events"] += ahi * usage_hours

            if pressure_95 is not None:
                period_data[date_str]["pressure_weighted_sum"] += (
                    pressure_95 * session_seconds
                )
                period_data[date_str]["usage_weight_sum"] += session_seconds

            if leak_95 is not None:
                period_data[date_str]["leak_weighted_sum"] += (
                    leak_95 * session_seconds
                )
                period_data[date_str]["usage_weight_sum"] += session_seconds

    # Convert accumulated data to OscarDaySummary objects
    summaries: list[OscarDaySummary] = []
    for date_str in sorted(period_data.keys()):
        data = period_data[date_str]
        total_seconds = data["total_usage_seconds"]
        if total_seconds <= 0:
            continue

        usage_hours = total_seconds / 3600.0
        ahi = data["total_events"] / usage_hours if usage_hours > 0 else 0.0

        pressure_95 = None
        if data["usage_weight_sum"] > 0:
            pressure_95 = (
                data["pressure_weighted_sum"] / data["usage_weight_sum"]
            )

        leak_95 = None
        if data["usage_weight_sum"] > 0:
            leak_95 = (
                data["leak_weighted_sum"] / data["usage_weight_sum"]
            )

        summaries.append(
            OscarDaySummary(
                date=date_str,
                ahi=ahi,
                ai=None,  # Not available from session aggregation
                hi=None,  # Not available from session aggregation
                usage_hours=usage_hours,
                pressure_50=None,  # Not available from session aggregation
                pressure_95=pressure_95,
                leak_50=None,  # Not available from session aggregation
                leak_95=leak_95,
            )
        )

    return summaries


def _parse_session_usage(row: dict[str, str], candidates) -> Optional[float]:
    """Parse session duration from Sessions CSV row, handling HH:MM:SS format."""
    for key in candidates:
        val = row.get(key, "").strip()
        if not val:
            continue
        if ":" in val:
            parts = val.split(":")
            try:
                h = int(parts[0])
                m = int(parts[1])
                s = float(parts[2]) if len(parts) > 2 else 0.0
                return h + m / 60.0 + s / 3600.0
            except (ValueError, IndexError):
                continue
        try:
            return float(val)
        except ValueError:
            continue
    return None


def _sum_event_counts(row: dict[str, str]) -> Optional[float]:
    """Sum event counts from AllAhiChannels in Sessions CSV row."""
    total = 0.0
    for key in _SESSION_EVENT_COUNT_COLS:
        val = row.get(key, "").strip()
        if val:
            try:
                total += float(val)
            except ValueError:
                pass
    return total if total > 0 else None


def _pick(row: dict[str, str], candidates) -> Optional[float]:
    """Return the first matching column value as a float, or ``None``."""
    for key in candidates:
        val = row.get(key, "").strip()
        if val:
            try:
                return float(val)
            except ValueError:
                pass
    return None


def oscar_data_dir() -> Path:
    """Return the OSCAR data directory (``~/Documents/OSCAR_Data/`` by default).

    Returns:
        Path to the OSCAR data directory.
    """
    return Path.home() / "Documents" / "OSCAR_Data"


def has_oscar_profiles() -> bool:
    """Return ``True`` if OSCAR has at least one imported profile with machine data.

    Returns:
        ``True`` when at least one machine directory exists under the OSCAR
        data folder, indicating that data has been imported into OSCAR.
    """
    data_dir = oscar_data_dir()
    if not data_dir.is_dir():
        return False
    for entry in data_dir.iterdir():
        if entry.is_dir() and entry.name not in ("logs",):
            machines = entry / "Machines"
            if machines.is_dir() and any(machines.iterdir()):
                return True
    return False
