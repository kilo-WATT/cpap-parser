"""Base classes and exceptions for manufacturer-specific CPAP adapters."""

from abc import ABC, abstractmethod
from pathlib import Path

from cpap_parser.schema import CPAPDirectory


class UnsupportedDirectoryError(ValueError):
    """Raised when no registered adapter can handle a given directory.

    Attributes:
        directory: The path that could not be matched.
        message: Human-readable explanation.
    """

    def __init__(self, directory: Path, message: str = "") -> None:
        self.directory = directory
        self.message = message or f"No adapter can handle directory: {directory}"
        super().__init__(self.message)


class BaseManufacturerAdapter(ABC):
    """Abstract base for all manufacturer-specific CPAP parsers.

    Subclasses must implement ``can_handle`` for directory fingerprinting
    and ``extract_and_map`` for the actual data extraction.

    Each concrete adapter declares a ``profile_key`` that maps to an entry
    in :mod:`cpap_parser.device_profiles`.  Override
    ``get_profile_key()`` when a single adapter handles multiple device
    sub-types with different validation statuses (e.g. Löwenstein).
    """

    #: Key into ``cpap_parser.device_profiles.PROFILES``.
    profile_key: str = "unknown"

    def get_profile_key(self, directory: Path) -> str:  # noqa: ARG002
        """Return the profile key for *directory*.

        The default implementation returns ``self.profile_key``.
        Override when a single adapter covers multiple device sub-types
        with different validation statuses.
        """
        return self.profile_key

    @abstractmethod
    def can_handle(self, directory: Path) -> bool:
        """Return True if *directory* contains data from this manufacturer.

        Args:
            directory: Root path of the SD card or data folder.

        Returns:
            True when the directory fingerprint matches this adapter.
        """
        ...

    @abstractmethod
    def extract_and_map(
        self, directory: Path, include_timeseries: bool = False
    ) -> CPAPDirectory:
        """Parse *directory* and return a normalised ``CPAPDirectory``.

        Args:
            directory: Root path of the SD card or data folder.
            include_timeseries: If True, decode high-resolution signal
                data where available.

        Returns:
            A ``CPAPDirectory`` containing all extracted data.
        """
        ...
