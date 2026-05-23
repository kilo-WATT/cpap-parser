//! Yuwell / BreathCare CPAP binary parser.
//!
//! Ported from OSCAR's `yuwell_loader.cpp`, copyright (c) The Oscar Team and
//! derived from SleepyHead by Mark Watkins (C) 2011-2018.
//!
//! Supports four SD-card layouts (all use `.BYS` binary files):
//!
//! | Format | Models         | Fingerprint                                      |
//! |--------|----------------|--------------------------------------------------|
//! | A      | YH-550         | `RunLog.bys` in root + `YH-*/\*.BYS`            |
//! | B      | YH-580 etc.    | `YHSD-NEW.BYS` (exactly 64 KB) in root          |
//! | C      | YH-830 etc.    | `YH-*/\*.BYS` without a root `RunLog.bys`       |
//! | D      | YH-680/690     | `YH-*/RunLog.bys` + session subdirectories       |

use std::collections::HashMap;
use std::io::{Cursor, Read};
use std::path::{Path, PathBuf};

use chrono::{TimeZone, Utc};

use crate::schema::{CpapDirectory, CpapSession, CpapSessionSummary, MachineInfo};

const YUWELL_CPAP: u8 = 0x00;
const YUWELL_APAP: u8 = 0x01;

// ─── Helper reads ─────────────────────────────────────────────────────────────

fn read_u8(cur: &mut Cursor<&[u8]>) -> Result<u8, String> {
    let mut buf = [0u8; 1];
    cur.read_exact(&mut buf).map_err(|e| e.to_string())?;
    Ok(buf[0])
}

fn read_i16_le(cur: &mut Cursor<&[u8]>) -> Result<i16, String> {
    let mut buf = [0u8; 2];
    cur.read_exact(&mut buf).map_err(|e| e.to_string())?;
    Ok(i16::from_le_bytes(buf))
}

#[allow(dead_code)]
fn read_u16_le(cur: &mut Cursor<&[u8]>) -> Result<u16, String> {
    let mut buf = [0u8; 2];
    cur.read_exact(&mut buf).map_err(|e| e.to_string())?;
    Ok(u16::from_le_bytes(buf))
}

fn skip_bytes(cur: &mut Cursor<&[u8]>, n: usize) -> Result<(), String> {
    let mut buf = vec![0u8; n];
    cur.read_exact(&mut buf).map_err(|e| e.to_string())
}

fn read_ascii_trimmed(cur: &mut Cursor<&[u8]>, len: usize) -> Result<String, String> {
    let mut buf = vec![0u8; len];
    cur.read_exact(&mut buf).map_err(|e| e.to_string())?;
    let s: String = buf.iter()
        .take_while(|&&b| b != 0)
        .map(|&b| b as char)
        .collect();
    Ok(s.trim().to_string())
}

// ─── Timestamp helpers ────────────────────────────────────────────────────────

/// Build a UTC Unix timestamp from Yuwell year-offset (year = raw + 2000), 1-based month/day/h/m/s.
fn yuwell_ts(year_offset: u8, month: u8, day: u8, hour: u8, minute: u8, second: u8) -> Option<i64> {
    let year = 2000i32 + year_offset as i32;
    Utc.with_ymd_and_hms(year, month as u32, day as u32, hour as u32, minute as u32, second as u32)
        .single()
        .map(|dt| dt.timestamp())
}

/// Build a UTC Unix timestamp from a full (non-offset) year used in Format C.
fn yuwell_ts_full_year(year: i16, month: u8, day: u8, hour: u8, minute: u8, second: u8) -> Option<i64> {
    Utc.with_ymd_and_hms(year as i32, month as u32, day as u32, hour as u32, minute as u32, second as u32)
        .single()
        .map(|dt| dt.timestamp())
}

// ─── Mode helpers ─────────────────────────────────────────────────────────────

fn mode_name(mode: u8) -> &'static str {
    if mode == YUWELL_CPAP { "CPAP" } else { "APAP" }
}

// ─── YH-* directory scanner ───────────────────────────────────────────────────

/// Return a list of `(dir_name, PathBuf)` for subdirectories starting with `YH`.
fn find_yh_dirs(root: &Path) -> Vec<(String, PathBuf)> {
    let Ok(entries) = std::fs::read_dir(root) else {
        return Vec::new();
    };
    entries
        .flatten()
        .filter(|e| {
            e.path().is_dir()
                && e.file_name().to_string_lossy().to_uppercase().starts_with("YH")
        })
        .map(|e| (e.file_name().to_string_lossy().to_string(), e.path()))
        .collect()
}

