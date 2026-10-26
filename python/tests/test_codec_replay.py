"""Incremental codec leases, rich replay recovery and exact binary accounting."""
from dataclasses import replace
import unittest
from statelessness import (AuditTrace, Auto, BinaryLimits, Check, Disposition,
    EncodeBuffer, Generated, Metadata, ModelError, OracleState, Paired, ReplayError,
    RunConfig, Trace, TraceStep, Transition, WithOracle, audit_to_bytes,
    record, record_audit, replay, replay_with_observations,
    replay_with_observations_bounded)
from statelessness.codec import encode_value


class Model:
    def __init__(self): self.calls = []
    def metadata(self): self.calls.append("metadata"); return Metadata("codec", build="fixed")
    def initial_state(self): self.calls.append("initial"); return 0
    def step(self, state, input): self.calls.append("step"); return Transition(state + input, (state + input,))
    def check_state(self, state): self.calls.append("state check"); return (Check("state"),)
    def check_transition(self, *args): self.calls.append("edge check"); return (Check("edge"),)
    def encode_state(self, state): raise AssertionError("legacy state")
    def encode_input(self, input): raise AssertionError("legacy input")
    def encode_output(self, output): raise AssertionError("legacy output")
    def encode_state_into(self, state, out): self.calls.append("encode state"); out.append(str(state).encode())
    def encode_input_into(self, input, out): self.calls.append("encode input"); out.append(str(input).encode())
    def encode_output_into(self, output, out): self.calls.append("encode output"); out.append(str(output).encode())
    def decode_state(self, data): self.calls.append("decode state"); return int(data)
    def decode_input(self, data): self.calls.append("decode input"); return int(data)


def saved():
    return Trace(Metadata("codec", build="fixed"), b"0", (Check("state"),),
        (TraceStep(b"1", Disposition(), (b"1",), b"1", (Check("state"), Check("edge"))),), "completed")


class BufferTests(unittest.TestCase):
    def test_clear_exact_sticky_and_mutable_aliases(self):
        raw = bytearray(b"stale")
        buffer = EncodeBuffer(raw, 3)
        self.assertEqual(raw, b"")
        chunk = bytearray(b"ab")
        buffer.append(memoryview(chunk)); chunk[:] = b"xx"
        buffer.append(b"c"); buffer.finish()
        self.assertEqual(buffer.snapshot(), b"abc")
        with self.assertRaises(ModelError): buffer.append(b"d")
        with self.assertRaises(ModelError): buffer.append(b"")
        with self.assertRaises(ModelError): buffer.finish()
        self.assertEqual(raw, b"abc")

    def test_framed_child_expiry_and_error_poison(self):
        raw, escaped = bytearray(), []
        buffer = EncodeBuffer(raw, 20)
        def child(out): escaped.append(out); out.append(b"ab")
        buffer.framed(child); buffer.append(b"z")
        self.assertEqual(buffer.snapshot(), (2).to_bytes(8, "little") + b"abz")
        for operation in (lambda: escaped[0].append(b"x"), escaped[0].finish, escaped[0].snapshot, lambda: len(escaped[0])):
            with self.assertRaisesRegex(ModelError, "expired"): operation()
        def broken(out): out.append(b"x"); raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): buffer.framed(broken)
        with self.assertRaises(ModelError): buffer.finish()

    def test_hook_expiry_sticky_finish_and_explicit_callback_error_precedence(self):
        escaped = []
        class Codec:
            def encode_state_into(self, value, out):
                escaped.append(out)
                try: out.append(b"too big")
                except ModelError: pass
        with self.assertRaisesRegex(ModelError, "byte limit"): encode_value(Codec(), "state", 0, maximum=1)
        with self.assertRaisesRegex(ModelError, "expired"): escaped[0].append(b"")
        class Error(Codec):
            def encode_state_into(self, value, out): super().encode_state_into(value, out); raise RuntimeError("explicit later")
        with self.assertRaisesRegex(ModelError, "explicit later"): encode_value(Error(), "state", 0, maximum=1)

    def test_oracle_incremental_shared_budget_and_wrapper_forwarding(self):
        class History:
            def encode_history(self, value): raise AssertionError("legacy history")
            def encode_history_into(self, value, out): out.append(str(value).encode())
        actual = Model()
        wrapped = WithOracle(actual, History())
        expected = b"ORC1" + (2).to_bytes(8, "little") + b"12" + (2).to_bytes(8, "little") + b"34"
        self.assertEqual(encode_value(wrapped, "state", OracleState(12, 34), maximum=len(expected)), expected)
        with self.assertRaises(ModelError): encode_value(wrapped, "state", OracleState(12, 34), maximum=len(expected)-1)
        for facade in (Auto(actual), Generated(actual, lambda state, rng: 1)):
            self.assertEqual(encode_value(facade, "input", 12), b"12")
        self.assertEqual(encode_value(Paired(actual, object()), "history", 34), b"34")


