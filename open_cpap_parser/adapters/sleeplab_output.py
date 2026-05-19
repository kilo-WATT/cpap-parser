"""Mapper from ``CPAPDirectory`` to the sleeplab database schema.

Transforms the unified ``CPAPDirectory`` model into the dict format
expected by ``sleeplab``'s ``upsert_session`` (and related) database
helpers.  Designed for use in ETL pipelines feeding the sleeplab
Postgres schema.

See: https://github.com/joshuamyers-dev/sleeplab/tree/main/importer
"""

from datetime import date, datetime
from typing import Optional

from open_cpap_parser.schema import (
    CPAPDirectory,
    CPAPEvent,
    CPAPSession,
    CPAPSessionSummary,
)


def map_summary_to_session(
    summary: CPAPSessionSummary,
    machine_serial: str,
    user_id: str,
    block_index: int = 0,
) -> dict:
    """Map a single ``CPAPSessionSummary`` to the upsert_session dict format.

    Converts per-hour event indices to integer counts using the
    summary's ``usage_hours``.

    Args:
        summary: Daily summary from a CPAP device.
        machine_serial: Device serial number for the session.
        user_id: sleeplab user UUID to associate the session with.
        block_index: Session block index (default 0 for daily summaries).

    Returns:
        A dict suitable for passing to ``db.upsert_session()``.
    """
    usage_seconds = int(summary.usage_hours * 3600) if summary.usage_hours else 0
    duration_hours = summary.usage_hours if summary.usage_hours > 0 else 0.0

    session_id = f"open-cpap-{summary.date.isoformat()}"

    def _index_to_count(index: float) -> Optional[int]:
        if duration_hours <= 0:
            return None
        return int(round(index * duration_hours))

    ca = _index_to_count(summary.cai)
    oa = _index_to_count(summary.oai)
    h = _index_to_count(summary.hi)
    a = _index_to_count(summary.ai)
    total_ahi_events = _index_to_count(summary.ahi)

    return {
        "session_id": session_id,
        "folder_date": summary.date,
        "block_index": block_index,
        "start_datetime": datetime(
            summary.date.year, summary.date.month, summary.date.day
        ),
        "pld_start_datetime": datetime(
            summary.date.year, summary.date.month, summary.date.day
        ),
        "duration_seconds": usage_seconds,
        "device_serial": machine_serial or None,
        "ahi": round(summary.ahi, 2) if summary.ahi else None,
        "central_apnea_count": ca,
        "obstructive_apnea_count": oa,
        "hypopnea_count": h,
        "apnea_count": a,
        "arousal_count": None,
        "total_ahi_events": total_ahi_events,
        "avg_pressure": summary.pressure_50 if summary.pressure_50 != 0 else None,
        "p95_pressure": summary.pressure_95 if summary.pressure_95 != 0 else None,
        "avg_leak": summary.leak_avg if summary.leak_avg is not None else (
            summary.leak_50 if summary.leak_50 != 0 else None
        ),
        "avg_resp_rate": summary.resp_rate_avg,
        "avg_tidal_vol": summary.tidal_volume_avg,
        "avg_min_vent": summary.minute_ventilation_avg,
        "avg_snore": summary.snore_avg,
        "avg_flow_lim": summary.flow_limitation_avg,
        "has_spo2": False,
        "user_id": user_id,
    }


def map_sessions_to_events(
    sessions: list[CPAPSession],
) -> list[tuple[str, float, Optional[float], datetime]]:
    """Flatten per-session events into a list of sleeplab event tuples.

    Each tuple is ``(event_type, onset_seconds, duration_seconds, session_start)``.

    Args:
        sessions: List of parsed ``CPAPSession`` objects.

    Returns:
        A flat list of event tuples ordered by encounter order.
    """
    result: list[tuple[str, float, Optional[float], datetime]] = []
    for session in sessions:
        if not session.events:
            continue
        for event in session.events:
            result.append((
                event.event_type,
                event.timestamp_sec,
                event.duration_sec,
                session.start_time,
            ))
    return result


