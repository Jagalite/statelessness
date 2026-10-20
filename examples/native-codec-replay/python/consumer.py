"""Installed preferred bounded codec and rich replay application consumer."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import statelessness
from statelessness import (Check, Metadata, Transition, EncodeBuffer, ReplayError,
    record_audit, replay, replay_with_observations, replay_with_observations_bounded)
from statelessness.trace_binary import RunConfig, BinaryLimits, audit_to_bytes, audit_from_bytes
from statelessness.trace_json import dumps, loads

LIMITS = BinaryLimits(max_total_bytes=4096, max_frame_bytes=1024, max_blob_bytes=64, max_string_bytes=128,
    max_steps=16, max_checks_per_step=16, max_outputs_per_step=16, max_parameters=16, max_items=128)


class Threshold:
    def __init__(self, overrun=False): self.legacy = self.into = self.decode = self.steps = 0; self.leases = []; self.overrun = overrun
    def metadata(self): return Metadata("native-codec-replay-threshold", build="application-v1")
    def initial_state(self): return 0
    def step(self, state, input):
        self.steps += 1
        if input not in (0, 1): raise ValueError("expected zero or one")
        return Transition(state + input, (("x" * 65 if self.overrun else "changed"),) if input else ())
    def check_state(self, state):
        return (Check("threshold.bound", "failed", "at least three") if state >= 3 else Check("threshold.bound"),)
    def legacy_encode(self, value): self.legacy += 1; raise RuntimeError("legacy encoder invoked")
    encode_state = encode_input = encode_output = legacy_encode
    def encode(self, value, sink):
        self.into += 1; self.leases.append(sink)
        for byte in str(value).encode("ascii"): sink.append(bytes((byte,)))
    encode_state_into = encode_input_into = encode_output_into = encode
    def decode_state(self, data): self.decode += 1; return int(data.decode("ascii"))
    def decode_input(self, data):
        self.decode += 1
        if data not in (b"0", b"1"): raise ValueError("expected zero or one")
        return int(data)


def checks(values): return [dict(id=c.id, status=c.status, details=c.details) for c in values]
def disposition(value): return dict(kind=value.kind, reason=value.reason)
def step(value): return dict(input=value.input.hex(), disposition=disposition(value.disposition), outputs=[v.hex() for v in value.outputs], post_state=value.post_state.hex(), checks=checks(value.checks))
def report(value): return dict(outcome=value.outcome, divergence_step=value.step, divergence_field=value.field,
    steps_verified=value.steps_verified, failure_reproduced=value.failure_reproduced, build_matches=value.build_matches)
def differences(value): return {name: getattr(value, name) for name in ("disposition", "outputs", "state", "checks")}
def observation(value, current):
    if value.kind == "initial":
        return dict(kind="initial", sequence=0, state=value.state, checks=checks(value.checks), expected_state=value.expected_state.hex(),
                    actual_state=value.actual_state.hex(), expected_checks=checks(value.expected_checks), differences=differences(value.differences), report=report(current))
    if value.kind != "turn": raise RuntimeError("unexpected error observation in exact replay")
    actual = value.actual
    return dict(kind="turn", sequence=actual.sequence, before=actual.before, input=actual.input,
        transition=dict(state=actual.transition.state, outputs=list(actual.transition.outputs), disposition=disposition(actual.transition.disposition)),
        checks=checks(actual.checks), state_check_count=actual.state_check_count, expected=step(value.expected), actual_input=value.actual_input.hex(),
        actual_outputs=[v.hex() for v in value.actual_outputs], actual_state=value.actual_state.hex(), differences=differences(value.differences), report=report(current))
def session(audit, bounded=False):
    model, observations = Threshold(), []
    def observer(value, current): observations.append(observation(value, current))
    if bounded: result = replay_with_observations_bounded(model, audit, observer, limits=LIMITS)
    else: result = replay_with_observations(model, audit.to_portable()[0], observer)
    return dict(report=report(result), observations=observations), model
def config(value): return dict(strategy=value.strategy, seed=None if value.seed is None else str(value.seed), parameters=value.parameters)
def expired(sink):
    operations = [lambda: sink.append(b""), lambda: sink.extend_from_slice(b""), lambda: sink.extend_from_vec(b""), lambda: sink.extend(b""),
        sink.finish, sink.snapshot, lambda: len(sink), lambda: sink.framed(lambda child: None)]
    for operation in operations:
        try: operation()
        except Exception as error:
            if "expired" not in str(error).lower(): raise
        else: raise RuntimeError("escaped buffer remained active")


def controls(audit, recorded_model):
    storage = bytearray(b"stale"); sink = EncodeBuffer(storage, 32)
    if storage != b"": raise RuntimeError("buffer did not clear entry")
    children = []
    def nested(child):
        children.append(child)
        child.framed(lambda grandchild: grandchild.append(b"ab"))
    sink.framed(nested)
    for child in children: expired(child)
    sink.append(b"z"); sink.finish()
    if bytes(storage) != (10).to_bytes(8, "little") + (2).to_bytes(8, "little") + b"abz": raise RuntimeError("nested frame bytes mismatch")
    sticky = EncodeBuffer(bytearray(), 1)
    try: sticky.append(b"ab")
    except Exception: pass
    else: raise RuntimeError("oversize chunk accepted")
    for operation in (lambda: sticky.append(b""), sticky.finish):
        try: operation()
        except Exception: pass
        else: raise RuntimeError("ignored error lost sticky failure")
    short = EncodeBuffer(bytearray(), 7); called = []
    try: short.framed(lambda child: called.append(True))
    except Exception: pass
    else: raise RuntimeError("oversize frame prefix accepted")
    if called: raise RuntimeError("frame callback ran before prefix admission")
    shared = EncodeBuffer(bytearray(), 9)
    try: shared.framed(lambda child: child.append(b"ab"))
    except Exception: pass
    else: raise RuntimeError("framing granted an independent body budget")
    for lease in recorded_model.leases: expired(lease)
    before = Threshold()
    try: replay_with_observations_bounded(before, audit, limits=replace(LIMITS, max_steps=2))
    except ReplayError: pass
    else: raise RuntimeError("stored artifact limit was not rejected")
    if before.decode != 0 or before.steps != 0 or before.into != 0 or before.legacy != 0:
        raise RuntimeError("stored artifact preflight executed decoder, reducer, or encoder")
    oversized = Threshold(overrun=True)
    try: replay_with_observations_bounded(oversized, audit, limits=LIMITS)
    except ReplayError as error:
        actual = error.actual
        if (actual is None or actual.kind != "turn" or actual.sequence != 1 or actual.before != 0 or actual.input != 1
                or actual.transition.state != 1 or actual.transition.outputs != ("x" * 65,) or actual.checks is None
                or actual.state_check_count != 1 or oversized.steps != 1): raise RuntimeError("bounded replay lost actual turn")
    else: raise RuntimeError("actual encoding quota ignored")
    last = audit.steps[-1]
    changed = replace(last, checks=(replace(last.checks[0], details="different expected detail"),))
    divergent = replace(audit, steps=audit.steps[:-1] + (changed,))
    varied, divergent_model = session(divergent)
    if varied["report"]["outcome"] != "diverged" or not varied["report"]["failure_reproduced"]: raise RuntimeError("rich divergence lost failure identity")
    return dict(clear_entry=True, sticky_finish=True, framed_shared_budget=True, nested_framing=True,
        preflight_decode_calls=before.decode, oversize_reducer_calls=oversized.steps, oversize_actual_retained=True,
        legacy_encoder_calls=before.legacy + oversized.legacy + divergent_model.legacy, codec_into_calls=before.into + oversized.into + divergent_model.into,
        rich_divergence=True, escaped_sink_expired=True, nested_child_expired=True, outer_active_after_nested=True)


mode, path = sys.argv[1:]
if mode == "record":
    model = Threshold(); audit = record_audit(model, [1, 1, 1], RunConfig("manual", None, ()), max_steps=9, limits=LIMITS)
    Path(path + ".bin").write_bytes(audit_to_bytes(audit, limits=LIMITS))
    portable, retained_config = audit.to_portable(); Path(path + ".json").write_text(dumps(portable), encoding="utf-8")
    rich, rich_model = session(audit); bounded, bounded_model = session(audit, True)
    receipt = dict(rich=rich, bounded=bounded, retained_config=config(retained_config), controls=controls(audit, model),
        legacy_encoder_calls=model.legacy + rich_model.legacy + bounded_model.legacy,
        codec_into_calls=model.into + rich_model.into + bounded_model.into, steps=len(audit.steps), termination=audit.termination)
elif mode == "replay":
    audit = audit_from_bytes(Path(path).read_bytes(), limits=LIMITS)
    rich, rich_model = session(audit); bounded, bounded_model = session(audit, True)
    receipt = dict(rich=rich, bounded=bounded, retained_config=config(audit.config),
        legacy_encoder_calls=rich_model.legacy + bounded_model.legacy, codec_into_calls=rich_model.into + bounded_model.into)
elif mode == "replay-json":
    model = Threshold(); result = replay(model, loads(Path(path).read_bytes()))
    receipt = dict(report(result), legacy_encoder_calls=model.legacy, codec_into_calls=model.into)
else: raise ValueError("expected record, replay, or replay-json")
print(json.dumps(dict(receipt, producer="python", mode=mode, package=str(Path(statelessness.__file__).resolve()))))
