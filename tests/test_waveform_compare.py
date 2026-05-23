import pytest
import numpy as np

from cpap_parser.schema import TimeSeriesData
from cpap_parser.validation.oscar_reader import OscarSession
from cpap_parser.validation.waveform_compare import (
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
    ts = _make_ts(mask_pressure=[4.5] * 200)
    session = _make_session(epap_50=None, epap_95=None)
    diff = compare_waveform(ts, session)
    assert diff.epap_50_delta is None
    assert diff.within_tolerance
