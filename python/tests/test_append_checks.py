"""Append dispatch, callback lease ownership and complete/partial boundaries."""
import unittest
from statelessness import (Auto, Check, CheckPolicy, CheckSink, Failure, FuzzConfig,
    Generated, Metadata, ModelError, Paired, SearchConfig, ShrinkConfig, Transition,
    Violation, WithOracle, check_observed, check_observed_into, enumerate_states,
    fuzz, record, replay, shrink)


class AppendModel:
    def metadata(self): return Metadata("append", build="native-test")
    def initial_state(self): return 0
    def inputs(self, state): return (1,)
    def generate(self, state, rng): return 1
    def step(self, state, input): return Transition(state + input)
    def check_state(self, state): raise AssertionError("legacy state must not execute")
    def check_transition(self, *args): raise AssertionError("legacy transition must not execute")
    def check_state_into(self, state, checks):
        checks.append(Check("bound", "failed", "high") if state >= 2 else Check("bound"))
    def check_transition_into(self, before, input, transition, checks): checks.extend((Check("edge"),))
    def encode_state(self, state): return bytes([state])
    def decode_state(self, data): return data[0]
    def encode_input(self, input): return bytes([input])
    def decode_input(self, data): return data[0]
    def encode_output(self, output): return bytes([output])


class AppendDispatchTests(unittest.TestCase):
    def test_observed_policy_is_validated_and_snapshotted_before_callbacks(self):
        policy = CheckPolicy(1, True)
        class Mutating(AppendModel):
            def check_state_into(self, state, sink):
                object.__setattr__(policy, 'transition_checks', False)
                sink.append(Check('state'))
            def check_transition_into(self, before, input, transition, sink):
                sink.append(Check('transition'))
        checks = check_observed(Mutating(), 0, 1, Transition(1), 1, policy)
        self.assertEqual([item.id for item in checks], ['state', 'transition'])
        object.__setattr__(policy, 'state_every', 0)
        buffer = [Check('old')]
        with self.assertRaises(ValueError):
            check_observed_into(Mutating(), 0, 1, Transition(1), 1, buffer, policy)
        self.assertEqual(buffer, [])

    def test_all_native_paths_prefer_append_hooks(self):
        for model in (AppendModel(), Auto(AppendModel()),
                      Generated(AppendModel(), lambda state, rng: 1)):
            self.assertEqual([c.id for c in check_observed(model, 0, 1, Transition(1), 1)], ["bound", "edge"])
            bfs = enumerate_states(model)
            self.assertEqual(bfs.failure.inputs, (1, 1))
            found = fuzz(model, FuzzConfig(cases=1)).failure
            self.assertEqual(found.inputs, (1, 1))
            result = shrink(model, found, ShrinkConfig(10))
            self.assertTrue(result.validated_original)
            trace = record(model, found.inputs)
            self.assertEqual(trace.termination, "property_failed")
            self.assertEqual(replay(model, trace).outcome, "exact")

    def test_legacy_fallback_exactly_once_and_partial_iterator(self):
        calls = []
        class Legacy:
            def check_state(self, state): calls.append("state"); return (Check("same"), Check("same", "skipped", "later"))
            def check_transition(self, *args): calls.append("edge"); return ()
        values = check_observed(Legacy(), 0, 1, Transition(1), 1)
        self.assertEqual(calls, ["state", "edge"])
        self.assertEqual([c.id for c in values], ["same", "same"])
        class Partial(AppendModel):
            check_state_into = None
            def check_state(self, state):
                yield Check("partial")
                raise RuntimeError("iterator failed")
        errors = []
        class Observer:
            def check_error(self, phase, checks, complete): errors.append((phase, checks, complete))
        with self.assertRaisesRegex(ModelError, "iterator failed"): enumerate_states(Partial(), observer=Observer())
        self.assertEqual(errors, [("initial_state", (Check("partial"),), False)])

    def test_observed_into_reuse_clear_on_all_errors_and_policy(self):
        buffer = [Check("old")]
        identity = id(buffer)
        check_observed_into(AppendModel(), 0, 1, Transition(1), 1, buffer)
        self.assertEqual((id(buffer), [c.id for c in buffer]), (identity, ["bound", "edge"]))
        check_observed_into(AppendModel(), 0, 1, Transition(1), 1, buffer, CheckPolicy(2, False))
        self.assertEqual([c.id for c in buffer], ["stateless.state_checks", "stateless.transition_checks"])
        for sequence in (0, True, -1):
            buffer.append(Check("old"))
            with self.assertRaises(ValueError): check_observed_into(AppendModel(), 0, 1, Transition(1), sequence, buffer)
            self.assertEqual(buffer, [])
        class Broken(AppendModel):
            def check_transition_into(self, before, input, transition, checks):
                checks.append(Check("partial")); raise RuntimeError("broken")
        with self.assertRaisesRegex(ModelError, "broken"): check_observed_into(Broken(), 0, 1, Transition(1), 1, buffer)
        self.assertEqual(buffer, [])
        class Panic(AppendModel):
            def check_state_into(self, state, checks): checks.append(Check("partial")); raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt): check_observed_into(Panic(), 0, 1, Transition(1), 1, buffer)
        self.assertEqual(buffer, [])
        class Uncopyable:
            def __deepcopy__(self, memo): raise RuntimeError("snapshot failed")
        buffer.append(Check("old"))
        with self.assertRaisesRegex(ModelError, "snapshot failed"):
            check_observed_into(AppendModel(), 0, 1, Transition(Uncopyable()), 1, buffer)
        self.assertEqual(buffer, [])


