"""Fisher & Paykel CPAP data adapter.

Ingests summary data from ``.FPH`` files produced by Fisher & Paykel
Healthcare devices.  The third-party ``fph-parser`` library (by jieter)
handles the low-level binary format; this adapter lifts its output into
our unified schema.

The import of ``fph-parser`` is deferred so that a missing dependency
never crashes the top-level package.
"""

import logging
from datetime import datetime
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
    import fph_parser  # type: ignore[import-untyped]

    HAS_FPH = True
except ImportError:
    HAS_FPH = False


class FisherPaykelAdapter(BaseManufacturerAdapter):
    """Adapter for Fisher & Paykel CPAP devices.

    Fingerprints a data directory by scanning recursively for ``.FPH``
    files (e.g. ``SUM0001.FPH``).  Each FPH record is mapped to one
    or more ``CPAPEvent`` objects carrying the event counts reported
    by the device (apneas, hypopneas, flow limitations).
    """

    FPH_GLOB = "**/*.FPH"

    def can_handle(self, directory: Path) -> bool:
        """Return True if *directory* contains at least one ``.FPH`` file.

        Args:
            directory: Root path of the SD card or data folder.

        Returns:
            True when an FPH file is found via recursive glob.
        """
        return any(directory.glob(self.FPH_GLOB))

    def extract_and_map(
        self,
        directory: Path,
        include_timeseries: bool = False,
    ) -> CPAPDirectory:
        """Parse Fisher & Paykel FPH files and return a ``CPAPDirectory``.

        Args:
            directory: Root path containing ``.FPH`` files.
            include_timeseries: Ignored — waveform data is not available
                from FPH summary files.

        Returns:
            A ``CPAPDirectory`` with per-file ``CPAPSession`` objects.

        Raises:
            ImportError: If ``fph-parser`` is not installed.
        """
        if not HAS_FPH:
            msg = (
                "The Fisher & Paykel adapter requires 'fph-parser'.\n"
                "  pip install fph-parser"
            )
            raise ImportError(msg)

        fph_files = sorted(directory.glob(self.FPH_GLOB))
        if not fph_files:
            raise UnsupportedDirectoryError(directory, "No .FPH files found")

        machine = MachineInfo(serial_number="Unknown")
        summaries: list[CPAPSessionSummary] = []
        sessions: list[CPAPSession] = self._parse_fph_files(fph_files)

        return CPAPDirectory(
            machine=machine,
            daily_summaries=summaries,
            sessions=sessions,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_fph_files(self, fph_files: list[Path]) -> list[CPAPSession]:
        """Iterate over FPH files and build ``CPAPSession`` objects."""
        sessions: list[CPAPSession] = []

        for fpath in fph_files:
            try:
                session = self._parse_single_fph(fpath)
                if session is not None:
                    sessions.append(session)
            except Exception as exc:
                logger.warning("Skipping corrupt FPH file %s: %s", fpath.name, exc)

        return sessions

    @staticmethod
    def _parse_single_fph(fpath: Path) -> Optional[CPAPSession]:
        """Parse one FPH file into a ``CPAPSession``."""
        records = fph_parser.parse(str(fpath))

        if not records:
            return None

        events: list[CPAPEvent] = []
        start_time = datetime.min
        end_time = datetime.min
        duration_minutes = 0.0

        for rec in records:
            rec_start = getattr(rec, "start_time", None)
            if isinstance(rec_start, datetime) and start_time == datetime.min:
                start_time = rec_start

            runtime = getattr(rec, "duration_minutes", 0.0) or 0.0
            if runtime > duration_minutes:
                duration_minutes = runtime

            _append_counts(events, rec)

        if start_time != datetime.min:
            end_time = start_time.replace(
                minute=int(start_time.minute + duration_minutes)
            )

        return CPAPSession(
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes,
            file_type="FPH",
            events=events,
            timeseries=None,
        )


def _append_counts(events: list[CPAPEvent], record) -> None:
    """Append ``CPAPEvent`` objects for each event count type in *record*."""
    event_fields: dict[str, str] = {
        "apnea_count": "Apnea",
        "hypopnea_count": "Hypopnea",
        "flow_limitation_count": "Flow Limitation",
    }

    for attr_name, event_type in event_fields.items():
        count = int(getattr(record, attr_name, 0) or 0)
        if count <= 0:
            continue
        events.append(
            CPAPEvent(
                timestamp_sec=0.0,
                event_type=event_type,
                duration_sec=None,
                data={event_type: float(count)},
            )
        )
