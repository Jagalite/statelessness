#!/usr/bin/env python3
"""Copy and identity-bind focused Swift inputs. No build/test/job execution."""
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import traceback

# Reuse the exact manifest definition; importing does not execute qualification.
sys.dont_write_bytecode = True
from qualify import manifest


def identity(path, relative):
    info = path.lstat()
    if stat.S_ISDIR(info.st_mode):
        return {"path": str(relative), "type": "directory", "mode": stat.S_IMODE(info.st_mode)}
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"source must be a regular file or directory: {relative}")
    return {"path": str(relative), "type": "file", "mode": stat.S_IMODE(info.st_mode),
            "size": info.st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def selected(root):
    def traverse(path):
        yield path
        if stat.S_ISDIR(path.lstat().st_mode):
            # Unlike recursive globbing, failure to enumerate a source directory
            # must propagate into the external failure receipt, never omit it.
            for child in sorted(path.iterdir()):
                yield from traverse(child)

    paths = [root / "Package.swift"]
    for directory in ["swift/Sources", "swift/Tests", "examples/native-campaign/swift/Sources"]:
        paths.extend(traverse(root / directory))
    paths.append(root / "examples/native-campaign/swift/Package.swift")
    # Every current Swift shared corpus is a root-level JSON/TSV file. Exclude
    # concurrent Rust fixtures and pending N05 author/receipt generation.
    paths.append(root / "conformance")
    for path in sorted((root / "conformance").iterdir()):
        if path.name.endswith((".json", ".tsv")):
            paths.append(path)
    paths += [root / "validation/native-parity/n06/swift" / name for name in ("qualify.py", "snapshot.py", "guards.py")]
    return sorted(paths)


def main():
    root = Path(__file__).resolve().parents[4]
    if root.name != "t3-25250336":
        raise RuntimeError("snapshot requires the authorized isolated checkout")
    revision = sys.argv[1]
    if not revision or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in revision):
        raise RuntimeError("invalid revision label")
    base = root / "validation/native-parity/n06/swift"
    destination = base / "inputs" / revision
    receipts = base / "receipts"
    receipts.mkdir(exist_ok=True)
    receipt_path = receipts / f"{revision}-snapshot.json"
    receipt = {"schema": "n06-swift-snapshot-receipt-v1", "snapshot": str(destination),
               "revision": revision, "status": "preparing", "stage": "selection",
               "scope": "source packaging only; no executable qualification"}
    # External to the partial snapshot and initialized BEFORE selection/copy.
    # A used revision must never overwrite either success or failure evidence.
    with receipt_path.open("x") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
        handle.write("\n")
    try:
        paths = selected(root)
        receipt["stage"] = "source-manifest"
        before = [identity(path, path.relative_to(root)) for path in paths]
        saved = {entry["path"]: entry for entry in before}
        receipt["stage"] = "destination-mapping"
        mapped = {}
        runner_paths = {Path("validation/native-parity/n06/swift") / name for name in ("qualify.py", "snapshot.py", "guards.py")}
        for path in paths:
            relative = path.relative_to(root)
            if relative.parts[:3] == ("examples", "native-campaign", "swift"):
                target = destination / "consumer" / Path(*relative.parts[3:])
            elif relative in runner_paths:
                target = destination / relative.name
            else:
                target = destination / "package" / relative
            if target in mapped:
                raise RuntimeError(f"snapshot destination collision: {mapped[target]} and {relative} -> {target}")
            mapped[target] = path
        receipt["stage"] = "copy"
        destination.mkdir(parents=True, exist_ok=False)
        for target, path in mapped.items():
            relative = path.relative_to(root)
            expected = saved[str(relative)]
            if expected["type"] == "directory":
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(expected["mode"])
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            if identity(target, relative) != expected:
                raise RuntimeError(f"copy identity mismatch: {relative}")
        receipt["stage"] = "source-recheck"
        if selected(root) != paths or before != [identity(path, path.relative_to(root)) for path in paths]:
            raise RuntimeError("source changed during copy; preserve this incomplete snapshot and use a new revision")
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=root, text=True).strip()
        n05_base = "0a26bf5a068e5948d82a3d8bd9eb4063ae85115f"
        ancestry = subprocess.run(["git", "merge-base", "--is-ancestor", n05_base, commit], cwd=root, check=False)
        if ancestry.returncode != 0 or branch != "t3/native-campaign-runtime":
            raise RuntimeError("unexpected source base or branch")
        receipt["stage"] = "seal"
        sealed = {"schema": "n06-swift-focused-source-v2", "base_commit": commit, "branch": branch,
                "required_ancestor": n05_base,
                "head_is_not_payload_identity": True,
                "checkout": str(root), "revision": revision, "identity_file_mode": 0o644,
                "source_entries": before,
                "snapshot_entries": manifest(destination),
                "selection": {"full_trees": ["swift/Sources", "swift/Tests", "examples/native-campaign/swift/Sources"],
                              "conformance": "root-level *.json and *.tsv only",
                              "package_manifests": ["Package.swift", "examples/native-campaign/swift/Package.swift"],
                              "runners": ["qualify.py", "snapshot.py", "guards.py"]}}
        encoded = (json.dumps(sealed, indent=2, sort_keys=True) + "\n").encode()
        with (destination / "source-identity.json").open("xb") as handle:
            handle.write(encoded)
        (destination / "source-identity.json").chmod(sealed["identity_file_mode"])
        with (receipts / f"{revision}-source-identity.json").open("xb") as handle:
            handle.write(encoded)
        receipt.update(status="sealed", stage="complete", source_identity_sha256=hashlib.sha256(encoded).hexdigest(),
                       files=sum(entry["type"] == "file" for entry in before),
                       snapshot_entries=len(sealed["snapshot_entries"]), base_commit=commit)
    except BaseException as error:
        receipt.update(status="failed", exception=repr(error), traceback=traceback.format_exc())
        raise
    finally:
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
