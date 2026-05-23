use std::collections::HashMap;
use std::io::{self, Read};
use std::path::Path;

use binrw::BinRead;
use chrono::{TimeZone, Utc};

use crate::schema::{
    CpapDirectory, CpapSession, CpapSessionSummary, MachineInfo,
};

const DEVILBISS_EPOCH: i64 = 1009843200; // 2002-01-01 00:00:00 UTC

#[allow(dead_code)]
fn dv6_timestamp(buf: &[u8; 4]) -> i64 {
    i64::from(u32::from_be_bytes(*buf)) + DEVILBISS_EPOCH
}

#[allow(dead_code)]
fn dv6_timestamp_slice(buf: &[u8]) -> Option<i64> {
    let arr: [u8; 4] = buf.get(..4)?.try_into().ok()?;
    Some(dv6_timestamp(&arr))
}

#[allow(dead_code)]
#[derive(BinRead)]
#[br(little)]
struct SetBinRec {
    unknown_00: u8,
    #[br(count = 11)]
    serial: Vec<u8>,
    language: u8,
    capabilities: u8,
    unknown_11: u8,
    cpap_pressure: u8,
    unknown_12: u8,
    max_pressure: u8,
    unknown_13: u8,
    min_pressure: u8,
    alg_apnea_threshold: u8,
    alg_apnea_duration: u8,
    alg_hypop_threshold: u8,
    alg_hypop_duration: u8,
    ramp_pressure: u8,
    unknown_01: u8,
    ramp_duration: u8,
    unknown_02: [u8; 3],
    smartflex_setting: u8,
    smartflex_when: u8,
    insp_flow_rounding: u8,
    exp_flow_rounding: u8,
    compliance_hours: u8,
    unknown_03: u8,
    tubing_diameter: u8,
    autostart_setting: u8,
    unknown_04: u8,
    show_hide: u8,
    unknown_05: u8,
    lock_flags: u8,
    unknown_06: u8,
    humidifier_setting: u8,
    unknown_7: u8,
    possible_alg_apnea: u8,
    unknown_8: [u8; 7],
    bacteria_filter: u8,
    unused: [u8; 73],
    checksum: u8,
}

#[allow(dead_code)]
#[derive(Debug, Clone)]
pub struct DevilbissDailySummary {
    start_time: i64,
    stop_time: i64,
    written: i64,
    hours: f64,
    pressure_set_min: f64,
    pressure_set_max: f64,
    pressure_avg: f64,
    pressure_max: f64,
    pressure_50: f64,
    pressure_90: f64,
    pressure_95: f64,
    pressure_std_dev: f64,
    leak_avg: f64,
    leak_max: f64,
    leak_50: f64,
    leak_90: f64,
    leak_95: f64,
    leak_std_dev: f64,
    tidal_volume: f64,
    avg_breath_rate: f64,
    snores: f64,
    time_in_exp: f64,
    time_in_fl: f64,
    time_in_pb: f64,
    mask_fit: f64,
    index_oa: f64,
    index_ca: f64,
    index_hyp: f64,
}

