"""Conformance tests for the ResMed adapter against the anonymized AirSense 10 fixture.

Covers all four bugs documented in issue #28:
  Bug 1 - Waveform timestamps start at Unix epoch (1970)
  Bug 2 - Usage duration disagrees with OSCAR
  Bug 3 - Serial number returns "Unknown"
  Bug 4 - Ghost sessions from full STR history
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

# DATALOG dates present in the fixture (directory names under DATALOG/)
_DATALOG_DATES = {"2026-05-06", "2026-05-17", "2026-05-28"}


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


def _read_oscar_sessions():
    rows = []
    with OSCAR_SESSIONS.open(newline="") as f:
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
    def test_summary_reported_usage_present(self, parsed_no_ts):
        for s in parsed_no_ts.daily_summaries:
            assert s.summary_reported_usage is not None, (
                f"summary_reported_usage is None for {s.date}"
            )

    def test_computed_usage_present_for_datalog_dates(self, parsed_no_ts):
        for s in parsed_no_ts.daily_summaries:
            date_str = s.date.isoformat()
            if date_str in _DATALOG_DATES:
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
        """2026-05-06 has 4 sessions; computed_usage must match OSCAR within 60 s."""
        oscar_rows = _read_oscar_summary()
        oscar_by_date = {row["Date"]: row for row in oscar_rows}
        oscar_row = oscar_by_date.get("2026-05-06")
        assert oscar_row is not None, "OSCAR summary missing 2026-05-06"
        oscar_seconds = _hms_to_seconds(oscar_row["Total Time"])

        summary = next(
            (s for s in parsed_no_ts.daily_summaries if s.date.isoformat() == "2026-05-06"),
            None,
        )
        assert summary is not None, "Parser returned no summary for 2026-05-06"
        assert summary.computed_usage is not None, "computed_usage is None for 2026-05-06"

        computed_seconds = summary.computed_usage * 3600.0
        diff = abs(computed_seconds - oscar_seconds)
        assert diff <= 60.0, (
            f"computed_usage for 2026-05-06 differs from OSCAR by {diff:.0f}s "
            f"(computed={computed_seconds:.0f}s, oscar={oscar_seconds:.0f}s)"
        )

    def test_all_datalog_dates_computed_usage_within_60s_of_oscar(self, parsed_no_ts):
        oscar_rows = _read_oscar_summary()
        oscar_by_date = {row["Date"]: row for row in oscar_rows}
        summaries_by_date = {s.date.isoformat(): s for s in parsed_no_ts.daily_summaries}

        for date_str in _DATALOG_DATES:
            oscar_row = oscar_by_date.get(date_str)
            if oscar_row is None:
                continue
            summary = summaries_by_date.get(date_str)
            if summary is None or summary.computed_usage is None:
                continue
            oscar_seconds = _hms_to_seconds(oscar_row["Total Time"])
            computed_seconds = summary.computed_usage * 3600.0
            diff = abs(computed_seconds - oscar_seconds)
            assert diff <= 60.0, (
                f"computed_usage for {date_str} differs from OSCAR by {diff:.0f}s"
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
        """A directory with a DATALOG but no identity file must not return 'Unknown'."""
        (tmp_path / "DATALOG").mkdir()
        # Create a minimal STR.edf to pass can_handle (not needed for machine info)
        adapter = ResMedAdapter()
        result = adapter.extract_and_map(tmp_path)
        assert result.machine.serial_number != "Unknown", (
            "serial_number must not fall back to 'Unknown' when no identity file exists"
        )


# ── Bug 4: Ghost sessions ─────────────────────────────────────────────────────

class TestGhostSessions:
    def test_has_detailed_data_true_for_datalog_dates(self, parsed_no_ts):
        summaries_by_date = {s.date.isoformat(): s for s in parsed_no_ts.daily_summaries}
        for date_str in _DATALOG_DATES:
            s = summaries_by_date.get(date_str)
            assert s is not None, f"No summary found for DATALOG date {date_str}"
            assert s.has_detailed_data is True, (
                f"has_detailed_data should be True for DATALOG date {date_str}"
            )

    def test_has_detailed_data_false_for_str_only_dates(self, parsed_no_ts):
        for s in parsed_no_ts.daily_summaries:
            if s.date.isoformat() not in _DATALOG_DATES:
                assert s.has_detailed_data is False, (
                    f"has_detailed_data should be False for STR-only date {s.date}"
                )

    def test_str_only_count_exceeds_detailed_count(self, parsed_no_ts):
        detailed = sum(1 for s in parsed_no_ts.daily_summaries if s.has_detailed_data)
        total = len(parsed_no_ts.daily_summaries)
        assert total > detailed, (
            f"Expected more total summaries ({total}) than detailed ({detailed})"
        )

    def test_total_summary_count_matches_oscar(self, parsed_no_ts):
        oscar_rows = _read_oscar_summary()
        assert len(parsed_no_ts.daily_summaries) == len(oscar_rows), (
            f"Expected {len(oscar_rows)} summaries (one per OSCAR row), "
            f"got {len(parsed_no_ts.daily_summaries)}"
        )
