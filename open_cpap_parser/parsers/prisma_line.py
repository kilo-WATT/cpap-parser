"""Löwenstein Prisma Line CPAP parser.

Handles the newer Prisma Line format used by devices such as the Löwenstein
Eyra / prisma25A / prisma25S / prisma25ST series.  Data is exported as two
ZIP archives placed at the root of the SD card:

* ``config.pcfg`` – device configuration; contains ``mnt/flash/conf/device.xml``
  with serial number and model code.
* ``therapy.pdat`` – therapy data; contains:
  - ``mnt/flash/data/statistics/statistics_year.bin`` – per-day XML summary
  - ``mnt/flash/data/therapy/events/YYYYMMDD/event_NNNNNN.xml`` – per-session
    respiratory event logs

This implementation is informed by the OSCAR open-source CPAP analysis project
(https://gitlab.com/CrimsonNape/OSCAR-code, GPL-3.0).

Supported device types (``DeviceType`` from ``device.xml``):
  "22" → prisma25S
  "23" → prisma25ST
  "27" → Löwenstein Eyra (Prisma Line)

Detection fingerprint: presence of ``config.pcfg`` in the directory root.
"""

import io
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from open_cpap_parser.schema import (
    CPAPDirectory,
    CPAPSession,
    CPAPSessionSummary,
    MachineInfo,
)

# ── Model name table (from OSCAR prisma_loader.cpp) ───────────────────────────

_MODEL_NAMES: dict[str, str] = {
    "0x92": "Prisma Smart",
    "0x91": "Prisma Soft",
    "22": "prisma25S",
    "23": "prisma25ST",
    "27": "Löwenstein Eyra",
}

# ── Therapy mode codes (from OSCAR Prisma_Mode enum) ─────────────────────────

_MODE_LABELS: dict[int, str] = {
    1: "CPAP",
    2: "APAP",
    3: "ACSV",
    4: "S",
    9: "Auto-S",
    10: "Auto-ST",
}

# ── Respiratory event IDs that count toward AHI (from OSCAR Prisma_Event_Type)

_OA_IDS = {101}          # obstructive apnea
_CA_IDS = {102}          # central apnea
_OAH_IDS = {111}         # obstructive hypopnea
_CAH_IDS = {112}         # central hypopnea


def can_handle(path: Path) -> bool:
    """Return ``True`` if *path* looks like a Prisma Line SD card root.

    Fingerprint: ``config.pcfg`` must be present.
    """
    return (path / "config.pcfg").is_file()


# ── Device identity ───────────────────────────────────────────────────────────

def _read_device_info(config_zip: zipfile.ZipFile) -> MachineInfo:
    """Parse ``mnt/flash/conf/device.xml`` from the config ZIP."""
    data = config_zip.read("mnt/flash/conf/device.xml")
    root = ET.fromstring(data)

    def _val(tag: str) -> str:
        el = root.find(tag)
        return el.get("value", "") if el is not None else ""

    device_type = _val("DeviceType")
    serial = _val("DeviceSerialNumber")
    model = _MODEL_NAMES.get(device_type, f"Prisma Line (type {device_type})")

    return MachineInfo(
        serial_number=serial,
        product_code=device_type,
        model=model,
        series="Löwenstein Medical",
        properties={
            "fw_version": _val("FWVersion"),
            "fw_build": _val("FWBuild"),
        },
    )


# ── Event file parsing ────────────────────────────────────────────────────────

def _count_events(xml_bytes: bytes) -> dict[str, int]:
    """Count apnea / hypopnea events from one session event XML.

    Returns a dict with keys: ``oa``, ``ca``, ``oah``, ``cah``.
    """
    root = ET.fromstring(xml_bytes)
    counts: dict[str, int] = {"oa": 0, "ca": 0, "oah": 0, "cah": 0}
    for el in root:
        if el.tag != "RespEvent":
            continue
        eid = int(el.get("RespEventID", "0"))
        if eid in _OA_IDS:
            counts["oa"] += 1
        elif eid in _CA_IDS:
            counts["ca"] += 1
        elif eid in _OAH_IDS:
            counts["oah"] += 1
        elif eid in _CAH_IDS:
            counts["cah"] += 1
    return counts


