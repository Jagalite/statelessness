#!/usr/bin/env python3
"""Render the reviewed source capability matrix; never build or execute Rust.

Versions come from manifests/protocol declarations. Capability and qualification
scope are reviewed declarations below, not inferred test success. Registry facts
come from an explicitly dated receipt and are never refreshed during this check.
"""

import argparse
import json
from pathlib import Path
import re
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT = ROOT / "docs/CAPABILITIES.md"
START = "<!-- BEGIN GENERATED CAPABILITIES -->"
END = "<!-- END GENERATED CAPABILITIES -->"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def value(path, pattern):
    match = re.search(pattern, read(path), re.MULTILINE)
    if match is None:
        raise ValueError(f"missing version declaration in {path}")
    return match.group(1)


def cargo_version(path):
    return tomllib.loads(read(path))["package"]["version"]


def link(label, path):
    if not (ROOT / path).is_file():
        raise ValueError(f"missing capability evidence/source: {path}")
    return f"[{label}](../{path})"


def render():
    core = cargo_version("Cargo.toml")
    macros = cargo_version("crates/statelessness-macros/Cargo.toml")
    debugger = cargo_version("crates/statelessness-debug/Cargo.toml")
    python = tomllib.loads(read("python/pyproject.toml"))["project"]["version"]
    typescript = json.loads(read("typescript/package.json"))["version"]
    go = value("go/model.go", r'^const Version = "([^"]+)"')
    swift = value("swift/Sources/Statelessness/Model.swift", r'^public let statelessnessVersion = "([^"]+)"')
    declarations = [
        (python, "python/src/statelessness/__init__.py", r'^__version__ = "([^"]+)"'),
        (typescript, "typescript/src/core.ts", r"^export const VERSION = '([^']+)'"),
    ]
    for expected, path, pattern in declarations:
        if value(path, pattern) != expected:
            raise ValueError(f"package/runtime version mismatch in {path}")
    spec = value("spec/README.md", r'^# Statelessness portable specification ([0-9.]+)$')
    for path, pattern in [
        ("python/src/statelessness/__init__.py", r'^SPEC_VERSION = "([^"]+)"'),
        ("typescript/src/core.ts", r"^export const SPEC_VERSION = '([^']+)'"),
        ("go/model.go", r'^const SpecVersion = "([^"]+)"'),
        ("swift/Sources/Statelessness/Model.swift", r'^public let specificationVersion = "([^"]+)"'),
    ]:
        if value(path, pattern) != spec:
            raise ValueError(f"portable specification version mismatch in {path}")

    receipt = json.loads(read("validation/releases/2026-10-12-registry.json"))
    released = {}
    for crate in receipt["crates"]:
        if crate["http_status"] == 200:
            if crate["archive_sha256"] != crate["registry_checksum"]:
                raise ValueError("registry receipt checksum mismatch")
            status = "yanked" if crate["yanked"] else "unyanked"
            released[crate["name"]] = f"Registry {crate['version']} ({status}); older source snapshot"
        else:
            released[crate["name"]] = "Absent from crates.io at receipt date; use local path"

    historical = link("Historical core/binding evidence", "SUPPORT.md")
    native = link("Historical native qualification", "spec/NATIVE-PORTS-QUALIFICATION.md")
    abi_scope = "ABI 1 byte-model record/replay/BFS; Rust binary traces"
    rows = [
        (link("Rust core / CLI", "Cargo.toml"), core, "Checking, assurance catalogs/coverage, BFS, fuzz/shrink/campaigns, lifecycle retention, explicit multi-machine networks, v2 evidence envelope; reference host/fault controls; CLI runs bundled fixture", historical, released["statelessness"]),
        (link("Rust macros", "crates/statelessness-macros/Cargo.toml"), macros, "Model/domain/codec/inspection derives, default initial state, opt-in bounded generation, typed property metadata; explicit syntax/protocol", link("Historical macro evidence", "docs/MACRO-STATUS.md"), released["statelessness-macros"] + "; inspection derives are newer"),
        (link("Rust debugger", "crates/statelessness-debug/Cargo.toml"), debugger, "Sessions, exact replay/forks, inspection/watches/probes, stdio, cooperative live control, offline v2 evidence/phase minimization and passive diagnostic reports", link("Historical debugger acceptance", "docs/debugger/ACCEPTANCE.md"), released["statelessness-debug"]),
        (link("Native Python", "python/README.md"), python, "Four portable v1 profiles; candidate paired-v1 oracles/projections, search-v1 generation/fuzz/shrink, and native-binary-v1 lossless audit codecs, and bfs-controls-v1 controls/estimates/observations, and append-checks-v1 storage (unqualified); independent engine", link("Historical Python qualification", "spec/QUALIFICATION.md"), "Source/wheel instructions; registry availability not verified"),
        (link("Native Go", "go/README.md"), go, "Four portable v1 profiles; candidate paired-v1 oracles/projections, search-v1 generation/fuzz/shrink, and native-binary-v1 lossless audit codecs, and bfs-controls-v1 controls/estimates/observations, and append-checks-v1 storage (unqualified); no Rust/cgo", native, "Local module replace; module release not verified"),
        (link("Native TypeScript", "typescript/README.md"), typescript, "Four portable v1 profiles; candidate paired-v1 oracles/projections, search-v1 generation/fuzz/shrink, and native-binary-v1 lossless audit codecs, and bfs-controls-v1 controls/estimates/observations, and append-checks-v1 storage (unqualified); independent ESM engine", native, "Local npm tarball; npm availability not verified"),
        (link("Native Swift", "swift/README.md"), swift, "Four portable v1 profiles; candidate paired-v1 oracles/projections, search-v1 generation/fuzz/shrink, and native-binary-v1 lossless audit codecs, and bfs-controls-v1 controls/estimates/observations, and append-checks-v1 storage (unqualified); root SwiftPM package", native, "Local source package; registry/binary release not verified"),
        (link("Rust-backed C", "bindings/c/README.md"), f"{core} / ABI 1", abi_scope, historical, "Build matching native library locally"),
        (link("Rust-backed Swift", "bindings/swift/README.md"), f"{core} / ABI 1", abi_scope + "; StatelessNative product", historical, "Generated local SwiftPM package + native library"),
        (link("Rust-backed JavaScript", "bindings/browser/README.md"), f"{core} / ABI 1", abi_scope + "; Wasm engine", historical, "Generated local npm package; version comes from core manifest"),
        (link("Rust-backed Go SDK", "bindings/go/README.md"), "ABI 1; local module", abi_scope + "; scoped synchronous cgo sessions", link("Historical Go bridge qualification", "docs/GO-CONFORMANCE-VALIDATION.md"), "Local module + matching native engine"),
        (link("Macro qualification crate", "crates/macro-qualification/Cargo.toml"), cargo_version("crates/macro-qualification/Cargo.toml"), "Modeling fixtures and measurements", link("Historical macro evidence", "docs/MACRO-STATUS.md"), "publish = false"),
        (link("Rust portable corpus runner", "validation/corpus/Cargo.toml"), cargo_version("validation/corpus/Cargo.toml"), "Four-profile adapter to the Rust engine; separate serde dependencies", link("Portable contract", "spec/README.md"), "publish = false"),
        (link("Rust/Go jobs example bridge", "examples/go-jobs/Cargo.toml"), cargo_version("examples/go-jobs/Cargo.toml"), "Paired enumeration/shrinking/replay for the bounded job fixture", link("Historical Go bridge qualification", "docs/GO-CONFORMANCE-VALIDATION.md"), "publish = false"),
    ]
    lines = [
        "| Component | Source version | Implemented source scope (experimental) | Qualification evidence | Distribution status |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    lines += [
        "", "### Independently versioned contracts", "",
        "| Contract | Current declaration | Source |",
        "| --- | --- | --- |",
    ]
    protocols = [
        ("Rust binary trace", value("src/trace.rs", r'pub const FORMAT_VERSION: u16 = ([0-9]+);'), "src/trace.rs"),
        ("Causal evidence envelope", value("src/evidence.rs", r'pub const FORMAT_VERSION: u16 = ([0-9]+);'), "src/evidence.rs"),
        ("Diagnostic export", "statelessness.diagnostic-export/1; non-replayable", "spec/diagnostic-export.schema.json"),
        ("Foreign callback ABI", value("src/ffi.rs", r'pub const ABI_VERSION: u32 = ([0-9]+);'), "src/ffi.rs"),
        ("Macro/runtime protocol marker", ", ".join(re.findall(r"pub const (MACRO_API_V[0-9]+):", read("src/modeling.rs"))), "src/modeling.rs"),
        ("Debugger stdio protocol", value("crates/statelessness-debug/src/protocol.rs", r'pub const PROTOCOL_VERSION: u32 = ([0-9]+);'), "crates/statelessness-debug/src/protocol.rs"),
        ("Portable specification", spec, "spec/README.md"),
        ("Portable runner protocol", value("validation/corpus/src/main.rs", r'"protocol_version":\s*([0-9]+)'), "validation/corpus/src/main.rs"),
        ("Portable fixture", str(json.loads(read("conformance/corpus.json"))["corpus_version"]), "conformance/corpus.json"),
        ("Assurance manifest/report", value("src/assurance.rs", r'pub const ASSURANCE_VERSION: u32 = ([0-9]+);'), "src/assurance.rs"),
        ("Portable paired profile", "paired-v1 candidate; not a promoted portable profile", "spec/paired-v1.md"),
        ("Native search profile", "search-v1 candidate; not a promoted portable profile", "spec/search-v1.md"),
        ("Native BFS controls", "bfs-controls-v1 candidate; not a promoted portable profile", "spec/bfs-controls-v1.md"),
        ("Native append checking", "append-checks-v1 candidate; not a promoted portable profile", "spec/append-checks-v1.md"),
        ("Native binary interoperability", "native-binary-v1 candidate; Rust audit format 1; not promoted", "spec/native-binary-v1.md"),
        ("Reference requirements", str(json.loads(read("docs/requirements/controller.json"))["schema_version"]), "docs/requirements/controller.json"),
    ]
    lines.extend(f"| {name} | `{version}` | {link(path, path)} |" for name, version, path in protocols)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="update generated sections; otherwise check")
    args = parser.parse_args()
    original = DOCUMENT.read_text(encoding="utf-8")
    before, middle = original.split(START, 1)
    _, after = middle.split(END, 1)
    expected = before + START + "\n\n" + render() + "\n\n" + END + after
    if args.write:
        DOCUMENT.write_text(expected, encoding="utf-8")
    elif expected != original:
        print("capability matrix is stale; run python3 scripts/check-capabilities.py --write", file=sys.stderr)
        return 1
    print("capability matrix matches source declarations (no runtime qualification)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
