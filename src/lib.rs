//! PyO3 extension module exposing Rust CPAP parsers to Python.
//!
//! Compiled by Maturin into `open_cpap_parser._rust_parsers`.  Each
//! `parse_*` function accepts an absolute directory path and returns a
//! [`PyDirectory`] tree that the Python adapter layer maps into the
//! schema defined in `open_cpap_parser.schema`.

use std::path::PathBuf;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::parsers::apex;
use crate::parsers::bmc;
use crate::parsers::devilbiss;
use crate::parsers::fisher_paykel;
use crate::parsers::lowenstein;
use crate::parsers::yuwell;

mod parsers;
mod schema;

/// Device identity returned by every parser.
#[pyclass]
#[derive(Clone)]
struct PyMachineInfo {
    #[pyo3(get)]
    serial_number: String,
    #[pyo3(get)]
    product_code: String,
    #[pyo3(get)]
    model: String,
    #[pyo3(get)]
    series: String,
    #[pyo3(get)]
    properties: Vec<(String, String)>,
}

/// A single detected therapy event (apnea, hypopnea, etc.).
#[pyclass]
#[derive(Clone)]
struct PyEvent {
    #[pyo3(get)]
    timestamp_sec: f64,
    #[pyo3(get)]
    event_type: String,
    #[pyo3(get)]
    duration_sec: Option<f64>,
    #[pyo3(get)]
    data: Vec<(String, f64)>,
}

/// Aggregated daily therapy metrics for one calendar date.
#[pyclass]
#[derive(Clone)]
struct PySessionSummary {
    #[pyo3(get)]
    date: String,
    #[pyo3(get)]
    ahi: f64,
    #[pyo3(get)]
    ai: f64,
    #[pyo3(get)]
    hi: f64,
    #[pyo3(get)]
    cai: f64,
    #[pyo3(get)]
    oai: f64,
    #[pyo3(get)]
    leak_50: f64,
    #[pyo3(get)]
    leak_95: f64,
    #[pyo3(get)]
    leak_avg: Option<f64>,
    #[pyo3(get)]
    pressure_50: f64,
    #[pyo3(get)]
    pressure_95: f64,
    #[pyo3(get)]
    usage_hours: f64,
    #[pyo3(get)]
    pressure_mode: String,
    #[pyo3(get)]
    resp_rate_avg: Option<f64>,
    #[pyo3(get)]
    tidal_volume_avg: Option<f64>,
    #[pyo3(get)]
    minute_ventilation_avg: Option<f64>,
    #[pyo3(get)]
    snore_avg: Option<f64>,
    #[pyo3(get)]
    flow_limitation_avg: Option<f64>,
}

/// High-resolution waveform data exposed to Python.
///
/// High-rate track (`timestamps`): `flow_rate`, `pressure`
/// Low-rate track (`timestamps_low`): all therapy signals
#[pyclass]
#[derive(Clone, Default)]
struct PyTimeSeries {
    #[pyo3(get)]
    timestamps: Vec<f64>,
    #[pyo3(get)]
    flow_rate: Vec<f64>,
    #[pyo3(get)]
    pressure: Vec<f64>,
    #[pyo3(get)]
    timestamps_low: Vec<f64>,
    #[pyo3(get)]
    mask_pressure: Vec<f64>,
    #[pyo3(get)]
    leak: Vec<f64>,
    #[pyo3(get)]
    tidal_volume: Vec<f64>,
    #[pyo3(get)]
    minute_ventilation: Vec<f64>,
    #[pyo3(get)]
    respiratory_rate: Vec<f64>,
    #[pyo3(get)]
    snore: Vec<f64>,
    #[pyo3(get)]
    flow_limitation: Vec<f64>,
    #[pyo3(get)]
    spo2: Vec<f64>,
    #[pyo3(get)]
    pulse: Vec<f64>,
}

/// One contiguous therapy session block.
#[pyclass]
#[derive(Clone)]
struct PySession {
    #[pyo3(get)]
    start_time: String,
    #[pyo3(get)]
    end_time: String,
    #[pyo3(get)]
    duration_minutes: f64,
    #[pyo3(get)]
    file_type: String,
    #[pyo3(get)]
    events: Vec<PyEvent>,
    #[pyo3(get)]
    sample_rate: f64,
    #[pyo3(get)]
    timeseries: Option<PyTimeSeries>,
}

