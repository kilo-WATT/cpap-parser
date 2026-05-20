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