/// Collect all `*.BYS` files inside a directory (non-recursive).
fn bys_files_in(dir: &Path) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return Vec::new();
    };
    let mut files: Vec<PathBuf> = entries
        .flatten()
        .filter(|e| e.file_name().to_string_lossy().to_uppercase().ends_with(".BYS"))
        .map(|e| e.path())
        .collect();
    files.sort();
    files
}

// ─── Format A ─────────────────────────────────────────────────────────────────
//
// Root layout:  RunLog.bys (0-byte marker) + YH-<model-serial>/ containing *.BYS files.
// Each BYS file = one session (0x33-byte header + N × 10-byte minute records).
// Model/serial string is at offset 0x1E in the header (16 bytes, null-padded).

fn detect_format_a(root: &Path) -> bool {
    if !root.join("RunLog.bys").exists() {
        return false;
    }
    find_yh_dirs(root).iter().any(|(_, yh_path)| {
        bys_files_in(yh_path).first().and_then(|p| read_model_serial_at(p, 0x1E)).is_some()
    })
}

fn parse_format_a(root: &Path) -> Result<CpapDirectory, String> {
    let yh_dirs = find_yh_dirs(root);
    if yh_dirs.is_empty() {
        return Err("Format A: no YH-* directories found".to_string());
    }

    let (_, yh_path) = &yh_dirs[0];
    let files = bys_files_in(yh_path);
    let model_serial = files.first()
        .and_then(|p| read_model_serial_at(p, 0x1E))
        .unwrap_or_else(|| "Unknown".to_string());

    let mut daily_summaries = Vec::new();
    let mut sessions = Vec::new();

    for path in &files {
        if let Ok(data) = std::fs::read(path) {
            if let Some((session, summary)) = parse_format_a_session(&data, &model_serial) {
                daily_summaries.push(summary);
                sessions.push(session);
            }
        }
    }

    Ok(CpapDirectory {
        machine: make_machine(model_serial, "BreathCare ECO"),
        daily_summaries,
        sessions,
    })
}

fn parse_format_a_session(data: &[u8], _model_serial: &str) -> Option<(CpapSession, CpapSessionSummary)> {
    if data.len() < 0x33 {
        return None;
    }

    let mut cur = Cursor::new(data);

    // Header (0x33 bytes)
    let start_year   = read_u8(&mut cur).ok()?;
    let start_month  = read_u8(&mut cur).ok()?;
    let start_day    = read_u8(&mut cur).ok()?;
    let start_hour   = read_u8(&mut cur).ok()?;
    let start_min    = read_u8(&mut cur).ok()?;
    let start_sec    = read_u8(&mut cur).ok()?;

    let finish_year  = read_u8(&mut cur).ok()?;
    let finish_month = read_u8(&mut cur).ok()?;
    let finish_day   = read_u8(&mut cur).ok()?;
    let finish_hour  = read_u8(&mut cur).ok()?;
    let finish_min   = read_u8(&mut cur).ok()?;
    let finish_sec   = read_u8(&mut cur).ok()?;

    let mode          = read_u8(&mut cur).ok()?;
    let _ramp         = read_u8(&mut cur).ok()?;
    let _init_press   = read_u8(&mut cur).ok()?;
    let _min_press    = read_u8(&mut cur).ok()?;
    let _max_press    = read_u8(&mut cur).ok()?;
    let _skip1        = read_u8(&mut cur).ok()?;
    let _humidity     = read_u8(&mut cur).ok()?;
    skip_bytes(&mut cur, 7).ok()?;           // 7 unknown bytes
    let _avg_leak     = read_u8(&mut cur).ok()?;
    let _skip2        = read_u8(&mut cur).ok()?;
    let _avg_pressure = read_u8(&mut cur).ok()?;
    let _skip3        = read_u8(&mut cur).ok()?;
    skip_bytes(&mut cur, 16).ok()?;          // model/serial (already known)
    let record_count  = read_i16_le(&mut cur).ok()?;
    // 2 remaining header bytes + 0xF9 terminator = skip to 0x33
    skip_bytes(&mut cur, 3).ok()?;

    let start_ts  = yuwell_ts(start_year, start_month, start_day, start_hour, start_min, start_sec)?;
    let finish_ts = yuwell_ts(finish_year, finish_month, finish_day, finish_hour, finish_min, finish_sec)?;

    let mut total_oa: u32 = 0;
    let mut total_hi: u32 = 0;
    let mut total_ca: u32 = 0;
    let mut pressure_sum: f64 = 0.0;
    let mut leak_sum: f64 = 0.0;
    let mut valid_minutes: u32 = 0;

    for _ in 0..record_count.max(0) {
        let pressure    = match read_u8(&mut cur) { Ok(v) => v, Err(_) => break };
        skip_bytes(&mut cur, 2).ok();
        let oai         = match read_u8(&mut cur) { Ok(v) => v, Err(_) => break };
        let hi          = match read_u8(&mut cur) { Ok(v) => v, Err(_) => break };
        let cai         = match read_u8(&mut cur) { Ok(v) => v, Err(_) => break };
        skip_bytes(&mut cur, 3).ok();
        let leak_volume = match read_u8(&mut cur) { Ok(v) => v, Err(_) => break };

        total_oa += oai as u32;
        total_hi += hi as u32;
        total_ca += cai as u32;
        pressure_sum += pressure as f64 / 10.0;
        leak_sum += leak_volume as f64;
        valid_minutes += 1;
    }

    let usage_hours = (finish_ts - start_ts).max(0) as f64 / 3600.0;
    let (ahi, ai, hi_idx, oai_idx, cai_idx) = compute_indices(total_oa, total_hi, total_ca, usage_hours);
    let avg_pressure = if valid_minutes > 0 { pressure_sum / valid_minutes as f64 } else { 0.0 };
    let avg_leak = if valid_minutes > 0 { leak_sum / valid_minutes as f64 } else { 0.0 };

    let start  = Utc.timestamp_opt(start_ts, 0).single().unwrap_or_else(Utc::now);
    let finish = Utc.timestamp_opt(finish_ts, 0).single().unwrap_or(start);

    Some((
        CpapSession {
            start_time: start,
            end_time: finish,
            duration_minutes: (finish_ts - start_ts).max(0) as f64 / 60.0,
            file_type: "BYS-A".to_string(),
            sample_rate: 0.0,
            events: Vec::new(),
            timeseries: None,
        },
        CpapSessionSummary {
            date: start.format("%Y-%m-%d").to_string(),
            ahi,
            ai,
            hi: hi_idx,
            cai: cai_idx,
            oai: oai_idx,
            leak_50: 0.0,
            leak_95: 0.0,
            leak_avg: Some(avg_leak),
            pressure_50: avg_pressure,
            pressure_95: 0.0,
            usage_hours,
            pressure_mode: mode_name(mode).to_string(),
            resp_rate_avg: None,
            tidal_volume_avg: None,
            minute_ventilation_avg: None,
            snore_avg: None,
            flow_limitation_avg: None,
        },
    ))
}

