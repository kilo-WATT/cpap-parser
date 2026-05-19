/// BMC / 3B Medical CPAP data parser.
///
/// Ported from OSCAR's `bmc_loader.cpp` and `bmcDataParsing.cpp`.
///
/// File layout:
///   `*.USR` — main data file (sessions, serial, model)
///   `*.idx` — index with 512-byte packets (IDX entry + machine settings)
///   `*.000`, `*.001`, … — waveform data (256-byte packets, 25 Hz)
///
/// All multi-byte integers are little-endian.

use std::io::Read;
use std::io::SeekFrom;
use std::path::Path;
use std::io::Seek;

use chrono::{NaiveDate, NaiveDateTime, TimeZone, Utc};
use crate::schema::{CpapDirectory, CpapSession, MachineInfo};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// IE ratio lookup table (BMC raw value → inspiration percentage).
const IE_RATIO_LOOKUP: [f32; 101] = [
    0.0, 9.1, 16.7, 23.1, 28.6, 33.3, 37.5, 41.2, 44.4, 47.4,
    50.0, 52.4, 54.5, 56.5, 58.3, 60.0, 61.5, 63.0, 64.3, 65.5,
    66.7, 67.7, 68.8, 69.7, 70.6, 71.4, 72.2, 73.0, 73.7, 74.4,
    75.0, 75.6, 76.2, 76.7, 77.3, 77.8, 78.3, 78.7, 79.2, 79.6,
    80.0, 80.4, 80.8, 81.1, 81.5, 81.8, 82.1, 82.5, 82.8, 83.1,
    83.3, 83.6, 83.9, 84.1, 84.4, 84.6, 84.8, 85.1, 85.3, 85.5,
    85.7, 85.9, 86.1, 86.3, 86.5, 86.7, 86.8, 87.0, 87.2, 87.3,
    87.5, 87.7, 87.8, 88.0, 88.1, 88.2, 88.4, 88.5, 88.6, 88.8,
    88.9, 89.0, 89.1, 89.2, 89.4, 89.5, 89.6, 89.7, 89.8, 89.9,
    90.0, 90.1, 90.2, 90.3, 90.4, 90.5, 90.6, 90.7, 90.7, 90.8,
    90.9,
];

const SESSION_MARKER: u8 = 0xE1;
const SESSIONS_START_OFFSET: u64 = 0x102340;
const IDX_START_OFFSET: u64 = 0x800;
const IDX_PACKET_SIZE: usize = 512;
const WAVEFORM_PACKET_SIZE: usize = 256;
const WAVEFORM_TIMESTAMP_OFFSET: usize = 0xF8;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct BmcMachineSettings {
    pub timestamp: NaiveDate,
    pub reslex: u8,
    pub reslex_patient: bool,
    pub ramp_time_minutes: u8,
    pub humidifier_level: u8,
    pub apap_initial_p: f32,
    pub apap_min_apap: f32,
    pub apap_max_apap: f32,
    pub apap_sensitivity: u8,
    pub apap_smart_a: bool,
    pub cpap_initial_p: f32,
    pub cpap_treat_p: f32,
    pub cpap_manual_p: f32,
    pub cpap_smart_c: bool,
    pub s_initial_epap: f32,
    pub s_epap: f32,
    pub s_ipap: f32,
    pub s_isens: i32,
    pub s_esens: f32,
    pub s_rise_time: u8,
    pub s_ti_min: f32,
    pub s_ti_max: f32,
    pub s_backup_rr: bool,
    pub autos_initial_epap: f32,
    pub autos_min_epap: f32,
    pub autos_min_ipap: f32,
    pub autos_max_ipap: f32,
    pub autos_isens: i32,
    pub autos_esens: f32,
    pub autos_rise_time: u8,
    pub autos_smart_b: bool,
    pub leak_alert: bool,
    pub auto_on: bool,
    pub auto_off: bool,
    pub mode: u8,
    pub mask_type: u8,
    pub air_tube_type: u8,
    pub heated_tube_level: i32,
}

#[derive(Debug, Clone)]
pub struct BmcRespiratoryEvent {
    pub event_type: u8,
    pub start_time: NaiveDateTime,
    pub duration_seconds: i32,
}

#[derive(Debug, Clone)]
pub struct BmcUsrSession {
    pub start_timestamp: NaiveDateTime,
    pub end_timestamp: NaiveDateTime,
    pub duration_minutes: i32,
    pub respiratory_events: Vec<BmcRespiratoryEvent>,
}

