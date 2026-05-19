//! Apex Medical CPAP binary parser.
//!
//! Ports the OSCAR `ApexLoader.cpp` byte-unpacking logic into safe Rust.
//! Apex Medical devices (XT, XT Auto, iCH, Spirit series) write session data
//! to an `APDATA/` directory on the SD card as daily `.APC` binary files plus
//! an `INFO.APC` device-identity record.

use std::io::{Cursor, Read};
use std::path::Path;

use chrono::{TimeZone, Utc};

use crate::schema::{CpapDirectory, CpapEvent, CpapSession, CpapSessionSummary, MachineInfo};

/// Seconds from Unix epoch (1970-01-01) to the Apex device epoch (2000-01-01).
const APEX_EPOCH_OFFSET: i64 = 946_684_800;

/// Magic bytes that begin every `.APC` file header.
const APC_MAGIC: [u8; 4] = [0x41, 0x50, 0x43, 0x00];

/// Magic bytes that begin the `INFO.APC` device-identity record.
const INFO_MAGIC: [u8; 2] = [0x41, 0x50];

/// Size of a single session record inside a `.APC` file (bytes).
const SESSION_RECORD_SIZE: usize = 0x30;

/// Size of the `.APC` file header (bytes).
const APC_FILE_HEADER_SIZE: usize = 16;

// ─── Helper reads ────────────────────────────────────────────────────────────

fn read_u8(cur: &mut Cursor<&[u8]>) -> Result<u8, String> {
    let mut buf = [0u8; 1];
    cur.read_exact(&mut buf).map_err(|e| e.to_string())?;
    Ok(buf[0])
}

fn read_u16_le(cur: &mut Cursor<&[u8]>) -> Result<u16, String> {
    let mut buf = [0u8; 2];
    cur.read_exact(&mut buf).map_err(|e| e.to_string())?;
    Ok(u16::from_le_bytes(buf))
}

fn read_u32_le(cur: &mut Cursor<&[u8]>) -> Result<u32, String> {
    let mut buf = [0u8; 4];
    cur.read_exact(&mut buf).map_err(|e| e.to_string())?;
    Ok(u32::from_le_bytes(buf))
}

fn read_ascii_field(cur: &mut Cursor<&[u8]>, len: usize) -> Result<String, String> {
    let mut buf = vec![0u8; len];
    cur.read_exact(&mut buf).map_err(|e| e.to_string())?;
    let trimmed = buf
        .iter()
        .take_while(|&&b| b != 0)
        .copied()
        .collect::<Vec<u8>>();
    String::from_utf8(trimmed).map_err(|e| e.to_string())
}

// ─── Public fingerprint ───────────────────────────────────────────────────────

/// Returns `true` when *path* looks like an Apex Medical SD card root.
///
/// The check requires an `APDATA/` subdirectory containing at least one file
/// whose name ends with `.APC` (case-insensitive).
pub fn can_handle(path: &Path) -> bool {
    let apdata = path.join("APDATA");
    if !apdata.is_dir() {
        return false;
    }
    let Ok(entries) = std::fs::read_dir(&apdata) else {
        return false;
    };
    entries
        .filter_map(|e| e.ok())
        .any(|e| {
            e.path()
                .extension()
                .and_then(|x| x.to_str())
                .map(|x| x.eq_ignore_ascii_case("APC"))
                .unwrap_or(false)
        })
}

// ─── INFO.APC parsing ────────────────────────────────────────────────────────