// ─── Format B ─────────────────────────────────────────────────────────────────
//
// Root layout: YHSD-NEW.BYS (exactly 64 KB).
// Contains a global header + up to N 30-byte session summaries starting at 0xC00,
// with per-minute minute data at offsets stored in each summary.

fn detect_format_b(root: &Path) -> bool {
    let p = root.join("YHSD-NEW.BYS");
    p.is_file()
        && std::fs::metadata(&p)
            .map(|m| m.len() == 0x10000)
            .unwrap_or(false)
}

fn parse_format_b(root: &Path) -> Result<CpapDirectory, String> {
    let path = root.join("YHSD-NEW.BYS");
    let data = std::fs::read(&path)
        .map_err(|e| format!("Cannot read YHSD-NEW.BYS: {}", e))?;

    if data.len() != 0x10000 {
        return Err("YHSD-NEW.BYS is not exactly 64 KB".to_string());
    }

    let mut cur = Cursor::new(data.as_slice());

    // Global header
    skip_bytes(&mut cur, 4).ok(); // magic "AAAA"
    let _mode        = read_u8(&mut cur)?;
    let _ramp        = read_u8(&mut cur)?;
    let _init_press  = read_u8(&mut cur)?;
    let _press_set   = read_u8(&mut cur)?;
    let _max_press   = read_u8(&mut cur)?;
    let _min_press   = read_u8(&mut cur)?;
    let _humidity    = read_u8(&mut cur)?;
    let _fps_level   = read_u8(&mut cur)?;
    skip_bytes(&mut cur, 19)?;
    let record_count = read_i16_le(&mut cur)?;
    skip_bytes(&mut cur, 99)?;
    let model_serial = read_ascii_trimmed(&mut cur, 16)?;
    skip_bytes(&mut cur, 2924)?; // jump to 0xC00

    let mut daily_summaries = Vec::new();
    let mut sessions = Vec::new();

    for _ in 0..record_count.max(0) {
        let start_year   = read_u8(&mut cur)?;
        let start_month  = read_u8(&mut cur)?;
        let start_day    = read_u8(&mut cur)?;
        let start_hour   = read_u8(&mut cur)?;
        let start_min    = read_u8(&mut cur)?;
        let start_sec    = read_u8(&mut cur)?;
        let finish_year  = read_u8(&mut cur)?;
        let finish_month = read_u8(&mut cur)?;
        let finish_day   = read_u8(&mut cur)?;
        let finish_hour  = read_u8(&mut cur)?;
        let finish_min   = read_u8(&mut cur)?;
        let finish_sec   = read_u8(&mut cur)?;
        let sess_mode     = read_u8(&mut cur)?;
        let _ramp2       = read_u8(&mut cur)?;
        let _init_press2 = read_u8(&mut cur)?;
        let _press_set2  = read_u8(&mut cur)?;
        let _max_press2  = read_u8(&mut cur)?;
        let _min_press2  = read_u8(&mut cur)?;
        let _humidity2   = read_u8(&mut cur)?;
        let _fps_level2  = read_u8(&mut cur)?;
        let oai_count    = read_u8(&mut cur)?;
        let hi_count     = read_u8(&mut cur)?;
        skip_bytes(&mut cur, 2)?;
        let _avg_leak    = read_u8(&mut cur)?;
        let _avg_press   = read_u8(&mut cur)?;
        let offset_high  = read_u8(&mut cur)?;
        let offset_low   = read_u8(&mut cur)?;
        skip_bytes(&mut cur, 1)?;
        let session_minutes = read_u8(&mut cur)?;

        let Some(start_ts) = yuwell_ts(start_year, start_month, start_day, start_hour, start_min, start_sec) else { continue };
        let Some(finish_ts) = yuwell_ts(finish_year, finish_month, finish_day, finish_hour, finish_min, finish_sec) else { continue };

        let usage_hours = session_minutes as f64 / 60.0;

        // Read per-minute data at the stored offset within the 64KB file.
        let data_offset = ((offset_high as usize) << 8 | offset_low as usize) + 0x7600;
        let minutes = session_minutes as usize;
        let mut total_oa: u32 = oai_count as u32;
        let mut total_hi: u32 = hi_count as u32;
        let mut total_ca: u32 = 0;

        if data_offset + minutes * 7 <= data.len() {
            let minute_data = &data[data_offset..data_offset + minutes * 7];
            let mc = Cursor::new(minute_data);
            for i in 0..minutes {
                let leakage = mc.get_ref()[i * 7];
                // Sanity check: first record uses leakage==0xF9 as valid-data sentinel.
                if i == 0 && leakage != 0xF9 {
                    break;
                }
                let _pressure = mc.get_ref()[i * 7 + 1];
                let _spo2    = mc.get_ref()[i * 7 + 2];
                let oai_m    = mc.get_ref()[i * 7 + 3];
                let hi_m     = mc.get_ref()[i * 7 + 4];
                let _pulse   = mc.get_ref()[i * 7 + 5];
                let cai_m    = mc.get_ref()[i * 7 + 6];
                // Supplement summary-level counts with per-minute data.
                if i > 0 {
                    total_oa += oai_m as u32;
                    total_hi += hi_m as u32;
                    total_ca += cai_m as u32;
                }
            }
        }

        let (ahi, ai, hi_idx, oai_idx, cai_idx) = compute_indices(total_oa, total_hi, total_ca, usage_hours);

        let start  = Utc.timestamp_opt(start_ts, 0).single().unwrap_or_else(Utc::now);
        let finish = Utc.timestamp_opt(finish_ts, 0).single().unwrap_or(start);

        daily_summaries.push(CpapSessionSummary {
            date: start.format("%Y-%m-%d").to_string(),
            ahi,
            ai,
            hi: hi_idx,
            cai: cai_idx,
            oai: oai_idx,
            leak_50: 0.0,
            leak_95: 0.0,
            leak_avg: None,
            pressure_50: 0.0,
            pressure_95: 0.0,
            usage_hours,
            pressure_mode: mode_name(sess_mode).to_string(),
            resp_rate_avg: None,
            tidal_volume_avg: None,
            minute_ventilation_avg: None,
            snore_avg: None,
            flow_limitation_avg: None,
        });

        sessions.push(CpapSession {
            start_time: start,
            end_time: finish,
            duration_minutes: (finish_ts - start_ts).max(0) as f64 / 60.0,
            file_type: "BYS-B".to_string(),
            sample_rate: 0.0,
            events: Vec::new(),
            timeseries: None,
        });
    }

    Ok(CpapDirectory {
        machine: make_machine(model_serial, "BreathCare I"),
        daily_summaries,
        sessions,
    })
}

