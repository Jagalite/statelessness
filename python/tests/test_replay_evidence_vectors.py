"""Independent normalized shared rich-replay and audit-recording vectors."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import unittest
from statelessness import (BinaryLimits, Check, Disposition, Metadata, ModelError,
    ReplayError, RunConfig, Transition, audit_to_bytes, record_audit,
    replay_with_observations, replay_with_observations_bounded)
from test_trace_binary import fixture_trace


def checks(values):
    return None if values is None else [asdict(c) for c in values]


def disposition(value): return asdict(value)


def transition(value):
    return {"state": value.state, "outputs": list(value.outputs), "disposition": disposition(value.disposition)}


def actual(value):
    if value is None: return None
    result = {"kind": value.kind, "sequence": value.sequence, "checks": checks(value.checks)}
    if value.kind == "initial": result["state"] = value.state
    else: result.update(before=value.before, input=value.input, transition=transition(value.transition), state_check_count=value.state_check_count)
    return result


def report(value):
    return dict(outcome=value.outcome, divergence_step=value.step, divergence_field=value.field,
        steps_verified=value.steps_verified, failure_reproduced=value.failure_reproduced, build_matches=value.build_matches)


def observation(value, current):
    result = {"kind": value.kind, "report": report(current)}
    if value.kind == "error": result.update(sequence=value.sequence, actual=actual(value.actual))
    elif value.kind == "initial":
        result.update(sequence=0, state=value.state, checks=checks(value.checks), expected_state=value.expected_state.hex(),
            actual_state=value.actual_state.hex(), expected_checks=checks(value.expected_checks), differences=asdict(value.differences))
    else:
        result.update(actual(value.actual)); result["kind"] = "turn"
        expected = value.expected
        result.update(expected=dict(input=expected.input.hex(), disposition=disposition(expected.disposition),
            outputs=[o.hex() for o in expected.outputs], post_state=expected.post_state.hex(), checks=checks(expected.checks)),
            actual_input=value.actual_input.hex(), actual_outputs=[o.hex() for o in value.actual_outputs],
            actual_state=value.actual_state.hex(), differences=asdict(value.differences))
    return result


class Fixture:
    def __init__(self, settings, events): self.settings, self.events = settings, events
    def event(self, value):
        self.events.append(value)
        if self.settings.get("error_at") == value: raise ModelError("fixture codec replay error")
    def metadata(self):
        self.event("metadata")
        return Metadata(self.settings.get("name", "codec-fixture"), build=self.settings.get("build", "candidate-v1"))
    def initial_state(self): return 0
    def decode_state(self, value): self.event("decode_state:" + value.hex()); return int(value)
    def decode_input(self, value): self.event("decode_input:" + value.hex()); return int(value)
    def encode_state_into(self, value, out): self.event(f"encode_state:{value}"); out.append(str(value).encode("ascii"))
    def encode_input_into(self, value, out): self.event(f"encode_input:{value}"); out.append(str(value).encode("ascii"))
    def encode_output_into(self, value, out):
        self.event(f"encode_output:{value}")
        out.append(str(value).encode("ascii") + b"x" * self.settings.get("output_padding", 0))
    def encode_state(self, value): raise AssertionError("legacy state codec called")
    def encode_input(self, value): raise AssertionError("legacy input codec called")
    def encode_output(self, value): raise AssertionError("legacy output codec called")
    def step(self, before, input):
        self.event(f"step:{before}:{input}")
        kind = self.settings.get("disposition", "accepted")
        return Transition(before + input + self.settings.get("state_delta", 0),
            (before + input + self.settings.get("output_delta", 0),) * self.settings.get("output_count", 1),
            Disposition(kind, "" if kind == "accepted" else self.settings.get("disposition_reason", "why")))
    def check(self, setting, default):
        c = self.settings.get(setting)
        return (default,) if c is None else (Check(c["id"], c["status"], c.get("reason") or ""),)
    def check_state(self, value):
        self.event(f"check_state:{value}")
        return self.check("initial_check" if value == 0 else "turn_state_check", Check("ok"))
    def check_transition(self, before, input, result):
        self.event(f"check_transition:{before}:{input}:{result.state}")
        return self.check("turn_transition_check", Check("edge"))


class ReplayVectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = os.environ.get("STATELESS_REPLAY_CORPUS") or Path(__file__).resolve().parents[2] / "conformance/replay-evidence-v1.json"
        cls.corpus = json.loads(Path(path).read_text())
    def test_shared_replay_vectors(self):
        for case in self.corpus["cases"]:
            with self.subTest(case=case["case"]):
                events, observations = [], []
                model, trace = Fixture(case["model"], events), fixture_trace(case["trace"])
                def observer(value, current):
                    observations.append(observation(value, current))
                    event = "observe_initial" if value.kind == "initial" else f"observe_{value.kind}:{value.sequence if value.kind == 'error' else value.actual.sequence}"
                    events.append(event)
                    key = value.kind if value.kind != "turn" else f"turn:{value.actual.sequence}"
                    if key == case["observer_error_at"]: raise ModelError("fixture observer error")
                try:
                    if case["bounded"]:
                        current = replay_with_observations_bounded(model, trace, observer, limits=BinaryLimits(**case["limits"]), allow_build_mismatch=case["allow_build_mismatch"])
                    else:
                        portable, _ = trace.to_portable()
                        current = replay_with_observations(model, portable, observer, allow_build_mismatch=case["allow_build_mismatch"])
                    result = dict(report=report(current), observations=observations, error=False, actual=None, observer_error=False)
                except ReplayError as error:
                    result = dict(report=report(error.report), observations=observations, error=True, actual=actual(error.actual), observer_error=error.observer_error is not None)
                    if case["error_contains"]: self.assertIn(case["error_contains"], str(error))
                self.assertEqual(result, case["expected"])
                self.assertEqual(events, case["events"])
    def test_shared_record_vectors(self):
        for case in self.corpus["record_cases"]:
            with self.subTest(case=case["case"]):
                config = RunConfig(**case["config"])
                limits = BinaryLimits(**case["limits"])
                if case["budget_formula"]:
                    baseline = record_audit(Fixture({}, []), case["inputs"], config)
                    # Passing terminal footer is18; reserved error footer is278.
                    total = len(audit_to_bytes(baseline)) + 260
                    if case["budget_formula"].endswith("-1"): total -= 1
                    limits = replace(limits, max_total_bytes=total)
                try:
                    trace = record_audit(Fixture({"error_at": case["error_at"]}, []), case["inputs"], config, limits=limits)
                    result = dict(error=False, termination=trace.termination, steps=len(trace.steps))
                    audit_to_bytes(trace, limits=limits)
                except ModelError:
                    result = dict(error=True, termination=None, steps=None)
                self.assertEqual(result, case["expected"])
