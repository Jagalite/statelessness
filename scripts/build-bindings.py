#!/usr/bin/env python3
"""Build/stage local ABI distributions. ABI v2 is opt-in; never publishes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

from binding_evidence import build_receipt, same_source, sha256, source_snapshot, validate_receipt, write_json

ROOT = Path(__file__).resolve().parents[1]
SWIFT_FILES = (
    "Package.swift", "c/stateless.h", "c/module.modulemap", "swift/Counter.swift",
    "swift/README.md", "swift/Sources/StatelessNative.swift", "swift/Tests/NativeTests.swift",
)
PYTHON_FILES = (
    "pyproject.toml", "LICENSE", "README.md",
    "src/statelessness/__init__.py", "src/statelessness/core.py",
    "src/statelessness/trace_json.py", "src/statelessness/conformance.py",
    "src/statelessness/py.typed", "src/statelessness/native.py",
    "src/statelessness/_native_packet.py",
    "src/statelessness/native_conformance.py", "src/statelessness/oracle.py",
)
BROWSER_V2_FILES = ("v2.mjs", "v2.d.ts", "v2.d.mts", "v2-values.mjs", "v2-packet.mjs", "v2-json.mjs")
SWIFT_V2_FILES = (
    "Package.swift", "README.md", "c/module.modulemap", "c/shim.h",
    "Sources/Native.swift", "Sources/Packet.swift", "Tests/NativeV2Tests.swift", "Corpus/main.swift", "Benchmarks/main.swift",
)
PORTABLE_SWIFT_FILES = (
    "swift/Sources/Statelessness/Model.swift", "swift/Sources/Statelessness/Explore.swift",
    "swift/Sources/Statelessness/Execution.swift", "swift/Sources/Statelessness/Rng.swift",
    "swift/Sources/Statelessness/JSON.swift", "swift/Sources/Statelessness/TraceJSON.swift",
    "swift/Sources/StatelessConformance/Conformance.swift",
)
PORTABLE_SWIFT_MANIFEST = '''// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "Statelessness", platforms: [.macOS(.v11)],
    products: [.library(name: "Statelessness", targets: ["Statelessness"]),
               .library(name: "StatelessConformance", targets: ["StatelessConformance"])],
    targets: [.target(name: "Statelessness", path: "swift/Sources/Statelessness"),
              .target(name: "StatelessConformance", dependencies: ["Statelessness"], path: "swift/Sources/StatelessConformance")])
'''
GO_V2_FILES = (
    "README.md", "model.go", "session.go", "operations.go", "portable.go",
    "trace_types.go", "trace_json.go", "packet.go", "bridge.h", "bridge.c",
    "session_test.go", "packet_test.go", "benchmark_test.go", "internal/portablejson/json.go",
    "internal/portablejson/json_test.go", "conformance/runner.go",
    "cmd/stateless-corpus/main.go",
)


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def stage_sources(root, stage, kind, version, abi_v2=False):
    """Explicit public file lists: local notes/caches must never enter a bundle."""
    components = []
    if kind in ("all", "native"):
        components += ["native", "swift"]
        for name in ("stateless.h", "module.modulemap"):
            copy(root / "bindings/c" / name, stage / "native/include" / name)
        copy(root / "LICENSE", stage / "native/LICENSE")
        copy(root / "bindings/c/README.md", stage / "native/README.md")
        copy(root / "bindings/README.md", stage / "native/ABI.md")
        for name in SWIFT_FILES:
            copy(root / "bindings" / name, stage / "swift" / name)
        copy(root / "LICENSE", stage / "swift/LICENSE")
        copy(root / "bindings/swift/README.md", stage / "swift/README.md")
        if abi_v2:
            copy(root / "bindings/c/stateless_v2.h", stage / "native/include/stateless_v2.h")
            copy(root / "docs/ABI-V2.md", stage / "native/ABI-V2.md")
            components += ["python", "swift-v2", "portable-swift", "go-v2"]
            for name in PYTHON_FILES:
                copy(root / "python" / name, stage / "python" / name)
            for name in SWIFT_V2_FILES:
                copy(root / "bindings/swift-v2" / name, stage / "swift-v2" / name)
            copy(root / "LICENSE", stage / "swift-v2/LICENSE")
            copy(root / "bindings/c/stateless_v2.h", stage / "swift-v2/c/stateless_v2.h")
            (stage / "swift-v2/c/shim.h").write_text('#include "stateless_v2.h"\n')
            manifest = stage / "swift-v2/Package.swift"
            manifest.write_text(manifest.read_text().replace('path: "../.."', 'path: "../portable-swift"'))
            for name in PORTABLE_SWIFT_FILES:
                copy(root / name, stage / "portable-swift" / name)
            copy(root / "LICENSE", stage / "portable-swift/LICENSE")
            (stage / "portable-swift/Package.swift").write_text(PORTABLE_SWIFT_MANIFEST)
            for name in GO_V2_FILES:
                copy(root / "bindings/go/v2" / name, stage / "go-v2/v2" / name)
            copy(root / "bindings/go/go.mod", stage / "go-v2/go.mod")
            copy(root / "LICENSE", stage / "go-v2/LICENSE")
            copy(root / "bindings/c/stateless_v2.h", stage / "go-v2/include/stateless_v2.h")
            bridge = stage / "go-v2/v2/bridge.h"
            bridge.write_text(bridge.read_text().replace('"../../c/stateless_v2.h"', '"../include/stateless_v2.h"'))
    if kind in ("all", "browser"):
        components += ["browser", "browser-fixture"]
        for name in ("stateless.mjs", "package.json", "README.md"):
            copy(root / "bindings/browser" / name, stage / "browser" / name)
        # A release bump must change the actual package identity, not only its
        # hash manifest. The source manifest supplies npm settings, not version.
        package = stage / "browser/package.json"
        metadata = json.loads(package.read_text())
        metadata["version"] = version
        if abi_v2:
            for name in BROWSER_V2_FILES:
                copy(root / "bindings/browser" / name, stage / "browser" / name)
            metadata.setdefault("exports", {})["./v2"] = {
                "types": "./v2.d.ts", "import": "./v2.mjs",
            }
            metadata["exports"]["./stateless_v2.wasm"] = "./stateless_v2.wasm"
            for name in (*BROWSER_V2_FILES, "stateless_v2.wasm"):
                if name not in metadata.setdefault("files", []):
                    metadata["files"].append(name)
        metadata.setdefault("exports", {})["./engine-build.json"] = "./engine-build.json"
        if "engine-build.json" not in metadata.setdefault("files", []):
            metadata["files"].append("engine-build.json")
        package.write_text(json.dumps(metadata, indent=2) + "\n")
        copy(root / "LICENSE", stage / "browser/LICENSE")
        for name in ("adapter.mjs", "stateless.mjs", "index.html", "self-test.html"):
            copy(root / "bindings/browser" / name, stage / "browser-fixture" / name)
        for name in ("index.html", "self-test.html"):
            html = stage / "browser-fixture" / name
            html.write_text(html.read_text().replace(
                "../../target/wasm32-unknown-unknown/release/stateless.wasm", "../browser/stateless.wasm"))
    return components


def publish(stage, destination, components, kind, version, host, evidence=None):
    """Replace only selected generated bundles, restoring the old set on error."""
    manifest_name = f"manifest-{kind}.json"
    manifest = {"version": version, "host": host, "components": components, "files": {
        p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for component in components for p in sorted((stage / component).rglob("*")) if p.is_file()
    }}
    if evidence is not None:
        manifest["schema"] = "stateless.binding-package.v2"
        manifest["build"] = evidence
    (stage / manifest_name).write_text(json.dumps(manifest, indent=2) + "\n")
    destination.mkdir(parents=True, exist_ok=True)
    backup = stage / "previous"
    backup.mkdir()
    moved = []
    installed = []
    obsolete = set()
    if kind in ("all", "native"):
        for name in ("manifest-all.json", "manifest-native.json"):
            previous = destination / name
            if previous.is_file():
                try:
                    prior_components = json.loads(previous.read_text()).get("components", [])
                except (ValueError, AttributeError):
                    continue
                # Only directories explicitly managed by the previous manifest
                # are retired; arbitrary local directories are never inferred.
                obsolete.update(set(prior_components) & {"python", "swift-v2", "portable-swift", "go-v2"} - set(components))
    try:
        # Invalidate successful-run manifests whose files are being replaced.
        # A browser-only rebuild preserves a native-only manifest and vice versa.
        for name in ("manifest-all.json", manifest_name):
            if name not in moved and (destination / name).exists():
                (destination / name).rename(backup / name)
                moved.append(name)
        if kind == "all":
            for name in ("manifest-native.json", "manifest-browser.json"):
                if (destination / name).exists():
                    (destination / name).rename(backup / name)
                    moved.append(name)
        for name in sorted(obsolete):
            if (destination / name).exists():
                (destination / name).rename(backup / name)
                moved.append(name)
        for name in components:
            if (destination / name).exists():
                (destination / name).rename(backup / name)
                moved.append(name)
            (stage / name).rename(destination / name)
            installed.append(name)
        (stage / manifest_name).rename(destination / manifest_name)
        installed.append(manifest_name)
    except BaseException:
        for name in reversed(installed):
            (destination / name).rename(stage / name)
        for name in reversed(moved):
            (backup / name).rename(destination / name)
        raise


def native_libraries(target):
    if "apple-darwin" in target:
        return ("libstateless.a", "libstateless.dylib")
    if "linux" in target:
        return ("libstateless.a", "libstateless.so")
    if "windows-msvc" in target:
        return ("stateless.lib", "stateless.dll", "stateless.dll.lib")
    if "windows-gnu" in target:
        return ("libstateless.a", "stateless.dll", "libstateless.dll.a")
    raise ValueError(f"native distribution target unsupported: {target}")


def engine_metadata(stage, kind, version, receipt, abi_v2):
    """Each binary package can be checked without access to the build machine."""
    for component in ("native", "browser"):
        if kind not in ("all", component):
            continue
        files = {
            path.relative_to(stage / component).as_posix(): sha256(path)
            for path in sorted((stage / component).rglob("*"))
            if path.is_file()
        }
        write_json(stage / component / "engine-build.json", {
            "schema": "stateless.engine-package.v1",
            "package_version": version,
            "source_commit": receipt["source"]["commit"],
            "source_sha256": receipt["source"]["sha256"],
            "target": receipt["native_target"] if component == "native" else "wasm32-unknown-unknown",
            "toolchain": receipt["toolchain"],
            "declared_abi_versions": [1, 2] if abi_v2 else [1],
            "files": files,
            "qualification": {"packaging": "hashed", "runtime": "not_run", "performance": "not_run"},
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=["all", "native", "browser"], default="all")
    parser.add_argument("--abi-v2", action="store_true", help="stage opt-in v2 headers/wrappers beside ABI 1")
    parser.add_argument("--target-dir", type=Path, default=ROOT / "target")
    parser.add_argument("--output", type=Path, default=ROOT / "target/bindings")
    parser.add_argument("--toolchain", help="select explicit rustup toolchain for direct builds")
    parser.add_argument("--build-receipt", type=Path,
                        help="stage verified prebuilt outputs; invokes no Cargo/rustc")
    args = parser.parse_args()
    if args.build_receipt and args.toolchain:
        parser.error("--toolchain selects direct builds; prebuilt builds use the receipt's actual toolchain")
    target = args.target_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    destination = args.output.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock = destination.parent / ("." + destination.name + "-build.lock")
    try:
        lock.mkdir()
    except FileExistsError:
        raise SystemExit(f"binding build already locked: {lock}; verify no build is active before removing a stale lock")
    try:
        version = tomllib.loads((ROOT / "Cargo.toml").read_text())["package"]["version"]
        source = source_snapshot(ROOT)
        if args.build_receipt:
            receipt = json.loads(args.build_receipt.read_text())
            native_target = receipt["native_target"]
        else:
            def tool(name):
                if args.toolchain:
                    return subprocess.check_output(["rustup", "which", "--toolchain", args.toolchain, name], text=True).strip()
                selected = os.environ.get(name.upper(), name)
                resolved = shutil.which(selected)
                if resolved is None:
                    raise ValueError(f"build tool is unavailable: {selected}")
                return resolved
            rustc, cargo = tool("rustc"), tool("cargo")
            rustc_version = subprocess.check_output([rustc, "-vV"], text=True)
            cargo_version = subprocess.check_output([cargo, "-V"], text=True)
            native_target = next(line.removeprefix("host: ") for line in rustc_version.splitlines() if line.startswith("host: "))
            environment = {**os.environ, "CARGO_TARGET_DIR": str(target), "RUSTC": rustc}
            command = [cargo, "build", "--locked", "--offline", "--release", "--lib"]
        # Staging and replacement share a filesystem even with an external build cache.
        with tempfile.TemporaryDirectory(prefix=".bindings-stage-", dir=destination.parent) as temporary:
            stage = Path(temporary)
            components = stage_sources(ROOT, stage, args.kind, version, args.abi_v2)
            required = []
            if args.kind in ("all", "native"):
                required += ["native/lib/" + name for name in native_libraries(native_target)]
            if args.kind in ("all", "browser"):
                required.append("browser/stateless.wasm")
                if args.abi_v2:
                    required.append("browser/stateless_v2.wasm")
            if args.build_receipt:
                artifacts = validate_receipt(receipt, ROOT, required)
            else:
                commands = []
                artifacts = {}
                if args.kind in ("all", "native"):
                    native_command = [*command, "--target", native_target]
                    subprocess.run(native_command, cwd=ROOT, env=environment, check=True)
                    commands.append({"argv": native_command, "exit_code": 0})
                    artifacts.update({"native/lib/" + name: target / native_target / "release" / name for name in native_libraries(native_target)})
                if args.kind in ("all", "browser"):
                    wasm_command = [*command, "--target", "wasm32-unknown-unknown"]
                    subprocess.run(wasm_command, cwd=ROOT, env=environment, check=True)
                    commands.append({"argv": wasm_command, "exit_code": 0})
                    artifacts["browser/stateless.wasm"] = target / "wasm32-unknown-unknown/release/stateless.wasm"
                    if args.abi_v2:
                        v2_target = target / "abi-v2-wasm"
                        v2_command = [*wasm_command, "--features", "abi-v2-wasm"]
                        subprocess.run(v2_command, cwd=ROOT, env={**environment, "CARGO_TARGET_DIR": str(v2_target)}, check=True)
                        commands.append({"argv": v2_command, "exit_code": 0, "cargo_target_dir": str(v2_target)})
                        artifacts["browser/stateless_v2.wasm"] = v2_target / "wasm32-unknown-unknown/release/stateless.wasm"
                same_source(source, source_snapshot(ROOT))
                receipt = build_receipt(source, artifacts, native_target, rustc_version, cargo_version, commands)
            for name, path in artifacts.items():
                copy(path, stage / name)
                if sha256(stage / name) != receipt["artifacts"][name]["sha256"]:
                    raise ValueError(f"artifact changed while staging: {name}")
            if args.kind in ("all", "native") and "apple-darwin" in native_target:
                if sys.platform != "darwin":
                    raise ValueError("Darwin relocation/signing requires a macOS packaging host")
                dylib = stage / "native/lib/libstateless.dylib"
                subprocess.run(["install_name_tool", "-id", "@rpath/libstateless.dylib", str(dylib)], check=True)
                subprocess.run(["codesign", "--force", "--sign", "-", str(dylib)], check=True)
            same_source(source, source_snapshot(ROOT))
            engine_metadata(stage, args.kind, version, receipt, args.abi_v2)
            publish(stage, destination, components, args.kind, version,
                    receipt["toolchain"]["rustc"], receipt)
        print(destination)
    finally:
        lock.rmdir()


if __name__ == "__main__":
    main()