class SinkLeaseTests(unittest.TestCase):
    def assertExpired(self, sink):
        for operation in (lambda: sink.append(Check("late")), lambda: sink.extend([Check("late")]),
                          lambda: sink.reserve(1), sink.snapshot, lambda: len(sink),
                          lambda: iter(sink), lambda: sink[0]):
            with self.assertRaisesRegex(ModelError, "expired"): operation()

    def test_escaped_normal_and_exception_leases_cannot_touch_reused_storage(self):
        escaped = []
        class Capturing(AppendModel):
            def check_state_into(self, state, checks): escaped.append(checks); checks.append(Check("safe"))
        buffer = []
        check_observed_into(Capturing(), 0, 1, Transition(1), 1, buffer)
        self.assertExpired(escaped[0])
        check_observed_into(AppendModel(), 0, 1, Transition(1), 1, buffer)
        previous = tuple(buffer)
        self.assertExpired(escaped[0])
        self.assertEqual(tuple(buffer), previous)
        class Broken(Capturing):
            def check_state_into(self, state, checks): super().check_state_into(state, checks); raise RuntimeError("return error")
        with self.assertRaisesRegex(ModelError, "return error"): check_observed_into(Broken(), 0, 1, Transition(1), 1, buffer)
        self.assertExpired(escaped[-1])
        self.assertEqual(buffer, [])

    def test_checks_and_inspection_are_detached(self):
        original = Check("original")
        buffer = []
        sink = CheckSink(buffer)
        sink.append(original)
        object.__setattr__(original, "id", "changed")
        snapshot = sink.snapshot()
        object.__setattr__(snapshot[0], "id", "changed snapshot")
        self.assertEqual(buffer, [Check("original")])
        self.assertFalse(hasattr(sink, "clear"))
        self.assertFalse(hasattr(sink, "checks"))
        with self.assertRaises(TypeError): sink[0] = Check("replace")

    def test_sticky_validation_failure_even_when_caught_and_later_error(self):
        for kind in ("append", "extend", "reserve"):
            errors = []
            class Invalid(AppendModel):
                def check_state_into(self, state, checks):
                    checks.append(Check("valid"))
                    try:
                        if kind == "append": checks.append(object())
                        elif kind == "extend": checks.extend([Check("second"), object()])
                        else: checks.reserve(-1)
                    except ModelError: pass
            class Observer:
                def check_error(self, phase, checks, complete): errors.append((phase, checks, complete))
            with self.assertRaises(ModelError): enumerate_states(Invalid(), observer=Observer())
            expected = (Check("valid"), Check("second")) if kind == "extend" else (Check("valid"),)
            self.assertEqual(errors, [("initial_state", expected, False)])
            buffer = [Check("old")]
            with self.assertRaises(ModelError): check_observed_into(Invalid(), 0, 1, Transition(1), 1, buffer)
            self.assertEqual(buffer, [])

    def test_check_failure_with_unprintable_exception_remains_sticky(self):
        class BadDetail(Exception):
            def __str__(self): raise RuntimeError('detail failed')
        class BadCheck(Check):
            def __getattribute__(self, name):
                if name == 'id': raise BadDetail()
                return super().__getattribute__(name)
        class Invalid(AppendModel):
            def check_state_into(self, state, checks):
                checks.append(Check('valid'))
                bad = object.__new__(BadCheck)
                object.__setattr__(bad, 'id', 'bad')
                object.__setattr__(bad, 'status', 'passed')
                object.__setattr__(bad, 'details', '')
                try: checks.append(bad)
                except Exception: pass
                try: checks.append(Check('should-not-pass'))
                except Exception: pass
        observed = []
        class Observer:
            def check_error(self, phase, checks, complete): observed.append((phase, checks, complete))
        with self.assertRaises(ModelError): enumerate_states(Invalid(), observer=Observer())
        self.assertEqual(observed, [('initial_state', (Check('valid'),), False)])

    def test_exception_formatting_cannot_reenter_a_parent_sink(self):
        from statelessness.core import _dispatch_checks
        class Parent:
            def check_state_into(self, state, outer):
                outer.append(Check('before'))
                class BadDetail(Exception):
                    def __str__(self):
                        try: outer.append(Check('reentrant'))
                        except ModelError: pass
                        return 'bad check'
                class BadCheck(Check):
                    def __getattribute__(self, name):
                        if name == 'id': raise BadDetail()
                        return super().__getattribute__(name)
                bad = object.__new__(BadCheck)
                object.__setattr__(bad, 'id', 'bad')
                object.__setattr__(bad, 'status', 'passed')
                object.__setattr__(bad, 'details', '')
                class Child:
                    def check_state_into(self, state, inner): inner.append(bad)
                try: _dispatch_checks(Child(), 'check_state_into', 'check_state', (state,), outer, 'nested')
                except ModelError: pass
        values = []
        with self.assertRaises(ModelError):
            _dispatch_checks(Parent(), 'check_state_into', 'check_state', (0,), values, 'outer')
        self.assertEqual(values, [Check('before')])

    def test_nested_lease_expires_but_outer_and_preceding_checks_remain(self):
        from statelessness.core import _dispatch_checks
        captured = []
        class Child:
            def check_state_into(self, state, checks):
                captured.append(checks)
                self.seen = checks.snapshot()
                checks.append(Check("child"))
        child = Child()
        class Parent(AppendModel):
            def check_state_into(self, state, checks):
                checks.append(Check("parent"))
                _dispatch_checks(child, "check_state_into", "check_state", (state,), checks, "nested")
                self_test.assertExpired(captured[0])
                checks.append(Check("after"))
        self_test = self
        result = check_observed(Parent(), 0, 1, Transition(1), 1)
        self.assertEqual(child.seen, (Check("parent"),))
        self.assertEqual([c.id for c in result], ["parent", "child", "after", "edge"])


