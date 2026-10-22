#!/usr/bin/env python3
"""Installed public-API producer for the independently frozen guided-v2 corpus.

The fixture knows only case inputs. Expectations are used for schema admission
and post-production comparison. No search algorithm lives here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import struct
import sys


AUTHORITY = "a4cc11eea8db62115b452eee3c6610c04fa06b96"
EXPECTED_FIELDS = {"report", "error", "trace", "generator_rng", "witness_replays", "shrink"}
PHASES = {"initial_state": "InitialState", "state": "State", "transition": "Transition"}
STATUS = {"passed": "Passed", "failed": "Failed", "skipped": "Skipped"}
DISPOSITIONS = {"accepted": "Accepted", "rejected": "Rejected", "ignored": "Ignored"}
DISPOSITION_KINDS = {value: key for key, value in DISPOSITIONS.items()}
FUZZ_TERMINATIONS = {
    "cases_completed": "CasesCompleted", "transition_limit": "TransitionLimit",
    "failure_found": "FailureFound", "cancelled": "Cancelled", "deadline": "Deadline",
}
SHRINK_TERMINATIONS = {
    "search_complete": "SearchComplete", "attempt_limit": "AttemptLimit",
    "transition_limit": "TransitionLimit", "cancelled": "Cancelled", "deadline": "Deadline",
}
SHRINK_OUTCOMES = {
    "retained": "Retained", "no_failure": "NoFailure", "different_failure": "DifferentFailure",
    "invalid_causality": "InvalidCausality", "already_seen": "AlreadySeen",
    "resource_limit": "ResourceLimit",
}


def strict_json(data):
    def pairs(items):
        result = {}
        for name, value in items:
            if name in result:
                raise ValueError(f"duplicate JSON key: {name}")
            result[name] = value
        return result

    def constant(value):
        raise ValueError(f"non-finite JSON value: {value}")

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_frozen_corpus(path, freeze_path, corpus_sha256=None, freeze_sha256=None):
    # Freeze admission precedes imports, candidate callbacks and comparisons.
    # These bytes are read once, so a concurrent rewrite cannot replace the
    # already admitted expectations between production and comparison.
    freeze_bytes = freeze_path.read_bytes()
    freeze = strict_json(freeze_bytes)
    if not isinstance(freeze, dict) or not freeze:
        raise ValueError("FREEZE.json must be a nonempty JSON object")
    if freeze_sha256 is not None and digest(freeze_bytes) != freeze_sha256:
        raise ValueError("FREEZE.json does not match --freeze-sha256")
    corpus_bytes = path.read_bytes()
    if corpus_sha256 is not None and digest(corpus_bytes) != corpus_sha256:
        raise ValueError("corpus does not match --corpus-sha256")
    # The independent author supplies this binding; this consumer never writes
    # either the freeze or expectations. Unknown freeze formats fail closed.
    files = freeze.get("files")
    if (not isinstance(files, dict)
            or files.get("conformance/guided-v2/corpus.json") != digest(corpus_bytes)):
        raise ValueError("FREEZE.json files binding does not match corpus bytes")
    if freeze.get("source_commit") != AUTHORITY:
        raise ValueError("FREEZE.json has an unexpected source authority")
    corpus = strict_json(corpus_bytes)
    if corpus.get("schema") != "stateless-guided-v2/1" or corpus.get("source_commit") != AUTHORITY:
        raise ValueError("unsupported corpus schema or source authority")
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("corpus must contain cases")
    ids = set()
    for case in cases:
        if not isinstance(case.get("id"), str) or not case["id"] or case["id"] in ids:
            raise ValueError("invalid or duplicate corpus case ID")
        ids.add(case["id"])
        expected = case["expected"]
        if not EXPECTED_FIELDS <= expected.keys() or expected.keys() - EXPECTED_FIELDS - {"rng_audit"}:
            raise ValueError(f"unexpected expected fields in {case['id']}")
        if (expected["report"] is None) == (expected["error"] is None):
            raise ValueError(f"report/error exclusivity violated in {case['id']}")
    return corpus


def load_installed_api():
    if not sys.flags.isolated or sys.prefix == sys.base_prefix:
        raise RuntimeError("run with a wheel-installed virtualenv Python using -I")
    module = importlib.import_module("statelessness")
    distribution = importlib.metadata.distribution("statelessness")
    origin = Path(module.__file__).resolve()
    expected = Path(distribution.locate_file("statelessness/__init__.py")).resolve()
    if origin != expected or not origin.is_relative_to(Path(sys.prefix).resolve()):
        raise RuntimeError("statelessness did not resolve to this virtualenv's installed distribution")
    direct = strict_json(distribution.read_text("direct_url.json") or "{}")
    if (direct.get("dir_info", {}).get("editable") or "archive_info" not in direct
            or not direct.get("url", "").endswith(".whl")):
        raise RuntimeError("statelessness must be installed from a wheel, not an editable/source tree")
    required = (
        "FeedbackMetadata", "Feedback", "StateFeedback", "FeatureBuffer", "GuidanceConfig",
        "CorpusEntry", "GuidanceReport", "guided_fuzz", "exact_state", "FuzzConfig", "fuzz",
        "RunLimits", "ModelError", "Check", "Disposition", "Transition", "Metadata", "EXHAUSTED",
        "record", "replay", "ShrinkConfig", "shrink",
    )
    missing = [name for name in required if not hasattr(module, name)]
    if missing:
        raise RuntimeError("installed public API is missing: " + ", ".join(missing))
    return module


def checks_json(checks):
    return [{"id": check.id, "status": STATUS[check.status],
             "message": None if check.status == "passed" else check.details} for check in checks]


def disposition_json(disposition):
    return {"kind": DISPOSITIONS[disposition.kind],
            "reason": None if disposition.kind == "accepted" else disposition.reason}


def failure_json(failure):
    if failure is None:
        return None
    return {"inputs": list(failure.inputs), "violations": [
        {"phase": PHASES[v.phase], "check": checks_json((v.check,))[0]}
        for v in failure.violations]}


def report_json(report):
    guidance = report.guidance  # Missing new public fields are API failures.
    if guidance is not None:
        if not isinstance(guidance.corpus, tuple) or any(not isinstance(e.inputs, tuple) for e in guidance.corpus):
            raise TypeError("GuidanceReport.corpus and CorpusEntry.inputs must be tuples")
        guidance = asdict(guidance)
        guidance["corpus"] = [dict(entry, inputs=list(entry["inputs"])) for entry in guidance["corpus"]]
    return {"termination": FUZZ_TERMINATIONS[report.termination], "seed": str(report.seed),
            "cases": report.cases, "transitions": report.transitions,
            "skipped_checks": report.skipped_checks, "failure": failure_json(report.failure),
            "guidance": guidance}


def shrink_json(report):
    history = []
    for attempt in report.history:
        operation = attempt.operation
        if operation.kind == "validate_original":
            normalized = {"kind": "ValidateOriginal"}
        elif operation.kind == "remove":
            normalized = {"kind": "Remove", "start": operation.start, "end": operation.end}
        elif operation.kind == "simplify":
            normalized = {"kind": "Simplify", "index": operation.index, "alternative": operation.alternative}
        else:
            raise ValueError(f"unknown shrink operation: {operation.kind}")
        history.append({"attempt": attempt.attempt, "candidate_length": attempt.candidate_length,
                        "operation": normalized, "outcome": SHRINK_OUTCOMES[attempt.outcome]})
    return {"original": failure_json(report.original), "minimized": failure_json(report.minimized),
            "attempts": report.attempts, "termination": SHRINK_TERMINATIONS[report.termination],
            "history": history, "replayed_transitions": report.replayed_transitions,
            "validated_original": report.validated_original}


def error_json(error):
    """Only documented public validation/context spellings are normalized."""
    message = str(error)
    if isinstance(error, ValueError):
        for field in ("max_corpus_entries", "max_corpus_inputs", "max_features", "max_features_per_observation"):
            if message == f"{field} must be an integer in 1..18446744073709551615":
                return "guidance limits must be positive"
        if message == "mutation_percent must be an integer in 0..100":
            return "mutation_percent must be in 0..=100"
    if isinstance(error, native.ModelError):
        for python, rust in (("initial check: ", "initial check_state: "),
                             ("state check: ", "check_state: "),
                             ("transition check: ", "check_transition: "),
                             ("transition: ", "step: ")):
            if message.startswith(python):
                return rust + message[len(python):]
    return message


class Events:
    def __init__(self, control):
        self.control = control
        self.cancelled = control["cancelled"]
        self.case = 0
        self.calls = Counter()
        self.trace = []
        self.generator_rng = []

    def log(self, *entry):
        self.trace.append(deepcopy(list(entry)))

    def done(self, callback):
        self.calls[callback] += 1
        if self.control["cancel_after"] == {"callback": callback, "call": self.calls[callback]}:
            self.cancelled = True


RESET_CONTROL = {"cancelled": False, "zero_duration": False, "cancel_after": None}


class TableModel:
    def __init__(self, spec, events, *, recording=False):
        if spec["kind"] != "table-walk-v1":
            raise ValueError("unsupported model fixture")
        self.spec, self.events, self.recording = spec, events, recording

    def fault(self, callback):
        fault = self.spec["fault"]
        if fault is not None and fault["callback"] == callback and fault["call"] == self.events.calls[callback]:
            raise native.ModelError(fault["message"])

    def initial_state(self):
        self.events.case += 1
        self.events.log("initial_state", self.events.case)
        self.events.done("initial_state")
        self.fault("initial_state")
        return (0, 0)

    def generate(self, state, rng):
        raw = [rng.next_u64() for _ in range(self.spec["draws"])]
        rows = self.spec["generate"]
        row = rows[min(self.events.case - 1, len(rows) - 1)] if rows else []
        value = row[state[0]] if state[0] < len(row) else self.spec["fallback"]
        if value is not None and self.spec["rng_mod"] is not None:
            value = 1 + raw[-1] % self.spec["rng_mod"]
        values = [str(value) for value in raw]
        self.events.generator_rng.append({"case": self.events.case, "state": list(state), "values": values})
        self.events.log("generate", self.events.case, list(state), value, values)
        self.events.done("generate")
        self.fault("generate")
        return native.EXHAUSTED if value is None else value

    def is_enabled(self, state, input):
        enabled = input >= 0 and (self.spec["enabled"] != "successor" or input == state[1] + 1)
        self.events.log("is_enabled", self.events.case, list(state), input, enabled)
        self.events.done("is_enabled")
        self.fault("is_enabled")
        return enabled

    def step(self, state, input):
        self.events.log("step", self.events.case, list(state), input)
        self.events.done("step")
        self.fault("step")
        after = (state[0] + 1, state[1] + input)
        disposition = self.spec.get("disposition", {"kind": "Accepted", "reason": None})
        kind = DISPOSITION_KINDS[disposition["kind"]]
        return native.Transition(after, (after[1],),
                                 native.Disposition(kind, "" if kind == "accepted" else disposition["reason"]))

    def batch(self, phase, state):
        checks, error = [], None
        for rule in self.spec["checks"]:
            if (rule["phase"] == phase and rule["case"] in (None, self.events.case)
                    and rule["depth"] in (None, state[0])):
                checks, error = deepcopy(rule["checks"]), rule["error"]
                break
        limit = self.spec["fail_total"]
        if error is None and phase != "Transition" and limit is not None and state[1] >= limit:
            checks.append({"id": "limit", "status": "Failed", "message": "total reached limit"})
        return checks, error

    @staticmethod
    def append_checks(out, checks, error):
        for check in checks:
            out.append(native.Check(check["id"], check["status"].lower(),
                                    "" if check["message"] is None else check["message"]))
        if error is not None:
            raise native.ModelError(error)

    def check_state_into(self, state, out):
        phase = "InitialState" if state[0] == 0 else "State"
        checks, error = self.batch(phase, state)
        self.events.log("check_state", self.events.case, list(state), checks, error)
        self.events.done("check_state")
        self.append_checks(out, checks, error)

    def check_transition_into(self, before, input, after, out):
        checks, error = self.batch("Transition", after.state)
        self.events.log("check_transition", self.events.case, list(before), input, list(after.state), checks, error)
        self.events.done("check_transition")
        self.append_checks(out, checks, error)

    def simpler_inputs(self, input):
        return ()

    def metadata(self):
        if not self.recording:
            raise native.ModelError("bare fuzz unexpectedly requested model metadata")
        return native.Metadata("guided-table-walk", 1, 1, 1, "table-walk-v1")

    def encode_state(self, state):
        return struct.pack("<Qq", *state)

    def decode_state(self, data):
        return struct.unpack("<Qq", data)

    def encode_input(self, input):
        return struct.pack("<q", input)

    def decode_input(self, data):
        return struct.unpack("<q", data)[0]

    def encode_output(self, output):
        return struct.pack("<q", output)


@dataclass(frozen=True)
class Key:
    value: str | int | float | tuple
    constant_hash: bool

    def __hash__(self):
        return 0 if self.constant_hash else hash(self.value)


def feature_key(value, spec):
    return Key(tuple(value) if isinstance(value, list) else value, spec["hash"] == "constant")


def feedback_metadata():
    return native.FeedbackMetadata("table-features", 1, "guided-v2-fixture-v1")


class CustomFeedback:
    # Deliberately lacks initial_features: a null initial hook exercises the
    # engine's public default-empty behavior instead of a consumer substitute.
    def __init__(self, spec, events):
        self.spec, self.events = spec, events

    def metadata(self):
        self.events.log("metadata")
        self.events.done("metadata")
        return feedback_metadata()

    def emit(self, keys, error, out, callback):
        try:
            for value in keys:
                try:
                    out.push(feature_key(value, self.spec))
                except native.ModelError:
                    self.events.log("push", value, False)
                    if not self.spec["ignore_overrun"]:
                        raise
                else:
                    self.events.log("push", value, True)
            if error is not None:
                raise native.ModelError(error)
        finally:
            self.events.done(callback)

    def transition_features(self, before, input, after, out):
        self.events.log("transition_feedback", self.events.case, list(before), input,
                        list(after.state), list(after.outputs), disposition_json(after.disposition))
        keys, error = [], None
        for row in self.spec["transitions"]:
            if row["case"] in (None, self.events.case) and row["depth"] in (None, after.state[0]):
                keys, error = row["keys"], row["error"]
                break
        self.emit(keys, error, out, "transition_feedback")


class InitialCustomFeedback(CustomFeedback):
    def initial_features(self, state, out):
        self.events.log("initial_feedback", self.events.case, list(state))
        self.emit(self.spec["initial"], self.spec["initial_error"], out, "initial_feedback")


class TracedFeedback:
    def __init__(self, delegate, events):
        self.delegate, self.events = delegate, events

    def metadata(self):
        self.events.log("metadata")
        try:
            return self.delegate.metadata()
        finally:
            self.events.done("metadata")

    def initial_features(self, state, out):
        self.events.log("initial_feedback", self.events.case, list(state))
        try:
            return self.delegate.initial_features(state, out)
        finally:
            self.events.done("initial_feedback")

    def transition_features(self, before, input, after, out):
        self.events.log("transition_feedback", self.events.case, list(before), input,
                        list(after.state), list(after.outputs), disposition_json(after.disposition))
        try:
            return self.delegate.transition_features(before, input, after, out)
        finally:
            self.events.done("transition_feedback")


def make_feedback(spec, events):
    if spec["kind"] == "custom":
        return (CustomFeedback if spec["initial"] is None else InitialCustomFeedback)(spec, events)
    if spec["kind"] == "state":
        delegate = native.StateFeedback(feedback_metadata(), lambda state: feature_key(state[spec["project"]], spec))
    elif spec["kind"] == "exact":
        delegate = native.exact_state()
    else:
        raise ValueError("unsupported feedback kind")
    return TracedFeedback(delegate, events)


class Observer:
    def __init__(self, events):
        self.events = events

    def needs_history(self):
        return True

    def initial(self, state, checks):
        self.events.log("observer_initial", self.events.case, list(state), checks_json(checks))
        self.events.done("observer_initial")

    def transition(self, before, input, after, state_checks, transition_checks, history):
        self.events.log("observer_transition", self.events.case, list(before), input, list(after.state),
                        list(after.outputs), checks_json(state_checks), checks_json(transition_checks), list(history),
                        disposition_json(after.disposition))
        self.events.done("observer_transition")

    def check_error(self, phase, checks, complete):
        self.events.log("observer_check_error", self.events.case, PHASES[phase], checks_json(checks), complete)
        self.events.done("observer_check_error")


def witness_replay(spec, inputs, *, failing=False):
    recorder = TableModel(spec, Events(RESET_CONTROL), recording=True)
    trace = native.record(recorder, inputs, max_steps=len(inputs))
    termination = "property_failed" if failing else "completed"
    if trace.termination != termination or len(trace.steps) != len(inputs):
        raise RuntimeError(f"witness recording expected {termination}: {trace.termination}: {trace.error}")
    replay_model = TableModel(spec, Events(RESET_CONTROL), recording=True)
    # Replay decodes the initial checkpoint rather than calling initial_state.
    replay_model.events.case = 1
    replayed = native.replay(replay_model, trace)
    if (replayed.outcome != "exact" or replayed.steps_verified != len(inputs)
            or replayed.failure_reproduced is not failing or not replayed.build_matches):
        raise RuntimeError(f"witness did not replay exactly (failing={failing}): {replayed}")
    # Preserve phase separation using the checker batches actually invoked by
    # public record. Verify them against its retained public Trace checkpoints.
    batches = []
    for event in recorder.events.trace:
        if event[0] == "check_state":
            batches.append({"phase": "InitialState" if event[2][0] == 0 else "State", "checks": event[3]})
        elif event[0] == "check_transition":
            batches.append({"phase": "Transition", "checks": event[5]})
    if len(batches) != 1 + 2 * len(trace.steps) or batches[0]["checks"] != checks_json(trace.initial_checks):
        raise RuntimeError("record did not retain its initial checker evidence")
    for index, step in enumerate(trace.steps):
        if batches[1 + 2 * index]["checks"] + batches[2 + 2 * index]["checks"] != checks_json(step.checks):
            raise RuntimeError("record did not retain its transition checker evidence")
    return {"inputs": [recorder.decode_input(step.input) for step in trace.steps],
            "states": [list(recorder.decode_state(trace.initial_state))]
                      + [list(recorder.decode_state(step.post_state)) for step in trace.steps],
            "outputs": [[struct.unpack("<q", output)[0] for output in step.outputs] for step in trace.steps],
            "dispositions": [disposition_json(step.disposition) for step in trace.steps],
            "checks": batches,
            "termination": {"completed": "Completed", "property_failed": "PropertyFailed"}[trace.termination],
            "replay": {"outcome": {"exact": "Exact"}[replayed.outcome],
                       "failure_reproduced": replayed.failure_reproduced,
                       "steps_verified": replayed.steps_verified}}


def run_case(case):
    events = Events(case["control"])
    model, observer = TableModel(case["model"], events), Observer(events)
    limits = native.RunLimits(max_duration=0 if case["control"]["zero_duration"] else None,
                              cancellation=lambda: events.cancelled)
    report, error = None, None
    try:
        # Rust admits guidance before fuzz config and before metadata/control.
        guidance = native.GuidanceConfig(**case["guidance"]) if case["mode"] == "guided" else None
        config = native.FuzzConfig(**dict(case["config"], seed=int(case["config"]["seed"])))
        if case["mode"] == "guided":
            report = native.guided_fuzz(model, make_feedback(case["feedback"], events), config, guidance,
                                       limits=limits, observer=observer)
        elif case["mode"] == "ordinary":
            report = native.fuzz(model, config, limits=limits, observer=observer)
        else:
            raise RuntimeError("unsupported search mode")
    except (native.ModelError, ValueError) as caught:
        error = error_json(caught)
    witnesses, shrunk = [], None
    if report is not None:
        if report.guidance is not None:
            witnesses = [witness_replay(case["model"], entry.inputs) for entry in report.guidance.corpus]
        if case["shrink_config"] is not None:
            if report.failure is None:
                raise RuntimeError("shrink_config requires an actual native failure")
            shrink_report = native.shrink(TableModel(case["model"], Events(RESET_CONTROL)), report.failure,
                                          native.ShrinkConfig(**case["shrink_config"]))
            shrunk = shrink_json(shrink_report)
            # Retained passing entries stay first. Keep both failing witnesses,
            # including when an attempt limit leaves their inputs identical.
            witnesses.extend((witness_replay(case["model"], report.failure.inputs, failing=True),
                              witness_replay(case["model"], shrink_report.minimized.inputs, failing=True)))
    return {"report": None if report is None else report_json(report), "error": error,
            "trace": events.trace, "generator_rng": events.generator_rng,
            "witness_replays": witnesses, "shrink": shrunk}


def first_difference(expected, actual, path):
    if type(expected) is not type(actual):
        return f"{path}: expected type {type(expected).__name__}, got {type(actual).__name__}"
    if isinstance(expected, dict):
        if expected.keys() != actual.keys():
            return f"{path}: fields differ: expected {sorted(expected)}, got {sorted(actual)}"
        for key in expected:
            difference = first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference:
                return difference
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}: expected {len(expected)} items, got {len(actual)}"
        for index, (left, right) in enumerate(zip(expected, actual)):
            difference = first_difference(left, right, f"{path}[{index}]")
            if difference:
                return difference
    elif expected != actual:
        return f"{path}: expected {expected!r}, got {actual!r}"
    return None


def compare_results(cases, results):
    failures = []
    for case, result in zip(cases, results):
        expected = {key: value for key, value in case["expected"].items() if key != "rng_audit"}
        difference = first_difference(expected, result["actual"], case["id"])
        if difference:
            failures.append(difference)
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, help="defaults to FREEZE.json beside corpus")
    parser.add_argument("--corpus-sha256")
    parser.add_argument("--freeze-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", help="produce one exact case ID")
    parser.add_argument("--compare", action="store_true", help="compare all required fields after production")
    args = parser.parse_args()
    corpus = read_frozen_corpus(args.corpus, args.freeze or args.corpus.with_name("FREEZE.json"),
                                args.corpus_sha256, args.freeze_sha256)
    cases = [case for case in corpus["cases"] if args.case is None or case["id"] == args.case]
    if not cases:
        raise ValueError("no selected cases")
    global native
    native = load_installed_api()
    results = []
    for case in cases:
        # Do not make expected/purpose available to any candidate fixture.
        inputs = {key: deepcopy(value) for key, value in case.items() if key not in ("expected", "purpose")}
        results.append({"id": case["id"], "actual": run_case(inputs)})
    payload = {"schema": "stateless-guided-v2-results/1", "engine": "python", "cases": results}
    data = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output is None:
        sys.stdout.write(data)
    else:
        # Keep failed/superseded outputs; never overwrite a prior receipt.
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(data)
    failures = compare_results(cases, results) if args.compare else []
    for failure in failures:
        print(failure, file=sys.stderr)
    if args.compare:
        print(f"python guided-v2: {len(cases) - len(failures)}/{len(cases)} cases matched", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"native-guided-python: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2)