def _aggregate_events(therapy_zip: zipfile.ZipFile) -> dict[str, dict[str, int]]:
    """Return per-date aggregated event counts from all event XML files.

    Returns ``{date_str: {"oa": N, "ca": N, "oah": N, "cah": N}}``.
    """
    per_day: dict[str, dict[str, int]] = defaultdict(
        lambda: {"oa": 0, "ca": 0, "oah": 0, "cah": 0}
    )
    prefix = "mnt/flash/data/therapy/events/"
    for name in therapy_zip.namelist():
        if not name.startswith(prefix) or not name.endswith(".xml"):
            continue
        # path: prefix + YYYYMMDD/event_NNNNNN.xml
        parts = name[len(prefix):].split("/")
        if len(parts) != 2 or len(parts[0]) != 8:
            continue
        date_str = f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]}"
        counts = _count_events(therapy_zip.read(name))
        for k in per_day[date_str]:
            per_day[date_str][k] += counts[k]
    return dict(per_day)


# ── Statistics XML parsing ────────────────────────────────────────────────────

def _parse_t_intervals(t_str: str) -> list[tuple[int, int]]:
    """Parse the ``t="start-duration,..."`` attribute into (start_sec, dur_sec) tuples."""
    result = []
    for part in t_str.split(","):
        if "-" not in part:
            continue
        start_s, dur_s = part.split("-", 1)
        try:
            result.append((int(start_s), int(dur_s)))
        except ValueError:
            continue
    return result


