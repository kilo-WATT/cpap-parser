from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class MachineInfo(BaseModel):
    serial_number: str
    product_code: str = ""
    model: str = ""
    series: str = ""
    properties: dict[str, str] = Field(default_factory=dict)


class CPAPEvent(BaseModel):
    timestamp_sec: float
    event_type: str
    duration_sec: Optional[float] = None
    data: dict[str, float] = Field(default_factory=dict)


class CPAPSessionSummary(BaseModel):
    date: date
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


class TimeSeriesData(BaseModel):
    timestamps: list[float] = Field(default_factory=list)
    flow_rate: list[float] = Field(default_factory=list)
    mask_pressure: list[float] = Field(default_factory=list)
    leak: list[float] = Field(default_factory=list)
    tidal_volume: list[float] = Field(default_factory=list)
    minute_ventilation: list[float] = Field(default_factory=list)
    respiratory_rate: list[float] = Field(default_factory=list)
    spo2: list[float] = Field(default_factory=list)
    pulse: list[float] = Field(default_factory=list)


class CPAPSession(BaseModel):
    start_time: datetime
    end_time: datetime
    duration_minutes: float = 0.0
    file_type: str = ""
    sample_rate: float = 0.0
    events: list[CPAPEvent] = Field(default_factory=list)
    timeseries: Optional[TimeSeriesData] = None


class CPAPDirectory(BaseModel):
    machine: MachineInfo
    daily_summaries: list[CPAPSessionSummary] = Field(default_factory=list)
    sessions: list[CPAPSession] = Field(default_factory=list)
