from datetime import date, datetime
from uuid import uuid4

from cpap_parser.adapters.sleeplab_output import (
    map_directory_to_sleeplab,
    map_sessions_to_events,
    map_summary_to_session,
    map_timeseries_to_metrics,
    map_timeseries_to_spo2,
)
from cpap_parser.schema import (
    CPAPDirectory,
    CPAPEvent,
    CPAPSession,
    CPAPSessionSummary,
    MachineInfo,
    TimeSeriesData,
)


def test_map_summary_to_session_basic():
    summary = CPAPSessionSummary(
        date=date(2025, 6, 1),
        ahi=5.2,
        ai=2.0,
        hi=3.0,
        cai=1.0,
        oai=1.0,
        leak_50=10.0,
        leak_95=20.0,
        pressure_50=12.0,
        pressure_95=14.0,
        usage_hours=7.5,
    )
    uid = str(uuid4())
    result = map_summary_to_session(summary, "SN12345", uid)

    assert result["session_id"] == "open-cpap-2025-06-01"
    assert result["folder_date"] == date(2025, 6, 1)
    assert result["device_serial"] == "SN12345"
    assert result["user_id"] == uid
    assert result["duration_seconds"] == 27000  # 7.5 * 3600
    assert result["ahi"] == 5.2
    assert result["central_apnea_count"] == 8  # 1.0 * 7.5
    assert result["obstructive_apnea_count"] == 8  # 1.0 * 7.5
    assert result["hypopnea_count"] == 22  # 3.0 * 7.5
    assert result["total_ahi_events"] == 39  # 5.2 * 7.5
    assert result["avg_pressure"] == 12.0
    assert result["p95_pressure"] == 14.0
    assert result["avg_leak"] == 10.0


def test_map_summary_to_session_zero_usage():
    summary = CPAPSessionSummary(
        date=date(2025, 6, 1),
        usage_hours=0.0,
    )
    uid = str(uuid4())
    result = map_summary_to_session(summary, "SN12345", uid)
    assert result["duration_seconds"] == 0
    assert result["ahi"] is None
    assert result["central_apnea_count"] is None
    assert result["total_ahi_events"] is None


def test_map_summary_to_session_no_serial():
    summary = CPAPSessionSummary(
        date=date(2025, 6, 1),
        usage_hours=6.0,
    )
    uid = str(uuid4())
    result = map_summary_to_session(summary, "", uid)
    assert result["device_serial"] is None


def test_map_sessions_to_events_empty():
    assert map_sessions_to_events([]) == []


def test_map_sessions_to_events_with_data():
    sessions = [
        CPAPSession(
            start_time=datetime(2025, 6, 1, 22, 0, 0),
            end_time=datetime(2025, 6, 2, 6, 0, 0),
            duration_minutes=480,
            file_type="EVE",
            events=[
                CPAPEvent(timestamp_sec=3600.0, event_type="Obstructive Apnea", duration_sec=10.0),
                CPAPEvent(timestamp_sec=7200.0, event_type="Hypopnea", duration_sec=None),
            ],
        )
    ]
    result = map_sessions_to_events(sessions)
    assert len(result) == 2
    assert result[0] == ("Obstructive Apnea", 3600.0, 10.0, datetime(2025, 6, 1, 22, 0, 0))
    assert result[1] == ("Hypopnea", 7200.0, None, datetime(2025, 6, 1, 22, 0, 0))


def test_map_timeseries_to_metrics_none():
    session = CPAPSession(
        start_time=datetime(2025, 6, 1, 22, 0, 0),
        end_time=datetime(2025, 6, 1, 23, 0, 0),
        duration_minutes=60,
        timeseries=None,
    )
    assert map_timeseries_to_metrics(session) == []


def test_map_timeseries_to_metrics_with_data():
    ts = TimeSeriesData(
        timestamps=[0.0, 1.0, 2.0],
        mask_pressure=[11.0, 12.0, 13.0],
        leak=[5.0, 6.0, 7.0],
        respiratory_rate=[15.0, 16.0, 17.0],
        tidal_volume=[500.0, 510.0, 520.0],
        minute_ventilation=[8.0, 8.5, 9.0],
    )
    session = CPAPSession(
        start_time=datetime(2025, 6, 1, 22, 0, 0),
        end_time=datetime(2025, 6, 1, 22, 0, 3),
        duration_minutes=0.05,
        file_type="BRP",
        timeseries=ts,
    )
    result = map_timeseries_to_metrics(session)
    assert len(result) == 3
    assert result[0]["ts"] == 0.0
    assert result[0]["mask_pressure"] == 11.0
    assert result[0]["leak"] == 5.0
    assert result[0]["resp_rate"] == 15.0
    assert result[1]["tidal_vol"] == 510.0
    assert result[2]["min_vent"] == 9.0


