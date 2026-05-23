# Waveform Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the OSCAR cross-validation suite to cover waveform-derived metrics and event records, so waveform extraction accuracy can be measured before and after each parser improvement.

**Architecture:** The existing validation framework (`feat/oscar-validation`) compares daily summaries against OSCAR Summary CSVs. This plan adds two new comparison dimensions: (1) per-session waveform-derived stats (pressure/leak percentiles computed from `TimeSeriesData` arrays) compared against OSCAR Sessions CSVs, and (2) per-event records (our `CPAPEvent` list) compared against OSCAR Details CSVs. Each dimension gets its own reader, comparer, and pytest test file. The Löwenstein waveform validation is scaffolded now (skipped) so it passes automatically once extraction is implemented.

**Tech Stack:** Python 3.11+, pytest, numpy (already in venv), pydantic, existing validation framework on `feat/oscar-validation` branch, `glab` CLI for MRs.

---

## Context and Prerequisites

### Branch state at plan creation (2026-05-21)

| Branch | Contains | Status |
|--------|----------|--------|
| `feat/waveforms` | BRP+PLD session merging, dual-rate `TimeSeriesData` | Open MR, being merged |
| `feat/oscar-validation` | Summary CSV reader, daily compare, runner, conftest | Open MR |
| `main` | Neither set of changes yet |

### OSCAR export files available (in `~/ZedProjects/sleepData/validation/`)

| File pattern | Content |
|---|---|
| `OSCAR_*_Summary_*.csv` | Per-day aggregate stats — already validated |
| `OSCAR_*_Sessions_*.csv` | Per-session stats (same cols as Summary + Session ID column) |
| `OSCAR_*_Details_*.csv` | Per-event records: `DateTime, Session, Event, Data/Duration` |

### Relevant validation code (on `feat/oscar-validation`)

- `open_cpap_parser/validation/oscar_reader.py` — `OscarDaySummary`, `read_oscar_csv()`
- `open_cpap_parser/validation/compare.py` — `DayDiff`, `CompareResult`, `compare()`
- `open_cpap_parser/validation/runner.py` — `validate_sample()`
- `open_cpap_parser/validation/report.py` — Markdown/JSON report generation
- `validation/conftest.py` — `--run-validation` flag, `oscar_csv()` fixture, `sample_paths` fixture

### ResMed waveform signals (from `feat/waveforms`)

After BRP+PLD merging, a `CPAPSession` with `file_type="BRP+PLD"` has:

- `timeseries.timestamps` — 25 Hz timestamps (BRP)
- `timeseries.flow_rate` — 25 Hz flow (BRP `Flow.40ms`, gain=0.002 L/s, multiply by 60 for L/min)
- `timeseries.pressure` — 25 Hz pressure (BRP `Press.40ms`, cmH₂O)
- `timeseries.timestamps_low` — 0.5 Hz timestamps (PLD)
- `timeseries.mask_pressure` — 0.5 Hz mask pressure (PLD `MaskPress.2s`, cmH₂O)
- `timeseries.leak` — 0.5 Hz leak (PLD `Leak.2s`, L/min)
- `timeseries.respiratory_rate` — 0.5 Hz (PLD `RespRate.2s`, breaths/min)
- `timeseries.tidal_volume` — 0.5 Hz (PLD `TidVol.2s`, mL)

### OSCAR Sessions CSV column names

```
Date, Session, Start, End, Total Time, AHI, CA Count, A Count, OA Count, H Count,
UA Count, ..., Median Pressure, Median Pressure Set, Median IPAP, Median IPAP Set,
Median EPAP, Median EPAP Set, Median Flow Limit., 95% Pressure, ..., 95% EPAP, ...,
95% Flow Limit., ...
```

For ResMed APAP (AirSense 11):
- `Median Pressure` = 50th-percentile APAP target (≈ `Press.2s` at 0.5 Hz, or `Press.40ms` at 25 Hz)
- `95% EPAP` = 95th-percentile EPAP (≈ `MaskPress.2s`)
- `Median Flow Limit.` = median leak (≈ `Leak.2s`)
- `95% Flow Limit.` = 95th-percentile leak

### OSCAR Details CSV format

```
DateTime, Session, Event, Data/Duration
2026-05-16T03:26:54, 1778897460, ClearAirway, 14.00
2026-05-15T22:22:45, 1778897460, Obstructive, 14.00
```

- `Session` is OSCAR's session ID (Unix timestamp of session start).
- `Event` strings: `Obstructive`, `ClearAirway`, `Hypopnea`, `RERA`, `FlowLimit`, `Snore`, `Pressure`.
- `Data/Duration` is duration in seconds for apnea/hypopnea events.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `open_cpap_parser/validation/oscar_reader.py` | Modify | Add `OscarSession`, `OscarEvent`, `read_sessions_csv()`, `read_details_csv()` |
| `open_cpap_parser/validation/waveform_compare.py` | Create | Compute waveform-derived stats; compare against `OscarSession` |
| `open_cpap_parser/validation/event_compare.py` | Create | Match/compare `CPAPEvent` lists against `OscarEvent` lists |
| `validation/conftest.py` | Modify | Add `oscar_sessions_csv()` and `oscar_details_csv()` fixtures; auto-export Sessions+Details CSVs |
| `validation/test_resmed_waveform_validation.py` | Create | pytest tests for ResMed waveform stats + event validation |
| `validation/test_lowenstein_waveform_validation.py` | Create | Placeholder tests (skipped until waveform extraction done) |

---

## Task 1: Set up the working branch

This plan requires both the waveform extraction changes (`feat/waveforms`) and the validation framework (`feat/oscar-validation`) to be on the same branch.

**Prerequisites:** Both `feat/waveforms` and `feat/oscar-validation` merged into `main` (via their respective MRs).

- [ ] **Step 1: Create and check out the new branch**

```bash
git checkout main && git pull origin main
git checkout -b feat/waveform-validation
```

