"""Lowenstein / Weinmann CPAP data adapter.

Parses the proprietary ``WM_DATA.TDF`` file format produced by
Lowenstein Medical (formerly Weinmann) devices.  Delegates the
heavy lifting to the third-party ``cpap-analyst-mcp`` library.

The adapter performs a lazy import so that a missing dependency
produces a clean error at parse time rather than a crash at import
time.
"""

import logging
from datetime import datetime, timedelta
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
    from cpap_analyst_mcp.parsers.weinmann import WeinmannParser  # type: ignore[import-untyped]

    HAS_LOWENSTEIN = True
except ImportError:
    HAS_LOWENSTEIN = False


class LowensteinAdapter(BaseManufacturerAdapter):
    """Adapter for Lowenstein Medical (Weinmann) CPAP devices.

    Detects a Lowenstein data directory by the presence of a root-level
    ``WM_DATA.TDF`` file.  Extracts per-day usage duration, AHI, and
    95th-percentile leak rate from the TDF records.  High-resolution
    events and waveform data are not available in this format.
    """

    TDF_FILENAME = "WM_DATA.TDF"

    def can_handle(self, directory: Path) -> bool:
        """Return True if *directory* contains a ``WM_DATA.TDF`` file.

        Args:
            directory: Root path of the SD card or data folder.

        Returns:
            True when the Lowenstein TDF fingerprint is found.
        """
        return (directory / self.TDF_FILENAME).is_file()

    def extract_and_map(
        self,
        directory: Path,
        include_timeseries: bool = False,
    ) -> CPAPDirectory:
        """Parse a Lowenstein data directory and return a ``CPAPDirectory``.

        Args:
            directory: Root path containing ``WM_DATA.TDF``.
            include_timeseries: Ignored — timeseries are not available
                from the TDF format.

        Returns:
            A fully populated ``CPAPDirectory``.

        Raises:
            ImportError: If ``cpap-analyst-mcp`` is not installed.
        """
        if not HAS_LOWENSTEIN:
            msg = (
                "The Lowenstein / Weinmann adapter requires 'cpap-analyst-mcp'.\n"
                "  pip install cpap-analyst-mcp"
            )
            raise ImportError(msg)

        tdf_path = directory / self.TDF_FILENAME
        if not tdf_path.is_file():
            raise UnsupportedDirectoryError(
                directory, f"{self.TDF_FILENAME} not found"
            )

        try:
            parser = WeinmannParser(str(tdf_path))
            records = parser.parse()
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", tdf_path.name, exc)
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
        """Try to read a device serial from the parsed TDF records."""
        if not records:
            return "Unknown"
        first = records[0]
        return str(getattr(first, "serial", getattr(first, "device_serial", "Unknown")))

    @staticmethod
    def _map_summaries(records: list) -> list[CPAPSessionSummary]:
        """Convert TDF records into ``CPAPSessionSummary`` objects."""
        summaries: list[CPAPSessionSummary] = []

        for rec in records or []:
            rec_date = getattr(rec, "date", None)
            if rec_date is None:
                continue

            usage_sec = float(getattr(rec, "usage_duration", getattr(rec, "duration", 0.0)))
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
                    pressure_mode=getattr(rec, "mode", ""),
                )
            )

        return summaries
