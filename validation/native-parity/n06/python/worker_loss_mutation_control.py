#!/usr/bin/env python3
"""Execute the real spawned-worker fault test against source and a mutant.

This is a scoped mutation control, not installed-package qualification. The
mutant permits a later worker failure to replace an already reported sink error.
The test must fail by its specific semantic assertion, not by crash or timeout.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "python" / "src" / "statelessness"
TEST = ROOT / "python" / "tests" / "test_campaign.py"
TARGET = "CampaignProcessTests.test_hostile_sink_diagnostic_survives_later_worker_failure_and_join"
OLD = 'if r.termination != "callback_error":\n                r.termination, r.error = "worker_error", event.error'
NEW = 'if True:  # MUTATION: worker loss overwrites the first sink error\n                r.termination, r.error = "worker_error", event.error'


def execute(root: Path, label: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    env["PYTHONOPTIMIZE"] = "0"
    result = subprocess.run([sys.executable, str(root / "test_campaign.py"), TARGET],
                            cwd=root, env=env, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=90)
    print(f"--- {label}: exit {result.returncode} ---", flush=True)
    print(result.stdout, flush=True)
    return result


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="n06-python-worker-mutation-") as name:
        root = Path(name)
        shutil.copytree(SOURCE, root / "src" / "statelessness",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(TEST, root / "test_campaign.py")
        lifecycle = root / "src" / "statelessness" / "_campaign_lifecycle.py"
        original = lifecycle.read_text(encoding="utf-8")
        if original.count(OLD) != 1:
            raise RuntimeError("mutation target must occur exactly once")
        print("source_sha256=" + hashlib.sha256(original.encode()).hexdigest(), flush=True)
        clean = execute(root, "clean")
        if clean.returncode != 0 or "Ran 1 test" not in clean.stdout or "OK" not in clean.stdout:
            raise RuntimeError("unmodified worker-loss control did not pass")
        mutant = original.replace(OLD, NEW, 1)
        lifecycle.write_text(mutant, encoding="utf-8")
        print("mutant_sha256=" + hashlib.sha256(mutant.encode()).hexdigest(), flush=True)
        bad = execute(root, "mutant")
        if (bad.returncode == 0 or "Ran 1 test" not in bad.stdout
                or "FAIL: test_hostile_sink_diagnostic_survives_later_worker_failure_and_join" not in bad.stdout
                or "AssertionError" not in bad.stdout
                or "worker_error" not in bad.stdout or "callback_error" not in bad.stdout
                or "FAILED (failures=1)" not in bad.stdout):
            raise RuntimeError("mutant was not detected by the required semantic assertion")
        print("worker-loss-first-sink-error mutation detected", flush=True)


if __name__ == "__main__":
    main()