Expected: you are on `feat/waveform-validation`, which has both waveform extraction and the validation framework.

- [ ] **Step 2: Verify the validation framework is present**

```bash
ls open_cpap_parser/validation/
# expected: __init__.py  compare.py  oscar_reader.py  report.py  runner.py

ls validation/
# expected: conftest.py  .gitignore  (no OSCAR CSV files)
```

- [ ] **Step 3: Verify waveform schema is present**

```bash
python -c "from open_cpap_parser.schema import TimeSeriesData; ts = TimeSeriesData(); print(ts.timestamps_low, ts.mask_pressure)"
# expected: [] []
```

---

## Task 2: Add OSCAR Sessions and Details CSV readers

Extend `open_cpap_parser/validation/oscar_reader.py` to read per-session and per-event OSCAR exports.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_oscar_reader_waveform.py`:

```python
import csv
import tempfile
from pathlib import Path

import pytest

from open_cpap_parser.validation.oscar_reader import (
    OscarSession,
    OscarEvent,
    read_sessions_csv,
    read_details_csv,
)


SESSION_HEADER = (
    "Date,Session,Start,End,Total Time,AHI,CA Count,A Count,OA Count,H Count,"
    "UA Count,VS Count,VS2 Count,RE Count,FL Count,SA Count,NR Count,EP Count,"
    "LF Count,UF1 Count,UF2 Count,PP Count,Median Pressure,Median Pressure Set,"
    "Median IPAP,Median IPAP Set,Median EPAP,Median EPAP Set,Median Flow Limit.,"
    "95% Pressure,95% Pressure Set,95% IPAP,95% IPAP Set,95% EPAP,95% EPAP Set,"
    "95% Flow Limit.,99.5% Pressure,99.5% Pressure Set,99.5% IPAP,99.5% IPAP Set,"
    "99.5% EPAP,99.5% EPAP Set,99.5% Flow Limit."
)
SESSION_ROW = (
    "2026-05-15,1778897460,2026-05-15T22:11:06,2026-05-16T03:49:06,05:38:00,"
    "0.533,1,0,2,0,0,0,0,0,0,0,0,0,0,0,0,0,6.72,0,0,0,4.2,0,0.01,"
    "7.42,0,0,0,4.58,0,0.24,7.62,0,0,0,4.66,0,0.28"
)

DETAILS_HEADER = "DateTime,Session,Event,Data/Duration"
DETAILS_ROWS = [
    "2026-05-16T03:26:54,1778897460,ClearAirway,14.00",
    "2026-05-15T22:22:45,1778897460,Obstructive,14.00",
    "2026-05-15T22:11:16,1778897460,Pressure,4.00",
]


@pytest.fixture
def sessions_csv(tmp_path: Path) -> Path:
    p = tmp_path / "sessions.csv"
    p.write_text(SESSION_HEADER + "\n" + SESSION_ROW + "\n")
    return p


@pytest.fixture
def details_csv(tmp_path: Path) -> Path:
    p = tmp_path / "details.csv"
    p.write_text(DETAILS_HEADER + "\n" + "\n".join(DETAILS_ROWS) + "\n")
    return p


def test_read_sessions_returns_one_session(sessions_csv):
    sessions = read_sessions_csv(sessions_csv)
    assert len(sessions) == 1


def test_session_fields(sessions_csv):
    session = read_sessions_csv(sessions_csv)[0]
    assert session.session_id == "1778897460"
    assert session.start == "2026-05-15T22:11:06"
    assert session.end == "2026-05-16T03:49:06"
    assert abs(session.ahi - 0.533) < 0.001
    assert abs(session.pressure_50 - 6.72) < 0.01
    assert abs(session.pressure_95 - 7.42) < 0.01
    assert abs(session.epap_50 - 4.2) < 0.01
    assert abs(session.epap_95 - 4.58) < 0.01
    assert abs(session.leak_50 - 0.01) < 0.001
    assert abs(session.leak_95 - 0.24) < 0.001


def test_read_details_filters_non_events(details_csv):
    # "Pressure" rows are device settings changes, not therapy events
    events = read_details_csv(details_csv)
    event_types = {e.event_type for e in events}
    assert "Pressure" not in event_types


def test_read_details_returns_therapy_events(details_csv):
    events = read_details_csv(details_csv)
    assert len(events) == 2
    types = {e.event_type for e in events}
    assert "ClearAirway" in types
    assert "Obstructive" in types


def test_event_fields(details_csv):
    events = [e for e in read_details_csv(details_csv) if e.event_type == "Obstructive"]
    assert len(events) == 1
    e = events[0]
    assert e.session_id == "1778897460"
    assert e.datetime_str == "2026-05-15T22:22:45"
    assert abs(e.duration_sec - 14.0) < 0.01
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
uv run pytest tests/test_oscar_reader_waveform.py -v
```

Expected: `ImportError` — `OscarSession`, `OscarEvent`, `read_sessions_csv`, `read_details_csv` not found.

- [ ] **Step 3: Implement in `open_cpap_parser/validation/oscar_reader.py`**

Append after the existing `OscarDaySummary` class and `read_oscar_csv` function:

```python
# ── Non-event types in Details CSV ────────────────────────────────────────────
# These rows represent device state changes, not therapy events.
_DETAILS_NON_EVENTS = {"Pressure", "CPAP", "Flow Limit", "Leak"}


@dataclass(frozen=True)
class OscarSession:
    """Per-session stats from an OSCAR Sessions CSV export.

    Attributes:
        session_id: OSCAR session ID (Unix timestamp string).
        start: ISO datetime string of session start.
        end: ISO datetime string of session end.
        ahi: Apnea-Hypopnea Index (events/hour).
        pressure_50: Median target pressure (cmH2O).
        pressure_95: 95th-percentile target pressure (cmH2O).
        epap_50: Median EPAP / delivered mask pressure (cmH2O).
        epap_95: 95th-percentile EPAP (cmH2O).
        leak_50: Median unintentional leak (L/min).
        leak_95: 95th-percentile leak (L/min).
    """
    session_id: str
    start: str
    end: str
    ahi: float
    pressure_50: Optional[float]
    pressure_95: Optional[float]
    epap_50: Optional[float]
    epap_95: Optional[float]
    leak_50: Optional[float]
    leak_95: Optional[float]


