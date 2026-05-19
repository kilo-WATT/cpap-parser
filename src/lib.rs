use std::path::PathBuf;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::parsers::apex;
use crate::parsers::bmc;
use crate::parsers::devilbiss;
use crate::parsers::lowenstein;

mod parsers;
mod schema;

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
}

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

#[pyfunction]
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
            })
            .collect(),
    })
}

#[pyfunction]
fn can_handle(path: String) -> bool {
    let p = PathBuf::from(&path);
    devilbiss::can_handle(&p)
}

#[pyfunction]
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
            })
            .collect(),
    })
}

#[pyfunction]
fn can_handle_bmc(path: String) -> bool {
    bmc::can_handle(&path)
}

#[pyfunction]
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
            })
            .collect(),
    })
}

#[pyfunction]
fn can_handle_apex(path: String) -> bool {
    let p = PathBuf::from(&path);
    apex::can_handle(&p)
}

#[pyfunction]
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
            })
            .collect(),
    })
}

#[pyfunction]
fn can_handle_lowenstein(path: String) -> bool {
    let p = PathBuf::from(&path);
    lowenstein::can_handle(&p)
}

#[pymodule]
fn _rust_parsers(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_devilbiss, m)?)?;
    m.add_function(wrap_pyfunction!(can_handle, m)?)?;
    m.add_function(wrap_pyfunction!(parse_bmc, m)?)?;
    m.add_function(wrap_pyfunction!(can_handle_bmc, m)?)?;
    m.add_function(wrap_pyfunction!(parse_apex, m)?)?;
    m.add_function(wrap_pyfunction!(can_handle_apex, m)?)?;
    m.add_function(wrap_pyfunction!(parse_lowenstein, m)?)?;
    m.add_function(wrap_pyfunction!(can_handle_lowenstein, m)?)?;
    m.add_class::<PyDirectory>()?;
    m.add_class::<PyMachineInfo>()?;
    m.add_class::<PySessionSummary>()?;
    m.add_class::<PySession>()?;
    m.add_class::<PyEvent>()?;
    Ok(())
}
