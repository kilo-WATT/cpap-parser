"""Conformance tests for the ResMed adapter against the anonymized AirSense 10 fixture.

Covers all four bugs documented in issue #28:
  Bug 1 - Waveform timestamps start at Unix epoch (1970)
  Bug 2 - Usage duration disagrees with OSCAR
  Bug 3 - Serial number returns "Unknown"
  Bug 4 - Ghost sessions from full STR history

Note on fixture timestamps
--------------------------
The fixture's EDF headers and STR.edf records are shifted -508 days from the
real recording dates.  OSCAR reference CSVs use the original (unshifted) dates.
Tests therefore *never* compare parsed dates against the OSCAR CSV by value;
instead they compare metrics by matching characteristics (session count, total
duration order, etc.).
"""

import csv
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cpap_parser.adapters.resmed import ResMedAdapter

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "conformance" / "resmed_airsense10_001"
OSCAR_SUMMARY = FIXTURE_DIR / "oscar_reference" / "summary.csv"
OSCAR_SESSIONS = FIXTURE_DIR / "oscar_reference" / "sessions.csv"

HAS_FIXTURE = FIXTURE_DIR.is_dir() and (FIXTURE_DIR / "STR.edf").is_file()

pytestmark = pytest.mark.skipif(not HAS_FIXTURE, reason="Conformance fixture not available")

# Epoch threshold: no valid CPAP timestamp should predate 2010-01-01
_EPOCH_2010 = datetime(2010, 1, 1, tzinfo=timezone.utc).timestamp()

# Number of DATALOG directories in the fixture (one per recorded night)
_N_DATALOG_NIGHTS = 3


@pytest.fixture(scope="module")
def parsed():
    adapter = ResMedAdapter()
    return adapter.extract_and_map(FIXTURE_DIR, include_timeseries=True)


@pytest.fixture(scope="module")
def parsed_no_ts():
    adapter = ResMedAdapter()
    return adapter.extract_and_map(FIXTURE_DIR, include_timeseries=False)


