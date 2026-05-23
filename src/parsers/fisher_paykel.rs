//! Fisher & Paykel SleepStyle CPAP binary parser.
//!
//! Ported from OSCAR's `sleepstyle_loader.cpp`, which is derived from
//! SleepyHead by Mark Watkins (C) 2011-2018, and is copyright (c) 2020-2025
//! The Oscar Team.
//!
//! Fingerprint: root contains `FPHCARE/ICON/` with at least one serial-number
//! subdirectory holding a `SUM*.fph` summary file whose fifth CR-terminated
//! text line is `SLEEPSTYLE`.
//!
//! Supported files:
//! - `SUM*.fph` — 512-byte text header followed by 40-byte binary session records

use std::collections::HashMap;
use std::io::{Cursor, Read};
use std::path::{Path, PathBuf};

use chrono::{TimeZone, Utc};

use crate::schema::{CpapDirectory, CpapSession, CpapSessionSummary, MachineInfo};

// ─── Helper reads ─────────────────────────────────────────────────────────────

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

fn skip_bytes(cur: &mut Cursor<&[u8]>, n: usize) -> Result<(), String> {
    let mut buf = vec![0u8; n];
    cur.read_exact(&mut buf).map_err(|e| e.to_string())
}

// ─── F&P packed timestamp ─────────────────────────────────────────────────────

/// Decode the F&P 32-bit packed date/time field to a Unix timestamp.
///
/// Bit layout (LE u32):
/// - bits \[4:0\]   = day
/// - bits \[8:5\]   = month
/// - bits \[14:9\]  = year − 2000
/// - bits \[20:15\] = second
/// - bits \[26:21\] = minute
/// - bits \[31:27\] = hour
///
/// Matches OSCAR's `ssconvertDate`, including the −54-second adjustment.
fn decode_fp_timestamp(raw: u32) -> Option<i64> {
    let day    = raw & 0x1f;
    let month  = (raw >> 5) & 0x0f;
    let year   = 2000i32 + ((raw >> 9) & 0x3f) as i32;
    let ts2    = raw >> 15;
    let second = ts2 & 0x3f;
    let minute = (ts2 >> 6) & 0x3f;
    let hour   = ts2 >> 12;

    if month == 0 || day == 0 {
        return None;
    }

    Utc.with_ymd_and_hms(year, month, day, hour, minute, second)
        .single()
        .map(|dt| dt.timestamp() - 54)
}

// ─── Directory helpers ────────────────────────────────────────────────────────

fn find_icon_dir(path: &Path) -> Option<PathBuf> {
    let candidate = path.join("FPHCARE").join("ICON");
    if candidate.is_dir() {
        return Some(candidate);
    }
    None
}

fn has_sum_fph(machine_dir: &Path) -> bool {
    std::fs::read_dir(machine_dir)
        .ok()
        .map(|entries| {
            entries.flatten().any(|e| {
                let name = e.file_name().to_string_lossy().to_uppercase();
                name.starts_with("SUM") && name.ends_with(".FPH")
            })
        })
        .unwrap_or(false)
}

/// Locate `(serial_number, machine_path)` pairs for SleepStyle devices.
///
/// A subdirectory under `ICON/` qualifies when it contains a `SUM*.fph` file
/// whose fifth CR-terminated text line equals `SLEEPSTYLE`.
fn find_sleepstyle_machines(icon_path: &Path) -> Vec<(String, PathBuf)> {
    let mut result = Vec::new();
    let Ok(entries) = std::fs::read_dir(icon_path) else {
        return result;
    };

    for entry in entries.flatten() {
        let machine_path = entry.path();
        if !machine_path.is_dir() {
            continue;
        }
        let Ok(files) = std::fs::read_dir(&machine_path) else {
            continue;
        };
        for file in files.flatten() {
            let fname = file.file_name().to_string_lossy().to_uppercase();
            if !fname.starts_with("SUM") || !fname.ends_with(".FPH") {
                continue;
            }
            let Ok(data) = std::fs::read(file.path()) else {
                continue;
            };

            // Parse up to 7 CR-terminated text lines before the ';' terminator.
            let text_region = &data[..data.len().min(0x200)];
            let mut lines: Vec<String> = Vec::new();
            let mut cur_line: Vec<u8> = Vec::new();
            for &b in text_region {
                match b {
                    b'\r' | b'\n' => {
                        if !cur_line.is_empty() {
                            lines.push(
                                String::from_utf8_lossy(&cur_line).trim().to_string(),
                            );
                            cur_line.clear();
                        }
                    }
                    b';' => break,
                    _ => cur_line.push(b),
                }
            }

            // Line 5 (0-based index 4) must be "SLEEPSTYLE".
            if lines.get(4).map(|l| l.to_uppercase() == "SLEEPSTYLE").unwrap_or(false) {
                let serial = entry.file_name().to_string_lossy().to_string();
                result.push((serial, machine_path.clone()));
                break;
            }
        }
    }
    result
}

