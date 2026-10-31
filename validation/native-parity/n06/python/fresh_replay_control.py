#!/usr/bin/env python3
"""N06 duplicate-delivery persisted witness: producer and fresh replay process.

Run with PYTHONPATH=python/src from the repository root. This is a source-tree
control, not evidence that an installed distribution or another engine ran it.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "python" / "tests"))
from campaign_lifecycle_fixture import AuditedLifecycle, complete, sink
from statelessness._campaign_lifecycle import Event
from statelessness import (AuditTrace, Failure, RunConfig, SearchConfig, ShrinkConfig,
                           audit_from_bytes, audit_to_bytes, enumerate_states, record,
                           replay, shrink)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def produce(path, original_path):
    model = AuditedLifecycle(jobs=2, duplicate_delivery=True)
    found = enumerate_states(model, SearchConfig(5000, 100_000, 10))
    require(found.termination == "failure_found", "search did not discover fault")
    target = next(v for v in found.failure.violations
                  if v.phase == "transition" and v.check.id == "campaign.effects")
    original = Failure((sink(999), Event("claim")) + found.failure.inputs, (target,))
    reduced = shrink(model, original, ShrinkConfig(max_attempts=1000))
    require(reduced.validated_original and reduced.termination == "search_complete", "shrink incomplete")
    require(tuple(reduced.minimized.inputs) == (Event("claim"), complete(50), Event("receive", job_id=50)),
            "unexpected minimized witness")
    for dest, inputs in ((path, reduced.minimized.inputs), (original_path, original.inputs)):
        trace = record(model, inputs)
        require(trace.termination == "property_failed", "record did not fail")
        data = audit_to_bytes(AuditTrace.from_portable(trace, RunConfig("n06-fresh-replay")))
        with open(dest, "xb") as stream:
            stream.write(data)


def consume(path):
    audit = audit_from_bytes(Path(path).read_bytes())
    trace, config = audit.to_portable()
    require(config.strategy == "n06-fresh-replay", "unexpected run config")
    model = AuditedLifecycle(jobs=2, duplicate_delivery=True)
    expected = (Event("claim"), complete(50), Event("receive", job_id=50))
    # Verify the witness independently of replay's own success classification.
    require(tuple(model.decode_input(step.input) for step in trace.steps) == expected,
            "unexpected persisted witness")
    result = replay(model, trace)
    require((result.outcome, result.steps_verified, result.failure_reproduced) == ("exact", 3, True),
            "fault was not exactly reproduced")
    require(any(c.id == "campaign.effects" and c.status == "failed"
                for c in trace.steps[-1].checks), "wrong failure identity")
    healthy = AuditedLifecycle(jobs=2)
    strict = replay(healthy, trace)
    require(strict.outcome == "incompatible" and not strict.build_matches, "healthy strict control failed")
    relaxed = replay(healthy, trace, allow_build_mismatch=True)
    require(relaxed.outcome == "diverged" and not relaxed.failure_reproduced, "healthy observation control failed")
    print("fresh-replay-verified")


def exercise(directory):
    witness, original = directory / "witness.bin", directory / "original.bin"
    produce(witness, original)
    command = [sys.executable, str(Path(__file__).resolve()), "consume"]
    positive = subprocess.run([*command, str(witness)], capture_output=True, text=True, timeout=120)
    (directory / "replay.stdout").write_text(positive.stdout)
    (directory / "replay.stderr").write_text(positive.stderr)
    require(positive.returncode == 0 and positive.stdout.strip() == "fresh-replay-verified",
            "fresh replay failed: " + positive.stderr)
    negative = subprocess.run([*command, str(original)], capture_output=True, text=True, timeout=120)
    (directory / "unreduced.stdout").write_text(negative.stdout)
    (directory / "unreduced.stderr").write_text(negative.stderr)
    require(negative.returncode != 0 and "unexpected persisted witness" in negative.stderr,
            "unreduced witness negative control failed")
    sources = (Path(__file__), ROOT / "python/tests/campaign_lifecycle_fixture.py",
               *sorted((ROOT / "python/src/statelessness").rglob("*.py")))
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"scope": "source-tree, caller-attested, single host", "python": sys.version,
                "producer_argv": sys.argv, "consumer_argv": command,
                "sources": {str(p.relative_to(ROOT)): digest(p) for p in sources},
                "artifacts": {p.name: digest(p) for p in sorted(directory.iterdir()) if p.is_file()},
                "replay_exit": positive.returncode, "unreduced_exit": negative.returncode}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print("fresh-process fault and unreduced negative control passed")


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "consume":
        consume(sys.argv[2]); return
    if len(sys.argv) == 3 and sys.argv[1] == "--output":
        directory = Path(sys.argv[2]).resolve()
        directory.mkdir(parents=True, exist_ok=False)
        exercise(directory); return
    if len(sys.argv) != 1:
        raise SystemExit("usage: fresh_replay_control.py [consume TRACE | --output NEW_DIRECTORY]")
    with tempfile.TemporaryDirectory(prefix="n06-py-fresh-") as tmp:
        exercise(Path(tmp))


if __name__ == "__main__":
    main()