@dataclass(frozen=True)
class OscarEvent:
    """A single therapy event from an OSCAR Details CSV export.

    Attributes:
        datetime_str: ISO datetime string of event onset.
        session_id: OSCAR session ID this event belongs to.
        event_type: Event classification (e.g. "Obstructive", "ClearAirway").
        duration_sec: Event duration in seconds.
    """
    datetime_str: str
    session_id: str
    event_type: str
    duration_sec: float


def read_sessions_csv(path: Path) -> list[OscarSession]:
    """Read an OSCAR Sessions CSV and return one ``OscarSession`` per row."""
    sessions: list[OscarSession] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            sessions.append(OscarSession(
                session_id=row.get("Session", "").strip(),
                start=row.get("Start", "").strip(),
                end=row.get("End", "").strip(),
                ahi=_safe_float(row.get("AHI")),
                pressure_50=_pick_nonzero(row, _P50_COLS),
                pressure_95=_pick_nonzero(row, _P95_COLS),
                epap_50=_pick_nonzero(row, ("Median EPAP",)),
                epap_95=_pick_nonzero(row, ("95% EPAP",)),
                leak_50=_pick_nonzero(row, ("Median Flow Limit.", "Median Leak", "Leak 50%")),
                leak_95=_pick_nonzero(row, ("95% Flow Limit.", "95% Leak", "Leak 95%")),
            ))
    return sessions


def read_details_csv(path: Path) -> list[OscarEvent]:
    """Read an OSCAR Details CSV and return therapy events (non-device-state rows)."""
    events: list[OscarEvent] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            event_type = row.get("Event", "").strip()
            if event_type in _DETAILS_NON_EVENTS:
                continue
            events.append(OscarEvent(
                datetime_str=row.get("DateTime", "").strip(),
                session_id=row.get("Session", "").strip(),
                event_type=event_type,
                duration_sec=_safe_float(row.get("Data/Duration")),
            ))
    return events


def _safe_float(value: Optional[str]) -> float:
    try:
        return float(value or 0)
    except (ValueError, TypeError):
        return 0.0
```

> **Note:** `_pick_nonzero`, `_P50_COLS`, `_P95_COLS` already exist in `oscar_reader.py` from `feat/oscar-validation`. `_safe_float` may already exist; if so, skip adding it.

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_oscar_reader_waveform.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add open_cpap_parser/validation/oscar_reader.py tests/test_oscar_reader_waveform.py
git commit -m "feat(validation): add OscarSession and OscarEvent readers for Sessions+Details CSVs"
```

---

## Task 3: Add waveform stats comparer

Create `open_cpap_parser/validation/waveform_compare.py` to compute percentile stats from `TimeSeriesData` and diff them against `OscarSession` values.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_waveform_compare.py`:

```python
import pytest
import numpy as np

from open_cpap_parser.schema import TimeSeriesData
from open_cpap_parser.validation.oscar_reader import OscarSession
from open_cpap_parser.validation.waveform_compare import (
    WaveformTolerances,
    WaveformDiff,
    compute_waveform_stats,
    compare_waveform,
    DEFAULT_WAVEFORM_TOLERANCES,
)


def _make_ts(mask_pressure=None, leak=None) -> TimeSeriesData:
    ts = TimeSeriesData()
    ts.mask_pressure = mask_pressure or []
    ts.leak = leak or []
    return ts


def _make_session(**kwargs) -> OscarSession:
    defaults = dict(
        session_id="123", start="2026-01-01T22:00:00", end="2026-01-02T06:00:00",
        ahi=1.0, pressure_50=None, pressure_95=None,
        epap_50=None, epap_95=None, leak_50=None, leak_95=None,
    )
    defaults.update(kwargs)
    return OscarSession(**defaults)


def test_compute_stats_empty_returns_none():
    stats = compute_waveform_stats(_make_ts())
    assert stats["epap_50"] is None
    assert stats["epap_95"] is None
    assert stats["leak_50"] is None
    assert stats["leak_95"] is None


def test_compute_stats_mask_pressure():
    values = [4.0] * 100 + [8.0] * 100
    ts = _make_ts(mask_pressure=values)
    stats = compute_waveform_stats(ts)
    assert stats["epap_50"] is not None
    assert 4.0 <= stats["epap_50"] <= 8.0
    assert stats["epap_95"] is not None
    assert stats["epap_95"] >= stats["epap_50"]


def test_compute_stats_leak():
    values = list(range(100))
    ts = _make_ts(leak=values)
    stats = compute_waveform_stats(ts)
    assert stats["leak_50"] is not None
    assert abs(stats["leak_50"] - np.median(values)) < 0.01
    assert stats["leak_95"] is not None
    assert abs(stats["leak_95"] - np.percentile(values, 95)) < 0.01


def test_compare_within_tolerance():
    ts = _make_ts(mask_pressure=[4.5] * 200, leak=[5.0] * 200)
    session = _make_session(epap_50=4.5, epap_95=4.5, leak_50=5.0, leak_95=5.0)
    diff = compare_waveform(ts, session)
    assert diff.within_tolerance


def test_compare_outside_tolerance():
    ts = _make_ts(mask_pressure=[4.0] * 200, leak=[5.0] * 200)
    session = _make_session(epap_50=8.0, epap_95=8.0, leak_50=5.0, leak_95=5.0)
    diff = compare_waveform(ts, session)
    assert not diff.within_tolerance