fn parse_sbin(data: &[u8]) -> Result<Vec<DevilbissDailySummary>, String> {
    let ep = DEVILBISS_EPOCH;
    let rec_size: usize = 55;
    let n = data.len() / rec_size;
    let mut summaries = Vec::with_capacity(n);

    for i in 0..n {
        let pos = i * rec_size;
        if pos + rec_size > data.len() {
            break;
        }
        let r = &data[pos..pos + rec_size];

        let start_time = u32::from_be_bytes([r[0], r[1], r[2], r[3]]) as i64 + ep;
        let stop_time = u32::from_be_bytes([r[4], r[5], r[6], r[7]]) as i64 + ep;
        let written = u32::from_be_bytes([r[8], r[9], r[10], r[11]]) as i64 + ep;

        summaries.push(DevilbissDailySummary {
            start_time,
            stop_time,
            written,
            hours: f64::from(r[12]) / 10.0,
            pressure_set_min: f64::from(r[48]) / 10.0,
            pressure_set_max: f64::from(r[49]) / 10.0,
            pressure_avg: f64::from(r[14]) / 10.0,
            pressure_max: f64::from(r[15]) / 10.0,
            pressure_50: f64::from(r[16]) / 10.0,
            pressure_90: f64::from(r[17]) / 10.0,
            pressure_95: f64::from(r[18]) / 10.0,
            pressure_std_dev: f64::from(r[19]) / 10.0,
            leak_avg: f64::from(r[21]) / 10.0,
            leak_max: f64::from(r[22]) / 10.0,
            leak_50: f64::from(r[23]) / 10.0,
            leak_90: f64::from(r[24]) / 10.0,
            leak_95: f64::from(r[25]) / 10.0,
            leak_std_dev: f64::from(r[26]) / 10.0,
            tidal_volume: f64::from(u16::from_le_bytes([r[27], r[28]])),
            avg_breath_rate: f64::from(r[29]),
            snores: f64::from(r[31]),
            time_in_exp: f64::from(r[32]) / 2.0,
            time_in_fl: f64::from(r[33]) / 2.0,
            time_in_pb: f64::from(r[34]) / 2.0,
            mask_fit: f64::from(r[35]) / 2.0,
            index_oa: f64::from(r[36]) / 4.0,
            index_ca: f64::from(r[37]) / 4.0,
            index_hyp: f64::from(r[38]) / 4.0,
        });
    }
    Ok(summaries)
}

struct SessionInfo {
    begin: i64,
    end: i64,
}

fn parse_ubin(data: &[u8]) -> Result<Vec<SessionInfo>, String> {
    let rec_size: usize = 9;
    let n = data.len() / rec_size;
    let mut sessions = Vec::with_capacity(n);
    for i in 0..n {
        let pos = i * rec_size;
        if pos + rec_size > data.len() {
            break;
        }
        let r = &data[pos..pos + rec_size];
        let begin = u32::from_be_bytes([r[0], r[1], r[2], r[3]]) as i64 + DEVILBISS_EPOCH;
        let end = u32::from_be_bytes([r[4], r[5], r[6], r[7]]) as i64 + DEVILBISS_EPOCH;
        sessions.push(SessionInfo { begin, end });
    }
    Ok(sessions)
}

fn parse_verbin(data: &[u8]) -> (String, String) {
    let mut serial = String::new();
    let mut model = String::new();
    let mut field_idx = 0usize;
    let mut current = Vec::new();
    for &b in data {
        if b == 0 || b == 0xFF {
            let s = String::from_utf8_lossy(&current).trim().to_string();
            match field_idx {
                1 => serial = s,
                2 => model = s,
                _ => {}
            }
            current.clear();
            field_idx += 1;
        } else {
            current.push(b);
        }
    }
    if !current.is_empty() {
        let s = String::from_utf8_lossy(&current).trim().to_string();
        match field_idx {
            1 => serial = s,
            2 => model = s,
            _ => {}
        }
    }
    (serial, model)
}

/// A rolling-buffer file reader for DeVilbiss DV6 binary log files.
///
/// DV6 files use a circular-buffer layout where new records wrap around
/// to the beginning after reaching the end of the pre-allocated space.
#[allow(dead_code)]
pub struct Dv6RollingFile {
    data: Vec<u8>,
    record_length: usize,
    wrap_record: usize,
    record_number: usize,
    current_pos: usize,
    wrapped: bool,
}

impl Dv6RollingFile {
    fn open(data: Vec<u8>) -> Result<Self, String> {
        if data.len() < 56 {
            return Err("File too small for header".into());
        }
        let hdr = &data[..56];
        let record_length = hdr[17] as usize;
        let wrap_record = u32::from_be_bytes([hdr[18], hdr[19], hdr[20], hdr[21]]) as usize;
        if record_length == 0 {
            return Err("Zero record length in header".into());
        }
        let seekpos = 56 + wrap_record * record_length;
        let clamped = seekpos.min(data.len());
        Ok(Dv6RollingFile {
            data,
            record_length,
            wrap_record,
            record_number: wrap_record,
            current_pos: clamped,
            wrapped: false,
        })
    }