def _parse_statistics(
    therapy_zip: zipfile.ZipFile,
    event_counts_by_date: dict[str, dict[str, int]],
) -> tuple[list[CPAPSession], list[CPAPSessionSummary]]:
    """Parse ``statistics_year.bin`` to build one session + summary per calendar day.

    Multiple ``<rec>`` elements on the same date are merged: usage totals
    accumulate and the dominant mode (most usage) is used for the summary.
    AHI and event indices are computed from the day-level event totals, avoiding
    the inflated rates that arise when dividing a full day's events by a
    short single-record window.
    """
    from datetime import timedelta

    stat_path = "mnt/flash/data/statistics/statistics_year.bin"
    data = therapy_zip.read(stat_path).decode("utf-8", errors="replace")
    root = ET.fromstring(data)

    # Per-day accumulators keyed by date string.
    # Each value: {usage_sec, set_pressure, dominant_mode_code, dominant_usage,
    #              first_start_sec, last_end_sec}
    day_data: dict[str, dict] = {}

    for day_el in root.findall("day"):
        date_str: str = day_el.get("d", "")
        if not date_str:
            continue

        acc = day_data.setdefault(date_str, {
            "total_usage_sec": 0,
            "dominant_mode": 1,
            "dominant_usage_sec": 0,
            "set_pressure": 0.0,
            "first_start_sec": None,
            "last_end_sec": None,
        })

        for rec_el in day_el.findall("rec"):
            mode_code = int(rec_el.get("m", "0"))
            if mode_code not in _MODE_LABELS:
                continue

            t_str = rec_el.get("t", "")
            intervals = _parse_t_intervals(t_str)
            if not intervals:
                continue

            rec_usage_sec = sum(dur for _, dur in intervals)
            if rec_usage_sec <= 0:
                continue

            # Simple scalar stats
            stats: dict[str, int] = {}
            for s_el in rec_el.findall("s"):
                v_str = s_el.get("v", "")
                if "," not in v_str:
                    try:
                        stats[s_el.get("i", "")] = int(v_str)
                    except ValueError:
                        pass

            acc["total_usage_sec"] += rec_usage_sec

            # Track the record with the most usage as the "dominant" one
            if rec_usage_sec > acc["dominant_usage_sec"]:
                acc["dominant_mode"] = mode_code
                acc["dominant_usage_sec"] = rec_usage_sec
                # Set pressure from the dominant record (i=309 = pressure × 100)
                acc["set_pressure"] = stats.get("309", 0) / 100.0

            # Track earliest start and latest end for session timing
            first_start = intervals[0][0]
            last_end = intervals[-1][0] + intervals[-1][1]
            if acc["first_start_sec"] is None or first_start < acc["first_start_sec"]:
                acc["first_start_sec"] = first_start
            if acc["last_end_sec"] is None or last_end > acc["last_end_sec"]:
                acc["last_end_sec"] = last_end

    sessions: list[CPAPSession] = []
    summaries: list[CPAPSessionSummary] = []

    for date_str, acc in sorted(day_data.items()):
        usage_hours = acc["total_usage_sec"] / 3600.0
        if usage_hours <= 0:
            continue

        # AHI from aggregated event XML files for this date
        ev = event_counts_by_date.get(date_str, {})
        oa = ev.get("oa", 0)
        ca = ev.get("ca", 0)
        oah = ev.get("oah", 0)
        cah = ev.get("cah", 0)
        total_h = oah + cah
        total_apnea = oa + ca

        ahi = (total_apnea + total_h) / usage_hours if ev else 0.0
        ai = total_apnea / usage_hours if ev else 0.0
        hi = total_h / usage_hours if ev else 0.0
        cai = ca / usage_hours if ev else 0.0
        oai = oa / usage_hours if ev else 0.0

        pressure_mode = _MODE_LABELS[acc["dominant_mode"]]
        set_pressure = acc["set_pressure"]

        summaries.append(CPAPSessionSummary(
            date=date_str,
            ahi=ahi,
            ai=ai,
            hi=hi,
            cai=cai,
            oai=oai,
            leak_50=0.0,
            leak_95=0.0,
            leak_avg=None,
            pressure_50=set_pressure,
            pressure_95=set_pressure,
            usage_hours=usage_hours,
            pressure_mode=pressure_mode,
            resp_rate_avg=None,
            tidal_volume_avg=None,
            minute_ventilation_avg=None,
            snore_avg=None,
            flow_limitation_avg=None,
        ))

        # Session timing
        try:
            date_midnight = datetime.strptime(date_str, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue

        first_start_sec = acc["first_start_sec"] or 0
        last_end_sec = acc["last_end_sec"] or acc["total_usage_sec"]

        start_dt = date_midnight + timedelta(seconds=first_start_sec)
        end_dt = date_midnight + timedelta(seconds=last_end_sec)
        if end_dt < start_dt:
            end_dt += timedelta(days=1)

        sessions.append(CPAPSession(
            start_time=start_dt,
            end_time=end_dt,
            duration_minutes=usage_hours * 60.0,
            file_type=f"PrismaLine-{pressure_mode}",
        ))

    sessions.sort(key=lambda s: s.start_time)
    return sessions, summaries


# ── Public entry point ────────────────────────────────────────────────────────

def parse_prisma_line(path: Path) -> CPAPDirectory:
    """Parse a Prisma Line SD card directory into a :class:`CPAPDirectory`.

    Reads ``config.pcfg`` for device identity and ``therapy.pdat`` for
    per-day therapy statistics and event data.

    Args:
        path: Root of the SD card or data directory (must contain ``config.pcfg``).

    Returns:
        A :class:`~open_cpap_parser.schema.CPAPDirectory` with machine info,
        daily summaries, and session metadata.

    Raises:
        FileNotFoundError: If ``config.pcfg`` or ``therapy.pdat`` are missing.
        ValueError: If the files cannot be parsed.
    """
    config_path = path / "config.pcfg"
    therapy_path = path / "therapy.pdat"

    if not config_path.is_file():
        raise FileNotFoundError(f"config.pcfg not found in {path}")
    if not therapy_path.is_file():
        raise FileNotFoundError(f"therapy.pdat not found in {path}")

    try:
        with zipfile.ZipFile(io.BytesIO(config_path.read_bytes())) as cfg_zip:
            machine = _read_device_info(cfg_zip)
    except Exception as exc:
        raise ValueError(f"Cannot read config.pcfg: {exc}") from exc

    try:
        therapy_bytes = therapy_path.read_bytes()
        with zipfile.ZipFile(io.BytesIO(therapy_bytes)) as therapy_zip:
            event_counts = _aggregate_events(therapy_zip)
            sessions, summaries = _parse_statistics(therapy_zip, event_counts)
    except Exception as exc:
        raise ValueError(f"Cannot read therapy.pdat: {exc}") from exc

    return CPAPDirectory(
        machine=machine,
        daily_summaries=summaries,
        sessions=sessions,
    )