// ─── SUM file parsing ─────────────────────────────────────────────────────────

struct SumFileInfo {
    model: String,
    product_code: String,
}

/// Extract model name and product code from the 512-byte SUM text header.
///
/// Fields (whitespace-delimited): h1, version, fname, serial, model, type, ...
/// `type[3]` == `'C'` → CPAP mode; otherwise → Auto.
fn parse_sum_header(header: &[u8]) -> SumFileInfo {
    let text = String::from_utf8_lossy(header);
    let tokens: Vec<&str> = text.split_whitespace().collect();
    let model_name = tokens.get(4).unwrap_or(&"SleepStyle").to_string();
    let type_code  = tokens.get(5).copied().unwrap_or("");
    let mode_suffix = type_code.chars().nth(3)
        .map(|c| if c == 'C' { " CPAP" } else { " Auto" })
        .unwrap_or("");
    SumFileInfo {
        model: format!("{}{}", model_name, mode_suffix),
        product_code: type_code.to_string(),
    }
}

struct SumSession {
    start_ts: i64,
    usage_seconds: i64,
    min_pressure: f64,
    pressure_95: f64,
    pressure_mode: String,
}

/// Parse binary session records from the data that follows the SUM text header.
///
/// Each 40-byte record: 4-byte LE packed timestamp + 36 bytes of session data.
/// Records terminate at `0xFFFFFFFF` or when the low 16 bits equal `0xFAFE`.
fn parse_sum_sessions(data: &[u8]) -> Vec<SumSession> {
    let mut sessions = Vec::new();
    let mut cur = Cursor::new(data);

    while let Ok(ts_raw) = read_u32_le(&mut cur) {
        if ts_raw == 0xFFFF_FFFF || (ts_raw & 0xFFFF) == 0xFAFE {
            break;
        }

        let start_ts = match decode_fp_timestamp(ts_raw) {
            Some(t) => t,
            None => {
                let _ = skip_bytes(&mut cur, 36);
                continue;
            }
        };

        macro_rules! r8  { () => { match read_u8(&mut cur)     { Ok(v) => v, Err(_) => break } } }
        macro_rules! r16 { () => { match read_u16_le(&mut cur) { Ok(v) => v, Err(_) => break } } }

        let _run_time        = r8!();
        let use_time         = r8!();
        let min_press_seen   = r8!();
        let pct95_press_seen = r8!();
        let max_press_seen   = r8!();
        // d1-d6
        let _ = r8!(); let _ = r8!(); let _ = r8!();
        let _ = r8!(); let _ = r8!(); let _ = r8!();
        // c1-c4 (u16 LE)
        let _ = r16!(); let _ = r16!(); let _ = r16!(); let _ = r16!();
        let _j1              = r8!();
        let _mode            = r8!();
        let _ramp            = r8!();
        let _x1              = r8!();
        let _x2              = r8!();
        let cpap_press_set   = r8!();
        let _min_press_set   = r8!();
        let _max_press_set   = r8!();
        let _sens_awake      = r8!();
        let _humidity        = r8!();
        let _epr_level       = r8!();
        let _flags           = r8!();
        // 5 unknown trailing bytes
        let _ = r8!(); let _ = r8!(); let _ = r8!();
        let _ = r8!(); let _ = r8!();

        // CPAP when 95th-perc and max both equal the fixed pressure setting.
        let is_cpap = max_press_seen == cpap_press_set && pct95_press_seen == cpap_press_set;

        sessions.push(SumSession {
            start_ts,
            usage_seconds: use_time as i64 * 360,
            min_pressure:  min_press_seen as f64 / 10.0,
            pressure_95:   pct95_press_seen as f64 / 10.0,
            pressure_mode: if is_cpap { "CPAP" } else { "APAP" }.to_string(),
        });
    }

    sessions
}

// ─── Public API ───────────────────────────────────────────────────────────────

/// Return `true` if *dir_path* contains a Fisher & Paykel SleepStyle layout.
///
/// Checks for `FPHCARE/ICON/` with at least one subdirectory holding `SUM*.fph`
/// files.
pub fn can_handle(dir_path: &Path) -> bool {
    let Some(icon) = find_icon_dir(dir_path) else {
        return false;
    };
    std::fs::read_dir(&icon)
        .ok()
        .map(|entries| {
            entries.flatten().any(|e| e.path().is_dir() && has_sum_fph(&e.path()))
        })
        .unwrap_or(false)
}