#[derive(Debug, Clone)]
pub struct BmcWaveformPacket {
    pub ipap: f32,
    pub epap: f32,
    pub flow: [f32; 25],
    pub pressure_wave: [i16; 25],
    pub flow_abnormality: [i16; 25],
    pub leak: f32,
    pub tidal_volume: i32,
    pub minute_ventilation: f32,
    pub respiratory_rate: u16,
    pub ie_ratio_mapped: i16,
    pub spo2_pct: u16,
    pub pulse_rate: u16,
    pub timestamp: NaiveDateTime,
}

#[derive(Debug, Clone)]
pub struct BmcIdxEntry {
    pub timestamp: NaiveDateTime,
    pub start_offset_packet: u16,
    pub start_file_index: u16,
    pub next_offset_packet: u16,
    pub next_file_index: u8,
    pub has_valid_next: bool,
}

#[derive(Debug, Clone)]
pub struct BmcWaveformCrumb {
    pub filepath: String,
    pub file_index: u16,
    pub byte_offset: u64,
    pub packet_offset: u16,
    pub timestamp: NaiveDateTime,
}

#[derive(Debug, Clone)]
pub struct BmcDataLink {
    pub usr_session: BmcUsrSession,
    pub waveform_crumb: BmcWaveformCrumb,
}

pub struct BmcData {
    dir_path: String,
    usr_filepath: String,
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Check if a directory contains BMC data.
pub fn can_handle(path: &str) -> bool {
    BmcData::directory_has_bmc_data(path)
}

/// Parse a BMC data directory into a `CpapDirectory`.
pub fn parse_bmc(path: &str) -> Result<CpapDirectory, String> {
    let bmc = BmcData::new(path)?;
    bmc.read_data()?;
    bmc.build_cpap_directory()
}

// ---------------------------------------------------------------------------
// Implementation
// ---------------------------------------------------------------------------

impl BmcData {
    pub fn new(path: &str) -> Result<Self, String> {
        let dir_path = if path.ends_with('/') {
            path.to_string()
        } else {
            format!("{}/", path)
        };

        let usr_filepath = Self::find_usr_file(&dir_path)
            .ok_or_else(|| format!("No .USR file found in {}", dir_path))?;

        Ok(BmcData {
            dir_path,
            usr_filepath,
        })
    }

    pub fn directory_has_bmc_data(path: &str) -> bool {
        let dir_path = if path.ends_with('/') {
            path.to_string()
        } else {
            format!("{}/", path)
        };

        let usr = match Self::find_usr_file(&dir_path) {
            Some(f) => f,
            None => return false,
        };

        let idx = Self::change_extension(&usr, ".idx");
        let wav = Self::change_extension(&usr, ".000");

        Path::new(&usr).exists() && Path::new(&idx).exists() && Path::new(&wav).exists()
    }

    fn find_usr_file(dir: &str) -> Option<String> {
        let d = Path::new(dir);
        for entry in std::fs::read_dir(d).ok()? {
            let entry = entry.ok()?;
            let name = entry.file_name().to_string_lossy().to_string();
            if name.to_uppercase().ends_with(".USR") {
                return Some(format!("{}{}", dir, name));
            }
        }
        None
    }

    fn change_extension(path: &str, new_ext: &str) -> String {
        let p = Path::new(path);
        let stem = p.file_stem().unwrap_or_default().to_string_lossy();
        format!("{}{}", p.parent().unwrap_or(Path::new("")).join(&*stem).display(), new_ext)
    }

    fn read_machine_info(&self) -> Result<MachineInfo, String> {
        use std::io::{Seek, Read};
        let mut f = std::fs::File::open(&self.usr_filepath)
            .map_err(|e| format!("Cannot open USR: {e}"))?;

        let mut buf = [0u8; 32];

        // Serial at 0x2D
        f.seek(SeekFrom::Start(0x2D)).map_err(|e| format!("Seek error: {e}"))?;
        f.read_exact(&mut buf).map_err(|e| format!("Read serial: {e}"))?;
        let serial = String::from_utf8_lossy(&buf).trim_matches(|c: char| c == '\0' || c == ' ').to_string();

        // Model at 0x2296
        f.seek(SeekFrom::Start(0x2296)).map_err(|e| format!("Seek error: {e}"))?;
        f.read_exact(&mut buf).map_err(|e| format!("Read model: {e}"))?;
        let model = String::from_utf8_lossy(&buf).trim_matches(|c: char| c == '\0' || c == ' ').to_string();

        Ok(MachineInfo {
            serial_number: serial,
            product_code: String::new(),
            model,
            series: "BMC".to_string(),
            properties: std::collections::HashMap::new(),
        })
    }

