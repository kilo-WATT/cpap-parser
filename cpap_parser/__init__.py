"""open-cpap-parser: multi-manufacturer CPAP data parsing library.

Exposes the core data models and the universal parser for use by
downstream libraries and CLI tools.
"""

from cpap_parser.adapters.sleeplab_output import map_directory_to_sleeplab
from cpap_parser.core import UniversalCPAPParser
from cpap_parser.schema import CPAPDirectory, CPAPSession, CPAPSessionSummary, CPAPEvent, TimeSeriesData, MachineInfo

__all__ = [
    "UniversalCPAPParser",
    "CPAPDirectory",
    "CPAPSession",
    "CPAPSessionSummary",
    "CPAPEvent",
    "TimeSeriesData",
    "MachineInfo",
    "map_directory_to_sleeplab",
]
