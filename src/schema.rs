//! Shared data structures returned by all manufacturer parsers.
//!
//! These types mirror the Python Pydantic schema in
//! `open_cpap_parser/schema.py` and are mapped to Python via the PyO3
//! wrapper classes in `src/lib.rs`.

use chrono::{DateTime, Utc};

/// A single therapy event (apnea, hypopnea, flow limitation, etc.).
#[derive(Debug, Clone)]
pub struct CpapEvent {
    /// Seconds since session start when the event was detected.
    pub timestamp_sec: f64,
    /// Human-readable event label (e.g. `"ObstructiveApnea"`).
    pub event_type: String,
    /// Duration of the event in seconds, if known.
    pub duration_sec: Option<f64>,
    /// Additional numeric data keyed by signal name.
    pub data: std::collections::HashMap<String, f64>,
}

/// Aggregated daily therapy metrics for one calendar date.
#[derive(Debug, Clone)]
pub struct CpapSessionSummary {
    /// ISO-8601 date string (`YYYY-MM-DD`).
    pub date: String,
    /// Apnea–Hypopnea Index (events/hour).
    pub ahi: f64,
    /// Apnea Index — obstructive + central (events/hour).
    pub ai: f64,
    /// Hypopnea Index (events/hour).
    pub hi: f64,
    /// Central Apnea Index (events/hour).
    pub cai: f64,
    /// Obstructive Apnea Index (events/hour).
    pub oai: f64,
    /// 50th-percentile leak rate (L/min).
    pub leak_50: f64,
    /// 95th-percentile leak rate (L/min).
    pub leak_95: f64,
    /// Mean leak rate (L/min), if available.
    pub leak_avg: Option<f64>,
    /// 50th-percentile mask pressure (cmH₂O).
    pub pressure_50: f64,
    /// 95th-percentile mask pressure (cmH₂O).
    pub pressure_95: f64,
    /// Total therapy time (hours).
    pub usage_hours: f64,
    /// Therapy mode label (e.g. `"CPAP"`, `"APAP"`, `"BiLevel"`).
    pub pressure_mode: String,
    /// Mean respiratory rate (breaths/min), if available.
    pub resp_rate_avg: Option<f64>,
    /// Mean tidal volume (mL), if available.
    pub tidal_volume_avg: Option<f64>,
    /// Mean minute ventilation (L/min), if available.
    pub minute_ventilation_avg: Option<f64>,
    /// Mean snore index (events/hour), if available.
    pub snore_avg: Option<f64>,
    /// Mean flow-limitation index (events/hour), if available.
    pub flow_limitation_avg: Option<f64>,
}

/// High-resolution time-series signals for one therapy session.
///
/// Two sample-rate tracks are supported for devices that record signals at
/// different rates (e.g. Löwenstein RespFlow at 10 Hz, therapy signals at 1 Hz).
#[derive(Debug, Clone, Default)]
pub struct TimeSeriesData {
    /// High-rate sample timestamps in seconds from session start.
    pub timestamps: Vec<f64>,
    /// Inspiratory/expiratory flow rate (L/min) — high rate.
    pub flow_rate: Vec<f64>,
    /// Delivered mask pressure (cmH₂O) — high rate.
    pub pressure: Vec<f64>,
    /// Low-rate sample timestamps in seconds from session start.
    pub timestamps_low: Vec<f64>,
    /// EPAP / mask pressure (cmH₂O) — low rate.
    pub mask_pressure: Vec<f64>,
    /// Total leak rate (L/min) — low rate.
    pub leak: Vec<f64>,
    /// Tidal volume (mL) — low rate.
    pub tidal_volume: Vec<f64>,
    /// Minute ventilation (L/min) — low rate.
    pub minute_ventilation: Vec<f64>,
    /// Respiratory rate (breaths/min) — low rate.
    pub respiratory_rate: Vec<f64>,
    /// Snore index — low rate.
    pub snore: Vec<f64>,
    /// Flow limitation index — low rate.
    pub flow_limitation: Vec<f64>,
    /// Blood oxygen saturation (%) — low rate.
    pub spo2: Vec<f64>,
    /// Heart rate (bpm) — low rate.
    pub pulse: Vec<f64>,
}

/// Device identity metadata read from the SD card.
#[derive(Debug, Clone)]
pub struct MachineInfo {
    /// Device serial number.
    pub serial_number: String,
    /// Manufacturer product code / SKU.
    pub product_code: String,
    /// Human-readable model name.
    pub model: String,
    /// Product series or brand family.
    pub series: String,
    /// Arbitrary key-value properties (firmware version, etc.).
    pub properties: std::collections::HashMap<String, String>,
}

/// One contiguous therapy session block.
#[derive(Debug, Clone)]
pub struct CpapSession {
    /// UTC timestamp when therapy started.
    pub start_time: DateTime<Utc>,
    /// UTC timestamp when therapy ended.
    pub end_time: DateTime<Utc>,
    /// Session length in minutes.
    pub duration_minutes: f64,
    /// Source file type label (e.g. `"BRP"`, `"APEX-APAP"`, `"WM-CPAP"`).
    pub file_type: String,
    /// Nominal waveform sample rate in Hz (0.0 if no waveform data).
    pub sample_rate: f64,
    /// Discrete therapy events detected during this session.
    pub events: Vec<CpapEvent>,
    /// High-resolution time-series signals, if decoded.
    pub timeseries: Option<TimeSeriesData>,
}

/// Top-level container returned by every manufacturer parser.
#[derive(Debug, Clone)]
pub struct CpapDirectory {
    /// Device identity information.
    pub machine: MachineInfo,
    /// One summary record per calendar date of therapy.
    pub daily_summaries: Vec<CpapSessionSummary>,
    /// Individual session blocks, potentially multiple per day.
    pub sessions: Vec<CpapSession>,
}