    fn read_data(&self) -> Result<(Vec<(BmcIdxEntry, BmcMachineSettings)>, Vec<BmcUsrSession>), String> {
        let idx_entries = self.read_idx_file()?;
        let all_sessions = self.read_all_sessions()?;
        Ok((idx_entries, all_sessions))
    }

    fn read_idx_file(&self) -> Result<Vec<(BmcIdxEntry, BmcMachineSettings)>, String> {
        let idx_path = Self::change_extension(&self.usr_filepath, ".idx");
        let mut f = std::fs::File::open(&idx_path)
            .map_err(|e| format!("Cannot open IDX: {e}"))?;

        let file_size = f.metadata().map_err(|e| format!("Metadata: {e}"))?.len();
        f.seek(SeekFrom::Start(IDX_START_OFFSET)).map_err(|e| format!("Seek IDX: {e}"))?;

        let mut results = Vec::new();

        loop {
            let pos = f.seek(SeekFrom::Current(0)).map_err(|e| format!("Stream pos: {e}"))?;
            if pos + IDX_PACKET_SIZE as u64 > file_size {
                break;
            }

            let mut packet = vec![0u8; IDX_PACKET_SIZE];
            if f.read_exact(&mut packet).is_err() {
                break;
            }

            let entry = match read_idx_entry(&packet) {
                Ok(e) => e,
                Err(_) => continue,
            };

            let settings = match read_machine_settings(&packet) {
                Ok(s) => s,
                Err(_) => continue,
            };

            results.push((entry, settings));
        }

        Ok(results)
    }

    fn read_all_sessions(&self) -> Result<Vec<BmcUsrSession>, String> {
        let mut f = std::fs::File::open(&self.usr_filepath)
            .map_err(|e| format!("Cannot open USR: {e}"))?;

        // In-progress session at 0x431
        let in_progress = self.read_in_progress_session(&f)?;

        // Historic sessions at 0x102340
        let file_size = f.metadata().map_err(|e| format!("Metadata: {e}"))?.len();
        let mut f2 = std::fs::File::open(&self.usr_filepath)
            .map_err(|e| format!("Cannot open USR: {e}"))?;

        let mut sessions = Vec::new();

        let mut offset = SESSIONS_START_OFFSET;
        while offset < file_size {
            f2.seek(std::io::SeekFrom::Start(offset)).map_err(|e| format!("Seek: {e}"))?;

            let mut header = [0u8; 1];
            if f2.read_exact(&mut header).is_err() {
                break;
            }

            if header[0] != SESSION_MARKER {
                break;
            }

            // Read next offset (u32 at offset 1)
            let mut next_buf = [0u8; 4];
            f2.seek_relative(-1).ok();
            f2.read_exact(&mut next_buf).ok();
            // Re-read with the marker
            f2.seek_relative(-1).ok();
            let mut buf = [0u8; 5];
            if f2.read_exact(&mut buf).is_err() {
                break;
            }

            let next = u32::from_le_bytes([buf[1], buf[2], buf[3], buf[4]]);
            let slice_end = if next == 0 || next == 0xFFFFFFFF || next <= offset as u32 || next > file_size as u32 {
                file_size as u32
            } else {
                next
            };

            let len = slice_end - offset as u32;
            let mut raw = vec![0u8; len as usize];
            f2.seek(std::io::SeekFrom::Start(offset)).map_err(|e| format!("Seek2: {e}"))?;
            f2.read_exact(&mut raw).map_err(|e| format!("Read session: {e}"))?;

            match parse_historic_session(&raw) {
                Ok(s) => sessions.push(s),
                Err(_) => { /* skip corrupt session */ }
            }

            offset = slice_end as u64;
        }

        sessions.push(in_progress);

        Ok(sessions)
    }