def test_compare_none_oscar_value_is_skipped():
    # If OSCAR didn't export a value, the diff should not count it as a failure.
    ts = _make_ts(mask_pressure=[4.5] * 200)
    session = _make_session(epap_50=None, epap_95=None)
    diff = compare_waveform(ts, session)
    assert diff.epap_50_delta is None
    assert diff.within_tolerance
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
uv run pytest tests/test_waveform_compare.py -v
```

Expected: `ImportError` — `waveform_compare` module not found.

- [ ] **Step 3: Create `open_cpap_parser/validation/waveform_compare.py`**

```python
"""Waveform-derived statistics comparison against OSCAR session-level values.

Computes pressure and leak percentiles from ``TimeSeriesData`` arrays and
diffs them against the corresponding OSCAR Sessions CSV columns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from open_cpap_parser.schema import TimeSeriesData
from open_cpap_parser.validation.oscar_reader import OscarSession


@dataclass(frozen=True)
class WaveformTolerances:
    """Acceptable deviation thresholds for waveform-derived stats.

    Attributes:
        pressure: Maximum allowed pressure delta (cmH2O).
        leak: Maximum allowed leak delta (L/min).
    """
    pressure: float = 0.5
    leak: float = 2.0


DEFAULT_WAVEFORM_TOLERANCES = WaveformTolerances()


@dataclass
class WaveformDiff:
    """Comparison result for waveform-derived stats vs OSCAR session values.

    Attributes:
        session_id: OSCAR session ID.
        epap_50_delta: |parser − OSCAR| for median EPAP, or None if unavailable.
        epap_95_delta: |parser − OSCAR| for 95th-percentile EPAP, or None.
        leak_50_delta: |parser − OSCAR| for median leak, or None.
        leak_95_delta: |parser − OSCAR| for 95th-percentile leak, or None.
        within_tolerance: True when every available delta is within tolerance.
    """
    session_id: str
    epap_50_delta: Optional[float]
    epap_95_delta: Optional[float]
    leak_50_delta: Optional[float]
    leak_95_delta: Optional[float]
    within_tolerance: bool


def compute_waveform_stats(ts: TimeSeriesData) -> dict[str, Optional[float]]:
    """Compute percentile statistics from ``TimeSeriesData`` signal arrays.

    Uses ``ts.mask_pressure`` (PLD ``MaskPress.2s``) for EPAP and
    ``ts.leak`` (PLD ``Leak.2s``) for leak.

    Returns:
        Dict with keys: ``epap_50``, ``epap_95``, ``leak_50``, ``leak_95``.
        Values are ``None`` when the signal array is empty.
    """
    def _pct(arr: list[float], q: float) -> Optional[float]:
        if not arr:
            return None
        return float(np.percentile(arr, q))

    return {
        "epap_50": _pct(ts.mask_pressure, 50),
        "epap_95": _pct(ts.mask_pressure, 95),
        "leak_50": _pct(ts.leak, 50),
        "leak_95": _pct(ts.leak, 95),
    }


def compare_waveform(
    ts: TimeSeriesData,
    oscar_session: OscarSession,
    tolerances: WaveformTolerances = DEFAULT_WAVEFORM_TOLERANCES,
) -> WaveformDiff:
    """Compare waveform-derived stats against an OSCAR session record.

    Args:
        ts: Parsed ``TimeSeriesData`` for the session.
        oscar_session: The matching ``OscarSession`` from OSCAR's Sessions CSV.
        tolerances: Per-metric deviation thresholds.

    Returns:
        A ``WaveformDiff`` with per-metric deltas and an overall pass/fail flag.
    """
    stats = compute_waveform_stats(ts)

    def _delta(parser_val: Optional[float], oscar_val: Optional[float]) -> Optional[float]:
        if parser_val is None or oscar_val is None:
            return None
        return abs(parser_val - oscar_val)

    epap_50_delta = _delta(stats["epap_50"], oscar_session.epap_50)
    epap_95_delta = _delta(stats["epap_95"], oscar_session.epap_95)
    leak_50_delta = _delta(stats["leak_50"], oscar_session.leak_50)
    leak_95_delta = _delta(stats["leak_95"], oscar_session.leak_95)

    within = all(
        d is None or d <= threshold
        for d, threshold in (
            (epap_50_delta, tolerances.pressure),
            (epap_95_delta, tolerances.pressure),
            (leak_50_delta, tolerances.leak),
            (leak_95_delta, tolerances.leak),
        )
    )

    return WaveformDiff(
        session_id=oscar_session.session_id,
        epap_50_delta=epap_50_delta,
        epap_95_delta=epap_95_delta,
        leak_50_delta=leak_50_delta,
        leak_95_delta=leak_95_delta,
        within_tolerance=within,
    )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_waveform_compare.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add open_cpap_parser/validation/waveform_compare.py tests/test_waveform_compare.py
git commit -m "feat(validation): add waveform stats comparer (EPAP/leak percentiles vs OSCAR Sessions CSV)"
```

---

## Task 4: Add event comparer

Create `open_cpap_parser/validation/event_compare.py` to match our `CPAPEvent` lists against OSCAR Details CSV events.

Event matching strategy:
- Match by session (start_time → Unix timestamp → OSCAR session ID)
- Compare count-per-type (within ±1 absolute or ±10% relative)
- Individual event onset timing (within ±2 seconds of session start)

OSCAR event type → our event_type mapping:
| OSCAR | Our `CPAPEvent.event_type` |
|-------|--------------------------|
| `Obstructive` | `"Obstructive Apnea"` or `"OA"` |
| `ClearAirway` | `"Clear Airway"` or `"CA"` |
| `Hypopnea` | `"Hypopnea"` or `"H"` |
| `RERA` | `"RERA"` |
| `FlowLimit` | `"Flow Limitation"` |

- [ ] **Step 1: Write the failing tests**

Create `tests/test_event_compare.py`:

```python
from datetime import datetime, timezone
from open_cpap_parser.schema import CPAPEvent, CPAPSession
from open_cpap_parser.validation.oscar_reader import OscarEvent
from open_cpap_parser.validation.event_compare import (
    EventCounts,
    EventCountDiff,
    count_events,
    count_oscar_events,
    compare_event_counts,
    OSCAR_TO_PARSER_EVENT,
)


def _make_event(event_type: str, onset_sec: float = 0.0, dur: float = 10.0) -> CPAPEvent:
    return CPAPEvent(timestamp_sec=onset_sec, event_type=event_type, duration_sec=dur)


def _make_oscar_event(session_id: str, event_type: str) -> OscarEvent:
    return OscarEvent(
        datetime_str="2026-01-01T22:05:00",
        session_id=session_id,
        event_type=event_type,
        duration_sec=10.0,
    )


def test_count_events_empty():
    counts = count_events([])
    assert counts.obstructive == 0
    assert counts.central == 0
    assert counts.hypopnea == 0


def test_count_events_by_type():
    events = [
        _make_event("Obstructive Apnea"),
        _make_event("Obstructive Apnea"),
        _make_event("Clear Airway"),
        _make_event("Hypopnea"),
    ]
    counts = count_events(events)
    assert counts.obstructive == 2
    assert counts.central == 1
    assert counts.hypopnea == 1


def test_count_oscar_events():
    oscar_events = [
        _make_oscar_event("123", "Obstructive"),
        _make_oscar_event("123", "ClearAirway"),
        _make_oscar_event("123", "ClearAirway"),
    ]
    counts = count_oscar_events(oscar_events)
    assert counts.obstructive == 1
    assert counts.central == 2


def test_compare_event_counts_within_tolerance():
    ours = EventCounts(obstructive=5, central=1, hypopnea=3, rera=0, flow_limit=0)
    oscar = EventCounts(obstructive=5, central=1, hypopnea=3, rera=0, flow_limit=0)
    diff = compare_event_counts(ours, oscar)
    assert diff.within_tolerance


def test_compare_event_counts_outside_tolerance():
    ours = EventCounts(obstructive=0, central=1, hypopnea=3, rera=0, flow_limit=0)
    oscar = EventCounts(obstructive=5, central=1, hypopnea=3, rera=0, flow_limit=0)
    diff = compare_event_counts(ours, oscar)
    assert not diff.within_tolerance


def test_oscar_event_mapping_keys_exist():
    # Each OSCAR event type must map to something we can count.
    for oscar_type in ("Obstructive", "ClearAirway", "Hypopnea", "RERA", "FlowLimit"):
        assert oscar_type in OSCAR_TO_PARSER_EVENT, f"Missing mapping for {oscar_type}"
```

- [ ] **Step 2: Run tests to confirm failure**

```bash
uv run pytest tests/test_event_compare.py -v
```

Expected: `ImportError` — module not found.

- [ ] **Step 3: Create `open_cpap_parser/validation/event_compare.py`**

```python
"""Event count comparison between parsed CPAPEvent lists and OSCAR Details CSV.

