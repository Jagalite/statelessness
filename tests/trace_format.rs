use stateless::model::{Check, Disposition, ModelMetadata};
use stateless::trace::{ReadLimits, RunConfig, Termination, Trace, TraceError, TraceStep};
use std::io::{self, Cursor, Read, Write};

fn fixture() -> Trace {
    Trace {
        metadata: ModelMetadata {
            name: "lease lifecycle λ".into(),
            model_version: 3,
            properties_version: 2,
            codec_version: 1,
            build: "test-build-content-id".into(),
        },
        config: RunConfig {
            strategy: "seeded".into(),
            seed: Some(u64::MAX),
            parameters: vec![
                ("leases".into(), "2".into()),
                ("logical_time".into(), "true".into()),
            ],
        },
        initial_state: vec![0, 1, 0, 255],
        initial_checks: vec![
            Check::passed("ownership"),
            Check::skipped("expensive", "policy"),
        ],
        steps: vec![
            TraceStep {
                input: vec![10, 0],
                disposition: Disposition::Accepted,
                outputs: vec![vec![], vec![0, 0, 3]],
                post_state: vec![1, 2],
                checks: vec![Check::passed("ownership")],
            },
            TraceStep {
                input: vec![11],
                disposition: Disposition::Ignored("stale generation".into()),
                outputs: vec![],
                post_state: vec![1, 2],
                checks: vec![],
            },
            TraceStep {
                input: vec![12],
                disposition: Disposition::Rejected("retired".into()),
                outputs: vec![vec![7]],
                post_state: vec![2, 2],
                checks: vec![Check::failed("release_once", "released twice")],
            },
        ],
        termination: Termination::PropertyFailed,
    }
}

fn encoded(trace: &Trace) -> Vec<u8> {
    let mut bytes = Vec::new();
    trace.write_to(&mut bytes).unwrap();
    bytes
}

fn decode(bytes: &[u8]) -> Result<Trace, TraceError> {
    Trace::read_from(Cursor::new(bytes), &ReadLimits::default())
}

#[test]
fn round_trip_preserves_all_evidence_and_is_deterministic() {
    let trace = fixture();
    let bytes = encoded(&trace);
    assert_eq!(encoded(&trace), bytes);
    assert_eq!(decode(&bytes).unwrap(), trace);
    assert_eq!(&bytes[..12], b"STLESS\x1a\n\x01\x00\x00\x00");
}

#[test]
fn version_one_golden_bytes_preserve_the_existing_wire_format() {
    // Independent version-one fixture with CRC values computed from the wire
    // specification. Buffer reuse and property-ID storage must not change it.
    let golden = "53544c4553531a0a01000000012f000000010000006d010000000100000001000000010000006201000000730000000000010000000001000000010000007000d00b9faa02260000000000000000000000010000000100010000000100000002010000000301000000010000007000e4c092b30309000000010000000000000000b742343d";
    let bytes: Vec<u8> = golden
        .as_bytes()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
        .collect();
    let trace = Trace {
        metadata: ModelMetadata {
            name: "m".into(),
            model_version: 1,
            properties_version: 1,
            codec_version: 1,
            build: "b".into(),
        },
        config: RunConfig {
            strategy: "s".into(),
            seed: None,
            parameters: vec![],
        },
        initial_state: vec![0],
        initial_checks: vec![Check::passed("p")],
        steps: vec![TraceStep {
            input: vec![1],
            disposition: Disposition::Accepted,
            outputs: vec![vec![2]],
            post_state: vec![3],
            checks: vec![Check::passed("p")],
        }],
        termination: Termination::Completed,
    };
    assert_eq!(encoded(&trace), bytes);
    assert_eq!(decode(&bytes).unwrap(), trace);
}

#[test]
fn empty_runs_and_all_termination_kinds_round_trip() {
    for termination in [
        Termination::Completed,
        Termination::PropertyFailed,
        Termination::StepLimit,
        Termination::Interrupted,
        Termination::ModelError("state adapter failed".into()),
    ] {
        let mut trace = fixture();
        trace.steps.clear();
        trace.initial_checks.clear();
        trace.config = RunConfig::default();
        trace.initial_state.clear();
        trace.termination = termination;
        assert_eq!(decode(&encoded(&trace)).unwrap(), trace);
    }
}

#[test]
fn every_truncated_prefix_is_rejected_including_missing_footer() {
    let bytes = encoded(&fixture());
    for length in 0..bytes.len() {
        assert!(
            decode(&bytes[..length]).is_err(),
            "accepted truncated prefix {length}"
        );
    }
}

#[test]
fn every_single_bit_corruption_is_rejected() {
    let bytes = encoded(&fixture());
    for offset in 0..bytes.len() {
        for bit in 0..8 {
            let mut altered = bytes.clone();
            altered[offset] ^= 1 << bit;
            assert!(
                decode(&altered).is_err(),
                "accepted corruption at byte {offset}, bit {bit}"
            );
        }
    }
}

