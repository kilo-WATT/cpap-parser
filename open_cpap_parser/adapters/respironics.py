"""Philips Respironics CPAP data adapter.

Reads standard European Data Format (``.edf``) files produced by
Philips Respironics devices.  This adapter uses ``pyedflib`` (a
pure-Python EDF reader) to unpack the signal streams and map them
into our ``TimeSeriesData`` and ``CPAPEvent`` schemas.

The ``pyedflib`` import is deferred so that a missing dependency
never crashes the top-level package.
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
    import pyedflib  # type: ignore[import-untyped]

    HAS_PYEDFLIB = True
except ImportError:
    HAS_PYEDFLIB = False


#: Known subdirectory names inside a Respironics SD card.
RESPIRONICS_DIR_MARKERS = ("p0", "P0", "EDF", "edf")

#: Mapping from pyedflib signal labels to our internal keys.
SIGNAL_LABEL_MAP: dict[str, str] = {
    "Flow": "flow_rate",
    "Pressure": "mask_pressure",
    "Leak": "leak",
    "RR": "respiratory_rate",
    "TidalVolume": "tidal_volume",
}


class RespironicsAdapter(BaseManufacturerAdapter):
    """Adapter for Philips Respironics CPAP devices.

    Fingerprints a data directory by scanning for a known subdirectory
    (``p0/``, ``P0/``, ``EDF/``) that contains ``.edf`` files.
    Each EDF file is parsed as a ``CPAPSession`` whose signal channels
    are mapped into ``TimeSeriesData`` and whose annotations are parsed
    as ``CPAPEvent`` objects.
    """

    def can_handle(self, directory: Path) -> bool:
        """Return True if *directory* contains a Respironics EDF layout.

        Args:
            directory: Root path of the SD card or data folder.

        Returns:
            True when a known EDF subdirectory is found.
        """
        for marker in RESPIRONICS_DIR_MARKERS:
            candidate = directory / marker
            if candidate.is_dir() and any(candidate.glob("*.edf")):
                return True
        return bool(list(directory.glob("[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]/*.edf")) or
                     list(directory.glob("[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]_*/*.edf")))

    def extract_and_map(
        self,
        directory: Path,
        include_timeseries: bool = False,
    ) -> CPAPDirectory:
        """Parse Respironics EDF files and return a ``CPAPDirectory``.

        Args:
            directory: Root path containing Respironics EDF data.
            include_timeseries: If True, decode signal waveforms into
                ``TimeSeriesData``.

        Returns:
            A ``CPAPDirectory`` with per-file ``CPAPSession`` objects.

        Raises:
            ImportError: If ``pyedflib`` is not installed.
        """
        if not HAS_PYEDFLIB:
            msg = (
                "The Philips Respironics adapter requires 'pyedflib'.\n"
                "  pip install pyedflib"
            )
            raise ImportError(msg)

        edf_files = self._find_edf_files(directory)
        if not edf_files:
            raise UnsupportedDirectoryError(directory, "No Respironics EDF files found")

        machine = MachineInfo(serial_number="Unknown")
        summaries: list[CPAPSessionSummary] = []
        sessions = self._parse_edf_files(edf_files, include_timeseries)

        return CPAPDirectory(
            machine=machine,
            daily_summaries=summaries,
            sessions=sessions,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_edf_files(directory: Path) -> list[Path]:
        """Collect all ``.edf`` files in known Respironics locations."""
        found: list[Path] = []
        for marker in RESPIRONICS_DIR_MARKERS:
            candidate = directory / marker
            if candidate.is_dir():
                found.extend(sorted(candidate.glob("*.edf")))

        if not found:
            found.extend(sorted(directory.glob("*.edf")))

        return found

    @staticmethod
    def _parse_edf_files(
        edf_files: list[Path],
        include_timeseries: bool,
    ) -> list[CPAPSession]:
        """Parse every EDF file into a ``CPAPSession``."""
        sessions: list[CPAPSession] = []
        for fpath in edf_files:
            try:
                session = _parse_single_edf(fpath, include_timeseries)
                if session is not None:
                    sessions.append(session)
            except Exception as exc:
                logger.warning("Skipping unreadable EDF %s: %s", fpath.name, exc)
        return sessions


def _parse_single_edf(
    fpath: Path,
    include_timeseries: bool,
) -> Optional[CPAPSession]:
    """Parse one ``.edf`` file with ``pyedflib``.

    Returns:
        A ``CPAPSession``, or ``None`` if the file could not be parsed.
    """
    try:
        f = pyedflib.EdfReader(str(fpath))
    except Exception as exc:
        logger.warning("pyedflib rejected %s: %s", fpath.name, exc)
        return None

    try:
        n_signals = f.signals_in_file
        if n_signals == 0:
            return None

        file_duration = f.file_duration
        start_time = _edf_start_datetime(f)

        if file_duration is None or file_duration <= 0:
            return None

        end_time = start_time + timedelta(seconds=file_duration)
        duration_minutes = file_duration / 60.0

        events = _parse_edf_annotations(f)

        timeseries: Optional[TimeSeriesData] = None
        if include_timeseries:
            timeseries = _read_signal_data(f, n_signals, file_duration)

        return CPAPSession(
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_minutes,
            file_type="EDF",
            events=events,
            timeseries=timeseries,
        )
    finally:
        f.close()


def _edf_start_datetime(f: "pyedflib.EdfReader") -> datetime:
    """Build a ``datetime`` from EDF header fields."""
    try:
        return datetime(
            year=f.getStartdatetime().year,
            month=f.getStartdatetime().month,
            day=f.getStartdatetime().day,
            hour=f.getStartdatetime().hour,
            minute=f.getStartdatetime().minute,
            second=f.getStartdatetime().second,
        )
    except Exception:
        return datetime.min


def _parse_edf_annotations(f: "pyedflib.EdfReader") -> list[CPAPEvent]:
    """Extract EDF annotations (TALs) as ``CPAPEvent`` objects."""
    events: list[CPAPEvent] = []
    try:
        annotations = f.readAnnotations()
    except Exception:
        return events

    if not annotations:
        return events

    for onset, duration, text in annotations:
        if not text or not text.strip():
            continue
        events.append(
            CPAPEvent(
                timestamp_sec=float(onset),
                event_type=str(text).strip(),
                duration_sec=float(duration) if duration else None,
            )
        )
    return events


def _read_signal_data(
    f: "pyedflib.EdfReader",
    n_signals: int,
    file_duration: float,
) -> TimeSeriesData:
    """Read all signal channels and fill a ``TimeSeriesData`` struct."""
    mapped: dict[str, list[float]] = {
        "flow_rate": [],
        "mask_pressure": [],
        "leak": [],
        "respiratory_rate": [],
        "tidal_volume": [],
    }

    sample_count = 0
    sample_rate = 0.0

    for idx in range(n_signals):
        label = f.getLabel(idx).strip()
        try:
            samples = f.readSignal(idx)
        except Exception:
            continue

        if len(samples) == 0:
            continue

        sample_count = max(sample_count, len(samples))

        if sample_rate == 0.0 and file_duration > 0:
            sample_rate = len(samples) / file_duration

        target_key = SIGNAL_LABEL_MAP.get(label)
        if target_key is not None and target_key in mapped:
            mapped[target_key] = [float(v) for v in samples]

    timestamps = [i / sample_rate for i in range(sample_count)] if sample_rate > 0 else []

    return TimeSeriesData(
        timestamps=timestamps,
        flow_rate=mapped["flow_rate"],
        mask_pressure=mapped["mask_pressure"],
        leak=mapped["leak"],
        respiratory_rate=mapped["respiratory_rate"],
        tidal_volume=mapped["tidal_volume"],
    )