def test_map_timeseries_to_spo2_empty():
    ts = TimeSeriesData(
        timestamps=[0.0, 1.0, 2.0],
    )
    session = CPAPSession(
        start_time=datetime(2025, 6, 1, 22, 0, 0),
        end_time=datetime(2025, 6, 1, 22, 0, 3),
        duration_minutes=0.05,
        timeseries=ts,
    )
    result = map_timeseries_to_spo2(session)
    assert result == []


def test_map_timeseries_to_spo2_with_data():
    ts = TimeSeriesData(
        timestamps=[0.0, 1.0, 2.0],
        spo2=[98.0, 97.0, 99.0],
        pulse=[70.0, 71.0, 72.0],
    )
    session = CPAPSession(
        start_time=datetime(2025, 6, 1, 22, 0, 0),
        end_time=datetime(2025, 6, 1, 22, 0, 3),
        duration_minutes=0.05,
        file_type="SA2",
        timeseries=ts,
    )
    result = map_timeseries_to_spo2(session)
    assert len(result) == 3
    assert result[0]["spo2"] == 98.0
    assert result[0]["pulse"] == 70.0
    assert result[1]["spo2"] == 97.0


def test_map_summary_start_datetime_from_sessions():
    summary = CPAPSessionSummary(date=date(2025, 6, 1), usage_hours=8.0)
    sessions = [
        CPAPSession(
            start_time=datetime(2025, 6, 1, 22, 30, 0),
            end_time=datetime(2025, 6, 2, 6, 0, 0),
            duration_minutes=450,
        ),
        CPAPSession(
            start_time=datetime(2025, 6, 1, 22, 0, 0),
            end_time=datetime(2025, 6, 1, 22, 15, 0),
            duration_minutes=15,
        ),
    ]
    uid = str(uuid4())
    result = map_summary_to_session(summary, "SN001", uid, sessions=sessions)
    assert result["start_datetime"] == datetime(2025, 6, 1, 22, 0, 0)


def test_map_summary_start_datetime_fallback_to_midnight():
    summary = CPAPSessionSummary(date=date(2025, 6, 1), usage_hours=8.0)
    uid = str(uuid4())
    result = map_summary_to_session(summary, "SN001", uid)
    assert result["start_datetime"] == datetime(2025, 6, 1, 0, 0, 0)


def test_map_summary_start_datetime_from_summary_field():
    summary = CPAPSessionSummary(
        date=date(2025, 6, 1),
        usage_hours=8.0,
        start_time=datetime(2025, 6, 1, 21, 45, 0),
    )
    uid = str(uuid4())
    result = map_summary_to_session(summary, "SN001", uid, sessions=None)
    assert result["start_datetime"] == datetime(2025, 6, 1, 21, 45, 0)


def test_map_summary_spo2_from_sessions():
    summary = CPAPSessionSummary(date=date(2025, 6, 1), usage_hours=8.0)
    ts = TimeSeriesData(timestamps=[0.0, 1.0, 2.0], spo2=[98.0, 95.0, 97.0])
    sessions = [
        CPAPSession(
            start_time=datetime(2025, 6, 1, 22, 0, 0),
            end_time=datetime(2025, 6, 2, 6, 0, 0),
            duration_minutes=480,
            timeseries=ts,
        )
    ]
    uid = str(uuid4())
    result = map_summary_to_session(summary, "SN001", uid, sessions=sessions)
    assert result["has_spo2"] is True
    assert result["spo2_avg"] == round((98 + 95 + 97) / 3, 2)
    assert result["spo2_min"] == 95.0


def test_map_summary_no_spo2():
    summary = CPAPSessionSummary(date=date(2025, 6, 1), usage_hours=8.0)
    uid = str(uuid4())
    result = map_summary_to_session(summary, "SN001", uid)
    assert result["has_spo2"] is False
    assert result["spo2_avg"] is None
    assert result["spo2_min"] is None