/// Top-level container returned by every `parse_*` function.
#[pyclass]
#[derive(Clone)]
struct PyDirectory {
    #[pyo3(get)]
    machine: PyMachineInfo,
    #[pyo3(get)]
    daily_summaries: Vec<PySessionSummary>,
    #[pyo3(get)]
    sessions: Vec<PySession>,
}

fn epoch_to_iso(ts: &chrono::DateTime<chrono::Utc>) -> String {
    ts.format("%Y-%m-%dT%H:%M:%SZ").to_string()
}

/// Parse a DeVilbiss IntelliPAP directory (DV6 or DV5 format).
///
/// # Errors
/// Raises `ValueError` if the directory cannot be parsed.
#[pyfunction]
#[pyo3(signature = (path, /))]
fn parse_devilbiss(path: String) -> PyResult<PyDirectory> {
    let p = PathBuf::from(&path);
    let dir = devilbiss::parse_dv6_directory(&p).map_err(|e| PyValueError::new_err(e))?;

    Ok(PyDirectory {
        machine: PyMachineInfo {
            serial_number: dir.machine.serial_number,
            product_code: dir.machine.product_code,
            model: dir.machine.model,
            series: dir.machine.series,
            properties: dir.machine.properties.into_iter().collect(),
        },
        daily_summaries: dir
            .daily_summaries
            .into_iter()
            .map(|s| PySessionSummary {
                date: s.date,
                ahi: s.ahi,
                ai: s.ai,
                hi: s.hi,
                cai: s.cai,
                oai: s.oai,
                leak_50: s.leak_50,
                leak_95: s.leak_95,
                leak_avg: s.leak_avg,
                pressure_50: s.pressure_50,
                pressure_95: s.pressure_95,
                usage_hours: s.usage_hours,
                pressure_mode: s.pressure_mode,
                resp_rate_avg: s.resp_rate_avg,
                tidal_volume_avg: s.tidal_volume_avg,
                minute_ventilation_avg: s.minute_ventilation_avg,
                snore_avg: s.snore_avg,
                flow_limitation_avg: s.flow_limitation_avg,
            })
            .collect(),
        sessions: dir
            .sessions
            .into_iter()
            .map(|s| PySession {
                start_time: epoch_to_iso(&s.start_time),
                end_time: epoch_to_iso(&s.end_time),
                duration_minutes: s.duration_minutes,
                file_type: s.file_type,
                events: s
                    .events
                    .into_iter()
                    .map(|e| PyEvent {
                        timestamp_sec: e.timestamp_sec,
                        event_type: e.event_type,
                        duration_sec: e.duration_sec,
                        data: e.data.into_iter().collect(),
                    })
                    .collect(),
                sample_rate: s.sample_rate,
                timeseries: None,
            })
            .collect(),
    })
}

/// Return `True` if *path* is a DeVilbiss IntelliPAP data directory.
#[pyfunction]
#[pyo3(signature = (path, /))]
fn can_handle_devilbiss(path: String) -> bool {
    let p = PathBuf::from(&path);
    devilbiss::can_handle(&p)
}

