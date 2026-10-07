#!/usr/bin/env python3
"""Validate a supplied Playscale production core against this engine in isolation.

Requires Playscale's existing serde dependencies in Cargo's offline cache.
Does not modify Playscale or copy application source into the public repository.
"""
import argparse
import csv
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import statistics
import subprocess
import tempfile
import time

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("playscale", type=Path)
parser.add_argument("--steps", type=int, default=100_000)
parser.add_argument("--samples", type=int, default=5)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
if args.steps < 1025 or args.samples < 3:
    parser.error("use at least 1025 steps and 3 samples to exercise eviction and variation")
source = args.playscale.resolve() / "crates/core"
output = (args.output or root / "target/adoption" / time.strftime("%Y%m%d-%H%M%S")).resolve()
output.mkdir(parents=True, exist_ok=False)
def hashes(directory, excludes=()):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file() and not set(p.parts) & set(excludes)}
with tempfile.TemporaryDirectory(prefix="stateless-adoption-") as temporary:
    # Compile exactly the files whose hashes we report, even if the working
    # checkout changes while the benchmark is running.
    engine = Path(temporary) / "engine"
    engine.mkdir()
    for name in ["src", "benches"]:
        shutil.copytree(root / name, engine / name)
    for name in ["Cargo.toml", "Cargo.lock", "build.rs", "README.md"]:
        shutil.copy2(root / name, engine / name)
    engine_hashes = hashes(engine)
    core = Path(temporary) / "core"
    shutil.copytree(source, core, ignore=shutil.ignore_patterns("target", ".git"))
    source_hashes = hashes(core)
    manifest = core / "Cargo.toml"
    text = manifest.read_text()
    if "stateless.workspace = true" not in text:
        raise SystemExit("expected Playscale's workspace Stateless dev-dependency")
    manifest.write_text(text.replace("stateless.workspace = true",
        'stateless = { package = "statelessness", path = ' + json.dumps(str(engine)) + ' }') + '\n[workspace]\n')
    examples = core / "examples"
    examples.mkdir(exist_ok=True)
    shutil.copy2(root / "scripts/playscale-measure.rs", examples / "measure.rs")
    def cargo(arguments, log):
        result = subprocess.run(["cargo", *arguments], cwd=core,
                                env={**os.environ, "CARGO_TARGET_DIR": str(core / "target")},
                                text=True, capture_output=True)
        (output / log).write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f"cargo failed; see {output / log}")
    cargo(["test", "--offline", "--", "--nocapture"], "tests.txt")
    cargo(["build", "--offline", "--release", "--example", "measure"], "build.txt")
    binary = core / "target/release/examples/measure"
    if platform.system() == "Windows":
        binary = binary.with_suffix(".exe")
    command = [str(binary), str(args.steps), str(args.samples)]
    if platform.system() == "Darwin":
        command = ["/usr/bin/time", "-l", *command]
    elif platform.system() == "Linux":
        command = ["/usr/bin/time", "-v", *command]
    with (output / "measurements.csv").open("w") as stdout, (output / "resources.txt").open("w") as stderr:
        subprocess.run(command, cwd=output, stdout=stdout, stderr=stderr, check=True)
    for sample in range(args.samples):
        result = subprocess.run([str(binary), "replay", str(output / f"jobs-{sample}.sttrace")],
                                text=True, capture_output=True, check=True)
        (output / f"replay-{sample}.txt").write_text(result.stdout)
    rows = list(csv.DictReader((output / "measurements.csv").open()))
    summary = {}
    for mode in ["reducer", "checked", "recorded"]:
        timings = [int(row["elapsed_ns"]) / args.steps for row in rows if row["mode"] == mode]
        summary[mode] = {"median_ns_per_transition": statistics.median(timings),
                         "min_ns_per_transition": min(timings), "max_ns_per_transition": max(timings)}
    evidence = {"host": platform.platform(), "rustc": subprocess.check_output(["rustc", "-vV"], text=True),
                "steps": args.steps, "samples": args.samples, "summary": summary,
                "core_source_sha256": source_hashes,
                "engine_snapshot_sha256": engine_hashes,
                "resolved_core_lockfile": (core / "Cargo.lock").read_text(),
                "measurement_sha256": hashlib.sha256((examples / "measure.rs").read_bytes()).hexdigest(),
                "notes": "Production reducer/adapter snapshot; simulated schedules, not live server or FFmpeg qualification. RSS is whole measurement-process peak, not incremental recorder memory. No warmup excluded; min/median/max include all samples."}
    (output / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")
print(output)
