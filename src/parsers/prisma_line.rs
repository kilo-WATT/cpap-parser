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

    for line in xml.lines() {
        let line = line.trim();
        if line.starts_with("<day ") {
            current_date = attr_val(line, "d");
        } else if line.starts_with("</day>") {
            current_date = None;
        } else if let Some(ref date) = current_date.clone() {
            if line.starts_with("<rec ") {
                let m: u32 = attr_val(line, "m")
                    .and_then(|v| v.parse().ok())
                    .unwrap_or(0);
                if !mode_labels.contains_key(&m) {
                    current_mode = 0;
                    current_usage_sec = 0;
                    continue;
                }
                current_mode = m;
                current_usage_sec = attr_val(line, "t")
                    .map(|t_str| parse_t_intervals_total_sec(&t_str))
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
            } else if line.starts_with("<s ") && current_mode != 0 {
                let i = attr_val(line, "i").unwrap_or_default();
                let v = attr_val(line, "v").unwrap_or_default();
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

fn parse_sessions(therapy_bytes: &[u8], _include_timeseries: bool) -> Result<Vec<CpapSession>, String> {
    // Implemented in Task 4
    Ok(Vec::new())
}

fn parse_event_xml(xml_bytes: &[u8]) -> Result<Vec<CpapEvent>, String> {
    todo!()
}

fn decode_wmedf_signals(wmedf_bytes: &[u8]) -> Result<TimeSeriesData, String> {
    todo!()
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
