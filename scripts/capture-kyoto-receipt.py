#!/usr/bin/env python3
"""Archive terminal Kyoto job metadata and exact raw logs without replacing evidence.

This reads scheduler metadata only; it never submits, retries, or cancels a job.
Compile-only success remains compile-only in the saved scheduler command/policy.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("jobs", nargs="+")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for job in args.jobs:
        raw = subprocess.check_output([
            sys.executable, "/Volumes/seed1/Projects/kyoto/kyoto.py", "show", job
        ])
        metadata = json.loads(raw)
        if metadata["id"] != job or metadata["status"] not in {"succeeded", "failed", "cancelled", "completed"}:
            raise RuntimeError(f"{job}: retain only terminal jobs, got {metadata['status']}")
        log_path = Path(metadata["log"])
        present = log_path.is_file()
        if not present and not (metadata["status"] == "cancelled" and metadata["started"] is None):
            raise RuntimeError(f"{job}: terminal execution log is missing")
        log = log_path.read_bytes() if present else None
        receipt = {
            "job": metadata,
            "raw_log_present": present,
            "raw_log_bytes": len(log) if present else None,
            "raw_log_sha256": hashlib.sha256(log).hexdigest() if present else None,
            "scheduler_metadata_sha256": hashlib.sha256(raw).hexdigest(),
            "qualification": "not inferred; inspect effective command and executed assertions",
        }
        # Exclusive writes preserve earlier failed/superseded receipts as well.
        payloads = [(".show.json", raw)]
        if present:
            payloads.append((".log.gz", gzip.compress(log, mtime=0)))
        payloads.append((".receipt.json", (json.dumps(receipt, indent=2) + "\n").encode()))
        for suffix, payload in payloads:
            with (args.output / (job + suffix)).open("xb") as target:
                target.write(payload)
        print(f"{job}: {metadata['status']}, raw bytes {receipt['raw_log_bytes']}, sha256 {receipt['raw_log_sha256']}")


if __name__ == "__main__":
    main()