def test_map_summary_arousal_count_from_sessions():
    summary = CPAPSessionSummary(date=date(2025, 6, 1), usage_hours=8.0)
    sessions = [
        CPAPSession(
            start_time=datetime(2025, 6, 1, 22, 0, 0),
            end_time=datetime(2025, 6, 2, 6, 0, 0),
            duration_minutes=480,
            events=[
                CPAPEvent(timestamp_sec=100.0, event_type="Arousal"),
                CPAPEvent(timestamp_sec=200.0, event_type="Obstructive Apnea", duration_sec=10.0),
                CPAPEvent(timestamp_sec=300.0, event_type="Arousal"),
            ],
        )
    ]
    uid = str(uuid4())
    result = map_summary_to_session(summary, "SN001", uid, sessions=sessions)
    assert result["arousal_count"] == 2


def test_map_summary_arousal_count_none_when_no_arousals():
    summary = CPAPSessionSummary(date=date(2025, 6, 1), usage_hours=8.0)
    uid = str(uuid4())
    result = map_summary_to_session(summary, "SN001", uid)
    assert result["arousal_count"] is None


def test_map_summary_duration_seconds_rounded():
    # usage_hours that produces a fractional seconds value — must round, not truncate
    summary = CPAPSessionSummary(date=date(2025, 6, 1), usage_hours=7.5)
    uid = str(uuid4())
    result = map_summary_to_session(summary, "SN001", uid)
    assert result["duration_seconds"] == 27000


def test_map_directory_groups_sessions_by_date():
    summary = CPAPSessionSummary(date=date(2025, 6, 1), usage_hours=8.0)
    ts = TimeSeriesData(timestamps=[0.0, 1.0], spo2=[97.0, 98.0])
    session = CPAPSession(
        start_time=datetime(2025, 6, 1, 22, 0, 0),
        end_time=datetime(2025, 6, 2, 6, 0, 0),
        duration_minutes=480,
        timeseries=ts,
    )
    directory = CPAPDirectory(
        machine=MachineInfo(serial_number="SN001"),
        daily_summaries=[summary],
        sessions=[session],
    )
    uid = str(uuid4())
    result = map_directory_to_sleeplab(directory, uid)
    s = result["sessions"][0]
    assert s["start_datetime"] == datetime(2025, 6, 1, 22, 0, 0)
    assert s["has_spo2"] is True
    assert s["spo2_avg"] == 97.5


def test_map_directory_includes_meta_validation_status():
    summary = CPAPSessionSummary(date=date(2025, 6, 1), usage_hours=8.0)
    directory = CPAPDirectory(
        machine=MachineInfo(
            serial_number="SN001",
            validation_status="validated",
            validation_notes="Tested against OSCAR.",
        ),
        daily_summaries=[summary],
    )
    uid = str(uuid4())
    result = map_directory_to_sleeplab(directory, uid)
    meta = result["sessions"][0]["meta"]
    assert meta["validation_status"] == "validated"
    assert "OSCAR" in meta["validation_notes"]


def test_map_directory_to_sleeplab_empty():
    directory = CPAPDirectory(
        machine=MachineInfo(serial_number="SN001"),
    )
    uid = str(uuid4())
    result = map_directory_to_sleeplab(directory, uid)
    assert result["sessions"] == []
    assert result["events"] == []
    assert result["metrics"] == []
    assert result["spo2"] == []


def test_map_directory_to_sleeplab_full():
    summary = CPAPSessionSummary(
        date=date(2025, 6, 1),
        ahi=3.0,
        usage_hours=8.0,
    )
    ts = TimeSeriesData(
        timestamps=[0.0, 1.0],
        mask_pressure=[10.0, 11.0],
        leak=[5.0, 6.0],
    )
    session = CPAPSession(
        start_time=datetime(2025, 6, 1, 22, 0, 0),
        end_time=datetime(2025, 6, 2, 6, 0, 0),
        duration_minutes=480,
        file_type="BRP",
        timeseries=ts,
    )
    directory = CPAPDirectory(
        machine=MachineInfo(serial_number="SN001"),
        daily_summaries=[summary],
        sessions=[session],
    )
    uid = str(uuid4())
    result = map_directory_to_sleeplab(directory, uid)
    assert len(result["sessions"]) == 1
    assert result["sessions"][0]["session_id"] == "open-cpap-2025-06-01"
    assert result["events"] == []
    assert len(result["metrics"]) == 2
    assert result["spo2"] == []
