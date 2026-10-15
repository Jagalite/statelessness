"""Source/artifact identity for local binding builds; no compiler or runtime calls.

External build runners can use snapshot + attest to stage their actual outputs
with build-bindings.py --build-receipt. Attestation records the runner's exit
code and log; it is not an independent execution or runtime qualification.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

SCHEMA = "stateless.binding-build.v1"
SOURCE_PATHS = (
    "Cargo.toml", "Cargo.lock", "build.rs", "LICENSE", ".cargo", "src", "bindings",
    "scripts", "python", "typescript", "go", "swift", "Package.swift",
    "tests", "benches", "examples", "crates", "spec", "conformance",
    "validation/corpus", "docs/ABI-V2.md",
    ".github/workflows/abi-v2.yml", "docs/ABI-V2-DISTRIBUTION.md",
    "docs/ABI-V2-BENCHMARKS.md",
)
ARTIFACT_NAMES = {
    "native/lib/libstateless.a", "native/lib/libstateless.so",
    "native/lib/libstateless.dylib", "native/lib/stateless.lib",
    "native/lib/stateless.dll", "native/lib/stateless.dll.lib",
    "native/lib/libstateless.dll.a", "browser/stateless.wasm", "browser/stateless_v2.wasm",
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def content_identity(files):
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def source_snapshot(root):
    root = Path(root).resolve()
    names = subprocess.check_output([
        "git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
        "--", *SOURCE_PATHS,
    ], cwd=root).decode().split("\0")
    files = {}
    for name in sorted(set(names) - {""}):
        path = root / name
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        # Deleted tracked files affect the identity instead of disappearing.
        files[name] = sha256(path) if path.is_file() else None
    if not files or files.get("Cargo.toml") is None:
        raise ValueError("source snapshot requires a Git checkout with Cargo.toml")
    return {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "files": files,
        "sha256": content_identity(files),
    }


def same_source(before, after):
    if before.get("sha256") != content_identity(before.get("files", {})):
        raise ValueError("source snapshot digest does not match its file inventory")
    if before.get("sha256") != after.get("sha256") or before.get("commit") != after.get("commit"):
        raise ValueError("source changed since the build snapshot; rebuild before packaging")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def build_receipt(source, artifacts, native_target, rustc, cargo, commands, logs=None):
    if not commands or any(command.get("exit_code") != 0 or not command.get("argv") for command in commands):
        raise ValueError("a build receipt requires successful recorded build commands")
    if not rustc.strip() or not cargo.strip():
        raise ValueError("record the actual build toolchain identity")
    if not native_target or source.get("sha256") != content_identity(source.get("files", {})):
        raise ValueError("record a valid source inventory and native target")
    unknown = set(artifacts) - ARTIFACT_NAMES
    if unknown or not artifacts:
        raise ValueError(f"unsupported or empty artifact set: {sorted(unknown)}")
    return {
        "schema": SCHEMA,
        "source": source,
        "native_target": native_target,
        "toolchain": {"rustc": rustc, "cargo": cargo},
        "commands": commands,
        "logs": logs or [],
        "artifacts": {
            name: {"path": str(Path(path).resolve()), "sha256": sha256(path)}
            for name, path in sorted(artifacts.items())
        },
        "qualification": {"build": "passed", "runtime": "not_run", "performance": "not_run"},
    }


def validate_receipt(receipt, root, required):
    if receipt.get("schema") != SCHEMA:
        raise ValueError("unsupported binding build receipt schema")
    if receipt.get("qualification", {}).get("build") != "passed":
        raise ValueError("receipt does not record a successful build")
    commands = receipt.get("commands", [])
    if not commands or any(command.get("exit_code") != 0 or not command.get("argv") for command in commands):
        raise ValueError("receipt contains no successful build command or a failed command")
    if not receipt.get("native_target") or any(not receipt.get("toolchain", {}).get(name, "").strip() for name in ("rustc", "cargo")):
        raise ValueError("receipt lacks actual toolchain/target identity")
    same_source(receipt["source"], source_snapshot(root))
    artifacts = receipt.get("artifacts", {})
    for name in required:
        if name not in ARTIFACT_NAMES or name not in artifacts:
            raise ValueError(f"build receipt lacks required artifact: {name}")
        artifact = artifacts[name]
        if sha256(artifact["path"]) != artifact["sha256"]:
            raise ValueError(f"built artifact changed: {name}")
    return {name: Path(artifacts[name]["path"]) for name in required}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    snapshot = commands.add_parser("snapshot", help="capture source before an external build")
    snapshot.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    snapshot.add_argument("--output", type=Path, required=True)
    attest = commands.add_parser("attest", help="record an external runner's successful build")
    attest.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    attest.add_argument("--snapshot", type=Path, required=True)
    attest.add_argument("--artifact", action="append", default=[], metavar="BUNDLE_PATH=FILE")
    attest.add_argument("--native-target", required=True)
    attest.add_argument("--rustc-version", type=Path, required=True)
    attest.add_argument("--cargo-version", type=Path, required=True)
    attest.add_argument("--commands", type=Path, required=True,
                        help="JSON array of actual command records with argv and exit_code")
    attest.add_argument("--build-log", type=Path, action="append", default=[])
    attest.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.operation == "snapshot":
        write_json(args.output, source_snapshot(args.root))
        return
    source = json.loads(args.snapshot.read_text())
    same_source(source, source_snapshot(args.root))
    artifacts = {}
    for item in args.artifact:
        name, separator, path = item.partition("=")
        if not separator or not name or not path or name in artifacts:
            parser.error("each artifact must have a unique BUNDLE_PATH=FILE")
        artifacts[name] = Path(path)
    logs = [{"path": str(path.resolve()), "sha256": sha256(path)} for path in args.build_log]
    receipt = build_receipt(source, artifacts, args.native_target,
                            args.rustc_version.read_text(), args.cargo_version.read_text(),
                            json.loads(args.commands.read_text()), logs)
    write_json(args.output, receipt)


if __name__ == "__main__":
    main()
