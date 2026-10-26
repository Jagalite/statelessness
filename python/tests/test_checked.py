"""Checked authority, detached context and independent public Rust vectors."""
from copy import copy, deepcopy
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import pickle
import unittest
from statelessness import (Check, CheckPolicy, CheckedInitial, CheckedTurn,
    Disposition, ModelError, Transition, TurnObservation, check_initial, check_turn)
from statelessness.checked import _initial_context, _turn_context


class Plain:
    def __init__(self): self.events = []; self.initial_calls = self.step_calls = 0
    def initial_state(self): self.initial_calls += 1; raise AssertionError("no initial")
    def step(self, *args): self.step_calls += 1; raise AssertionError("no reducer")
    def check_state_into(self, state, sink): self.events.append("state"); sink.append(Check("s"))
    def check_transition_into(self, before, input, transition, sink): self.events.append("edge"); sink.append(Check("t"))


class CheckedTests(unittest.TestCase):
    def test_noncodec_supplied_checkpoint_and_turn_full_u64(self):
        model = Plain()
        initial = check_initial(model, {"value": [7]})
        self.assertIs(initial.model, model)
        self.assertEqual(initial.state, {"value": [7]})
        self.assertEqual(initial.checks, (Check("s"),))
        actual = Transition({"value": [9]}, ([90],), Disposition("ignored", "why"))
        turn = check_turn(model, {"value": [7]}, [2], actual, (1 << 64) - 1)
        self.assertIs(turn.model, model)
        self.assertEqual(turn.sequence, (1 << 64) - 1)
        self.assertEqual(turn.state_check_count, 1)
        self.assertEqual(turn.checks, (Check("s"), Check("t")))
        self.assertTrue(turn.full_checking)
        self.assertEqual(model.events, ["state", "state", "edge"])
        self.assertEqual((model.initial_calls, model.step_calls), (0, 0))

    def test_tokens_and_all_accessor_payloads_are_detached_and_read_only(self):
        model = Plain()
        state, input = [7], [2]
        transition = Transition([9], ([90],))
        initial = check_initial(model, state)
        token = check_turn(model, state, input, transition, 1)
        state[0] = input[0] = transition.state[0] = transition.outputs[0][0] = -1
        initial.state[0] = -2
        token.before[0] = token.input[0] = token.transition.state[0] = token.transition.outputs[0][0] = -2
        observed = token.observation()
        observed.before[0] = observed.input[0] = observed.transition.state[0] = observed.transition.outputs[0][0] = -3
        self.assertEqual(initial.state, [7])
        self.assertEqual((token.before, token.input, token.transition.state, token.transition.outputs), ([7], [2], [9], ([90],)))
        for name, value in (("sequence", 8), ("checks", ()), ("full_checking", False), ("before", [8])):
            with self.assertRaises(AttributeError): setattr(token, name, value)
        # Even bypassing Check's frozen dataclass on a returned copy cannot alter
        # the privately retained canonical evidence.
        object.__setattr__(token.checks[0], "id", "changed")
        self.assertEqual(token.checks[0].id, "s")
        object.__setattr__(initial.checks[0], "id", "changed")
        self.assertEqual(initial.checks[0].id, "s")
        self.assertIs(copy(token), token)
        self.assertIs(deepcopy(token), token)
        self.assertEqual(token.into_checks(), token.checks)
        self.assertEqual(token.observation().checks, token.checks)

    def test_no_public_construction_replacement_subclass_pickle_or_diagnostic_authority(self):
        for kind in (CheckedInitial, CheckedTurn):
            with self.assertRaises(TypeError): kind()
            with self.assertRaises(TypeError): type("Forged", (kind,), {})
        initial, turn = check_initial(Plain(), 7), check_turn(Plain(), 7, 2, Transition(9), 1)
        with self.assertRaises(TypeError): replace(turn, sequence=3)
        with self.assertRaises(TypeError): pickle.dumps(turn)
        for value in ({}, turn.observation(), object.__new__(CheckedTurn), initial):
            with self.assertRaisesRegex(ModelError, "authentic CheckedTurn"): _turn_context(value)
        for value in ({}, object.__new__(CheckedInitial), turn):
            with self.assertRaisesRegex(ModelError, "authentic CheckedInitial"): _initial_context(value)

    def test_mutating_checkers_do_not_replace_certified_context(self):
        before, input, actual = [7], [2], Transition([9], ([90],))
        class Mutating(Plain):
            def check_state_into(self, value, sink):
                value[0] = -10
                before[0] = input[0] = actual.state[0] = -20
                sink.append(Check("s"))
            def check_transition_into(self, b, i, t, sink):
                self_seen.append((b[0], i[0], t.state[0]))
                b[0] = i[0] = t.state[0] = t.outputs[0][0] = -30
                sink.append(Check("t"))
        self_seen = []
        token = check_turn(Mutating(), before, input, actual, 1)
        self.assertEqual(self_seen, [(7, 2, 9)])
        self.assertEqual((token.before, token.input, token.transition.state, token.transition.outputs), ([7], [2], [9], ([90],)))

    def test_failed_checks_still_certify_full_ordered_batch(self):
        class Failing(Plain):
            def check_state_into(self, state, sink): sink.extend((Check("s", "failed", "bad"), Check("skip", "skipped", "later")))
        model = Failing()
        self.assertEqual(check_initial(model, 7).state_check_count, 2)
        token = check_turn(model, 7, 2, Transition(9), 1)
        self.assertEqual(token.state_check_count, 2)
        self.assertEqual(token.checks[-1], Check("t"))
        self.assertTrue(token.full_checking)

    def test_check_or_snapshot_errors_make_no_token_and_never_repeat(self):
        for stage in ("state", "edge"):
            class Broken(Plain):
                def check_state_into(self, state, sink):
                    super().check_state_into(state, sink)
                    if stage == "state": raise ModelError("broken")
                def check_transition_into(self, b, i, t, sink):
                    super().check_transition_into(b, i, t, sink)
                    raise ModelError("broken")
            model, original = Broken(), Transition([9], ([90],))
            with self.assertRaisesRegex(ModelError, "broken"): check_turn(model, [7], [2], original, 1)
            self.assertEqual(model.events, ["state"] if stage == "state" else ["state", "edge"])
            self.assertEqual(original, Transition([9], ([90],)))
        class Uncopyable:
            def __deepcopy__(self, memo): raise RuntimeError("cannot copy")
        model = Plain()
        with self.assertRaisesRegex(ModelError, "snapshot"): check_initial(model, Uncopyable())
        self.assertEqual(model.events, [])
        with self.assertRaisesRegex(ModelError, "snapshot"): check_turn(model, 7, 2, Transition(Uncopyable()), 1)
        self.assertEqual(model.events, [])
        for sequence in (0, -1, 1 << 64, True, 1.0):
            with self.assertRaises((ValueError, ModelError)): check_turn(model, 7, 2, Transition(9), sequence)
        self.assertEqual(model.events, [])

    def test_native_malformed_check_caught_error_and_baseexception_leave_no_token(self):
        class Invalid(Plain):
            def check_state_into(self, state, sink):
                sink.append(Check("prefix"))
                try: sink.append("wrong")
                except ModelError: pass
        with self.assertRaises(ModelError): check_initial(Invalid(), 7)
        class Interrupt(Plain):
            def check_state_into(self, state, sink): self.saved = sink; raise KeyboardInterrupt()
        model = Interrupt()
        with self.assertRaises(KeyboardInterrupt): check_initial(model, 7)
        with self.assertRaisesRegex(ModelError, "expired"): model.saved.append(Check("late"))


