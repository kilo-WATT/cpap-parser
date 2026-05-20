# OSCAR Validation Suite

Cross-validates `open-cpap-parser` output against [OSCAR](https://sleepfiles.com/OSCAR)
(Open Source CPAP Analysis Reporter), the reference CPAP analysis application.

---

## How it works

For each sample (one SD card directory, one device):

1. `open-cpap-parser` parses the SD card dump → per-day `CPAPDirectory`.
2. An OSCAR CSV export (exported manually from OSCAR) provides reference values.
3. Per-day deltas are computed for AHI, pressure, usage, and leak.
4. Days outside tolerance are flagged; a pass/fail verdict is produced.
5. Reports are written to `validation/reports/` in Markdown and JSON.

---

## Prerequisites

### 1. Install OSCAR

**System package (Fedora/RHEL via COPR):**
```bash
sudo dnf copr enable johanh/oscar
sudo dnf install oscar
```

**Flatpak:**
```bash
flatpak install flathub com.sleepfiles.OSCAR
```

OSCAR is already installed on this machine at `/usr/bin/OSCAR`.

### 2. Import SD card data into OSCAR

Launch OSCAR (`/usr/bin/OSCAR`) and for each sample:

1. **File → Import CPAP Data → Choose/Specify**
2. Select the SD card root directory (e.g. `~/ZedProjects/sleepData/tmpdata/cam/`)
3. OSCAR detects the device format and imports all sessions

### 3. Export CSV summaries

After importing, for each device:

1. **File → Export → CSV Export Wizard**
2. Select all desired fields (Date, AHI, Pressure 95%, Leak 95%, Usage are required)
3. Save to `validation/oscar_exports/<sample_name>.csv`

| Sample | SD card path | CSV export filename |
|--------|-------------|---------------------|
| `resmed_cam` | `~/ZedProjects/sleepData/tmpdata/cam/` | `resmed_cam.csv` |
| `resmed_hanna` | `~/ZedProjects/sleepData/tmpdata/hanna/` | `resmed_hanna.csv` |
| `lowenstein_eyra` | `~/ZedProjects/sleepData/loweinstein-sample/ExampleFiles/` | `lowenstein_eyra.csv` |

### 4. Install validation extras

```bash
uv pip install -e ".[validate]"
```

---

## Running the suite

```bash
# All samples (skips any missing CSV exports)
pytest validation/ -m validation --run-validation -v

# Single sample
pytest validation/test_resmed_cam.py -m validation --run-validation -v

# Override SD card root
SLEEP_DATA_ROOT=/mnt/external pytest validation/ -m validation --run-validation -v
```

Tests that are missing either the SD card data or the OSCAR CSV export are
**automatically skipped** — they do not count as failures.

---

## Tolerance thresholds

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| AHI | ±0.1 events/hr | Rounding in per-night event counts |
| Pressure (95th %ile) | ±0.2 cmH₂O | Device pressure resolution is 0.2 cmH₂O |
| Usage | ±0.1 hr | Session boundary rounding |
| Leak (95th %ile) | ±5 L/min | OSCAR applies a leak estimation algorithm |

To use custom tolerances in a test, pass `tolerances=Tolerances(...)` to
`validate_sample()`.

---

## Report output

Each run writes two files to `validation/reports/`:

| File | Format | Consumer |
|------|--------|----------|
| `<sample>-<date>.md` | Markdown | Humans, code review |
| `<sample>-<date>.json` | JSON | CI systems, agents |

The JSON format includes per-day deltas and a top-level `"status": "PASS"/"FAIL"` field.

---

## Data sample acknowledgements

- **Löwenstein Eyra sample**: provided by [@drew2323](https://github.com/drew2323).
  This sample enabled validation of the Prisma Line format parser.

- **ResMed AirSense 11 samples** (`cam`, `hanna`): 72 and 45 nights of real
  therapy data used for EDF parser validation.

> **Security note:** SD card data is never committed to this repository.
> All sample paths in `conftest.py` are local filesystem paths only.

---

## Directory layout

```
validation/
├── conftest.py                  # pytest setup: --run-validation flag, fixtures
├── test_resmed_cam.py           # ResMed cam validation test
├── test_resmed_hanna.py         # ResMed hanna validation test
├── test_lowenstein_eyra.py      # Löwenstein Eyra validation test
├── oscar_exports/               # OSCAR CSV exports (not committed)
│   ├── resmed_cam.csv
│   ├── resmed_hanna.csv
│   └── lowenstein_eyra.csv
└── reports/                     # Generated reports (not committed)
    ├── resmed_cam-2026-05-20.md
    ├── resmed_cam-2026-05-20.json
    └── ...
```
