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
#[derive(Clone, Debug, PartialEq, Eq, Hash, TraceEncode, TraceDecode)]
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
#[derive(Debug, TraceDecode)]
struct DecodeOnly(u8);

// cases.json decode_only_u8: a raw scalar callback, not a derived record.
#[derive(Debug)]
struct DecodeOnlyByte(u8);
impl stateless::value_codec::TraceDecode for DecodeOnlyByte {
    fn trace_decode(reader: &mut Decoder<'_>) -> Result<Self, ModelError> {
        Ok(Self(u8::trace_decode(reader)?))
    }
}

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
    let mut raw = Decoder::new(&[8, 9], DecodeLimits::default()).unwrap();
    assert!(Event8::trace_decode(&mut raw).is_err());
    assert_eq!(raw.value::<u8>().unwrap(), 9);
    raw.finish().unwrap();
    let mut guarded = Decoder::new(&[8, 9], DecodeLimits::default()).unwrap();
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
        let calls = LIMIT_CALLS.load(Ordering::SeqCst);
        if calls <= 2 { limits(1, 1) } else if calls == 4 { limits(1, 2) } else { limits(2, 2) }
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
        let mut bytes = Vec::new();
        {
            let mut sink = EncodeBuffer::new(&mut bytes, 1);
            sink.extend_from_slice(&[9]).unwrap();
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
        // Consecutive calls receive changed limits; no reader budget is reused.
        assert_eq!(model.decode_state(&[7]).unwrap(), value);
        assert!(model.decode_input(&[7]).is_err());
        assert_eq!(model.decode_input(&[7]).unwrap(), value);
        assert!(model.decode_state(&[7, 8]).unwrap_err().to_string().contains("trailing"));
        assert!(model.decode_input(&[7, 8]).unwrap_err().to_string().contains("trailing"));
        assert!(model.decode_state(&[]).unwrap_err().to_string().contains("truncated"));
        assert!(model.decode_input(&[]).unwrap_err().to_string().contains("truncated"));
        assert_eq!(LIMIT_CALLS.load(Ordering::SeqCst), 9);
        let mut encoded = vec![];
        { let mut sink = EncodeBuffer::new(&mut encoded, 3);
          model.encode_state_into(&value, &mut sink).unwrap();
          model.encode_input_into(&value, &mut sink).unwrap();
          model.encode_output_into(&output, &mut sink).unwrap(); sink.finish().unwrap(); }
        assert_eq!(encoded, [7, 7, 7]);
        assert_eq!(LIMIT_CALLS.load(Ordering::SeqCst), 9);
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
        budget::<LengthRecord>(&[3, 97, 98, 99], 5, 2, 3, 3);
        golden(EmptyTuple(), &[]);
        assert!(LengthRecord::from_trace(&[3, 97, 98, 99], limits(5, 2)).is_ok());
    }
}