class OracleAppendTests(unittest.TestCase):
    def test_actual_oracle_and_reference_projection_order_with_nested_expiry(self):
        escaped = []
        class Actual(AppendModel):
            def check_state_into(self, state, checks): escaped.append(checks); checks.append(Check("actual"))
        class History:
            def initial_state(self): return 0
            def advance(self, before, input, outputs, disposition): return before + input
            def check_state(self, history, actual): raise AssertionError("legacy oracle")
            def check_state_into(self, history, actual, checks):
                SinkLeaseTests().assertExpired(escaped[-1])
                self.seen = checks.snapshot()
                checks.append(Check("oracle"))
        history = History()
        model = WithOracle(Actual(), history)
        state = model.initial_state()
        self.assertEqual([c.id for c in model.check_state(state)], ["actual", "oracle"])
        self.assertEqual(history.seen, (Check("actual"),))
        class Projection:
            def actual_state(self, value): return value
            def reference_state(self, value): return value
            def actual_effect(self, value): return value
            def reference_effect(self, value): return value
        paired = Paired(Actual(), Projection())
        self.assertEqual([c.id for c in paired.check_state(0, 0)], ["actual", "pair.state"])

    def test_partial_actual_and_oracle_append_error_observation(self):
        class History:
            def initial_state(self): return 0
            def check_state_into(self, history, actual, checks):
                checks.append(Check("oracle partial")); raise RuntimeError("oracle failed")
        errors = []
        class Observer:
            def check_error(self, phase, checks, complete): errors.append((phase, checks, complete))
        with self.assertRaisesRegex(ModelError, "oracle failed"):
            enumerate_states(WithOracle(AppendModel(), History()), observer=Observer())
        self.assertEqual(errors, [("initial_state", (Check("bound"), Check("oracle partial")), False)])




