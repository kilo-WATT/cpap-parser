from pathlib import Path

import pytest

from open_cpap_parser.adapters.base import UnsupportedDirectoryError
from open_cpap_parser.adapters.resmed import ResMedAdapter
from open_cpap_parser.core import create_parser, UniversalCPAPParser

REAL_DATA = Path.home() / "ZedProjects/sleepData/tmpdata/cam"
EMPTY_DIR = Path.home() / "ZedProjects/open-cpap-parser/tests"


@pytest.fixture
def adapter() -> ResMedAdapter:
    return ResMedAdapter()


@pytest.fixture
def parser() -> UniversalCPAPParser:
    return create_parser()


class TestResMedCanHandle:
    def test_detects_resmed_directory(self, adapter: ResMedAdapter):
        assert adapter.can_handle(REAL_DATA) is True

    def test_rejects_empty_directory(self, adapter: ResMedAdapter):
        assert adapter.can_handle(EMPTY_DIR) is False

    def test_rejects_nonexistent_directory(self, adapter: ResMedAdapter):
        assert adapter.can_handle(Path("/nonexistent")) is False


class TestResMedExtraction:
    def test_machine_info(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(REAL_DATA)
        machine = result.machine
        assert machine.serial_number == "23233254908"
        assert machine.model == "AirSense11AutoSet"
        assert machine.series == "AirSense11"

    def test_daily_summaries_present(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(REAL_DATA)
        assert len(result.daily_summaries) > 0
        for s in result.daily_summaries[:5]:
            assert s.date is not None

    def test_daily_summaries_have_ahi(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(REAL_DATA)
        non_zero = [s for s in result.daily_summaries if s.ahi > 0]
        assert len(non_zero) > 0, "Expected at least one day with AHI > 0"

    def test_sessions_present(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(REAL_DATA)
        assert len(result.sessions) > 0

    def test_edf_event_parsing_known_format(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(REAL_DATA)
        eve_sessions = [s for s in result.sessions if s.file_type == "EVE"]
        assert len(eve_sessions) > 0, "Expected EVE sessions to be present"
        for session in eve_sessions[:3]:
            for event in session.events[:5]:
                assert event.timestamp_sec >= 0
                assert isinstance(event.event_type, str)
                assert len(event.event_type) > 0

    def test_session_events_structured(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(REAL_DATA)
        eve_sessions = [s for s in result.sessions if s.file_type == "EVE"]
        for session in eve_sessions[:3]:
            for event in session.events[:5]:
                assert event.timestamp_sec >= 0
                assert isinstance(event.event_type, str)
                assert len(event.event_type) > 0

    def test_session_file_types(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(REAL_DATA)
        file_types = {s.file_type for s in result.sessions}
        assert "BRP" in file_types, "Expected BRP (breathing) file type"
        assert "EVE" in file_types, "Expected EVE (events) file type"

    def test_session_start_time(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(REAL_DATA)
        for session in result.sessions[:5]:
            assert session.start_time is not None

    def test_session_duration(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(REAL_DATA)
        positive = [s for s in result.sessions if s.duration_minutes > 0]
        assert len(positive) > 0


class TestTimeSeries:
    def test_timeseries_excluded_by_default(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(REAL_DATA, include_timeseries=False)
        for session in result.sessions[:5]:
            assert session.timeseries is None

    def test_timeseries_included_when_requested(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(REAL_DATA, include_timeseries=True)
        brp_sessions = [s for s in result.sessions if s.file_type == "BRP"]
        assert len(brp_sessions) > 0, "Expected at least one BRP session"
        ts = brp_sessions[0].timeseries
        assert ts is not None
        assert len(ts.flow_rate) > 0, "Expected flow rate data in BRP session"


class TestWaveformOnlyFilter:
    def test_waveform_only_reduces_summaries(self, parser: UniversalCPAPParser):
        all_data = parser.parse(REAL_DATA, waveform_only=False)
        filtered = parser.parse(REAL_DATA, waveform_only=True)

        assert len(filtered.daily_summaries) <= len(all_data.daily_summaries)
        assert len(filtered.daily_summaries) > 0

    def test_waveform_only_dates_match_sessions(self, parser: UniversalCPAPParser):
        result = parser.parse(REAL_DATA, waveform_only=True)
        session_dates = {s.start_time.date() for s in result.sessions}
        for summary in result.daily_summaries:
            assert summary.date in session_dates


class TestCore:
    def test_unsupported_directory_raises(self):
        p = UniversalCPAPParser()
        with pytest.raises(UnsupportedDirectoryError):
            p.parse(EMPTY_DIR)

    def test_nonexistent_directory_raises(self, parser: UniversalCPAPParser):
        with pytest.raises(NotADirectoryError):
            parser.parse("/nonexistent/path")

    def test_round_trip_json(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(REAL_DATA)
        js = result.model_dump_json(indent=2, exclude_none=True)
        assert isinstance(js, str)
        assert len(js) > 100
        assert '"serial_number": "23233254908"' in js
