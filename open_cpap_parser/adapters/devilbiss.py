"""DeVilbiss / IntelliPAP CPAP data adapter.

This adapter calls into the compiled Rust extension module
(``open_cpap_parser._rust_parsers``) which ports the binary-format
parsing logic from the OSCAR / SleepyHead C++ implementation.

The Rust module handles:
  - DV6 format: SET.BIN, VER.BIN, S.BIN, U.BIN, L.BIN, R.BIN, E.BIN
  - DV5 format: SET1 and U file in the SL/ directory

Fingerprint: presence of ``DV6/SET.BIN`` or ``SL/SET1`` in the root.
"""

import logging
from pathlib import Path
from typing import Optional

from open_cpap_parser.adapters.base import BaseManufacturerAdapter, UnsupportedDirectoryError
from open_cpap_parser.schema import (
    CPAPDirectory,
    CPAPEvent,
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
    logger.warning("Rust extension module not available; DeVilbiss adapter disabled")


class DeVilbissAdapter(BaseManufacturerAdapter):
    """Adapter for DeVilbiss IntelliPAP DV54, DV64, and DV6x devices.

    Fingerprints a data directory by the presence of ``DV6/SET.BIN``
    (DV64) or ``SL/SET1`` (DV54).  Delegates binary parsing to the
    Rust extension.
    """

    def can_handle(self, directory: Path) -> bool:
        """Return ``True`` if *directory* contains a DeVilbiss data layout.

        Fingerprints by the presence of ``DV6/SET.BIN`` (DV64 format) or
        ``SL/SET1`` (DV54 format) in the directory root.

        Args:
            directory: Absolute path to the root of the data directory to inspect.

        Returns:
            ``True`` when the DeVilbiss fingerprint is detected; ``False``
            otherwise or if the Rust extension is unavailable.
        """
        if not HAS_RUST:
            return False
        try:
            return _rust_parsers.can_handle(str(directory))
        except Exception:
            return False

    def extract_and_map(
        self,
        directory: Path,
        include_timeseries: bool = False,
    ) -> CPAPDirectory:
        """Parse a DeVilbiss data directory and return a normalised result.

        Reads DV6 (``SET.BIN``, ``VER.BIN``, ``S.BIN``, ``U.BIN``) or DV5
        (``SL/SET1``, ``SL/U``) files via the compiled Rust extension.

        Args:
            directory: Absolute path to the SD card or data folder root.
            include_timeseries: Accepted for interface compatibility; the
                DeVilbiss binary format does not expose per-breath waveforms
                so this flag has no effect.

        Returns:
            A :class:`~open_cpap_parser.schema.CPAPDirectory` populated with
            machine info, daily summaries, and session metadata.

        Raises:
            ImportError: If the compiled Rust extension is not installed.
            ValueError: If the Rust parser encounters a malformed data file.
        """
        if not HAS_RUST:
            raise ImportError(
                "The DeVilbiss adapter requires the compiled Rust extension.\n"
                "  pip install maturin && maturin develop"
            )

        raw = _rust_parsers.parse_devilbiss(str(directory))

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