class RichReplayTests(unittest.TestCase):
    def test_exact_observations_default_record_and_basic_compatibility(self):
        model, observations = Model(), []
        report = replay_with_observations(model, saved(), lambda o, r: observations.append((o, r)))
        self.assertEqual((report.outcome, report.steps_verified), ("exact", 1))
        self.assertEqual([o.kind for o, _ in observations], ["initial", "turn"])
        self.assertEqual(observations[1][0].actual.state_check_count, 1)
        self.assertEqual(observations[1][1].steps_verified, 1)
        self.assertEqual(record(Model(), [1]), saved())
        self.assertEqual(replay(Model(), saved()).outcome, "exact")

    def test_all_four_differences_primary_and_no_second_transition(self):
        class Different(Model):
            def step(self, state, input): self.calls.append("step"); return Transition(2, (3,), Disposition("ignored", "why"))
            def check_state(self, state): return (Check("state" if state == 0 else "different"),)
        trace = replace(saved(), steps=saved().steps * 2)
        model, observations = Different(), []
        result = replay_with_observations(model, trace, lambda o, r: observations.append(o))
        self.assertEqual((result.outcome, result.field, result.steps_verified), ("diverged", "disposition", 0))
        flags = observations[-1].differences
        self.assertTrue(flags.disposition and flags.outputs and flags.state and flags.checks)
        self.assertEqual(model.calls.count("step"), 1)

    def test_recovery_none_unknown_complete_and_secondary_error(self):
        for stage in ("decode", "step", "check", "encode"):
            class Broken(Model):
                def decode_input(self, data):
                    if stage == "decode": raise RuntimeError("broken decode")
                    return super().decode_input(data)
                def step(self, state, input):
                    if stage == "step": raise RuntimeError("broken step")
                    return super().step(state, input)
                def check_transition(self, *args):
                    if stage == "check": raise RuntimeError("broken check")
                    return super().check_transition(*args)
                def encode_output_into(self, output, out):
                    if stage == "encode": raise RuntimeError("broken encode")
                    return super().encode_output_into(output, out)
            observed = []
            def observer(observation, report):
                observed.append(observation)
                if observation.kind == "error": raise RuntimeError("secondary observer")
            with self.assertRaises(ReplayError) as caught: replay_with_observations(Broken(), saved(), observer)
            error = caught.exception
            self.assertIn("broken " + stage, str(error.error))
            self.assertIn("secondary observer", str(error.observer_error))
            self.assertEqual(error.actual is None, stage in ("decode", "step"))
            if error.actual is not None:
                self.assertEqual(error.actual.checks is None, stage == "check")
                self.assertEqual(error.actual.state_check_count, None if stage == "check" else 1)
            self.assertEqual(observed[-1].sequence, 1)

    def test_observer_error_updated_report_no_recursive_error_hook(self):
        kinds = []
        def observer(observation, report):
            kinds.append(observation.kind)
            if observation.kind == "turn":
                self.assertEqual(report.steps_verified, 1)
                raise RuntimeError("stop observer")
        with self.assertRaises(ReplayError) as caught: replay_with_observations(Model(), saved(), observer)
        self.assertEqual(kinds, ["initial", "turn"])
        self.assertEqual(caught.exception.actual.state_check_count, 1)
        self.assertIsNone(caught.exception.observer_error)

    def test_failure_id_reproduction_details_can_differ(self):
        trace = replace(saved(), initial_checks=(Check("state", "failed", "old"),), steps=(), termination="property_failed")
        class Failed(Model):
            def check_state(self, state): return (Check("state", "failed", "new"),)
        report = replay_with_observations(Failed(), trace)
        self.assertEqual((report.outcome, report.field, report.failure_reproduced), ("diverged", "initial checks", True))