/// Parses the 64-byte `APDATA/INFO.APC` device-identity record.
///
/// Layout (little-endian):
/// - `0x00..0x01`: magic `0x41 0x50` ("AP")
/// - `0x02..0x11`: model string (16 bytes, null-padded ASCII)
/// - `0x12..0x1D`: serial number (12 bytes, null-padded ASCII)
/// - `0x1E..0x23`: firmware version (6 bytes, null-padded ASCII)
fn parse_info(data: &[u8]) -> Result<MachineInfo, String> {
    if data.len() < 36 {
        return Err(format!(
            "INFO.APC too short: {} bytes (expected ≥36)",
            data.len()
        ));
    }
    let mut cur = Cursor::new(data);

    // magic
    let mut magic = [0u8; 2];
    cur.read_exact(&mut magic).map_err(|e| e.to_string())?;
    if magic != INFO_MAGIC {
        return Err(format!(
            "INFO.APC bad magic: {:02X} {:02X}",
            magic[0], magic[1]
        ));
    }

    let model = read_ascii_field(&mut cur, 16)?;
    let serial = read_ascii_field(&mut cur, 12)?;
    let firmware = read_ascii_field(&mut cur, 6)?;

    Ok(MachineInfo {
        serial_number: serial,
        product_code: String::from("APEX"),
        model,
        series: String::from("Apex Medical"),
        properties: [("firmware".to_owned(), firmware)].into_iter().collect(),
    })
}

// ─── Daily session file parsing ───────────────────────────────────────────────

