from pathlib import Path

import pytest

from open_cpap_parser.adapters.base import UnsupportedDirectoryError
from open_cpap_parser.adapters.resmed import ResMedAdapter
from open_cpap_parser.core import create_parser, UniversalCPAPParser

HAS_REAL_DATA = Path("/home/camden/ZedProjects/sleepData/tmpdata/cam").is_dir()


def _real_data() -> Path:
    return Path("/home/camden/ZedProjects/sleepData/tmpdata/cam")


@pytest.fixture
def adapter() -> ResMedAdapter:
    return ResMedAdapter()


@pytest.fixture
def parser() -> UniversalCPAPParser:
    return create_parser()


@pytest.fixture
def empty_dir(tmp_path: Path) -> Path:
    return tmp_path / "empty"
    (tmp_path / "empty").mkdir()


class TestResMedCanHandle:
    def test_detects_resmed_directory(self, adapter: ResMedAdapter):
        if not HAS_REAL_DATA:
            pytest.skip("Real ResMed test data not available")
        assert adapter.can_handle(_real_data()) is True

    def test_rejects_directory_without_datalog(self, adapter: ResMedAdapter, tmp_path: Path):
        assert adapter.can_handle(tmp_path) is False

    def test_rejects_nonexistent_directory(self, adapter: ResMedAdapter):
        assert adapter.can_handle(Path("/nonexistent")) is False


class TestResMedExtraction:
    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_machine_info(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(_real_data())
        machine = result.machine
        assert machine.serial_number == "23233254908"
        assert machine.model == "AirSense11AutoSet"
        assert machine.series == "AirSense11"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_daily_summaries_present(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(_real_data())
        assert len(result.daily_summaries) > 0
        for s in result.daily_summaries[:5]:
            assert s.date is not None

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_daily_summaries_have_ahi(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(_real_data())
        non_zero = [s for s in result.daily_summaries if s.ahi > 0]
        assert len(non_zero) > 0

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_sessions_present(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(_real_data())
        assert len(result.sessions) > 0

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_session_file_types(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(_real_data())
        file_types = {s.file_type for s in result.sessions}
        # BRP+PLD are merged into one session; EVE is no longer a standalone session type
        assert "BRP+PLD" in file_types or "BRP" in file_types
        assert "EVE" not in file_types

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_session_start_time(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(_real_data())
        for session in result.sessions[:5]:
            assert session.start_time is not None

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_session_duration(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(_real_data())
        positive = [s for s in result.sessions if s.duration_minutes > 0]
        assert len(positive) > 0


class TestTimeSeries:
    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_timeseries_excluded_by_default(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(_real_data(), include_timeseries=False)
        for session in result.sessions[:5]:
            assert session.timeseries is None

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_timeseries_included_when_requested(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(_real_data(), include_timeseries=True)
        merged = [s for s in result.sessions if s.file_type == "BRP+PLD"]
        assert len(merged) > 0
        ts = merged[0].timeseries
        assert ts is not None
        assert len(ts.flow_rate) > 0

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_merged_session_has_both_tracks(self, adapter: ResMedAdapter):
        """BRP+PLD sessions expose high-rate flow and low-rate therapy signals."""
        result = adapter.extract_and_map(_real_data(), include_timeseries=True)
        merged = [s for s in result.sessions if s.file_type == "BRP+PLD"]
        assert len(merged) > 0
        ts = merged[0].timeseries
        assert ts is not None
        # High-rate BRP track
        assert len(ts.flow_rate) > 1000, "expected ~162k samples at 25 Hz"
        assert len(ts.timestamps) == len(ts.flow_rate)
        # Low-rate PLD track
        assert len(ts.mask_pressure) > 0, "PLD mask_pressure should be populated"
        assert len(ts.timestamps_low) == len(ts.mask_pressure)

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_merged_session_sample_rates_differ(self, adapter: ResMedAdapter):
        """High-rate track should have ~50x more samples than low-rate track."""
        result = adapter.extract_and_map(_real_data(), include_timeseries=True)
        merged = [s for s in result.sessions if s.file_type == "BRP+PLD"]
        assert len(merged) > 0
        ts = merged[0].timeseries
        assert ts is not None
        ratio = len(ts.flow_rate) / len(ts.mask_pressure)
        assert 40 < ratio < 60, f"expected ~50x ratio, got {ratio:.1f}"

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_merged_session_pld_signals(self, adapter: ResMedAdapter):
        """Merged session should carry all PLD signals on low-rate track."""
        result = adapter.extract_and_map(_real_data(), include_timeseries=True)
        merged = [s for s in result.sessions if s.file_type == "BRP+PLD"]
        assert len(merged) > 0
        ts = merged[0].timeseries
        assert ts is not None
        assert len(ts.leak) > 0
        assert len(ts.respiratory_rate) > 0
        assert len(ts.tidal_volume) > 0


class TestWaveformOnlyFilter:
    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_waveform_only_reduces_summaries(self, parser: UniversalCPAPParser):
        all_data = parser.parse(_real_data(), waveform_only=False)
        filtered = parser.parse(_real_data(), waveform_only=True)
        assert len(filtered.daily_summaries) <= len(all_data.daily_summaries)
        assert len(filtered.daily_summaries) > 0

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_waveform_only_dates_match_sessions(self, parser: UniversalCPAPParser):
        result = parser.parse(_real_data(), waveform_only=True)
        session_dates = {s.start_time.date() for s in result.sessions}
        for summary in result.daily_summaries:
            assert summary.date in session_dates


class TestCore:
    def test_unsupported_directory_raises(self, tmp_path: Path):
        p = UniversalCPAPParser()
        with pytest.raises(UnsupportedDirectoryError):
            p.parse(tmp_path)

    def test_nonexistent_directory_raises(self, parser: UniversalCPAPParser):
        with pytest.raises(NotADirectoryError):
            parser.parse("/nonexistent/path")

    @pytest.mark.skipif(not HAS_REAL_DATA, reason="Real ResMed test data not available")
    def test_round_trip_json(self, adapter: ResMedAdapter):
        result = adapter.extract_and_map(_real_data())
        js = result.model_dump_json(indent=2, exclude_none=True)
        assert isinstance(js, str)
        assert len(js) > 100
        assert '"serial_number": "23233254908"' in js
