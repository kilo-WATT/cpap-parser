# Project: cpap-parser

## Phase 1 Architecture

### Three-tier output model
```
CPAPDirectory
├── machine: MachineInfo           # from Identification.json
├── daily_summaries: list[CPAPSessionSummary]  # from STR.edf
└── sessions: list[CPAPSession]    # from DATALOG/*.edf
```

### Adapter pattern
- `BaseManufacturerAdapter` ABC in `adapters/base.py`
- `ResMedAdapter` in `adapters/resmed.py` — fingerprints via `DATALOG/` directory
- Register new adapters in `core.create_parser()`
- `UniversalCPAPParser` dispatches to first matching adapter

### Known behaviors
- AirSense 11 stores events as AHI aggregates in STR.edf, NOT as individual EDF annotations in EVE files
- EVE file `EDF Annotations` signals are empty in tested data
- 21 EDF files with `num_data_records=0` exist in the test dataset — these are safely skipped
- Signal labels use `Label.XXms` format (e.g., `Flow.40ms`) — matched by prefix, not exact string

### Event parsing (future work)
Current state: no per-event data in AirSense 11 EDF files.
If needed, explore:
- STR.edf stores daily event counts (ahi, ai, hi, cai, oai)
- SleepHQ API returns per-event data (via their proprietary format)
- OSCAR project's format documentation for AirSense 11

### CLI usage
```bash
cpap-parser --input /path/to/sd_card
cpap-parser --input /path/to/sd_card --include-timeseries
cpap-parser --input /path/to/sd_card --include-timeseries --waveform-only
```

### Testing
- Tests run against a real AirSense 11 dataset (not committed — local only)
- STR.edf parsing produces 439 daily records
- DATALOG parsing produces sessions for all non-zero-record EDF files
- Time-series data is verified on BRP (breathing) sessions

### Commits
- All commits must be [Conventional Commits](https://www.conventionalcommits.org/)
- Types: feat, fix, docs, refactor, test, chore, ci, style
- Examples: `fix(ci): make tests portable for CI`, `feat(sleeplab): add output mapper module`

### Development
- Python 3.11+, uv package manager
- Dependencies: cpap-py>=1.0.0, pydantic>=2.0.0
- No secrets, keys, or real patient paths in version control
