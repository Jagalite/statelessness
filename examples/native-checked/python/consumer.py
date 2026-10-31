"""Installed checked handoff and bounded runtime Recorder application."""
from dataclasses import replace
import io
import json
from pathlib import Path
import sys
import statelessness
from statelessness import (Check, Metadata, Transition, EncodeBuffer, ReplayError,
    check_initial, check_turn, CheckPolicy, CheckedInitial, Recorder, RecorderOptions, replay, replay_with_observations, replay_with_observations_bounded)
from statelessness.trace_binary import AuditTrace, RunConfig, BinaryLimits, audit_to_bytes, audit_from_bytes
from statelessness.trace_json import dumps, loads
from statelessness.observation import (Event, LogLevel, NoPayloads, ObservationOptions,
    SnapshotPolicy, TextObserver)

LIMITS = BinaryLimits(max_total_bytes=8192, max_frame_bytes=2048, max_blob_bytes=64, max_string_bytes=256,
    max_steps=16, max_checks_per_step=16, max_outputs_per_step=16, max_parameters=32, max_items=256)


class Threshold:
    def __init__(self, overrun=False): self.legacy = self.into = self.decode = self.steps = 0; self.leases = []; self.overrun = overrun; self.initial_calls = self.state_calls = self.edge_calls = 0
    def metadata(self): return Metadata("native-checked-threshold", build="application-v1")
    def initial_state(self): self.initial_calls += 1; return 0
    def step(self, state, input):
        self.steps += 1
        if input not in (0, 1): raise ValueError("expected zero or one")
        return Transition(state + input, (("x" * 65 if self.overrun else "changed"),) if input else ())
    def check_state(self, state): raise RuntimeError("legacy state checker invoked")
    def check_transition(self, before, input, transition): raise RuntimeError("legacy edge checker invoked")
    def check_state_into(self, state, sink):
        self.state_calls += 1
        sink.append(Check("threshold.bound", "failed", "at least three") if state >= 3 else Check("threshold.bound"))
        sink.append(Check("state.appended"))
    def check_transition_into(self, before, input, transition, sink):
        self.edge_calls += 1; sink.append(Check("edge.checked"))
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
def session(trace, bounded=False):
    model, observations = Threshold(), []
    def observer(value, current): observations.append(observation(value, current))
    if bounded: result = replay_with_observations_bounded(model, trace, observer, limits=LIMITS)
    else:
        portable = trace.to_portable()[0] if isinstance(trace, AuditTrace) else trace
        result = replay_with_observations(model, portable, observer)
    return dict(report=report(result), observations=observations), model
def config(value): return dict(strategy=value.strategy, seed=None if value.seed is None else str(value.seed), parameters=value.parameters)
def options(max_steps=3): return RecorderOptions(max_steps, 8192, LIMITS)
def attach(model, state=0, origin=0, max_steps=3):
    initial = check_initial(model, state)
    return Recorder.with_checked_initial_at(initial, origin, RunConfig("manual"), options(max_steps))
def token(model, before, sequence, policy=CheckPolicy(), output="changed"):
    return check_turn(model, before, 1, Transition(before+1, (output,)), sequence, policy)
def rejected(operation):
    try: operation()
    except Exception: return True
    raise RuntimeError("expected rejection")
def counts(model): return dict(initial_state=model.initial_calls, reducer=model.steps, state=model.state_calls, transition=model.edge_calls)
def suffix_counts(model):
    actual = counts(model)
    if actual != dict(initial_state=0, reducer=1, state=2, transition=1): raise RuntimeError("suffix replay counts differ")
    return actual
