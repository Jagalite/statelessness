//! Public Rust finite-fixture equivalence qualification.
#[path = "../research_reference.rs"]
mod research_reference;
use std::path::{Path, PathBuf};
fn run() -> Result<serde_json::Value, String> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let default = std::env::var_os("STATELESS_RESEARCH_CORPUS")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../conformance/research-equivalence-v1.json")
        });
    match args.as_slice() {
        [] => research_reference::qualify(&default),
        [mode, path] if mode == "--corpus" => research_reference::qualify(Path::new(path)),
        [mode, input, output] if mode == "run" => {
            research_reference::run(Path::new(input), Path::new(output))
        }
        _ => Err("expected [--corpus FILE | run CORPUS OUTPUT]".into()),
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