// ─── Format C ─────────────────────────────────────────────────────────────────
//
// Root layout: YH-<model-serial>/ containing *.BYS files.
// No RunLog.bys in root (distinguishing from Format A).
// Each BYS file: 0x3C-byte header + N × 0x28-byte minute records.
// Model/serial string is at offset 0x27 in the header.

fn detect_format_c(root: &Path) -> bool {
    find_yh_dirs(root).iter().any(|(_, yh_path)| {
        bys_files_in(yh_path)
            .first()
            .and_then(|p| read_model_serial_at(p, 0x27))
            .is_some()
    })
}

fn parse_format_c(root: &Path) -> Result<CpapDirectory, String> {
    let yh_dirs = find_yh_dirs(root);
    if yh_dirs.is_empty() {
        return Err("Format C: no YH-* directories found".to_string());
    }

    let (_, yh_path) = &yh_dirs[0];
    let files = bys_files_in(yh_path);
    let model_serial = files.first()
        .and_then(|p| read_model_serial_at(p, 0x27))
        .unwrap_or_else(|| "Unknown".to_string());

    let mut daily_summaries = Vec::new();
    let mut sessions = Vec::new();

    for path in &files {
        if let Ok(data) = std::fs::read(path) {
            if let Some((sess, summary)) = parse_format_c_session(&data) {
                daily_summaries.push(summary);
                sessions.push(sess);
            }
        }
    }

    Ok(CpapDirectory {
        machine: make_machine(model_serial, "BreathCare II"),
        daily_summaries,
        sessions,
    })
}