    fn read_in_progress_session(&self, _f: &std::fs::File) -> Result<BmcUsrSession, String> {
        let mut f = std::fs::File::open(&self.usr_filepath)
            .map_err(|e| format!("Cannot open USR: {e}"))?;

        // Date at 0x431
        f.seek(std::io::SeekFrom::Start(0x431)).map_err(|e| format!("Seek: {e}"))?;
        let mut date_buf = [0u8; 2];
        f.read_exact(&mut date_buf).map_err(|e| format!("Read date: {e}"))?;
        let encoded = u16::from_le_bytes(date_buf);
        let start_ts = decode_encoded_date(encoded);
        let end_ts = start_ts + chrono::Duration::days(1);

        // Events at 0x441
        f.seek(std::io::SeekFrom::Start(0x441)).map_err(|e| format!("Seek: {e}"))?;
        let mut events = Vec::new();

        loop {
            let mut msg_type_buf = [0u8; 1];
            if f.read_exact(&mut msg_type_buf).is_err() {
                break;
            }
            let msg_type = msg_type_buf[0];
            if msg_type == 0xFF {
                break;
            }

            let datalen = if msg_type == 0x02 {
                3u8
            } else {
                let mut dbuf = [0u8; 1];
                f.read_exact(&mut dbuf).map_err(|e| format!("Read datalen: {e}"))?;
                dbuf[0]
            };

            let mut msg_data = vec![0u8; datalen as usize];
            f.read_exact(&mut msg_data).map_err(|e| format!("Read msg: {e}"))?;

            if msg_type == 0x07 || msg_type == 0x08 || msg_type == 0x09 {
                let event_type = match msg_type {
                    0x07 => 2, // CSA
                    0x08 => 1, // OSA
                    0x09 => 0, // HYP
                    _ => 3,    // Unknown
                };

                let minutes = msg_data[0] as i64 * 60 + msg_data[1] as i64;
                let evt_start = start_ts + chrono::Duration::minutes(minutes);
                let dur = msg_data[2] as i32;

                events.push(BmcRespiratoryEvent {
                    event_type,
                    start_time: evt_start,
                    duration_seconds: dur,
                });
            }
        }

        Ok(BmcUsrSession {
            start_timestamp: start_ts,
            end_timestamp: end_ts,
            duration_minutes: 0,
            respiratory_events: events,
        })
    }

