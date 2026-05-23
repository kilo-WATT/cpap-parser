//! Löwenstein Prisma Line parser.
//!
//! Parses `config.pcfg` (device identity) and `therapy.pdat` (daily summaries,
//! per-session events, and waveform signals) from Löwenstein Eyra/prisma25 devices.

use std::collections::HashMap;
use std::io::{Cursor, Read};
use std::path::Path;

use crate::schema::{CpapDirectory, CpapEvent, CpapSession, CpapSessionSummary, MachineInfo, TimeSeriesData};

pub fn can_handle(path: &Path) -> bool {
    path.join("config.pcfg").is_file()
}

pub fn parse_prisma_line(path: &Path, include_timeseries: bool) -> Result<CpapDirectory, String> {
    let config_bytes = std::fs::read(path.join("config.pcfg"))
        .map_err(|e| format!("Cannot read config.pcfg: {e}"))?;
    let therapy_bytes = std::fs::read(path.join("therapy.pdat"))
        .map_err(|e| format!("Cannot read therapy.pdat: {e}"))?;

    let machine = read_device_info(&config_bytes)?;
    let daily_summaries = parse_daily_summaries(&therapy_bytes)?;
    let sessions = parse_sessions(&therapy_bytes, include_timeseries)?;

    Ok(CpapDirectory { machine, daily_summaries, sessions })
}

fn read_device_info(config_bytes: &[u8]) -> Result<MachineInfo, String> {
    let model_names: HashMap<&str, &str> = [
        ("0x92", "Prisma Smart"),
        ("0x91", "Prisma Soft"),
        ("22",   "prisma25S"),
        ("23",   "prisma25ST"),
        ("27",   "Löwenstein Eyra"),
    ]
    .iter()
    .cloned()
    .collect();

    let cursor = Cursor::new(config_bytes);
    let mut archive =
        zip::ZipArchive::new(cursor).map_err(|e| format!("config.pcfg ZIP error: {e}"))?;
    let mut xml_file = archive
        .by_name("mnt/flash/conf/device.xml")
        .map_err(|e| format!("device.xml not found in config.pcfg: {e}"))?;
    let mut xml = String::new();
    xml_file
        .read_to_string(&mut xml)
        .map_err(|e| format!("Cannot read device.xml: {e}"))?;

    let device_type = xml_attr_value(&xml, "DeviceType").unwrap_or_default();
    let serial = xml_attr_value(&xml, "DeviceSerialNumber").unwrap_or_default();
    let fw_version = xml_attr_value(&xml, "FWVersion").unwrap_or_default();
    let fw_build = xml_attr_value(&xml, "FWBuild").unwrap_or_default();

    let model = model_names
        .get(device_type.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| format!("Prisma Line (type {device_type})"));

    Ok(MachineInfo {
        serial_number: serial,
        product_code: device_type,
        model,
        series: "Löwenstein Medical".to_string(),
        properties: [
            ("fw_version".to_string(), fw_version),
            ("fw_build".to_string(), fw_build),
        ]
        .iter()
        .cloned()
        .collect(),
    })
}

