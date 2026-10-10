"""Optional Git provenance for a source-bound qualification run.

An exported source tree remains testable without Git metadata. Never attribute a
nested archive to an unrelated parent repository: its source hashes are the proof.
"""
from pathlib import Path
import subprocess


def git_context(root):
    root = Path(root).resolve()
    unavailable = {"available": False, "commit": None, "status": None}
    try:
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root,
                             capture_output=True, text=True, timeout=10)
        if top.returncode or Path(top.stdout.strip()).resolve() != root:
            return unavailable
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                capture_output=True, text=True, timeout=10)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                capture_output=True, text=True, timeout=10)
        if commit.returncode or status.returncode:
            return unavailable
        return {"available": True, "commit": commit.stdout.strip(), "status": status.stdout}
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return unavailable