Matches our therapy events against OSCAR's per-session event records,
comparing counts by type within a tolerance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from open_cpap_parser.schema import CPAPEvent
from open_cpap_parser.validation.oscar_reader import OscarEvent


# Maps OSCAR Details CSV event type → our CPAPEvent.event_type prefix(es).
OSCAR_TO_PARSER_EVENT: dict[str, set[str]] = {
    "Obstructive": {"Obstructive Apnea", "OA", "Obstructive"},
    "ClearAirway": {"Clear Airway", "CA", "ClearAirway", "Central Apnea"},
    "Hypopnea": {"Hypopnea", "H"},
    "RERA": {"RERA"},
    "FlowLimit": {"Flow Limitation", "Flow Limit", "FlowLimit", "FL"},
}

# Maps our event_type → count field name
_PARSER_TO_FIELD: dict[str, str] = {}
for _oscar_key, _parser_set in OSCAR_TO_PARSER_EVENT.items():
    _field = _oscar_key.lower()
    if _oscar_key == "ClearAirway":
        _field = "central"
    elif _oscar_key == "Obstructive":
        _field = "obstructive"
    elif _oscar_key == "Hypopnea":
        _field = "hypopnea"
    elif _oscar_key == "RERA":
        _field = "rera"
    elif _oscar_key == "FlowLimit":
        _field = "flow_limit"
    for _pt in _parser_set:
        _PARSER_TO_FIELD[_pt] = _field


@dataclass
class EventCounts:
    """Per-type therapy event counts for one session."""
    obstructive: int = 0
    central: int = 0
    hypopnea: int = 0
    rera: int = 0
    flow_limit: int = 0


@dataclass
class EventCountDiff:
    """Comparison result for event counts."""
    obstructive_delta: Optional[int]
    central_delta: Optional[int]
    hypopnea_delta: Optional[int]
    rera_delta: Optional[int]
    flow_limit_delta: Optional[int]
    within_tolerance: bool


_COUNT_TOLERANCE = 1  # allow ±1 event count difference


def count_events(events: list[CPAPEvent]) -> EventCounts:
    """Count therapy events from a ``CPAPEvent`` list by category."""
    counts = EventCounts()
    for ev in events:
        field = _PARSER_TO_FIELD.get(ev.event_type)
        if field:
            setattr(counts, field, getattr(counts, field) + 1)
    return counts


def count_oscar_events(oscar_events: list[OscarEvent]) -> EventCounts:
    """Count therapy events from an ``OscarEvent`` list by category."""
    counts = EventCounts()
    for ev in oscar_events:
        field = {
            "Obstructive": "obstructive",
            "ClearAirway": "central",
            "Hypopnea": "hypopnea",
            "RERA": "rera",
            "FlowLimit": "flow_limit",
        }.get(ev.event_type)
        if field:
            setattr(counts, field, getattr(counts, field) + 1)
    return counts


