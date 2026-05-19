"""ResMed CPAP data adapter.

Parses ResMed SD card directories using the ``cpap-py`` library.
Handles AirSense 10/11 data including identification, STR.edf daily
summaries, and DATALOG session files with high-resolution signals.
"""

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from cpap_py import IdentificationParser, STRParser, DatalogParser, EDFParser

from open_cpap_parser.adapters.base import BaseManufacturerAdapter
from open_cpap_parser.schema import (
    CPAPDirectory,
    CPAPEvent,
    CPAPSession,
    CPAPSessionSummary,
    MachineInfo,
    TimeSeriesData,
)

logger = logging.getLogger(__name__)


PRESSURE_MODE_NAMES: dict[int, str] = {
    0: "Unknown",
    1: "CPAP",
    2: "APAP",
    3: "BiLevel Fixed",
    4: "BiLevel Auto",
    5: "BiLevel S",
    6: "BiLevel ST",
    7: "BiLevel T",
    8: "BiLevel PAC",
    9: "ASV",
}

SIGNAL_MAP: dict[str, list[str]] = {
    "Flow": ["Flow"],
    "MaskPressure": ["MaskPress", "Mask Pressure", "MaskPressure"],
    "Pressure": ["Press", "Pressure"],
    "Leak": ["Leak"],
    "TidalVolume": ["TidVol", "Tidal Volume", "TidalVolume", "TV"],
    "MinuteVent": ["MinVent", "Minute Vent", "MinuteVent", "MV"],
    "RespRate": ["RespRate", "Resp. Rate", "Respiratory Rate", "RR"],
    "SpO2": ["SpO2", "SpO₂"],
    "Pulse": ["Pulse"],
}

FILE_TYPE_NAMES = {
    "BRP": "Breathing",
    "PLD": "PressureLeak",
    "EVE": "Events",
    "CSL": "SettingsLog",
    "SA2": "Oximetry",
    "SAD": "SummaryAdvanced",
    "AEV": "AdvancedEvents",
}