fn parse_format_c_session(data: &[u8]) -> Option<(CpapSession, CpapSessionSummary)> {
    if data.len() < 0x3C {
        return None;
    }

    let mut cur = Cursor::new(data);

    // Header (0x3C = 60 bytes)
    let start_year   = read_i16_le(&mut cur).ok()?;
    let start_month  = read_u8(&mut cur).ok()?;
    let start_day    = read_u8(&mut cur).ok()?;
    let start_hour   = read_u8(&mut cur).ok()?;
    let start_min    = read_u8(&mut cur).ok()?;
    let start_sec    = read_u8(&mut cur).ok()?;

    let finish_year  = read_i16_le(&mut cur).ok()?;
    let finish_month = read_u8(&mut cur).ok()?;
    let finish_day   = read_u8(&mut cur).ok()?;
    let finish_hour  = read_u8(&mut cur).ok()?;
    let finish_min   = read_u8(&mut cur).ok()?;
    let finish_sec   = read_u8(&mut cur).ok()?;

    let record_count  = read_i16_le(&mut cur).ok()?;
    skip_bytes(&mut cur, 5).ok()?;
    let _humidity     = read_u8(&mut cur).ok()?;
    skip_bytes(&mut cur, 17).ok()?;
    skip_bytes(&mut cur, 16).ok()?; // model_serial (already retrieved)
    skip_bytes(&mut cur, 5).ok()?;  // remainder of header

    let start_ts  = yuwell_ts_full_year(start_year, start_month, start_day, start_hour, start_min, start_sec)?;
    let finish_ts = yuwell_ts_full_year(finish_year, finish_month, finish_day, finish_hour, finish_min, finish_sec)?;

    let mut total_oa: u32 = 0;
    let mut total_hi: u32 = 0;
    let mut total_ca: u32 = 0;
    let mut pressure_sum: f64 = 0.0;
    let mut tidal_sum: f64 = 0.0;
    let mut rr_sum: f64 = 0.0;
    let mut valid_minutes: u32 = 0;
    let mut detected_mode: u8 = YUWELL_APAP;

    // Each record is 0x28 = 40 bytes.
    for _ in 0..record_count.max(0) {
        skip_bytes(&mut cur, 1).ok()?;       // 0xF9 marker
        let mode_rec = read_u8(&mut cur).ok()?;
        skip_bytes(&mut cur, 10).ok()?;
        let pressure = read_u8(&mut cur).ok()?;
        let _init    = read_u8(&mut cur).ok()?;
        skip_bytes(&mut cur, 4).ok()?;
        let _ramp    = read_u8(&mut cur).ok()?;
        skip_bytes(&mut cur, 2).ok()?;
        let tidal_volume = read_i16_le(&mut cur).ok()?;
        let _leak_vol = read_u8(&mut cur).ok()?;
        skip_bytes(&mut cur, 1).ok()?;
        let _minute_vol  = read_u8(&mut cur).ok()?;
        skip_bytes(&mut cur, 5).ok()?;
        let _insp_ratio  = read_u8(&mut cur).ok()?;
        skip_bytes(&mut cur, 2).ok()?;
        let resp_rate    = read_u8(&mut cur).ok()?;
        let oai          = read_u8(&mut cur).ok()?;
        let hi           = read_u8(&mut cur).ok()?;
        skip_bytes(&mut cur, 1).ok()?;
        let cai          = read_u8(&mut cur).ok()?;
        skip_bytes(&mut cur, 1).ok()?;

        if valid_minutes == 0 {
            detected_mode = mode_rec;
        }
        total_oa += oai as u32;
        total_hi += hi as u32;
        total_ca += cai as u32;
        pressure_sum += pressure as f64 / 10.0;
        tidal_sum += tidal_volume as f64;
        rr_sum += resp_rate as f64;
        valid_minutes += 1;
    }

    let usage_hours = (finish_ts - start_ts).max(0) as f64 / 3600.0;
    let (ahi, ai, hi_idx, oai_idx, cai_idx) = compute_indices(total_oa, total_hi, total_ca, usage_hours);
    let avg_pressure = if valid_minutes > 0 { pressure_sum / valid_minutes as f64 } else { 0.0 };
    let avg_tidal    = if valid_minutes > 0 { Some(tidal_sum / valid_minutes as f64) } else { None };
    let avg_rr       = if valid_minutes > 0 { Some(rr_sum / valid_minutes as f64) } else { None };

    let start  = Utc.timestamp_opt(start_ts, 0).single().unwrap_or_else(Utc::now);
    let finish = Utc.timestamp_opt(finish_ts, 0).single().unwrap_or(start);

    Some((
        CpapSession {
            start_time: start,
            end_time: finish,
            duration_minutes: (finish_ts - start_ts).max(0) as f64 / 60.0,
            file_type: "BYS-C".to_string(),
            sample_rate: 0.0,
            events: Vec::new(),
            timeseries: None,
        },
        CpapSessionSummary {
            date: start.format("%Y-%m-%d").to_string(),
            ahi,
            ai,
            hi: hi_idx,
            cai: cai_idx,
            oai: oai_idx,
            leak_50: 0.0,
            leak_95: 0.0,
            leak_avg: None,
            pressure_50: avg_pressure,
            pressure_95: 0.0,
            usage_hours,
            pressure_mode: mode_name(detected_mode).to_string(),
            resp_rate_avg: avg_rr,
            tidal_volume_avg: avg_tidal,
            minute_ventilation_avg: None,
            snore_avg: None,
            flow_limitation_avg: None,
        },
    ))
}