class BoundedReplayAndRecordTests(unittest.TestCase):
    def test_actual_binary_exact_boundary_and_artifact_before_decode(self):
        audit = AuditTrace.from_portable(saved(), RunConfig(parameters=(("k", "v"),)))
        size = len(audit_to_bytes(audit))
        report = replay_with_observations_bounded(Model(), audit, limits=BinaryLimits(max_total_bytes=size))
        self.assertEqual(report.outcome, "exact")
        model = Model()
        with self.assertRaises(ReplayError): replay_with_observations_bounded(model, audit, limits=BinaryLimits(max_total_bytes=size-1))
        self.assertNotIn("decode state", model.calls)
        foreign = replace(audit, metadata=Metadata("foreign"))
        self.assertEqual(replay_with_observations_bounded(Model(), foreign, limits=BinaryLimits(max_total_bytes=0)).outcome, "incompatible")

    def test_actual_shared_output_budget_and_all_nine_artifact_limits(self):
        audit = AuditTrace.from_portable(saved(), RunConfig(parameters=(("key", "value"),)))
        class Large(Model):
            def step(self, state, input): self.calls.append("step"); return Transition(1, (1, 2))
            def encode_output_into(self, output, out): self.calls.append("encode output"); out.append(b"x" * 8)
        size = len(audit_to_bytes(audit))
        with self.assertRaises(ReplayError) as caught:
            replay_with_observations_bounded(Large(), audit, limits=BinaryLimits(max_total_bytes=size+1))
        self.assertIsNotNone(caught.exception.actual.checks)
        for field in BinaryLimits.__dataclass_fields__:
            model = Model()
            with self.subTest(limit=field), self.assertRaises(ReplayError):
                replay_with_observations_bounded(model, audit, limits=BinaryLimits(**{field: 0}))
            self.assertNotIn("decode state", model.calls)

    def test_record_audit_stamps_config_and_preserves_coherent_prefix(self):
        trace = record_audit(Model(), [1, 1], RunConfig(parameters=(("k", "one"), ("k", "two"))))
        self.assertEqual(trace.termination, "completed")
        self.assertEqual(trace.config.parameters[:2], (("k", "one"), ("k", "two")))
        self.assertEqual(trace.config.parameters[-1], ("stateless.engine.build", "native-python-v1"))
        self.assertEqual(replay_with_observations_bounded(Model(), trace).outcome, "exact")
        class Broken(Model):
            def encode_output_into(self, output, out):
                if output == 2: raise RuntimeError("second output error")
                super().encode_output_into(output, out)
        broken = record_audit(Broken(), [1, 1], RunConfig())
        self.assertEqual((broken.termination, len(broken.steps)), ("model_error", 1))
        audit_to_bytes(broken)
        with self.assertRaisesRegex(ModelError, "reserved"):
            record_audit(Model(), (), RunConfig(parameters=(("stateless.engine.fake", "x"),)))




