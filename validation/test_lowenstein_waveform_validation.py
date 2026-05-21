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
    raise NotImplementedError("Implement session matching once waveform extraction is done.")


def test_event_counts_pass_rate(sample_paths, oscar_details_csv):
    """Löwenstein event counts within ±1 of OSCAR Details CSV."""
    raise NotImplementedError("Implement once waveform extraction is done.")
