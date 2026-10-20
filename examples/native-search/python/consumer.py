"""Installed-package application consumer; no conformance internals or Rust."""
import json
from pathlib import Path
import sys
import statelessness
from statelessness import Check, Metadata, Transition, FuzzConfig, fuzz, shrink, record, replay
from statelessness.trace_json import dumps, loads


class Threshold:
    def initial_state(self): return 0
    def step(self, state, input):
        if input not in (0, 1): raise ValueError("expected zero or one")
        return Transition(state + input, ("changed",) if input else ())
    def check_state(self, state):
        return (Check("threshold.bound", "failed", "at least three") if state >= 3 else Check("threshold.bound"),)
    def generate(self, state, rng): return 1 - rng.index(2)
    def is_enabled(self, state, input): return input in (0, 1)
    def simpler_inputs(self, input): return ()
    def metadata(self): return Metadata("native-search-threshold", model_version=2, build="application-v2")
    def encode_state(self, state): return str(state).encode("ascii")
    def decode_state(self, data): return int(data.decode("ascii"))
    def encode_input(self, input): return str(input).encode("ascii")
    def decode_input(self, data):
        if data not in (b"0", b"1"): raise ValueError("expected zero or one")
        return int(data)
    def encode_output(self, output): return output.encode("ascii")


mode, path = sys.argv[1:]
model = Threshold()
search_receipt = {}
if mode == "record":
    found = fuzz(model, FuzzConfig(seed=0, cases=4, max_steps=32, max_transitions=128, mutation_percent=0))
    if found.termination != "failure_found" or found.failure is None:
        raise RuntimeError("failure missing")
    Path(path + ".original.json").write_text(dumps(record(model, found.failure.inputs)), encoding="utf-8")
    reduced = shrink(model, found.failure)
    target = lambda failure: tuple((v.phase, v.check.id) for v in failure.violations)
    if (not reduced.validated_original or reduced.termination != "search_complete" or reduced.minimized.inputs != (1, 1, 1)
            or len(found.failure.inputs) <= 3 or reduced.attempts <= 0
            or target(found.failure) != (("state", "threshold.bound"),) or target(reduced.minimized) != target(found.failure)):
        raise RuntimeError("shrink incomplete")
    search_receipt = {"original_steps": len(found.failure.inputs), "minimized_steps": len(reduced.minimized.inputs),
                      "shrink_attempts": reduced.attempts, "validated_original": reduced.validated_original,
                      "phase": "state", "property": "threshold.bound"}
    Path(path).write_text(dumps(record(model, reduced.minimized.inputs)), encoding="utf-8")
elif mode != "replay":
    raise ValueError("expected record or replay")
restored = loads(Path(path).read_bytes())
result = replay(model, restored)
if result.outcome != "exact" or not result.failure_reproduced:
    raise RuntimeError("failure did not replay")
print(json.dumps({"mode": mode, "outcome": result.outcome, "failure_reproduced": result.failure_reproduced,
                  "steps": len(restored.steps), "package": str(Path(statelessness.__file__).resolve()), **search_receipt}))