class EncodeSharedCorpusTests(unittest.TestCase):
    def test_independent_encoding_and_wrapper_vectors(self):
        import json, os
        from pathlib import Path
        path = os.environ.get("STATELESS_ENCODE_CORPUS", str(Path(__file__).resolve().parents[2] / "conformance/encode-buffers-v1.json"))
        cases = json.loads(Path(path).read_text(encoding="utf-8"))["cases"]
        for case in cases:
            with self.subTest(case=case["case"]):
                events, results = [], []
                raw = bytearray.fromhex(case.get("initial_hex", ""))
                maximum = int(case["maximum"])
                def actions(sink, operations, stop=False):
                    records = []
                    for action in operations:
                        children, ok = [], True
                        try:
                            if action["kind"] == "framed":
                                def callback(child):
                                    children.extend(actions(child, action["actions"], not action.get("ignore_child_errors", False)))
                                    if any(not c["ok"] for c in children) and not action.get("ignore_child_errors", False):
                                        raise ModelError("fixture child error")
                                    if action.get("callback_error", False): raise ModelError("fixture callback error")
                                sink.framed(callback)
                            else:
                                (sink.extend_from_vec if action["kind"] == "legacy" else sink.append)(bytes.fromhex(action["hex"]))
                        except ModelError: ok = False
                        records.append({"ok": ok, "children": children})
                        if stop and not ok: break
                    return records
                def codec(mode, kind):
                    class Codec: pass
                    instance = Codec()
                    def legacy(value):
                        if mode == "into": raise AssertionError("legacy selected")
                        events.append("legacy_" + kind)
                        return str(value).encode("ascii")
                    def into(value, out):
                        events.append("into_" + kind)
                        for chunk in str(value).encode("ascii"): out.append(bytes([chunk]))
                    setattr(instance, "encode_" + kind, legacy)
                    if mode == "into": setattr(instance, "encode_" + kind + "_into", into)
                    return instance
                try:
                    if case["operation"] == "buffer":
                        buffer = EncodeBuffer(raw, maximum)
                        results = actions(buffer, case["actions"])
                        buffer.finish()
                        encoded = buffer.snapshot()
                    else:
                        operation = case["operation"]
                        kind = case.get("encoder", "state")
                        model, value = codec(case["mode"], kind), 12
                        if operation == "automatic": model = Auto(model)
                        elif operation in ("oracle", "nested", "paired"):
                            history = codec(case["history_mode"], "state" if operation == "paired" else "history")
                            if operation == "paired": history = Paired(history, object())
                            model, value = WithOracle(model, history), OracleState(12, 34)
                            if operation == "nested": model, value = WithOracle(model, codec("into", "history")), OracleState(value, 56)
                        encoded = encode_value(model, kind, value, maximum=maximum)
                    ok = True
                except ModelError:
                    ok, encoded = False, None
                self.assertEqual({"finish_ok": ok, "encoded_hex": None if encoded is None else encoded.hex(),
                                  "results": results, "events": events}, case["expected"])


class SnapshotClassificationTests(unittest.TestCase):
    def test_application_snapshot_text_is_not_a_typed_snapshot_failure(self):
        class Broken(Model):
            def check_transition(self, *args): raise ModelError("snapshot: deliberate application error")
        observations = []
        with self.assertRaises(ReplayError):
            replay_with_observations(Broken(), saved(), lambda o, r: observations.append(o))
        self.assertIsNotNone(observations[-1].actual)
        self.assertIsNone(observations[-1].actual_unavailable_reason)


if __name__ == "__main__": unittest.main()


