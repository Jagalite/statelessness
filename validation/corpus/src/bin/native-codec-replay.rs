//! Isolated public-Rust codec/replay qualification; no core JSON dependencies.
#[path = "../codec_consumer_reference.rs"]
mod codec_consumer_reference;
#[path = "../encode_reference.rs"]
mod encode_reference;
#[path = "../replay_reference.rs"]
mod replay_reference;
use serde_json::{Value, json};
use std::path::Path;
fn run() -> Result<Value, String> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    match args.as_slice() {
        [] => Ok(json!({"encode":encode_reference::qualify(&root.join("conformance/encode-buffers-v1.json"))?,"replay":replay_reference::qualify(&root.join("conformance/replay-evidence-v1.json"))?})),
        [mode,path] if mode=="--encode-corpus" => encode_reference::qualify(Path::new(path)),
        [mode,path] if mode=="--replay-corpus" => replay_reference::qualify(Path::new(path)),
        [mode,path] if mode=="record" => codec_consumer_reference::record(path),
        [mode,path] if mode=="replay" => codec_consumer_reference::replay(Path::new(path)),
        [mode,path] if mode=="replay-json" => codec_consumer_reference::replay_json(Path::new(path)),
        _=>Err("usage: native-codec-replay [--encode-corpus PATH | --replay-corpus PATH | record PREFIX | replay AUDIT_BIN | replay-json PORTABLE_JSON]".into()),
    }
}
fn main() {
    match run() {
        Ok(receipt) => println!("{receipt}"),
        Err(error) => {
            eprintln!("native-codec-replay: {error}");
            std::process::exit(1)
        }
    }
}
