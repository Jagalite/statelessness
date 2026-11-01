#!/usr/bin/env python3
"""Qualify the Swift-only migration extensions (S1-S5) through Kyoto.

Stages an exact copy of Package.swift, swift/, conformance/ and
docs/requirements/ (tracked plus
untracked, non-ignored files), records their SHA-256 values and repository
identity, then submits three serialized Kyoto jobs: an external consumer build
against the staged package, that consumer's executable, and the staged
package's full Swift test suite. Kyoto job records and logs are copied into
validation/swift-migration/<label>/ with a SUMMARY.json and SHA256SUMS. The
exit status is nonzero unless every job succeeded. Build products stay in the
scratch directory; receipts never do.
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
KYOTO = os.environ.get("KYOTO", "/Volumes/seed1/Projects/kyoto/kyoto.py")
# Every repository path read by the Swift package or its tests.
STAGED = ["Package.swift", "swift", "conformance", "docs/requirements"]
CONSUMER = ROOT / "swift/ExternalConsumers/MigrationConsumer"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def kyoto(*args):
    result = subprocess.run([sys.executable, KYOTO, *args], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0 and args[0] != "wait":
        raise SystemExit(f"kyoto {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def run_job(name, command, resource, receipts):
    job = json.loads(kyoto("submit", "--resource", str(resource), "--", *command))["id"]
    kyoto("wait", job)
    record = json.loads(kyoto("show", job))
    (receipts / f"{name}.job.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    (receipts / f"{name}.log").write_text(kyoto("logs", job))
    return {"name": name, "job": job, "command": record.get("requested_command") or command,
            "status": record.get("status"), "exit_code": record.get("exit_code"),
            "elapsed_seconds": record.get("elapsed_seconds"), "peak_rss_mb": record.get("peak_rss_mb")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    head = git("rev-parse", "--short=12", "HEAD").strip()
    parser.add_argument("--label", default=f"{datetime.date.today():%Y%m%d}-{head}")
    parser.add_argument("--scratch", type=pathlib.Path, default=None, help="build scratch directory (default: /tmp/stateless-swift-migration-<label>)")
    args = parser.parse_args()
    receipts = ROOT / "validation/swift-migration" / args.label
    if receipts.exists():
        raise SystemExit(f"{receipts} already exists; receipts are never overwritten")
    scratch = (args.scratch or pathlib.Path(f"/tmp/stateless-swift-migration-{args.label}")).resolve()
    if scratch.exists():
        shutil.rmtree(scratch)
    stage, consumer = scratch / "Statelessness", scratch / "consumer"
    files = sorted(f for f in git("ls-files", "-co", "--exclude-standard", "--", *STAGED).splitlines()
                   if f and (ROOT / f).is_file() and not f.startswith("swift/ExternalConsumers/"))
    for f in files:
        (stage / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, stage / f)
    shutil.copytree(CONSUMER, consumer, ignore=shutil.ignore_patterns(".build", ".swiftpm"))
    receipts.mkdir(parents=True)
    (receipts / "SOURCES.sha256").write_text("".join(f"{sha256(stage / f)}  {f}\n" for f in files) +
                                             "".join(f"{sha256(p)}  consumer/{p.relative_to(consumer)}\n" for p in sorted(consumer.rglob("*")) if p.is_file()))
    identity = {
        "label": args.label, "created": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "head": git("rev-parse", "HEAD").strip(), "branch": git("rev-parse", "--abbrev-ref", "HEAD").strip(),
        "dirty": [line for line in git("status", "--porcelain", "--", *STAGED, "scripts/swift-migration").splitlines()],
        "swift": " ".join(subprocess.run(["swift", "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.split()),
        "platform": os.uname().sysname + " " + os.uname().release + " " + os.uname().machine,
        "kyoto": KYOTO, "staged_files": len(files),
        "profiles": "graph-search-v1, progress-analysis-v1, reduction-v1, continued-failure-evidence-v1 (Swift-only)",
    }
    (receipts / "IDENTITY.json").write_text(json.dumps(identity, indent=1) + "\n")
    consumer_build = scratch / "consumer-build"
    jobs = [
        ("consumer-build", ["env", f"STATELESS_SWIFT_PACKAGE_PATH={stage}", "swift", "build", "--package-path", str(consumer), "--scratch-path", str(consumer_build)], consumer_build),
        ("consumer-run", [str(consumer_build / "debug/MigrationConsumer")], consumer_build),
        ("staged-tests", ["swift", "test", "--package-path", str(stage), "--scratch-path", str(scratch / "engine-build")], scratch / "engine-build"),
    ]
    results = []
    for name, command, resource in jobs:
        results.append(run_job(name, command, resource, receipts))
        print(f"{name}: {results[-1]['status']} (exit {results[-1]['exit_code']})", flush=True)
        if results[-1]["status"] != "succeeded":
            break
    ok = len(results) == len(jobs) and all(r["status"] == "succeeded" and r["exit_code"] == 0 for r in results)
    (receipts / "SUMMARY.json").write_text(json.dumps({"ok": ok, "jobs": results}, indent=1) + "\n")
    (receipts / "SHA256SUMS").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in sorted(receipts.iterdir()) if p.name != "SHA256SUMS"))
    print(f"receipts: {receipts.relative_to(ROOT)} ok={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