def map_timeseries_to_metrics(
    session: CPAPSession,
) -> list[dict]:
    """Convert a session's time-series data to sleeplab metrics rows.

    Each row is a dict with keys ``ts``, ``mask_pressure``, ``leak``,
    ``resp_rate``, ``tidal_vol``, ``min_vent``, ``flow_lim``, ``snore``,
    and ``pressure``.  Unavailable signals are stored as ``None``.

    Args:
        session: A ``CPAPSession`` with optional ``timeseries``.

    Returns:
        A list of metric dicts (one per timestamp sample).
    """
    ts = session.timeseries
    if ts is None:
        return []
    n = len(ts.timestamps)
    if n == 0:
        return []
    rows: list[dict] = []
    for i in range(n):
        rows.append({
            "ts": ts.timestamps[i],
            "mask_pressure": _safe_get(ts.mask_pressure, i),
            "leak": _safe_get(ts.leak, i),
            "resp_rate": _safe_get(ts.respiratory_rate, i),
            "tidal_vol": _safe_get(ts.tidal_volume, i),
            "min_vent": _safe_get(ts.minute_ventilation, i),
            "flow_lim": None,
            "snore": None,
            "pressure": None,
        })
    return rows


def map_timeseries_to_spo2(
    session: CPAPSession,
) -> list[dict]:
    """Convert a session's oximetry data to sleeplab SpO2 rows.

    Each row is a dict with keys ``ts``, ``spo2``, and ``pulse``.
    Rows where both ``spo2`` and ``pulse`` are missing are excluded.

    Args:
        session: A ``CPAPSession`` with optional ``timeseries``.

    Returns:
        A list of SpO2 dicts (one per timestamp with data).
    """
    ts = session.timeseries
    if ts is None:
        return []
    n = len(ts.timestamps)
    if n == 0:
        return []
    rows: list[dict] = []
    for i in range(n):
        spo2 = _safe_get(ts.spo2, i)
        pulse = _safe_get(ts.pulse, i)
        if spo2 is not None or pulse is not None:
            rows.append({
                "ts": ts.timestamps[i],
                "spo2": spo2,
                "pulse": pulse,
            })
    return rows


def map_directory_to_sleeplab(
    directory: CPAPDirectory,
    user_id: str,
) -> dict:
    """Map an entire ``CPAPDirectory`` to the sleeplab DB format.

    Produces four top-level lists suitable for sleeplab's upsert
    helpers:
        ``sessions`` — for ``upsert_session``
        ``events`` — for ``replace_session_events``
        ``metrics`` — for ``replace_session_metrics``
        ``spo2`` — for ``replace_session_spo2``

    Args:
        directory: A parsed ``CPAPDirectory``.
        user_id: sleeplab user UUID to associate all data with.

    Returns:
        A dict with keys ``sessions``, ``events``, ``metrics``, ``spo2``.
    """
    sessions_data = [
        map_summary_to_session(s, directory.machine.serial_number, user_id)
        for s in directory.daily_summaries
    ]
    all_events = map_sessions_to_events(directory.sessions)
    all_metrics = [
        row
        for s in directory.sessions
        for row in map_timeseries_to_metrics(s)
    ]
    all_spo2 = [
        row
        for s in directory.sessions
        for row in map_timeseries_to_spo2(s)
    ]
    return {
        "sessions": sessions_data,
        "events": all_events,
        "metrics": all_metrics,
        "spo2": all_spo2,
    }


def _safe_get(lst: Optional[list], idx: int) -> Optional[float]:
    """Return ``lst[idx]`` or ``None`` if the index is out of range.

    Args:
        lst: A list of floats, or ``None``.
        idx: Zero-based index.

    Returns:
        The value at *idx*, or ``None``.
    """
    if lst is None or idx < 0 or idx >= len(lst):
        return None
    return lst[idx]
