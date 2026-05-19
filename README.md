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

| Manufacturer | Status | Adapter | Dependency |
|---|---|---|---|---|
| ResMed (S9, AirSense 10/11, AirCurve) | ✅ | ResMedAdapter | `cpap-py` |
| Philips Respironics | ✅ | RespironicsAdapter | `pyedflib` |
| Lowenstein / Weinmann | ✅ | LowensteinAdapter | `cpap-analyst-mcp` |
| Fisher & Paykel | ✅ | FisherPaykelAdapter | `fph-parser` |
| Yuwell / DJMed | ✅ | YuwellAdapter | `djmed` |

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
│   ├── lowenstein.py     # Lowenstein / Weinmann adapter
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

This project builds on several open-source libraries.  Each adapter
imports its dependency lazily — no library is required at install time
unless you need that specific manufacturer's support.

| Library | Used By | License | Compatible with MIT |
|---|---|---|---|---|
| **[cpap-py](https://github.com/dynacylabs/cpap-py)** | ResMedAdapter | MIT | ✅ Yes |
| **[pyedflib](https://github.com/holgern/pyedflib)** | RespironicsAdapter | BSD-2-Clause | ✅ Yes |
| **[pydantic](https://github.com/pydantic/pydantic)** | All schema models | MIT | ✅ Yes |
| *cpap-analyst-mcp* | LowensteinAdapter | GPL-3.0 | ❌ No — copyleft |
| *fph-parser* ([jieter/fph-parser](https://github.com/jieter/fph-parser)) | FisherPaykelAdapter | All Rights Reserved | ❌ No |
| *djmed* ([Centurix/djmed](https://github.com/Centurix/djmed)) | YuwellAdapter | All Rights Reserved | ❌ No |

### License notes

- **cpap-analyst-mcp** derives from [OSCAR](https://www.sleepfiles.com/OSCAR/)
  (GPL-3.0), which it acknowledges in its documentation.  Translating
  OSCAR's C++ byte-offset logic into Python is a derivative work, so the
  GPL-3.0 copyleft applies.  This library is **incompatible** with MIT
  if distributed together.

- **fph-parser** and **djmed** publish source code to GitHub without an
  explicit license.  Under international copyright law this defaults to
  All Rights Reserved — viewing and forking is permitted by GitHub's ToS,
  but downloading, modifying, or using the code as a library dependency
  is not.

### How the project stays MIT-compatible

All three restricted libraries are imported **lazily** (`try/except
ImportError`) inside adapter methods — they are never listed in
`pyproject.toml`, bundled in the wheel, or imported at module scope.
This project does not distribute, statically link, or require them.
The act of combining the MIT adapter code with a GPL/unlicensed parser
shifts entirely to the end-user who installs the library on their own
machine.

To use an adapter that requires a restricted dependency you must
install it manually:

```bash
pip install open-cpap-parser
pip install cpap-analyst-mcp   # GPL-3.0 — check your compliance
```

## License

MIT
