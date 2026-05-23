"""Tests for the Löwenstein Medical adapter."""

import pytest
from pathlib import Path

HAS_SAMPLE = Path("~/ZedProjects/sleepData/loweinstein-sample/ExampleFiles").expanduser().is_dir()


@pytest.mark.skipif(not HAS_SAMPLE, reason="Löwenstein sample data not available")
def test_rust_parse_prisma_line_callable():
    from open_cpap_parser import _rust_parsers
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
        from open_cpap_parser.adapters.lowenstein import LowensteinAdapter
        adapter = LowensteinAdapter()
        data_path = Path("~/ZedProjects/sleepData/loweinstein-sample/ExampleFiles").expanduser()
        result = adapter.extract_and_map(data_path, include_timeseries=True)
        sessions_with_ts = [s for s in result.sessions if s.timeseries is not None]
        assert len(sessions_with_ts) > 0, "No sessions have timeseries data"

    def test_timeseries_has_flow_rate(self):
        from open_cpap_parser.adapters.lowenstein import LowensteinAdapter
        adapter = LowensteinAdapter()
        data_path = Path("~/ZedProjects/sleepData/loweinstein-sample/ExampleFiles").expanduser()
        result = adapter.extract_and_map(data_path, include_timeseries=True)
        ts_sessions = [s for s in result.sessions if s.timeseries is not None]
        assert len(ts_sessions) > 0
        ts = ts_sessions[0].timeseries
        assert len(ts.flow_rate) > 0, "flow_rate should have samples"

    def test_timeseries_has_mask_pressure(self):
        from open_cpap_parser.adapters.lowenstein import LowensteinAdapter
        adapter = LowensteinAdapter()
        data_path = Path("~/ZedProjects/sleepData/loweinstein-sample/ExampleFiles").expanduser()
        result = adapter.extract_and_map(data_path, include_timeseries=True)
        ts_sessions = [s for s in result.sessions if s.timeseries is not None]
        assert len(ts_sessions) > 0
        ts = ts_sessions[0].timeseries
        assert len(ts.mask_pressure) > 0, "mask_pressure (EPAPsoll) should have samples"
