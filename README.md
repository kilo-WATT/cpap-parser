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

### Implemented

| Manufacturer | Devices | Adapter | Backend |
|---|---|---|---|
| ResMed | S9, AirSense 10/11, AirCurve | `ResMedAdapter` | `cpap-py` (Python) |
| Philips Respironics | EDF exports from any model | `RespironicsAdapter` | `pyedflib` (Python) |
| DeVilbiss / IntelliPAP | DV5, DV6 series | `DeVilbissAdapter` | Rust (OSCAR port) |
| Apex Medical | any with `APDATA/*.APC` | `ApexAdapter` | Rust (OSCAR port) |
| Lowenstein / Weinmann | WM-series | `LowensteinAdapter` | Rust (OSCAR port) |
| BMC / 3B Medical | GII, iBreeze | `BMCAdapter` | Rust (OSCAR port) |
| Fisher & Paykel | SleepStyle (CPAP + Auto) | `FisherPaykelAdapter` | Rust (OSCAR port) |
| Yuwell / DJMed | BreathCare YH-550/580/680/690/830 | `YuwellAdapter` | Rust (OSCAR port) |

### Not Yet Implemented

| OSCAR Loader | Device / Format | Notes |
|---|---|---|
| `prs1_loader.cpp` | Philips Respironics System One / DreamStation (native binary) | Highest priority — most Respironics users have native `.001`/`.002` session files, not EDF exports. ~5,000 lines in OSCAR. |
| `icon_loader.cpp` | Fisher & Paykel ICON (older generation) | Different binary format from SleepStyle; separate OSCAR loader. |
| `resmed_edi_loader.cpp` | ResMed EDI format (older devices) | Older ResMed variant predating the current SD card layout. |
| `compumedics_loader.cpp` | Compumedics | Sleep-lab device; niche use case outside home CPAP. |

## Architecture

```
open_cpap_parser/
├── __init__.py     # Package exports
├── cli.py          # CLI entry point (argparse)
├── core.py         # UniversalCPAPParser — orchestrator with adapter dispatch
├── schema.py       # Pydantic models (CPAPDirectory, CPAPSession, etc.)
├── adapters/
│   ├── __init__.py
│   ├── base.py           # BaseManufacturerAdapter ABC
│   ├── resmed.py         # ResMed adapter
│   ├── respironics.py    # Philips Respironics adapter
│   ├── lowenstein.py     # Lowenstein / Weinmann adapter (disabled)
│   ├── fisher_paykel.py  # Fisher & Paykel adapter
│   ├── yuwell.py         # Yuwell / DJMed adapter
│   └── sleeplab_output.py  # sleeplab DB format mapper
└── tests/
    ├── test_resmed_adapter.py
    └── test_sleeplab_output.py
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

This project is based on the free and open-source software **SleepyHead**,
developed and copyright by Mark Watkins (Jedimark) (C) 2011-2018.

The binary-format parsing logic in the Rust extension module is ported
from **[OSCAR](https://gitlab.com/CrimsonNape/OSCAR-code)** (Open Source
CPAP Analysis Reporter), which is itself a derivative of SleepyHead.

Per Mark Watkins' redistribution request, any derivative of this work
must mention clearly in its advertising material, software installer, and
about screens that it **"is based on the free and open-source software
SleepyHead, developed and copyright by Mark Watkins (C) 2011-2018."**
Referencing "GPL software" alone is not sufficient. See [NOTICE.md](NOTICE.md)
for the full redistribution notice and third-party copyright statements.

The project also builds on several open-source libraries.  Each adapter
imports its dependency lazily — no library is required at install time
unless you need that specific manufacturer's support.

| Library | Used By | License | Compatible with GPL-3.0 |
|---|---|---|---|
| **[cpap-py](https://github.com/dynacylabs/cpap-py)** | ResMedAdapter | MIT | ✅ Yes |
| **[pyedflib](https://github.com/holgern/pyedflib)** | RespironicsAdapter | BSD-2-Clause | ✅ Yes |
| **[pydantic](https://github.com/pydantic/pydantic)** | All schema models | MIT | ✅ Yes |

DeVilbiss, Apex, Lowenstein, BMC, Fisher & Paykel, and Yuwell adapters use the
compiled Rust extension (``_rust_parsers``) with no third-party runtime dependencies.

## License

GNU General Public License v3.0 (GPL-3.0)

This project is based on the free and open-source software **SleepyHead**,
developed and copyright by Mark Watkins (Jedimark) (C) 2011-2018.
The Rust extension module's binary parsing logic is ported from
[OSCAR](https://gitlab.com/CrimsonNape/OSCAR-code), itself a SleepyHead
derivative.  Both SleepyHead and OSCAR are distributed under the GPL-3.0,
which this project inherits.  All other components are permissively
licensed (MIT, BSD-2-Clause) and are compatible with GPL-3.0.
