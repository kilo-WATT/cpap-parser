"""Waveform validation: Löwenstein Eyra — per-session waveform + event counts.

Run with:
    uv run pytest validation/ --run-validation -v

Requires:
    - SD card dump at ~/ZedProjects/sleepData/loweinstein-sample/ExampleFiles/
    - OSCAR Sessions CSV at ~/ZedProjects/sleepData/validation/OSCAR_LowensteinTest_Sessions_*.csv
    - OSCAR Details CSV at ~/ZedProjects/sleepData/validation/OSCAR_LowensteinTest_Details_*.csv

Test Tiers
----------
Tests are grouped into three tiers with different regression-detection goals.

**Tier 1 — Infrastructure (no threshold)**
    Sanity checks that fixtures resolve and return non-empty data.  These fail
    fast if the sample data is missing or a fixture is misconfigured.

**Tier 2 — Session matching (≥95% threshold)**
    Validates that our 4.5-minute duration filter produces the same session set
    as OSCAR.  The filter reduces 43 per-file sessions to 22, currently matching
    22/22 OSCAR session IDs (100%).  The 95% threshold catches any regression in
    session selection logic — e.g. a change to ``filter_sessions`` thresholds,
    session boundary detection, or session ID extraction from ``file_type``.

    This tier is strict by design: session matching is determined entirely by
    our filtering logic and is independent of signal accuracy.  A regression
    here means the adapter is producing wrong sessions, not just noisy signal.

**Tier 3 — Signal accuracy (current baselines)**
    Validates per-session waveform stats (EPAP p50/p95 ±0.5 hPa) and event
    counts (±1 per type) against the filtered 22-session matched set.  Thresholds
    reflect current capability, not targets.  They are intentionally set below
    the measured baseline to avoid false failures from minor float variance while
    still catching meaningful regressions.

    Waveform stats: 27.3% (6/22 sessions pass).
        Accuracy is limited by therapy-mode-dependent signal selection: EPAPsoll
        serves as mask_pressure but applies to CPAP mode; BiPAP/ST sessions use
        a different pressure signal.  The Rust decoder already filters EPAPsoll
        to 2–30 hPa to strip sentinels.  Improvement requires per-mode signal
        routing (CPAP vs BiPAP/ST) in the Rust parser.

    Event counts: 45.5% (10/22 sessions pass).
        Discrepancies come from event IDs in the device XML that are not yet
        mapped to our CPAPEvent event_type vocabulary.

    Raise these thresholds when the underlying accuracy improves (per-mode
    pressure routing, additional event type coverage).  Do not raise them without
    a corresponding code change that actually improves accuracy.
"""
from __future__ import annotations

import pytest

from open_cpap_parser.adapters.lowenstein import LowensteinAdapter
from open_cpap_parser.schema import CPAPSession
from open_cpap_parser.validation.oscar_reader import (
    OscarSession,
    OscarEvent,
    read_sessions_csv,
    read_details_csv,
)
from open_cpap_parser.validation.waveform_compare import compare_waveform
from open_cpap_parser.validation.event_compare import (
    count_events,
    count_oscar_events,
    compare_event_counts,
)

pytestmark = pytest.mark.validation

_SAMPLE = "lowenstein_eyra"

# ---------------------------------------------------------------------------
# Tier 2 threshold: session matching.
#
# Our 4.5-minute filter reduces 43 per-file sessions to 22.  As of 2026-05-23
# we match 22/22 OSCAR session IDs (100%).  The threshold is set at 95% so
# that minor edge-case regressions (e.g., one session boundary shifts) are
# still caught without requiring an exact count.
# ---------------------------------------------------------------------------
_SESSION_MATCH_RATE_THRESHOLD = 0.95

