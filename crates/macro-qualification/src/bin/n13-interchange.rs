//! Fresh-process finite interchange authority. This does not implement a dynamic codec.
use stateless::value_codec::{TraceDecode as _, TraceEncode as _};
use statelessness_macros::{TraceDecode, TraceEncode};
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
struct Point {
    x: u32,
    y: String,
}
#[derive(Debug, PartialEq, TraceEncode, TraceDecode)]
enum Event {
    #[trace(tag = 0)]
    Empty,
    #[trace(tag = 1)]
    Point { x: u32, y: String },
}
fn main() {
    let point = Point {
        x: 7,
        y: "hi".into(),
    };
    let event = Event::Point {
        x: 7,
        y: "hi".into(),
    };
    // Optional finite consumer interface: <fixture> <hex>, for fresh native producers.
    let args: Vec<String> = std::env::args().skip(1).collect();
    if !args.is_empty() {
        assert_eq!(args.len(), 2);
        assert_eq!(args[1].len() % 2, 0);
        let bytes: Vec<u8> = args[1]
            .as_bytes()
            .chunks(2)
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect();
        match args[0].as_str() {
            "Point" => assert_eq!(
                Point::from_trace(&bytes, Default::default()).unwrap(),
                point
            ),
            "Empty" => assert_eq!(
                Event::from_trace(&bytes, Default::default()).unwrap(),
                Event::Empty
            ),
            "EventPoint" => assert_eq!(
                Event::from_trace(&bytes, Default::default()).unwrap(),
                event
            ),
            _ => panic!("unknown finite fixture"),
        }
        println!("consumed {}", args[0]);
        return;
    }
    for (name, bytes) in [
        ("Point", point.trace_bytes(64).unwrap()),
        ("Empty", Event::Empty.trace_bytes(64).unwrap()),
        ("EventPoint", event.trace_bytes(64).unwrap()),
    ] {
        let hex: String = bytes.iter().map(|v| format!("{v:02x}")).collect();
        println!("{name} {hex}");
    }
    assert_eq!(
        point.trace_bytes(64).unwrap(),
        [7, 0, 0, 0, 2, 0, 0, 0, 104, 105]
    );
    assert_eq!(
        Point::from_trace(&[7, 0, 0, 0, 2, 0, 0, 0, 104, 105], Default::default()).unwrap(),
        point
    );
    assert_eq!(
        Event::from_trace(&[0], Default::default()).unwrap(),
        Event::Empty
    );
    assert_eq!(
        Event::from_trace(&[1, 7, 0, 0, 0, 2, 0, 0, 0, 104, 105], Default::default()).unwrap(),
        event
    );
}