def controls():
    model = Threshold(); initial = check_initial(model, 1)
    before = counts(model); rec = Recorder.with_checked_initial_at(initial, 10, RunConfig("manual"), options())
    if counts(model) != before: raise RuntimeError("attach rechecked")
    for sequence, state in ((11, 1), (12, 2)):
        checked = token(model, state, sequence); before = counts(model); rec.observe_checked(checked)
        if counts(model) != before: raise RuntimeError("handoff rechecked")
    if rec.snapshot().initial_state != b"1" or not rec.is_frozen() or ("stateless.monitor.sequence_origin", "10") not in rec.snapshot().config.parameters: raise RuntimeError("origin lost")
    model = Threshold(); initial = check_initial(model, 0)
    first = token(model, 0, 1); left = Recorder.with_checked_initial(initial, options=options()); right = Recorder.with_checked_initial(initial, options=options())
    left.observe_checked(first); right.observe_checked(first)
    if left.snapshot() != right.snapshot(): raise RuntimeError("token not reusable")
    for kind in ("sampled", "duplicate", "gap"):
        rec = attach(model)
        if kind == "duplicate": rec.observe_checked(first); bad = first
        else: bad = token(model, 0, 2 if kind == "gap" else 1, CheckPolicy(2) if kind == "sampled" else CheckPolicy())
        rejected(lambda: rec.observe_checked(bad))
        prefix = rec.snapshot()
        if not rec.is_frozen() or prefix.termination != "model_error" or len(prefix.steps) != (1 if kind == "duplicate" else 0): raise RuntimeError("rejection did not freeze prefix")
        rejected(lambda: rec.observe_checked(first))
        if prefix != rec.snapshot(): raise RuntimeError("frozen prefix changed")
    # Diagnostic views and public construction cannot mint checked authority.
    rec = attach(model); rejected(lambda: rec.observe_checked(first.observation())); rejected(lambda: CheckedInitial())
    detached = first.observation()
    rejected(lambda: setattr(first, "sequence", 7))
    if first.sequence != 1 or first.transition.state != 1 or len(first.into_checks()) != 3: raise RuntimeError("accessor ownership changed context")
    rec = attach(model); rec.observe_checked(first)
    bad = token(model, 1, 2, output="x"*65); before = counts(model); rejected(lambda: rec.observe_checked(bad))
    if counts(model) != before: raise RuntimeError("bounded error reran checks")
    prefix = rec.snapshot(); encoded = audit_to_bytes(prefix, limits=LIMITS)
    if prefix.termination != "model_error" or len(prefix.steps) != 1 or audit_to_bytes(audit_from_bytes(encoded, limits=LIMITS), limits=LIMITS) != encoded: raise RuntimeError("bounded failure prefix incoherent")
    ring_model = Threshold(); ring_rec = attach(ring_model, max_steps=1)
    ring_rec.observe_checked(token(ring_model, 0, 1)); ring_rec.observe_checked(token(ring_model, 1, 2))
    ring = ring_rec.snapshot()
    if ring.initial_state != b"1" or len(ring.initial_checks) != 2 or any(c.id == "edge.checked" for c in ring.initial_checks) or ring_rec.evicted_steps() != 1: raise RuntimeError("ring promoted edge checks")
    rich, _ = session(ring); bounded, _ = session(ring, True)
    return dict(check_once=True, attach_rechecks=False, handoff_rechecks=False, token_reused=True, sampled_rejected=True,
        duplicate_rejected=True, gap_rejected=True, origin_preserved=True, ring_state_prefix_only=True, bounded_error_prefix=True,
        forged_diagnostic_rejected=True, accessor_ownership=True), dict(rich=rich,bounded=bounded,retained_config=config(ring.config))


mode, path = sys.argv[1:]
if mode == "record":
    model = Threshold(); initial = check_initial(model, 0)
    rec = Recorder.with_checked_initial(initial, RunConfig("manual"), options())
    for sequence in (1, 2, 3):
        actual = model.step(sequence-1, 1)
        checked = check_turn(model, sequence-1, 1, actual, sequence)
        if sequence == 1: first_checked = checked
        before = counts(model); rec.observe_checked(checked)
        if counts(model) != before: raise RuntimeError("checked handoff reran application")
    audit = rec.snapshot(); main_counts = counts(model)
    if main_counts != dict(initial_state=0,reducer=3,state=4,transition=3): raise RuntimeError("main check-once counts differ")
    control_receipt, ring = controls()
    Path(path + ".bin").write_bytes(audit_to_bytes(audit, limits=LIMITS))
    portable, retained_config = audit.to_portable(); Path(path + ".json").write_text(dumps(portable), encoding="utf-8")
    rich, rich_model = session(audit); bounded, bounded_model = session(audit, True)
    receipt = dict(rich=rich, bounded=bounded, retained_config=config(retained_config), controls=control_receipt, ring=ring, main_counts=main_counts,
        checked=dict(initial_checks=initial.state_check_count,state_check_count=first_checked.state_check_count,full_checking=first_checked.full_checking,sequence=str(first_checked.sequence)),
        legacy_encoder_calls=model.legacy + rich_model.legacy + bounded_model.legacy,
        codec_into_calls=model.into + rich_model.into + bounded_model.into, steps=len(audit.steps), termination=audit.termination)
