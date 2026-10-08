use stateless::value_codec::*;
use stateless::{EncodeBuffer, ModelError};
#[test]
fn independent_golden_and_strict_failures() {
    assert_eq!(
        (0x1234u16, -2i32, true, Some(9u8))
            .trace_bytes(100)
            .unwrap(),
        [0x34, 0x12, 0xfe, 0xff, 0xff, 0xff, 1, 1, 9]
    );
    assert_eq!(
        "hi".to_string().trace_bytes(100).unwrap(),
        [2, 0, 0, 0, b'h', b'i']
    );
    let limits = DecodeLimits::default();
    assert!(bool::from_trace(&[2], limits).is_err());
    assert!(u16::from_trace(&[1], limits).is_err());
    assert!(u16::from_trace(&[1, 2, 3], limits).is_err());
    assert!(String::from_trace(&[1, 0, 0, 0, 0xff], limits).is_err());
    assert!(Option::<u8>::from_trace(&[2], limits).is_err());
    let value = (vec![1u64, 2], Some(vec![true, false]));
    let bytes = value.trace_bytes(100).unwrap();
    for end in 0..bytes.len() {
        assert!(<(Vec<u64>, Option<Vec<bool>>)>::from_trace(&bytes[..end], limits).is_err());
    }
    assert_eq!(
        <(Vec<u64>, Option<Vec<bool>>)>::from_trace(&bytes, limits).unwrap(),
        value
    );
}
#[test]
fn aggregate_depth_work_and_allocation_bounds() {
    let bytes = vec![vec![1u64, 2], vec![3, 4]].trace_bytes(100).unwrap();
    assert!(
        Vec::<Vec<u64>>::from_trace(
            &bytes,
            DecodeLimits {
                allocation: 50,
                ..Default::default()
            }
        )
        .is_err()
    );
    assert!(
        Vec::<Vec<u64>>::from_trace(
            &bytes,
            DecodeLimits {
                elements: 5,
                ..Default::default()
            }
        )
        .is_err()
    );
    assert!(
        Vec::<Vec<u64>>::from_trace(
            &bytes,
            DecodeLimits {
                depth: 2,
                ..Default::default()
            }
        )
        .is_err()
    );
    assert!(
        Vec::<()>::from_trace(
            &1_000_000u32.to_le_bytes(),
            DecodeLimits {
                work: 10,
                ..Default::default()
            }
        )
        .is_err()
    );
    assert!(Vec::<u128>::from_trace(&u32::MAX.to_le_bytes(), DecodeLimits::default()).is_err());
    assert_eq!(
        Vec::<()>::from_trace(&3u32.to_le_bytes(), DecodeLimits::default())
            .unwrap()
            .len(),
        3
    );
    assert!(
        u8::from_trace(
            &[1],
            DecodeLimits {
                bytes: 0,
                ..Default::default()
            }
        )
        .is_err()
    );
    assert!([()].trace_bytes(0).is_ok());
    let a = [3u16, 4, 5];
    assert_eq!(
        <[u16; 3]>::from_trace(&a.trace_bytes(6).unwrap(), Default::default()).unwrap(),
        a
    );
}
#[test]
fn allocation_overflow_and_encoder_limit_are_errors() {
    let mut reader = Decoder::new(
        &[],
        DecodeLimits {
            elements: usize::MAX,
            allocation: usize::MAX,
            work: usize::MAX,
            ..Default::default()
        },
    )
    .unwrap();
    assert!(reader.collection(usize::MAX, 2).is_err());
    let mut bytes = Vec::new();
    let mut out = EncodeBuffer::new(&mut bytes, 2);
    assert!(vec![1u32].trace_encode(&mut out).is_err());
    assert!(out.finish().is_err());
    assert!(bytes.is_empty());
    struct Huge;
    impl TraceDecode for Huge {
        fn trace_decode(r: &mut Decoder<'_>) -> Result<Self, ModelError> {
            r.collection(usize::MAX, 1)?;
            let mut v = Vec::<u8>::new();
            v.try_reserve_exact(usize::MAX)
                .map_err(|_| ModelError::new("allocation refused"))?;
            Ok(Self)
        }
    }
    assert!(
        Huge::from_trace(
            &[],
            DecodeLimits {
                elements: usize::MAX,
                allocation: usize::MAX,
                work: usize::MAX,
                ..Default::default()
            }
        )
        .is_err()
    );
}

#[test]
fn swallowed_reader_errors_never_become_successful_values() {
    struct Suppressed;
    impl TraceDecode for Suppressed {
        fn trace_decode(reader: &mut Decoder<'_>) -> Result<Self, ModelError> {
            let _ = reader.value::<bool>();
            Ok(Self)
        }
    }
    assert!(Suppressed::from_trace(&[2], Default::default()).is_err());
    assert!(Suppressed::from_trace(&[], Default::default()).is_err());
    let mut reader = Decoder::new(
        &[],
        DecodeLimits {
            elements: 0,
            ..Default::default()
        },
    )
    .unwrap();
    assert!(reader.collection(1, 0).is_err());
    assert!(reader.value::<()>().is_err());
    assert!(reader.finish().is_err());
}
