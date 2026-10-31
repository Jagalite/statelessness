//! Actual public Rust checked-result and runtime recorder reference.
#[path = "../checked_reference.rs"]
mod checked_reference;
use serde_json::Value;
use std::path::Path;
fn run() -> Result<Value, String> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    match args.as_slice() {
        [] => checked_reference::qualify(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../conformance/checked-results-v1.json"),
        ),
        [mode, path] if mode == "--corpus" => checked_reference::qualify(Path::new(path)),
        [mode, path] if mode == "record" => checked_reference::record(path),
        [mode, path] if mode == "record-suffix" => checked_reference::record_suffix(path),
        [mode, path] if mode == "record-failing-suffix" => checked_reference::record_failing_suffix(path),
        [mode, path] if mode == "replay-suffix" => checked_reference::replay_suffix(Path::new(path)),
        [mode, path] if mode == "replay-suffix-json" => checked_reference::replay_suffix_json(Path::new(path)),
        [mode, path] if mode == "replay" => checked_reference::replay(Path::new(path)),
        [mode, path] if mode == "replay-json" => checked_reference::replay_json(Path::new(path)),
        _ => Err("expected [--corpus FILE]".into()),
    }
}
fn main() {
    match run() {
        Ok(value) => println!("{value}"),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1)
        }
    }
}
