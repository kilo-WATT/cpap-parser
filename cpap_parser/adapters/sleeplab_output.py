"""Mapper from ``CPAPDirectory`` to the sleeplab database schema.

Transforms the unified ``CPAPDirectory`` model into the dict format
expected by ``sleeplab``'s ``upsert_session`` (and related) database
helpers.  Designed for use in ETL pipelines feeding the sleeplab
Postgres schema.

See: https://github.com/joshuamyers-dev/sleeplab/tree/main/importer

Pressure mode strings by manufacturer
--------------------------------------
The ``pressure_mode`` field (mapped to ``therapy_mode`` in sleeplab) uses
the following string values, which are set by each device adapter:

- ``"CPAP"``  — fixed-pressure CPAP
- ``"APAP"``  — auto-titrating CPAP (also ``"AutoSet"``, ``"AutoMode"``)
- ``"BiPAP"`` — bilevel pressure (BPAP / BiLevel)
- ``"ASV"``   — adaptive servo-ventilation
- ``""``      — mode unknown or not reported (treat as ``None`` in sleeplab)

MachineInfo property keys
--------------------------
When present in ``MachineInfo.properties``, the following keys are mapped
to optional sleeplab columns:

- ``"mask_type"``      → ``sessions.mask_type TEXT``
- ``"humidity_level"`` → ``sessions.humidity_level SMALLINT``
- ``"temperature_c"``  → ``sessions.temperature_c NUMERIC(4,1)``
"""

from datetime import date, datetime
from typing import Optional

from cpap_parser.schema import (
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
    sessions: list[CPAPSession] | None = None,
    machine_properties: dict[str, str] | None = None,
) -> dict:
    """Map a single ``CPAPSessionSummary`` to the upsert_session dict format.

    Converts per-hour event indices to integer counts using the
    summary's ``usage_hours``.  When *sessions* for the same date are
    provided, the earliest session start is used as ``start_datetime``
    and SpO2/arousal fields are computed from their timeseries and events.

    Args:
        summary: Daily summary from a CPAP device.
        machine_serial: Device serial number for the session.
        user_id: sleeplab user UUID to associate the session with.
        block_index: Session block index (default 0 for daily summaries).
        sessions: ``CPAPSession`` objects for this date, used to derive
            ``start_datetime``, SpO2 stats, and arousal count.
        machine_properties: ``MachineInfo.properties`` dict, used to probe
            optional device settings (mask type, humidity, temperature).

    Returns:
        A dict suitable for passing to ``db.upsert_session()``.
    """
    usage_seconds = round(summary.usage_hours * 3600) if summary.usage_hours else 0
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

    # Derive start_datetime from earliest session when available
    day_start = datetime(summary.date.year, summary.date.month, summary.date.day)
    if sessions:
        start_datetime = min(s.start_time for s in sessions)
        start_datetime = start_datetime.replace(tzinfo=None)  # strip tz; stored as naive
    elif summary.start_time is not None:
        start_datetime = summary.start_time.replace(tzinfo=None)
    else:
        start_datetime = day_start

    # Derive SpO2 stats and arousal count from sessions
    all_spo2: list[float] = []
    arousal_count: Optional[int] = None
    if sessions:
        for s in sessions:
            if s.timeseries and s.timeseries.spo2:
                all_spo2.extend(s.timeseries.spo2)
            for ev in s.events:
                if ev.event_type == "Arousal":
                    arousal_count = (arousal_count or 0) + 1

    has_spo2 = summary.has_spo2 or bool(all_spo2)
    spo2_avg = (sum(all_spo2) / len(all_spo2)) if all_spo2 else summary.spo2_avg
    spo2_min = min(all_spo2) if all_spo2 else summary.spo2_min
    if arousal_count is None:
        arousal_count = summary.arousal_count

    props = machine_properties or {}
    return {
        "session_id": session_id,
        "folder_date": summary.date,
        "block_index": block_index,
        "start_datetime": start_datetime,
        "pld_start_datetime": start_datetime,
        "duration_seconds": usage_seconds,
        "device_serial": machine_serial or None,
        "ahi": round(summary.ahi, 2) if summary.ahi else None,
        "central_apnea_count": ca,
        "obstructive_apnea_count": oa,
        "hypopnea_count": h,
        "apnea_count": a,
        "arousal_count": arousal_count,
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
        "has_spo2": has_spo2,
        "spo2_avg": round(spo2_avg, 2) if spo2_avg is not None else None,
        "spo2_min": spo2_min,
        "therapy_mode": summary.pressure_mode or None,
        "mask_type": props.get("mask_type"),
        "humidity_level": props.get("humidity_level"),
        "temperature_c": props.get("temperature_c"),
        "user_id": user_id,
        "summary_reported_duration_seconds": (
            round(summary.summary_reported_usage * 3600)
            if summary.summary_reported_usage is not None else None
        ),
        "computed_duration_seconds": (
            round(summary.computed_usage * 3600)
            if summary.computed_usage is not None else None
        ),
        "recording_span_seconds": (
            round(summary.recording_span * 3600)
            if summary.recording_span is not None else None
        ),
        "has_detailed_data": summary.has_detailed_data,
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
    sessions_by_date: dict[date, list[CPAPSession]] = {}
    for s in directory.sessions:
        d = s.start_time.date()
        sessions_by_date.setdefault(d, []).append(s)

    machine_meta = {
        "validation_status": directory.machine.validation_status,
        "validation_notes": directory.machine.validation_notes,
    }

    sessions_data = []
    for s in directory.daily_summaries:
        session_dict = map_summary_to_session(
            s,
            directory.machine.serial_number,
            user_id,
            sessions=sessions_by_date.get(s.date),
            machine_properties=directory.machine.properties,
        )
        session_dict["meta"] = machine_meta
        sessions_data.append(session_dict)
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
