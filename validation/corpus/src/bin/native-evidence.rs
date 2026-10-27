//! Public Rust causal evidence-v2 reference and installed application consumer.
#[path = "../evidence_reference.rs"]
mod evidence_reference;
use std::path::Path;
fn run() -> Result<serde_json::Value, String> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    match args.as_slice() {
        [] => evidence_reference::qualify(
            &std::env::var_os("STATELESS_EVIDENCE_CORPUS")
                .map(std::path::PathBuf::from)
                .unwrap_or_else(|| {
                    Path::new(env!("CARGO_MANIFEST_DIR"))
                        .join("../../conformance/causal-evidence-v2.json")
                }),
        ),
        [mode, path] if mode == "--corpus" => evidence_reference::qualify(Path::new(path)),
        [mode, prefix] if mode == "record" => evidence_reference::record(prefix),
        [mode, input] if mode == "read-v1" => evidence_reference::read_v1(Path::new(input)),
        [mode, input, output] if mode == "replay" => {
            evidence_reference::replay(Path::new(input), Path::new(output))
        }
        [mode, input, output] if mode == "upgrade" => {
            evidence_reference::upgrade(Path::new(input), Path::new(output))
        }
        [mode, input, output] if mode == "diagnostic" => {
            evidence_reference::diagnostic(Path::new(input), Path::new(output))
        }
        _ => Err("expected [--corpus FILE]".into()),
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