// ─── Format D ─────────────────────────────────────────────────────────────────
//
// Root layout: YH-<serial>/ (each containing its own RunLog.bys) with numbered
// session subdirectories that hold *s.BYS (summary) and *m.BYS (minute) files.

fn detect_format_d(root: &Path) -> bool {
    find_yh_dirs(root).iter().any(|(_, yh_path)| {
        yh_path.join("RunLog.bys").exists()
            && std::fs::read_dir(yh_path)
                .ok()
                .map(|e| e.flatten().any(|f| f.path().is_dir()))
                .unwrap_or(false)
    })
}

fn parse_format_d(root: &Path) -> Result<CpapDirectory, String> {
    let yh_dirs = find_yh_dirs(root);
    if yh_dirs.is_empty() {
        return Err("Format D: no YH-* directories found".to_string());
    }

    let (dir_name, yh_path) = &yh_dirs[0];
    let mut model_serial = dir_name.clone();
    let mut daily_summaries = Vec::new();
    let mut sessions = Vec::new();

    // Iterate numbered session subdirectories.
    let Ok(session_dirs) = std::fs::read_dir(yh_path) else {
        return Err(format!("Cannot read {}", yh_path.display()));
    };
    let mut sess_paths: Vec<PathBuf> = session_dirs
        .flatten()
        .filter(|e| e.path().is_dir())
        .map(|e| e.path())
        .collect();
    sess_paths.sort();

    for sess_dir in &sess_paths {
        if let Some((ms, sess, summary)) = parse_format_d_session(sess_dir) {
            if !ms.is_empty() && ms != "Unknown" {
                model_serial = ms;
            }
            daily_summaries.push(summary);
            sessions.push(sess);
        }
    }

    Ok(CpapDirectory {
        machine: make_machine(model_serial, "BreathCare III"),
        daily_summaries,
        sessions,
    })
}

fn find_file_with_suffix(dir: &Path, suffix: &str) -> Option<PathBuf> {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return None;
    };
    let sfx = suffix.to_uppercase();
    entries.flatten().find(|e| {
        e.file_name().to_string_lossy().to_uppercase().ends_with(&sfx)
    }).map(|e| e.path())
}