def compare_event_counts(
    ours: EventCounts,
    oscar: EventCounts,
    tolerance: int = _COUNT_TOLERANCE,
) -> EventCountDiff:
    """Compare parser event counts against OSCAR counts.

    Args:
        ours: Event counts from our parser.
        oscar: Event counts from OSCAR Details CSV.
        tolerance: Maximum allowed count difference per type.

    Returns:
        ``EventCountDiff`` with per-type deltas and overall pass/fail.
    """
    fields = ("obstructive", "central", "hypopnea", "rera", "flow_limit")
    deltas = {f: abs(getattr(ours, f) - getattr(oscar, f)) for f in fields}
    within = all(d <= tolerance for d in deltas.values())
    return EventCountDiff(
        obstructive_delta=deltas["obstructive"],
        central_delta=deltas["central"],
        hypopnea_delta=deltas["hypopnea"],
        rera_delta=deltas["rera"],
        flow_limit_delta=deltas["flow_limit"],
        within_tolerance=within,
    )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/test_event_compare.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add open_cpap_parser/validation/event_compare.py tests/test_event_compare.py
git commit -m "feat(validation): add event count comparer (CPAPEvents vs OSCAR Details CSV)"
```

---

## Task 5: Extend conftest.py to auto-export Sessions and Details CSVs

Extend `validation/conftest.py` to add fixtures for Sessions and Details CSVs, and extend the `_refresh_oscar_exports` session fixture to auto-generate them alongside Summary CSVs.

- [ ] **Step 1: Write the failing test (fixture existence check)**

Add to `validation/test_resmed_waveform_validation.py` (create the file now):

```python
"""Waveform validation: ResMed BRP+PLD sessions vs OSCAR Sessions+Details CSVs."""
import pytest

pytestmark = pytest.mark.validation


def test_oscar_sessions_csv_fixture_resolves(oscar_sessions_csv, sample_paths):
    """Smoke test: fixture finds a Sessions CSV for resmed_cam."""
    path = oscar_sessions_csv("resmed_cam")
    assert path.exists()
    assert path.stat().st_size > 100
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest validation/test_resmed_waveform_validation.py --run-validation -v
```

Expected: `FAILED` — fixture `oscar_sessions_csv` not found.

- [ ] **Step 3: Extend `validation/conftest.py`**

Add directly below the existing `oscar_csv` fixture:

```python
@pytest.fixture(scope="session")
def oscar_sessions_csv(oscar_export_root: Path):
    """Return a callable that resolves sample name → OSCAR Sessions CSV path."""
    def _find(sample_name: str) -> Path:
        profile = _OSCAR_PROFILE_NAMES.get(sample_name)
        if not profile:
            pytest.skip(f"No OSCAR profile name configured for '{sample_name}'.")
        pattern = f"OSCAR_{profile}_Sessions_*.csv"
        matches = sorted(oscar_export_root.glob(pattern))
        if not matches:
            pytest.skip(
                f"No OSCAR Sessions CSV found for '{sample_name}'. "
                f"Expected pattern: {oscar_export_root / pattern}"
            )
        return matches[-1]
    return _find


@pytest.fixture(scope="session")
def oscar_details_csv(oscar_export_root: Path):
    """Return a callable that resolves sample name → OSCAR Details CSV path."""
    def _find(sample_name: str) -> Path:
        profile = _OSCAR_PROFILE_NAMES.get(sample_name)
        if not profile:
            pytest.skip(f"No OSCAR profile name configured for '{sample_name}'.")
        pattern = f"OSCAR_{profile}_Details_*.csv"
        matches = sorted(oscar_export_root.glob(pattern))
        if not matches:
            pytest.skip(
                f"No OSCAR Details CSV found for '{sample_name}'. "
                f"Expected pattern: {oscar_export_root / pattern}"
            )
        return matches[-1]
    return _find