#[test]
fn unsupported_versions_flags_and_trailing_artifacts_are_rejected() {
    let bytes = encoded(&fixture());
    let mut version = bytes.clone();
    version[8..10].copy_from_slice(&2u16.to_le_bytes());
    assert!(matches!(
        decode(&version),
        Err(TraceError::UnsupportedVersion(2))
    ));
    let mut flags = bytes.clone();
    flags[10] = 1;
    assert!(matches!(decode(&flags), Err(TraceError::InvalidData(_))));
    let mut trailing = bytes.clone();
    trailing.push(0);
    assert!(matches!(decode(&trailing), Err(TraceError::InvalidData(_))));
    let mut concatenated = bytes.clone();
    concatenated.extend_from_slice(&bytes);
    assert!(matches!(
        decode(&concatenated),
        Err(TraceError::InvalidData(_))
    ));
}

// Independent bitwise CRC implementation: semantic parser tests deliberately
// repair CRC after mutation, so they exercise validation beyond corruption checks.
fn repair_crc(bytes: &mut [u8], frame: usize) {
    let len = u32::from_le_bytes(bytes[frame + 1..frame + 5].try_into().unwrap()) as usize;
    let end = frame + 5 + len;
    let mut crc = u32::MAX;
    for byte in &bytes[frame..end] {
        crc ^= *byte as u32;
        for _ in 0..8 {
            crc = (crc >> 1) ^ (0xedb88320 & 0u32.wrapping_sub(crc & 1));
        }
    }
    bytes[end..end + 4].copy_from_slice(&(!crc).to_le_bytes());
}

fn frame_offsets(bytes: &[u8]) -> Vec<usize> {
    let mut result = Vec::new();
    let mut pos = 12;
    while pos < bytes.len() {
        result.push(pos);
        let n = u32::from_le_bytes(bytes[pos + 1..pos + 5].try_into().unwrap()) as usize;
        pos += n + 9;
    }
    result
}

#[test]
fn unknown_records_reordered_steps_and_wrong_footer_count_are_rejected() {
    let original = encoded(&fixture());
    let frames = frame_offsets(&original);
    let mut unknown = original.clone();
    unknown[frames[1]] = 77;
    repair_crc(&mut unknown, frames[1]);
    assert!(matches!(
        decode(&unknown),
        Err(TraceError::InvalidData("unknown or misplaced record"))
    ));
    let mut wrong_index = original.clone();
    wrong_index[frames[1] + 5..frames[1] + 13].copy_from_slice(&1u64.to_le_bytes());
    repair_crc(&mut wrong_index, frames[1]);
    assert!(matches!(
        decode(&wrong_index),
        Err(TraceError::InvalidData("transition index"))
    ));
    let mut wrong_count = original.clone();
    let footer = *frames.last().unwrap();
    wrong_count[footer + 5..footer + 13].copy_from_slice(&0u64.to_le_bytes());
    repair_crc(&mut wrong_count, footer);
    assert!(matches!(
        decode(&wrong_count),
        Err(TraceError::InvalidData("footer transition count"))
    ));
    let mut misplaced = original;
    misplaced[frames[0]] = 2;
    repair_crc(&mut misplaced, frames[0]);
    assert!(matches!(
        decode(&misplaced),
        Err(TraceError::InvalidData("first record must be run metadata"))
    ));
}

#[test]
fn invalid_utf8_and_impossible_nested_count_are_rejected_before_allocation() {
    let original = encoded(&fixture());
    let mut invalid_utf8 = original;
    invalid_utf8[12 + 5 + 4] = 255;
    repair_crc(&mut invalid_utf8, 12);
    assert!(matches!(
        decode(&invalid_utf8),
        Err(TraceError::InvalidData("invalid UTF-8"))
    ));

    let mut trace = fixture();
    trace.metadata.name.clear();
    trace.metadata.build.clear();
    trace.config = RunConfig {
        strategy: String::new(),
        seed: None,
        parameters: vec![],
    };
    let original = encoded(&trace);
    // Run payload: name length (4), versions (12), build length (4),
    // strategy length (4), seed tag (1), then parameter count.
    let count_offset = 12 + 5 + 4 + 12 + 4 + 4 + 1;
    for (count, expected_limit) in [(u32::MAX, true), (1000, false)] {
        let mut bytes = original.clone();
        bytes[count_offset..count_offset + 4].copy_from_slice(&count.to_le_bytes());
        repair_crc(&mut bytes, 12);
        let err = decode(&bytes).unwrap_err();
        if expected_limit {
            assert!(matches!(err, TraceError::LimitExceeded("parameters")));
        } else {
            assert!(matches!(
                err,
                TraceError::InvalidData("collection count exceeds frame")
            ));
        }
    }
}