class SynchronousAppendTests(unittest.TestCase):
    def test_nonvoid_and_async_append_hooks_are_errors(self):
        for returned in (True, (), [], 1):
            class NonVoid(AppendModel):
                def check_state_into(self, state, checks): checks.append(Check("partial")); return returned
            with self.assertRaisesRegex(ModelError, "return None synchronously"):
                enumerate_states(NonVoid())
        class Async(AppendModel):
            async def check_state_into(self, state, checks): checks.append(Check("async must not run"))
        with self.assertRaisesRegex(ModelError, "return None synchronously"):
            enumerate_states(Async())




class SharedAppendCorpusTests(unittest.TestCase):
    def test_shared_order_prefix_and_dispatch_vectors(self):
        import json
        from pathlib import Path
        from dataclasses import asdict
        from statelessness import OracleState, Trace, TraceStep, Disposition
        from statelessness.core import _dispatch_checks
        cases = json.loads((Path(__file__).resolve().parents[2] / "conformance/append-checks-v1.json").read_text(encoding="utf-8"))["cases"]
        for case in cases:
            with self.subTest(case=case["case"]):
                events, observations = [], []
                class Fixture:
                    def __init__(self, reference=False): self.prefix = "reference_" if reference else ""
                    def metadata(self): return Metadata("append-fixture", build="fixed")
                    def initial_state(self): events.append(self.prefix + "initial"); return 0
                    def inputs(self, state): return (1,) if state == 0 else ()
                    def generate(self, state, rng): return 1
                    def step(self, state, input): events.append(f"{self.prefix}step:{state}:{input}"); return Transition(state + input)
                    def check_state(self, state):
                        if case["mode"] == "append": raise AssertionError("legacy state selected")
                        events.append(f"{self.prefix}legacy_state:{state}")
                        if case["error_at"] == f"state:{state}": raise ModelError("fixture append error")
                        values = [Check("same"), Check("skip", "skipped", "not run")]
                        if case["fail_state"] == state: values.append(Check("bad", "failed", "broken"))
                        return values
                    def check_transition(self, before, input, transition):
                        if case["mode"] == "append": raise AssertionError("legacy transition selected")
                        events.append(f"{self.prefix}legacy_transition:{transition.state}")
                        if case["error_at"] == f"transition:{transition.state}": raise ModelError("fixture append error")
                        return (Check("same"),)
                    def check_state_into(self, state, sink):
                        events.append(f"{self.prefix}append_state:{state}:{len(sink)}")
                        sink.append(Check("same"))
                        if case["error_at"] == f"state:{state}" or self.prefix and case["wrapper_error"] == "reference_state":
                            raise ModelError("fixture append error")
                        sink.append(Check("skip", "skipped", "not run"))
                        if case["fail_state"] == state: sink.append(Check("bad", "failed", "broken"))
                    def check_transition_into(self, before, input, transition, sink):
                        events.append(f"{self.prefix}append_transition:{transition.state}:{len(sink)}")
                        sink.append(Check("same"))
                        if case["error_at"] == f"transition:{transition.state}": raise ModelError("fixture append error")
                    def encode_state(self, state): return str(state).encode("ascii")
                    def decode_state(self, data): return int(data)
                    encode_input = encode_state
                    decode_input = decode_state
                    encode_output = encode_state
                model = Fixture()
                if case["mode"] == "legacy":
                    model.check_state_into = model.check_transition_into = None
                class Observer:
                    def initial(self, state, checks):
                        events.append("observe_initial")
                        observations.append({"phase": "initial_state", "complete": True, "checks": [asdict(c) for c in checks]})
                    def transition(self, before, input, transition, state_checks, edge_checks, history):
                        events.append("observe_transition")
                        for phase, checks in (("state", state_checks), ("transition", edge_checks)):
                            observations.append({"phase": phase, "complete": True, "checks": [asdict(c) for c in checks]})
                    def check_error(self, phase, checks, complete):
                        events.append(f"check_error:{phase}:{str(complete).lower()}")
                        observations.append({"phase": phase, "complete": complete, "checks": [asdict(c) for c in checks]})
                class History:
                    def check_state_into(self, history, actual, sink):
                        events.append(f"oracle_state:{actual}:{len(sink)}")
                        sink.append(Check("oracle.state"))
                        if case["wrapper_error"] == "oracle_state": raise ModelError("fixture append error")
                        sink.append(Check("oracle.skip", "skipped", "not run"))
                    def check_transition_into(self, before, input, transition, sink):
                        events.append(f"oracle_transition:{transition.state.model}:{len(sink)}")
                        sink.append(Check("oracle.transition"))
                        if case["wrapper_error"] == "oracle_transition": raise ModelError("fixture append error")
                        sink.append(Check("oracle.skip", "skipped", "not run"))
                class Project:
                    def actual_state(self, state):
                        events.append("projection_actual_state")
                        if case["wrapper_error"] == "projection_actual_state": raise ModelError("fixture append error")
                        return state
                    def reference_state(self, state): events.append("projection_reference_state"); return state
                    def actual_effect(self, output): return output
                    def reference_effect(self, output): return output
                values = [Check("sentinel")]
                outcome, witness, attempts = "ok", None, None
                operation = case["operation"]
                public_checks = operation in ("direct_state", "direct_transition", "observed", "record",
                                               "oracle_state", "oracle_transition", "paired_state", "paired_transition")
                try:
                    if operation == "direct_state":
                        _dispatch_checks(model, "check_state_into", "check_state", (1,), values, "state check")
                    elif operation == "direct_transition":
                        _dispatch_checks(model, "check_transition_into", "check_transition", (0, 1, Transition(1)), values, "transition check", optional=True)
                    elif operation == "observed":
                        check_observed_into(model, 0, 1, Transition(1), case["sequence"], values,
                                            CheckPolicy(case["state_every"], case["transition_checks"]))
                    elif operation == "bfs":
                        result = enumerate_states(model, observer=Observer()); outcome = result.termination
                        witness = None if result.failure is None else list(result.failure.inputs)
                    elif operation == "fuzz":
                        result = fuzz(model, FuzzConfig(cases=1, max_steps=1), observer=Observer()); outcome = result.termination
                        witness = None if result.failure is None else list(result.failure.inputs)
                    elif operation == "shrink":
                        original = Failure((1,), (Violation("state", Check("bad", "failed", "broken")),))
                        result = shrink(model, original); outcome, witness, attempts = result.termination, list(result.minimized.inputs), result.attempts
                    elif operation == "record":
                        result = record(model, (1,)); outcome = result.termination
                        values = list(result.initial_checks) + [c for step in result.steps for c in step.checks]
                    elif operation == "replay":
                        base = (Check("same"), Check("skip", "skipped", "not run"))
                        trace = Trace(model.metadata(), b"0", base,
                                      (TraceStep(b"1", Disposition(), (), b"1", base + (Check("same"),)),), "completed")
                        outcome = replay(model, trace).outcome
                    else:
                        oracle = History() if operation.startswith("oracle") else Paired(Fixture(True), Project())
                        wrapped = WithOracle(model, oracle)
                        if operation.endswith("state"):
                            _dispatch_checks(wrapped, "check_state_into", "check_state", (OracleState(1, 1),), values, "wrapper state")
                        else:
                            _dispatch_checks(wrapped, "check_transition_into", "check_transition",
                                             (OracleState(0, 0), 1, Transition(OracleState(1, 1))), values, "wrapper transition")
                except (ModelError, ValueError) as error:
                    outcome = "error"
                    if operation == "record": public_checks = False
                    if case["error_contains"] is not None:
                        # Native integer validation keeps its established wording.
                        if case["sequence"] != 0: self.assertIn(case["error_contains"], str(error))
                actual = {"outcome": outcome, "checks": [asdict(c) for c in values] if public_checks else None,
                          "observations": observations, "witness": witness, "attempts": attempts}
                self.assertEqual(actual, case["expected"])
                self.assertEqual(events, case["events"])