/// Parse a BMC / 3B Medical data directory.
///
/// # Errors
/// Raises `ValueError` if the directory cannot be parsed.
#[pyfunction]
#[pyo3(signature = (path, /))]
fn parse_bmc(path: String) -> PyResult<PyDirectory> {
    let dir = bmc::parse_bmc(&path).map_err(|e| PyValueError::new_err(e))?;

    Ok(PyDirectory {
        machine: PyMachineInfo {
            serial_number: dir.machine.serial_number,
            product_code: dir.machine.product_code,
            model: dir.machine.model,
            series: dir.machine.series,
            properties: dir.machine.properties.into_iter().collect(),
        },
        daily_summaries: dir
            .daily_summaries
            .into_iter()
            .map(|s| PySessionSummary {
                date: s.date,
                ahi: s.ahi,
                ai: s.ai,
                hi: s.hi,
                cai: s.cai,
                oai: s.oai,
                leak_50: s.leak_50,
                leak_95: s.leak_95,
                leak_avg: s.leak_avg,
                pressure_50: s.pressure_50,
                pressure_95: s.pressure_95,
                usage_hours: s.usage_hours,
                pressure_mode: s.pressure_mode,
                resp_rate_avg: s.resp_rate_avg,
                tidal_volume_avg: s.tidal_volume_avg,
                minute_ventilation_avg: s.minute_ventilation_avg,
                snore_avg: s.snore_avg,
                flow_limitation_avg: s.flow_limitation_avg,
            })
            .collect(),
        sessions: dir
            .sessions
            .into_iter()
            .map(|s| PySession {
                start_time: epoch_to_iso(&s.start_time),
                end_time: epoch_to_iso(&s.end_time),
                duration_minutes: s.duration_minutes,
                file_type: s.file_type,
                events: s
                    .events
                    .into_iter()
                    .map(|e| PyEvent {
                        timestamp_sec: e.timestamp_sec,
                        event_type: e.event_type,
                        duration_sec: e.duration_sec,
                        data: e.data.into_iter().collect(),
                    })
                    .collect(),
                sample_rate: s.sample_rate,
                timeseries: None,
            })
            .collect(),
    })
}

/// Return `True` if *path* is a BMC / 3B Medical data directory.
#[pyfunction]
#[pyo3(signature = (path, /))]
fn can_handle_bmc(path: String) -> bool {
    bmc::can_handle(&path)
}

/// Parse an Apex Medical data directory (`APDATA/*.APC` files).
///
/// # Errors
/// Raises `ValueError` if the directory cannot be parsed.
#[pyfunction]
#[pyo3(signature = (path, /))]
fn parse_apex(path: String) -> PyResult<PyDirectory> {
    let p = PathBuf::from(&path);
    let dir = apex::parse_apex(&p).map_err(|e| PyValueError::new_err(e))?;

    Ok(PyDirectory {
        machine: PyMachineInfo {
            serial_number: dir.machine.serial_number,
            product_code: dir.machine.product_code,
            model: dir.machine.model,
            series: dir.machine.series,
            properties: dir.machine.properties.into_iter().collect(),
        },
        daily_summaries: dir
            .daily_summaries
            .into_iter()
            .map(|s| PySessionSummary {
                date: s.date,
                ahi: s.ahi,
                ai: s.ai,
                hi: s.hi,
                cai: s.cai,
                oai: s.oai,
                leak_50: s.leak_50,
                leak_95: s.leak_95,
                leak_avg: s.leak_avg,
                pressure_50: s.pressure_50,
                pressure_95: s.pressure_95,
                usage_hours: s.usage_hours,
                pressure_mode: s.pressure_mode,
                resp_rate_avg: s.resp_rate_avg,
                tidal_volume_avg: s.tidal_volume_avg,
                minute_ventilation_avg: s.minute_ventilation_avg,
                snore_avg: s.snore_avg,
                flow_limitation_avg: s.flow_limitation_avg,
            })
            .collect(),
        sessions: dir
            .sessions
            .into_iter()
            .map(|s| PySession {
                start_time: epoch_to_iso(&s.start_time),
                end_time: epoch_to_iso(&s.end_time),
                duration_minutes: s.duration_minutes,
                file_type: s.file_type,
                events: s
                    .events
                    .into_iter()
                    .map(|e| PyEvent {
                        timestamp_sec: e.timestamp_sec,
                        event_type: e.event_type,
                        duration_sec: e.duration_sec,
                        data: e.data.into_iter().collect(),
                    })
                    .collect(),
                sample_rate: s.sample_rate,
                timeseries: None,
            })
            .collect(),
    })
}

/// Return `True` if *path* is an Apex Medical data directory.
#[pyfunction]
#[pyo3(signature = (path, /))]
fn can_handle_apex(path: String) -> bool {
    let p = PathBuf::from(&path);
    apex::can_handle(&p)
}