/// Parse a Fisher & Paykel SleepStyle data directory into a [`CpapDirectory`].
///
/// Reads `FPHCARE/ICON/<serial>/SUM*.fph` files for machine identity and
/// per-session summary data.
///
/// # Errors
///
/// Returns `Err(String)` if no SleepStyle device is found or no files can be
/// read.
pub fn parse_fisher_paykel(dir_path: &Path) -> Result<CpapDirectory, String> {
    let icon = find_icon_dir(dir_path)
        .ok_or_else(|| "FPHCARE/ICON directory not found".to_string())?;

    let machines = find_sleepstyle_machines(&icon);
    if machines.is_empty() {
        return Err("No SleepStyle device directories found under FPHCARE/ICON".to_string());
    }

    let (serial, machine_path) = &machines[0];

    let mut sum_files: Vec<PathBuf> = std::fs::read_dir(machine_path)
        .map_err(|e| format!("Cannot read machine directory: {}", e))?
        .flatten()
        .filter(|e| {
            let name = e.file_name().to_string_lossy().to_uppercase();
            name.starts_with("SUM") && name.ends_with(".FPH")
        })
        .map(|e| e.path())
        .collect();
    sum_files.sort();

    let mut all_sessions: Vec<SumSession> = Vec::new();
    let mut model_info = SumFileInfo {
        model: "SleepStyle".to_string(),
        product_code: String::new(),
    };

    for sum_path in &sum_files {
        let data = std::fs::read(sum_path)
            .map_err(|e| format!("Cannot read {:?}: {}", sum_path, e))?;
        if data.len() < 0x200 {
            continue;
        }
        let info = parse_sum_header(&data[..0x200]);
        if !info.product_code.is_empty() {
            model_info = info;
        }
        all_sessions.extend(parse_sum_sessions(&data[0x200..]));
    }

    let mut daily_summaries: Vec<CpapSessionSummary> = Vec::new();
    let mut sessions: Vec<CpapSession> = Vec::new();

    for s in &all_sessions {
        let start = Utc.timestamp_opt(s.start_ts, 0)
            .single()
            .unwrap_or_else(Utc::now);
        let end = Utc.timestamp_opt(s.start_ts + s.usage_seconds, 0)
            .single()
            .unwrap_or(start);
        let usage_hours = s.usage_seconds as f64 / 3600.0;

        daily_summaries.push(CpapSessionSummary {
            date: start.format("%Y-%m-%d").to_string(),
            ahi: 0.0,
            ai: 0.0,
            hi: 0.0,
            cai: 0.0,
            oai: 0.0,
            leak_50: 0.0,
            leak_95: 0.0,
            leak_avg: None,
            pressure_50: s.min_pressure,
            pressure_95: s.pressure_95,
            usage_hours,
            pressure_mode: s.pressure_mode.clone(),
            resp_rate_avg: None,
            tidal_volume_avg: None,
            minute_ventilation_avg: None,
            snore_avg: None,
            flow_limitation_avg: None,
        });

        sessions.push(CpapSession {
            start_time: start,
            end_time: end,
            duration_minutes: s.usage_seconds as f64 / 60.0,
            file_type: "FPH-SUM".to_string(),
            sample_rate: 0.0,
            events: Vec::new(),
            timeseries: None,
        });
    }

    Ok(CpapDirectory {
        machine: MachineInfo {
            serial_number: serial.clone(),
            product_code: model_info.product_code,
            model: model_info.model,
            series: "SleepStyle".to_string(),
            properties: HashMap::new(),
        },
        daily_summaries,
        sessions,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fp_timestamp_known_value() {
        // Verify decode_fp_timestamp does not panic on zero-ish input.
        assert!(decode_fp_timestamp(0x0000_0000).is_none()); // month=0, day=0
    }

    #[test]
    fn fp_timestamp_roundtrip() {
        // Pack a known date and verify recovery: 2023-06-15 22:30:00
        // day=15=0x0F, month=6=0x06, year-2000=23=0x17
        // second=0, minute=30=0x1E, hour=22=0x16
        // raw = day | (month<<5) | (year<<9) | (second<<15) | (minute<<21) | (hour<<27)
        let day = 15u32;
        let month = 6u32;
        let year = 23u32;
        let second = 0u32;
        let minute = 30u32;
        let hour = 22u32;
        let raw = day | (month << 5) | (year << 9) | (second << 15) | (minute << 21) | (hour << 27);
        let ts = decode_fp_timestamp(raw);
        assert!(ts.is_some());
        let dt = Utc.timestamp_opt(ts.unwrap(), 0).unwrap();
        assert_eq!(dt.format("%Y-%m-%d %H:%M").to_string(), "2023-06-15 22:30");
    }

    #[test]
    fn can_handle_absent_dir() {
        assert!(!can_handle(Path::new("/tmp/nonexistent_fp_dir_xyz")));
    }

    #[test]
    fn parse_sum_sessions_empty() {
        // 0xFFFFFFFF terminator immediately — expect empty result.
        let data = 0xFFFF_FFFFu32.to_le_bytes();
        let sessions = parse_sum_sessions(&data);
        assert!(sessions.is_empty());
    }
}
