"""Base classes and exceptions for manufacturer-specific CPAP adapters."""

from abc import ABC, abstractmethod
from pathlib import Path

from open_cpap_parser.schema import CPAPDirectory


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
    """

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