/// Parse a Lowenstein / Weinmann data directory (`WM_DATA.TDF`).
///
/// # Errors
/// Raises `ValueError` if the directory cannot be parsed.
#[pyfunction]
#[pyo3(signature = (path, /))]
fn parse_lowenstein(path: String) -> PyResult<PyDirectory> {
    let p = PathBuf::from(&path);
    let dir = lowenstein::parse_lowenstein(&p).map_err(|e| PyValueError::new_err(e))?;

    Ok(PyDirectory {
        machine: PyMachineInfo {
            serial_number: dir.machine.serial_number,
            product_code: dir.machine.product_code,
            model: dir.machine.model,
            series: dir.machine.series,
            properties: dir.machine.properties.into_iter().collect(),
        },
        daily_summaries: dir
            .daily_summaries
            .into_iter()
            .map(|s| PySessionSummary {
                date: s.date,
                ahi: s.ahi,
                ai: s.ai,
                hi: s.hi,
                cai: s.cai,
                oai: s.oai,
                leak_50: s.leak_50,
                leak_95: s.leak_95,
                leak_avg: s.leak_avg,
                pressure_50: s.pressure_50,
                pressure_95: s.pressure_95,
                usage_hours: s.usage_hours,
                pressure_mode: s.pressure_mode,
                resp_rate_avg: s.resp_rate_avg,
                tidal_volume_avg: s.tidal_volume_avg,
                minute_ventilation_avg: s.minute_ventilation_avg,
                snore_avg: s.snore_avg,
                flow_limitation_avg: s.flow_limitation_avg,
            })
            .collect(),
        sessions: dir
            .sessions
            .into_iter()
            .map(|s| PySession {
                start_time: epoch_to_iso(&s.start_time),
                end_time: epoch_to_iso(&s.end_time),
                duration_minutes: s.duration_minutes,
                file_type: s.file_type,
                events: s
                    .events
                    .into_iter()
                    .map(|e| PyEvent {
                        timestamp_sec: e.timestamp_sec,
                        event_type: e.event_type,
                        duration_sec: e.duration_sec,
                        data: e.data.into_iter().collect(),
                    })
                    .collect(),
                sample_rate: s.sample_rate,
                timeseries: None,
            })
            .collect(),
    })
}

/// Return `True` if *path* is a Lowenstein / Weinmann data directory.
#[pyfunction]
#[pyo3(signature = (path, /))]
fn can_handle_lowenstein(path: String) -> bool {
    let p = PathBuf::from(&path);
    lowenstein::can_handle(&p)
}

/// Parse a Fisher & Paykel SleepStyle data directory (`FPHCARE/ICON/<serial>/SUM*.fph`).
///
/// # Errors
/// Raises `ValueError` if the directory cannot be parsed.
#[pyfunction]
#[pyo3(signature = (path, /))]
fn parse_fisher_paykel(path: String) -> PyResult<PyDirectory> {
    let p = PathBuf::from(&path);
    let dir = fisher_paykel::parse_fisher_paykel(&p).map_err(|e| PyValueError::new_err(e))?;

    Ok(PyDirectory {
        machine: PyMachineInfo {
            serial_number: dir.machine.serial_number,
            product_code: dir.machine.product_code,
            model: dir.machine.model,
            series: dir.machine.series,
            properties: dir.machine.properties.into_iter().collect(),
        },
        daily_summaries: dir
            .daily_summaries
            .into_iter()
            .map(|s| PySessionSummary {
                date: s.date,
                ahi: s.ahi,
                ai: s.ai,
                hi: s.hi,
                cai: s.cai,
                oai: s.oai,
                leak_50: s.leak_50,
                leak_95: s.leak_95,
                leak_avg: s.leak_avg,
                pressure_50: s.pressure_50,
                pressure_95: s.pressure_95,
                usage_hours: s.usage_hours,
                pressure_mode: s.pressure_mode,
                resp_rate_avg: s.resp_rate_avg,
                tidal_volume_avg: s.tidal_volume_avg,
                minute_ventilation_avg: s.minute_ventilation_avg,
                snore_avg: s.snore_avg,
                flow_limitation_avg: s.flow_limitation_avg,
            })
            .collect(),
        sessions: dir
            .sessions
            .into_iter()
            .map(|s| PySession {
                start_time: epoch_to_iso(&s.start_time),
                end_time: epoch_to_iso(&s.end_time),
                duration_minutes: s.duration_minutes,
                file_type: s.file_type,
                events: Vec::new(),
                sample_rate: s.sample_rate,
                timeseries: None,
            })
            .collect(),
    })
}

