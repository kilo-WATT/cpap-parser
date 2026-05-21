"""Waveform-derived statistics comparison against OSCAR session-level values.

Computes pressure and leak percentiles from TimeSeriesData arrays and
diffs them against the corresponding OSCAR Sessions CSV columns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from open_cpap_parser.schema import TimeSeriesData
from open_cpap_parser.validation.oscar_reader import OscarSession


@dataclass(frozen=True)
class WaveformTolerances:
    """Acceptable deviation thresholds for waveform-derived stats."""
    pressure: float = 0.5
    leak: float = 2.0


DEFAULT_WAVEFORM_TOLERANCES = WaveformTolerances()


@dataclass
class WaveformDiff:
    """Comparison result for waveform-derived stats vs OSCAR session values."""
    session_id: str
    epap_50_delta: Optional[float]
    epap_95_delta: Optional[float]
    leak_50_delta: Optional[float]
    leak_95_delta: Optional[float]
    within_tolerance: bool


def compute_waveform_stats(ts: TimeSeriesData) -> dict[str, Optional[float]]:
    """Compute percentile statistics from TimeSeriesData signal arrays.

    Uses ts.mask_pressure (PLD MaskPress.2s) for EPAP and ts.leak for leak.
    Returns None values when the signal array is empty.
    """
    def _pct(arr: list[float], q: float) -> Optional[float]:
        if not arr:
            return None
        return float(np.percentile(arr, q))

    return {
        "epap_50": _pct(ts.mask_pressure, 50),
        "epap_95": _pct(ts.mask_pressure, 95),
        "leak_50": _pct(ts.leak, 50),
        "leak_95": _pct(ts.leak, 95),
    }


def compare_waveform(
    ts: TimeSeriesData,
    oscar_session: OscarSession,
    tolerances: WaveformTolerances = DEFAULT_WAVEFORM_TOLERANCES,
) -> WaveformDiff:
    """Compare waveform-derived stats against an OSCAR session record."""
    stats = compute_waveform_stats(ts)

    def _delta(parser_val: Optional[float], oscar_val: Optional[float]) -> Optional[float]:
        if parser_val is None or oscar_val is None:
            return None
        return abs(parser_val - oscar_val)

    epap_50_delta = _delta(stats["epap_50"], oscar_session.epap_50)
    epap_95_delta = _delta(stats["epap_95"], oscar_session.epap_95)
    leak_50_delta = _delta(stats["leak_50"], oscar_session.leak_50)
    leak_95_delta = _delta(stats["leak_95"], oscar_session.leak_95)

    within = all(
        d is None or d <= threshold
        for d, threshold in (
            (epap_50_delta, tolerances.pressure),
            (epap_95_delta, tolerances.pressure),
            (leak_50_delta, tolerances.leak),
            (leak_95_delta, tolerances.leak),
        )
    )

    return WaveformDiff(
        session_id=oscar_session.session_id,
        epap_50_delta=epap_50_delta,
        epap_95_delta=epap_95_delta,
        leak_50_delta=leak_50_delta,
        leak_95_delta=leak_95_delta,
        within_tolerance=within,
    )
