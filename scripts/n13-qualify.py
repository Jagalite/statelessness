#!/usr/bin/env python3
"""Run one N13 qualifier through Kyoto with source comparisons and raw logs.

Usage: kyoto.py submit -- python3 scripts/n13-qualify.py --output DIR -- COMMAND...
The output directory must be new. This wrapper does not submit or cancel jobs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import stat
import time

ROOT = Path(__file__).resolve().parents[1]


def identity(data):
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def snapshot():
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode().split("\0")
    rows = {}
    for name in sorted(set(names)):
        # This is the dedicated receipt tree, never an executable source tree.
        if not name or name.startswith(("validation/native-parity/derive-model-v2/", "target/")):
            continue
        path = ROOT / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            rows[name] = {"type": "missing"}
            continue
        if stat.S_ISLNK(info.st_mode):
            import os
            rows[name] = {"type": "symlink", "target": os.readlink(path), **identity(path.read_bytes())}
        elif stat.S_ISREG(info.st_mode):
            rows[name] = {"type": "regular", "mode": stat.S_IMODE(info.st_mode), **identity(path.read_bytes())}
        else:
            rows[name] = {"type": "other", "mode": info.st_mode}
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required")
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"command": command, "cwd": str(ROOT), "started_ns": time.time_ns(),
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
    code = 1
    before = after = None
    try:
        before = snapshot()
        (out / "source-before.json").write_text(json.dumps(before, indent=2) + "\n")
        with (out / "raw.log").open("wb") as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        code = result.returncode
        report["command_exit_code"] = code
    except BaseException as exc:
        report["error"] = repr(exc)
    finally:
        try:
            after = snapshot()
            (out / "source-after.json").write_text(json.dumps(after, indent=2) + "\n")
        except BaseException as exc:
            report["after_snapshot_error"] = repr(exc)
        equal = (before is not None and after is not None and before == after
                 and "error" not in report and "after_snapshot_error" not in report)
        report.update(finished_ns=time.time_ns(), source_before_after_equal=equal)
        if before is not None and after is not None:
            report.update(added=sorted(after.keys() - before.keys()), removed=sorted(before.keys() - after.keys()),
                          changed=sorted(p for p in before.keys() & after.keys() if before[p] != after[p]))
        report["files"] = {p.name: identity(p.read_bytes()) for p in out.iterdir() if p.is_file()}
        report["status"] = "passed" if code == 0 and equal else "failed"
        (out / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)
    raise SystemExit(code if code else (0 if equal else 1))


if __name__ == "__main__":
    main()