fn parse_daily_summaries(therapy_bytes: &[u8]) -> Result<Vec<CpapSessionSummary>, String> {
    use quick_xml::events::Event;
    use quick_xml::Reader;

    let cursor = Cursor::new(therapy_bytes);
    let mut archive =
        zip::ZipArchive::new(cursor).map_err(|e| format!("therapy.pdat ZIP error: {e}"))?;
    let mut stat_file = archive
        .by_name("mnt/flash/data/statistics/statistics_year.bin")
        .map_err(|e| format!("statistics_year.bin not found: {e}"))?;
    let mut xml = String::new();
    stat_file
        .read_to_string(&mut xml)
        .map_err(|e| format!("Cannot read statistics_year.bin: {e}"))?;

    let mode_labels: HashMap<u32, &str> = [
        (1, "CPAP"), (2, "APAP"), (3, "ACSV"), (4, "S"), (9, "Auto-S"), (10, "Auto-ST"),
    ]
    .iter()
    .cloned()
    .collect();

    struct DayAcc {
        total_usage_sec: u64,
        dominant_mode: u32,
        dominant_usage_sec: u64,
        set_pressure_x100: u32,
    }
    let mut days: std::collections::BTreeMap<String, DayAcc> = Default::default();

    let mut current_date: Option<String> = None;
    let mut current_mode: u32 = 0;
    let mut current_usage_sec: u64 = 0;

    fn read_attr(e: &quick_xml::events::BytesStart<'_>, name: &[u8]) -> Option<String> {
        e.attributes()
            .filter_map(|a| a.ok())
            .find(|a| a.key.as_ref() == name)
            .and_then(|a| String::from_utf8(a.value.to_vec()).ok())
    }

    let mut reader = Reader::from_str(&xml);
    loop {
        match reader.read_event() {
            Ok(Event::Start(ref e)) | Ok(Event::Empty(ref e)) => {
                match e.name().as_ref() {
                    b"day" => {
                        current_date = read_attr(e, b"d");
                        current_mode = 0;
                        current_usage_sec = 0;
                    }
                    b"rec" => {
                        let Some(ref date) = current_date else { continue };
                        let m: u32 = read_attr(e, b"m")
                            .and_then(|v| v.parse().ok())
                            .unwrap_or(0);
                        if !mode_labels.contains_key(&m) {
                            current_mode = 0;
                            current_usage_sec = 0;
                            continue;
                        }
                        current_mode = m;
                        current_usage_sec = read_attr(e, b"t")
                            .map(|t| parse_t_intervals_total_sec(&t))
                            .unwrap_or(0);
                        let acc = days.entry(date.clone()).or_insert(DayAcc {
                            total_usage_sec: 0,
                            dominant_mode: current_mode,
                            dominant_usage_sec: 0,
                            set_pressure_x100: 0,
                        });
                        acc.total_usage_sec += current_usage_sec;
                        if current_usage_sec > acc.dominant_usage_sec {
                            acc.dominant_mode = current_mode;
                            acc.dominant_usage_sec = current_usage_sec;
                        }
                    }
                    b"s" => {
                        let Some(ref date) = current_date else { continue };
                        if current_mode == 0 { continue; }
                        let i = read_attr(e, b"i").unwrap_or_default();
                        let v = read_attr(e, b"v").unwrap_or_default();
                        if i == "309" {
                            if let Ok(val) = v.parse::<u32>() {
                                if let Some(acc) = days.get_mut(date) {
                                    if current_usage_sec >= acc.dominant_usage_sec {
                                        acc.set_pressure_x100 = val;
                                    }
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
            Ok(Event::End(ref e)) => match e.name().as_ref() {
                b"day" => {
                    current_date = None;
                    current_mode = 0;
                    current_usage_sec = 0;
                }
                b"rec" => {
                    current_mode = 0;
                    current_usage_sec = 0;
                }
                _ => {}
            },
            Ok(Event::Eof) => break,
            Err(e) => return Err(format!("XML parse error in statistics_year.bin: {e}")),
            _ => {}
        }
    }

    let mut summaries = Vec::new();
    for (date_str, acc) in &days {
        let usage_hours = acc.total_usage_sec as f64 / 3600.0;
        if usage_hours <= 0.0 {
            continue;
        }
        let mode_label = mode_labels
            .get(&acc.dominant_mode)
            .copied()
            .unwrap_or("CPAP");
        let set_pressure = acc.set_pressure_x100 as f64 / 100.0;
        summaries.push(CpapSessionSummary {
            date: date_str.clone(),
            ahi: 0.0,
            ai: 0.0,
            hi: 0.0,
            cai: 0.0,
            oai: 0.0,
            leak_50: 0.0,
            leak_95: 0.0,
            leak_avg: None,
            pressure_50: set_pressure,
            pressure_95: set_pressure,
            usage_hours,
            pressure_mode: mode_label.to_string(),
            resp_rate_avg: None,
            tidal_volume_avg: None,
            minute_ventilation_avg: None,
            snore_avg: None,
            flow_limitation_avg: None,
        });
    }
    Ok(summaries)
}

fn parse_sessions(therapy_bytes: &[u8], include_timeseries: bool) -> Result<Vec<CpapSession>, String> {
    use chrono::Utc;
    let cursor = Cursor::new(therapy_bytes);
    let mut archive =
        zip::ZipArchive::new(cursor).map_err(|e| format!("therapy.pdat ZIP error: {e}"))?;

    let events_prefix = "mnt/flash/data/therapy/events/";
    let signals_prefix = "mnt/flash/data/therapy/signals/";

    let mut event_map: HashMap<String, Vec<u8>> = HashMap::new();
    let mut signal_map: HashMap<String, Vec<u8>> = HashMap::new();

    let names: Vec<String> = archive.file_names().map(|s| s.to_string()).collect();
    for name in &names {
        if name.starts_with(events_prefix) && name.ends_with(".xml") {
            let stem = std::path::Path::new(name)
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("");
            if let Some(num_str) = stem.strip_prefix("event_") {
                let session_num = num_str.trim_start_matches('0').to_string();
                let session_num = if session_num.is_empty() { "0".to_string() } else { session_num };
                let mut f = archive.by_name(name).map_err(|e| e.to_string())?;
                let mut bytes = Vec::new();
                f.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
                event_map.insert(session_num, bytes);
            }
        } else if name.starts_with(signals_prefix) && name.ends_with(".wmedf") {
            let stem = std::path::Path::new(name)
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("");
            if let Some(num_str) = stem.strip_prefix("signal_") {
                let session_num = num_str.trim_start_matches('0').to_string();
                let session_num = if session_num.is_empty() { "0".to_string() } else { session_num };
                let mut f = archive.by_name(name).map_err(|e| e.to_string())?;
                let mut bytes = Vec::new();
                f.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
                signal_map.insert(session_num, bytes);
            }
        }
    }

    let mut sessions: Vec<CpapSession> = Vec::new();
    let mut keys: Vec<String> = event_map.keys().cloned().collect();
    keys.sort_by(|a, b| {
        a.parse::<u64>()
            .unwrap_or(0)
            .cmp(&b.parse::<u64>().unwrap_or(0))
    });

    for session_num in &keys {
        let event_bytes = &event_map[session_num];
        let events = parse_event_xml(event_bytes).unwrap_or_default();

        let signal_bytes = signal_map.get(session_num);
        let (start_time, end_time, duration_minutes, sample_rate, timeseries) =
            if let Some(sig) = signal_bytes {
                match parse_wmedf_session(sig, include_timeseries) {
                    Ok((st, et, dur, sr, ts)) => (st, et, dur, sr, ts),
                    Err(_) => {
                        let now = Utc::now();
                        (now, now, 0.0, 0.0, None)
                    }
                }
            } else {
                let now = Utc::now();
                (now, now, 0.0, 0.0, None)
            };

        sessions.push(CpapSession {
            start_time,
            end_time,
            duration_minutes,
            file_type: format!("PrismaLine/{}", session_num),
            sample_rate,
            events,
            timeseries,
        });
    }

    sessions.sort_by_key(|s| s.start_time);
    Ok(sessions)
}

fn parse_event_xml(xml_bytes: &[u8]) -> Result<Vec<CpapEvent>, String> {
    let xml = std::str::from_utf8(xml_bytes).map_err(|e| format!("UTF-8 error: {e}"))?;
    let mut events = Vec::new();

    for line in xml.lines() {
        let line = line.trim();
        if !line.starts_with("<RespEvent ") {
            continue;
        }
        let eid: u32 = match attr_val(line, "RespEventID").and_then(|v| v.parse().ok()) {
            Some(v) => v,
            None => continue,
        };
        let event_type = match eid {
            101 => "ObstructiveApnea",
            102 => "CentralApnea",
            103 | 105 | 106 => "ClearAirwayApnea",
            111 | 112 => "Hypopnea",
            _ => continue,
        };
        let end_time_tenths: i64 = attr_val(line, "EndTime")
            .and_then(|v| v.parse().ok())
            .unwrap_or(0);
        let duration_tenths: i64 = attr_val(line, "Duration")
            .and_then(|v| v.parse().ok())
            .unwrap_or(0);
        let onset_sec = (end_time_tenths - duration_tenths) as f64 / 10.0;
        let duration_sec = duration_tenths as f64 / 10.0;

        events.push(CpapEvent {
            timestamp_sec: onset_sec,
            event_type: event_type.to_string(),
            duration_sec: Some(duration_sec),
            data: HashMap::new(),
        });
    }
    Ok(events)
}

/// Parse a wmedf EDF file to get session timing and optionally waveform signals.
///
/// Returns `(start_time, end_time, duration_minutes, sample_rate, timeseries)`.
fn parse_wmedf_session(
    wmedf_bytes: &[u8],
    include_timeseries: bool,
) -> Result<(chrono::DateTime<chrono::Utc>, chrono::DateTime<chrono::Utc>, f64, f64, Option<TimeSeriesData>), String> {
    use chrono::{TimeZone, Utc};
    let edf = crate::parsers::edf::parse_edf(wmedf_bytes)?;

    let start_naive = edf.header.start_datetime;

    let signal_data_offset = edf.header.num_header_bytes as usize;
    let bytes_per_record: usize = edf.signals.iter().map(|s| s.sample_count as usize * 2).sum();
    let actual_records = if bytes_per_record > 0 {
        wmedf_bytes.len().saturating_sub(signal_data_offset) / bytes_per_record
    } else {
        0
    };
    let duration_secs = actual_records as f64 * edf.header.duration_seconds;
    let duration_minutes = duration_secs / 60.0;

    let start_utc = Utc.from_utc_datetime(&start_naive);
    let end_utc = start_utc + chrono::Duration::seconds(duration_secs as i64);

    let sample_rate = edf
        .signals
        .iter()
        .map(|s| {
            if edf.header.duration_seconds > 0.0 {
                s.sample_count as f64 / edf.header.duration_seconds
            } else {
                0.0
            }
        })
        .fold(0.0_f64, f64::max);

    let timeseries = if include_timeseries {
        Some(decode_wmedf_signals_from_edf(&edf, duration_secs, sample_rate)?)
    } else {
        None
    };

    Ok((start_utc, end_utc, duration_minutes, sample_rate, timeseries))
}

fn decode_wmedf_signals_from_edf(
    edf: &crate::parsers::edf::EdfFile,
    duration_secs: f64,
    _session_sample_rate: f64,
) -> Result<TimeSeriesData, String> {
    // Physical value conversion using pre-computed gain/offset from EDF header.
    let to_phys = |sig: &crate::parsers::edf::EdfSignal| -> Vec<f64> {
        sig.samples.iter().map(|&v| crate::parsers::edf::phys(v, sig)).collect()
    };

    let find = |label: &str| edf.signals.iter().find(|s| s.label.trim() == label);

    // High-rate track: use RespFlow rate for timestamps.
    let flow_sig = find("RespFlow");
    let pressure_sig = find("Pressure");

    let flow_rate: Vec<f64> = flow_sig.map(|s| to_phys(s)).unwrap_or_default();
    let pressure: Vec<f64> = pressure_sig.map(|s| to_phys(s)).unwrap_or_default();

    let n_high = flow_rate.len();
    let flow_rate_hz = flow_sig
        .map(|s| {
            if edf.header.duration_seconds > 0.0 {
                s.sample_count as f64 / edf.header.duration_seconds
            } else {
                0.0
            }
        })
        .unwrap_or(0.0);
    let timestamps: Vec<f64> = (0..n_high)
        .map(|i| i as f64 / flow_rate_hz.max(1.0))
        .collect();

    // Low-rate track: use EPAPsoll as anchor for sample count.
    let epap_sig = find("EPAPsoll");
    let n_low = epap_sig.map(|s| s.samples.len()).unwrap_or_else(|| {
        // Fall back to any 1-Hz signal.
        edf.signals
            .iter()
            .filter(|s| {
                edf.header.duration_seconds > 0.0
                    && (s.sample_count as f64 / edf.header.duration_seconds - 1.0).abs() < 0.1
            })
            .map(|s| s.samples.len())
            .next()
            .unwrap_or(0)
    });

    let low_rate_hz = if n_low > 0 && duration_secs > 0.0 {
        n_low as f64 / duration_secs
    } else {
        1.0
    };
    let timestamps_low: Vec<f64> = (0..n_low)
        .map(|i| i as f64 / low_rate_hz)
        .collect();

    let extract = |label: &str| -> Vec<f64> {
        find(label).map(|s| to_phys(s)).unwrap_or_default()
    };

    Ok(TimeSeriesData {
        timestamps,
        flow_rate,
        pressure,
        timestamps_low,
        mask_pressure: extract("EPAPsoll"),
        leak: extract("TotalLeakage"),
        tidal_volume: extract("BreathVolume"),
        minute_ventilation: extract("MV"),
        respiratory_rate: extract("BreathFrequency"),
        snore: Vec::new(),
        flow_limitation: Vec::new(),
        spo2: extract("SpO2"),
        pulse: extract("HeartFrequency"),
    })
}

/// Extract attribute `name="value"` from a tag fragment string.
fn attr_val(tag: &str, name: &str) -> Option<String> {
    let search = format!("{name}=\"");
    let start = tag.find(&search)? + search.len();
    let rest = &tag[start..];
    let end = rest.find('"')?;
    Some(rest[..end].to_string())
}

/// Extract the `value` attribute of the first XML element with tag `tag_name`.
fn xml_attr_value(xml: &str, tag_name: &str) -> Option<String> {
    let search = format!("<{tag_name} ");
    let start = xml.find(&search)?;
    let fragment = &xml[start..];
    let val_start = fragment.find("value=\"")? + 7;
    let rest = &fragment[val_start..];
    let val_end = rest.find('"')?;
    Some(rest[..val_end].to_string())
}

/// Sum total seconds from a `t` attribute like `"63000-3600,70000-1800"`.
fn parse_t_intervals_total_sec(t_str: &str) -> u64 {
    t_str
        .split(',')
        .filter_map(|part| {
            let mut it = part.splitn(2, '-');
            let _start = it.next()?;
            it.next()?.parse::<u64>().ok()
        })
        .sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_device_xml(device_type: &str, serial: &str) -> Vec<u8> {
        format!(
            r#"<?xml version="1.0"?>
<device>
  <DeviceType value="{device_type}"/>
  <DeviceSerialNumber value="{serial}"/>
  <FWVersion value="3.2.1"/>
  <FWBuild value="12345"/>
</device>"#
        )
        .into_bytes()
    }

    fn build_zip(entry_name: &str, content: &[u8]) -> Vec<u8> {
        use std::io::Write;
        let mut buf = Cursor::new(Vec::new());
        {
            let mut zip = zip::ZipWriter::new(&mut buf);
            let opts = zip::write::SimpleFileOptions::default();
            zip.start_file(entry_name, opts).unwrap();
            zip.write_all(content).unwrap();
            zip.finish().unwrap();
        }
        buf.into_inner()
    }

    #[test]
    fn test_read_device_info_eyra() {
        let xml = make_device_xml("27", "SN123456");
        let zip_bytes = build_zip("mnt/flash/conf/device.xml", &xml);
        let info = read_device_info(&zip_bytes).unwrap();
        assert_eq!(info.serial_number, "SN123456");
        assert_eq!(info.model, "Löwenstein Eyra");
        assert_eq!(info.series, "Löwenstein Medical");
    }

    #[test]
    fn test_parse_event_xml_counts_hypopneas() {
        let xml = br#"<?xml version="1.0"?>
<desc>
  <DeviceEvent DeviceEventID="0" Time="0" ParameterID="1001" NewValue="1"/>
  <RespEvent RespEventID="111" EndTime="1500" Duration="120" Pressure="0" Strength="5"/>
  <RespEvent RespEventID="111" EndTime="2800" Duration="100" Pressure="0" Strength="3"/>
  <RespEvent RespEventID="101" EndTime="3600" Duration="200" Pressure="0" Strength="0"/>
</desc>"#;

        let events = parse_event_xml(xml).unwrap();
        // 2 hypopneas (ID 111) + 1 obstructive apnea (ID 101)
        assert_eq!(events.len(), 3);
        let hypopneas: Vec<_> = events.iter().filter(|e| e.event_type == "Hypopnea").collect();
        let apneas: Vec<_> = events.iter().filter(|e| e.event_type == "ObstructiveApnea").collect();
        assert_eq!(hypopneas.len(), 2);
        assert_eq!(apneas.len(), 1);
        // onset_sec = (EndTime - Duration) / 10.0
        // First hypopnea: (1500 - 120) / 10 = 138.0
        assert!((hypopneas[0].timestamp_sec - 138.0).abs() < 0.01);
        assert!((hypopneas[0].duration_sec.unwrap() - 12.0).abs() < 0.01);
    }

    #[test]
    fn test_parse_sessions_returns_one_per_event_file() {
        use std::io::Write;

        let event_xml_349 = br#"<?xml version="1.0"?><desc>
<RespEvent RespEventID="111" EndTime="1500" Duration="120" Pressure="0" Strength="5"/>
</desc>"#;
        let event_xml_350 = br#"<?xml version="1.0"?><desc></desc>"#;

        let zip_bytes = {
            let mut buf = Cursor::new(Vec::new());
            let mut zip = zip::ZipWriter::new(&mut buf);
            let opts = zip::write::SimpleFileOptions::default();
            zip.start_file("mnt/flash/data/therapy/events/20260512/event_000349.xml", opts).unwrap();
            zip.write_all(event_xml_349).unwrap();
            zip.start_file("mnt/flash/data/therapy/events/20260512/event_000350.xml", opts).unwrap();
            zip.write_all(event_xml_350).unwrap();
            zip.finish().unwrap();
            buf.into_inner()
        };

        let sessions = parse_sessions(&zip_bytes, false).unwrap();
        assert_eq!(sessions.len(), 2, "one session per event file");
        let s349 = sessions.iter().find(|s| s.events.len() == 1).unwrap();
        assert_eq!(s349.events[0].event_type, "Hypopnea");
    }

    #[test]
    fn test_decode_wmedf_signals_extracts_flow_rate() {
        let wmedf = build_minimal_wmedf();
        let edf = crate::parsers::edf::parse_edf(&wmedf).unwrap();
        // duration_secs: 3 records × 1 second = 3.0
        let duration_secs = 3.0_f64;
        let sample_rate = 10.0_f64;
        let ts = decode_wmedf_signals_from_edf(&edf, duration_secs, sample_rate).unwrap();
        assert!(!ts.flow_rate.is_empty(), "flow_rate should be populated");
        assert!(!ts.pressure.is_empty(), "pressure should be populated");
        assert!(!ts.timestamps.is_empty(), "high-rate timestamps should be populated");
        // pressure is 5 Hz, flow_rate is 10 Hz — timestamps follow flow_rate rate
        assert_eq!(ts.flow_rate.len(), 30, "3 records × 10 samples = 30 flow samples");
        assert_eq!(ts.pressure.len(), 15, "3 records × 5 samples = 15 pressure samples");
    }

    /// Build a minimal valid EDF buffer with 2 signals: Pressure (5 Hz) and RespFlow (10 Hz).
    /// 3 records of 1 second each, num_data_records = -1.
    fn build_minimal_wmedf() -> Vec<u8> {
        let num_signals: usize = 2;
        let header_bytes = 256 + num_signals * 256;
        let mut buf = vec![b' '; header_bytes];

        fn fill(buf: &mut Vec<u8>, offset: usize, s: &[u8], len: usize) {
            let n = s.len().min(len);
            buf[offset..offset + n].copy_from_slice(&s[..n]);
        }

        // Fixed header
        fill(&mut buf, 0, b"0", 8);
        fill(&mut buf, 8, b"Patient", 80);
        fill(&mut buf, 88, b"Recording", 80);
        buf[168..184].copy_from_slice(b"17.05.2621.23.17");
        fill(&mut buf, 184, format!("{header_bytes}").as_bytes(), 8);
        fill(&mut buf, 236, b"-1", 8); // num_records = -1
        fill(&mut buf, 244, b"1", 8);  // 1 second per record
        fill(&mut buf, 252, format!("{num_signals}").as_bytes(), 4);

        // Signal descriptors (interleaved by field):
        // Labels (16 bytes × num_signals)
        fill(&mut buf, 256 + 0*16, b"Pressure        ", 16);
        fill(&mut buf, 256 + 1*16, b"RespFlow        ", 16);
        // Transducers (80 bytes × num_signals) — leave as spaces
        // Phys dim (8 bytes × num_signals)
        let pd_off = 256 + num_signals*16 + num_signals*80;
        fill(&mut buf, pd_off + 0*8, b"hPa     ", 8);
        fill(&mut buf, pd_off + 1*8, b"l/min   ", 8);
        // Phys min (8 bytes × num_signals)
        let pmin_off = pd_off + num_signals*8;
        fill(&mut buf, pmin_off + 0*8, b"-32.768 ", 8);
        fill(&mut buf, pmin_off + 1*8, b"-500    ", 8);
        // Phys max (8 bytes × num_signals)
        let pmax_off = pmin_off + num_signals*8;
        fill(&mut buf, pmax_off + 0*8, b"32.767  ", 8);
        fill(&mut buf, pmax_off + 1*8, b"500     ", 8);
        // Dig min (8 bytes × num_signals)
        let dmin_off = pmax_off + num_signals*8;
        fill(&mut buf, dmin_off + 0*8, b"-32768  ", 8);
        fill(&mut buf, dmin_off + 1*8, b"-32768  ", 8);
        // Dig max (8 bytes × num_signals)
        let dmax_off = dmin_off + num_signals*8;
        fill(&mut buf, dmax_off + 0*8, b"32767   ", 8);
        fill(&mut buf, dmax_off + 1*8, b"32767   ", 8);
        // Prefiltering (80 bytes × num_signals) — leave as spaces
        // Samples per record (8 bytes × num_signals)
        let spr_off = dmax_off + num_signals*8 + num_signals*80;
        fill(&mut buf, spr_off + 0*8, b"5       ", 8); // Pressure: 5 Hz
        fill(&mut buf, spr_off + 1*8, b"10      ", 8); // RespFlow: 10 Hz
        // Reserved (32 bytes × num_signals) — leave as spaces

        // 3 records of data: 5 Pressure samples + 10 RespFlow samples
        for _ in 0..3 {
            for i in 0i16..5i16 {
                buf.extend_from_slice(&(i * 100).to_le_bytes()); // Pressure
            }
            for i in 0i16..10i16 {
                buf.extend_from_slice(&(i * 50).to_le_bytes()); // RespFlow
            }
        }
        buf
    }

    #[test]
    fn test_parse_daily_summaries_single_day() {
        let stats_xml = br#"<?xml version="1.0"?>
<therapy>
  <day d="2026-05-17">
    <rec m="2" t="63000-3600">
      <s i="309" v="550"/>
    </rec>
  </day>
</therapy>"#;

        let zip_bytes = build_zip("mnt/flash/data/statistics/statistics_year.bin", stats_xml);
        let summaries = parse_daily_summaries(&zip_bytes).unwrap();
        assert_eq!(summaries.len(), 1);
        let s = &summaries[0];
        assert_eq!(s.date, "2026-05-17");
        assert!((s.usage_hours - 1.0).abs() < 0.01, "expected ~1 hour usage");
        assert_eq!(s.pressure_mode, "APAP");
        assert!((s.pressure_50 - 5.5).abs() < 0.01, "set pressure should be 5.5 hPa");
    }
}