elif mode in ("record-suffix", "record-failing-suffix"):
    failing = mode == "record-failing-suffix"
    turn_count = 3 if failing else 2
    model = Threshold(); state = 0
    initial = check_initial(model, state)
    before = counts(model)
    rec = Recorder.with_checked_initial_at(initial, 40, RunConfig("manual"), options(1))
    if counts(model) != before: raise RuntimeError("suffix attach reran application")
    observer_prefix = "suffix-observer\n"
    writer = io.BytesIO(); writer.write(observer_prefix.encode("utf-8"))
    observer = TextObserver(writer, "suffix",
        ObservationOptions(LogLevel.TRACE, SnapshotPolicy.EVERY_TRANSITION, 1024), NoPayloads())
    before_codecs = (model.legacy, model.into, model.decode)
    observer.observe(model, Event.Checkpoint(40, initial.state, initial.checks))
    if counts(model) != before or (model.legacy, model.into, model.decode) != before_codecs: raise RuntimeError("suffix checkpoint observer ran application or codec callbacks")
    for sequence in range(41, 41 + turn_count):
        actual = model.step(state, 1)
        checked = check_turn(model, state, 1, actual, sequence)
        if sequence == 41: first_checked = checked
        before = counts(model)
        # Detached diagnostics do not authorize exact recording; retain the token.
        view = checked.observation()
        before_codecs = (model.legacy, model.into, model.decode)
        observer.observe(model, Event.Transition(view.sequence, view.before, view.input, view.transition, view.checks))
        if counts(model) != before or (model.legacy, model.into, model.decode) != before_codecs: raise RuntimeError("suffix turn observer ran application or codec callbacks")
        rec.observe_checked(checked)
        if counts(model) != before: raise RuntimeError("suffix checked handoff reran application")
        state = actual.state
    before_codecs = (model.legacy, model.into, model.decode)
    observer.flush()
    if counts(model) != before or (model.legacy, model.into, model.decode) != before_codecs: raise RuntimeError("suffix observer flush ran application or codec callbacks")
    observer_text = writer.getvalue().decode("utf-8")
    audit = rec.snapshot(); main_counts = counts(model)
    if main_counts != dict(initial_state=0,reducer=turn_count,state=turn_count+1,transition=turn_count): raise RuntimeError("suffix check-once counts differ")
    if state != turn_count or checked.sequence != 40+turn_count: raise RuntimeError("suffix final application turn differs")
    state_checks = (Check("threshold.bound"), Check("state.appended"))
    terminal_checks = ((Check("threshold.bound", "failed", "at least three"), Check("state.appended")) if failing else state_checks) + (Check("edge.checked"),)
    if audit.initial_state != str(turn_count-1).encode("ascii") or audit.initial_checks != state_checks: raise RuntimeError("suffix checkpoint lost state-only checks")
    if len(audit.steps) != 1 or audit.steps[0].post_state != str(turn_count).encode("ascii") or audit.steps[0].checks != terminal_checks: raise RuntimeError("suffix retained turn differs")
    if audit.termination != ("property_failed" if failing else "interrupted") or rec.is_frozen() != failing or rec.observed_steps() != turn_count or rec.evicted_steps() != turn_count-1: raise RuntimeError("suffix recorder status differs")
    parameters = dict(audit.config.parameters)
    for name, expected in (("sequence_origin", "40"), ("checkpoint_after_sequence", str(turn_count-1)), ("observed_steps", str(turn_count)), ("evicted_steps", str(turn_count-1))):
        if parameters.get("stateless.monitor." + name) != expected: raise RuntimeError("suffix local metadata differs: " + name)
    binary = audit_to_bytes(audit, limits=LIMITS)
    portable, retained_config = audit.to_portable()
    portable_bytes = dumps(portable).encode("utf-8")
    if failing:
        before = counts(model); before_codecs = (model.legacy, model.into, model.decode)
        # Supplied diagnostics have no checked authority, even after a failure.
        freeze_writer = io.BytesIO()
        freeze_observer = TextObserver(freeze_writer, "suffix",
            ObservationOptions(LogLevel.TRACE, SnapshotPolicy.EVERY_TRANSITION, 1024), NoPayloads())
        freeze_observer.observe(model, Event.Checkpoint(44, 999, ()))
        freeze_observer.flush()
        if counts(model) != before or (model.legacy, model.into, model.decode) != before_codecs: raise RuntimeError("frozen suffix observer ran application or codec callbacks")
        # Reuse sequence 43's authentic token; do not execute or check another turn.
        rejected(lambda: rec.observe_checked(checked))
        after = rec.snapshot()
        if (not rec.is_frozen() or counts(model) != before
                or (model.legacy, model.into, model.decode) != before_codecs
                or audit_to_bytes(after, limits=LIMITS) != binary
                or dumps(after.to_portable()[0]).encode("utf-8") != portable_bytes):
            raise RuntimeError("frozen suffix artifact or callback counts changed")
        if counts(model) != before or (model.legacy, model.into, model.decode) != before_codecs: raise RuntimeError("frozen artifact comparison ran application or codec callbacks")
        if freeze_writer.getvalue() != b'Trace stream="suffix" event=checkpoint sequence=44 checks=0 state=unavailable\n': raise RuntimeError("frozen diagnostic differs")
    Path(path + ".bin").write_bytes(binary)
    Path(path + ".json").write_bytes(portable_bytes)
    rich, rich_model = session(audit); bounded, bounded_model = session(audit, True)
    receipt = dict(rich=rich, bounded=bounded, rich_counts=suffix_counts(rich_model), bounded_counts=suffix_counts(bounded_model), retained_config=config(retained_config), main_counts=main_counts,
        checked=dict(initial_checks=initial.state_check_count,state_check_count=first_checked.state_check_count,full_checking=first_checked.full_checking,sequence=str(first_checked.sequence)),
        observer_prefix=observer_prefix, observer_text=observer_text,
        legacy_encoder_calls=model.legacy + rich_model.legacy + bounded_model.legacy,
        codec_into_calls=model.into + rich_model.into + bounded_model.into, steps=len(audit.steps), termination=audit.termination)
    if failing:
        receipt.update(freeze_verified=True, freeze_observer_text=freeze_writer.getvalue().decode("utf-8"))