class NativeRecoveryTests(unittest.TestCase):
    def test_entry_budget_capture_once_before_metadata_and_deferred_validation(self):
        trace = AuditTrace.from_portable(saved(), RunConfig())
        ordinary = BinaryLimits()
        class Budgets:
            def __init__(self): self.reads = []; self.small = False
            def __getattr__(self, key):
                self.reads.append(key)
                return 0 if self.small else getattr(ordinary, key)
        limits = Budgets()
        class Mutating(Model):
            def metadata(self): limits.small = True; return super().metadata()
        self.assertEqual(replay_with_observations_bounded(Mutating(), trace, limits=limits).outcome, "exact")
        self.assertEqual(limits.reads, list(BinaryLimits.__dataclass_fields__))
        class Invalid:
            def __getattr__(self, key): raise ValueError("bad budget getter")
        incompatible = replace(trace, metadata=Metadata("different"))
        self.assertEqual(replay_with_observations_bounded(Model(), incompatible, limits=Invalid()).outcome, "incompatible")
        with self.assertRaisesRegex(ReplayError, "bad budget getter"):
            replay_with_observations_bounded(Model(), trace, limits=Invalid())

    def test_transition_snapshot_failure_transfers_original_without_retry_or_restep(self):
        class State:
            failures = 0
            def __deepcopy__(self, memo):
                State.failures += 1
                raise ValueError("uncopyable produced state")
        class Broken(Model):
            steps = 0
            def step(self, state, input):
                self.steps += 1
                return Transition(State(), ())
        model, observations = Broken(), []
        with self.assertRaises(ReplayError) as caught:
            replay_with_observations(model, saved(), lambda o, r: observations.append(o))
        error = caught.exception
        self.assertEqual(model.steps, 1)
        self.assertEqual(State.failures, 1)
        self.assertIsInstance(error.actual.transition.state, State)
        self.assertIsNone(error.actual.checks)
        self.assertIsNone(observations[-1].actual)
        self.assertIn("unavailable", observations[-1].actual_unavailable_reason)

    def test_initial_snapshot_failure_retains_decoded_original(self):
        class State:
            failures = 0
            def __deepcopy__(self, memo): State.failures += 1; raise ValueError("initial snapshot failed")
        original = State()
        class Broken(Model):
            def decode_state(self, data): return original
        observations = []
        with self.assertRaises(ReplayError) as caught:
            replay_with_observations(Broken(), saved(), lambda o, r: observations.append(o))
        self.assertIs(caught.exception.actual.state, original)
        self.assertEqual(State.failures, 1)
        self.assertIsNone(observations[-1].actual)

    def test_observer_sync_void_errors_and_error_observer_secondary(self):
        for returned in ([1], iter([1])):
            events = []
            with self.assertRaisesRegex(ReplayError, "return None") as caught:
                replay_with_observations(Model(), saved(), lambda o, r: (events.append(o.kind), returned)[1])
            self.assertEqual(events, ["initial"])
            self.assertIsNotNone(caught.exception.actual.checks)
            self.assertIsNone(caught.exception.observer_error)
        class Broken(Model):
            def decode_state(self, data): raise ModelError("decode primary")
        async def observer(o, r): pass
        with self.assertRaisesRegex(ReplayError, "decode primary") as caught:
            replay_with_observations(Broken(), saved(), observer)
        self.assertIn("return None", str(caught.exception.observer_error))

    def test_disposition_equality_spoof_cannot_claim_exact(self):
        class Spoof:
            def __eq__(self, other): return True
        class Broken(Model):
            def step(self, state, input):
                d = Disposition()
                object.__setattr__(d, "kind", Spoof())
                return Transition(state + input, (state + input,), d)
        with self.assertRaises(ReplayError) as caught:
            replay_with_observations(Broken(), saved())
        self.assertEqual(caught.exception.report.steps_verified, 0)
        self.assertIsNotNone(caught.exception.actual.checks)

    def test_expired_codec_lease_releases_controller_and_backing_owner(self):
        import gc
        import weakref
        escaped, owners = [], []
        class Codec:
            def encode_state_into(self, value, out):
                escaped.append(out)
                owners.append(weakref.ref(out._EncodeBuffer__encoding))
                out.append(b"a")
        self.assertEqual(encode_value(Codec(), "state", 0), b"a")
        gc.collect()
        self.assertIsNone(owners[0]())
        with self.assertRaisesRegex(ModelError, "expired"): escaped[0].snapshot()

    def test_caught_baseexception_in_framed_child_still_poisoned(self):
        outer = EncodeBuffer(bytearray(), 100)
        def child(out): out.append(b"x"); raise KeyboardInterrupt("stop")
        try: outer.framed(child)
        except KeyboardInterrupt: pass
        with self.assertRaises(ModelError): outer.finish()

    def test_noncontiguous_or_released_view_poison_before_copy(self):
        for view in (memoryview(b"abcd")[::2], memoryview(b"x")):
            if view.nbytes == 1: view.release()
            buffer = EncodeBuffer(bytearray(), 5)
            with self.assertRaises(ModelError): buffer.append(view)
            with self.assertRaises(ModelError): buffer.finish()


