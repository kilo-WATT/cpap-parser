"""Lowenstein Medical / Weinmann CPAP data adapter.

Supports two distinct on-device data formats:

**Weinmann legacy format** (``WM_DATA.TDF``)
    Older Weinmann / early Löwenstein devices — Prisma SMART, Prisma SMART MAX,
    Lumis, SOMNOsoft series.  Parsed by the compiled Rust extension module
    (``_rust_parsers.parse_lowenstein``), ported from the OSCAR
    ``weinmann_loader.cpp`` C++ implementation.

**Prisma Line format** (``config.pcfg`` + ``therapy.pdat``)
    Newer Löwenstein Prisma Line devices — prisma25S, prisma25ST, Eyra series.
    Data is exported as ZIP archives containing XML statistics and per-session
    event logs.  Parsed by ``open_cpap_parser.parsers.prisma_line``.

Fingerprints:
  - ``WM_DATA.TDF`` present → Weinmann legacy path (Rust parser)
  - ``config.pcfg`` present → Prisma Line path (Python parser)

This project is based on the free and open-source software SleepyHead,
developed and copyright by Mark Watkins (C) 2011-2018.
"""

import logging
from pathlib import Path

from open_cpap_parser.adapters.base import BaseManufacturerAdapter, UnsupportedDirectoryError
from open_cpap_parser.parsers import prisma_line as _prisma_line
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
    """Adapter for Löwenstein Medical (Weinmann) CPAP/BiPAP devices.

    Supports two data formats:

    * **Weinmann legacy** (``WM_DATA.TDF``): older devices parsed by the
      compiled Rust extension.
    * **Prisma Line** (``config.pcfg`` + ``therapy.pdat``): newer Löwenstein
      devices parsed by :mod:`open_cpap_parser.parsers.prisma_line`.

    This implementation is based on the free and open-source software
    SleepyHead, developed and copyright by Mark Watkins (C) 2011-2018.
    """

    def _is_prisma_line(self, directory: Path) -> bool:
        return _prisma_line.can_handle(directory)

    def can_handle(self, directory: Path) -> bool:
        """Return ``True`` if *directory* is a recognised Löwenstein data directory.

        Accepts both the Weinmann legacy format (``WM_DATA.TDF``) and the
        newer Prisma Line format (``config.pcfg``).

        Args:
            directory: Absolute path to the root of the data directory to inspect.

        Returns:
            ``True`` when a recognised format fingerprint is found.
        """
        if self._is_prisma_line(directory):
            return True
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
        """Parse a Löwenstein data directory and return a normalised result.

        Dispatches to the appropriate parser based on the directory fingerprint:

        * ``config.pcfg`` present → Prisma Line XML parser (Python)
        * ``WM_DATA.TDF`` present → Weinmann legacy binary parser (Rust)

        Args:
            directory: Absolute path to the SD card or data folder root.
            include_timeseries: Accepted for interface compatibility; neither
                format currently exposes per-breath waveforms so this flag
                has no effect.

        Returns:
            A :class:`~open_cpap_parser.schema.CPAPDirectory` populated with
            machine info, daily summaries, and session metadata.

        Raises:
            ImportError: If the Rust extension is needed but not installed.
            ValueError: If the directory cannot be parsed.
        """
        if self._is_prisma_line(directory):
            return _prisma_line.parse_prisma_line(directory)

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
