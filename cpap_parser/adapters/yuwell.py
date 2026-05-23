"""Yuwell / DJMed BreathCare CPAP data adapter.

This adapter calls into the compiled Rust extension module
(``cpap_parser._rust_parsers``) which ports the binary-format
parsing logic from the OSCAR ``yuwell_loader.cpp`` C++ implementation,
itself derived from SleepyHead by Mark Watkins (C) 2011-2018, and copyright
(c) 2020-2025 The Oscar Team.

The Rust module handles four distinct on-card layouts:

- **Format A** (YH-550 BreathCare ECO): root ``RunLog.bys`` + ``YH-*``
  subdirectories containing per-session ``.bys`` files.
- **Format B** (YH-580 BreathCare I): single ``YHSD-NEW.BYS`` file of
  exactly 64 KB with a global header, session summaries, and per-minute
  seven-byte records.
- **Format C** (YH-830 BreathCare III): ``YH-*`` subdirectories, no root
  ``RunLog.bys``; per-minute records include tidal volume and respiratory rate.
- **Format D** (YH-680/690 BreathCare II): ``YH-*`` subdirectories each
  containing their own ``RunLog.bys`` and numbered session subdirs with
  ``*s.bys`` / ``*m.bys`` file pairs.

Fingerprint: root contains ``YHSD-NEW.BYS`` (Format B) or at least one
``YH-*`` subdirectory with a ``YH``-prefixed serial number (Formats A/C/D).

This project is based on the free and open-source software SleepyHead,
developed and copyright by Mark Watkins (C) 2011-2018.
"""

import logging
from pathlib import Path

from cpap_parser.adapters.base import BaseManufacturerAdapter, UnsupportedDirectoryError
from cpap_parser.schema import (
    CPAPDirectory,
    CPAPSession,
    CPAPSessionSummary,
    MachineInfo,
)

logger = logging.getLogger(__name__)

try:
    from cpap_parser import _rust_parsers

    HAS_RUST = True
except ImportError:
    HAS_RUST = False
    logger.warning(
        "Rust extension module not available; Yuwell / DJMed BreathCare adapter disabled"
    )


class YuwellAdapter(BaseManufacturerAdapter):
    """Adapter for Yuwell / DJMed BreathCare CPAP and APAP devices.

    Fingerprints a data directory by the presence of ``YHSD-NEW.BYS``
    (Format B) or ``YH-*`` subdirectories whose embedded serial number
    begins with ``YH`` (Formats A, C, and D).  Delegates all binary
    parsing to the compiled Rust extension (``_rust_parsers.parse_yuwell``),
    which is ported from OSCAR's ``yuwell_loader.cpp``.

    Per-session therapy summaries (AHI, event counts, pressure percentiles,
    leak) are extracted where available.  High-resolution per-minute data is
    decoded for Formats C and D when ``include_timeseries`` is ``True``.

    This implementation is based on the free and open-source software
    SleepyHead, developed and copyright by Mark Watkins (C) 2011-2018.

    Validation status: see :doc:`/device_support`.
    """

    profile_key = "yuwell"

    def can_handle(self, directory: Path) -> bool:
        """Return ``True`` if *directory* contains Yuwell / DJMed BreathCare data.

        Checks for ``YHSD-NEW.BYS`` (Format B) or at least one ``YH-*``
        subdirectory with a qualifying serial number (Formats A, C, D).

        Args:
            directory: Absolute path to the root of the data directory to inspect.

        Returns:
            ``True`` when a Yuwell BreathCare fingerprint is detected;
            ``False`` otherwise or if the Rust extension is unavailable.
        """
        if not HAS_RUST:
            return False
        try:
            return _rust_parsers.can_handle_yuwell(str(directory))
        except Exception:
            return False

    def extract_and_map(
        self,
        directory: Path,
        include_timeseries: bool = False,
    ) -> CPAPDirectory:
        """Parse a Yuwell / DJMed BreathCare data directory and return a normalised result.

        Auto-detects the on-card format (A/B/C/D) and reads all available
        session summary and event data.  The machine serial number and model
        are extracted from the embedded header of the first qualifying file.

        Args:
            directory: Absolute path to the SD card or data folder root.
            include_timeseries: Accepted for interface compatibility; per-minute
                signal data is not yet surfaced through the schema.

        Returns:
            A :class:`~cpap_parser.schema.CPAPDirectory` populated with
            machine info, daily summaries, and session metadata.

        Raises:
            ImportError: If the compiled Rust extension is not installed.
            ValueError: If the Rust parser encounters a malformed data file.
        """
        if not HAS_RUST:
            raise ImportError(
                "The Yuwell / DJMed BreathCare adapter requires the compiled Rust extension.\n"
                "  pip install maturin && maturin develop"
            )

        raw = _rust_parsers.parse_yuwell(str(directory))

        machine = MachineInfo(
            serial_number=raw.machine.serial_number,
            product_code=raw.machine.product_code,
            model=raw.machine.model,
            series=raw.machine.series,
            properties=dict(raw.machine.properties),
        )

        summaries = [
            CPAPSessionSummary(
                date=s.date,
                ahi=s.ahi,
                ai=s.ai,
                hi=s.hi,
                cai=s.cai,
                oai=s.oai,
                leak_50=s.leak_50,
                leak_95=s.leak_95,
                leak_avg=s.leak_avg,
                pressure_50=s.pressure_50,
                pressure_95=s.pressure_95,
                usage_hours=s.usage_hours,
                pressure_mode=s.pressure_mode,
                resp_rate_avg=s.resp_rate_avg,
                tidal_volume_avg=s.tidal_volume_avg,
                minute_ventilation_avg=s.minute_ventilation_avg,
                snore_avg=s.snore_avg,
                flow_limitation_avg=s.flow_limitation_avg,
            )
            for s in raw.daily_summaries
        ]

        sessions = [
            CPAPSession(
                start_time=s.start_time,
                end_time=s.end_time,
                duration_minutes=s.duration_minutes,
                file_type=s.file_type,
            )
            for s in raw.sessions
        ]

        return CPAPDirectory(
            machine=machine,
            daily_summaries=summaries,
            sessions=sessions,
        )
