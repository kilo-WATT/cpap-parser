"""BMC / 3B Medical CPAP data adapter.

This adapter calls into the compiled Rust extension module
(``open_cpap_parser._rust_parsers``) which ports the binary-format
parsing logic from the OSCAR ``bmc_loader.cpp`` and
``bmcDataParsing.cpp`` C++ implementation.

The Rust module handles:
  - ``*.USR``: main data file containing machine identity (serial at 0x2D,
    model at 0x2296), in-progress session block at 0x431, and historic
    session records starting at 0x102340
  - ``*.idx``: 512-byte index packets (0xAAAA header, date, waveform
    file/offset references, and machine settings at 0x140)
  - ``*.000``, ``*.001``, …: 256-byte waveform packets at 25 Hz

Supported devices: GII, iBreeze, and other BMC / 3B Medical CPAP/APAP units.

Fingerprint: presence of a ``.USR`` file alongside matching ``.idx`` and
``.000`` waveform files in the same directory.

This project is based on the free and open-source software SleepyHead,
developed and copyright by Mark Watkins (C) 2011-2018.
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
    logger.warning("Rust extension module not available; BMC / 3B Medical adapter disabled")


class BMCAdapter(BaseManufacturerAdapter):
    """Adapter for BMC / 3B Medical CPAP and APAP devices (GII, iBreeze series).

    Fingerprints a data directory by the presence of a ``.USR`` file
    alongside matching ``.idx`` and ``.000`` waveform files.  Delegates
    all binary parsing to the compiled Rust extension
    (``_rust_parsers.parse_bmc``), which is ported from OSCAR's
    ``bmc_loader.cpp`` and ``bmcDataParsing.cpp``.

    High-resolution waveform data (25 Hz flow, pressure, SpO₂, pulse) is
    available in the ``.nnn`` packet files; waveform decoding is included
    in the Rust parser but only surfaced when ``include_timeseries`` is
    ``True``.

    This implementation is based on the free and open-source software
    SleepyHead, developed and copyright by Mark Watkins (C) 2011-2018.

    Validation status: see :doc:`/device_support`.
    """

    profile_key = "bmc"

    def can_handle(self, directory: Path) -> bool:
        """Return ``True`` if *directory* contains BMC data files.

        The check requires a ``.USR`` file in *directory* alongside a
        matching ``.idx`` index file and ``.000`` waveform file.

        Args:
            directory: Absolute path to the root of the data directory to inspect.

        Returns:
            ``True`` when the BMC three-file fingerprint is detected;
            ``False`` otherwise or if the Rust extension is unavailable.
        """
        if not HAS_RUST:
            return False
        try:
            return _rust_parsers.can_handle_bmc(str(directory))
        except Exception:
            return False

    def extract_and_map(
        self,
        directory: Path,
        include_timeseries: bool = False,
    ) -> CPAPDirectory:
        """Parse a BMC data directory and return a normalised result.

        Reads the ``.USR`` file for machine identity and session records,
        the ``.idx`` file for date-indexed waveform references and machine
        settings, and optionally the ``.nnn`` waveform files for
        high-resolution time-series data.

        Args:
            directory: Absolute path to the SD card or data folder root.
            include_timeseries: If ``True``, decode 25 Hz waveform packets
                from the ``.nnn`` files and attach them to each session.

        Returns:
            A :class:`~open_cpap_parser.schema.CPAPDirectory` populated with
            machine info, daily summaries, and session metadata.

        Raises:
            ImportError: If the compiled Rust extension is not installed.
            ValueError: If the Rust parser encounters a malformed data file.
        """
        if not HAS_RUST:
            raise ImportError(
                "The BMC / 3B Medical adapter requires the compiled Rust extension.\n"
                "  pip install maturin && maturin develop"
            )

        raw = _rust_parsers.parse_bmc(str(directory))

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
