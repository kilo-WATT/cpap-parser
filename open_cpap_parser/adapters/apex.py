"""Apex Medical CPAP data adapter.

This adapter calls into the compiled Rust extension module
(``open_cpap_parser._rust_parsers``) which ports the binary-format
parsing logic from the OSCAR ``ApexLoader.cpp`` C++ implementation.

The Rust module handles:
  - ``APDATA/INFO.APC``: 64-byte device-identity record (model, serial, firmware)
  - ``APDATA/YYYYMMDD.APC``: daily session files containing one or more
    48-byte therapy records (pressure, leak, respiratory-event indices)

Fingerprint: presence of an ``APDATA/`` subdirectory containing at least
one ``.APC`` file.
"""

import logging
from pathlib import Path

from open_cpap_parser.adapters.base import BaseManufacturerAdapter, UnsupportedDirectoryError
from open_cpap_parser.schema import (
    CPAPDirectory,
    CPAPSession,
    CPAPSessionSummary,
    MachineInfo,
)

logger = logging.getLogger(__name__)

try:
    from open_cpap_parser import _rust_parsers

    HAS_RUST = True
except ImportError:
    HAS_RUST = False
    logger.warning("Rust extension module not available; Apex Medical adapter disabled")


class ApexAdapter(BaseManufacturerAdapter):
    """Adapter for Apex Medical CPAP/APAP devices (XT, XT Auto, iCH, Spirit series).

    Fingerprints a data directory by the presence of an ``APDATA/`` subdirectory
    containing at least one ``.APC`` session file.  Delegates all binary
    parsing to the compiled Rust extension (``_rust_parsers.parse_apex``).

    The adapter does **not** decode high-resolution waveform data; Apex Medical
    devices do not expose per-breath time-series in the SD-card format ported
    from OSCAR.  The ``include_timeseries`` parameter is accepted for interface
    compatibility but has no effect.
    """

    def can_handle(self, directory: Path) -> bool:
        """Return ``True`` if *directory* looks like an Apex Medical SD card root.

        Args:
            directory: Absolute path to the root of the data directory to inspect.

        Returns:
            ``True`` when an ``APDATA/`` subdirectory exists and contains at
            least one ``.APC`` file; ``False`` otherwise or if the Rust
            extension is unavailable.
        """
        if not HAS_RUST:
            return False
        try:
            return _rust_parsers.can_handle_apex(str(directory))
        except Exception:
            return False

    def extract_and_map(
        self,
        directory: Path,
        include_timeseries: bool = False,
    ) -> CPAPDirectory:
        """Parse an Apex Medical data directory and return a normalised result.

        Reads ``APDATA/INFO.APC`` for machine identity then iterates every
        ``APDATA/*.APC`` session file to build daily summaries and session
        blocks.

        Args:
            directory: Absolute path to the SD card or data folder root.
            include_timeseries: Accepted for interface compatibility; Apex
                Medical's SD-card format does not expose per-breath waveforms
                so this flag has no effect.

        Returns:
            A :class:`~open_cpap_parser.schema.CPAPDirectory` populated with
            machine info, daily summaries, and session metadata.

        Raises:
            ImportError: If the compiled Rust extension is not installed.
            ValueError: If the Rust parser returns a malformed or truncated
                data structure.
        """
        if not HAS_RUST:
            raise ImportError(
                "The Apex Medical adapter requires the compiled Rust extension.\n"
                "  pip install maturin && maturin develop"
            )

        raw = _rust_parsers.parse_apex(str(directory))

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
