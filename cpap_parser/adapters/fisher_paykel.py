"""Fisher & Paykel SleepStyle CPAP data adapter.

This adapter calls into the compiled Rust extension module
(``cpap_parser._rust_parsers``) which ports the binary-format
parsing logic from the OSCAR ``sleepstyle_loader.cpp`` C++ implementation,
itself derived from SleepyHead by Mark Watkins (C) 2011-2018, and copyright
(c) 2020-2025 The Oscar Team.

The Rust module handles:
  - ``FPHCARE/ICON/<serial>/SUM*.fph``: 512-byte text header followed by
    40-byte binary session records encoding start timestamp, usage duration,
    min/max/95th-percentile pressures, and therapy mode.

Supported devices: F&P SleepStyle series (CPAP and Auto modes).

Fingerprint: root contains ``FPHCARE/ICON/`` with at least one serial-number
subdirectory holding a ``SUM*.fph`` file whose fifth CR-terminated text line
is ``SLEEPSTYLE``.

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
        "Rust extension module not available; Fisher & Paykel SleepStyle adapter disabled"
    )


class FisherPaykelAdapter(BaseManufacturerAdapter):
    """Adapter for Fisher & Paykel SleepStyle CPAP and Auto devices.

    Fingerprints a data directory by the presence of ``FPHCARE/ICON/``
    containing a serial-number subdirectory with ``SUM*.fph`` files whose
    text header identifies the device as ``SLEEPSTYLE``.  Delegates all
    binary parsing to the compiled Rust extension
    (``_rust_parsers.parse_fisher_paykel``), which is ported from OSCAR's
    ``sleepstyle_loader.cpp``.

    Only per-session summary data is available from ``.fph`` files; no AHI
    or event-count data is present in this format.

    This implementation is based on the free and open-source software
    SleepyHead, developed and copyright by Mark Watkins (C) 2011-2018.

    Validation status: see :doc:`/device_support`.
    """

    profile_key = "fisher_paykel"

    def can_handle(self, directory: Path) -> bool:
        """Return ``True`` if *directory* contains Fisher & Paykel SleepStyle data.

        Checks for the ``FPHCARE/ICON/`` directory structure with at least one
        qualifying ``SUM*.fph`` file.

        Args:
            directory: Absolute path to the root of the data directory to inspect.

        Returns:
            ``True`` when the F&P SleepStyle fingerprint is detected;
            ``False`` otherwise or if the Rust extension is unavailable.
        """
        if not HAS_RUST:
            return False
        try:
            return _rust_parsers.can_handle_fisher_paykel(str(directory))
        except Exception:
            return False

    def extract_and_map(
        self,
        directory: Path,
        include_timeseries: bool = False,
    ) -> CPAPDirectory:
        """Parse a Fisher & Paykel SleepStyle data directory and return a normalised result.

        Reads ``FPHCARE/ICON/<serial>/SUM*.fph`` files for machine identity
        and per-session therapy summary data.  Event-level data (AHI, apnea
        counts) is not available in this format and will be zero-filled.

        Args:
            directory: Absolute path to the SD card or data folder root.
            include_timeseries: Accepted for interface compatibility; no
                high-resolution time-series data is available in ``.fph`` files.

        Returns:
            A :class:`~cpap_parser.schema.CPAPDirectory` populated with
            machine info, daily summaries, and session metadata.

        Raises:
            ImportError: If the compiled Rust extension is not installed.
            ValueError: If the Rust parser encounters a malformed data file.
        """
        if not HAS_RUST:
            raise ImportError(
                "The Fisher & Paykel SleepStyle adapter requires the compiled Rust extension.\n"
                "  pip install maturin && maturin develop"
            )

        raw = _rust_parsers.parse_fisher_paykel(str(directory))

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