fn parse_format_d_session(sess_dir: &Path) -> Option<(String, CpapSession, CpapSessionSummary)> {
    // *s.BYS — session-level summary (start/end times, mode, ramp)
    let s_file = find_file_with_suffix(sess_dir, "s.bys")?;
    let s_data = std::fs::read(&s_file).ok()?;
    if s_data.len() < 0x4D {
        return None;
    }

    let mut cur = Cursor::new(s_data.as_slice());
    skip_bytes(&mut cur, 2).ok()?;

    let start_year   = read_u8(&mut cur).ok()?;
    let start_month  = read_u8(&mut cur).ok()?;
    let start_day    = read_u8(&mut cur).ok()?;
    let start_hour   = read_u8(&mut cur).ok()?;
    let start_min    = read_u8(&mut cur).ok()?;
    let start_sec    = read_u8(&mut cur).ok()?;

    let finish_year  = read_u8(&mut cur).ok()?;
    let finish_month = read_u8(&mut cur).ok()?;
    let finish_day   = read_u8(&mut cur).ok()?;
    let finish_hour  = read_u8(&mut cur).ok()?;
    let finish_min   = read_u8(&mut cur).ok()?;
    let finish_sec   = read_u8(&mut cur).ok()?;

    skip_bytes(&mut cur, 18).ok()?;
    let model_serial = read_ascii_trimmed(&mut cur, 16).ok()?;
    skip_bytes(&mut cur, 8).ok()?;  // some embedded date
    let mode = read_u8(&mut cur).ok()?;
    skip_bytes(&mut cur, 18).ok()?;
    let _fps_level = read_u8(&mut cur).ok()?;
    let _ramp      = read_u8(&mut cur).ok()?;

    let start_ts  = yuwell_ts(start_year, start_month, start_day, start_hour, start_min, start_sec)?;
    let finish_ts = yuwell_ts(finish_year, finish_month, finish_day, finish_hour, finish_min, finish_sec)?;

    // *m.BYS — per-minute data
    let mut total_oa: u32 = 0;
    let mut total_hi: u32 = 0;
    let mut total_ca: u32 = 0;
    let mut pressure_sum: f64 = 0.0;
    let mut valid_minutes: u32 = 0;

    if let Some(m_file) = find_file_with_suffix(sess_dir, "m.bys") {
        if let Ok(m_data) = std::fs::read(&m_file) {
            if m_data.len() >= 0x08 {
                let mut mc = Cursor::new(m_data.as_slice());
                skip_bytes(&mut mc, 6).ok();
                if let Ok(record_count) = read_i16_le(&mut mc) {
                    for _ in 0..record_count.max(0) {
                        let pressure = match read_u8(&mut mc) { Ok(v) => v, Err(_) => break };
                        skip_bytes(&mut mc, 1).ok();
                        let oai      = match read_u8(&mut mc) { Ok(v) => v, Err(_) => break };
                        let cai      = match read_u8(&mut mc) { Ok(v) => v, Err(_) => break };
                        let hi       = match read_u8(&mut mc) { Ok(v) => v, Err(_) => break };
                        skip_bytes(&mut mc, 4).ok();
                        let _leakage = match read_u8(&mut mc) { Ok(v) => v, Err(_) => break };
                        skip_bytes(&mut mc, 5).ok();
                        let _spo2  = match read_u8(&mut mc) { Ok(v) => v, Err(_) => break };
                        let _pulse = match read_u8(&mut mc) { Ok(v) => v, Err(_) => break };
                        skip_bytes(&mut mc, 2).ok();

                        total_oa += oai as u32;
                        total_hi += hi as u32;
                        total_ca += cai as u32;
                        pressure_sum += pressure as f64 / 10.0;
                        valid_minutes += 1;
                    }
                }
            }
        }
    }

    let usage_hours = (finish_ts - start_ts).max(0) as f64 / 3600.0;
    let (ahi, ai, hi_idx, oai_idx, cai_idx) = compute_indices(total_oa, total_hi, total_ca, usage_hours);
    let avg_pressure = if valid_minutes > 0 { pressure_sum / valid_minutes as f64 } else { 0.0 };

    let start  = Utc.timestamp_opt(start_ts, 0).single().unwrap_or_else(Utc::now);
    let finish = Utc.timestamp_opt(finish_ts, 0).single().unwrap_or(start);

    Some((
        model_serial,
        CpapSession {
            start_time: start,
            end_time: finish,
            duration_minutes: (finish_ts - start_ts).max(0) as f64 / 60.0,
            file_type: "BYS-D".to_string(),
            sample_rate: 0.0,
            events: Vec::new(),
            timeseries: None,
        },
        CpapSessionSummary {
            date: start.format("%Y-%m-%d").to_string(),
            ahi,
            ai,
            hi: hi_idx,
            cai: cai_idx,
            oai: oai_idx,
            leak_50: 0.0,
            leak_95: 0.0,
            leak_avg: None,
            pressure_50: avg_pressure,
            pressure_95: 0.0,
            usage_hours,
            pressure_mode: mode_name(mode).to_string(),
            resp_rate_avg: None,
            tidal_volume_avg: None,
            minute_ventilation_avg: None,
            snore_avg: None,
            flow_limitation_avg: None,
        },
    ))
}

// ─── Shared utilities ─────────────────────────────────────────────────────────