/// Parses a single `YYYYMMDD.APC` session file, returning zero or more sessions
/// and their corresponding daily summary.
///
/// File layout:
///
/// **Header (16 bytes):**
/// - `0x00..0x03`: magic `APC\x00`
/// - `0x04..0x07`: date token `u32` = `(year-2000)*512 + month*32 + day`
/// - `0x08..0x09`: record count `u16`
/// - `0x0A..0x0F`: reserved
///
/// **Session record (48 bytes each):**
/// - `0x00..0x03`: session start, seconds since 2000-01-01 (`u32`)
/// - `0x04..0x05`: duration minutes (`u16`)
/// - `0x06`:       therapy mode — 0=CPAP, 1=APAP, 2=BiLevel (`u8`)
/// - `0x07`:       min/fixed pressure ×10 (`u8`)
/// - `0x08`:       max pressure ×10 (`u8`)
/// - `0x09`:       pressure P50 ×10 (`u8`)
/// - `0x0A`:       pressure P90 ×10 (`u8`)
/// - `0x0B`:       pressure P95 ×10 (`u8`)
/// - `0x0C..0x0D`: obstructive apnea count (`u16`)
/// - `0x0E..0x0F`: hypopnea count (`u16`)
/// - `0x10..0x11`: central apnea count (`u16`)
/// - `0x12`:       leak P50 L/min (`u8`)
/// - `0x13`:       leak P95 L/min (`u8`)
/// - `0x14`:       average leak L/min (`u8`)
/// - `0x15`:       snore index × 10 (`u8`)
/// - `0x16`:       flow limitation index × 10 (`u8`)
/// - `0x17..0x2F`: reserved / padding
fn parse_session_file(
    data: &[u8],
) -> Result<(Vec<CpapSession>, Vec<CpapSessionSummary>), String> {
    if data.len() < APC_FILE_HEADER_SIZE {
        return Err(format!(
            "APC file too short for header: {} bytes",
            data.len()
        ));
    }

    let mut cur = Cursor::new(data);

    // magic
    let mut magic = [0u8; 4];
    cur.read_exact(&mut magic).map_err(|e| e.to_string())?;
    if magic != APC_MAGIC {
        return Err(format!(
            "APC bad magic: {:02X} {:02X} {:02X} {:02X}",
            magic[0], magic[1], magic[2], magic[3]
        ));
    }

    let date_token = read_u32_le(&mut cur)?;
    let record_count = read_u16_le(&mut cur)? as usize;
    // skip 6 reserved bytes
    let mut _pad = [0u8; 6];
    cur.read_exact(&mut _pad).map_err(|e| e.to_string())?;

    let year = (date_token >> 9) as i32 + 2000;
    let month = ((date_token >> 5) & 0x0F) as u32;
    let day = (date_token & 0x1F) as u32;
    let date_str = format!("{:04}-{:02}-{:02}", year, month, day);

    let needed = APC_FILE_HEADER_SIZE + record_count * SESSION_RECORD_SIZE;
    if data.len() < needed {
        return Err(format!(
            "APC file truncated: have {} bytes, need {} for {} records",
            data.len(),
            needed,
            record_count
        ));
    }

    let mut sessions: Vec<CpapSession> = Vec::with_capacity(record_count);
    let mut summaries: Vec<CpapSessionSummary> = Vec::with_capacity(record_count);

    for _ in 0..record_count {
        let start_offset = read_u32_le(&mut cur)? as i64;
        let duration_minutes = read_u16_le(&mut cur)? as f64;
        let mode_byte = read_u8(&mut cur)?;
        let min_pressure_raw = read_u8(&mut cur)?;
        let max_pressure_raw = read_u8(&mut cur)?;
        let p50_raw = read_u8(&mut cur)?;
        let p90_raw = read_u8(&mut cur)?;
        let p95_raw = read_u8(&mut cur)?;
        let oa_count = read_u16_le(&mut cur)? as f64;
        let hi_count = read_u16_le(&mut cur)? as f64;
        let ca_count = read_u16_le(&mut cur)? as f64;
        let leak_50 = read_u8(&mut cur)? as f64;
        let leak_95 = read_u8(&mut cur)? as f64;
        let leak_avg = read_u8(&mut cur)? as f64;
        let snore_raw = read_u8(&mut cur)? as f64;
        let flow_lim_raw = read_u8(&mut cur)? as f64;
        // skip reserved bytes 0x17..0x2F (25 bytes)
        let mut _reserved = [0u8; 25];
        cur.read_exact(&mut _reserved).map_err(|e| e.to_string())?;

        let pressure_mode = match mode_byte {
            0 => "CPAP",
            1 => "APAP",
            2 => "BiLevel",
            _ => "Unknown",
        }
        .to_owned();

        let usage_hours = duration_minutes / 60.0;
        let (ahi, ai, hi, oai, cai) = if usage_hours > 0.0 {
            (
                (oa_count + hi_count + ca_count) / usage_hours,
                (oa_count + ca_count) / usage_hours,
                hi_count / usage_hours,
                oa_count / usage_hours,
                ca_count / usage_hours,
            )
        } else {
            (0.0, 0.0, 0.0, 0.0, 0.0)
        };

        let start_unix = APEX_EPOCH_OFFSET + start_offset;
        let start_dt = Utc
            .timestamp_opt(start_unix, 0)
            .single()
            .ok_or_else(|| format!("invalid start timestamp: {}", start_unix))?;
        let end_dt = Utc
            .timestamp_opt(start_unix + (duration_minutes as i64 * 60), 0)
            .single()
            .ok_or_else(|| format!("invalid end timestamp for session in {}", date_str))?;

        let pressure_50 = p50_raw as f64 / 10.0;
        let pressure_95 = p95_raw as f64 / 10.0;
        let _pressure_90 = p90_raw as f64 / 10.0;
        let _min_pressure = min_pressure_raw as f64 / 10.0;
        let _max_pressure = max_pressure_raw as f64 / 10.0;

        summaries.push(CpapSessionSummary {
            date: date_str.clone(),
            ahi,
            ai,
            hi,
            cai,
            oai,
            leak_50,
            leak_95,
            leak_avg: Some(leak_avg),
            pressure_50,
            pressure_95,
            usage_hours,
            pressure_mode: pressure_mode.clone(),
            resp_rate_avg: None,
            tidal_volume_avg: None,
            minute_ventilation_avg: None,
            snore_avg: Some(snore_raw / 10.0),
            flow_limitation_avg: Some(flow_lim_raw / 10.0),
        });

        sessions.push(CpapSession {
            start_time: start_dt,
            end_time: end_dt,
            duration_minutes,
            file_type: format!("APEX-{}", pressure_mode),
            sample_rate: 0.0,
            events: Vec::<CpapEvent>::new(),
            timeseries: None,
        });
    }

    Ok((sessions, summaries))
}

