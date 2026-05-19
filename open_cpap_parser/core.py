"""Orchestrator factory for the unified CPAP parser.

Discovers the correct manufacturer-specific adapter for a given data
directory, dispatches parsing, and returns a normalised ``CPAPDirectory``.
"""

from pathlib import Path

from open_cpap_parser.adapters.apex import ApexAdapter
from open_cpap_parser.adapters.base import BaseManufacturerAdapter, UnsupportedDirectoryError
from open_cpap_parser.adapters.bmc import BMCAdapter
from open_cpap_parser.adapters.devilbiss import DeVilbissAdapter
from open_cpap_parser.adapters.lowenstein import LowensteinAdapter
from open_cpap_parser.adapters.resmed import ResMedAdapter
from open_cpap_parser.adapters.respironics import RespironicsAdapter
from open_cpap_parser.schema import CPAPDirectory


class UniversalCPAPParser:
    """Aggregate parser that tries registered adapters in priority order.

    Usage::

        parser = create_parser()
        result = parser.parse("/path/to/sd_card")
    """

    def __init__(self) -> None:
        self._adapters: list[BaseManufacturerAdapter] = []

    def register(self, adapter: BaseManufacturerAdapter) -> None:
        """Register a manufacturer adapter.

        Args:
            adapter: An instance of ``BaseManufacturerAdapter``.
        """
        self._adapters.append(adapter)

    def parse(
        self,
        directory: str | Path,
        include_timeseries: bool = False,
        waveform_only: bool = False,
    ) -> CPAPDirectory:
        """Parse a CPAP data directory using the first matching adapter.

        Args:
            directory: Path to the SD card or data folder root.
            include_timeseries: If True, decode high-resolution signal
                data where available.
            waveform_only: If True, filter ``daily_summaries`` to only
                include dates that have session-level waveform data.

        Returns:
            A ``CPAPDirectory`` containing all extracted data.

        Raises:
            NotADirectoryError: If *directory* does not exist.
            UnsupportedDirectoryError: If no registered adapter can
                handle the directory layout.
        """
        path = Path(directory).expanduser().resolve()
        if not path.is_dir():
            raise NotADirectoryError(f"Not a valid directory: {path}")

        for adapter in self._adapters:
            if adapter.can_handle(path):
                result = adapter.extract_and_map(path, include_timeseries=include_timeseries)
                if waveform_only and result.sessions:
                    session_dates = {s.start_time.date() for s in result.sessions}
                    result.daily_summaries = [
                        s for s in result.daily_summaries if s.date in session_dates
                    ]
                return result

        raise UnsupportedDirectoryError(path)


def create_parser() -> UniversalCPAPParser:
    """Create a fully-configured parser with all known adapters.

    Adapter priority (most-specific match first):

    # 1. ResMed (``DATALOG/`` directory)
    # 2. Philips Respironics (``.edf`` files in known locations)
    # 3. DeVilbiss / IntelliPAP (``DV6/SET.BIN`` or ``SL/SET1``)
    # 4. Apex Medical (``APDATA/*.APC`` files)
    # 5. Lowenstein / Weinmann (``WM_DATA.TDF``)
    # 6. BMC / 3B Medical (``.USR`` + ``.idx`` + ``.000``)
    # 7. Fisher & Paykel (``.FPH`` files) — disabled; see fisher_paykel.py
    # 8. Yuwell / DJMed (proprietary markers) — disabled; see yuwell.py

    Returns:
        A ``UniversalCPAPParser`` instance.
    """
    parser = UniversalCPAPParser()
    parser.register(ResMedAdapter())
    parser.register(RespironicsAdapter())
    parser.register(DeVilbissAdapter())
    parser.register(ApexAdapter())
    parser.register(LowensteinAdapter())
    parser.register(BMCAdapter())
    return parser