/// Read the 16-byte null-padded model/serial string at *offset* bytes into *path*.
fn read_model_serial_at(path: &Path, offset: usize) -> Option<String> {
    let data = std::fs::read(path).ok()?;
    if data.len() < offset + 16 {
        return None;
    }
    let s: String = data[offset..offset + 16]
        .iter()
        .take_while(|&&b| b != 0)
        .map(|&b| b as char)
        .collect();
    let trimmed = s.trim().to_string();
    if trimmed.to_uppercase().starts_with("YH") {
        Some(trimmed)
    } else {
        None
    }
}

/// Compute AHI and component indices (events/hour) from raw event counts.
fn compute_indices(total_oa: u32, total_hi: u32, total_ca: u32, usage_hours: f64) -> (f64, f64, f64, f64, f64) {
    if usage_hours < 0.01 {
        return (0.0, 0.0, 0.0, 0.0, 0.0);
    }
    let ahi  = (total_oa + total_hi + total_ca) as f64 / usage_hours;
    let ai   = (total_oa + total_ca) as f64 / usage_hours;
    let hi   = total_hi as f64 / usage_hours;
    let oai  = total_oa as f64 / usage_hours;
    let cai  = total_ca as f64 / usage_hours;
    (ahi, ai, hi, oai, cai)
}

fn make_machine(model_serial: String, series: &str) -> MachineInfo {
    MachineInfo {
        serial_number: model_serial.clone(),
        product_code: String::new(),
        model: format!("Yuwell {}", model_serial),
        series: series.to_string(),
        properties: HashMap::new(),
    }
}

// ─── Public API ───────────────────────────────────────────────────────────────

/// Return `true` if *dir_path* contains a recognisable Yuwell data layout.
///
/// Accepts any of the four BYS-format variants (A–D).
pub fn can_handle(dir_path: &Path) -> bool {
    // Format B: single 64 KB file
    let new_bys = dir_path.join("YHSD-NEW.BYS");
    if new_bys.is_file() && std::fs::metadata(&new_bys).map(|m| m.len() == 0x10000).unwrap_or(false) {
        return true;
    }
    // Formats A, C, D: at least one YH-* subdirectory
    find_yh_dirs(dir_path).iter().any(|(_, p)| {
        !bys_files_in(p).is_empty()
            || std::fs::read_dir(p).ok().map(|e| e.flatten().any(|f| f.path().is_dir())).unwrap_or(false)
    })
}

/// Parse a Yuwell data directory into a [`CpapDirectory`].
///
/// Detects the sub-format automatically and delegates to the appropriate parser.
///
/// # Errors
///
/// Returns `Err(String)` if no supported Yuwell layout is found or parsing fails.
pub fn parse_yuwell(dir_path: &Path) -> Result<CpapDirectory, String> {
    if detect_format_a(dir_path) {
        return parse_format_a(dir_path);
    }
    if detect_format_b(dir_path) {
        return parse_format_b(dir_path);
    }
    if detect_format_d(dir_path) {
        return parse_format_d(dir_path);
    }
    if detect_format_c(dir_path) {
        return parse_format_c(dir_path);
    }
    Err("No supported Yuwell format (A–D) detected".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn can_handle_absent_dir() {
        assert!(!can_handle(Path::new("/tmp/nonexistent_yuwell_xyz")));
    }

    #[test]
    fn compute_indices_zero_hours() {
        let (ahi, ai, hi, oai, cai) = compute_indices(10, 5, 2, 0.0);
        assert_eq!(ahi, 0.0);
        assert_eq!(ai, 0.0);
        assert_eq!(hi, 0.0);
        assert_eq!(oai, 0.0);
        assert_eq!(cai, 0.0);
    }

    #[test]
    fn compute_indices_nonzero() {
        // 8 OA, 4 HI, 2 CA over 2 hours → AHI=7, AI=5, HI=2, OAI=4, CAI=1
        let (ahi, _ai, hi, oai, cai) = compute_indices(8, 4, 2, 2.0);
        assert!((ahi - 7.0).abs() < 1e-9);
        assert!((oai - 4.0).abs() < 1e-9);
        assert!((cai - 1.0).abs() < 1e-9);
        assert!((hi  - 2.0).abs() < 1e-9);
    }

    #[test]
    fn yuwell_ts_valid() {
        let ts = yuwell_ts(23, 6, 15, 22, 30, 0);
        assert!(ts.is_some());
        let dt = Utc.timestamp_opt(ts.unwrap(), 0).unwrap();
        assert_eq!(dt.format("%Y-%m-%d").to_string(), "2023-06-15");
    }

    #[test]
    fn detect_format_b_absent() {
        assert!(!detect_format_b(Path::new("/tmp/nonexistent_yuwell_xyz")));
    }
}