// ─── Public entry point ───────────────────────────────────────────────────────

/// Parses an Apex Medical SD card directory tree into a [`CpapDirectory`].
///
/// Reads `APDATA/INFO.APC` for machine identity, then iterates every
/// `APDATA/*.APC` session file (skipping `INFO.APC`) to accumulate sessions
/// and daily summaries.
pub fn parse_apex(path: &Path) -> Result<CpapDirectory, String> {
    let apdata = path.join("APDATA");

    // Machine identity
    let info_path = apdata.join("INFO.APC");
    let machine = if info_path.exists() {
        let data = std::fs::read(&info_path)
            .map_err(|e| format!("cannot read INFO.APC: {}", e))?;
        parse_info(&data)?
    } else {
        MachineInfo {
            serial_number: String::from("UNKNOWN"),
            product_code: String::from("APEX"),
            model: String::from("Apex Medical"),
            series: String::from("Apex Medical"),
            properties: std::collections::HashMap::new(),
        }
    };

    let mut all_sessions: Vec<CpapSession> = Vec::new();
    let mut all_summaries: Vec<CpapSessionSummary> = Vec::new();

    let entries = std::fs::read_dir(&apdata)
        .map_err(|e| format!("cannot read APDATA/: {}", e))?;

    let mut session_files: Vec<std::path::PathBuf> = entries
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| {
            p.extension()
                .and_then(|x| x.to_str())
                .map(|x| x.eq_ignore_ascii_case("APC"))
                .unwrap_or(false)
                && p.file_name()
                    .and_then(|n| n.to_str())
                    .map(|n| !n.eq_ignore_ascii_case("INFO.APC"))
                    .unwrap_or(false)
        })
        .collect();

    session_files.sort();

    for file_path in &session_files {
        let data = std::fs::read(file_path)
            .map_err(|e| format!("cannot read {}: {}", file_path.display(), e))?;
        match parse_session_file(&data) {
            Ok((sessions, summaries)) => {
                all_sessions.extend(sessions);
                all_summaries.extend(summaries);
            }
            Err(e) => {
                eprintln!(
                    "open-cpap-parser: skipping {}: {}",
                    file_path.display(),
                    e
                );
            }
        }
    }

    all_sessions.sort_by_key(|s| s.start_time);
    all_summaries.sort_by(|a, b| a.date.cmp(&b.date));

    Ok(CpapDirectory {
        machine,
        daily_summaries: all_summaries,
        sessions: all_sessions,
    })
}