class MalformedRuntimeCheckTests(unittest.TestCase):
    def test_status_objects_and_spoofing_subclasses_cannot_bypass_validation(self):
        class EqualAnything:
            def __eq__(self, other): return True
        class Spoof(str):
            def __eq__(self, other): return True
        for status in (EqualAnything(), Spoof("unknown")):
            malformed = Check("bad", status)
            class Invalid(AppendModel):
                def check_state_into(self, state, sink):
                    try: sink.append(malformed)
                    except ModelError: pass
            with self.assertRaises(ModelError): enumerate_states(Invalid())
        class ValidSubclass(str): pass
        source = Check(ValidSubclass("ok"), ValidSubclass("passed"), ValidSubclass(""))
        values = []
        CheckSink(values).append(source)
        self.assertTrue(all(type(field) is str for field in (values[0].id, values[0].status, values[0].details)))




class ExpiredSinkRetentionTests(unittest.TestCase):
    def test_retained_expired_view_does_not_pin_storage_or_future_checks(self):
        import gc
        import weakref
        escaped = []
        class Capturing(AppendModel):
            def check_state_into(self, state, checks): escaped.append(checks); checks.append(Check("retained"))
        buffer = []
        check_observed_into(Capturing(), 0, 1, Transition(1), 1, buffer)
        reference = weakref.ref(buffer[0])
        del buffer
        gc.collect()
        self.assertIsNone(reference())
        SinkLeaseTests().assertExpired(escaped[0])

    def test_generator_append_hook_is_not_silently_consumed(self):
        class Lazy(AppendModel):
            def check_state_into(self, state, checks):
                checks.append(Check("must not run"))
                yield Check("also must not run")
        with self.assertRaisesRegex(ModelError, "return None synchronously"):
            enumerate_states(Lazy())




