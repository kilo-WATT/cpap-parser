"""Yuwell / DJMed CPAP data adapter.

Reads compliance and summary data from devices that use the DJMed
binary format (commonly found on Yuwell and other Chinese-market
CPAP machines).  The third-party ``djmed`` library (by Centurix)
handles the raw decoding; this adapter maps the results into
``CPAPSessionSummary`` records.

The ``djmed`` import is deferred so that a missing dependency
never crashes the top-level package.
"""

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from open_cpap_parser.adapters.base import BaseManufacturerAdapter, UnsupportedDirectoryError
from open_cpap_parser.schema import (
    CPAPDirectory,
    CPAPEvent,
    CPAPSession,
    CPAPSessionSummary,
    MachineInfo,
    TimeSeriesData,
)

logger = logging.getLogger(__name__)

try:
    import djmed  # type: ignore[import-untyped]

    HAS_YUWELL = True
except ImportError:
    HAS_YUWELL = False


class YuwellAdapter(BaseManufacturerAdapter):
    """Adapter for Yuwell / DJMed CPAP devices.

    Identifies a Yuwell data directory by the presence of file
    structures recognised by the ``djmed`` library.  Extracts
    daily compliance hours, basic event flags, and aggregate AHI
    from the decoded records.  High-resolution waveforms are not
    exposed by the DJMed format.
    """

    # Known DJMed file markers — at least one must be present.
    DJMED_FILE_MARKERS = ("DJMED.BIN", "YUWELL.BIN", "DATA")

    def can_handle(self, directory: Path) -> bool:
        """Return True if *directory* matches a Yuwell / DJMed layout.

        Args:
            directory: Root path of the SD card or data folder.

        Returns:
            True when a known DJMed file or directory is found.
        """
        for marker in self.DJMED_FILE_MARKERS:
            if (directory / marker).exists():
                return True
        return False

    def extract_and_map(
        self,
        directory: Path,
        include_timeseries: bool = False,
    ) -> CPAPDirectory:
        """Parse a Yuwell data directory and return a ``CPAPDirectory``.

        Args:
            directory: Root path containing DJMed data files.
            include_timeseries: Ignored — waveform data is not available
                from the DJMed summary format.

        Returns:
            A ``CPAPDirectory`` with daily summaries populated.

        Raises:
            ImportError: If ``djmed`` is not installed.
        """
        if not HAS_YUWELL:
            msg = (
                "The Yuwell / DJMed adapter requires 'djmed'.\n"
                "  pip install djmed"
            )
            raise ImportError(msg)

        if not self.can_handle(directory):
            raise UnsupportedDirectoryError(directory, "No DJMed data found")

        try:
            parser = djmed.DJMedParser(str(directory))
            records = parser.parse()
        except Exception as exc:
            logger.warning("Failed to parse DJMed data in %s: %s", directory, exc)
            records = []

        machine = MachineInfo(serial_number=self._extract_serial(records))
        summaries = self._map_summaries(records)
        sessions: list[CPAPSession] = []

        return CPAPDirectory(
            machine=machine,
            daily_summaries=summaries,
            sessions=sessions,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_serial(records: list) -> str:
        """Attempt to read a device serial from the parsed records."""
        if not records:
            return "Unknown"
        first = records[0]
        return str(getattr(first, "serial", getattr(first, "device_id", "Unknown")))

    @staticmethod
    def _map_summaries(records: list) -> list[CPAPSessionSummary]:
        """Convert DJMed records into ``CPAPSessionSummary`` objects."""
        summaries: list[CPAPSessionSummary] = []

        for rec in records or []:
            rec_date = _get_date(rec)
            if rec_date is None:
                continue

            usage_sec = float(getattr(rec, "compliance_seconds", getattr(rec, "runtime", 0)) or 0)
            usage_hours = usage_sec / 3600.0

            summaries.append(
                CPAPSessionSummary(
                    date=rec_date,
                    ahi=float(getattr(rec, "ahi", 0.0) or 0.0),
                    ai=float(getattr(rec, "ai", 0.0) or 0.0),
                    hi=float(getattr(rec, "hi", 0.0) or 0.0),
                    cai=float(getattr(rec, "cai", 0.0) or 0.0),
                    oai=float(getattr(rec, "oai", 0.0) or 0.0),
                    leak_95=float(getattr(rec, "leak_95", 0.0) or 0.0),
                    usage_hours=usage_hours,
                )
            )

        return summaries


def _get_date(record) -> Optional[date]:
    """Extract a ``date`` from *record* regardless of attribute name."""
    for attr in ("date", "session_date", "day"):
        val = getattr(record, attr, None)
        if isinstance(val, date):
            return val
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, str):
            try:
                return date.fromisoformat(val)
            except (ValueError, TypeError):
                pass
    return None