    fn build_cpap_directory(&self) -> Result<CpapDirectory, String> {
        let (_idx_data, sessions) = self.read_data()?;
        let machine = self.read_machine_info()?;

        let mut session_list = Vec::new();
        for s in &sessions {
            session_list.push(CpapSession {
                start_time: Utc.from_utc_datetime(&s.start_timestamp),
                end_time: Utc.from_utc_datetime(&s.end_timestamp),
                duration_minutes: s.duration_minutes as f64,
                file_type: String::new(),
                sample_rate: 0.0,
                events: Vec::new(),
                timeseries: None,
            });
        }

        Ok(CpapDirectory {
            machine,
            daily_summaries: Vec::new(),
            sessions: session_list,
        })
    }
}

// ---------------------------------------------------------------------------
// Binary parsing helpers
// ---------------------------------------------------------------------------

fn read_u8(data: &[u8], pos: &mut usize) -> Result<u8, String> {
    if *pos + 1 > data.len() {
        return Err("EOF reading u8".to_string());
    }
    let v = data[*pos];
    *pos += 1;
    Ok(v)
}

fn read_u16_le(data: &[u8], pos: &mut usize) -> Result<u16, String> {
    if *pos + 2 > data.len() {
        return Err("EOF reading u16".to_string());
    }
    let v = u16::from_le_bytes([data[*pos], data[*pos + 1]]);
    *pos += 2;
    Ok(v)
}

fn read_u32_le(data: &[u8], pos: &mut usize) -> Result<u32, String> {
    if *pos + 4 > data.len() {
        return Err("EOF reading u32".to_string());
    }
    let v = u32::from_le_bytes([data[*pos], data[*pos + 1], data[*pos + 2], data[*pos + 3]]);
    *pos += 4;
    Ok(v)
}

fn read_i16_le(data: &[u8], pos: &mut usize) -> Result<i16, String> {
    if *pos + 2 > data.len() {
        return Err("EOF reading i16".to_string());
    }
    let v = i16::from_le_bytes([data[*pos], data[*pos + 1]]);
    *pos += 2;
    Ok(v)
}

fn decode_encoded_date(encoded: u16) -> NaiveDateTime {
    let year = 2000 + (encoded >> 9) as i32;
    let month = ((encoded >> 5) & 0x0F) as u32;
    let day = (encoded & 0x1F) as u32;
    NaiveDate::from_ymd_opt(year, month, day)
        .unwrap_or_else(|| NaiveDate::from_ymd_opt(2010, 1, 1).unwrap())
        .and_hms_opt(12, 0, 0)
        .unwrap()
}

fn parse_u16_timestamp(data: &[u8], pos: &mut usize) -> Result<NaiveDateTime, String> {
    let year = read_u16_le(data, pos)? as i32;
    let month = read_u8(data, pos)?;
    let day = read_u8(data, pos)?;
    let hour = read_u8(data, pos)?;
    let minute = read_u8(data, pos)?;
    let second = read_u8(data, pos)?;
    NaiveDate::from_ymd_opt(year, month.into(), day.into())
        .and_then(|d| d.and_hms_opt(hour.into(), minute.into(), second.into()))
        .ok_or_else(|| format!("Invalid timestamp: {}-{:02}-{:02} {:02}:{:02}:{:02}", year, month, day, hour, minute, second))
}

// ---------------------------------------------------------------------------
// IDX entry parsing (512-byte packet)
// ---------------------------------------------------------------------------

fn read_idx_entry(data: &[u8]) -> Result<BmcIdxEntry, String> {
    let mut pos = 0usize;
    let header = read_u16_le(data, &mut pos)?;
    if header != 0xAAAA {
        return Err("Bad IDX header".to_string());
    }

    let _idx = read_u16_le(data, &mut pos)?; // idx at 0x002
    let year = read_u8(data, &mut pos)?;
    let month = read_u8(data, &mut pos)?;
    let day = read_u8(data, &mut pos)?;

    let timestamp = NaiveDate::from_ymd_opt(2000 + year as i32, month as u32, day as u32)
        .ok_or_else(|| "Bad IDX date".to_string())?
        .and_hms_opt(0, 0, 0)
        .unwrap();

    pos += 6; // skip 0x007-0x00C

    let start_offset_packet = read_u16_le(data, &mut pos)?; // 0x00D
    let start_file_index = read_u16_le(data, &mut pos)?;    // 0x00F
    let next_offset_packet = read_u16_le(data, &mut pos)?;   // 0x011
    let next_file_index = read_u8(data, &mut pos)?;          // 0x013

    if start_file_index > 999 {
        return Err("Invalid nnn waveform file index".to_string());
    }

    Ok(BmcIdxEntry {
        timestamp,
        start_offset_packet,
        start_file_index,
        next_offset_packet,
        next_file_index,
        has_valid_next: next_file_index != 0xFF,
    })
}

// ---------------------------------------------------------------------------
// Machine settings parsing (IDX packet, offset 0x140)
// ---------------------------------------------------------------------------

fn read_machine_settings(data: &[u8]) -> Result<BmcMachineSettings, String> {
    if data.len() < 0x200 {
        return Err("IDX packet too short for settings".to_string());
    }

    let mut pos: usize = 0x140;

    let apap_initial_p = read_u8(data, &mut pos)? as f32 / 2.0;
    let min_apap = read_u8(data, &mut pos)? as f32 / 2.0;
    let ramp_time_minutes = read_u8(data, &mut pos)?;
    pos += 1; // skip 143

    let cpap_manual_p = read_u8(data, &mut pos)? as f32 / 2.0;
    let backup_rr_b = read_u8(data, &mut pos)?;
    let s_backup_rr = (backup_rr_b & 0x80) != 0;
    let humidifier_level = read_u8(data, &mut pos)?;

    let b147 = read_u8(data, &mut pos)?;
    let leak_alert = (b147 & 0x40) != 0;
    let auto_off = (b147 & 0x02) != 0;
    let auto_on = (b147 & 0x01) != 0;

    let b148 = read_u8(data, &mut pos)?;
    let reslex = b148 & 0x03;
    let ipap_offset = (b148 >> 2) as f32 / 2.0;
    let s_epap = min_apap;
    let s_ipap = s_epap + ipap_offset;

    let b149 = read_u8(data, &mut pos)?;
    let s_isens = 1 + (b149 & 0x07) as i32;
    let s_esens = 1.0_f32 + ((b149 >> 3) & 0x07) as f32;

    let _ = read_u8(data, &mut pos)?; // 14a
    let _ = read_u8(data, &mut pos)?; // 14b

    let max_apap = read_u8(data, &mut pos)? as f32 / 2.0;

    let b14d = read_u8(data, &mut pos)?;
    let mode = b14d >> 4;
    let apap_sensitivity = b14d & 0x0F;

    let _ = read_u8(data, &mut pos)?; // 14e
    let b14f = read_u8(data, &mut pos)?;
    let s_rise_time = 1 + (b14f >> 6);

    let _ = read_u8(data, &mut pos)?; // 150

    let b151 = read_u8(data, &mut pos)?;
    let reslex_patient = (b151 & 0x80) != 0;

    let s_ti_min = read_u8(data, &mut pos)? as f32 / 10.0;
    let s_ti_max = read_u8(data, &mut pos)? as f32 / 10.0;

    pos += 0x0C; // skip 154-160

    let mask_type = read_u8(data, &mut pos)?;
    let _ = read_u8(data, &mut pos)?; // 161
    let air_tube_type = read_u8(data, &mut pos)?;
    let _ = read_u8(data, &mut pos)?; // 163
    let heated_tube_level = read_u8(data, &mut pos)? as i32;

    let b165 = read_u8(data, &mut pos)?;
    let apap_smart_a = (b165 & 0x02) != 0;
    let cpap_smart_c = (b165 & 0x01) != 0;
    let autos_smart_b = (b165 & 0x04) != 0;

    Ok(BmcMachineSettings {
        timestamp: NaiveDate::from_ymd_opt(2000, 1, 1).unwrap(),
        reslex,
        reslex_patient,
        ramp_time_minutes,
        humidifier_level: humidifier_level,
        apap_initial_p,
        apap_min_apap: min_apap,
        apap_max_apap: max_apap,
        apap_sensitivity,
        apap_smart_a,
        cpap_initial_p: apap_initial_p,
        cpap_treat_p: min_apap,
        cpap_manual_p,
        cpap_smart_c,
        s_initial_epap: apap_initial_p,
        s_epap,
        s_ipap,
        s_isens,
        s_esens,
        s_rise_time,
        s_ti_min,
        s_ti_max,
        s_backup_rr,
        autos_initial_epap: apap_initial_p,
        autos_min_epap: min_apap,
        autos_min_ipap: s_ipap,
        autos_max_ipap: max_apap,
        autos_isens: s_isens,
        autos_esens: s_esens,
        autos_rise_time: s_rise_time,
        autos_smart_b,
        leak_alert,
        auto_on,
        auto_off,
        mode,
        mask_type,
        air_tube_type,
        heated_tube_level,
    })
}

// ---------------------------------------------------------------------------
// Historic session parsing
// ---------------------------------------------------------------------------

fn parse_historic_session(data: &[u8]) -> Result<BmcUsrSession, String> {
    let mut pos = 0usize;
    let marker = read_u8(data, &mut pos)?;
    if marker != SESSION_MARKER {
        return Err("Bad session marker".to_string());
    }

    // Seek to 0x07 for start date
    pos = 0x07;
    let start_encoded = read_u16_le(data, &mut pos)?;
    let start_ts = decode_encoded_date(start_encoded);
    let end_ts = start_ts + chrono::Duration::days(1);

    // Duration at 0x0F
    pos = 0x0F;
    let duration_minutes = read_u16_le(data, &mut pos)? as i32;

    // Messages at 0x45
    pos = 0x45;
    let mut _messages_45: Vec<(u8, u32)> = Vec::new();
    loop {
        if pos + 5 > data.len() {
            break;
        }
        let b = read_u8(data, &mut pos)?;
        let _val = read_u32_le(data, &mut pos)?;
        if b == 0xFF {
            break;
        }
    }

    // Data messages: 24-bit events (respiratory events)
    let mut events_24 = Vec::new();

    while pos < data.len() {
        if pos + 4 > data.len() {
            break;
        }
        let msg_type = read_u8(data, &mut pos)?;
        let count = read_u16_le(data, &mut pos)?;
        let _discard = read_u16_le(data, &mut pos)?; // skip 2 bytes

        match msg_type {
            0x82 | 0x86 => {
                // 32-bit messages
                for _ in 0..count {
                    if pos + 4 > data.len() {
                        break;
                    }
                    let _val = read_u32_le(data, &mut pos)?;
                }
            }
            0x83 | 0x84 | 0x87 => {
                // 24-bit messages (respiratory events)
                for _ in 0..count {
                    if pos + 3 > data.len() {
                        break;
                    }
                    let b1 = read_u8(data, &mut pos)?;
                    let b2 = read_u8(data, &mut pos)?;
                    let b3 = read_u8(data, &mut pos)?;
                    events_24.push((msg_type, b1, b2, b3));
                }
            }
            _ => {
                // 16-bit messages
                for _ in 0..count {
                    if pos + 2 > data.len() {
                        break;
                    }
                    let _val = read_u16_le(data, &mut pos)?;
                }
            }
        }
    }

    // Convert 24-bit events to respiratory events
    let mut respiratory_events = Vec::new();
    for (msg_type, data1, data2, data3) in events_24 {
        let event_type = match msg_type {
            0x83 => 1,  // OSA
            0x84 => 0,  // HYP
            0x87 => 2,  // CSA
            _ => 3,     // Unknown
        };

        let minutes = data1 as i64 * 60 + data2 as i64;
        let evt_start = start_ts + chrono::Duration::minutes(minutes);
        let dur = data3 as i32;

        respiratory_events.push(BmcRespiratoryEvent {
            event_type,
            start_time: evt_start,
            duration_seconds: dur,
        });
    }

    Ok(BmcUsrSession {
        start_timestamp: start_ts,
        end_timestamp: end_ts,
        duration_minutes,
        respiratory_events,
    })
}

// ---------------------------------------------------------------------------
// Waveform packet parsing (256-byte packets in .nnn files)
// ---------------------------------------------------------------------------

fn parse_waveform_packet(data: &[u8]) -> Result<BmcWaveformPacket, String> {
    let mut pos = 0usize;

    let _header = read_u16_le(data, &mut pos)?;
    let _ = read_i16_le(data, &mut pos)?; // offset 0x02
    let ipap_raw = read_i16_le(data, &mut pos)?;
    let epap_raw = read_i16_le(data, &mut pos)?;

    let mut pressure_wave = [0i16; 25];
    for v in &mut pressure_wave {
        *v = read_i16_le(data, &mut pos)?;
    }

    let mut flow_abnormality = [0i16; 25];
    for v in &mut flow_abnormality {
        *v = read_i16_le(data, &mut pos)?;
    }

    let mut flow_raw = [0i16; 25];
    for v in &mut flow_raw {
        *v = read_i16_le(data, &mut pos)?;
    }

    // Skip many fields from 0x9E to 0xC2
    pos = 0xC4;

    let leak_raw = read_i16_le(data, &mut pos)?;
    let tidal_vol_raw = read_i16_le(data, &mut pos)?;
    let _ = read_i16_le(data, &mut pos)?; // 0xC8
    let mv_raw = read_i16_le(data, &mut pos)?;
    let spo2_raw = read_u16_le(data, &mut pos)?;
    let pulse_raw = read_u16_le(data, &mut pos)?;
    let rr_raw = read_u16_le(data, &mut pos)?;
    let ie_raw = read_i16_le(data, &mut pos)?;

    // Skip to 0xF8
    pos = 0xF8;

    let timestamp = parse_u16_timestamp(data, &mut pos)?;

    let ipap = ipap_raw as f32 / 2.0;
    let epap = epap_raw as f32 / 2.0;

    let mut flow = [0.0f32; 25];
    for i in 0..25 {
        flow[i] = flow_raw[i] as f32 / 10.0;
    }

    let leak = leak_raw as f32 / 10.0;
    let tidal_volume = tidal_vol_raw as i32;
    let minute_ventilation = mv_raw as f32 / 10.0;

    let ie_ratio_mapped = if ie_raw >= 0 && (ie_raw as usize) < IE_RATIO_LOOKUP.len() {
        let pct = IE_RATIO_LOOKUP[ie_raw as usize];
        if ie_raw <= 100 {
            (100.0 - pct) as i16
        } else {
            0
        }
    } else {
        0
    };

    Ok(BmcWaveformPacket {
        ipap,
        epap,
        flow,
        pressure_wave,
        flow_abnormality,
        leak,
        tidal_volume,
        minute_ventilation,
        respiratory_rate: rr_raw,
        ie_ratio_mapped,
        spo2_pct: spo2_raw,
        pulse_rate: pulse_raw,
        timestamp,
    })
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_decode_encoded_date() {
        // 2023-06-15: year=23 (0x17), month=6, day=15
        // encoded = (23 << 9) | (6 << 5) | 15 = 11776 + 192 + 15 = 11983
        let dt = decode_encoded_date(11983);
        assert_eq!(dt.year(), 2023);
        assert_eq!(dt.month(), 6);
        assert_eq!(dt.day(), 15);
    }

    #[test]
    fn test_ie_ratio_lookup() {
        assert!((IE_RATIO_LOOKUP[0] - 0.0).abs() < 1e-9);
        assert!((IE_RATIO_LOOKUP[50] - 50.0).abs() < 1e-9);
        assert!((IE_RATIO_LOOKUP[100] - 90.9).abs() < 1e-9);
    }

    #[test]
    fn test_idx_entry_parsing() {
        let mut packet = vec![0u8; 512];
        // Header 0xAAAA
        packet[0] = 0xAA;
        packet[1] = 0xAA;
        // idx (unused)
        packet[2] = 0x01;
        packet[3] = 0x00;
        // date: 2023-06-15 → year=23, month=6, day=15
        packet[4] = 23;
        packet[5] = 6;
        packet[6] = 15;
        // skip 0x007-0x00C (6 bytes of zeros)
        // start_offset_packet at 0x00D
        packet[0x0D] = 0x10;
        packet[0x0E] = 0x00;
        // start_file_index at 0x00F
        packet[0x0F] = 0x00;
        packet[0x10] = 0x00;
        // next_offset_packet at 0x011
        packet[0x11] = 0x20;
        packet[0x12] = 0x00;
        // next_file_index at 0x013
        packet[0x13] = 0x01;

        let entry = read_idx_entry(&packet).unwrap();
        assert_eq!(entry.timestamp.year(), 2023);
        assert_eq!(entry.timestamp.month(), 6);
        assert_eq!(entry.timestamp.day(), 15);
        assert_eq!(entry.start_offset_packet, 0x10);
        assert!(entry.has_valid_next);
    }

    #[test]
    fn test_machine_settings_parsing() {
        let mut packet = vec![0u8; 0x200];
        // Minimum viable settings at 0x140
        packet[0x140] = 20;   // initial_p = 10.0
        packet[0x141] = 24;   // min_apap = 12.0
        packet[0x142] = 30;   // ramp = 30 min
        packet[0x144] = 20;   // manual_p = 10.0
        packet[0x145] = 0;    // backup_rr = disabled
        packet[0x146] = 0;    // humidifier = 0
        packet[0x147] = 0;    // leak/auto on/off = off
        packet[0x148] = 0;    // reslex=0, ipap_offset=0
        packet[0x149] = 0;    // isens/esens
        packet[0x14A] = 0;
        packet[0x14B] = 0;
        packet[0x14C] = 28;   // max_apap = 14.0
        packet[0x14D] = 0;    // mode=CPAP, sensitivity=0
        packet[0x14E] = 0;
        packet[0x14F] = 0;    // rise_time=1
        packet[0x150] = 0;
        packet[0x151] = 0;    // reslex_patient=false
        packet[0x152] = 15;   // ti_min=1.5
        packet[0x153] = 30;   // ti_max=3.0
        // skip to 0x160
        packet[0x160] = 0;    // mask=FullFace
        packet[0x162] = 0;    // air_tube=Unheated22mm
        packet[0x164] = 0;    // heated_tube=0
        packet[0x165] = 0;    // smart features = off

        let s = read_machine_settings(&packet).unwrap();
        assert!((s.apap_initial_p - 10.0).abs() < 1e-6);
        assert!((s.apap_min_apap - 12.0).abs() < 1e-6);
        assert!((s.apap_max_apap - 14.0).abs() < 1e-6);
        assert_eq!(s.ramp_time_minutes, 30);
        assert!(!s.apap_smart_a);
        assert!(!s.cpap_smart_c);
        assert!(!s.autos_smart_b);
        assert_eq!(s.mode, 0); // CPAP
    }

    #[test]
    fn test_directory_has_bmc_data() {
        // Just verify the function handles nonexistent dirs gracefully
        assert!(!BmcData::directory_has_bmc_data("/nonexistent/path"));
    }
}
