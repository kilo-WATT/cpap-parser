"""Waveform validation: ResMed BRP+PLD sessions vs OSCAR Sessions+Details CSVs.

Run with:
    uv run pytest validation/ --run-validation -v

Requires:
    - SD card dump at ~/ZedProjects/sleepData/tmpdata/cam/
    - OSCAR Sessions CSV at ~/ZedProjects/sleepData/validation/OSCAR_ResMedCam_Sessions_*.csv
    - OSCAR Details CSV at ~/ZedProjects/sleepData/validation/OSCAR_ResMedCam_Details_*.csv
"""
from __future__ import annotations

from datetime import timedelta
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
)
from open_cpap_parser.validation.event_compare import (
    count_events,
    count_oscar_events,
    compare_event_counts,
)

pytestmark = pytest.mark.validation

_SAMPLE = "resmed_cam"
_PASS_RATE_THRESHOLD = 0.80

# ResMed EDF stores local time at UTC-7 (no DST adjustment).
# OSCAR exports UTC. Add this offset to parser local time to get UTC.
_DEVICE_LOCAL_TO_UTC = timedelta(hours=-7)


def _session_start_key(session: CPAPSession) -> str:
    """Convert parser's local start_time (UTC-7) to UTC string for OSCAR matching."""
    return (session.start_time + _DEVICE_LOCAL_TO_UTC).strftime("%Y-%m-%dT%H:%M:%S")


def _oscar_session_start_key(oscar: OscarSession) -> str:
    return oscar.start[:19]


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
    groups: dict[str, list[OscarEvent]] = {}
    for ev in oscar_events:
        groups.setdefault(ev.session_id, []).append(ev)
    return groups


def test_oscar_sessions_csv_fixture_resolves(oscar_sessions_csv):
    path = oscar_sessions_csv(_SAMPLE)
    assert path.exists()
    assert path.stat().st_size > 100


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

    sessions_with_ts = [(s, o) for s, o in matched_sessions if s.timeseries is not None]
    if not sessions_with_ts:
        pytest.skip("No sessions have timeseries data.")

    passed = sum(
        1
        for session, oscar in sessions_with_ts
        if compare_waveform(session.timeseries, oscar).within_tolerance
    )
    total = len(sessions_with_ts)
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
