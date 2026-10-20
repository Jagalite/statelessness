//! Public Rust canonical value-codec qualification.
#[path = "../values_reference.rs"]
mod values_reference;
use std::path::{Path, PathBuf};
fn run() -> Result<serde_json::Value, String> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let default = std::env::var_os("STATELESS_VALUE_CORPUS")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../conformance/canonical-value-codec-v1.json")
        });
    match args.as_slice() {
        [] => values_reference::qualify(&default),
        [mode, path] if mode == "--corpus" => values_reference::qualify(Path::new(path)),
        [mode] if mode == "--layouts" => values_reference::layout_receipt(&default),
        [mode, path] if mode == "--layouts" => values_reference::layout_receipt(Path::new(path)),
        [mode, output] if mode == "values" => values_reference::values(Path::new(output)),
        [mode, input, output] if mode == "transcode" => {
            values_reference::transcode(Path::new(input), Path::new(output))
        }
        [mode, prefix] if mode == "record" => values_reference::record(prefix),
        [mode, input, output] if mode == "replay" => {
            values_reference::replay(Path::new(input), Path::new(output))
        }
        _ => Err("expected [--corpus FILE | --layouts [FILE]]".into()),
    }
}
fn main() {
    match run() {
        Ok(v) => println!("{v}"),
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(1)
        }
    }
}