# ---------------------------------------------------------------------------
# Tier 3 thresholds: signal accuracy baselines (measured 2026-05-23).
#
# These are floors, not targets.  Do NOT raise them without a code change that
# demonstrably improves accuracy on the same 22-session matched set.
# ---------------------------------------------------------------------------
_WAVEFORM_PASS_RATE_THRESHOLD = 0.25   # measured: 27.3% (6/22)
_EVENT_PASS_RATE_THRESHOLD = 0.40      # measured: 45.5% (10/22)


def _session_number(session: CPAPSession) -> str | None:
    """Extract session number from file_type like 'PrismaLine/388' → '388'."""
    parts = session.file_type.split("/", 1)
    return parts[1] if len(parts) == 2 else None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def lowenstein_data(sample_paths):
    """Parse the Löwenstein sample directory with timeseries enabled.

    The adapter internally applies ``filter_sessions`` (≥4.5 min), so this
    fixture returns the filtered 22-session set, not the raw 43 per-file
    sessions.  Tests in all tiers operate on this filtered set.
    """
    path = sample_paths[_SAMPLE]
    if not path.is_dir():
        pytest.skip(f"Löwenstein sample data not available at {path}")
    adapter = LowensteinAdapter()
    return adapter.extract_and_map(path, include_timeseries=True)


@pytest.fixture(scope="module")
def oscar_sessions(oscar_sessions_csv):
    """OSCAR Sessions CSV rows for the Löwenstein Eyra sample."""
    csv_path = oscar_sessions_csv(_SAMPLE)
    return read_sessions_csv(csv_path)


@pytest.fixture(scope="module")
def oscar_events(oscar_details_csv):
    """OSCAR Details CSV rows (per-event log) for the Löwenstein Eyra sample."""
    csv_path = oscar_details_csv(_SAMPLE)
    return read_details_csv(csv_path)


@pytest.fixture(scope="module")
def matched_sessions(lowenstein_data, oscar_sessions):
    """Pair each filtered PrismaLine session with its OSCAR Sessions row.

    Matching is done by session number — the integer suffix of
    ``CPAPSession.file_type`` (e.g., ``PrismaLine/388`` → ``388``) must equal
    the ``session_id`` column in the OSCAR Sessions CSV.

    Sessions without a matching OSCAR row are silently dropped.  The
    ``test_session_match_rate`` test verifies that the drop rate stays low.
    """
    oscar_by_id = {s.session_id: s for s in oscar_sessions}
    pairs = []
    for session in lowenstein_data.sessions:
        num = _session_number(session)
        if num is None:
            continue
        oscar = oscar_by_id.get(num)
        if oscar is not None:
            pairs.append((session, oscar))
    return pairs


@pytest.fixture(scope="module")
def oscar_events_by_session(oscar_events):
    """Index OSCAR detail rows by session ID for O(1) per-session lookup."""
    groups: dict[str, list[OscarEvent]] = {}
    for ev in oscar_events:
        groups.setdefault(ev.session_id, []).append(ev)
    return groups


# ---------------------------------------------------------------------------
# Tier 1 — Infrastructure
# ---------------------------------------------------------------------------

def test_oscar_sessions_csv_fixture_resolves(oscar_sessions_csv):
    """OSCAR Sessions CSV must exist and be non-trivially sized."""
    path = oscar_sessions_csv(_SAMPLE)
    assert path.exists()
    assert path.stat().st_size > 100


def test_matched_sessions_found(matched_sessions):
    """At least one filtered PrismaLine session must match an OSCAR Sessions row.

    A zero-match result means either the sample data is missing, the session
    number is not being extracted from ``file_type`` correctly, or the OSCAR
    CSV uses a different session ID format than expected.
    """
    assert len(matched_sessions) > 0, (
        "No sessions matched. Verify that session numbers in wmedf filenames "
        "match the Session column in the OSCAR Sessions CSV."
    )


# ---------------------------------------------------------------------------
# Tier 2 — Session matching (strict: ≥95%)
# ---------------------------------------------------------------------------

