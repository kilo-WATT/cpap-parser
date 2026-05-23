# cpap-parser

Multi-manufacturer CPAP data parsing library and CLI tool. Parses raw SD card directories from CPAP machines, automatically identifies the manufacturer, normalises the output into a strict JSON schema, and prints to stdout.

## Quick Start

```bash
pip install cpap-parser

# Parse a CPAP SD card
cpap-parser --input /path/to/sd_card

# Include high-resolution waveform data
cpap-parser --input /path/to/sd_card --include-timeseries

# Only daily summaries with waveform data
cpap-parser --input /path/to/sd_card --include-timeseries --waveform-only
```

## Supported Manufacturers

| Manufacturer | Adapter | Backend |
|---|---|---|
| ResMed S9, AirSense 10/11, AirCurve | `ResMedAdapter` | cpap-py |
| Philips Respironics (EDF exports) | `RespironicsAdapter` | pyedflib |
| DeVilbiss / IntelliPAP DV5/DV6 | `DeVilbissAdapter` | Rust (OSCAR port) |
| Apex Medical (APDATA/*.APC) | `ApexAdapter` | Rust (OSCAR port) |
| Lowenstein / Weinmann WM-series | `LowensteinAdapter` | Rust (OSCAR port) |
| BMC / 3B Medical GII, iBreeze | `BMCAdapter` | Rust (OSCAR port) |
| Fisher & Paykel SleepStyle | `FisherPaykelAdapter` | Rust (OSCAR port) |
| Yuwell / DJMed BreathCare YH-550/580/680/690/830 | `YuwellAdapter` | Rust (OSCAR port) |

## Output Schema

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

## License

GPL-3.0. The Rust extension module's binary parsing logic is ported from [OSCAR](https://gitlab.com/CrimsonNape/OSCAR-code), itself a derivative of SleepyHead by Mark Watkins (C) 2011-2018.
