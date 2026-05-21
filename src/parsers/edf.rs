/// EDF / EDF+ file format parser.
///
/// Ported from OSCAR's `edfparser.cpp` / `edfparser.h`.
///
/// European Data Format is a standard binary format for
/// multichannel biological signals.  This module provides
/// safe, no-unsafe Rust parsing of EDF and EDF+ files.

use std::path::Path;

/// Fixed-size raw EDF header (256 bytes).
#[derive(Debug, Clone)]
#[repr(C)]
pub struct EdfHeaderRaw {
    pub version: [u8; 8],
    pub patient_id: [u8; 80],
    pub recording_id: [u8; 80],
    pub datetime: [u8; 16],
    pub num_header_bytes: [u8; 8],
    pub reserved: [u8; 44],
    pub num_data_records: [u8; 8],
    pub dur_data_records: [u8; 8],
    pub num_signals: [u8; 4],
}

impl EdfHeaderRaw {
    pub const SIZE: usize = 256;

    pub fn from_slice(data: &[u8]) -> Result<Self, String> {
        if data.len() < Self::SIZE {
            return Err(format!("EDF header too short: {} bytes", data.len()));
        }
        fn arr8(d: &[u8], offset: usize) -> [u8; 8] {
            let mut a = [0u8; 8];
            a.copy_from_slice(&d[offset..offset + 8]);
            a
        }
        fn arr4(d: &[u8], offset: usize) -> [u8; 4] {
            let mut a = [0u8; 4];
            a.copy_from_slice(&d[offset..offset + 4]);
            a
        }
        fn arr80(d: &[u8], offset: usize) -> [u8; 80] {
            let mut a = [0u8; 80];
            a.copy_from_slice(&d[offset..offset + 80]);
            a
        }
        fn arr44(d: &[u8], offset: usize) -> [u8; 44] {
            let mut a = [0u8; 44];
            a.copy_from_slice(&d[offset..offset + 44]);
            a
        }
        fn arr16(d: &[u8], offset: usize) -> [u8; 16] {
            let mut a = [0u8; 16];
            a.copy_from_slice(&d[offset..offset + 16]);
            a
        }

        Ok(EdfHeaderRaw {
            version: arr8(data, 0),
            patient_id: arr80(data, 8),
            recording_id: arr80(data, 88),
            datetime: arr16(data, 168),
            num_header_bytes: arr8(data, 184),
            reserved: arr44(data, 192),
            num_data_records: arr8(data, 236),
            dur_data_records: arr8(data, 244),
            num_signals: arr4(data, 252),
        })
    }

    fn ascii_trimmed(&self, field: &[u8]) -> String {
        String::from_utf8_lossy(field)
            .trim_end_matches(|c: char| c.is_ascii_control() || c == ' ')
            .to_string()
    }
}

/// Parsed EDF header with typed fields.
#[derive(Debug, Clone)]
pub struct EdfHeader {
    pub version: i64,
    pub patient_ident: String,
    pub recording_ident: String,
    pub start_datetime: chrono::NaiveDateTime,
    pub num_header_bytes: i64,
    pub reserved44: String,
    pub num_data_records: i64,
    pub duration_seconds: f64,
    pub num_signals: i64,
}

/// Per-signal metadata and data.
#[derive(Debug, Clone)]
pub struct EdfSignal {
    pub label: String,
    pub transducer_type: String,
    pub physical_dimension: String,
    pub physical_minimum: f64,
    pub physical_maximum: f64,
    pub digital_minimum: f64,
    pub digital_maximum: f64,
    pub gain: f64,
    pub offset: f64,
    pub prefiltering: String,
    pub sample_count: i64,
    pub reserved: String,
    pub samples: Vec<i16>,
}

/// An EDF+ annotation.
#[derive(Debug, Clone)]
pub struct Annotation {
    pub offset_seconds: f64,
    pub duration_seconds: Option<f64>,
    pub text: String,
}

