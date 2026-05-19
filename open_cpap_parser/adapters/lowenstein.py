"""Lowenstein Medical / Weinmann CPAP data adapter.

This adapter calls into the compiled Rust extension module
(``open_cpap_parser._rust_parsers``) which ports the binary-format
parsing logic from the OSCAR ``weinmann_loader.cpp`` C++ implementation.

The Rust module handles:
  - ``WM_DATA.TDF``: 32-byte file header (model, serial) followed by
    tagged record blocks containing daily therapy summaries

Supported devices: Prisma SMART, Prisma SMART MAX, Lumis, SOMNOsoft series.

Fingerprint: presence of ``WM_DATA.TDF`` in the SD card root directory.

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
    logger.warning(
        "Rust extension module not available; Lowenstein Medical adapter disabled"
    )


class LowensteinAdapter(BaseManufacturerAdapter):
    """Adapter for Lowenstein Medical (Weinmann) CPAP/BiPAP devices.

    Fingerprints a data directory by the presence of a root-level
    ``WM_DATA.TDF`` file.  Delegates all binary parsing to the compiled
    Rust extension (``_rust_parsers.parse_lowenstein``), which is ported
    from OSCAR's ``weinmann_loader.cpp``.

    The adapter does not decode high-resolution waveform data; the
    ``WM_DATA.TDF`` format does not expose per-breath time-series on the
    SD card.  The ``include_timeseries`` parameter is accepted for
    interface compatibility but has no effect.

    This implementation is based on the free and open-source software
    SleepyHead, developed and copyright by Mark Watkins (C) 2011-2018.
    """

    def can_handle(self, directory: Path) -> bool:
        """Return ``True`` if *directory* contains a ``WM_DATA.TDF`` file.

        Args:
            directory: Absolute path to the root of the data directory to inspect.

        Returns:
            ``True`` when ``WM_DATA.TDF`` is present at the directory root;
            ``False`` otherwise or if the Rust extension is unavailable.
        """
        if not HAS_RUST:
            return False
        try:
            return _rust_parsers.can_handle_lowenstein(str(directory))
        except Exception:
            return False

    def extract_and_map(
        self,
        directory: Path,
        include_timeseries: bool = False,
    ) -> CPAPDirectory:
        """Parse a Lowenstein data directory and return a normalised result.

        Reads ``WM_DATA.TDF`` for machine identity and iterates all tagged
        session blocks to build daily summaries and session metadata.

        Args:
            directory: Absolute path to the SD card or data folder root.
            include_timeseries: Accepted for interface compatibility; the
                ``WM_DATA.TDF`` format does not expose per-breath waveforms
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
                "The Lowenstein Medical adapter requires the compiled Rust extension.\n"
                "  pip install maturin && maturin develop"
            )

        raw = _rust_parsers.parse_lowenstein(str(directory))

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
