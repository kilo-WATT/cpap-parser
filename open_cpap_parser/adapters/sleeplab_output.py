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
            "flow_lim": _safe_get(None, 0),  # not available in TimeSeriesData
            "snore": _safe_get(None, 0),  # not available in TimeSeriesData
            "pressure": _safe_get(None, 0),  # not in our TimeSeriesData
        })
    return rows


def map_timeseries_to_spo2(
    session: CPAPSession,
) -> list[dict]:
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
    if lst is None or idx < 0 or idx >= len(lst):
        return None
    return lst[idx]
