#!/usr/bin/env python3
"""Build and exercise an isolated installed N05 consumer. Submit through Kyoto.

The shared spec must already be frozen. Every subprocess command, raw log and
input hash is retained. No expected values are authored or modified here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_frozen_manifest(corpus, freeze):
    freeze_data = json.loads(freeze.read_text())
    files = freeze_data.get("files")
    if (not isinstance(files, dict)
            or files.get("conformance/guided-v2/corpus.json") != fingerprint(corpus)):
        raise ValueError("independent freeze does not bind current corpus")
    return freeze_data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    logs = out / "logs"
    logs.mkdir()
    work = Path(tempfile.mkdtemp(prefix="stateless-n05-python-")).resolve()
    report = {"status": "running", "workspace": str(work), "commands": [],
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                        cwd=ROOT, text=True).strip()}
    env = dict(os.environ, PYTHONPATH="", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")

    def save():
        (out / "qualification.json").write_text(json.dumps(report, indent=2) + "\n")

    def run(name, argv):
        command = list(map(str, argv))
        print(f"[{name}] " + " ".join(command), flush=True)
        completed = subprocess.run(command, cwd=work, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=900)
        log = logs / (name + ".log")
        log.write_bytes(completed.stdout)
        report["commands"].append({"name": name, "argv": command, "cwd": str(work),
                                    "exit_code": completed.returncode, "log_sha256": fingerprint(log)})
        save()
        if completed.returncode:
            print(completed.stdout.decode("utf-8", "replace"), flush=True)
            raise RuntimeError(f"{name} failed with exit {completed.returncode}")
        return completed.stdout.decode("utf-8", "replace")

    try:
        corpus = ROOT / "conformance/guided-v2/corpus.json"
        freeze = corpus.with_name("FREEZE.json")
        consumer = ROOT / "scripts/native-guided-consumers/python/consumer.py"
        # Fail before building/running if the independent author has not frozen.
        read_frozen_manifest(corpus, freeze)
        source = work / "package"
        source.mkdir()
        shutil.copytree(ROOT / "python/src", source / "src",
                        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
        for name in ("pyproject.toml", "README.md", "GUIDED.md", "LICENSE"):
            shutil.copy2(ROOT / "python" / name, source / name)
        shutil.copy2(ROOT / "python/examples/guided.py", work / "example.py")
        shutil.copy2(consumer, work / "consumer.py")
        shutil.copy2(corpus, work / "corpus.json")
        shutil.copy2(freeze, work / "FREEZE.json")
        report["input_sha256"] = {str(path.relative_to(work)): fingerprint(path)
                                  for path in work.rglob("*") if path.is_file()}
        save()
        run("builder-venv", [sys.executable, "-m", "venv", work / "builder"])
        builder = work / "builder/bin/python"
        run("builder-tools", [builder, "-m", "pip", "install", "setuptools>=77", "wheel"])
        report["builder_versions"] = run("builder-versions", [builder, "-m", "pip", "freeze", "--all"])
        wheels = out / "packages"
        wheels.mkdir()
        run("wheel", [builder, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                      "--wheel-dir", wheels, source])
        wheel, = wheels.glob("*.whl")
        report["wheel_sha256"] = fingerprint(wheel)
        run("consumer-venv", [sys.executable, "-m", "venv", work / "installed"])
        installed = work / "installed/bin/python"
        run("wheel-install", [installed, "-m", "pip", "install", "--no-index", "--no-deps", wheel])
        report["installed_origin"] = run("installed-origin", [installed, "-I", "-c",
            "import json, statelessness, sys; print(json.dumps({'package':statelessness.__file__,"
            "'python':sys.executable,'version':sys.version}))"]).strip()
        run("example", [installed, "-I", work / "example.py"])
        run("guided-consumer", [installed, "-I", work / "consumer.py", "--corpus", work / "corpus.json",
            "--corpus-sha256", report["input_sha256"]["corpus.json"], "--freeze-sha256",
            report["input_sha256"]["FREEZE.json"], "--compare", "--output", out / "results.json"])
        report["results_sha256"] = fingerprint(out / "results.json")
        report["status"] = "passed"
        save()
        print("Installed Python guided consumer passed; " + str(out), flush=True)
    except BaseException as error:
        report["status"] = "failed"
        report["error"] = str(error)
        save()
        raise


if __name__ == "__main__":
    main()
