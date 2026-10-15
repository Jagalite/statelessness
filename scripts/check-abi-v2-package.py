#!/usr/bin/env python3
"""Check packaged v2 identities; runtime loading requires explicit --mode runtime.

The default inspect mode does not execute native/Wasm code. Runtime mode checks
bootstrap/loading only, not model correctness, portable parity, or performance.
"""
import argparse
import ctypes
import json
from pathlib import Path
import platform
import sys
import tempfile

from binding_evidence import sha256, source_snapshot, write_json


class LoadError(RuntimeError):
    pass


class WasmReader:
    """Read standard wasm32 import/export metadata without instantiating code."""
    def __init__(self, data):
        self.data, self.offset = data, 0

    def take(self, size):
        if size < 0 or size > len(self.data) - self.offset:
            raise ValueError("truncated Wasm section")
        value = self.data[self.offset:self.offset + size]
        self.offset += size
        return value

    def byte(self):
        return self.take(1)[0]

    def u32(self):
        value = 0
        for shift in range(0, 35, 7):
            byte = self.byte()
            if shift == 28 and byte > 15:
                raise ValueError("Wasm u32 overflow")
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError("invalid Wasm u32")

    def name(self):
        return self.take(self.u32()).decode("utf-8", "strict")

    def limits(self):
        flags = self.u32()
        if flags > 3:
            raise ValueError("unsupported non-wasm32 limits")
        self.u32()
        if flags & 1:
            self.u32()


def wasm_interface(data):
    reader = WasmReader(data)
    if reader.take(8) != b"\0asm\1\0\0\0":
        raise ValueError("invalid Wasm module header")
    imports, exports, seen = set(), set(), set()
    while reader.offset < len(data):
        kind, size = reader.byte(), reader.u32()
        section = WasmReader(reader.take(size))
        if kind not in (2, 7):
            continue
        if kind in seen:
            raise ValueError("duplicate Wasm interface section")
        seen.add(kind)
        count = section.u32()
        if count > 100_000:
            raise ValueError("oversized Wasm interface")
        for _ in range(count):
            if kind == 7:
                name = section.name(); external = section.byte(); section.u32()
                if external == 0:
                    exports.add(name)
            else:
                module, name, external = section.name(), section.name(), section.byte()
                imports.add((module, name))
                if external == 0:
                    section.u32()
                elif external == 1:
                    section.byte(); section.limits()
                elif external == 2:
                    section.limits()
                elif external == 3:
                    section.byte(); section.byte()
                elif external == 4:
                    section.byte(); section.u32()
                else:
                    raise ValueError("unknown Wasm import kind")
        if section.offset != len(section.data):
            raise ValueError("trailing Wasm interface bytes")
    return imports, exports


def inspect_wasm_pair(directory):
    legacy_imports, legacy_exports = wasm_interface((directory / "stateless.wasm").read_bytes())
    v2_imports, v2_exports = wasm_interface((directory / "stateless_v2.wasm").read_bytes())
    legacy_host = {("stateless_host", "dispatch")}
    if legacy_imports != legacy_host:
        raise ValueError("default Wasm imports changed; unchanged ABI 1 loader cannot instantiate it")
    if v2_imports != legacy_host | {("stateless_v2", "dispatch"), ("stateless_v2", "release")}:
        raise ValueError("v2 Wasm is missing its separate host-import transport")
    legacy_symbols = {"stateless_abi_version", "stateless_wasm_model_new", "stateless_enumerate", "stateless_record", "stateless_replay"}
    v2_symbols = {"stateless_v2_" + name for name in (
        "abi_version", "capabilities", "wasm_session_new", "session_close", "check_observed",
        "enumerate", "record_cursor_limited", "replay", "fuzz", "shrink", "trace_convert",
        "ring_new", "ring_observe", "ring_snapshot", "result_release",
    )}
    if not legacy_symbols.issubset(legacy_exports) or not (legacy_symbols | v2_symbols).issubset(v2_exports):
        raise ValueError("Wasm artifact lacks required versioned exports")


def checked_path(root, name):
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError(f"invalid package path: {name}")
    return path


def inspect_package(directory, expected_source=None):
    directory = Path(directory).resolve()
    metadata = json.loads((directory / "engine-build.json").read_text())
    if metadata.get("schema") != "stateless.engine-package.v1":
        raise ValueError("unsupported engine package metadata")
    if 2 not in metadata.get("declared_abi_versions", []):
        raise ValueError("this package was not built with opt-in ABI v2 sources")
    files = metadata.get("files", {})
    if not files:
        raise ValueError("package identity has no files")
    for name, expected in files.items():
        path = checked_path(directory, name)
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"packaged file missing or changed: {name}")
    actual = {path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file()}
    if actual != set(files) | {"engine-build.json"}:
        raise ValueError("package contains unrecorded files or its inventory is incomplete")
    if expected_source and metadata.get("source_sha256") != expected_source["sha256"]:
        raise ValueError("package belongs to a different source snapshot")
    if metadata.get("target") == "wasm32-unknown-unknown":
        required = {"stateless.wasm", "stateless_v2.wasm", "v2.mjs", "v2.d.ts", "v2.d.mts", "v2-values.mjs", "v2-packet.mjs", "v2-json.mjs", "package.json"}
        package = json.loads((directory / "package.json").read_text())
        if package.get("exports", {}).get("./v2") != {"types": "./v2.d.ts", "import": "./v2.mjs"}:
            raise ValueError("npm package lacks the explicit typed /v2 export")
    else:
        required = {"include/stateless.h", "include/stateless_v2.h"}
        if not any(name.endswith((".so", ".dylib", ".dll")) for name in files):
            raise ValueError("package lacks its native shared library")
    if not required.issubset(files):
        raise ValueError(f"package identity omits required files: {sorted(required - files.keys())}")
    if metadata.get("target") == "wasm32-unknown-unknown":
        inspect_wasm_pair(directory)
    return metadata