// ─── Tests ────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_apc_file(date_token: u32, records: &[(&[u8; 48])] ) -> Vec<u8> {
        let mut buf = vec![0u8; APC_FILE_HEADER_SIZE];
        buf[0..4].copy_from_slice(&APC_MAGIC);
        buf[4..8].copy_from_slice(&date_token.to_le_bytes());
        buf[8..10].copy_from_slice(&(records.len() as u16).to_le_bytes());
        for rec in records {
            buf.extend_from_slice(*rec);
        }
        buf
    }

    #[test]
    fn test_date_token_decoding() {
        // 2024-01-15 → (2024-2000)*512 + 1*32 + 15 = 12288 + 32 + 15 = 12335
        let date_token: u32 = (24u32 << 9) | (1u32 << 5) | 15u32;
        assert_eq!(date_token, 12335);

        let year = (date_token >> 9) as i32 + 2000;
        let month = ((date_token >> 5) & 0x0F) as u32;
        let day = (date_token & 0x1F) as u32;
        assert_eq!(year, 2024);
        assert_eq!(month, 1);
        assert_eq!(day, 15);
    }

    #[test]
    fn test_parse_session_file_empty_records() {
        let date_token: u32 = (24u32 << 9) | (1u32 << 5) | 15u32;
        let data = make_apc_file(date_token, &[]);
        let (sessions, summaries) = parse_session_file(&data).unwrap();
        assert!(sessions.is_empty());
        assert!(summaries.is_empty());
    }

    #[test]
    fn test_parse_session_file_one_record() {
        let date_token: u32 = (24u32 << 9) | (1u32 << 5) | 15u32;

        // Build a 48-byte session record
        let mut rec = [0u8; 48];
        // start: 1 second after Apex epoch
        rec[0..4].copy_from_slice(&1u32.to_le_bytes());
        // duration: 480 minutes (8 h)
        rec[4..6].copy_from_slice(&480u16.to_le_bytes());
        // mode: APAP
        rec[6] = 1;
        // min pressure ×10 = 60 → 6.0 cmH2O
        rec[7] = 60;
        // max pressure ×10 = 120 → 12.0 cmH2O
        rec[8] = 120;
        // P50 ×10 = 75 → 7.5 cmH2O
        rec[9] = 75;
        // P90 ×10 = 90
        rec[10] = 90;
        // P95 ×10 = 100 → 10.0 cmH2O
        rec[11] = 100;
        // OA=2, HI=3, CA=1
        rec[12..14].copy_from_slice(&2u16.to_le_bytes());
        rec[14..16].copy_from_slice(&3u16.to_le_bytes());
        rec[16..18].copy_from_slice(&1u16.to_le_bytes());
        // leak P50=5, P95=10, avg=6
        rec[18] = 5;
        rec[19] = 10;
        rec[20] = 6;
        // snore×10=25 → 2.5, flow_lim×10=10 → 1.0
        rec[21] = 25;
        rec[22] = 10;

        let data = make_apc_file(date_token, &[&rec]);
        let (sessions, summaries) = parse_session_file(&data).unwrap();

        assert_eq!(sessions.len(), 1);
        assert_eq!(summaries.len(), 1);

        let s = &summaries[0];
        assert_eq!(s.date, "2024-01-15");
        assert_eq!(s.pressure_mode, "APAP");
        assert!((s.usage_hours - 8.0).abs() < 1e-9);
        assert!((s.pressure_50 - 7.5).abs() < 1e-9);
        assert!((s.pressure_95 - 10.0).abs() < 1e-9);
        // AHI = (2+3+1)/8 = 0.75
        assert!((s.ahi - 0.75).abs() < 1e-9);
        assert_eq!(s.leak_50 as u8, 5);
        assert_eq!(s.leak_95 as u8, 10);
        assert!((s.snore_avg.unwrap() - 2.5).abs() < 1e-9);
        assert!((s.flow_limitation_avg.unwrap() - 1.0).abs() < 1e-9);
    }

    #[test]
    fn test_parse_info_valid() {
        let mut data = vec![0u8; 36];
        data[0] = 0x41; // 'A'
        data[1] = 0x50; // 'P'
        // model at 0x02, 16 bytes: "iCH Auto"
        let model = b"iCH Auto";
        data[2..2 + model.len()].copy_from_slice(model);
        // serial at 0x12, 12 bytes: "SN12345678"
        let serial = b"SN12345678";
        data[18..18 + serial.len()].copy_from_slice(serial);
        // firmware at 0x1E, 6 bytes: "V1.2"
        let fw = b"V1.2";
        data[30..30 + fw.len()].copy_from_slice(fw);

        let info = parse_info(&data).unwrap();
        assert_eq!(info.model, "iCH Auto");
        assert_eq!(info.serial_number, "SN12345678");
        assert_eq!(info.properties["firmware"], "V1.2");
        assert_eq!(info.series, "Apex Medical");
    }

    #[test]
    fn test_parse_info_bad_magic() {
        let data = vec![0xFFu8; 36];
        assert!(parse_info(&data).is_err());
    }
}
