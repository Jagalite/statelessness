#!/usr/bin/env python3
"""Build reviewable local ABI 1 distributions. Never publishes or uploads."""
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

ROOT = Path(__file__).resolve().parents[1]
SWIFT_FILES = (
    "Package.swift", "c/stateless.h", "c/module.modulemap", "swift/Counter.swift",
    "swift/README.md", "swift/Sources/StatelessNative.swift", "swift/Tests/NativeTests.swift",
)


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def stage_sources(root, stage, kind, version):
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
    if kind in ("all", "browser"):
        components += ["browser", "browser-fixture"]
        for name in ("stateless.mjs", "package.json", "README.md"):
            copy(root / "bindings/browser" / name, stage / "browser" / name)
        # A release bump must change the actual package identity, not only its
        # hash manifest. The source manifest supplies npm settings, not version.
        package = stage / "browser/package.json"
        metadata = json.loads(package.read_text())
        metadata["version"] = version
        package.write_text(json.dumps(metadata, indent=2) + "\n")
        copy(root / "LICENSE", stage / "browser/LICENSE")
        for name in ("adapter.mjs", "stateless.mjs", "index.html", "self-test.html"):
            copy(root / "bindings/browser" / name, stage / "browser-fixture" / name)
        for name in ("index.html", "self-test.html"):
            html = stage / "browser-fixture" / name
            html.write_text(html.read_text().replace(
                "../../target/wasm32-unknown-unknown/release/stateless.wasm", "../browser/stateless.wasm"))
    return components


def publish(stage, destination, components, kind, version, host):
    """Replace only selected generated bundles, restoring the old set on error."""
    manifest_name = f"manifest-{kind}.json"
    manifest = {"version": version, "host": host, "files": {
        p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for component in components for p in sorted((stage / component).rglob("*")) if p.is_file()
    }}
    (stage / manifest_name).write_text(json.dumps(manifest, indent=2) + "\n")
    destination.mkdir(parents=True, exist_ok=True)
    backup = stage / "previous"
    backup.mkdir()
    moved = []
    installed = []
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=["all", "native", "browser"], default="all")
    args = parser.parse_args()
    target = ROOT / "target"
    target.mkdir(exist_ok=True)
    destination = target / "bindings"
    lock = target / ".bindings-build.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise SystemExit(f"binding build already locked: {lock}; verify no build is active before removing a stale lock")
    try:
        version = tomllib.loads((ROOT / "Cargo.toml").read_text())["package"]["version"]
        environment = {**os.environ, "CARGO_TARGET_DIR": str(target)}
        command = ["cargo", "build", "--locked", "--offline", "--release", "--lib"]
        with tempfile.TemporaryDirectory(prefix=".bindings-stage-", dir=target) as temporary:
            stage = Path(temporary)
            components = stage_sources(ROOT, stage, args.kind, version)
            if args.kind in ("all", "native"):
                libraries = {
                    "darwin": ("libstateless.a", "libstateless.dylib"),
                    "linux": ("libstateless.a", "libstateless.so"),
                    "win32": ("stateless.lib", "stateless.dll", "stateless.dll.lib"),
                }.get(sys.platform)
                if libraries is None:
                    raise SystemExit("native distribution unsupported on this host")
                subprocess.run(command, cwd=ROOT, env=environment, check=True)
                for name in libraries:
                    copy(target / "release" / name, stage / "native/lib" / name)
                if sys.platform == "darwin":
                    dylib = stage / "native/lib/libstateless.dylib"
                    subprocess.run(["install_name_tool", "-id", "@rpath/libstateless.dylib", str(dylib)], check=True)
                    subprocess.run(["codesign", "--force", "--sign", "-", str(dylib)], check=True)
            if args.kind in ("all", "browser"):
                subprocess.run([*command, "--target", "wasm32-unknown-unknown"], cwd=ROOT, env=environment, check=True)
                copy(target / "wasm32-unknown-unknown/release/stateless.wasm", stage / "browser/stateless.wasm")
            publish(stage, destination, components, args.kind, version,
                    subprocess.check_output(["rustc", "-vV"], text=True))
        print(destination)
    finally:
        lock.rmdir()


if __name__ == "__main__":
    main()