class SharedCheckedTests(unittest.TestCase):
    def test_shared_tokens(self):
        path = os.environ.get("STATELESS_CHECKED_CORPUS") or Path(__file__).resolve().parents[2] / "conformance/checked-results-v1.json"
        corpus = json.loads(Path(path).read_text(encoding="utf-8"))
        for case in corpus["cases"]:
            with self.subTest(case=case["case"]):
                settings, events = case["model"], []
                class Fixture(Plain):
                    def check_state_into(self, state, sink):
                        events.append(f"state:{state}")
                        sink.extend(Check(**c) for c in settings["state_checks"])
                        if settings["state_error"]: raise ModelError(settings["state_error"])
                    def check_transition_into(self, before, input, transition, sink):
                        events.append(f"transition:{before}:{input}:{transition.state}")
                        sink.extend(Check(**c) for c in settings["transition_checks"])
                        if settings["transition_error"]: raise ModelError(settings["transition_error"])
                model, normalized, error = Fixture(), None, None
                try:
                    if case["kind"] == "initial":
                        token = check_initial(model, case["state"])
                        normalized = dict(kind="initial", state=token.state, checks=[asdict(c) for c in token.checks], state_check_count=token.state_check_count)
                    else:
                        t = case["transition"]
                        transition = Transition(t["state"], t["outputs"], Disposition(**t["disposition"]))
                        policy = CheckPolicy(int(case["policy"]["state_every"]), case["policy"]["transition_checks"])
                        token = check_turn(model, case["before"], case["input"], transition, int(case["sequence"]), policy)
                        normalized = dict(kind="turn", before=token.before, input=token.input,
                            transition=dict(state=token.transition.state, outputs=list(token.transition.outputs), disposition=asdict(token.transition.disposition)),
                            sequence=str(token.sequence), checks=[asdict(c) for c in token.checks],
                            state_check_count=token.state_check_count, full_checking=token.full_checking)
                except (ModelError, ValueError) as failure: error = str(failure)
                expected = case["expected"]
                if expected["error_contains"]: self.assertIn(expected["error_contains"], error)
                else: self.assertIsNone(error)
                self.assertEqual(normalized, expected["token"])
                self.assertEqual(events, expected["events"])
                self.assertEqual((model.initial_calls, model.step_calls), (expected["initial_state_calls"], expected["step_calls"]))


class CheckedWrapperTests(unittest.TestCase):
    def test_oracle_and_generation_wrappers_share_tokens_without_rechecking(self):
        from statelessness import Auto, Generated, Metadata, OracleState, Recorder, WithOracle
        class Actual(Plain):
            def metadata(self): return Metadata("actual")
            def inputs(self, state): return (1,)
            def encode_state_into(self, value, out): out.append(str(value).encode())
            encode_input_into = encode_state_into
            encode_output_into = encode_state_into
        class Oracle:
            def __init__(self): self.checks = 0
            def metadata(self): return Metadata("oracle")
            def check_state_into(self, history, actual, sink): self.checks += 1; sink.append(Check("oracle"))
            def encode_history_into(self, history, out): out.append(str(history).encode())
        for wrap in (lambda m: m, Auto, lambda m: Generated(m, lambda s, r: 1)):
            actual, oracle = Actual(), Oracle()
            model = WithOracle(wrap(actual), oracle)
            before, after = OracleState(7, 70), OracleState(9, 90)
            initial = check_initial(model, before)
            recorder = Recorder.with_checked_initial(initial)
            token = check_turn(model, before, 2, Transition(after, (90,)), 1)
            counts = (actual.events[:], oracle.checks)
            self.assertEqual(token.state_check_count, 2)
            self.assertEqual(recorder.observe_checked(token), (Check("s"), Check("oracle"), Check("t")))
            self.assertEqual((actual.events, oracle.checks), counts)
            self.assertEqual(recorder.retained_steps(), 1)