class NestedStickyFailureTests(unittest.TestCase):
    def test_parent_cannot_swallow_child_validation_failure(self):
        from statelessness.core import _dispatch_checks
        class Child:
            def check_state_into(self, state, sink):
                sink.append(Check("child prefix"))
                try: sink.append(object())
                except ModelError: pass
        class Parent(AppendModel):
            def check_state_into(self, state, sink):
                sink.append(Check("parent"))
                try: _dispatch_checks(Child(), "check_state_into", "check_state", (state,), sink, "child")
                except ModelError: pass
        errors = []
        class Observer:
            def check_error(self, phase, checks, complete): errors.append((phase, checks, complete))
        with self.assertRaises(ModelError): enumerate_states(Parent(), observer=Observer())
        self.assertEqual(errors, [("initial_state", (Check("parent"), Check("child prefix")), False)])




class FirstValidationFailureTests(unittest.TestCase):
    def test_validation_error_precedes_later_callback_error(self):
        class Invalid(AppendModel):
            def check_state_into(self, state, sink):
                try: sink.append(object())
                except ModelError: pass
                raise RuntimeError("later callback error")
        with self.assertRaisesRegex(ModelError, "checker must emit Check") as caught:
            enumerate_states(Invalid())
        self.assertNotIn("later callback error", str(caught.exception))


if __name__ == "__main__": unittest.main()