def bootstrap(library, expected_abi=2):
    try:
        v1 = library.stateless_abi_version
        v2 = library.stateless_v2_abi_version
        capabilities = library.stateless_v2_capabilities
    except AttributeError as error:
        raise LoadError("native library lacks required Stateless ABI symbols") from error
    v1.restype = ctypes.c_uint32
    v2.restype = ctypes.c_uint32
    capabilities.restype = ctypes.c_uint64
    v1.argtypes = v2.argtypes = capabilities.argtypes = []
    observed_v1, observed_v2 = v1(), v2()
    if observed_v1 != 1 or observed_v2 != expected_abi:
        raise LoadError(f"ABI version mismatch: expected 1/{expected_abi}, got {observed_v1}/{observed_v2}")
    mask = capabilities()
    if mask & 3 != 3:
        raise LoadError("engine lacks required checking/enumeration capabilities")
    return {"abi_1": observed_v1, "abi_2": observed_v2, "capabilities": mask}


def load_native(path, expected_sha, expected_abi=2):
    path = Path(path)
    if not path.is_file():
        raise LoadError(f"native library is missing: {path}")
    if sha256(path) != expected_sha:
        raise LoadError("native library content identity mismatch")
    try:
        library = ctypes.CDLL(str(path.resolve()))
    except OSError as error:
        raise LoadError(f"native library cannot load on this runtime: {error}") from error
    return bootstrap(library, expected_abi)


def expect_load_error(callback, text):
    try:
        callback()
    except LoadError as error:
        if text not in str(error):
            raise AssertionError(f"unexpected loading error: {error}") from error
        return
    raise AssertionError(f"expected rejected load: {text}")


def runtime_native(directory, metadata):
    files = metadata["files"]
    names = [name for name in files if name.endswith((".so", ".dylib", ".dll"))]
    if len(names) != 1:
        raise ValueError("expected exactly one native shared library")
    name = names[0]
    path = checked_path(directory, name)
    result = load_native(path, files[name])
    with tempfile.TemporaryDirectory(prefix="stateless-load-negative-") as temporary:
        root = Path(temporary)
        expect_load_error(lambda: load_native(root / path.name, files[name]), "is missing")
        corrupt = root / path.name
        corrupt.write_bytes(b"not the qualified native engine")
        expect_load_error(lambda: load_native(corrupt, files[name]), "content identity mismatch")
    expect_load_error(lambda: load_native(path, files[name], expected_abi=3), "ABI version mismatch")
    # The process/system library has no Stateless symbols. This verifies actual
    # missing-symbol handling without compiling or executing an invented engine.
    unrelated = ctypes.CDLL("kernel32.dll") if sys.platform == "win32" else ctypes.CDLL(None)
    expect_load_error(lambda: bootstrap(unrelated), "lacks required Stateless ABI symbols")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path("target/bindings/native"))
    parser.add_argument("--mode", choices=("inspect", "runtime"), default="inspect")
    parser.add_argument("--source-root", type=Path, help="require the package to match this checkout")
    parser.add_argument("--report", type=Path, default=Path("target/abi-v2/package-check.json"))
    args = parser.parse_args()
    report = {
        "schema": "stateless.binding-qualification.v1", "mode": args.mode, "language": "c",
        "platform": platform.platform(), "machine": platform.machine(),
        "gates": {name: {"status": "pending"} for name in (
            "package_integrity", "runtime_bootstrap", "bootstrap_negative_loading", "sdk_negative_loading",
            "installed_model_consumer", "portable_profiles", "fresh_process_replay",
            "semantic_regression", "lifetime", "abi1_compatibility", "deployment_contract", "performance", "migration_window",
        )},
        "default_switch_ready": False,
    }
    try:
        source = source_snapshot(args.source_root) if args.source_root else None
        metadata = inspect_package(args.package, source)
        report["language"] = "typescript" if metadata["target"] == "wasm32-unknown-unknown" else "c"
        report.update({"source_sha256": metadata["source_sha256"],
                       "source_commit": metadata["source_commit"], "target": metadata["target"],
                       "package_metadata_sha256": sha256(args.package / "engine-build.json"),
                       "package_files": metadata["files"]})
        report["gates"]["package_integrity"] = {"status": "passed"}
        if args.mode == "runtime":
            if metadata["target"] == "wasm32-unknown-unknown":
                raise ValueError("Wasm runtime qualification uses the installed browser/Node consumer; this checker never substitutes native loading")
            report["bootstrap"] = runtime_native(args.package, metadata)
            report["gates"]["runtime_bootstrap"] = {"status": "passed"}
            report["gates"]["bootstrap_negative_loading"] = {"status": "passed"}
        report["outcome"] = "passed_for_requested_scope"
    except Exception as error:
        report["outcome"] = "failed"
        report["error"] = str(error)
        write_json(args.report, report)
        raise SystemExit(str(error)) from error
    write_json(args.report, report)
    print(f"{args.mode} package checks passed; remaining runtime/migration gates are pending: {args.report}")


if __name__ == "__main__":
    main()
