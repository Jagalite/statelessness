"""Installed append-only checker application; legacy checkers must never run."""
import json
from pathlib import Path
import sys
import statelessness
from statelessness import (Check, Metadata, Transition, FuzzConfig, CheckPolicy,
    check_observed_into, enumerate_states, fuzz, shrink, record, replay)
from statelessness.trace_json import dumps, loads


def expired(sink):
    operations = {"push": lambda: sink.push(Check("expired")), "append": lambda: sink.append(Check("expired")), "extend": lambda: sink.extend([Check("expired")]),
                  "snapshot": sink.snapshot, "length": lambda: len(sink), "reserve": lambda: sink.reserve(1),
                  "index": lambda: sink[0], "iter": lambda: iter(sink)}
    for name, operation in operations.items():
        try: operation()
        except Exception as error:
            if "expired" not in str(error).lower(): raise RuntimeError(f"{name} did not report an expired lease") from error
            continue
        raise RuntimeError(f"escaped sink operation {name} succeeded")


class Threshold:
    def __init__(self, fail_transition=False, nested=False):
        self.state_calls = self.transition_calls = self.step_calls = self.legacy_calls = 0
        self.leases = []
        self.fail_transition, self.nested = fail_transition, nested
        self.nested_expired = self.outer_active = False
    def initial_state(self): return 0
    def inputs(self, state): return (0, 1)
    def step(self, state, input):
        self.step_calls += 1
        if input not in (0, 1): raise ValueError("expected zero or one")
        return Transition(state + input, ("changed",) if input else ())
    def check_state(self, state): self.legacy_calls += 1; raise RuntimeError("legacy state checker invoked")
    def check_transition(self, before, input, transition): self.legacy_calls += 1; raise RuntimeError("legacy transition checker invoked")
    def check_state_into(self, state, sink):
        self.state_calls += 1; self.leases.append(sink)
        if len(sink) != 0 or sink.snapshot() != (): raise RuntimeError("state sink not empty")
        sink.reserve(2)
        sink.push(Check("threshold.bound", "failed", "at least three") if state >= 3 else Check("threshold.bound"))
        if len(sink) != 1 or sink.snapshot()[0].id != "threshold.bound": raise RuntimeError("state inspection mismatch")
        if sink[0].id != "threshold.bound" or tuple(c.id for c in sink) != ("threshold.bound",): raise RuntimeError("state iteration/index mismatch")
        if self.nested:
            child, checks = Threshold(), []
            check_observed_into(child, 0, 1, Transition(1, ("changed",)), 1, checks)
            for lease in child.leases: expired(lease)
            self.nested_expired = True
            self.outer_active = len(sink) == 1 and sink.snapshot()[0].id == "threshold.bound"
        sink.extend([Check("state.appended")])
        if tuple(c.id for c in sink.snapshot()) != ("threshold.bound", "state.appended"): raise RuntimeError("state append order mismatch")
    def check_transition_into(self, before, input, transition, sink):
        self.transition_calls += 1; self.leases.append(sink)
        if len(sink) != 2 or tuple(c.id for c in sink.snapshot()) != ("threshold.bound", "state.appended"):
            raise RuntimeError("transition sink lost state-check prefix")
        sink.reserve(2); sink.append(Check("edge.checked"))
        if len(sink) != 3 or sink.snapshot()[2].id != "edge.checked": raise RuntimeError("transition inspection mismatch")
        if self.fail_transition: raise RuntimeError("application checker failure")
        sink.extend([Check("edge.appended")])
        if tuple(c.id for c in sink.snapshot()) != ("threshold.bound", "state.appended", "edge.checked", "edge.appended"):
            raise RuntimeError("transition append order mismatch")
    def generate(self, state, rng): return 1 - rng.index(2)
    def is_enabled(self, state, input): return input in (0, 1)
    def simpler_inputs(self, input): return ()
    def metadata(self): return Metadata("native-append-threshold", build="application-v1")
    def encode_state(self, state): return str(state).encode("ascii")
    def decode_state(self, data): return int(data.decode("ascii"))
    def encode_input(self, input): return str(input).encode("ascii")
    def decode_input(self, data):
        if data not in (b"0", b"1"): raise ValueError("expected zero or one")
        return int(data)
    def encode_output(self, output): return output.encode("ascii")


def controls():
    model, checks = Threshold(), [Check("stale")]
    check_observed_into(model, 0, 1, Transition(1, ("changed",)), 1, checks)
    ids = [c.id for c in checks]
    for lease in model.leases: expired(lease)
    failing, failed_checks = Threshold(fail_transition=True), [Check("stale")]
    try: check_observed_into(failing, 0, 1, Transition(1, ("changed",)), 1, failed_checks)
    except Exception as error:
        if "application checker failure" not in str(error): raise
    else: raise RuntimeError("checker error missing")
    for lease in failing.leases: expired(lease)
    nested, nested_checks = Threshold(nested=True), []
    check_observed_into(nested, 0, 1, Transition(1, ("changed",)), 1, nested_checks)
    return dict(ids=ids, step_calls=model.step_calls, state_calls=model.state_calls, transition_calls=model.transition_calls,
        error_cleared=failed_checks == [], error_step_calls=failing.step_calls,
        error_state_calls=failing.state_calls, error_transition_calls=failing.transition_calls,
        escaped_sink_expired=True, escaped_operations=["push", "append", "extend", "snapshot", "length", "reserve", "index", "iter"],
        nested_child_expired=nested.nested_expired, outer_active_after_nested=nested.outer_active,
        legacy_calls=model.legacy_calls + failing.legacy_calls + nested.legacy_calls)


mode, path = sys.argv[1:]
if mode == "record":
    control = controls(); model = Threshold()
    bfs = enumerate_states(model)
    found = fuzz(model, FuzzConfig(seed=0, cases=4, max_steps=32, max_transitions=128, mutation_percent=0))
    if bfs.failure is None or found.failure is None: raise RuntimeError("search witness missing")
    reduced = shrink(model, found.failure)
    if reduced.minimized.inputs != (1, 1, 1) or not reduced.validated_original: raise RuntimeError("shrink witness mismatch")
    trace = record(model, reduced.minimized.inputs)
    result = replay(model, loads(dumps(trace)))
    if result.outcome != "exact" or not result.failure_reproduced: raise RuntimeError("local witness replay mismatch")
    Path(path).write_text(dumps(trace), encoding="utf-8")
    receipt = dict(controls=control, bfs=dict(states=bfs.states, transitions=bfs.transitions, witness=list(bfs.failure.inputs)),
        fuzz=dict(original_steps=len(found.failure.inputs), minimized_steps=len(reduced.minimized.inputs),
                  shrink_attempts=reduced.attempts, validated_original=reduced.validated_original),
        legacy_calls=model.legacy_calls, append_state_calls=model.state_calls, append_transition_calls=model.transition_calls)
elif mode == "replay":
    model = Threshold(); trace = loads(Path(path).read_bytes()); result = replay(model, trace)
    receipt = dict(outcome=result.outcome, failure_reproduced=result.failure_reproduced, steps=len(trace.steps), legacy_calls=model.legacy_calls,
                   append_state_calls=model.state_calls, append_transition_calls=model.transition_calls)
else: raise ValueError("expected record or replay")
print(json.dumps(dict(receipt, package=str(Path(statelessness.__file__).resolve()))))