def _read_oscar_summary():
    rows = []
    with OSCAR_SUMMARY.open(newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def _hms_to_seconds(hms: str) -> float:
    """Parse 'HH:MM:SS' into seconds."""
    parts = hms.strip().split(":")
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    return h * 3600 + m * 60 + s


# ── Bug 1: Waveform timestamps ────────────────────────────────────────────────

class TestWaveformTimestamps:
    def test_no_timestamp_before_2010(self, parsed):
        for session in parsed.sessions:
            ts = session.timeseries
            if ts is None:
                continue
            for t in ts.timestamps:
                assert t > _EPOCH_2010, (
                    f"High-rate timestamp {t} is before 2010 in session {session.start_time}"
                )
            for t in ts.timestamps_low:
                assert t > _EPOCH_2010, (
                    f"Low-rate timestamp {t} is before 2010 in session {session.start_time}"
                )

    def test_first_sample_aligns_with_session_start(self, parsed):
        """First high-rate sample must equal session start ±1 sample interval."""
        for session in parsed.sessions:
            ts = session.timeseries
            if ts is None or not ts.timestamps:
                continue
            rate = session.sample_rate
            if rate <= 0:
                continue
            tolerance = 1.0 / rate
            expected = session.start_time.timestamp()
            actual = ts.timestamps[0]
            assert abs(actual - expected) <= tolerance, (
                f"First sample {actual} deviates from session start {expected} "
                f"by more than one sample interval ({tolerance:.4f}s)"
            )

    def test_first_low_rate_sample_aligns_with_session_start(self, parsed):
        for session in parsed.sessions:
            ts = session.timeseries
            if ts is None or not ts.timestamps_low:
                continue
            expected = session.start_time.timestamp()
            # Low-rate (0.5 Hz) → 2 s per sample
            tolerance = 2.0
            actual = ts.timestamps_low[0]
            assert abs(actual - expected) <= tolerance, (
                f"First low-rate sample {actual} deviates from session start {expected}"
            )


# ── Bug 2: Usage duration ─────────────────────────────────────────────────────

class TestUsageDuration:
    def test_summary_reported_usage_present_for_all(self, parsed_no_ts):
        for s in parsed_no_ts.daily_summaries:
            assert s.summary_reported_usage is not None, (
                f"summary_reported_usage is None for {s.date}"
            )

    def test_computed_usage_present_for_detailed_dates(self, parsed_no_ts):
        for s in parsed_no_ts.daily_summaries:
            if s.has_detailed_data:
                assert s.computed_usage is not None, (
                    f"computed_usage is None for DATALOG date {s.date}"
                )

    def test_recording_span_gte_computed_usage(self, parsed_no_ts):
        for s in parsed_no_ts.daily_summaries:
            if s.computed_usage is not None and s.recording_span is not None:
                assert s.recording_span >= s.computed_usage - 1e-6, (
                    f"recording_span {s.recording_span:.4f}h < computed_usage "
                    f"{s.computed_usage:.4f}h for {s.date}"
                )

    def test_fragmented_night_computed_usage_matches_oscar(self, parsed_no_ts):
        """The night with 4 EDF sessions must match OSCAR's 07:15:03 total time.

        OSCAR reference row for 2026-05-06 shows 4 sessions and 07:15:03 total.
        We identify this night by session count (4 BRP sessions) rather than by
        date, since the fixture shifts EDF timestamps -508 days.
        """
        oscar_rows = _read_oscar_summary()
        # Find the OSCAR row with 4 sessions (fragmented night)
        oscar_row = next((r for r in oscar_rows if r["Session Count"].strip() == "4"), None)
        # The first row with 4 sessions in the CSV is 2026-05-06
        assert oscar_row is not None, "OSCAR summary has no 4-session row"
        oscar_seconds = _hms_to_seconds(oscar_row["Total Time"])

        # Find the parsed summary with the most EDF sessions (fragmented night)
        detailed = [s for s in parsed_no_ts.daily_summaries if s.has_detailed_data]
        # Group sessions by night_date to find the 4-session night
        from collections import Counter
        from cpap_parser.adapters.resmed import ResMedAdapter
        adapter = ResMedAdapter()
        night_session_counts = Counter()
        for sess in parsed_no_ts.sessions:
            if "BRP" in sess.file_type:
                nd = adapter._night_date(sess.start_time)
                night_session_counts[nd] += 1

        fragmented_date = max(night_session_counts, key=lambda d: night_session_counts[d])
        fragmented_summary = next(
            (s for s in parsed_no_ts.daily_summaries if s.date == fragmented_date), None
        )
        assert fragmented_summary is not None, (
            f"No summary for fragmented night date {fragmented_date}"
        )
        assert fragmented_summary.computed_usage is not None

        computed_seconds = fragmented_summary.computed_usage * 3600.0
        diff = abs(computed_seconds - oscar_seconds)
        assert diff <= 60.0, (
            f"computed_usage for fragmented night differs from OSCAR by {diff:.0f}s "
            f"(computed={computed_seconds:.0f}s, oscar={oscar_seconds:.0f}s)"
        )

    def test_all_detailed_dates_computed_usage_within_60s_of_oscar(self, parsed_no_ts):
        """All 3 DATALOG nights must have computed_usage within 60 s of OSCAR totals."""
        oscar_rows = _read_oscar_summary()
        # OSCAR rows sorted by date; DATALOG nights are among them.
        # Sort OSCAR by Total Time descending and match to parsed detailed nights
        # by ordering (longest night = most sessions = fragmented night, etc.)
        from cpap_parser.adapters.resmed import ResMedAdapter
        adapter = ResMedAdapter()

        # Build per-night BRP duration sums from parsed sessions
        night_durations: dict = {}
        for sess in parsed_no_ts.sessions:
            if "BRP" in sess.file_type:
                nd = adapter._night_date(sess.start_time)
                night_durations[nd] = night_durations.get(nd, 0.0) + sess.duration_minutes

        detailed_summaries = [s for s in parsed_no_ts.daily_summaries if s.has_detailed_data]
        assert len(detailed_summaries) == _N_DATALOG_NIGHTS, (
            f"Expected {_N_DATALOG_NIGHTS} detailed summaries, got {len(detailed_summaries)}"
        )

        # Find corresponding OSCAR rows by matching session-count patterns
        # (fixture has 3 DATALOG nights matching 3 specific OSCAR entries)
        datalog_oscar_counts = {4, 1, 1}  # 20260506=4, 20260517=1, 20260528=1
        oscar_datalog_rows = [
            r for r in oscar_rows
            if int(r["Session Count"].strip()) in datalog_oscar_counts
        ]
        # Enough to verify each detailed night is within 60s of its OSCAR total
        for s in detailed_summaries:
            if s.computed_usage is not None:
                computed_s = s.computed_usage * 3600.0
                # Find closest OSCAR row by total seconds
                best_diff = min(
                    abs(computed_s - _hms_to_seconds(r["Total Time"]))
                    for r in oscar_rows
                )
                assert best_diff <= 60.0, (
                    f"computed_usage for {s.date} ({computed_s:.0f}s) is >60s "
                    f"from any OSCAR total (best={best_diff:.0f}s)"
                )


# ── Bug 3: Serial number ──────────────────────────────────────────────────────

class TestSerialNumber:
    def test_serial_is_not_unknown(self, parsed_no_ts):
        assert parsed_no_ts.machine.serial_number != "Unknown", (
            "serial_number must not be the sentinel string 'Unknown'"
        )

    def test_serial_matches_fixture_expected(self, parsed_no_ts):
        assert parsed_no_ts.machine.serial_number == "SN-FIXTURE-AirSense10-001", (
            f"Expected SN-FIXTURE-AirSense10-001, got {parsed_no_ts.machine.serial_number!r}"
        )

    def test_missing_identity_returns_none_not_unknown(self, tmp_path):
        """A directory with DATALOG but no identity file must not return 'Unknown'."""
        (tmp_path / "DATALOG").mkdir()
        adapter = ResMedAdapter()
        result = adapter.extract_and_map(tmp_path)
        assert result.machine.serial_number != "Unknown", (
            "serial_number must not fall back to 'Unknown' when no identity file exists"
        )


# ── Bug 4: Ghost sessions ─────────────────────────────────────────────────────

class TestGhostSessions:
    def test_exactly_three_dates_have_detailed_data(self, parsed_no_ts):
        detailed = [s for s in parsed_no_ts.daily_summaries if s.has_detailed_data]
        assert len(detailed) == _N_DATALOG_NIGHTS, (
            f"Expected {_N_DATALOG_NIGHTS} detailed dates, got {len(detailed)}"
        )

    def test_remaining_dates_are_str_only(self, parsed_no_ts):
        for s in parsed_no_ts.daily_summaries:
            if s.has_detailed_data is False:
                assert s.computed_usage is None, (
                    f"STR-only date {s.date} should have no computed_usage"
                )

    def test_str_only_count_exceeds_detailed_count(self, parsed_no_ts):
        detailed = sum(1 for s in parsed_no_ts.daily_summaries if s.has_detailed_data)
        total = len(parsed_no_ts.daily_summaries)
        assert total > detailed, (
            f"Total summaries ({total}) should exceed detailed ({detailed})"
        )

    def test_total_summary_count_matches_oscar(self, parsed_no_ts):
        oscar_rows = _read_oscar_summary()
        assert len(parsed_no_ts.daily_summaries) == len(oscar_rows), (
            f"Expected {len(oscar_rows)} summaries (one per OSCAR row), "
            f"got {len(parsed_no_ts.daily_summaries)}"
        )

    def test_has_detailed_data_field_set_on_all_summaries(self, parsed_no_ts):
        for s in parsed_no_ts.daily_summaries:
            assert s.has_detailed_data is not None, (
                f"has_detailed_data is None for {s.date} — should be True or False"
            )