#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
struct Words8(#[trace(length = "u8")] Vec<u16>);
#[derive(TraceEncode)]
enum EncodeEvent { #[trace(tag = 7)] Idle, #[trace(tag = 2)] Value(EncodeOnly) }
#[derive(Debug, TraceDecode)]
enum DecodeEvent { #[trace(tag = 7)] Idle, #[trace(tag = 2)] Value(DecodeOnlyByte) }
#[derive(TraceEncode)]
enum Renamed { #[trace(tag = 7)] Waiting }
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
enum NamedEvent { #[trace(tag = 255)] Ready { ready: bool, byte: u8 } }
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
#[trace(tag_type = "u32")]
enum MaxPayload { #[trace(tag = 4294967295)] Maximum(u8) }

#[test]
fn literal_draft_routes_and_diagnostics() {
    golden(Text16("é".into()), &[2, 0, 0xc3, 0xa9]);
    golden(Text32("\u{feff}".into()), &[3, 0, 0, 0, 0xef, 0xbb, 0xbf]);
    golden(Text8("".into()), &[0]);
    golden(Words8(vec![258, 65535]), &[2, 2, 1, 255, 255]);
    golden(Words8(vec![]), &[0]);
    golden(Nested { marker: 9, pair: Pair(1, 258) }, &[9, 1, 2, 1]);
    golden(NamedEvent::Ready { ready: true, byte: 9 }, &[255, 1, 9]);
    golden(MaxPayload::Maximum(7), &[255, 255, 255, 255, 7]);
    assert_eq!(EncodeEvent::Idle.trace_bytes(1).unwrap(), [7]);
    assert_eq!(EncodeEvent::Value(EncodeOnly(9)).trace_bytes(2).unwrap(), [2, 9]);
    assert!(matches!(DecodeEvent::from_trace(&[7], limits(2, 2)).unwrap(), DecodeEvent::Idle));
    assert!(matches!(DecodeEvent::from_trace(&[2, 9], limits(3, 2)).unwrap(), DecodeEvent::Value(DecodeOnlyByte(9))));
    assert_eq!(NamedEvent::Ready { ready: true, byte: 9 }.trace_variant(), Some("Ready"));
    assert_eq!(Renamed::Waiting.trace_variant(), Some("Waiting"));
    assert_eq!(Renamed::Waiting.trace_bytes(1).unwrap(), Event8::Idle.trace_bytes(1).unwrap());
    for token in ["struct", "Record", "byte"] { assert!(Record::trace_schema().contains(token)); }
}

#[test]
fn exact_and_one_under_all_quotas_and_error_routes() {
    let bytes = [3, 97, 98, 99];
    let exact = DecodeLimits { bytes: 4, allocation: 3, elements: 3, work: 5, depth: 2 };
    assert_eq!(Text8::from_trace(&bytes, exact).unwrap(), Text8("abc".into()));
    for (limited, expected) in [
        (DecodeLimits { bytes: 3, ..exact }, "decode byte limit"),
        (DecodeLimits { allocation: 2, ..exact }, "decode allocation limit"),
        (DecodeLimits { elements: 2, ..exact }, "decode element limit"),
        (DecodeLimits { work: 4, ..exact }, "decode work limit"),
        (DecodeLimits { depth: 1, ..exact }, "decode nesting limit"),
    ] { assert_eq!(Text8::from_trace(&bytes, limited).unwrap_err().to_string(), expected); }
    assert!(Pair::from_trace(&[7, 0x34, 0x12], limits(3, 2)).is_ok());
    assert_eq!(Pair::from_trace(&[7, 0x34, 0x12], limits(2, 2)).unwrap_err().to_string(), "decode work limit");
    assert_eq!(Pair::from_trace(&[7, 0x34, 0x12], limits(3, 1)).unwrap_err().to_string(), "decode nesting limit");
    assert_eq!(Text::from_trace(&[3, 0, 0, 0, 97, 98, 99], limits(5, 3)).unwrap_err().to_string(), "decode work limit");
    assert_eq!(Text::from_trace(&[3, 0, 0, 0, 97, 98, 99], limits(6, 2)).unwrap_err().to_string(), "decode nesting limit");
    let mut raw = Decoder::new(&[], limits(0, 0)).unwrap();
    assert_eq!(Unit::trace_decode(&mut raw).unwrap(), Unit); raw.finish().unwrap();
    // This Rust64 layout is asserted, not inferred from summed wire widths.
    assert_eq!(std::mem::size_of::<Pair>(), 4);
    let pairs = [2, 0, 0, 0, 7, 0x34, 0x12, 1, 2, 1];
    assert_eq!(Vec::<Pair>::from_trace(&pairs, DecodeLimits { allocation: 8, elements: 2, work: 8, depth: 3, bytes: 10 }).unwrap(), vec![Pair(7, 4660), Pair(1, 258)]);
    assert_eq!(Vec::<Pair>::from_trace(&pairs, DecodeLimits { allocation: 7, ..Default::default() }).unwrap_err().to_string(), "decode allocation limit");
    assert_eq!(Pair::from_trace(&[7, 0x34, 0x12, 0], DecodeLimits::default()).unwrap_err().to_string(), "trailing value bytes");
    assert_eq!(Record::from_trace(&[7, 2, 0x34, 0x12], DecodeLimits::default()).unwrap_err().to_string(), "invalid boolean");
    assert_eq!(Text8::from_trace(&[1, 255], DecodeLimits::default()).unwrap_err().to_string(), "invalid UTF-8");
    assert_eq!(Text8::from_trace(&[3, 97], DecodeLimits::default()).unwrap_err().to_string(), "truncated value");
    let mut bytes = vec![];
    { let mut sink = EncodeBuffer::new(&mut bytes, 3); assert!(Record { byte: 7, ready: true, count: 4660 }.trace_encode(&mut sink).is_err()); }
    assert_eq!(bytes, [7, 1]);
    let mut bytes = vec![];
    { let mut sink = EncodeBuffer::new(&mut bytes, 999); assert_eq!(Text8("x".repeat(256)).trace_encode(&mut sink).unwrap_err().to_string(), "length overflow"); }
    assert!(bytes.is_empty());
}

mod script_callbacks {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    static CALLS: AtomicUsize = AtomicUsize::new(0);
    struct Later;
    impl stateless::value_codec::TraceDecode for Later {
        fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
            CALLS.fetch_add(1, Ordering::SeqCst);
            let _ = Event8::trace_decode(r);
            Err(ModelError::new("later callback error"))
        }
    }
    #[test]
    fn all_sticky_script_callback_counts() {
        CALLS.store(0, Ordering::SeqCst);
        let mut r = Decoder::new(&[], DecodeLimits::default()).unwrap();
        assert_eq!(Event8::trace_decode(&mut r).unwrap_err().to_string(), "truncated value");
        assert_eq!(r.value::<Later>().err().unwrap().to_string(), "truncated value");
        assert_eq!(CALLS.load(Ordering::SeqCst), 0);
        assert_eq!(r.finish().unwrap_err().to_string(), "truncated value");
        let mut r = Decoder::new(&[], DecodeLimits::default()).unwrap();
        assert_eq!(r.value::<Later>().err().unwrap().to_string(), "truncated value");
        assert_eq!(CALLS.load(Ordering::SeqCst), 1);
        assert_eq!(r.finish().unwrap_err().to_string(), "truncated value");
    }
}

mod callback_order {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};
    static CALLS: AtomicUsize = AtomicUsize::new(0);
    mod field {
        use super::*;
        pub fn decode(r: &mut Decoder<'_>) -> Result<u8, ModelError> {
            CALLS.fetch_add(1, Ordering::SeqCst); Ok(r.take(1)?[0])
        }
    }
    #[derive(Debug, TraceDecode)]
    struct Observed(#[trace(with = "field")] u8);
    #[derive(Debug, TraceDecode)]
    enum ObservedEnum { #[trace(tag = 7)] Value(#[trace(with = "field")] u8) }
    #[test]
    fn admission_and_unknown_tag_precede_field_callbacks() {
        CALLS.store(0, Ordering::SeqCst);
        assert_eq!(Observed::from_trace(&[1, 2, 3, 4], DecodeLimits { bytes: 3, ..Default::default() }).unwrap_err().to_string(), "decode byte limit");
        assert_eq!(CALLS.load(Ordering::SeqCst), 0);
        assert_eq!(ObservedEnum::from_trace(&[8, 9], DecodeLimits::default()).unwrap_err().to_string(), "unknown enum tag");
        assert_eq!(CALLS.load(Ordering::SeqCst), 0);
        assert_eq!(Observed::from_trace(&[9], DecodeLimits::default()).unwrap().0, 9);
        assert_eq!(CALLS.load(Ordering::SeqCst), 1);
        assert!(matches!(ObservedEnum::from_trace(&[7, 9], DecodeLimits::default()).unwrap(), ObservedEnum::Value(9)));
        assert_eq!(CALLS.load(Ordering::SeqCst), 2);
    }
}

mod literal_raw_sink {
    use super::*;
    mod rejected {
        use super::*;
        pub fn encode(_: &u8, sink: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
            sink.extend_from_slice(&[0xaa])?; Err(ModelError::new("adapter rejected"))
        }
    }
    #[derive(TraceEncode)]
    struct AdapterError { prefix: u8, #[trace(with = "rejected")] value: u8 }
    #[test]
    fn raw_adapter_literal_prefix_and_sticky_write() {
        let mut bytes = vec![];
        { let mut sink = EncodeBuffer::new(&mut bytes, 3);
          assert_eq!(AdapterError { prefix: 7, value: 0 }.trace_encode(&mut sink).unwrap_err().to_string(), "adapter rejected");
          sink.finish().unwrap(); sink.extend_from_slice(&[0xbb]).unwrap(); sink.finish().unwrap(); }
        assert_eq!(bytes, [7, 0xaa, 0xbb]);
        let mut bytes = vec![];
        { let mut sink = EncodeBuffer::new(&mut bytes, 1);
          let first = BigEndian(4660).trace_encode(&mut sink).unwrap_err().to_string();
          // extend_from_slice rejects the write directly; finish reports sticky failure.
          assert_eq!(first, "encoded blob exceeds byte limit");
          assert_eq!(sink.finish().unwrap_err().to_string(), "encoded blob exceeds byte or allocation limit"); }
        assert!(bytes.is_empty());
    }
}

mod literal_binding {
    use super::*;
    use stateless::{Check, Model, ModelCodec, ModelMetadata, Transition};
    use std::sync::atomic::{AtomicUsize, Ordering};
    static CALLS: AtomicUsize = AtomicUsize::new(0);
    fn fresh() -> DecodeLimits {
        CALLS.fetch_add(1, Ordering::SeqCst);
        DecodeLimits { bytes: 3, allocation: 0, elements: 0, work: 3, depth: 2 }
    }
    #[derive(Clone, Debug, PartialEq, Eq, Hash, TraceEncode, TraceDecode)]
    struct State(u8, u16);
    // The adapter has only encode; neither this output nor its field needs a decoder.
    mod output_be {
        use super::*;
        pub fn encode(value: &u16, sink: &mut EncodeBuffer<'_>) -> Result<(), ModelError> {
            sink.extend_from_slice(&value.to_be_bytes())
        }
    }
    #[derive(Clone, Debug, PartialEq, Eq, TraceEncode)]
    struct Output(#[trace(with = "output_be")] u16);
    #[derive(statelessness_macros::ModelCodec)]
    #[model_codec(version=1, decode_limits=fresh())]
    struct Bound;
    impl Model for Bound {
        type State = State; type Input = State; type Output = Output;
        fn metadata(&self) -> ModelMetadata { ModelMetadata { name: "n13-literal".into(), model_version: 9, properties_version: 3, codec_version: 1, build: "finite".into() } }
        fn initial_state(&self) -> Result<State, ModelError> { Ok(State(0, 0)) }
        fn step(&self, _: &State, input: &State) -> Result<Transition<State, Output>, ModelError> { Ok(Transition::accepted(input.clone(), vec![Output(input.1)])) }
        fn check_state(&self, _: &State) -> Result<Vec<Check>, ModelError> { Ok(vec![]) }
    }
    #[test]
    fn literal_model_exact_fresh_limits_and_encode_only_big_endian_output() {
        CALLS.store(0, Ordering::SeqCst);
        let model = Bound;
        assert_eq!(model.decode_state(&[7, 0x34, 0x12]).unwrap(), State(7, 4660));
        assert_eq!(CALLS.load(Ordering::SeqCst), 1);
        assert_eq!(model.decode_state(&[7, 0x34, 0x12]).unwrap(), State(7, 4660));
        assert_eq!(CALLS.load(Ordering::SeqCst), 2);
        assert_eq!(model.encode_output(&Output(4660)).unwrap(), [0x12, 0x34]);
        let mut bytes = vec![];
        { let mut sink = EncodeBuffer::new(&mut bytes, 8);
          model.encode_state_into(&State(7, 4660), &mut sink).unwrap();
          model.encode_input_into(&State(7, 4660), &mut sink).unwrap();
          model.encode_output_into(&Output(4660), &mut sink).unwrap(); sink.finish().unwrap(); }
        assert_eq!(bytes, [7, 0x34, 0x12, 7, 0x34, 0x12, 0x12, 0x34]);
        assert_eq!(CALLS.load(Ordering::SeqCst), 2);
        // With exact byte admission a trailing input first exceeds bytes. The
        // independently tested custom fresh limits above admit trailing bytes.
        assert_eq!(model.decode_state(&[7, 0x34]).unwrap_err().to_string(), "truncated value");
        assert_eq!(model.decode_input(&[7, 0x34]).unwrap_err().to_string(), "truncated value");
    }
    #[derive(statelessness_macros::ModelCodec)]
    struct EventBound;
    impl Model for EventBound {
        type State = Event8; type Input = Event8; type Output = Output;
        fn metadata(&self) -> ModelMetadata { Bound.metadata() }
        fn initial_state(&self) -> Result<Event8, ModelError> { Ok(Event8::Idle) }
        fn step(&self, _: &Event8, input: &Event8) -> Result<Transition<Event8, Output>, ModelError> { Ok(Transition::accepted(input.clone(), vec![])) }
        fn check_state(&self, _: &Event8) -> Result<Vec<Check>, ModelError> { Ok(vec![]) }
    }
    #[test]
    fn literal_bound_enum_trailing() {
        assert_eq!(EventBound.decode_state(&[7, 0]).unwrap_err().to_string(), "trailing value bytes");
        assert_eq!(EventBound.decode_input(&[7, 0]).unwrap_err().to_string(), "trailing value bytes");
        assert_eq!(EventBound.decode_state(&[7]).unwrap(), Event8::Idle);
    }

}

// Literal positive-row budgets, calculated from the public Rust call graph.
fn budget<T: stateless::value_codec::TraceDecode + std::fmt::Debug>(bytes: &[u8], work: usize, depth: usize, allocation: usize, elements: usize) {
    let exact = DecodeLimits { bytes: bytes.len(), work, depth, allocation, elements };
    assert!(T::from_trace(bytes, exact).is_ok());
    if work > 0 { assert_eq!(T::from_trace(bytes, DecodeLimits { work: work - 1, ..exact }).unwrap_err().to_string(), "decode work limit"); }
    if depth > 0 { assert_eq!(T::from_trace(bytes, DecodeLimits { depth: depth - 1, ..exact }).unwrap_err().to_string(), "decode nesting limit"); }
    if allocation > 0 { assert_eq!(T::from_trace(bytes, DecodeLimits { allocation: allocation - 1, ..exact }).unwrap_err().to_string(), "decode allocation limit"); }
    if elements > 0 { assert_eq!(T::from_trace(bytes, DecodeLimits { elements: elements - 1, ..exact }).unwrap_err().to_string(), "decode element limit"); }
}
#[test]
fn literal_positive_row_budgets() {
    budget::<Record>(&[7, 1, 0x34, 0x12], 5, 3, 0, 0);
    budget::<Pair>(&[7, 0x34, 0x12], 3, 2, 0, 0);
    budget::<Unit>(&[], 1, 1, 0, 0);
    budget::<Empty>(&[], 1, 1, 0, 0);
    budget::<Nested>(&[9, 1, 2, 1], 5, 3, 0, 0);
    budget::<Text>(&[3, 0, 0, 0, 97, 98, 99], 6, 3, 3, 3);
    budget::<Text8>(&[3, 97, 98, 99], 5, 2, 3, 3);
    budget::<Text16>(&[2, 0, 0xc3, 0xa9], 4, 2, 2, 2);
    budget::<Text32>(&[3, 0, 0, 0, 0xef, 0xbb, 0xbf], 5, 2, 3, 3);
    budget::<Text8>(&[0], 2, 2, 0, 0);
    budget::<Words8>(&[2, 2, 1, 255, 255], 4, 2, 4, 2);
    budget::<Words8>(&[0], 2, 2, 0, 0);
    budget::<BigEndian>(&[0x12, 0x34], 1, 1, 0, 0);
    budget::<Event8>(&[7], 2, 2, 0, 0);
    budget::<Event8>(&[2, 0x34, 0x12], 3, 2, 0, 0);
    budget::<NamedEvent>(&[255, 1, 9], 5, 3, 0, 0);
    budget::<Event16>(&[0x90, 1, 0x34, 0x12], 3, 2, 0, 0);
    budget::<MaxPayload>(&[255, 255, 255, 255, 7], 3, 2, 0, 0);
    budget::<Vec<Pair>>(&[2, 0, 0, 0, 7, 0x34, 0x12, 1, 2, 1], 8, 3, 8, 2);
    budget::<Event16>(&[255, 255], 2, 2, 0, 0);
    budget::<DecodeOnly>(&[7], 2, 2, 0, 0);
    budget::<DecodeEvent>(&[7], 2, 2, 0, 0);
    // Guarded enum (1), guarded tag (1), guarded raw payload (1).
    // Tag and payload are siblings at depth 2; the raw callback adds no guard.
    budget::<DecodeEvent>(&[2, 9], 3, 2, 0, 0);
}
