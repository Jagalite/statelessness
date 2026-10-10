#!/usr/bin/env python3
"""Offline external-consumer qualification for Inspect metadata diagnostics."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FAILURES = {
    "union": ('union U { value: u8 }', "SM409"),
    "discriminant": ('enum E { A = 1 }', "SM411"),
    "data_discriminant": ('#[repr(u8)] enum E { A(u8) = 1 }', "SM411"),
    "unknown_type": ('#[inspect(magic)] struct S;', "SM403"),
    "unknown_field": ('struct S { #[inspect(skip)] value: u8 }', "SM403"),
    "unknown_variant": ('enum E { #[inspect(redact)] A }', "SM403"),
    "duplicate_type_option": ('#[inspect(version=1, version=2)] struct S;', "SM402"),
    "duplicate_attribute_option": ('#[inspect(label="a")] #[inspect(label="b")] struct S;', "SM402"),
    "duplicate_field_option": ('struct S { #[inspect(redact,redact)] value: u8 }', "SM402"),
    "duplicate_variant_option": ('enum E { #[inspect(id="a",id="b")] A }', "SM402"),
    "duplicate_field_id": ('struct S { #[inspect(id="same")] a: u8, #[inspect(id="same")] b: u8 }', "SM408"),
    "duplicate_default_field_id": ('struct S { #[inspect(id="b")] a: u8, b: u8 }', "SM408"),
    "duplicate_escaped_field_id": (r'struct S { #[inspect(id="same")] a: u8, #[inspect(id="s\x61me")] b: u8 }', "SM408"),
    "duplicate_raw_field_id": ('struct S { #[inspect(id=r#"same"#)] a: u8, #[inspect(id="same")] b: u8 }', "SM408"),
    "duplicate_variant_id": ('enum E { #[inspect(id="same")] A, #[inspect(id="same")] B }', "SM412"),
    "duplicate_default_variant_id": ('enum E { #[inspect(id="B")] A, B }', "SM412"),
    "empty_field_id": ('struct S { #[inspect(id="")] a: u8 }', "SM405"),
    "bare_attribute": ('#[inspect] struct S;', "SM400"),
    "equals_attribute": ('#[inspect="oops"] struct S;', "SM400"),
    "wrong_group": ('#[inspect[label="oops"]] struct S;', "SM400"),
    "empty_option": ('#[inspect(label="a",,)] struct S;', "SM400"),
    "leading_comma": ('#[inspect(,label="a")] struct S;', "SM400"),
    "missing_value": ('#[inspect(version)] struct S;', "SM400"),
    "bool_redact": ('struct S { #[inspect(redact=true)] value: u8 }', "SM400"),
    "non_string_label": ('#[inspect(label=42)] struct S;', "SM401"),
    "byte_string_label": ('#[inspect(label=b"bytes")] struct S;', "SM401"),
    "string_version": ('#[inspect(version="1")] struct S;', "SM404"),
    "overflow_version": ('#[inspect(version=4294967296)] struct S;', "SM404"),
    "float_version": ('#[inspect(version=1.0)] struct S;', "SM404"),
    "invalid_runtime": ('#[inspect(crate="runtime::<u8>")] struct S;', "SM406"),
    "empty_runtime": ('#[inspect(crate="")] struct S;', "SM406"),
    "runtime_injection": ('#[inspect(crate="runtime; panic!()")] struct S;', "SM406"),
}


def check(command, cwd, env, expected=None):
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True)
    output = result.stdout + result.stderr
    if expected is None:
        assert result.returncode == 0, output
    else:
        assert result.returncode != 0 and expected in output, output
        assert "proc-macro derive panicked" not in output, output


def consumer_manifest(root):
    """Quote paths as TOML basic strings, including Windows slashes and quotes."""
    macros = json.dumps(str(root / "crates/statelessness-macros"), ensure_ascii=False)
    display = json.dumps(str(root / "crates/statelessness-debug"), ensure_ascii=False)
    return (
        '[package]\nname="inspect-consumer"\nversion="0.0.0"\nedition="2024"\n'
        '[dependencies]\n'
        f'macros={{package="statelessness-macros",path={macros}}}\n'
        f'display={{package="statelessness-debug",path={display}}}\n'
    )


def main():
    with tempfile.TemporaryDirectory(prefix="inspect-qualification-") as directory:
        consumer = Path(directory)
        (consumer / "src").mkdir()
        (consumer / "Cargo.toml").write_text(consumer_manifest(ROOT), encoding="utf-8")
        env = {**os.environ, "CARGO_TARGET_DIR": str(consumer / "target"), "CARGO_HOME": str(consumer / "empty-cargo-home")}
        source = consumer / "src/main.rs"
        source.write_text('''
    use macros::Inspect;
    use display::inspect::{Inspect as _, inspect, InspectQuery, NodeKind};
    struct Secret;
    #[derive(Inspect)]
    #[inspect(crate="::display", label="Renamed consumer")]
    struct View<T> { value:u128, #[inspect(redact)] secret:T }
    fn main() {
        let state = View { value: u128::MAX, secret: Secret };
        let result = inspect(&state, &InspectQuery::default()).unwrap();
        assert_eq!(state.schema().name, "Renamed consumer");
        assert!(matches!(result.node.children[1].node.kind, NodeKind::Redacted));
    }
    ''')
        check(["cargo", "run", "--offline", "--quiet"], consumer, env)
        print("PASS cold offline renamed external consumer")
        for name, (fixture, diagnostic) in FAILURES.items():
            source.write_text("use macros::Inspect;\n#[derive(Inspect)]\n" + fixture + "\nfn main() {}\n")
            check(["cargo", "check", "--offline", "--quiet"], consumer, env, diagnostic)
            print("PASS", name, diagnostic)
    print(f"Inspect qualification passed: renamed consumer and {len(FAILURES)} diagnostic fixtures")


if __name__ == "__main__":
    main()
