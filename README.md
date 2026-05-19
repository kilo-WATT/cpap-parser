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
|---|---|---|---|
| **[cpap-py](https://github.com/dynacylabs/cpap-py)** | ResMedAdapter | MIT | ✅ Yes |
| **[pyedflib](https://github.com/holgern/pyedflib)** | RespironicsAdapter | BSD-2-Clause | ✅ Yes |
| **[pydantic](https://github.com/pydantic/pydantic)** | All schema models | MIT | ✅ Yes |
| *cpap-analyst-mcp* | LowensteinAdapter | GPL-3.0 | ❌ No (Requires Lazy Import) |
| *fph-parser* | FisherPaykelAdapter | All Rights Reserved | ⚠️ Do Not Use |
| *djmed* | YuwellAdapter | All Rights Reserved | ⚠️ Do Not Use |

## Disclaimer

**Lowenstein (cpap-analyst-mcp).** `cpap-analyst-mcp` is licensed
under GPL-3.0 as a derivative of OSCAR.  This project does **not**
distribute, bundle, or require it.  However, if you install this
library and use the LowensteinAdapter, your local execution
environment must comply with the GPL-3.0 terms — including the
requirement to provide corresponding source code if you distribute
the combined work.  See the [GPL-3.0
FAQ](https://www.gnu.org/licenses/gpl-faq.html) for details.

**Fisher & Paykel (fph-parser) and Yuwell (djmed).** These
repositories publish source code on GitHub without an explicit
license file, which under international copyright law defaults to
All Rights Reserved.  Using them as dependencies is not permitted.
Do not install or use these libraries.  The adapters exist for
reference and educational purposes only; they are disabled by
default and will raise ``ImportError`` with a clear message.

All three restricted libraries are imported **lazily**
(``try/except ImportError``) inside adapter methods — they are never
listed in ``pyproject.toml``, bundled in the wheel, or imported at
module scope.  The legal responsibility for combining the MIT
adapter code with a GPL-3.0 or unlicensed library falls entirely on
the end-user.

## License

MIT