```

Also extend `_refresh_oscar_exports` in `validation/conftest.py` to generate Sessions and Details CSVs. Inside the `for sample_name, profile_dir in _OSCAR_PROFILE_DIRS.items():` loop, add two more blocks after the existing Summary export:

```python
        # Sessions CSV
        sessions_path = oscar_export_root / f"OSCAR_{display}_Sessions_{today}.csv"
        if not sessions_path.exists():
            cmd = cmd_prefix + [
                "export", "sessions",
                "--root", str(_OSCAR_DATA_ROOT),
                "--profile-user", profile_dir,
                "--from", "2020-01-01",
                "--to", today,
                "--out", str(sessions_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
            if result.returncode != 0:
                print(f"\nWARNING: oscar-export sessions failed for {sample_name}:\n{result.stderr}\n")

        # Details CSV
        details_path = oscar_export_root / f"OSCAR_{display}_Details_{today}.csv"
        if not details_path.exists():
            cmd = cmd_prefix + [
                "export", "details",
                "--root", str(_OSCAR_DATA_ROOT),
                "--profile-user", profile_dir,
                "--from", "2020-01-01",
                "--to", today,
                "--out", str(details_path),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
            if result.returncode != 0:
                print(f"\nWARNING: oscar-export details failed for {sample_name}:\n{result.stderr}\n")
```

> **Note:** Verify that `oscar-export export sessions` and `export details` are valid subcommands in the local patched oscar-export. If they use different subcommand names, check `go run . --help` from `~/ZedProjects/oscar-export/` and adjust accordingly. If the tool doesn't support sessions/details export, the conftest should fall back to globbing existing files as it currently does for Summary CSVs.

- [ ] **Step 4: Run to confirm the test passes**

```bash
uv run pytest validation/test_resmed_waveform_validation.py --run-validation -v
```

Expected: PASS (the existing CSV files at `~/ZedProjects/sleepData/validation/` are found by the fixture).

- [ ] **Step 5: Commit**

```bash
git add validation/conftest.py validation/test_resmed_waveform_validation.py
git commit -m "feat(validation): add oscar_sessions_csv and oscar_details_csv fixtures"
```

---

## Task 6: ResMed waveform validation tests

Complete `validation/test_resmed_waveform_validation.py` with full waveform stats and event count validation tests.

- [ ] **Step 1: Write the failing tests**

Replace the contents of `validation/test_resmed_waveform_validation.py` with:

```python
"""Waveform validation: ResMed BRP+PLD sessions vs OSCAR Sessions+Details CSVs.

Run with:
    uv run pytest validation/ --run-validation -v

Requires:
    - SD card dump at ~/ZedProjects/sleepData/tmpdata/cam/
    - OSCAR Sessions CSV at ~/ZedProjects/sleepData/validation/OSCAR_ResMedCam_Sessions_*.csv
    - OSCAR Details CSV at ~/ZedProjects/sleepData/validation/OSCAR_ResMedCam_Details_*.csv
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from open_cpap_parser.adapters.resmed import ResMedAdapter
from open_cpap_parser.schema import CPAPSession
from open_cpap_parser.validation.oscar_reader import (
    OscarSession,
    OscarEvent,
    read_sessions_csv,
    read_details_csv,
)
from open_cpap_parser.validation.waveform_compare import (
    compare_waveform,
    DEFAULT_WAVEFORM_TOLERANCES,
)
from open_cpap_parser.validation.event_compare import (
    count_events,
    count_oscar_events,
    compare_event_counts,
)

pytestmark = pytest.mark.validation

_SAMPLE = "resmed_cam"
_PASS_RATE_THRESHOLD = 0.80  # 80% of sessions must pass waveform validation


def _session_start_key(session: CPAPSession) -> str:
    """Return ISO start time string truncated to seconds for matching."""
    return session.start_time.strftime("%Y-%m-%dT%H:%M:%S")


def _oscar_session_start_key(oscar: OscarSession) -> str:
    return oscar.start.replace("Z", "")[:19]


@pytest.fixture(scope="module")
def resmed_data(sample_paths):
    path = sample_paths[_SAMPLE]
    if not path.is_dir():
        pytest.skip(f"ResMed sample data not available at {path}")
    adapter = ResMedAdapter()
    return adapter.extract_and_map(path, include_timeseries=True)


@pytest.fixture(scope="module")
def oscar_sessions(oscar_sessions_csv):
    csv_path = oscar_sessions_csv(_SAMPLE)
    return read_sessions_csv(csv_path)


@pytest.fixture(scope="module")
def oscar_events(oscar_details_csv):
    csv_path = oscar_details_csv(_SAMPLE)
    return read_details_csv(csv_path)


@pytest.fixture(scope="module")
def matched_sessions(resmed_data, oscar_sessions):
    """Pair each BRP+PLD session with its OSCAR Sessions row by start_time."""
    oscar_by_start = {_oscar_session_start_key(s): s for s in oscar_sessions}
    pairs = []
    for session in resmed_data.sessions:
        if session.file_type != "BRP+PLD":
            continue
        key = _session_start_key(session)
        oscar = oscar_by_start.get(key)
        if oscar is not None:
            pairs.append((session, oscar))
    return pairs


@pytest.fixture(scope="module")
def oscar_events_by_session(oscar_events):
    """Group OSCAR events by session_id."""
    groups: dict[str, list[OscarEvent]] = {}
    for ev in oscar_events:
        groups.setdefault(ev.session_id, []).append(ev)
    return groups


def test_matched_sessions_found(matched_sessions):
    """At least one BRP+PLD session must match an OSCAR Sessions row."""
    assert len(matched_sessions) > 0, (
        "No sessions matched. Check that start_time format aligns between "
        "parser output and OSCAR Sessions CSV."
    )


def test_waveform_stats_pass_rate(matched_sessions):
    """At least 80% of matched sessions must pass EPAP/leak tolerance check."""
    if not matched_sessions:
        pytest.skip("No matched sessions available.")

    passed = sum(
        1
        for session, oscar in matched_sessions
        if session.timeseries is not None
        and compare_waveform(session.timeseries, oscar).within_tolerance
    )
    total = sum(1 for _, _ in matched_sessions if _[0].timeseries is not None)
    if total == 0:
        pytest.skip("No sessions have timeseries data.")

    rate = passed / total
    assert rate >= _PASS_RATE_THRESHOLD, (
        f"Waveform stats pass rate {rate:.1%} < {_PASS_RATE_THRESHOLD:.0%} "
        f"({passed}/{total} sessions within tolerance)"
    )


def test_event_counts_pass_rate(resmed_data, oscar_events_by_session, oscar_sessions):
    """At least 80% of sessions must have event counts within ±1 of OSCAR."""
    oscar_id_to_start = {s.session_id: s.start for s in oscar_sessions}
    session_start_to_oscar_id = {v[:19]: k for k, v in oscar_id_to_start.items()}

    merged = [s for s in resmed_data.sessions if s.file_type == "BRP+PLD"]
    if not merged:
        pytest.skip("No BRP+PLD sessions found.")

    passed = 0
    total = 0
    for session in merged:
        start_key = _session_start_key(session)
        oscar_id = session_start_to_oscar_id.get(start_key)
        if oscar_id is None:
            continue
        our_counts = count_events(session.events)
        oscar_event_list = oscar_events_by_session.get(oscar_id, [])
        oscar_counts = count_oscar_events(oscar_event_list)
        diff = compare_event_counts(our_counts, oscar_counts)
        total += 1
        if diff.within_tolerance:
            passed += 1

    if total == 0:
        pytest.skip("No sessions matched for event comparison.")

    rate = passed / total
    assert rate >= _PASS_RATE_THRESHOLD, (
        f"Event count pass rate {rate:.1%} < {_PASS_RATE_THRESHOLD:.0%} "
        f"({passed}/{total} sessions within tolerance)"
    )
```

- [ ] **Step 2: Run to confirm the tests are discovered and either fail or skip**

```bash
uv run pytest validation/test_resmed_waveform_validation.py --run-validation -v
```

Expected: Tests run. `test_matched_sessions_found` and others may fail if session start_time matching needs calibration. This is expected — the tests reveal the actual accuracy of our waveform extraction.

- [ ] **Step 3: Investigate and calibrate if needed**

If `test_matched_sessions_found` fails (0 matched sessions), the start_time key format likely doesn't align. Debug:

```python
# Run interactively:
from open_cpap_parser.adapters.resmed import ResMedAdapter
from pathlib import Path
adapter = ResMedAdapter()
data = adapter.extract_and_map(Path("~/ZedProjects/sleepData/tmpdata/cam").expanduser(), include_timeseries=False)
merged = [s for s in data.sessions if s.file_type == "BRP+PLD"]
print([s.start_time.isoformat() for s in merged[:3]])
```

Compare against OSCAR Sessions CSV start column. Adjust `_session_start_key` if the format differs (e.g., timezone offset).

- [ ] **Step 4: Commit**

```bash
git add validation/test_resmed_waveform_validation.py
git commit -m "feat(validation): add ResMed waveform stats and event count validation tests"
```

---

## Task 7: Löwenstein waveform validation placeholder

Create a placeholder test file for Löwenstein. All tests skip until the waveform extraction parser is implemented (Phase 2 of the roadmap).

- [ ] **Step 1: Create `validation/test_lowenstein_waveform_validation.py`**

```python
"""Waveform validation: Löwenstein Eyra — PLACEHOLDER (Phase 2).

These tests will validate Löwenstein waveform extraction once the Rust
parser decodes waveform channels from therapy.pdat (Phase 2 of roadmap).

Until then, all tests in this file are skipped automatically.

When implementing Phase 2:
1. Remove the module-level skip marker below.
2. Implement waveform extraction in the Löwenstein adapter.
3. Run these tests to measure accuracy vs OSCAR Details/Sessions exports.
"""
import pytest

pytestmark = [
    pytest.mark.validation,
    pytest.mark.skip(reason="Löwenstein waveform extraction not yet implemented (Phase 2)."),
]

_SAMPLE = "lowenstein_eyra"
_PASS_RATE_THRESHOLD = 0.80


def test_waveform_stats_pass_rate(sample_paths, oscar_sessions_csv, oscar_details_csv):
    """Löwenstein waveform EPAP/leak stats within tolerance of OSCAR Sessions CSV."""
    from open_cpap_parser.adapters.lowenstein import LowensteinAdapter
    from open_cpap_parser.validation.oscar_reader import read_sessions_csv
    from open_cpap_parser.validation.waveform_compare import compare_waveform

    path = sample_paths[_SAMPLE]
    if not path.is_dir():
        pytest.skip(f"Löwenstein sample data not available at {path}")

    adapter = LowensteinAdapter()
    data = adapter.extract_and_map(path, include_timeseries=True)
    sessions_with_ts = [s for s in data.sessions if s.timeseries is not None]
    if not sessions_with_ts:
        pytest.skip("No sessions with timeseries — waveform extraction not implemented.")

    oscar_sessions = read_sessions_csv(oscar_sessions_csv(_SAMPLE))
    # matching and pass rate calculation mirrors test_resmed_waveform_validation.py
    raise NotImplementedError("Implement session matching once waveform extraction is done.")


def test_event_counts_pass_rate(sample_paths, oscar_details_csv):
    """Löwenstein event counts within ±1 of OSCAR Details CSV."""
    raise NotImplementedError("Implement once waveform extraction is done.")
```

- [ ] **Step 2: Run to confirm the tests are collected and skipped**

```bash
uv run pytest validation/test_lowenstein_waveform_validation.py --run-validation -v
```

Expected: `2 skipped` — tests collected but automatically skipped.

- [ ] **Step 3: Commit**

```bash
git add validation/test_lowenstein_waveform_validation.py
git commit -m "feat(validation): add Löwenstein waveform validation placeholder (Phase 2)"
```

---

## Task 8: Run the full validation suite and document baseline

After all tasks are implemented, run the full suite and record the baseline pass rates.

- [ ] **Step 1: Run the complete validation suite**

```bash
uv run pytest validation/ --run-validation -v 2>&1 | tee validation/reports/waveform-baseline.txt
```

- [ ] **Step 2: Note the pass rates in the PR description**

Record:
- ResMed waveform stats pass rate (EPAP/leak vs OSCAR)
- ResMed event count pass rate (CPAPEvents vs OSCAR Details)
- Any systematic offsets observed (e.g., "EPAP consistently 0.2 cmH₂O lower")

These become the baseline for measuring Löwenstein waveform extraction (Phase 2) and any future parser improvements.

- [ ] **Step 3: Commit the baseline report**

```bash
git add validation/reports/waveform-baseline.txt
git commit -m "chore(validation): record waveform validation baseline pass rates"
```

---

## Self-Review

### Spec coverage
- ✅ Waveform stats validation (EPAP, leak percentiles from PLD) — Tasks 3, 6
- ✅ Event count validation (CPAPEvents vs OSCAR Details) — Tasks 4, 6
- ✅ Löwenstein placeholder — Task 7
- ✅ Sessions CSV reader — Task 2
- ✅ Details CSV reader — Task 2
- ✅ conftest fixtures for new CSV types — Task 5
- ✅ Baseline measurement — Task 8
- ⚠️ oscar-export `export sessions` / `export details` subcommand names — **must verify** in Task 5 Step 3 note

### Placeholder scan
- No TBDs or "implement later" in any task (the Löwenstein file uses `NotImplementedError` intentionally as a signal, not as a placeholder in the plan itself)

### Type consistency
- `OscarSession` used consistently in Tasks 2, 3, 6
- `OscarEvent` used consistently in Tasks 2, 4, 6
- `WaveformDiff.within_tolerance`, `EventCountDiff.within_tolerance` — consistent field names in Tasks 3, 4, 6
- `compute_waveform_stats()` → returns `dict[str, Optional[float]]` — used in Task 3 tests and Task 6 via `compare_waveform()`
- `count_events()`, `count_oscar_events()`, `compare_event_counts()` — consistent signatures in Tasks 4 and 6
