use chrono::{DateTime, Utc};

#[derive(Debug, Clone)]
pub struct CpapEvent {
    pub timestamp_sec: f64,
    pub event_type: String,
    pub duration_sec: Option<f64>,
    pub data: std::collections::HashMap<String, f64>,
}

#[derive(Debug, Clone)]
pub struct CpapSessionSummary {
    pub date: String,
    pub ahi: f64,
    pub ai: f64,
    pub hi: f64,
    pub cai: f64,
    pub oai: f64,
    pub leak_50: f64,
    pub leak_95: f64,
    pub leak_avg: Option<f64>,
    pub pressure_50: f64,
    pub pressure_95: f64,
    pub usage_hours: f64,
    pub pressure_mode: String,
    pub resp_rate_avg: Option<f64>,
    pub tidal_volume_avg: Option<f64>,
    pub minute_ventilation_avg: Option<f64>,
    pub snore_avg: Option<f64>,
    pub flow_limitation_avg: Option<f64>,
}

#[derive(Debug, Clone)]
pub struct TimeSeriesData {
    pub timestamps: Vec<f64>,
    pub flow_rate: Vec<f64>,
    pub mask_pressure: Vec<f64>,
    pub leak: Vec<f64>,
    pub tidal_volume: Vec<f64>,
    pub minute_ventilation: Vec<f64>,
    pub respiratory_rate: Vec<f64>,
}

#[derive(Debug, Clone)]
pub struct MachineInfo {
    pub serial_number: String,
    pub product_code: String,
    pub model: String,
    pub series: String,
    pub properties: std::collections::HashMap<String, String>,
}

#[derive(Debug, Clone)]
pub struct CpapSession {
    pub start_time: DateTime<Utc>,
    pub end_time: DateTime<Utc>,
    pub duration_minutes: f64,
    pub file_type: String,
    pub sample_rate: f64,
    pub events: Vec<CpapEvent>,
    pub timeseries: Option<TimeSeriesData>,
}

#[derive(Debug, Clone)]
pub struct CpapDirectory {
    pub machine: MachineInfo,
    pub daily_summaries: Vec<CpapSessionSummary>,
    pub sessions: Vec<CpapSession>,
}
