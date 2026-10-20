//! Concrete Rust authority witnesses; this is a finite subset, not the 90-row runner.
use stateless::value_codec::{DecodeLimits, Decoder, TraceDecode as _, TraceEncode as _};
use stateless::{EncodeBuffer, ModelError};
use statelessness_macros::{TraceDecode, TraceEncode};

#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
struct Record {
    byte: u8,
    ready: bool,
    count: u16,
}
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
struct Pair(u8, u16);
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
struct Unit;
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
struct Empty {}
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
struct Nested {
    marker: u8,
    pair: Pair,
}
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
struct Text(String);
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
struct Text8(#[trace(length = "u8")] String);
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
struct Text16(#[trace(length = "u16")] String);
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
struct Text32(#[trace(length = "u32")] String);
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
enum Event8 {
    #[trace(tag = 7)]
    Idle,
    #[trace(tag = 2)]
    Set(u16),
    #[trace(tag = 255)]
    Ready { ready: bool },
}
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
#[trace(tag_type = "u16")]
enum Event16 {
    #[trace(tag = 400)]
    Set(u16),
    #[trace(tag = 65535)]
    Max,
}
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
#[trace(tag_type = "u32")]
enum Event32 {
    #[trace(tag = 4294967295)]
    Max,
}
mod be {
    use super::*;
    pub fn encode(v: &u16, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
        out.extend_from_slice(&v.to_be_bytes())
    }
    pub fn decode(r: &mut Decoder<'_>) -> Result<u16, ModelError> {
        Ok(u16::from_be_bytes(r.take(2)?.try_into().unwrap()))
    }
}
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
struct BigEndian(#[trace(with = "be")] u16);
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
struct Generic<T, const N: usize>([T; N]);
#[derive(TraceEncode)]
struct EncodeOnly(u8);
#[derive(TraceDecode)]
struct DecodeOnly(u8);

fn golden<
    T: stateless::value_codec::TraceEncode
        + stateless::value_codec::TraceDecode
        + std::fmt::Debug
        + PartialEq,
>(
    value: T,
    bytes: &[u8],
) {
    assert_eq!(value.trace_bytes(bytes.len()).unwrap(), bytes);
    assert_eq!(
        T::from_trace(bytes, DecodeLimits::default()).unwrap(),
        value
    );
    for n in 0..bytes.len() {
        assert!(T::from_trace(&bytes[..n], DecodeLimits::default()).is_err());
    }
    let mut trailing = bytes.to_vec();
    trailing.push(0);
    assert!(T::from_trace(&trailing, DecodeLimits::default()).is_err());
    if !bytes.is_empty() {
        assert!(value.trace_bytes(bytes.len() - 1).is_err());
    }
}
#[test]
fn finite_independent_wire_goldens() {
    golden(
        Record {
            byte: 7,
            ready: true,
            count: 0x1234,
        },
        &[7, 1, 0x34, 0x12],
    );
    golden(Pair(7, 0x1234), &[7, 0x34, 0x12]);
    golden(Unit, &[]);
    golden(Empty {}, &[]);
    golden(
        Nested {
            marker: 9,
            pair: Pair(7, 0x1234),
        },
        &[9, 7, 0x34, 0x12],
    );
    golden(Text("abc".into()), &[3, 0, 0, 0, b'a', b'b', b'c']);
    golden(Text8("abc".into()), &[3, b'a', b'b', b'c']);
    golden(Text16("abc".into()), &[3, 0, b'a', b'b', b'c']);
    golden(Text32("abc".into()), &[3, 0, 0, 0, b'a', b'b', b'c']);
    golden(BigEndian(0x1234), &[0x12, 0x34]);
    golden(Event8::Idle, &[7]);
    golden(Event8::Set(0x1234), &[2, 0x34, 0x12]);
    golden(Event8::Ready { ready: true }, &[255, 1]);
    golden(Event16::Set(0x1234), &[0x90, 1, 0x34, 0x12]);
    golden(Event16::Max, &[255, 255]);
    golden(Event32::Max, &[255, 255, 255, 255]);
    golden(
        Generic::<u16, 2>([0x1234, 0xabcd]),
        &[0x34, 0x12, 0xcd, 0xab],
    );
    assert_eq!(EncodeOnly(7).trace_bytes(1).unwrap(), [7]);
    assert_eq!(
        DecodeOnly::from_trace(&[7], DecodeLimits::default())
            .unwrap()
            .0,
        7
    );
    assert_eq!(Event8::Idle.trace_variant(), Some("Idle"));
    assert_eq!(Pair(0, 0).trace_variant(), None);
    assert!(Record::trace_schema().contains("Record"));
}
fn limits(work: usize, depth: usize) -> DecodeLimits {
    DecodeLimits {
        work,
        depth,
        ..Default::default()
    }
}
#[test]
fn exact_guard_and_direct_field_budgets() {
    assert!(Record::from_trace(&[7, 1, 0x34, 0x12], limits(5, 3)).is_ok());
    assert!(Record::from_trace(&[7, 1, 0x34, 0x12], limits(4, 3)).is_err());
    assert!(Record::from_trace(&[7, 1, 0x34, 0x12], limits(5, 2)).is_err());
    assert!(Text::from_trace(&[3, 0, 0, 0, 97, 98, 99], limits(6, 3)).is_ok());
    assert!(Text8::from_trace(&[3, 97, 98, 99], limits(5, 2)).is_ok());
    assert!(Text8::from_trace(&[3, 97, 98, 99], limits(4, 2)).is_err());
    assert!(Text8::from_trace(&[3, 97, 98, 99], limits(5, 1)).is_err());
    assert!(BigEndian::from_trace(&[0x12, 0x34], limits(1, 1)).is_ok());
    // A selected enum payload does not introduce a hidden record guard.
    assert!(Event8::Set(1).trace_bytes(3).is_ok());
    assert!(Event8::from_trace(&[2, 1, 0], limits(3, 2)).is_ok());
    let mut reader = Decoder::new(&[7, 0x34, 0x12], limits(2, 1)).unwrap();
    assert_eq!(Pair::trace_decode(&mut reader).unwrap(), Pair(7, 0x1234));
    reader.finish().unwrap();
}
#[test]
fn raw_unknown_tag_can_be_swallowed_but_guarded_and_structural_errors_stick() {
    let mut raw = Decoder::new(&[99, 7], DecodeLimits::default()).unwrap();
    assert!(Event8::trace_decode(&mut raw).is_err());
    assert_eq!(raw.value::<u8>().unwrap(), 7);
    raw.finish().unwrap();
    let mut guarded = Decoder::new(&[99, 7], DecodeLimits::default()).unwrap();
    let first = guarded.value::<Event8>().unwrap_err().to_string();
    assert_eq!(guarded.value::<u8>().unwrap_err().to_string(), first);
    assert_eq!(guarded.finish().unwrap_err().to_string(), first);
    let mut truncated = Decoder::new(&[], DecodeLimits::default()).unwrap();
    let first = Event16::trace_decode(&mut truncated)
        .unwrap_err()
        .to_string();
    assert_eq!(truncated.value::<u8>().unwrap_err().to_string(), first);
    assert_eq!(truncated.finish().unwrap_err().to_string(), first);
}

mod binding {
    use super::*;
    use stateless::{Check, Model, ModelCodec, ModelMetadata, Transition};
    use std::sync::atomic::{AtomicUsize, Ordering};
    static LIMIT_CALLS: AtomicUsize = AtomicUsize::new(0);
    fn fresh_limits() -> DecodeLimits {
        LIMIT_CALLS.fetch_add(1, Ordering::SeqCst);
        limits(1, 1)
    }
    #[derive(Debug, Clone, PartialEq, Eq, Hash, TraceEncode, TraceDecode)]
    struct Value(u8);
    // Deliberately no TraceDecode implementation.
    #[derive(Debug, Clone, PartialEq, Eq, TraceEncode)]
    struct Output(u8);
    #[derive(statelessness_macros::ModelCodec)]
    #[model_codec(version=1,decode_limits=fresh_limits())]
    struct Bound {
        version: u32,
    }
    impl Model for Bound {
        type State = Value;
        type Input = Value;
        type Output = Output;
        fn metadata(&self) -> ModelMetadata {
            ModelMetadata {
                name: "n13-reference".into(),
                model_version: 9,
                properties_version: 3,
                codec_version: self.version,
                build: "finite".into(),
            }
        }
        fn initial_state(&self) -> Result<Value, ModelError> {
            Ok(Value(0))
        }
        fn step(&self, _: &Value, i: &Value) -> Result<Transition<Value, Output>, ModelError> {
            Ok(Transition::accepted(i.clone(), vec![Output(i.0)]))
        }
        fn check_state(&self, _: &Value) -> Result<Vec<Check>, ModelError> {
            Ok(vec![])
        }
    }
    #[test]
    fn all_eight_hooks_gate_version_and_limits_are_fresh_decode_only() {
        LIMIT_CALLS.store(0, Ordering::SeqCst);
        let wrong = Bound { version: 2 };
        let value = Value(7);
        let output = Output(7);
        assert!(
            wrong
                .encode_state(&value)
                .unwrap_err()
                .to_string()
                .contains("version")
        );
        assert!(
            wrong
                .encode_input(&value)
                .unwrap_err()
                .to_string()
                .contains("version")
        );
        assert!(
            wrong
                .encode_output(&output)
                .unwrap_err()
                .to_string()
                .contains("version")
        );
        assert!(
            wrong
                .decode_state(&[])
                .unwrap_err()
                .to_string()
                .contains("version")
        );
        assert!(
            wrong
                .decode_input(&[])
                .unwrap_err()
                .to_string()
                .contains("version")
        );
        let mut bytes = vec![9];
        {
            let mut sink = EncodeBuffer::new(&mut bytes, 1);
            assert!(
                wrong
                    .encode_state_into(&value, &mut sink)
                    .unwrap_err()
                    .to_string()
                    .contains("version")
            );
            assert!(
                wrong
                    .encode_input_into(&value, &mut sink)
                    .unwrap_err()
                    .to_string()
                    .contains("version")
            );
            assert!(
                wrong
                    .encode_output_into(&output, &mut sink)
                    .unwrap_err()
                    .to_string()
                    .contains("version")
            );
            sink.finish().unwrap();
        }
        assert_eq!(bytes, [9]);
        assert_eq!(LIMIT_CALLS.load(Ordering::SeqCst), 0);
        let model = Bound { version: 1 };
        assert_eq!(model.encode_state(&value).unwrap(), [7]);
        assert_eq!(model.encode_input(&value).unwrap(), [7]);
        assert_eq!(model.encode_output(&output).unwrap(), [7]);
        assert_eq!(LIMIT_CALLS.load(Ordering::SeqCst), 0);
        // Value needs two entries. Each fresh work=1 reader must reject it.
        assert!(model.decode_state(&[7]).is_err());
        assert!(model.decode_input(&[7]).is_err());
        assert_eq!(LIMIT_CALLS.load(Ordering::SeqCst), 2);
        assert_eq!(model.metadata().model_version, 9);
        assert_eq!(model.step(&Value(0), &value).unwrap().outputs, vec![output]);
    }
}

mod raw_failure {
    use super::*;
    mod callback {
        use super::*;
        pub fn encode(_: &u8, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
            out.extend_from_slice(&[7])?;
            Err(ModelError::new("callback sentinel"))
        }
    }
    mod callback_after_write_failure {
        use super::*;
        pub fn encode(_: &u8, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
            let _ = out.extend_from_slice(&[7, 8]);
            Err(ModelError::new("callback sentinel"))
        }
    }
    #[derive(TraceEncode)]
    struct Callback(#[trace(with = "callback")] u8);
    #[derive(TraceEncode)]
    struct ActualFailure(#[trace(with = "callback_after_write_failure")] u8);
    #[test]
    fn raw_callback_error_priority_and_sticky_actual_write_failure() {
        let mut bytes = vec![];
        {
            let mut sink = EncodeBuffer::new(&mut bytes, 1);
            assert_eq!(
                Callback(0).trace_encode(&mut sink).unwrap_err().to_string(),
                "callback sentinel"
            );
            sink.finish().unwrap();
        }
        assert_eq!(bytes, [7]);
        let mut bytes = vec![];
        let mut sink = EncodeBuffer::new(&mut bytes, 1);
        assert_eq!(
            ActualFailure(0)
                .trace_encode(&mut sink)
                .unwrap_err()
                .to_string(),
            "callback sentinel"
        );
        assert!(sink.finish().is_err());
    }
}

mod independent_length {
    use super::*;
    use stateless::value_codec::{LengthDecode, LengthEncode};
    // No ordinary TraceEncode or TraceDecode is implemented for this field.
    #[derive(Debug, PartialEq)]
    struct LengthOnly(String);
    impl LengthEncode for LengthOnly {
        fn encode_length(&self, width: u8, out: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
            self.0.encode_length(width, out)
        }
    }
    impl LengthDecode for LengthOnly {
        fn decode_length(width: u8, reader: &mut Decoder<'_>) -> Result<Self, ModelError> {
            Ok(Self(String::decode_length(width, reader)?))
        }
    }
    #[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
    struct LengthRecord(#[trace(length = "u8")] LengthOnly);
    #[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
    struct EmptyTuple();
    #[test]
    fn length_only_field_needs_no_ordinary_direction() {
        golden(LengthRecord(LengthOnly("abc".into())), &[3, 97, 98, 99]);
        golden(EmptyTuple(), &[]);
        assert!(LengthRecord::from_trace(&[3, 97, 98, 99], limits(5, 2)).is_ok());
    }
}