elif mode in ("replay", "replay-suffix"):
    audit = audit_from_bytes(Path(path).read_bytes(), limits=LIMITS)
    rich, rich_model = session(audit); bounded, bounded_model = session(audit, True)
    receipt = dict(rich=rich, bounded=bounded, retained_config=config(audit.config),
        legacy_encoder_calls=rich_model.legacy + bounded_model.legacy, codec_into_calls=rich_model.into + bounded_model.into)
    if mode == "replay-suffix":
        receipt.update(rich_counts=suffix_counts(rich_model), bounded_counts=suffix_counts(bounded_model))
elif mode == "replay-json":
    model = Threshold(); result = replay(model, loads(Path(path).read_bytes()))
    receipt = dict(report(result), legacy_encoder_calls=model.legacy, codec_into_calls=model.into)
elif mode == "replay-suffix-json":
    portable = loads(Path(path).read_bytes())
    rich, rich_model = session(portable)
    # Bounded replay requires an audit envelope. JSON contains no configuration;
    # explicit defaults supply that envelope without claiming recovered provenance.
    audit = AuditTrace.from_portable(portable, RunConfig())
    bounded, bounded_model = session(audit, True)
    receipt = dict(rich=rich, bounded=bounded, rich_counts=suffix_counts(rich_model), bounded_counts=suffix_counts(bounded_model),
        legacy_encoder_calls=rich_model.legacy + bounded_model.legacy, codec_into_calls=rich_model.into + bounded_model.into)
else: raise ValueError("expected record, record-suffix, record-failing-suffix, replay, replay-suffix, replay-json, or replay-suffix-json")
print(json.dumps(dict(receipt, producer="python", mode=mode, package=str(Path(statelessness.__file__).resolve()))))
