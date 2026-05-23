"""Session filtering and stitching utilities.

Implements OSCAR-compatible session filtering and merging:

- ``filter_sessions``: drops sessions below a minimum duration threshold,
  matching OSCAR's behaviour of excluding short setup/calibration recordings.
- ``stitch_sessions``: merges adjacent sessions whose gap is small enough to
  be considered one continuous sleep period.
"""
from __future__ import annotations

from datetime import datetime, timezone

from open_cpap_parser.schema import CPAPSession, TimeSeriesData


def filter_sessions(
    sessions: list[CPAPSession],
    min_duration_minutes: float = 4.5,
) -> list[CPAPSession]:
    """Return only sessions at or above *min_duration_minutes*.

    OSCAR excludes sessions shorter than ~4.5 minutes (setup/calibration
    recordings).  The clean separation in Löwenstein Prisma Line data:
    longest excluded = 3.7 min, shortest OSCAR-matched = 4.9 min.
    """
    return [s for s in sessions if s.duration_minutes >= min_duration_minutes]


def stitch_sessions(
    sessions: list[CPAPSession],
    max_gap_minutes: float = 30.0,
) -> list[CPAPSession]:
    """Merge adjacent sessions whose gap does not exceed *max_gap_minutes*.

    Sessions are assumed to be in chronological order.  When two consecutive
    sessions are within *max_gap_minutes* of each other they are combined into
    one: start from the first, end from the last, events concatenated, and
    timeseries tracks concatenated.  Both timestamp tracks (``timestamps`` and
    ``timestamps_low``) must be absolute UTC epoch seconds — as guaranteed by
    all adapters — so concatenation produces a monotonically increasing sequence
    with no re-basing.
    """
    if not sessions:
        return []

    sorted_sessions = sorted(sessions, key=lambda s: s.start_time)
    groups: list[list[CPAPSession]] = [[sorted_sessions[0]]]

    for session in sorted_sessions[1:]:
        last = groups[-1][-1]
        gap = (session.start_time - last.end_time).total_seconds() / 60.0
        if gap <= max_gap_minutes:
            groups[-1].append(session)
        else:
            groups.append([session])

    return [_merge_group(g) for g in groups]


def _merge_group(group: list[CPAPSession]) -> CPAPSession:
    if len(group) == 1:
        return group[0]

    first = group[0]
    last = group[-1]
    start = first.start_time
    end = last.end_time
    duration = (end - start).total_seconds() / 60.0
    events = [e for s in group for e in s.events]

    ts_list = [s.timeseries for s in group if s.timeseries is not None]
    timeseries = _merge_timeseries(ts_list) if ts_list else None

    return CPAPSession(
        start_time=start,
        end_time=end,
        duration_minutes=duration,
        file_type=first.file_type,
        sample_rate=first.sample_rate,
        events=events,
        timeseries=timeseries,
    )


def _merge_timeseries(ts_list: list[TimeSeriesData]) -> TimeSeriesData:
    def concat(attr: str) -> list[float]:
        return [v for ts in ts_list for v in getattr(ts, attr)]

    return TimeSeriesData(
        timestamps=concat("timestamps"),
        flow_rate=concat("flow_rate"),
        pressure=concat("pressure"),
        timestamps_low=concat("timestamps_low"),
        mask_pressure=concat("mask_pressure"),
        leak=concat("leak"),
        tidal_volume=concat("tidal_volume"),
        minute_ventilation=concat("minute_ventilation"),
        respiratory_rate=concat("respiratory_rate"),
        snore=concat("snore"),
        flow_limitation=concat("flow_limitation"),
        spo2=concat("spo2"),
        pulse=concat("pulse"),
    )