/// Parsed EDF+ file.
#[derive(Debug, Clone)]
pub struct EdfFile {
    pub header: EdfHeader,
    pub signals: Vec<EdfSignal>,
    pub annotations: Vec<Vec<Annotation>>,
}

const ANNO_SEP: u8 = 20;
const ANNO_DUR_MARK: u8 = 21;
const ANNO_END: u8 = 0;
const EDF_ANNOTATIONS_LABEL: &str = "Annotations";

/// Parse an in-memory EDF/EDF+ byte buffer.
pub fn parse_edf(data: &[u8]) -> Result<EdfFile, String> {
    if data.len() < EdfHeaderRaw::SIZE {
        return Err("File too short for EDF header".to_string());
    }

    let raw = EdfHeaderRaw::from_slice(data)?;
    let header = parse_header(&raw)?;

    let signal_desc_size: usize = 256;
    let signal_descs_offset = EdfHeaderRaw::SIZE;
    let signal_data_offset = signal_descs_offset + (header.num_signals as usize) * signal_desc_size;

    if data.len() < signal_data_offset {
        return Err("File too short for signal descriptors".to_string());
    }

    let descs = &data[signal_descs_offset..signal_data_offset];

    let mut signals: Vec<EdfSignal> = Vec::with_capacity(header.num_signals as usize);
    let mut pos = 0usize;

    // Labels
    for _ in 0..header.num_signals {
        let label = read_ascii_field(descs, &mut pos, 16);
        signals.push(EdfSignal {
            label,
            transducer_type: String::new(),
            physical_dimension: String::new(),
            physical_minimum: 0.0,
            physical_maximum: 0.0,
            digital_minimum: 0.0,
            digital_maximum: 0.0,
            gain: 1.0,
            offset: 0.0,
            prefiltering: String::new(),
            sample_count: 0,
            reserved: String::new(),
            samples: Vec::new(),
        });
    }

    // Transducer type
    for sig in &mut signals {
        sig.transducer_type = read_ascii_field(descs, &mut pos, 80);
    }

    // Physical dimension
    for sig in &mut signals {
        sig.physical_dimension = read_ascii_field(descs, &mut pos, 8);
    }

    // Physical min
    for sig in &mut signals {
        sig.physical_minimum = parse_double_field(descs, &mut pos, 8);
    }

    // Physical max
    for sig in &mut signals {
        sig.physical_maximum = parse_double_field(descs, &mut pos, 8);
    }

    // Digital min
    for sig in &mut signals {
        sig.digital_minimum = parse_double_field(descs, &mut pos, 8);
    }

    // Digital max
    for sig in &mut signals {
        sig.digital_maximum = parse_double_field(descs, &mut pos, 8);
        sig.gain = if sig.digital_maximum != sig.digital_minimum {
            (sig.physical_maximum - sig.physical_minimum)
                / (sig.digital_maximum - sig.digital_minimum)
        } else {
            1.0
        };
    }

    // Prefiltering
    for sig in &mut signals {
        sig.prefiltering = read_ascii_field(descs, &mut pos, 80);
    }

    // Sample count
    for sig in &mut signals {
        sig.sample_count = parse_long_field(descs, &mut pos, 8);
    }

    // Reserved
    for sig in &mut signals {
        sig.reserved = read_ascii_field(descs, &mut pos, 32);
    }

    if pos > descs.len() {
        return Err("Signal descriptors truncated".to_string());
    }

    // EDF+ files written by live devices (e.g. Löwenstein .wmedf) set
    // num_data_records = -1.  Derive the actual count from the file size.
    let actual_records: usize = if header.num_data_records == -1 {
        let bytes_per_record: usize = signals
            .iter()
            .map(|s| s.sample_count as usize * 2)
            .sum();
        if bytes_per_record > 0 {
            // Integer division floors — partial trailing records are silently ignored.
            // Intentional: live-recording files (.wmedf) may end mid-record.
            data.len().saturating_sub(header.num_header_bytes as usize) / bytes_per_record
        } else {
            0
        }
    } else if header.num_data_records > 0 {
        header.num_data_records as usize
    } else {
        0
    };

    let mut annotations: Vec<Vec<Annotation>> = Vec::new();

    if actual_records > 0 {
        let mut data_pos = signal_data_offset;

        for rec_no in 0..actual_records {
            for sig_idx in 0..signals.len() {
                let sig = &signals[sig_idx];
                let bytes_needed = (sig.sample_count as usize) * 2;

                if data_pos + bytes_needed > data.len() {
                    return Err(format!(
                        "Truncated EDF data at record {}, signal {}",
                        rec_no, sig.label
                    ));
                }

                if sig.label == EDF_ANNOTATIONS_LABEL {
                    let chunk = &data[data_pos..data_pos + bytes_needed];
                    let annos = parse_annotations(chunk);
                    annotations.push(annos);
                } else {
                    let sig = &mut signals[sig_idx];
                    sig.samples.reserve(sig.sample_count as usize);
                    for j in 0..sig.sample_count as usize {
                        let lo = data[data_pos + j * 2] as u16;
                        let hi = data[data_pos + j * 2 + 1] as u16;
                        let val = (hi << 8) | lo;
                        sig.samples.push(val as i16);
                    }
                }

                data_pos += bytes_needed;
            }
        }
    }

    Ok(EdfFile {
        header,
        signals,
        annotations,
    })
}

