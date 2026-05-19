from abc import ABC, abstractmethod
from pathlib import Path

from open_cpap_parser.schema import CPAPDirectory


class UnsupportedDirectoryError(ValueError):
    def __init__(self, directory: Path, message: str = ""):
        self.directory = directory
        self.message = message or f"No adapter can handle directory: {directory}"
        super().__init__(self.message)


class BaseManufacturerAdapter(ABC):
    @abstractmethod
    def can_handle(self, directory: Path) -> bool:
        ...

    @abstractmethod
    def extract_and_map(
        self, directory: Path, include_timeseries: bool = False
    ) -> CPAPDirectory:
        ...
