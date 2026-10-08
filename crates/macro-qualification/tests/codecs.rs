use stateless::value_codec::{TraceDecode as _, TraceEncode as _};
use stateless::{ModelCodec, demo};
use statelessness_macros::{TraceDecode, TraceEncode};
#[derive(Debug, PartialEq, Eq, TraceEncode, TraceDecode)]
struct Snapshot {
    generation: u8,
    active: bool,
    ready: bool,
    #[trace(length = "u8")]
    pending: Vec<u8>,
}
#[derive(Debug, PartialEq, Eq, TraceEncode, TraceDecode)]
enum Effect {
    #[trace(tag = 0)]
    Request(u8),
    #[trace(tag = 1)]
    Publish(u8),
    #[trace(tag = 2)]
    Release(u8),
}
#[test]
fn legacy_request_bytes_and_property_failing_snapshots() {
    let model = demo::RequestModel::buggy();
    for generation in [0, 1, 2, 255] {
        for active in [false, true] {
            for ready in [false, true] {
                for pending in [vec![], vec![1], vec![2, 1, 255]] {
                    let handwritten = demo::State {
                        generation,
                        active,
                        ready,
                        pending: pending.clone(),
                    };
                    let generated = Snapshot {
                        generation,
                        active,
                        ready,
                        pending,
                    };
                    let bytes = generated.trace_bytes(100).unwrap();
                    assert_eq!(bytes, model.encode_state(&handwritten).unwrap());
                    assert_eq!(
                        Snapshot::from_trace(&bytes, Default::default()).unwrap(),
                        generated
                    );
                    for end in 0..bytes.len() {
                        assert!(Snapshot::from_trace(&bytes[..end], Default::default()).is_err());
                    }
                }
            }
        }
    }
    for key in [0, 1, 255] {
        for (generated, hand) in [
            (Effect::Request(key), demo::Output::Request(key)),
            (Effect::Publish(key), demo::Output::Publish(key)),
            (Effect::Release(key), demo::Output::Release(key)),
        ] {
            assert_eq!(
                generated.trace_bytes(100).unwrap(),
                model.encode_output(&hand).unwrap()
            );
        }
    }
    assert!(Snapshot::from_trace(&[0, 2, 0, 0], Default::default()).is_err());
    assert!(Snapshot::from_trace(&[0, 0, 0, 0, 0], Default::default()).is_err());
    assert!(
        Snapshot {
            generation: 1,
            active: false,
            ready: true,
            pending: vec![0; 256]
        }
        .trace_bytes(1000)
        .is_err()
    );
}
mod big_endian {
    pub fn encode(
        value: &u16,
        out: &mut stateless::EncodeBuffer<'_>,
    ) -> Result<(), stateless::ModelError> {
        out.extend_from_slice(&value.to_be_bytes())
    }
    pub fn decode(
        reader: &mut stateless::value_codec::Decoder<'_>,
    ) -> Result<u16, stateless::ModelError> {
        Ok(u16::from_be_bytes(reader.take(2)?.try_into().unwrap()))
    }
}
#[derive(Debug, PartialEq, Eq, TraceEncode, TraceDecode)]
struct Custom {
    #[trace(with = "big_endian")]
    value: u16,
}
#[test]
fn custom_adapter_and_limits() {
    let value = Custom { value: 0x1234 };
    assert_eq!(value.trace_bytes(2).unwrap(), [0x12, 0x34]);
    assert_eq!(
        Custom::from_trace(&[0x12, 0x34], Default::default()).unwrap(),
        value
    );
    assert!(value.trace_bytes(1).is_err());
}