def test_session_match_rate(matched_sessions, oscar_sessions):
    """At least 95% of OSCAR sessions must be matched by our filtered output.

    This test guards against regressions in session selection logic.  The
    filtered adapter output (22 sessions as of 2026-05-23) should cover
    ≥95% of the 22 sessions OSCAR reports for the same device.

    Failure modes to investigate:
    - ``filter_sessions`` min_duration threshold changed or removed
    - Session boundary detection changed in the Rust parser
    - ``_session_number()`` no longer extracts the correct field from file_type
    - The OSCAR CSV was re-exported with a different session ID column format
    """
    oscar_count = len(oscar_sessions)
    if oscar_count == 0:
        pytest.skip("No OSCAR sessions loaded — check CSV fixture.")

    matched_count = len(matched_sessions)
    rate = matched_count / oscar_count
    assert rate >= _SESSION_MATCH_RATE_THRESHOLD, (
        f"Session match rate {rate:.1%} < {_SESSION_MATCH_RATE_THRESHOLD:.0%} "
        f"({matched_count}/{oscar_count} OSCAR sessions matched). "
        "Check filter_sessions threshold and session ID extraction."
    )


# ---------------------------------------------------------------------------
# Tier 3 — Signal accuracy (current baselines, expected to improve)
# ---------------------------------------------------------------------------

def test_waveform_stats_pass_rate(matched_sessions):
    """At least 25% of matched sessions must pass the EPAP/leak tolerance check.

    Current accuracy: 27.3% (6/22 sessions within ±0.5 hPa on EPAP p50/p95).

    The gap to 100% is caused by therapy-mode-dependent signal selection:
    EPAPsoll is used as mask_pressure, which is correct for CPAP mode but
    diverges for BiPAP/ST sessions that use a different pressure signal.
    The Rust decoder already filters EPAPsoll to 2–30 hPa to remove sentinels.

    Raise this threshold only after implementing per-mode signal routing in the
    Rust parser (src/parsers/prisma_line.rs).
    """
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
    assert rate >= _WAVEFORM_PASS_RATE_THRESHOLD, (
        f"Waveform stats pass rate {rate:.1%} < {_WAVEFORM_PASS_RATE_THRESHOLD:.0%} "
        f"({passed}/{total} sessions within tolerance). "
        "If this regressed, check EPAPsoll signal selection in the Rust parser."
    )


def test_event_counts_pass_rate(lowenstein_data, oscar_events_by_session, oscar_sessions):
    """At least 40% of sessions must have event counts within ±1 of OSCAR.

    Current accuracy: 45.5% (10/22 sessions within tolerance).

    The gap to 100% comes from event IDs in the device's therapy XML that are
    not yet mapped to our CPAPEvent.event_type vocabulary.  Sessions included
    in this comparison are those that appear in the OSCAR Sessions CSV (even if
    they have zero detail rows — zero is a valid OSCAR-reported count).

    Raise this threshold only after expanding event type coverage in the Rust
    parser's event XML decoder.
    """
    if not lowenstein_data.sessions:
        pytest.skip("No sessions found.")

    oscar_session_ids = {s.session_id for s in oscar_sessions}
    passed = 0
    total = 0
    for session in lowenstein_data.sessions:
        num = _session_number(session)
        if num is None:
            continue
        oscar_event_list = oscar_events_by_session.get(num, [])
        if not oscar_event_list and num not in oscar_session_ids:
            continue
        our_counts = count_events(session.events)
        oscar_counts = count_oscar_events(oscar_event_list)
        diff = compare_event_counts(our_counts, oscar_counts)
        total += 1
        if diff.within_tolerance:
            passed += 1

    if total == 0:
        pytest.skip("No sessions matched for event comparison.")

    rate = passed / total
    assert rate >= _EVENT_PASS_RATE_THRESHOLD, (
        f"Event count pass rate {rate:.1%} < {_EVENT_PASS_RATE_THRESHOLD:.0%} "
        f"({passed}/{total} sessions within tolerance). "
        "If this regressed, check event type mapping in the Rust parser."
    )
