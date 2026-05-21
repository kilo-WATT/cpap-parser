from datetime import datetime, timezone
from open_cpap_parser.schema import CPAPEvent, CPAPSession
from open_cpap_parser.validation.oscar_reader import OscarEvent
from open_cpap_parser.validation.event_compare import (
    EventCounts,
    EventCountDiff,
    count_events,
    count_oscar_events,
    compare_event_counts,
    OSCAR_TO_PARSER_EVENT,
)


def _make_event(event_type: str, onset_sec: float = 0.0, dur: float = 10.0) -> CPAPEvent:
    return CPAPEvent(timestamp_sec=onset_sec, event_type=event_type, duration_sec=dur)


def _make_oscar_event(session_id: str, event_type: str) -> OscarEvent:
    return OscarEvent(
        datetime_str="2026-01-01T22:05:00",
        session_id=session_id,
        event_type=event_type,
        duration_sec=10.0,
    )


def test_count_events_empty():
    counts = count_events([])
    assert counts.obstructive == 0
    assert counts.central == 0
    assert counts.hypopnea == 0


def test_count_events_by_type():
    events = [
        _make_event("Obstructive Apnea"),
        _make_event("Obstructive Apnea"),
        _make_event("Clear Airway"),
        _make_event("Hypopnea"),
    ]
    counts = count_events(events)
    assert counts.obstructive == 2
    assert counts.central == 1
    assert counts.hypopnea == 1


def test_count_oscar_events():
    oscar_events = [
        _make_oscar_event("123", "Obstructive"),
        _make_oscar_event("123", "ClearAirway"),
        _make_oscar_event("123", "ClearAirway"),
    ]
    counts = count_oscar_events(oscar_events)
    assert counts.obstructive == 1
    assert counts.central == 2


def test_compare_event_counts_within_tolerance():
    ours = EventCounts(obstructive=5, central=1, hypopnea=3, rera=0, flow_limit=0)
    oscar = EventCounts(obstructive=5, central=1, hypopnea=3, rera=0, flow_limit=0)
    diff = compare_event_counts(ours, oscar)
    assert diff.within_tolerance


def test_compare_event_counts_outside_tolerance():
    ours = EventCounts(obstructive=0, central=1, hypopnea=3, rera=0, flow_limit=0)
    oscar = EventCounts(obstructive=5, central=1, hypopnea=3, rera=0, flow_limit=0)
    diff = compare_event_counts(ours, oscar)
    assert not diff.within_tolerance


def test_oscar_event_mapping_keys_exist():
    for oscar_type in ("Obstructive", "ClearAirway", "Hypopnea", "RERA", "FlowLimit"):
        assert oscar_type in OSCAR_TO_PARSER_EVENT, f"Missing mapping for {oscar_type}"