class RecoveryOwnershipTests(unittest.TestCase):
    def test_completed_observation_snapshot_failure_is_not_retried(self):
        class State:
            fail = False
            failures = 0
            def __init__(self, value): self.value = value
            def __deepcopy__(self, memo):
                if State.fail:
                    State.failures += 1
                    raise ValueError("observation snapshot unavailable")
                return State(self.value)
        class Mutable(Model):
            steps = 0
            def decode_state(self, data): return State(int(data))
            def step(self, state, input):
                self.steps += 1
                return Transition(State(state.value + input), (state.value + input,))
            def encode_state_into(self, state, out):
                out.append(str(state.value).encode())
                if state.value: State.fail = True
        model, observations = Mutable(), []
        with self.assertRaises(ReplayError) as caught:
            replay_with_observations(model, saved(), lambda o, r: observations.append(o))
        self.assertEqual(model.steps, 1)
        self.assertEqual(State.failures, 1)
        self.assertEqual(caught.exception.report.steps_verified, 1)
        self.assertEqual(caught.exception.actual.checks, (Check("state"), Check("edge")))
        self.assertIsNone(observations[-1].actual)
        self.assertIsNotNone(observations[-1].actual_unavailable_reason)

    def test_observation_mutation_cannot_change_later_turn_or_saved_evidence(self):
        class Mutable(Model):
            def decode_state(self, data): return [int(data)]
            def decode_input(self, data): return [int(data)]
            def step(self, state, input): return Transition([state[0] + input[0]], ([state[0] + input[0]],))
            def encode_state_into(self, value, out): out.append(str(value[0]).encode())
            encode_input_into = encode_state_into
            encode_output_into = encode_state_into
        trace = replace(saved(), steps=saved().steps + (TraceStep(b"1", Disposition(), (b"2",), b"2", (Check("state"), Check("edge"))),))
        observations = []
        def observe(value, current):
            observations.append(value)
            if value.kind == "initial": value.state[0] = 100
            if value.kind == "turn":
                self.assertEqual(value.actual.transition.state[0], value.actual.sequence)
                value.actual.before[0] = 200
                value.actual.input[0] = 200
                value.actual.transition.state[0] = 200
                value.actual.transition.outputs[0][0] = 200
        self.assertEqual(replay_with_observations(Mutable(), trace, observe).steps_verified, 2)
        self.assertEqual(observations[1].actual_state, b"1")
        self.assertEqual(observations[2].actual_state, b"2")

    def test_record_captures_quota_primitives_before_callbacks(self):
        class Limits:
            changed = False
            reads = []
            def __getattr__(self, name):
                self.reads.append(name)
                return 0 if self.changed else getattr(BinaryLimits(), name)
        limits = Limits()
        class Mutating(Model):
            def initial_state(self): limits.changed = True; return super().initial_state()
        trace = record_audit(Mutating(), [1], RunConfig(), limits=limits)
        self.assertEqual(trace.termination, "completed")
        self.assertEqual(limits.reads, list(BinaryLimits.__dataclass_fields__))

    def test_audit_error_reason_truncates_on_utf8_boundary(self):
        class Broken(Model):
            def step(self, state, input): raise ModelError("雪" * 200)
        limits = BinaryLimits(max_string_bytes=32)
        trace = record_audit(Broken(), [1], RunConfig(), limits=limits)
        self.assertEqual(trace.termination, "model_error")
        self.assertLessEqual(len(trace.error.encode("utf8")), 32)
        self.assertTrue(trace.error.endswith("..."))
        audit_to_bytes(trace, limits=limits)


class HostileExceptionTests(unittest.TestCase):
    def test_framed_callback_unprintable_error_still_poisoned(self):
        class Hostile(BaseException):
            def __str__(self): raise RuntimeError("cannot format")
        buffer = EncodeBuffer(bytearray(), 100)
        def child(out): out.append(b"partial"); raise Hostile()
        with self.assertRaises(Hostile): buffer.framed(child)
        with self.assertRaisesRegex(ModelError, "callback failed"): buffer.finish()

    def test_wrapped_encoder_unprintable_error_still_poisoned(self):
        from statelessness.codec import _dispatch_encode
        class Hostile(BaseException):
            def __str__(self): raise RuntimeError("cannot format")
        class Inner:
            def encode_state_into(self, value, out): out.append(b"partial"); raise Hostile()
        outer = EncodeBuffer(bytearray(), 100)
        with self.assertRaises(Hostile): _dispatch_encode(Inner(), "state", 0, outer)
        with self.assertRaisesRegex(ModelError, "callback failed"): outer.finish()