    #[allow(dead_code)]
    fn get_next(&mut self) -> Option<&[u8]> {
        if self.wrapped && self.record_number == self.wrap_record {
            return None;
        }
        if self.current_pos + self.record_length > self.data.len() {
            if self.wrapped {
                return None;
            }
            self.current_pos = 56;
            self.record_number = 1;
            self.wrapped = true;
        }
        if self.current_pos + self.record_length > self.data.len() {
            return None;
        }
        let rec = &self.data[self.current_pos..self.current_pos + self.record_length];
        self.current_pos += self.record_length;
        self.record_number += 1;
        Some(rec)
    }
}

#[allow(dead_code)]
fn open_rolling_file(path: &Path) -> Result<Dv6RollingFile, String> {
    let mut f = std::fs::File::open(path).map_err(|e| format!("Cannot open {:?}: {}", path, e))?;
    let mut data = Vec::new();
    f.read_to_end(&mut data)
        .map_err(|e| format!("Cannot read {:?}: {}", path, e))?;
    Dv6RollingFile::open(data)
}

/// Parse a DeVilbiss DV6-format data directory into a [`CpapDirectory`].
///
/// Reads `DV6/VER.BIN` for machine identity, `DV6/SET.BIN` for therapy
/// settings, `DV6/S.BIN` for daily summaries, and `DV6/U.BIN` for
/// session timestamps.  Falls back to [`parse_dv5_directory`] if `DV6/`
/// is absent but `SL/` is present.
///
/// # Errors
///
/// Returns `Err(String)` if neither `DV6/` nor `SL/` is found, or if
/// any required file cannot be read.
pub fn parse_dv6_directory(dir_path: &Path) -> Result<CpapDirectory, String> {
    let dv6_dir = dir_path.join("DV6");
    if !dv6_dir.is_dir() {
        let sl_dir = dir_path.join("SL");
        if !sl_dir.is_dir() {
            return Err("Neither DV6 nor SL directory found".into());
        }
        return parse_dv5_directory(dir_path);
    }

    let ver_path = dv6_dir.join("VER.BIN");
    let (serial, mut model) = if ver_path.exists() {
        let data = std::fs::read(&ver_path)
            .map_err(|e| format!("Cannot read VER.BIN: {}", e))?;
        parse_verbin(&data)
    } else {
        (String::new(), String::new())
    };
    if model.is_empty() {
        model = "IntelliPAP DV6".to_string();
    }

    let settings: Option<SetBinRec> = {
        let sp = dv6_dir.join("SET.BIN");
        if sp.exists() {
            let data = std::fs::read(&sp).map_err(|e| format!("Failed to read SET.BIN: {e}"))?;
            SetBinRec::read(&mut io::Cursor::new(data)).ok()
        } else {
            None
        }
    };

    let pressure_mode = match settings.as_ref().map(|s| s.capabilities) {
        Some(0) => "CPAP",
        Some(_) => "APAP",
        None => "Unknown",
    };

    let sb_path = dv6_dir.join("S.BIN");
    let daily_summaries = if sb_path.exists() {
        let data =
            std::fs::read(&sb_path).map_err(|e| format!("Cannot read S.BIN: {}", e))?;
        let raw = parse_sbin(&data).unwrap_or_default();
        raw.iter()
            .map(|s| {
                let dt = Utc
                    .timestamp_opt(s.start_time, 0)
                    .single()
                    .map(|d| d.format("%Y-%m-%d").to_string())
                    .unwrap_or_default();
                let usage_hours = s.hours;
                CpapSessionSummary {
                    date: dt,
                    ahi: s.index_oa + s.index_ca + s.index_hyp,
                    ai: s.index_oa + s.index_ca,
                    hi: s.index_hyp,
                    cai: s.index_ca,
                    oai: s.index_oa,
                    leak_50: s.leak_50,
                    leak_95: s.leak_95,
                    leak_avg: Some(s.leak_avg),
                    pressure_50: s.pressure_50,
                    pressure_95: s.pressure_95,
                    usage_hours,
                    pressure_mode: pressure_mode.to_string(),
                    resp_rate_avg: Some(s.avg_breath_rate),
                    tidal_volume_avg: Some(s.tidal_volume),
                    minute_ventilation_avg: Some(s.tidal_volume * s.avg_breath_rate / 1000.0),
                    snore_avg: Some(s.snores),
                    flow_limitation_avg: Some(s.time_in_fl),
                }
            })
            .collect()
    } else {
        Vec::new()
    };

    let ub_path = dv6_dir.join("U.BIN");
    let session_infos = if ub_path.exists() {
        let data =
            std::fs::read(&ub_path).map_err(|e| format!("Cannot read U.BIN: {}", e))?;
        parse_ubin(&data).unwrap_or_default()
    } else {
        Vec::new()
    };

    let sessions: Vec<CpapSession> = session_infos
        .iter()
        .map(|si| {
            let start = Utc
                .timestamp_opt(si.begin, 0)
                .single()
                .unwrap_or_else(Utc::now);
            let end = Utc
                .timestamp_opt(si.end, 0)
                .single()
                .unwrap_or_else(Utc::now);
            let dur = (si.end - si.begin).max(0) as f64 / 60.0;
            CpapSession {
                start_time: start,
                end_time: end,
                duration_minutes: dur,
                file_type: "DV6".into(),
                sample_rate: 0.0,
                events: Vec::new(),
                timeseries: None,
            }
        })
        .collect();

    Ok(CpapDirectory {
        machine: MachineInfo {
            serial_number: if serial.is_empty() {
                "Unknown".into()
            } else {
                serial
            },
            product_code: model.clone(),
            model: model.clone(),
            series: "DV6".into(),
            properties: HashMap::new(),
        },
        daily_summaries,
        sessions,
    })
}