/// Parse just the header from an EDF file on disk (fast path).
pub fn get_header(path: &Path) -> Result<EdfHeader, String> {
    let data = std::fs::read(path).map_err(|e| format!("Cannot read {}: {e}", path.display()))?;
    if data.len() < EdfHeaderRaw::SIZE {
        return Err(format!("File too short for EDF header: {}", path.display()));
    }
    let raw = EdfHeaderRaw::from_slice(&data[..EdfHeaderRaw::SIZE])?;
    parse_header(&raw)
}

/// Convenience: open, read, and parse an EDF file from disk.
pub fn open_and_parse(path: &Path) -> Result<EdfFile, String> {
    let data = std::fs::read(path).map_err(|e| format!("Cannot read {}: {e}", path.display()))?;
    parse_edf(&data)
}

/// Scale a raw digital sample to physical units.
pub fn phys(digital: i16, sig: &EdfSignal) -> f64 {
    digital as f64 * sig.gain + sig.offset
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

fn parse_header(raw: &EdfHeaderRaw) -> Result<EdfHeader, String> {
    let version = parse_long_field(&raw.version, &mut 0, 8);
    if version != 0 && version != 65536 {
        return Err(format!("Bad EDF version field: {}", version));
    }

    let patient_ident = raw.ascii_trimmed(&raw.patient_id);
    let recording_ident = raw.ascii_trimmed(&raw.recording_id);

    let dt_str = raw.ascii_trimmed(&raw.datetime);
    let start_datetime = parse_edf_datetime(&dt_str)?;

    let num_header_bytes = parse_long_field(&raw.num_header_bytes, &mut 0, 8);
    let reserved44 = raw.ascii_trimmed(&raw.reserved);
    let num_data_records = parse_long_field(&raw.num_data_records, &mut 0, 8);
    let duration_seconds = parse_double_field(&raw.dur_data_records, &mut 0, 8);
    let num_signals = parse_long_field(&raw.num_signals, &mut 0, 4);

    if num_signals < 1 || num_signals > 256 {
        return Err(format!("Invalid number of EDF signals: {}", num_signals));
    }

    Ok(EdfHeader {
        version,
        patient_ident,
        recording_ident,
        start_datetime,
        num_header_bytes,
        reserved44,
        num_data_records,
        duration_seconds,
        num_signals,
    })
}

fn parse_edf_datetime(s: &str) -> Result<chrono::NaiveDateTime, String> {
    let s = s.trim();
    if s.len() < 16 {
        return Err(format!("Short EDF datetime field: '{s}'"));
    }
    let date_str = &s[..8];
    let time_str = &s[8..16];

    let day: u32 = date_str[..2].parse().map_err(|_| format!("Bad day: {}", &date_str[..2]))?;
    let month: u32 = date_str[3..5].parse().map_err(|_| format!("Bad month: {}", &date_str[3..5]))?;
    let year_short: u32 = date_str[6..8].parse().map_err(|_| format!("Bad year: {}", &date_str[6..8]))?;
    let year = 2000 + year_short;

    let hour: u32 = time_str[..2].parse().map_err(|_| format!("Bad hour: {}", &time_str[..2]))?;
    let minute: u32 = time_str[3..5].parse().map_err(|_| format!("Bad minute: {}", &time_str[3..5]))?;
    let second: u32 = time_str[6..8].parse().map_err(|_| format!("Bad second: {}", &time_str[6..8]))?;

    chrono::NaiveDate::from_ymd_opt(year as i32, month, day)
        .and_then(|d| d.and_hms_opt(hour, minute, second))
        .ok_or_else(|| format!("Invalid EDF datetime: '{s}'"))
}

fn read_ascii_field(data: &[u8], pos: &mut usize, len: usize) -> String {
    if *pos + len > data.len() {
        *pos = data.len();
        return String::new();
    }
    let s = String::from_utf8_lossy(&data[*pos..*pos + len])
        .trim_end_matches(|c: char| c.is_ascii_control() || c == ' ')
        .to_string();
    *pos += len;
    s
}

fn parse_double_field(data: &[u8], pos: &mut usize, len: usize) -> f64 {
    if *pos + len > data.len() {
        *pos = data.len();
        return 0.0;
    }
    let s = std::str::from_utf8(&data[*pos..*pos + len])
        .unwrap_or("")
        .trim();
    *pos += len;
    // EDF can use "," as decimal separator
    let s = s.replace(',', ".");
    s.parse().unwrap_or(0.0)
}

fn parse_long_field(data: &[u8], pos: &mut usize, len: usize) -> i64 {
    if *pos + len > data.len() {
        *pos = data.len();
        return 0;
    }
    let s = std::str::from_utf8(&data[*pos..*pos + len])
        .unwrap_or("")
        .trim();
    *pos += len;
    s.parse().unwrap_or(0)
}

fn parse_annotations(data: &[u8]) -> Vec<Annotation> {
    let mut result = Vec::new();
    let len = data.len();
    let mut pos = 0usize;

    while pos < len {
        let c = data[pos];
        if c != b'+' && c != b'-' {
            break;
        }
        let positive = c == b'+';
        pos += 1;

        let mut text_buf = String::new();
        while pos < len && data[pos] != ANNO_SEP && data[pos] != ANNO_DUR_MARK {
            text_buf.push(data[pos] as char);
            pos += 1;
        }

        let offset: f64 = match text_buf.parse() {
            Ok(v) => if positive { v } else { -v },
            Err(_) => break,
        };

        let mut duration: Option<f64> = None;

        if pos < len && data[pos] == ANNO_DUR_MARK {
            pos += 1;
            let mut dur_buf = String::new();
            while pos < len && data[pos] != ANNO_SEP {
                dur_buf.push(data[pos] as char);
                pos += 1;
            }
            duration = dur_buf.parse().ok();
        }

        while pos < len && data[pos] == ANNO_SEP {
            pos += 1;
            if pos >= len {
                break;
            }
            if data[pos] == ANNO_END {
                break;
            }
            if data[pos] == ANNO_SEP {
                pos += 1;
                break;
            }

            let text_start = pos;
            while pos < len && data[pos] != ANNO_SEP {
                pos += 1;
            }
            let anno_text = String::from_utf8_lossy(&data[text_start..pos]).to_string();
            result.push(Annotation {
                offset_seconds: offset,
                duration_seconds: duration,
                text: anno_text,
            });
        }

        while pos < len && data[pos] == ANNO_END {
            pos += 1;
        }
    }

    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_minimal_edf_header() {
        let mut buf = vec![b' ' as u8; 256]; // EDF fields are space-padded
        fn fill(buf: &mut Vec<u8>, offset: usize, s: &[u8], len: usize) {
            assert!(offset + len <= buf.len());
            let n = s.len().min(len);
            buf[offset..offset + n].copy_from_slice(&s[..n]);
        }
        // version "0       "
        fill(&mut buf, 0, b"0", 8);
        // patient
        fill(&mut buf, 8, b"Patient X", 80);
        // recording
        fill(&mut buf, 88, b"SN12345", 80);
        // datetime "01.01.20 12.00.00" (dd.MM.yy HH.mm.ss) - EDF uses dots
        buf[168..184].copy_from_slice(b"01.01.2012.00.00");
        // num_header_bytes
        fill(&mut buf, 184, b"256", 8);
        // num_data_records
        fill(&mut buf, 236, b"1", 8);
        // dur_data_records
        fill(&mut buf, 244, b"30", 8);
        // num_signals
        fill(&mut buf, 252, b"1", 4);

        // One signal descriptor (256 bytes)
        buf.extend_from_slice(b"TestSignal      "); // label 16
        buf.extend_from_slice(&[b' ' as u8; 80]); // transducer 80
        buf.extend_from_slice(b"mV      "); // phys dimension 8
        buf.extend_from_slice(b"-10     "); // phys min 8
        buf.extend_from_slice(b"10      "); // phys max 8
        buf.extend_from_slice(b"-32768  "); // dig min 8
        buf.extend_from_slice(b"32767   "); // dig max 8
        buf.extend_from_slice(&[b' ' as u8; 80]); // prefiltering 80
        buf.extend_from_slice(b"100     "); // sample count 8
        buf.extend_from_slice(&[b' ' as u8; 32]); // reserved 32

        // Data: 100 samples, little-endian i16
        for i in 0i16..100i16 {
            buf.extend_from_slice(&i.to_le_bytes());
        }

        let edf = parse_edf(&buf).unwrap();
        assert_eq!(edf.header.num_signals, 1);
        assert_eq!(edf.signals[0].label, "TestSignal");
        assert_eq!(edf.signals[0].sample_count, 100);
        assert_eq!(edf.signals[0].samples.len(), 100);
        assert_eq!(edf.signals[0].samples[0], 0);
        assert_eq!(edf.signals[0].samples[99], 99);
    }

    #[test]
    fn test_invalid_header() {
        let result = parse_edf(&[0u8; 10]);
        assert!(result.is_err());
    }

    #[test]
    fn test_num_records_minus_one_computes_from_file_size() {
        // Build a minimal EDF with num_data_records = -1.
        let mut buf = vec![b' '; 256];
        fn fill(buf: &mut Vec<u8>, offset: usize, s: &[u8], len: usize) {
            let n = s.len().min(len);
            buf[offset..offset + n].copy_from_slice(&s[..n]);
        }
        fill(&mut buf, 0, b"0", 8);
        fill(&mut buf, 8, b"X", 80);
        fill(&mut buf, 88, b"X", 80);
        buf[168..184].copy_from_slice(b"01.01.2012.00.00");
        fill(&mut buf, 184, b"512", 8); // header: 256 (fixed) + 1*256 (signal)
        fill(&mut buf, 236, b"-1", 8); // num_data_records = -1
        fill(&mut buf, 244, b"1", 8);  // 1 second per record
        fill(&mut buf, 252, b"1", 4);  // 1 signal

        // One signal descriptor (256 bytes total):
        buf.extend_from_slice(b"TestSignal      "); // label 16
        buf.extend_from_slice(&[b' '; 80]);          // transducer 80
        buf.extend_from_slice(b"mV      ");          // phys dim 8
        buf.extend_from_slice(b"-100    ");          // phys min 8
        buf.extend_from_slice(b"100     ");          // phys max 8
        buf.extend_from_slice(b"-32768  ");          // dig min 8
        buf.extend_from_slice(b"32767   ");          // dig max 8
        buf.extend_from_slice(&[b' '; 80]);          // prefiltering 80
        buf.extend_from_slice(b"5       ");          // 5 samples/record 8
        buf.extend_from_slice(&[b' '; 32]);          // reserved 32

        // 3 records × 5 samples × 2 bytes = 30 data bytes
        for i in 0i16..15i16 {
            buf.extend_from_slice(&i.to_le_bytes());
        }

        let edf = parse_edf(&buf).unwrap();
        assert_eq!(edf.signals[0].samples.len(), 15); // 3 records × 5 samples
        assert_eq!(edf.signals[0].samples[0], 0);
        assert_eq!(edf.signals[0].samples[14], 14);
    }

    #[test]
    fn test_num_records_minus_one_ignores_partial_trailing_record() {
        // Same synthetic EDF as above (1 signal, 5 samples/record, 3 complete records),
        // but with 2 extra stray bytes appended to simulate a file truncated mid-record.
        // The parser should successfully return 15 samples and ignore the partial record.
        let mut buf = vec![b' '; 256];
        fn fill(buf: &mut Vec<u8>, offset: usize, s: &[u8], len: usize) {
            let n = s.len().min(len);
            buf[offset..offset + n].copy_from_slice(&s[..n]);
        }
        fill(&mut buf, 0, b"0", 8);
        fill(&mut buf, 8, b"X", 80);
        fill(&mut buf, 88, b"X", 80);
        buf[168..184].copy_from_slice(b"01.01.2012.00.00");
        fill(&mut buf, 184, b"512", 8); // header: 256 (fixed) + 1*256 (signal)
        fill(&mut buf, 236, b"-1", 8);  // num_data_records = -1
        fill(&mut buf, 244, b"1", 8);   // 1 second per record
        fill(&mut buf, 252, b"1", 4);   // 1 signal

        // One signal descriptor (256 bytes total):
        buf.extend_from_slice(b"TestSignal      "); // label 16
        buf.extend_from_slice(&[b' '; 80]);          // transducer 80
        buf.extend_from_slice(b"mV      ");          // phys dim 8
        buf.extend_from_slice(b"-100    ");          // phys min 8
        buf.extend_from_slice(b"100     ");          // phys max 8
        buf.extend_from_slice(b"-32768  ");          // dig min 8
        buf.extend_from_slice(b"32767   ");          // dig max 8
        buf.extend_from_slice(&[b' '; 80]);          // prefiltering 80
        buf.extend_from_slice(b"5       ");          // 5 samples/record 8
        buf.extend_from_slice(&[b' '; 32]);          // reserved 32

        // 3 records × 5 samples × 2 bytes = 30 data bytes
        for i in 0i16..15i16 {
            buf.extend_from_slice(&i.to_le_bytes());
        }

        // Append 2 stray bytes — a partial (incomplete) fourth record.
        buf.extend_from_slice(&[0u8; 2]);

        let result = parse_edf(&buf);
        assert!(result.is_ok());
        let edf = result.unwrap();
        // Only 3 complete records should be read; the partial record is silently dropped.
        assert_eq!(edf.signals[0].samples.len(), 15);
    }

    #[test]
    fn test_annotation_parsing() {
        let mut data = Vec::new();
        // "+1.5" offset
        data.extend_from_slice(b"+1.5");
        data.push(ANNO_SEP);
        data.extend_from_slice(b"Sleep onset");
        data.push(ANNO_SEP);
        data.push(ANNO_END);
        // "+10.0\x152.0 Apnea"
        // Actually, let's test simpler
        let annos = parse_annotations(&data);
        assert_eq!(annos.len(), 1);
        assert!((annos[0].offset_seconds - 1.5).abs() < 1e-9);
        assert!(annos[0].duration_seconds.is_none());
        assert_eq!(annos[0].text, "Sleep onset");
    }
}