/// Return `True` if *path* is a Fisher & Paykel SleepStyle data directory.
#[pyfunction]
#[pyo3(signature = (path, /))]
fn can_handle_fisher_paykel(path: String) -> bool {
    let p = PathBuf::from(&path);
    fisher_paykel::can_handle(&p)
}

/// Parse a Yuwell / BreathCare data directory (`.BYS` format A–D).
///
/// # Errors
/// Raises `ValueError` if the directory cannot be parsed.
#[pyfunction]
#[pyo3(signature = (path, /))]
fn parse_yuwell(path: String) -> PyResult<PyDirectory> {
    let p = PathBuf::from(&path);
    let dir = yuwell::parse_yuwell(&p).map_err(|e| PyValueError::new_err(e))?;

    Ok(PyDirectory {
        machine: PyMachineInfo {
            serial_number: dir.machine.serial_number,
            product_code: dir.machine.product_code,
            model: dir.machine.model,
            series: dir.machine.series,
            properties: dir.machine.properties.into_iter().collect(),
        },
        daily_summaries: dir
            .daily_summaries
            .into_iter()
            .map(|s| PySessionSummary {
                date: s.date,
                ahi: s.ahi,
                ai: s.ai,
                hi: s.hi,
                cai: s.cai,
                oai: s.oai,
                leak_50: s.leak_50,
                leak_95: s.leak_95,
                leak_avg: s.leak_avg,
                pressure_50: s.pressure_50,
                pressure_95: s.pressure_95,
                usage_hours: s.usage_hours,
                pressure_mode: s.pressure_mode,
                resp_rate_avg: s.resp_rate_avg,
                tidal_volume_avg: s.tidal_volume_avg,
                minute_ventilation_avg: s.minute_ventilation_avg,
                snore_avg: s.snore_avg,
                flow_limitation_avg: s.flow_limitation_avg,
            })
            .collect(),
        sessions: dir
            .sessions
            .into_iter()
            .map(|s| PySession {
                start_time: epoch_to_iso(&s.start_time),
                end_time: epoch_to_iso(&s.end_time),
                duration_minutes: s.duration_minutes,
                file_type: s.file_type,
                events: Vec::new(),
                sample_rate: s.sample_rate,
                timeseries: None,
            })
            .collect(),
    })
}

/// Return `True` if *path* is a Yuwell / BreathCare data directory.
#[pyfunction]
#[pyo3(signature = (path, /))]
fn can_handle_yuwell(path: String) -> bool {
    let p = PathBuf::from(&path);
    yuwell::can_handle(&p)
}

#[pymodule]
fn _rust_parsers(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_devilbiss, m)?)?;
    m.add_function(wrap_pyfunction!(can_handle_devilbiss, m)?)?;
    m.add_function(wrap_pyfunction!(parse_bmc, m)?)?;
    m.add_function(wrap_pyfunction!(can_handle_bmc, m)?)?;
    m.add_function(wrap_pyfunction!(parse_apex, m)?)?;
    m.add_function(wrap_pyfunction!(can_handle_apex, m)?)?;
    m.add_function(wrap_pyfunction!(parse_lowenstein, m)?)?;
    m.add_function(wrap_pyfunction!(can_handle_lowenstein, m)?)?;
    m.add_function(wrap_pyfunction!(parse_fisher_paykel, m)?)?;
    m.add_function(wrap_pyfunction!(can_handle_fisher_paykel, m)?)?;
    m.add_function(wrap_pyfunction!(parse_yuwell, m)?)?;
    m.add_function(wrap_pyfunction!(can_handle_yuwell, m)?)?;
    m.add_class::<PyDirectory>()?;
    m.add_class::<PyMachineInfo>()?;
    m.add_class::<PySessionSummary>()?;
    m.add_class::<PySession>()?;
    m.add_class::<PyEvent>()?;
    m.add_class::<PyTimeSeries>()?;
    Ok(())
}
