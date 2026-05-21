"""Event count comparison between parsed CPAPEvent lists and OSCAR Details CSV.

Matches our therapy events against OSCAR's per-session event records,
comparing counts by type within a tolerance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from open_cpap_parser.schema import CPAPEvent
from open_cpap_parser.validation.oscar_reader import OscarEvent


# Maps OSCAR Details CSV event type → our CPAPEvent.event_type prefix(es).
OSCAR_TO_PARSER_EVENT: dict[str, set[str]] = {
    "Obstructive": {"Obstructive Apnea", "OA", "Obstructive"},
    "ClearAirway": {"Clear Airway", "CA", "ClearAirway", "Central Apnea"},
    "Hypopnea": {"Hypopnea", "H"},
    "RERA": {"RERA"},
    "FlowLimit": {"Flow Limitation", "Flow Limit", "FlowLimit", "FL"},
}

_PARSER_TO_FIELD: dict[str, str] = {}
for _oscar_key, _parser_set in OSCAR_TO_PARSER_EVENT.items():
    _field = {
        "Obstructive": "obstructive",
        "ClearAirway": "central",
        "Hypopnea": "hypopnea",
        "RERA": "rera",
        "FlowLimit": "flow_limit",
    }[_oscar_key]
    for _pt in _parser_set:
        _PARSER_TO_FIELD[_pt] = _field


@dataclass
class EventCounts:
    """Per-type therapy event counts for one session."""
    obstructive: int = 0
    central: int = 0
    hypopnea: int = 0
    rera: int = 0
    flow_limit: int = 0


@dataclass
class EventCountDiff:
    """Comparison result for event counts."""
    obstructive_delta: int
    central_delta: int
    hypopnea_delta: int
    rera_delta: int
    flow_limit_delta: int
    within_tolerance: bool


_COUNT_TOLERANCE = 1


def count_events(events: list[CPAPEvent]) -> EventCounts:
    """Count therapy events from a CPAPEvent list by category."""
    counts = EventCounts()
    for ev in events:
        field = _PARSER_TO_FIELD.get(ev.event_type)
        if field:
            setattr(counts, field, getattr(counts, field) + 1)
    return counts


def count_oscar_events(oscar_events: list[OscarEvent]) -> EventCounts:
    """Count therapy events from an OscarEvent list by category."""
    _OSCAR_TO_FIELD = {
        "Obstructive": "obstructive",
        "ClearAirway": "central",
        "Hypopnea": "hypopnea",
        "RERA": "rera",
        "FlowLimit": "flow_limit",
    }
    counts = EventCounts()
    for ev in oscar_events:
        field = _OSCAR_TO_FIELD.get(ev.event_type)
        if field:
            setattr(counts, field, getattr(counts, field) + 1)
    return counts


def compare_event_counts(
    ours: EventCounts,
    oscar: EventCounts,
    tolerance: int = _COUNT_TOLERANCE,
) -> EventCountDiff:
    """Compare parser event counts against OSCAR counts.

    Args:
        ours: Event counts from our parser.
        oscar: Event counts from OSCAR Details CSV.
        tolerance: Maximum allowed count difference per type.

    Returns:
        EventCountDiff with per-type deltas and overall pass/fail.
    """
    fields = ("obstructive", "central", "hypopnea", "rera", "flow_limit")
    deltas = {f: abs(getattr(ours, f) - getattr(oscar, f)) for f in fields}
    within = all(d <= tolerance for d in deltas.values())
    return EventCountDiff(
        obstructive_delta=deltas["obstructive"],
        central_delta=deltas["central"],
        hypopnea_delta=deltas["hypopnea"],
        rera_delta=deltas["rera"],
        flow_limit_delta=deltas["flow_limit"],
        within_tolerance=within,
    )
