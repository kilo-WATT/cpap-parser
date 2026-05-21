import csv
import tempfile
from pathlib import Path

import pytest

from open_cpap_parser.validation.oscar_reader import (
    OscarSession,
    OscarEvent,
    read_sessions_csv,
    read_details_csv,
)


SESSION_HEADER = (
    "Date,Session,Start,End,Total Time,AHI,CA Count,A Count,OA Count,H Count,"
    "UA Count,VS Count,VS2 Count,RE Count,FL Count,SA Count,NR Count,EP Count,"
    "LF Count,UF1 Count,UF2 Count,PP Count,Median Pressure,Median Pressure Set,"
    "Median IPAP,Median IPAP Set,Median EPAP,Median EPAP Set,Median Flow Limit.,"
    "95% Pressure,95% Pressure Set,95% IPAP,95% IPAP Set,95% EPAP,95% EPAP Set,"
    "95% Flow Limit.,99.5% Pressure,99.5% Pressure Set,99.5% IPAP,99.5% IPAP Set,"
    "99.5% EPAP,99.5% EPAP Set,99.5% Flow Limit."
)
SESSION_ROW = (
    "2026-05-15,1778897460,2026-05-15T22:11:06,2026-05-16T03:49:06,05:38:00,"
    "0.533,1,0,2,0,0,0,0,0,0,0,0,0,0,0,0,0,6.72,0,0,0,4.2,0,0.01,"
    "7.42,0,0,0,4.58,0,0.24,7.62,0,0,0,4.66,0,0.28"
)

DETAILS_HEADER = "DateTime,Session,Event,Data/Duration"
DETAILS_ROWS = [
    "2026-05-16T03:26:54,1778897460,ClearAirway,14.00",
    "2026-05-15T22:22:45,1778897460,Obstructive,14.00",
    "2026-05-15T22:11:16,1778897460,Pressure,4.00",
]


@pytest.fixture
def sessions_csv(tmp_path: Path) -> Path:
    p = tmp_path / "sessions.csv"
    p.write_text(SESSION_HEADER + "\n" + SESSION_ROW + "\n")
    return p


@pytest.fixture
def details_csv(tmp_path: Path) -> Path:
    p = tmp_path / "details.csv"
    p.write_text(DETAILS_HEADER + "\n" + "\n".join(DETAILS_ROWS) + "\n")
    return p


def test_read_sessions_returns_one_session(sessions_csv):
    sessions = read_sessions_csv(sessions_csv)
    assert len(sessions) == 1


def test_session_fields(sessions_csv):
    session = read_sessions_csv(sessions_csv)[0]
    assert session.session_id == "1778897460"
    assert session.start == "2026-05-15T22:11:06"
    assert session.end == "2026-05-16T03:49:06"
    assert abs(session.ahi - 0.533) < 0.001
    assert abs(session.pressure_50 - 6.72) < 0.01
    assert abs(session.pressure_95 - 7.42) < 0.01
    assert abs(session.epap_50 - 4.2) < 0.01
    assert abs(session.epap_95 - 4.58) < 0.01
    assert abs(session.leak_50 - 0.01) < 0.001
    assert abs(session.leak_95 - 0.24) < 0.001


def test_read_details_filters_non_events(details_csv):
    events = read_details_csv(details_csv)
    event_types = {e.event_type for e in events}
    assert "Pressure" not in event_types


def test_read_details_returns_therapy_events(details_csv):
    events = read_details_csv(details_csv)
    assert len(events) == 2
    types = {e.event_type for e in events}
    assert "ClearAirway" in types
    assert "Obstructive" in types


def test_event_fields(details_csv):
    events = [e for e in read_details_csv(details_csv) if e.event_type == "Obstructive"]
    assert len(events) == 1
    e = events[0]
    assert e.session_id == "1778897460"
    assert e.datetime_str == "2026-05-15T22:22:45"
    assert abs(e.duration_sec - 14.0) < 0.01
