# Usage

## CLI

```bash
cpap-parser --input <path> [--include-timeseries] [--waveform-only]
```

| Flag | Description |
|---|---|
| `--input PATH` | Path to the SD card root directory (required) |
| `--include-timeseries` | Decode high-resolution signal waveforms where available |
| `--waveform-only` | Filter daily summaries to only dates with session waveform data |

Output is written as JSON to stdout. Errors go to stderr.

## Python API

```python
from cpap_parser.core import create_parser

parser = create_parser()
result = parser.parse("/path/to/sd_card")

print(result.machine.model)
for summary in result.daily_summaries:
    print(summary.date, summary.ahi, summary.usage_hours)
```

With timeseries:

```python
result = parser.parse("/path/to/sd_card", include_timeseries=True)
for session in result.sessions:
    if session.timeseries:
        print(session.timeseries.flow_rate[:10])
```

## Adding a Custom Adapter

```python
from pathlib import Path
from cpap_parser.adapters.base import BaseManufacturerAdapter
from cpap_parser.schema import CPAPDirectory
from cpap_parser.core import create_parser

class MyAdapter(BaseManufacturerAdapter):
    def can_handle(self, directory: Path) -> bool:
        return (directory / "MY_VENDOR_MARKER").exists()

    def extract_and_map(self, directory: Path, include_timeseries: bool = False) -> CPAPDirectory:
        # ... parse and return normalised schema
        pass

parser = create_parser()
parser.register(MyAdapter())
result = parser.parse("/path/to/sd_card")
```

Adapters are tried in registration order. The first `can_handle` returning `True` wins.

## Manufacturer-Specific Dependencies

| Adapter | Optional dependency | Install |
|---|---|---|
| `ResMedAdapter` | `cpap-py` | `pip install cpap-py` |
| `RespironicsAdapter` | `pyedflib` | `pip install open-cpap-parser[respironics]` |
| All Rust adapters | compiled `.so` | bundled in the wheel |
