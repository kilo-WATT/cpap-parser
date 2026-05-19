from pathlib import Path

from open_cpap_parser.adapters.base import BaseManufacturerAdapter, UnsupportedDirectoryError
from open_cpap_parser.adapters.resmed import ResMedAdapter
from open_cpap_parser.schema import CPAPDirectory


class UniversalCPAPParser:
    def __init__(self) -> None:
        self._adapters: list[BaseManufacturerAdapter] = []

    def register(self, adapter: BaseManufacturerAdapter) -> None:
        self._adapters.append(adapter)

    def parse(
        self,
        directory: str | Path,
        include_timeseries: bool = False,
        waveform_only: bool = False,
    ) -> CPAPDirectory:
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
    parser = UniversalCPAPParser()
    parser.register(ResMedAdapter())
    return parser
