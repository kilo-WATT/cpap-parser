from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class MachineInfo(BaseModel):
    """Metadata about the CPAP machine extracted from its data files.

    Attributes:
        serial_number: Machine serial number (may be "Unknown").
        product_code: Manufacturer model number string.
        model: Human-readable model name.
        series: Product series designation.
        properties: Arbitrary key-value metadata from the device.
    """
    serial_number: str
    product_code: str = ""
    model: str = ""
    series: str = ""
    properties: dict[str, str] = Field(default_factory=dict)


class CPAPEvent(BaseModel):
    """A single therapy event recorded by the device.

    Attributes:
        timestamp_sec: Onset time in seconds relative to session start.
        event_type: Event classification string (e.g. "Obstructive Apnea").
        duration_sec: Event duration in seconds, if available.
        data: Additional numeric metadata for the event.
    """
    timestamp_sec: float
    event_type: str
    duration_sec: Optional[float] = None
    data: dict[str, float] = Field(default_factory=dict)


class CPAPSessionSummary(BaseModel):
    """Daily aggregate summary of CPAP therapy.

    All event indices (ahi, ai, hi, cai, oai) are per-hour rates as
    reported by the device.  Use ``usage_hours`` to convert them to
    event counts for database storage.

    Attributes:
        date: Calendar date of the therapy session.
        start_time: Actual session start datetime (naive, machine-local).
            ``None`` when only daily-summary files are available.
        ahi: Apnea-Hypopnea Index (events/hour).
        ai: Apnea Index (events/hour).
        hi: Hypopnea Index (events/hour).
        cai: Central Apnea Index (events/hour).
        oai: Obstructive Apnea Index (events/hour).
        leak_50: Median leak rate (L/min).
        leak_95: 95th percentile leak rate (L/min).
        leak_avg: Mean leak rate, when available.
        pressure_50: Median mask pressure (cmH2O).
        pressure_95: 95th percentile mask pressure (cmH2O).
        usage_hours: Therapy duration in hours.
        pressure_mode: Pressure mode label (e.g. "CPAP", "APAP").
        resp_rate_avg: Average respiratory rate (breaths/min).
        tidal_volume_avg: Average tidal volume (mL).
        minute_ventilation_avg: Average minute ventilation (L/min).
        snore_avg: Average snore index.
        flow_limitation_avg: Average flow limitation index.
        spo2_avg: Mean SpO2 across the session (%), or ``None`` if unavailable.
        spo2_min: Minimum SpO2 across the session (%), or ``None`` if unavailable.
        has_spo2: ``True`` when oximetry data is present for this session.
        arousal_count: Total arousal events, or ``None`` if not reported.
    """
    date: date
    start_time: Optional[datetime] = None
    ahi: float = 0.0
    ai: float = 0.0
    hi: float = 0.0
    cai: float = 0.0
    oai: float = 0.0
    leak_50: float = 0.0
    leak_95: float = 0.0
    leak_avg: Optional[float] = None
    pressure_50: float = 0.0
    pressure_95: float = 0.0
    usage_hours: float = 0.0
    pressure_mode: str = ""
    resp_rate_avg: Optional[float] = None
    tidal_volume_avg: Optional[float] = None
    minute_ventilation_avg: Optional[float] = None
    snore_avg: Optional[float] = None
    flow_limitation_avg: Optional[float] = None
    spo2_avg: Optional[float] = None
    spo2_min: Optional[float] = None
    has_spo2: bool = False
    arousal_count: Optional[int] = None


class TimeSeriesData(BaseModel):
    """High-resolution waveform data decoded from device signal channels.

    Two sample-rate tracks are supported to handle devices (e.g. ResMed)
    that record breathing signals at high rate (BRP, ~25 Hz) and therapy
    signals at low rate (PLD, ~0.5 Hz) in separate files.

    High-rate track (``timestamps``):
        flow_rate, pressure

    Low-rate track (``timestamps_low``):
        mask_pressure, leak, tidal_volume, minute_ventilation,
        respiratory_rate, snore, flow_limitation

    Oximetry (may share either track):
        spo2, pulse

    Timestamp contract
    ------------------
    Both ``timestamps`` and ``timestamps_low`` are **UTC Unix epoch seconds**
    (float), suitable for direct use as a datetime index::

        pd.to_datetime(ts.timestamps, unit="s", utc=True)

    **UTC assumption:** device files that store local time only (e.g. Löwenstein
    Prisma Line) are treated as UTC.  If the recording device was configured to
    a non-UTC timezone, callers should apply the appropriate offset after parsing.
    All adapters follow this convention; no adapter returns timezone-aware or
    relative timestamps.
    """
    # High-rate track (e.g. BRP at 25 Hz)
    timestamps: list[float] = Field(default_factory=list)
    flow_rate: list[float] = Field(default_factory=list)
    pressure: list[float] = Field(default_factory=list)

    # Low-rate track (e.g. PLD at 0.5 Hz)
    timestamps_low: list[float] = Field(default_factory=list)
    mask_pressure: list[float] = Field(default_factory=list)
    leak: list[float] = Field(default_factory=list)
    tidal_volume: list[float] = Field(default_factory=list)
    minute_ventilation: list[float] = Field(default_factory=list)
    respiratory_rate: list[float] = Field(default_factory=list)
    snore: list[float] = Field(default_factory=list)
    flow_limitation: list[float] = Field(default_factory=list)

    # Oximetry
    spo2: list[float] = Field(default_factory=list)
    pulse: list[float] = Field(default_factory=list)


class CPAPSession(BaseModel):
    """A single session block parsed from a device data file.

    Typically corresponds to one EDF or binary data file in the
    device's DATALOG partition.

    Attributes:
        start_time: Session start datetime.
        end_time: Session end datetime.
        duration_minutes: Total session duration in minutes.
        file_type: Short code identifying the data file type
            (e.g. "BRP", "PLD", "EVE", "EDF", "FPH").
        sample_rate: Nominal sample rate of the session (Hz).
        events: Per-event annotations parsed from the file.
        timeseries: High-resolution signal data, if requested.
    """
    start_time: datetime
    end_time: datetime
    duration_minutes: float = 0.0
    file_type: str = ""
    sample_rate: float = 0.0
    events: list[CPAPEvent] = Field(default_factory=list)
    timeseries: Optional[TimeSeriesData] = None


class CPAPDirectory(BaseModel):
    """Top-level output model for a single CPAP data directory.

    Represents all data extracted from one SD card or data folder.

    Attributes:
        machine: Information about the CPAP machine.
        daily_summaries: Per-date therapy summaries.
        sessions: Per-file session data with optional events and
            time-series.
    """
    machine: MachineInfo
    daily_summaries: list[CPAPSessionSummary] = Field(default_factory=list)
    sessions: list[CPAPSession] = Field(default_factory=list)
