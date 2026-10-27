"""Installed public BFS limits/observer application and portable witness replay."""
import json
from pathlib import Path
import sys
import statelessness
from statelessness import (Check, Metadata, Transition, SearchConfig, SearchLimits, RunLimits,
                          enumerate_states, record, replay)
from statelessness.trace_json import dumps, loads


class Counter:
    def __init__(self): self.callbacks = 0
    def initial_state(self): self.callbacks += 1; return 0
    def inputs(self, state): self.callbacks += 1; return (0, 1)
    def step(self, state, input):
        self.callbacks += 1
        if input not in (0, 1): raise ValueError("expected zero or one")
        return Transition(state + input, ("changed",) if input else ())
    def check_state(self, state):
        self.callbacks += 1
        return (Check("threshold.bound", "failed", "at least three") if state >= 3 else Check("threshold.bound"),)
    def estimated_state_bytes(self, state): self.callbacks += 1; return 5
    def metadata(self): return Metadata("native-bfs-threshold", build="application-v1")
    def encode_state(self, state): return str(state).encode("ascii")
    def decode_state(self, data): return int(data.decode("ascii"))
    def encode_input(self, input): return str(input).encode("ascii")
    def decode_input(self, data):
        if data not in (b"0", b"1"): raise ValueError("expected zero or one")
        return int(data)
    def encode_output(self, output): return output.encode("ascii")


class Observer:
    def __init__(self): self.initials = 0; self.histories = []; self.errors = 0
    def needs_history(self): return True
    def initial(self, state, checks): self.initials += 1
    def transition(self, before, input, transition, state_checks, transition_checks, history): self.histories.append(list(history))
    def check_error(self, phase, checks, complete): self.errors += 1


def search(cap=None, cancelled=False):
    model, observer = Counter(), Observer()
    result = enumerate_states(model, SearchConfig(max_states=100, max_transitions=100, max_depth=10),
        limits=SearchLimits(control=RunLimits(cancellation=lambda: cancelled), max_estimated_state_bytes=cap), observer=observer)
    violation = result.failure.violations[0] if result.failure else None
    receipt = dict(termination=result.termination, states=result.states, transitions=result.transitions,
        estimated_retained_state_bytes=result.estimated_retained_state_bytes,
        witness=list(result.failure.inputs) if result.failure else None,
        failure_phase=violation.phase if violation else None, failure_property=violation.check.id if violation else None,
        observed_histories=observer.histories, initial_observations=observer.initials, check_errors=observer.errors,
        model_callbacks=model.callbacks, observer_callbacks=observer.initials + len(observer.histories) + observer.errors)
    return receipt, result.failure


mode, path = sys.argv[1:]
if mode == "record":
    base, failure = search()
    cap, _ = search(14)
    precancel, _ = search(cancelled=True)
    if failure is None: raise RuntimeError("expected BFS witness")
    Path(path).write_text(dumps(record(Counter(), failure.inputs)), encoding="utf-8")
    receipt = {"base": base, "cap": cap, "precancel": precancel}
elif mode == "replay":
    trace = loads(Path(path).read_bytes())
    result = replay(Counter(), trace)
    receipt = dict(outcome=result.outcome, failure_reproduced=result.failure_reproduced, steps=len(trace.steps))
else:
    raise ValueError("expected record or replay")
print(json.dumps(dict(receipt, package=str(Path(statelessness.__file__).resolve()))))
