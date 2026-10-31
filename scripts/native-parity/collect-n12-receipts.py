#!/usr/bin/env python3
"""Read-only scheduler inventory and source snapshot for the isolated N12 branch.

This does not execute/qualify a model or infer assertions from exit codes. It
never submits, changes, retries or cancels work. Preserve each output directory.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
KYOTO = "/Volumes/seed1/Projects/kyoto/kyoto.py"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)

    def run(*argv):
        return subprocess.check_output(argv, cwd=ROOT)

    active = json.loads(run(sys.executable, KYOTO, "status"))
    history = json.loads(run(sys.executable, KYOTO, "history", "--limit", "200"))
    jobs = {}
    for job in history + active:
        cwd = Path(job["cwd"]).resolve()
        if cwd == ROOT or ROOT in cwd.parents:
            jobs[job["id"]] = job
    source = {}
    paths = run("git", "ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")
    for path in paths:
        if not path or path.startswith(("validation/", "target/", ".build/")):
            continue
        item = ROOT / path
        if item.is_file():
            data = item.read_bytes()
            source[path] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    logs = {}
    for job_id, job in jobs.items():
        path = Path(job["log"])
        if path.is_file():
            data = path.read_bytes()
            logs[job_id] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                            "terminal": job["status"] in ("succeeded", "failed", "cancelled")}
    report = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "worktree": str(ROOT),
        "branch": run("git", "branch", "--show-current").decode().strip(),
        "commit": run("git", "rev-parse", "HEAD").decode().strip(),
        "dirty_status": run("git", "status", "--porcelain").decode(),
        "qualification": "scheduler inventory only; inspect raw logs and finite scope receipts for assertions",
        "history_limit": 200,
        "jobs": sorted(jobs.values(), key=lambda j: (j["submitted"], j["id"])),
        "log_observations": logs,
        "source": source,
        "broad_qualification_cells_pending": 144,
        "profile_promotion": False,
    }
    (args.output / "inventory.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"worktree": str(ROOT), "jobs": len(jobs), "sources": len(source),
                      "statuses": {status: sum(j["status"] == status for j in jobs.values())
                                   for status in sorted({j["status"] for j in jobs.values()})}}))


if __name__ == "__main__":
    main()
