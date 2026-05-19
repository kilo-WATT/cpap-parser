# open-cpap-parser

Multi-manufacturer CPAP data parsing library and CLI tool. Parses raw SD card directories from CPAP machines, automatically identifies the manufacturer, normalizes the output into a strict JSON schema, and outputs to stdout.

## Quick Start

```bash
pip install open-cpap-parser

# Parse a ResMed SD card
cpap-parser --input /path/to/sd_card

# Include high-resolution waveform data
cpap-parser --input /path/to/sd_card --include-timeseries

# Only daily summaries with waveform data
cpap-parser --input /path/to/sd_card --include-timeseries --waveform-only
```

## Output Schema

The parser outputs a three-tier JSON structure to stdout:

```json
{
  "machine": {
    "serial_number": "23233254908",
    "model": "AirSense11AutoSet",
    "series": "AirSense11"
  },
  "daily_summaries": [
    {
      "date": "2025-02-04",
      "ahi": 0.7,
      "leak_95": 0.0,
      "pressure_95": 11.2,
      "usage_hours": 7.5
    }
  ],
  "sessions": [
    {
      "start_time": "2025-02-04T22:00:00",
      "duration_minutes": 480.0,
      "file_type": "BRP",
      "events": [],
      "timeseries": null
    }
  ]
}
```

## Supported Manufacturers

| Manufacturer | Status | Adapter |
|---|---|---|
| ResMed (S9, AirSense 10/11, AirCurve) | ✅ | `cpap-py` |
| Philips Respironics | 🔲 Planned | |
| Lowenstein Medical | 🔲 Planned | |
| BMC | 🔲 Planned | |

## Architecture

```
open_cpap_parser/
├── cli.py          # CLI entry point (argparse)
├── core.py         # UniversalCPAPParser — orchestrator with adapter dispatch
├── schema.py       # Pydantic models (CPAPDirectory, CPAPSession, etc.)
├── adapters/
│   ├── base.py     # BaseManufacturerAdapter ABC
│   └── resmed.py   # ResMed adapter using cpap-py + custom EDF parsing
└── tests/
    └── test_resmed_adapter.py
```

### Adapter Pattern

To add a new manufacturer:

```python
from open_cpap_parser.adapters.base import BaseManufacturerAdapter

class PhilipsAdapter(BaseManufacturerAdapter):
    def can_handle(self, directory: Path) -> bool:
        return (directory / "PERSONDATA").is_dir()

    def extract_and_map(self, directory: Path, ...) -> CPAPDirectory:
        # ... parse and return normalized schema
        pass

# Register in core.py
parser.register(PhilipsAdapter())
```

## Development

```bash
git clone https://gitlab.com/open-cpap/open-cpap-parser.git
cd open-cpap-parser
uv sync
uv run pytest tests/ -v
```

## Acknowledgements

This project uses **[cpap-py](https://github.com/dynacylabs/cpap-py)** (MIT) for ResMed EDF parsing, device identification, and summary data extraction.

## License

MIT