class ResMedAdapter(BaseManufacturerAdapter):
    """Adapter for ResMed AirSense and AirCurve devices.

    Fingerprints the directory by the presence of a ``DATALOG/``
    subdirectory.  Uses ``cpap-py`` components (``IdentificationParser``,
    ``STRParser``, ``DatalogParser``, ``EDFParser``) for low-level
    file parsing.
    """

    def can_handle(self, directory: Path) -> bool:
        """Return True if *directory* contains a ``DATALOG/`` folder.

        Args:
            directory: Root path of the ResMed SD card.

        Returns:
            True when the ResMed DATALOG fingerprint is found.
        """
        datalog = directory / "DATALOG"
        return datalog.is_dir()

    def extract_and_map(
        self, directory: Path, include_timeseries: bool = False
    ) -> CPAPDirectory:
        """Parse a ResMed directory and return a normalised ``CPAPDirectory``.

        Args:
            directory: Root path of the ResMed SD card.
            include_timeseries: If True, decode high-resolution signal
                channels from DATALOG EDF files.

        Returns:
            A ``CPAPDirectory`` with machine info, daily summaries,
            and per-file sessions.
        """
        machine = self._load_machine_info(directory)
        summaries = self._load_summaries(directory)
        sessions = self._load_sessions(directory, include_timeseries)

        return CPAPDirectory(
            machine=machine,
            daily_summaries=summaries,
            sessions=sessions,
        )

    def _load_machine_info(self, directory: Path) -> MachineInfo:
        """Read machine identity from ``Identification.json``."""
        try:
            parser = IdentificationParser(str(directory))
            info = parser.parse()
            if info is None:
                return MachineInfo(serial_number="Unknown")
            return MachineInfo(
                serial_number=getattr(info, "serial", "Unknown"),
                product_code=getattr(info, "model_number", ""),
                model=getattr(info, "model", ""),
                series=getattr(info, "series", ""),
                properties=dict(getattr(info, "properties", {})),
            )
        except Exception as exc:
            logger.warning("Failed to load machine info: %s", exc)
            return MachineInfo(serial_number="Unknown")

    def _load_summaries(self, directory: Path) -> list[CPAPSessionSummary]:
        """Read daily summary records from ``STR.edf``."""
        str_path = directory / "STR.edf"
        if not str_path.is_file():
            logger.warning("STR.edf not found at %s", str_path)
            return []

        try:
            parser = STRParser(str(str_path))
            if not parser.parse():
                logger.warning("STRParser.parse() returned False")
                return []
            return self._map_summaries(parser.records)
        except Exception as exc:
            logger.warning("Failed to load STR.edf: %s", exc)
            return []

    def _load_sessions(
        self, directory: Path, include_timeseries: bool
    ) -> list[CPAPSession]:
        """Iterate over all DATALOG EDF files and parse each as a session."""
        datalog = directory / "DATALOG"
        if not datalog.is_dir():
            return []

        try:
            dlp = DatalogParser(str(datalog))
        except Exception as exc:
            logger.warning("Failed to initialize DatalogParser: %s", exc)
            return []

        file_map = dlp.scan_files()
        sessions: list[CPAPSession] = []
        seen: set[str] = set()

        for date_key in sorted(file_map):
            for fpath in file_map[date_key]:
                fpath_str = str(fpath)
                if fpath_str in seen:
                    continue
                seen.add(fpath_str)
                try:
                    session = self._parse_edf_file(fpath, include_timeseries)
                    if session is not None:
                        sessions.append(session)
                except Exception as exc:
                    logger.warning(
                        "Skipping corrupt session file %s: %s", fpath.name, exc
                    )

        return sessions

    def _parse_edf_file(
        self, fpath: Path, include_timeseries: bool
    ) -> CPAPSession | None:
        """Parse a single ResMed EDF file into a ``CPAPSession``.

        Returns:
            A ``CPAPSession``, or ``None`` if the file has zero records
            or cannot be parsed.
        """
        edf = EDFParser(str(fpath))
        if not edf.parse():
            return None

        if edf.header.num_data_records == 0:
            return None

        start = edf.header.start_date
        duration = edf.header.num_data_records * edf.header.duration_seconds
        end = start + timedelta(seconds=duration) if start else datetime.min

        file_type = ""
        for code in FILE_TYPE_NAMES:
            if code in fpath.stem.upper():
                file_type = code
                break

        events: list[CPAPEvent] = []
        if file_type == "EVE":
            events = self._parse_edf_events(edf)

        sample_rate = 0.0
        if edf.signals:
            first_sig = edf.signals[0]
            if edf.header.duration_seconds > 0:
                sample_rate = first_sig.sample_count / edf.header.duration_seconds

        timeseries: TimeSeriesData | None = None
        if include_timeseries:
            timeseries = self._parse_edf_signals(edf, sample_rate)

        return CPAPSession(
            start_time=start or datetime.min,
            end_time=end,
            duration_minutes=duration / 60.0,
            file_type=file_type,
            sample_rate=sample_rate,
            events=events,
            timeseries=timeseries,
        )

    def _parse_edf_events(self, edf: EDFParser) -> list[CPAPEvent]:
        """Extract TAL-format annotations from an EDF Annotations signal.

        Parses the ResMed-specific ``\\x15`` / ``\\x14`` delimited format
        used in ``EVE`` files.

        Args:
            edf: An already-parsed ``EDFParser`` instance.

        Returns:
            A list of ``CPAPEvent`` objects.
        """
        events: list[CPAPEvent] = []

        annotation_sig = None
        for sig in edf.signals:
            if sig.label == "EDF Annotations":
                annotation_sig = sig
                break

        if annotation_sig is None:
            return events

        raw = annotation_sig.data
        text = "".join(chr(v) if 32 <= v < 127 else " " for v in raw)

        event_pattern = re.compile(
            r"(\d+)(?:\.(\d+))?\x15(\d+)(?:\.(\d+))?\x15([^\x14\x15]+)\x14"
        )

        for match in event_pattern.finditer(text):
            onset_sec = float(match.group(1) or 0)
            duration_sec = None
            if match.group(3):
                duration_sec = float(match.group(3) or 0)
            event_type = match.group(5).strip()
            events.append(
                CPAPEvent(
                    timestamp_sec=onset_sec,
                    event_type=event_type,
                    duration_sec=duration_sec,
                )
            )

        return events

    def _parse_edf_signals(self, edf: EDFParser, sample_rate: float) -> TimeSeriesData:
        """Decode signal channels from an EDF file into ``TimeSeriesData``.

        Matches signal labels by prefix against ``SIGNAL_MAP`` and
        applies per-sample gain/offset correction.

        Args:
            edf: An already-parsed ``EDFParser`` instance.
            sample_rate: Nominal sample rate (Hz) for timestamp generation.

        Returns:
            A ``TimeSeriesData`` with decoded signal arrays.
        """
        mapped: dict[str, list[float]] = {
            "Flow": [],
            "MaskPressure": [],
            "Leak": [],
            "TidalVolume": [],
            "MinuteVent": [],
            "RespRate": [],
            "SpO2": [],
            "Pulse": [],
            "Pressure": [],
        }

        for sig in edf.signals:
            for target_key, prefixes in SIGNAL_MAP.items():
                if any(sig.label.upper().startswith(p.upper()) for p in prefixes):
                    vals = [float(v) * sig.gain + sig.offset for v in sig.data]
                    mapped[target_key] = vals
                    break

        n = max(len(v) for v in mapped.values())
        timestamps = [i / sample_rate for i in range(n)] if sample_rate > 0 else []

        return TimeSeriesData(
            timestamps=timestamps,
            flow_rate=mapped["Flow"],
            mask_pressure=mapped["MaskPressure"],
            leak=mapped["Leak"],
            tidal_volume=mapped["TidalVolume"],
            minute_ventilation=mapped["MinuteVent"],
            respiratory_rate=mapped["RespRate"],
            spo2=mapped["SpO2"],
            pulse=mapped["Pulse"],
        )

    def _map_machine_info(self, info) -> MachineInfo:
        """Convert a cpap-py identification object to ``MachineInfo``."""
        if info is None:
            return MachineInfo(serial_number="Unknown")

        return MachineInfo(
            serial_number=getattr(info, "serial", "Unknown"),
            product_code=getattr(info, "model_number", ""),
            model=getattr(info, "model", ""),
            series=getattr(info, "series", ""),
            properties=dict(getattr(info, "properties", {})),
        )

    def _map_summaries(self, records) -> list[CPAPSessionSummary]:
        """Convert cpap-py STR.edf records into ``CPAPSessionSummary`` objects.

        Args:
            records: List of STR.edf record objects from ``STRParser``.

        Returns:
            A list of ``CPAPSessionSummary`` with one entry per date.
        """
        summaries: list[CPAPSessionSummary] = []
        for rec in records or []:
            if rec.date is None:
                continue
            mode_name = PRESSURE_MODE_NAMES.get(getattr(rec, "mode", 0), "Unknown")
            usage_hours = getattr(rec, "mask_duration", 0) / 3600.0
            summaries.append(
                CPAPSessionSummary(
                    date=rec.date,
                    ahi=getattr(rec, "ahi", 0.0) or 0.0,
                    ai=getattr(rec, "ai", 0.0) or 0.0,
                    hi=getattr(rec, "hi", 0.0) or 0.0,
                    cai=getattr(rec, "cai", 0.0) or 0.0,
                    oai=getattr(rec, "oai", 0.0) or 0.0,
                    leak_50=getattr(rec, "leak_50", 0.0) or 0.0,
                    leak_95=getattr(rec, "leak_95", 0.0) or 0.0,
                    pressure_50=getattr(rec, "mp_50", 0.0) or 0.0,
                    pressure_95=getattr(rec, "mp_95", 0.0) or 0.0,
                    usage_hours=usage_hours,
                    pressure_mode=mode_name,
                )
            )
        return summaries