#[test]
fn read_and_write_enforce_all_limits_and_validation_precedes_writes() {
    let trace = fixture();
    let bytes = encoded(&trace);
    let defaults = ReadLimits::default();
    let limits = [
        ReadLimits {
            max_total_bytes: bytes.len() as u64 - 1,
            ..defaults.clone()
        },
        ReadLimits {
            max_frame_bytes: 4,
            ..defaults.clone()
        },
        ReadLimits {
            max_blob_bytes: 1,
            ..defaults.clone()
        },
        ReadLimits {
            max_string_bytes: 1,
            ..defaults.clone()
        },
        ReadLimits {
            max_steps: 2,
            ..defaults.clone()
        },
        ReadLimits {
            max_checks_per_step: 1,
            ..defaults.clone()
        },
        ReadLimits {
            max_outputs_per_step: 1,
            ..defaults.clone()
        },
        ReadLimits {
            max_parameters: 1,
            ..defaults.clone()
        },
        ReadLimits {
            max_items: 8,
            ..defaults.clone()
        },
    ];
    for limits in limits {
        assert!(
            matches!(
                Trace::read_from(Cursor::new(&bytes), &limits),
                Err(TraceError::LimitExceeded(_))
            ),
            "{limits:?}"
        );
        let mut output = Vec::new();
        assert!(
            matches!(
                trace.write_with_limits(&mut output, &limits),
                Err(TraceError::LimitExceeded(_))
            ),
            "{limits:?}"
        );
        assert!(output.is_empty(), "validation wrote a partial artifact");
    }
    let exact_bytes = ReadLimits {
        max_total_bytes: bytes.len() as u64,
        ..defaults
    };
    assert_eq!(
        Trace::read_from(Cursor::new(&bytes), &exact_bytes).unwrap(),
        trace
    );
    trace.write_with_limits(io::sink(), &exact_bytes).unwrap();
}

#[test]
fn aggregate_item_limit_catches_many_small_records() {
    let mut trace = fixture();
    trace.initial_checks.clear();
    trace.config.parameters.clear();
    trace.steps = vec![
        TraceStep {
            input: vec![],
            disposition: Disposition::Accepted,
            outputs: vec![vec![]; 10],
            post_state: vec![],
            checks: vec![],
        };
        10
    ];
    let bytes = encoded(&trace);
    let limits = ReadLimits {
        max_items: 109,
        ..ReadLimits::default()
    };
    assert!(matches!(
        Trace::read_from(Cursor::new(&bytes), &limits),
        Err(TraceError::LimitExceeded("aggregate items"))
    ));
    let limits = ReadLimits {
        max_items: 110,
        ..limits
    };
    assert_eq!(
        Trace::read_from(Cursor::new(&bytes), &limits).unwrap(),
        trace
    );
}

struct FailsAfter {
    remaining: usize,
}
impl Write for FailsAfter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if self.remaining == 0 {
            return Err(io::Error::other("disk unavailable"));
        }
        let count = bytes.len().min(self.remaining);
        self.remaining -= count;
        Ok(count)
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

struct FailingReader;
impl Read for FailingReader {
    fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
        Err(io::Error::other("read unavailable"))
    }
}

#[test]
fn underlying_io_errors_are_preserved() {
    let trace = fixture();
    let bytes = encoded(&trace);
    for budget in [0, 8, 12, 30, bytes.len() - 1] {
        let result = trace.write_to(FailsAfter { remaining: budget });
        assert!(matches!(result, Err(TraceError::Io(e)) if e.to_string() == "disk unavailable"));
    }
    let result = Trace::read_from(FailingReader, &ReadLimits::default());
    assert!(matches!(result, Err(TraceError::Io(e)) if e.to_string() == "read unavailable"));
}

struct ByteReader<R> {
    source: R,
    interrupt: bool,
}
impl<R: Read> Read for ByteReader<R> {
    fn read(&mut self, bytes: &mut [u8]) -> io::Result<usize> {
        self.interrupt = !self.interrupt;
        if self.interrupt {
            return Err(io::ErrorKind::Interrupted.into());
        }
        let n = bytes.len().min(1);
        self.source.read(&mut bytes[..n])
    }
}

#[test]
fn short_and_interrupted_reads_are_supported() {
    let trace = fixture();
    let reader = ByteReader {
        source: Cursor::new(encoded(&trace)),
        interrupt: false,
    };
    assert_eq!(
        Trace::read_from(reader, &ReadLimits::default()).unwrap(),
        trace
    );
}

#[test]
fn hostile_frame_length_hits_limit_without_reading_payload() {
    let mut bytes = b"STLESS\x1a\n\x01\x00\x00\x00".to_vec();
    bytes.push(1);
    bytes.extend_from_slice(&u32::MAX.to_le_bytes());
    assert!(matches!(
        decode(&bytes),
        Err(TraceError::LimitExceeded("frame bytes"))
    ));
    let limits = ReadLimits {
        max_frame_bytes: u32::MAX as usize,
        ..ReadLimits::default()
    };
    assert!(matches!(
        Trace::read_from(Cursor::new(bytes), &limits),
        Err(TraceError::LimitExceeded("total bytes"))
    ));
}
