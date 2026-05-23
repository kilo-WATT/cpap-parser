"""Tests for the Löwenstein Medical adapter."""

import pytest
from datetime import datetime, timezone
from pathlib import Path

from cpap_parser.schema import CPAPSession, TimeSeriesData
from cpap_parser.utils.session_filter import filter_sessions, stitch_sessions


def _make_session(start_min: float, end_min: float, n_events: int = 0) -> CPAPSession:
    """Build a minimal CPAPSession for filter/stitch tests."""
    epoch = datetime(2024, 1, 1, tzinfo=timezone.utc)
    from datetime import timedelta
    start = epoch + timedelta(minutes=start_min)
    end = epoch + timedelta(minutes=end_min)
    return CPAPSession(
        start_time=start,
        end_time=end,
        duration_minutes=end_min - start_min,
        file_type="PrismaLine/1",
        sample_rate=0.0,
    )


class TestFilterSessions:
    def test_keeps_sessions_at_threshold(self):
        s = _make_session(0, 4.5)
        assert filter_sessions([s]) == [s]

    def test_drops_sessions_below_threshold(self):
        short = _make_session(0, 3.0)
        assert filter_sessions([short]) == []

    def test_filters_mixed_list(self):
        short = _make_session(0, 3.7)
        long_ = _make_session(10, 20)
        result = filter_sessions([short, long_])
        assert result == [long_]

    def test_empty_input(self):
        assert filter_sessions([]) == []

    def test_custom_threshold(self):
        s = _make_session(0, 3.0)
        assert filter_sessions([s], min_duration_minutes=2.0) == [s]
        assert filter_sessions([s], min_duration_minutes=5.0) == []


class TestStitchSessions:
    def test_single_session_unchanged(self):
        s = _make_session(0, 30)
        assert stitch_sessions([s]) == [s]

    def test_merges_adjacent_within_gap(self):
        s1 = _make_session(0, 30)
        s2 = _make_session(35, 60)  # 5-min gap
        result = stitch_sessions([s1, s2], max_gap_minutes=30.0)
        assert len(result) == 1
        assert result[0].duration_minutes == pytest.approx(60.0)

    def test_keeps_separate_beyond_gap(self):
        s1 = _make_session(0, 30)
        s2 = _make_session(100, 120)  # 70-min gap
        result = stitch_sessions([s1, s2], max_gap_minutes=30.0)
        assert len(result) == 2

    def test_merges_events_from_all_sessions(self):
        from cpap_parser.schema import CPAPEvent
        ev1 = CPAPEvent(timestamp_sec=0.0, event_type="OA", duration_sec=10.0)
        ev2 = CPAPEvent(timestamp_sec=100.0, event_type="H", duration_sec=5.0)
        s1 = CPAPSession(
            start_time=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 1, 0, 30, tzinfo=timezone.utc),
            duration_minutes=30,
            file_type="PrismaLine/1",
            events=[ev1],
        )
        s2 = CPAPSession(
            start_time=datetime(2024, 1, 1, 0, 35, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc),
            duration_minutes=25,
            file_type="PrismaLine/2",
            events=[ev2],
        )
        result = stitch_sessions([s1, s2], max_gap_minutes=30.0)
        assert len(result[0].events) == 2

    def test_merges_timeseries(self):
        # Use realistic UTC epoch anchors: s1 starts at 2024-01-01T00:00Z,
        # s2 starts at 2024-01-01T00:35Z.  Timestamps must remain monotonically
        # increasing after stitching (absolute UTC epoch seconds guarantee this).
        epoch_s1 = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp()
        epoch_s2 = datetime(2024, 1, 1, 0, 35, tzinfo=timezone.utc).timestamp()
        ts1 = TimeSeriesData(
            timestamps=[epoch_s1, epoch_s1 + 1.0],
            flow_rate=[0.1, 0.2],
        )
        ts2 = TimeSeriesData(
            timestamps=[epoch_s2, epoch_s2 + 1.0],
            flow_rate=[0.3, 0.4],
        )
        s1 = CPAPSession(
            start_time=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 1, 0, 30, tzinfo=timezone.utc),
            duration_minutes=30,
            file_type="PrismaLine/1",
            timeseries=ts1,
        )
        s2 = CPAPSession(
            start_time=datetime(2024, 1, 1, 0, 35, tzinfo=timezone.utc),
            end_time=datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc),
            duration_minutes=25,
            file_type="PrismaLine/2",
            timeseries=ts2,
        )
        result = stitch_sessions([s1, s2], max_gap_minutes=30.0)
        assert result[0].timeseries is not None
        merged_ts = result[0].timeseries.timestamps
        assert merged_ts == [epoch_s1, epoch_s1 + 1.0, epoch_s2, epoch_s2 + 1.0]
        assert merged_ts == sorted(merged_ts), "stitched timestamps must be monotonically increasing"
        assert result[0].timeseries.flow_rate == [0.1, 0.2, 0.3, 0.4]

    def test_empty_input(self):
        assert stitch_sessions([]) == []

HAS_SAMPLE = Path("~/ZedProjects/sleepData/loweinstein-sample/ExampleFiles").expanduser().is_dir()


@pytest.mark.skipif(not HAS_SAMPLE, reason="Löwenstein sample data not available")
def test_rust_parse_prisma_line_callable():
    from cpap_parser import _rust_parsers
    path = str(Path("~/ZedProjects/sleepData/loweinstein-sample/ExampleFiles").expanduser())
    result = _rust_parsers.parse_prisma_line(path, False)
    assert result is not None
    assert len(result.daily_summaries) > 0
    assert len(result.sessions) > 0


@pytest.mark.skipif(
    not Path("~/ZedProjects/sleepData/loweinstein-sample/ExampleFiles").expanduser().is_dir(),
    reason="Löwenstein sample data not available",
)
class TestPrismaLineTimeSeries:
    def test_session_has_timeseries_when_requested(self):
        from cpap_parser.adapters.lowenstein import LowensteinAdapter
        adapter = LowensteinAdapter()
        data_path = Path("~/ZedProjects/sleepData/loweinstein-sample/ExampleFiles").expanduser()
        result = adapter.extract_and_map(data_path, include_timeseries=True)
        sessions_with_ts = [s for s in result.sessions if s.timeseries is not None]
        assert len(sessions_with_ts) > 0, "No sessions have timeseries data"

    def test_timeseries_has_flow_rate(self):
        from cpap_parser.adapters.lowenstein import LowensteinAdapter
        adapter = LowensteinAdapter()
        data_path = Path("~/ZedProjects/sleepData/loweinstein-sample/ExampleFiles").expanduser()
        result = adapter.extract_and_map(data_path, include_timeseries=True)
        ts_sessions = [s for s in result.sessions if s.timeseries is not None]
        assert len(ts_sessions) > 0
        ts = ts_sessions[0].timeseries
        assert len(ts.flow_rate) > 0, "flow_rate should have samples"

    def test_timeseries_has_mask_pressure(self):
        from cpap_parser.adapters.lowenstein import LowensteinAdapter
        adapter = LowensteinAdapter()
        data_path = Path("~/ZedProjects/sleepData/loweinstein-sample/ExampleFiles").expanduser()
        result = adapter.extract_and_map(data_path, include_timeseries=True)
        ts_sessions = [s for s in result.sessions if s.timeseries is not None]
        assert len(ts_sessions) > 0
        ts = ts_sessions[0].timeseries
        assert len(ts.mask_pressure) > 0, "mask_pressure (EPAPsoll) should have samples"