/// Parse a DeVilbiss DV5-format data directory into a [`CpapDirectory`].
///
/// Reads `SL/SET1` (tab-delimited text) for the serial number and
/// `SL/U` (binary) for session timestamps.  Daily summaries are not
/// available in the DV5 format and will be empty.
///
/// # Errors
///
/// Returns `Err(String)` if `SL/` directory is missing or any file
/// cannot be read.
pub fn parse_dv5_directory(dir_path: &Path) -> Result<CpapDirectory, String> {
    let sl_dir = dir_path.join("SL");
    if !sl_dir.is_dir() {
        return Err("SL directory not found for DV5".into());
    }
    let set1_path = sl_dir.join("SET1");
    let serial = if set1_path.exists() {
        let data =
            std::fs::read_to_string(&set1_path).map_err(|e| format!("Cannot read SET1: {}", e))?;
        data.lines()
            .find(|l| l.starts_with("Sn"))
            .and_then(|l| l.split('\t').nth(1))
            .unwrap_or("Unknown")
            .to_string()
    } else {
        "Unknown".to_string()
    };

    let ub_path = sl_dir.join("U");
    let session_infos = if ub_path.exists() {
        let data =
            std::fs::read(&ub_path).map_err(|e| format!("Cannot read U: {}", e))?;
        parse_ubin(&data).unwrap_or_default()
    } else {
        Vec::new()
    };

    let sessions: Vec<CpapSession> = session_infos
        .iter()
        .map(|si| {
            let start = Utc
                .timestamp_opt(si.begin, 0)
                .single()
                .unwrap_or_else(Utc::now);
            let end = Utc
                .timestamp_opt(si.end, 0)
                .single()
                .unwrap_or_else(Utc::now);
            let dur = (si.end - si.begin).max(0) as f64 / 60.0;
            CpapSession {
                start_time: start,
                end_time: end,
                duration_minutes: dur,
                file_type: "DV5".into(),
                sample_rate: 0.0,
                events: Vec::new(),
                timeseries: None,
            }
        })
        .collect();

    Ok(CpapDirectory {
        machine: MachineInfo {
            serial_number: serial,
            product_code: String::new(),
            model: "IntelliPAP DV5".into(),
            series: "DV5".into(),
            properties: HashMap::new(),
        },
        daily_summaries: Vec::new(),
        sessions,
    })
}

/// Return `true` if *dir_path* contains a recognisable DeVilbiss data layout.
///
/// Checks for `DV6/SET.BIN`, `SL/SET1`, or `DV6/VER.BIN` — the presence
/// of any one is sufficient to identify the directory as DeVilbiss.
pub fn can_handle(dir_path: &Path) -> bool {
    if dir_path.join("DV6").join("SET.BIN").exists() {
        return true;
    }
    if dir_path.join("SL").join("SET1").exists() {
        return true;
    }
    if dir_path.join("DV6").join("VER.BIN").exists() {
        return true;
    }
    false
}
