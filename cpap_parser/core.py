"""Orchestrator factory for the unified CPAP parser.

Discovers the correct manufacturer-specific adapter for a given data
directory, dispatches parsing, and returns a normalised ``CPAPDirectory``.
"""

import logging
import warnings
from pathlib import Path

from cpap_parser.adapters.apex import ApexAdapter
from cpap_parser.adapters.base import BaseManufacturerAdapter, UnsupportedDirectoryError
from cpap_parser.adapters.bmc import BMCAdapter
from cpap_parser.adapters.devilbiss import DeVilbissAdapter
from cpap_parser.adapters.fisher_paykel import FisherPaykelAdapter
from cpap_parser.adapters.lowenstein import LowensteinAdapter
from cpap_parser.adapters.resmed import ResMedAdapter
from cpap_parser.adapters.respironics import RespironicsAdapter
from cpap_parser.adapters.yuwell import YuwellAdapter
from cpap_parser.device_profiles import get_profile
from cpap_parser.schema import CPAPDirectory

_log = logging.getLogger(__name__)

_CONTRIBUTE_URL = "https://gitlab.com/open-cpap/cpap-parser/-/issues"


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

                profile_key = adapter.get_profile_key(path)
                profile = get_profile(profile_key)
                result.machine.validation_status = profile["validation_status"]
                result.machine.validation_notes = profile["validation_notes"]

                if profile["validation_status"] in ("needs_validation", "unimplemented"):
                    warnings.warn(
                        f"Parser pipeline '{profile_key}' has validation status "
                        f"'{profile['validation_status']}'. Output may not match "
                        f"your device software. Consider contributing sample data: "
                        f"{_CONTRIBUTE_URL}",
                        UserWarning,
                        stacklevel=2,
                    )

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
    # 7. Fisher & Paykel SleepStyle (``FPHCARE/ICON/<serial>/SUM*.fph``)
    # 8. Yuwell / DJMed BreathCare (``YHSD-NEW.BYS`` or ``YH-*`` dirs)

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
    parser.register(FisherPaykelAdapter())
    parser.register(YuwellAdapter())
    return parser
