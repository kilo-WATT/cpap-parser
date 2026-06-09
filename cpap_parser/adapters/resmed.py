"""ResMed CPAP data adapter.

Parses ResMed SD card directories using the ``cpap-py`` library.
Handles AirSense 10/11 data including identification, STR.edf daily
summaries, and DATALOG session files with high-resolution signals.
"""

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from cpap_parser.adapters.base import BaseManufacturerAdapter

try:
    from cpap_py import IdentificationParser, STRParser, DatalogParser, EDFParser  # type: ignore[import-untyped]

    HAS_CPAP_PY = True
except ImportError:
    HAS_CPAP_PY = False
from cpap_parser.schema import (
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

# High-rate BRP signals (25 Hz)
BRP_SIGNAL_MAP: dict[str, list[str]] = {
    "flow_rate": ["Flow"],
    "pressure": ["Press", "Pressure"],
}

# Low-rate PLD signals (0.5 Hz)
PLD_SIGNAL_MAP: dict[str, list[str]] = {
    "mask_pressure": ["MaskPress", "Mask Pressure", "MaskPressure"],
    "leak": ["Leak"],
    "tidal_volume": ["TidVol", "Tidal Volume", "TidalVolume", "TV"],
    "minute_ventilation": ["MinVent", "Minute Vent", "MinuteVent", "MV"],
    "respiratory_rate": ["RespRate", "Resp. Rate", "Respiratory Rate", "RR"],
    "snore": ["Snore"],
    "flow_limitation": ["FlowLim", "Flow Limitation", "FlowLimit"],
}

# Oximetry signals (SA2 file)
OXI_SIGNAL_MAP: dict[str, list[str]] = {
    "spo2": ["SpO2", "SpO₂"],
    "pulse": ["Pulse"],
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


def _prefix_gap_seconds(earlier: str, later: str) -> int:
    """Compute the gap in seconds between two timestamp prefix strings.

    Prefix format: "YYYYMMDD_HHMMSS".  Returns a large value on parse error.
    """
    try:
        fmt = "%Y%m%d_%H%M%S"
        t_early = datetime.strptime(earlier, fmt)
        t_late = datetime.strptime(later, fmt)
        return int((t_late - t_early).total_seconds())
    except ValueError:
        return 9999


class ResMedAdapter(BaseManufacturerAdapter):
    """Adapter for ResMed AirSense and AirCurve devices.

    Fingerprints the directory by the presence of a ``DATALOG/``
    subdirectory.  Uses ``cpap-py`` components (``IdentificationParser``,
    ``STRParser``, ``DatalogParser``, ``EDFParser``) for low-level
    file parsing.

    Validation status: see :doc:`/device_support`.
    """

    profile_key = "resmed"

    def can_handle(self, directory: Path) -> bool:
        """Return ``True`` if *directory* contains a ``DATALOG/`` folder.

        Args:
            directory: Absolute path to the root of the data directory to inspect.

        Returns:
            ``True`` when the ResMed ``DATALOG/`` fingerprint is found;
            ``False`` otherwise or if ``cpap-py`` is not installed.
        """
        if not HAS_CPAP_PY:
            return False
        datalog = directory / "DATALOG"
        return datalog.is_dir()

    def extract_and_map(
        self, directory: Path, include_timeseries: bool = False
    ) -> CPAPDirectory:
        """Parse a ResMed directory and return a normalised ``CPAPDirectory``.

        Reads ``Identification.json`` for machine identity, ``STR.edf`` for
        daily summaries, and all ``DATALOG/*.edf`` files for session data.

        Args:
            directory: Absolute path to the root of the ResMed SD card.
            include_timeseries: If ``True``, decode high-resolution signal
                channels from DATALOG EDF files into ``TimeSeriesData``.

        Returns:
            A ``CPAPDirectory`` with machine info, daily summaries,
            and per-file sessions.

        Raises:
            ImportError: If ``cpap-py`` is not installed.
        """
        if not HAS_CPAP_PY:
            raise ImportError(
                "The ResMed adapter requires 'cpap-py'.\n"
                "  pip install cpap-py"
            )
        machine = self._load_machine_info(directory)
        summaries = self._load_summaries(directory)
        sessions = self._load_sessions(directory, include_timeseries)
        self._annotate_summaries(summaries, sessions)

        return CPAPDirectory(
            machine=machine,
            daily_summaries=summaries,
            sessions=sessions,
        )

    def _load_machine_info(self, directory: Path) -> MachineInfo:
        """Read machine identity from ``Identification.json`` or ``Identification.tgt``.

        Tries ``cpap-py``'s ``IdentificationParser`` first.  If that returns
        ``None`` or a missing/empty serial, falls back to direct file parsing so
        that non-numeric or fixture serials are never silently replaced with
        ``"Unknown"``.
        """
        product_code = model = series = ""
        properties: dict[str, str] = {}

        try:
            parser = IdentificationParser(str(directory))
            info = parser.parse()
            if info is not None:
                serial = getattr(info, "serial", None) or None
                if serial and serial != "Unknown":
                    return MachineInfo(
                        serial_number=serial,
                        product_code=getattr(info, "model_number", ""),
                        model=getattr(info, "model", ""),
                        series=getattr(info, "series", ""),
                        properties=dict(getattr(info, "properties", {})),
                    )
                # cpap-py found metadata but no usable serial; keep non-serial fields
                product_code = getattr(info, "model_number", "") or ""
                model = getattr(info, "model", "") or ""
                series = getattr(info, "series", "") or ""
                properties = dict(getattr(info, "properties", {}))
        except Exception as exc:
            logger.warning("cpap-py IdentificationParser failed: %s", exc)

        # Direct fallback: parse identity files ourselves
        serial = self._parse_identity_fallback(directory)
        return MachineInfo(
            serial_number=serial,
            product_code=product_code,
            model=model,
            series=series,
            properties=properties,
        )

    def _parse_identity_fallback(self, directory: Path) -> str | None:
        """Extract serial number directly from Identification.tgt or .json."""
        tgt = directory / "Identification.tgt"
        if tgt.is_file():
            serial = self._parse_tgt_serial(tgt)
            if serial:
                return serial

        jsn = directory / "Identification.json"
        if jsn.is_file():
            serial = self._parse_json_serial(jsn)
            if serial:
                return serial

        return None

    @staticmethod
    def _parse_tgt_serial(path: Path) -> str | None:
        """Return the serial from a ResMed .tgt identity file.

        Handles both the ``#SRN <value>`` format used by AirSense 10/11
        and the ``SerialNo=<value>`` INI-style format used by older devices.
        """
        try:
            for line in path.read_text(errors="replace").splitlines():
                stripped = line.strip()
                if stripped.upper().startswith("#SRN "):
                    val = stripped[5:].strip()
                    return val if val else None
                if stripped.upper().startswith("SERIALNO="):
                    val = stripped.split("=", 1)[1].strip()
                    return val if val else None
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", path.name, exc)
        return None

    @staticmethod
    def _parse_json_serial(path: Path) -> str | None:
        """Return the serial from a ResMed Identification.json file.

        Tries three nesting patterns used across different firmware versions:
          ``data["SerialNo"]``, ``data["Identification"]["SerialNo"]``,
          ``data["Device"]["SerialNo"]``.
        """
        import json

        try:
            data = json.loads(path.read_text())
            for key_path in (
                ("SerialNo",),
                ("Identification", "SerialNo"),
                ("Device", "SerialNo"),
            ):
                val: object = data
                for k in key_path:
                    if not isinstance(val, dict):
                        val = None
                        break
                    val = val.get(k)  # type: ignore[union-attr]
                if val and isinstance(val, str):
                    return val
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", path.name, exc)
        return None

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
            summaries = self._map_summaries(parser.records)
            self._patch_pressure_from_edf(parser.edf, summaries)
            return summaries
        except Exception as exc:
            logger.warning("Failed to load STR.edf: %s", exc)
            return []

    def _load_sessions(
        self, directory: Path, include_timeseries: bool
    ) -> list[CPAPSession]:
        """Iterate over all DATALOG EDF files and parse each as a session.

        BRP and PLD files that share the same timestamp prefix are merged
        into a single ``CPAPSession`` with two sample-rate tracks.
        """
        datalog = directory / "DATALOG"
        if not datalog.is_dir():
            return []

        try:
            dlp = DatalogParser(str(datalog))
        except Exception as exc:
            logger.warning("Failed to initialize DatalogParser: %s", exc)
            return []

        file_map = dlp.scan_files()
        # Group files by (date_key, timestamp_prefix) so BRP+PLD can merge.
        groups: dict[tuple, dict[str, Path]] = {}
        seen: set[str] = set()

        for date_key in sorted(file_map):
            for fpath in file_map[date_key]:
                fpath_str = str(fpath)
                if fpath_str in seen:
                    continue
                seen.add(fpath_str)
                stem = fpath.stem.upper()
                file_code = next(
                    (code for code in FILE_TYPE_NAMES if code in stem), ""
                )
                # Timestamp prefix is everything before the type code, e.g. "20260203_215451_"
                prefix = fpath.stem
                for code in FILE_TYPE_NAMES:
                    prefix = prefix.replace(code, "").replace(code.lower(), "")
                prefix = prefix.rstrip("_")
                group_key = (date_key, prefix)
                groups.setdefault(group_key, {})[file_code] = fpath

        # EVE files have a slightly earlier timestamp than BRP/PLD (the device
        # starts logging events before the waveform capture begins).  Merge lone
        # EVE/CSL groups into the nearest BRP+PLD group within 120 seconds.
        waveform_keys = [k for k, g in groups.items() if "BRP" in g or "PLD" in g]
        for eve_key, eve_group in list(groups.items()):
            if "BRP" in eve_group or "PLD" in eve_group:
                continue
            if "EVE" not in eve_group:
                continue
            best_key = self._find_nearest_waveform_group(eve_key, waveform_keys)
            if best_key is not None:
                groups[best_key].setdefault("EVE", eve_group["EVE"])
                del groups[eve_key]

        sessions: list[CPAPSession] = []
        for group_key in sorted(groups):
            file_group = groups[group_key]
            try:
                session = self._parse_file_group(file_group, include_timeseries)
                if session is not None:
                    sessions.append(session)
            except Exception as exc:
                names = [p.name for p in file_group.values()]
                logger.warning("Skipping corrupt session group %s: %s", names, exc)

        return sessions

    @staticmethod
    def _find_nearest_waveform_group(
        eve_key: tuple, waveform_keys: list[tuple], max_gap_seconds: int = 120
    ) -> tuple | None:
        """Return the waveform group key nearest to (and after) the EVE key."""
        date_key, eve_prefix = eve_key
        best: tuple | None = None
        best_gap = max_gap_seconds + 1
        for wk in waveform_keys:
            if wk[0] != date_key:
                continue
            wk_prefix = wk[1]
            # Prefixes are like "20260203_215441"; compare as strings (YYYYMMDD_HHMMSS sorts lexically).
            if wk_prefix < eve_prefix:
                continue
            gap_str_secs = _prefix_gap_seconds(eve_prefix, wk_prefix)
            if gap_str_secs < best_gap:
                best_gap = gap_str_secs
                best = wk
        return best

    def _parse_file_group(
        self, file_group: dict[str, Path], include_timeseries: bool
    ) -> CPAPSession | None:
        """Parse a group of related EDF files into a single ``CPAPSession``.

        If both BRP and PLD files are present they are merged: the BRP
        provides the high-rate flow/pressure track and the PLD provides
        the low-rate therapy signals.  Other file types (EVE, SA2, …) are
        parsed on their own.
        """
        if "BRP" in file_group or "PLD" in file_group:
            return self._parse_brp_pld_group(file_group, include_timeseries)

        # Single-file session for EVE, SA2, CSL, etc.
        for code, fpath in file_group.items():
            return self._parse_edf_file(fpath, code, include_timeseries)
        return None

    def _parse_brp_pld_group(
        self, file_group: dict[str, Path], include_timeseries: bool
    ) -> CPAPSession | None:
        """Merge BRP + PLD files into one session with two waveform tracks."""
        brp_path = file_group.get("BRP")
        pld_path = file_group.get("PLD")

        # Use whichever file is present as the timing anchor (prefer BRP).
        anchor_path = brp_path or pld_path
        assert anchor_path is not None

        edf_anchor = EDFParser(str(anchor_path))
        if not edf_anchor.parse() or edf_anchor.header.num_data_records == 0:
            return None

        start = edf_anchor.header.start_date
        duration = edf_anchor.header.num_data_records * edf_anchor.header.duration_seconds
        end = start + timedelta(seconds=duration) if start else datetime.min

        brp_rate = 0.0
        brp_ts: TimeSeriesData | None = None
        if brp_path and include_timeseries:
            edf_brp = edf_anchor if brp_path == anchor_path else EDFParser(str(brp_path))
            if brp_path != anchor_path:
                edf_brp.parse()
            brp_rate = self._sample_rate(edf_brp)
            brp_ts = self._parse_brp_signals(edf_brp, brp_rate, start or datetime.min)

        pld_ts: TimeSeriesData | None = None
        if pld_path and include_timeseries:
            edf_pld = edf_anchor if pld_path == anchor_path else EDFParser(str(pld_path))
            if pld_path != anchor_path:
                edf_pld.parse()
            pld_rate = self._sample_rate(edf_pld)
            pld_ts = self._parse_pld_signals(edf_pld, pld_rate, start or datetime.min)

        timeseries: TimeSeriesData | None = None
        if include_timeseries:
            timeseries = self._merge_timeseries(brp_ts, pld_ts)

        events: list[CPAPEvent] = []
        eve_path = file_group.get("EVE")
        if eve_path is not None:
            try:
                edf_eve = EDFParser(str(eve_path))
                if edf_eve.parse():
                    events = self._parse_edf_events(edf_eve)
            except Exception as exc:
                logger.warning("Failed to parse EVE file %s: %s", eve_path.name, exc)

        parts = [code for code in ("BRP", "PLD") if code in file_group]
        file_type = "+".join(parts) if len(parts) > 1 else (parts[0] if parts else "")

        return CPAPSession(
            start_time=start or datetime.min,
            end_time=end,
            duration_minutes=duration / 60.0,
            file_type=file_type,
            sample_rate=brp_rate,
            events=events,
            timeseries=timeseries,
        )

    def _parse_edf_file(
        self, fpath: Path, file_type: str, include_timeseries: bool
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

        events: list[CPAPEvent] = []
        if file_type == "EVE":
            events = self._parse_edf_events(edf)

        sample_rate = self._sample_rate(edf)

        timeseries: TimeSeriesData | None = None
        if include_timeseries and file_type not in ("EVE", "CSL", "AEV"):
            timeseries = self._parse_generic_signals(edf, sample_rate, start or datetime.min)

        return CPAPSession(
            start_time=start or datetime.min,
            end_time=end,
            duration_minutes=duration / 60.0,
            file_type=file_type,
            sample_rate=sample_rate,
            events=events,
            timeseries=timeseries,
        )

    @staticmethod
    def _sample_rate(edf: "EDFParser") -> float:
        if edf.signals and edf.header.duration_seconds > 0:
            return edf.signals[0].sample_count / edf.header.duration_seconds
        return 0.0

    @staticmethod
    def _decode_signal(sig, signal_map: dict[str, list[str]]) -> dict[str, list[float]]:
        """Match *all* signals against a signal map and return decoded arrays."""
        result: dict[str, list[float]] = {k: [] for k in signal_map}
        for s in sig:
            for field, prefixes in signal_map.items():
                if any(s.label.upper().startswith(p.upper()) for p in prefixes):
                    result[field] = [float(v) * s.gain + s.offset for v in s.data]
                    break
        return result

    def _parse_brp_signals(
        self, edf: EDFParser, sample_rate: float, session_start: datetime
    ) -> TimeSeriesData:
        """Decode BRP high-rate signals into the high-rate track."""
        decoded = self._decode_signal(edf.signals, BRP_SIGNAL_MAP)
        n = max((len(v) for v in decoded.values()), default=0)
        base = session_start.timestamp()
        timestamps = [base + i / sample_rate for i in range(n)] if sample_rate > 0 else []
        return TimeSeriesData(
            timestamps=timestamps,
            flow_rate=decoded["flow_rate"],
            pressure=decoded["pressure"],
        )

    def _parse_pld_signals(
        self, edf: EDFParser, sample_rate: float, session_start: datetime
    ) -> TimeSeriesData:
        """Decode PLD low-rate signals into the low-rate track."""
        decoded = self._decode_signal(edf.signals, PLD_SIGNAL_MAP)
        n = max((len(v) for v in decoded.values()), default=0)
        base = session_start.timestamp()
        timestamps_low = [base + i / sample_rate for i in range(n)] if sample_rate > 0 else []
        return TimeSeriesData(
            timestamps_low=timestamps_low,
            mask_pressure=decoded["mask_pressure"],
            leak=decoded["leak"],
            tidal_volume=decoded["tidal_volume"],
            minute_ventilation=decoded["minute_ventilation"],
            respiratory_rate=decoded["respiratory_rate"],
            snore=decoded["snore"],
            flow_limitation=decoded["flow_limitation"],
        )

    def _parse_generic_signals(
        self, edf: EDFParser, sample_rate: float, session_start: datetime
    ) -> TimeSeriesData:
        """Decode oximetry or unknown signals using all maps."""
        oxi = self._decode_signal(edf.signals, OXI_SIGNAL_MAP)
        n = max((len(v) for v in oxi.values()), default=0)
        base = session_start.timestamp()
        timestamps = [base + i / sample_rate for i in range(n)] if sample_rate > 0 else []
        return TimeSeriesData(timestamps=timestamps, **oxi)

    @staticmethod
    def _merge_timeseries(
        brp: TimeSeriesData | None, pld: TimeSeriesData | None
    ) -> TimeSeriesData:
        """Combine BRP high-rate and PLD low-rate tracks into one object."""
        merged = TimeSeriesData()
        if brp is not None:
            merged.timestamps = brp.timestamps
            merged.flow_rate = brp.flow_rate
            merged.pressure = brp.pressure
        if pld is not None:
            merged.timestamps_low = pld.timestamps_low
            merged.mask_pressure = pld.mask_pressure
            merged.leak = pld.leak
            merged.tidal_volume = pld.tidal_volume
            merged.minute_ventilation = pld.minute_ventilation
            merged.respiratory_rate = pld.respiratory_rate
            merged.snore = pld.snore
            merged.flow_limitation = pld.flow_limitation
        return merged

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

        # EDF stores annotation data as 16-bit little-endian samples; decode
        # both bytes of each 16-bit value to recover the raw TAL byte stream.
        raw_bytes = bytes(
            b for v in annotation_sig.data for b in (v & 0xFF, (v >> 8) & 0xFF)
        )

        # ResMed TAL format: b'+<onset>\x15<duration>\x14<text>\x14'
        # where onset and duration are ASCII decimal strings.
        _NON_EVENTS = {b"Recording starts", b"EDF Annotations"}
        event_pattern = re.compile(
            rb"\+(\d+(?:\.\d+)?)\x15(\d+(?:\.\d+)?)\x14([^\x14\x00]+)\x14"
        )

        for match in event_pattern.finditer(raw_bytes):
            annotation = match.group(3)
            if annotation in _NON_EVENTS:
                continue
            events.append(
                CPAPEvent(
                    timestamp_sec=float(match.group(1)),
                    event_type=annotation.decode("ascii", errors="replace").strip(),
                    duration_sec=float(match.group(2)),
                )
            )

        return events

    def _patch_pressure_from_edf(self, edf, summaries: list[CPAPSessionSummary]) -> None:
        """Read pressure signals directly from the EDF, bypassing cpap-py's wrong lookups.

        cpap-py's STRParser looks for 'Press.95'/'MaskPres.95' but AirSense 11
        STR.edf uses 'MaskPress.95', so mp_50/mp_95 on every STRRecord is 0.0.

        OSCAR's "95% Pressure" column is the 95th percentile of the APAP target
        pressure (TgtIPAP), not the delivered mask pressure (MaskPress). TgtIPAP
        accounts for EPR and better matches what OSCAR exports.

        The EDF record index for a given date must be computed from the file's
        start date — cpap-py skips days with no mask events, so enumerate() over
        summaries does NOT give the correct EDF record position.
        """
        from datetime import date as date_type

        start_dt = edf.header.start_date
        if start_dt is None:
            return
        edf_start: date_type = start_dt.date() if hasattr(start_dt, "date") else start_dt

        # Prefer TgtIPAP (matches OSCAR's "95% Pressure" export column).
        # Fall back to MaskPress when TgtIPAP is absent (e.g. fixed CPAP).
        p50_sig = edf.get_signal("TgtIPAP.50") or edf.get_signal("MaskPress.50")
        p95_sig = edf.get_signal("TgtIPAP.95") or edf.get_signal("MaskPress.95")

        for summary in summaries:
            summary_date: date_type = (
                summary.date if isinstance(summary.date, date_type)
                else summary.date.date()
            )
            rec_idx = (summary_date - edf_start).days
            if rec_idx < 0:
                continue
            if p50_sig and rec_idx < len(p50_sig.data):
                val = p50_sig.data[rec_idx] * p50_sig.gain + p50_sig.offset
                if val > 0:
                    summary.pressure_50 = val
            if p95_sig and rec_idx < len(p95_sig.data):
                val = p95_sig.data[rec_idx] * p95_sig.gain + p95_sig.offset
                if val > 0:
                    summary.pressure_95 = val


    @staticmethod
    def _night_date(dt: datetime):
        """Return the 'night' date for a session start time.

        Sessions that start before noon are considered part of the previous
        calendar night — matching how OSCAR groups fragmented/overnight sessions.
        """
        from datetime import date as _date, timedelta as _td
        d = dt.date() if hasattr(dt, "date") else dt
        if isinstance(d, _date) and dt.hour < 12:
            return d - _td(days=1)
        return d

    def _annotate_summaries(
        self,
        summaries: list[CPAPSessionSummary],
        sessions: list[CPAPSession],
    ) -> None:
        """Back-fill computed_usage, recording_span, and has_detailed_data on summaries.

        Groups sessions by their 'night date' (noon-to-noon) so that overnight
        fragmented sessions (whose later files start on the next calendar day)
        are correctly associated with the preceding night's summary.
        """
        from collections import defaultdict

        # Group sessions by night date.  ``by_night`` excludes annotation-only
        # files (they carry no therapy duration), but ``events_by_night``
        # gathers events from *every* session — including standalone EVE
        # sessions — so the AHI numerator is not lost when events live on a
        # file type we don't count toward duration.
        by_night: dict = defaultdict(list)
        events_by_night: dict = defaultdict(list)
        for s in sessions:
            nd = self._night_date(s.start_time)
            if s.events:
                events_by_night[nd].extend(s.events)
            if s.file_type in ("EVE", "CSL", "AEV"):
                continue  # annotation files; don't count toward therapy duration
            by_night[nd].append(s)

        for summary in summaries:
            night_sessions = by_night.get(summary.date, [])
            summary.has_detailed_data = len(night_sessions) > 0
            if night_sessions:
                # Prefer BRP-containing sessions for duration to avoid double-counting:
                # when BRP and PLD files have slightly different timestamp prefixes they
                # appear as separate CPAPSession objects but cover the same therapy period.
                brp = [s for s in night_sessions if "BRP" in s.file_type]
                counting = brp if brp else [
                    s for s in night_sessions if "PLD" in s.file_type
                ]
                total_minutes = sum(s.duration_minutes for s in counting)
                summary.computed_usage = total_minutes / 60.0
                first_start = min(s.start_time for s in night_sessions)
                last_end = max(s.end_time for s in night_sessions)
                summary.recording_span = (last_end - first_start).total_seconds() / 3600.0

                # AHI calculation matches OSCAR: count of respiratory events
                # (Clear Airway/Central + Obstructive + Hypopnea + Unclassified
                # apneas) divided by mask-on hours. RERAs are excluded (they
                # belong to RDI, not AHI). For nights with detailed DATALOG/EVE
                # data this reproduces OSCAR's event-derived AHI exactly, instead
                # of STR.edf's coarser device-reported value (quantized to 0.1).
                # STR-only "ghost" nights have no EVE events and keep the STR.edf
                # AHI set in _map_summaries as the best available approximation.
                # See: OSCAR resmed_loader.cpp / Day::calcAHI (oscar-system/OSCAR)
                event_ahi = self._compute_event_ahi(
                    events_by_night.get(summary.date, ()), summary.computed_usage
                )
                if event_ahi is not None:
                    summary.ahi = event_ahi

    @staticmethod
    def _compute_event_ahi(events, usage_hours: float) -> float | None:
        """Return the OSCAR-style AHI for a night, or ``None`` if uncomputable.

        AHI = (Clear Airway/Central + Obstructive + Unclassified apneas +
        Hypopneas) / mask-on hours. RERAs and non-respiratory annotations are
        excluded. Matching the apnea/hypopnea event-type strings by substring
        keeps every apnea variant ("Central Apnea", "Obstructive Apnea",
        "Unclassified Apnea") and "Hypopnea" while naturally excluding "RERA".

        Returns ``None`` when ``usage_hours`` is missing or non-positive, so the
        caller keeps the STR.edf device-reported AHI as a fallback.
        """
        if not usage_hours or usage_hours <= 0:
            return None
        count = sum(
            1
            for e in events
            if "apnea" in e.event_type.lower() or "hypopnea" in e.event_type.lower()
        )
        return count / usage_hours

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
            usage_hours = getattr(rec, "mask_duration", 0) / 60.0
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
                    summary_reported_usage=usage_hours,
                    pressure_mode=mode_name,
                )
            )
        return summaries
