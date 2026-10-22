#!/usr/bin/env python3
"""Independent source-derived author/checker. Never imports or executes a runtime.

Run only through the default Kyoto queue. --author writes and seals corpus.json;
--check recomputes it and verifies every sealed file. No candidate-output input
is accepted.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = "a4cc11eea8db62115b452eee3c6610c04fa06b96"
MASK = (1 << 64) - 1
GAMMA = 0x9E3779B97F4A7C15
OVERRUN = "feedback exceeds per-observation feature limit"
META = {"name": "table-features", "version": 1, "build": "guided-v2-fixture-v1"}
EXACT = {"name": "exact-state", "version": 1, "build": "clone-eq-hash-v1"}


class SplitMix:
    def __init__(self, seed):
        self.state = int(seed)
        self.audit = []

    def next(self, reason):
        self.state = (self.state + GAMMA) & MASK
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK
        z ^= z >> 31
        self.audit.append({"reason": reason, "value": str(z)})
        return z

    def index(self, upper, reason):
        if upper == 0:
            return None
        threshold = ((-upper) & MASK) % upper
        while True:
            value = self.next(reason)
            if value >= threshold:
                return value % upper


class Halt(Exception):
    pass


class CallbackError(Exception):
    pass


def check(identifier, status="Passed", message=None):
    return {"id": identifier, "status": status, "message": message}


def rule(phase, depth, checks, error=None, case=None):
    return dict(phase=phase, case=case, depth=depth, checks=checks, error=error)


def row(depth, keys, case=None, error=None):
    return dict(case=case, depth=depth, keys=keys, error=error)


def fixture(identifier, purpose, **updates):
    case = {
        "id": identifier, "purpose": purpose, "mode": "guided",
        "config": dict(seed="0", cases=1, max_steps=3, max_transitions=100, mutation_percent=0),
        "guidance": dict(max_corpus_entries=256, max_corpus_inputs=25600,
                         max_features=100000, max_features_per_observation=256),
        "model": dict(kind="table-walk-v1", generate=[[1, 1, 1]], fallback=None,
                      draws=1, rng_mod=None, enabled="nonnegative", checks=[],
                      fail_total=None, fault=None),
        "feedback": dict(kind="state", project=0, hash="constant", initial=None,
                         initial_error=None, transitions=[], ignore_overrun=True),
        "control": dict(cancelled=False, zero_duration=False, cancel_after=None),
        "shrink_config": None,
    }
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(case.get(key), dict):
            case[key].update(value)
        else:
            case[key] = value
    return case


def authored_cases():
    out = []
    add = lambda *a, **kw: out.append(fixture(*a, **kw))
    add("zero-cases", "Metadata is queried once; no model/feedback work.", config={"cases": 0})
    add("zero-steps", "Initial states/checks/features count, no transition-cap probe.",
        config={"cases": 2, "max_steps": 0, "max_transitions": 0})
    add("zero-transitions", "Case setup and initial feature precede the step budget.",
        config={"max_transitions": 0})
    add("exact-last-transition", "No extra cap probe after the final configured step.",
        config={"max_transitions": 3})
    add("cap-at-next-step", "A remaining step observes the global cap.",
        config={"max_steps": 4, "max_transitions": 3})
    add("cap-after-next-case-setup", "Second case setup/mutation draws occur before cap.",
        config={"cases": 2, "max_steps": 1, "max_transitions": 1, "mutation_percent": 100})
    add("generator-none", "None consumes fixture draw and ends a case without a step.",
        model={"generate": [[None]]})
    for key in ["max_corpus_entries", "max_corpus_inputs", "max_features", "max_features_per_observation"]:
        add("invalid-" + key.replace("_", "-"), "Invalid guidance wins before metadata/control.",
            guidance={key: 0}, config={"mutation_percent": 101}, control={"cancelled": True})
    add("invalid-mutation", "Mutation validation precedes metadata and cancellation.",
        config={"mutation_percent": 101}, control={"cancelled": True})
    add("default-empty-initial", "Omitted initial hook emits no initial feature.",
        feedback={"kind": "custom", "transitions": [row(None, ["edge"])]})
    add("explicit-empty-initial", "Explicit initial callback still runs when it emits nothing.",
        feedback={"kind": "custom", "initial": []})
    add("duplicates-collisions", "Duplicate keys deduplicate, unequal constant-hash keys survive.",
        feedback={"kind": "custom", "initial": ["a", "a", "b"],
                  "transitions": [row(1, ["a", "c", "c", "d"]), row(2, ["d", "e"])]})
    add("feature-cap-exact-fill", "Filling feature capacity alone does not set saturation.",
        config={"max_steps": 1}, guidance={"max_features": 2})
    add("feature-cap-reject", "A vacant key rejected at capacity sets the sticky flag.",
        guidance={"max_features": 2})
    add("initial-cap-reject", "Initial rejected keys set feature flag but never corpus flag.",
        config={"max_steps": 0}, guidance={"max_features": 2},
        feedback={"kind": "custom", "initial": ["a", "b", "c", "a"]})
    add("corpus-exact-fill", "Exact corpus occupancy is not an attempted-admission overflow.",
        config={"max_steps": 1}, guidance={"max_corpus_entries": 1, "max_corpus_inputs": 1})
    add("fifo-entry-limit", "Oldest entry is evicted, all witnessed bits remain true.",
        guidance={"max_corpus_entries": 1})
    add("fifo-input-limit", "Total retained input limit evicts multiple oldest entries.",
        config={"max_steps": 4}, model={"generate": [[1, 1, 1, 1]]},
        guidance={"max_corpus_inputs": 5})
    add("fifo-no-readmission", "An evicted witnessed key does not re-admit a prefix.",
        guidance={"max_corpus_entries": 1}, feedback={"kind": "custom",
        "transitions": [row(1, ["a"]), row(2, ["b"]), row(3, ["a"])]})
    add("overlong-novel-once", "Only newly tracked overlong discoveries count as drops.",
        config={"max_steps": 4}, model={"generate": [[1, 1, 1, 1]]},
        guidance={"max_corpus_inputs": 1}, feedback={"kind": "custom",
        "transitions": [row(2, ["x", "x"]), row(3, ["x"]), row(4, ["x", "y"])]})
    add("recover-at-feature-cap", "Retainable witness recovers an unwitnessed key with new_features=0.",
        config={"cases": 2, "max_steps": 2}, guidance={"max_corpus_inputs": 1, "max_features": 1},
        feedback={"kind": "custom", "transitions": [row(2, ["x", "x"], case=1),
                  row(1, ["x", "x", "blocked"], case=2)]})
    add("projection-state-independence", "Different full states share one projected key without merging state/check work.",
        config={"cases": 2, "max_steps": 2}, guidance={"max_corpus_inputs": 1},
        feedback={"kind": "state", "project": 1}, model={"generate": [[0, 0]]})
    add("exact-state", "Exact feedback clones both state components and uses exact-state metadata.",
        feedback={"kind": "exact"}, model={"generate": [[0, 0, 0]]})
    for phase in ["initial", "transition"]:
        for own_error in [False, True]:
            fb = dict(kind="custom", ignore_overrun=True)
            if phase == "initial":
                fb.update(initial=["same", "same", "same", "after"],
                          initial_error="feedback own error" if own_error else None)
            else:
                fb["transitions"] = [row(1, ["same", "same", "same", "after"],
                                                 error="feedback own error" if own_error else None)]
            add(f"{phase}-sticky-overrun" + ("-own-error" if own_error else ""),
                "Duplicates consume slots; sticky overrun cannot be ignored; callback Err takes precedence.",
                guidance={"max_features_per_observation": 2}, feedback=fb)
    add("propagated-push-error", "A propagated push error interrupts callback before its own trailing error.",
        guidance={"max_features_per_observation": 1}, feedback={"kind": "custom",
        "ignore_overrun": False, "transitions": [row(1, ["a", "a", "b"], error="unreached")]})
    add("initial-feedback-error", "Initial callback error has its own contextual prefix.",
        feedback={"kind": "custom", "initial": [], "initial_error": "initial broken"})
    add("transition-feedback-error", "Transition callback error returns no partial report.",
        feedback={"kind": "custom", "transitions": [row(1, ["a"], error="transition broken")]})
    add("pre-cancelled", "Metadata queried but no initial model callback.", control={"cancelled": True})
    add("zero-duration", "Supported clock zero budget stops before model work, after metadata.",
        control={"zero_duration": True})
    add("cancel-before-deadline", "Cancellation wins when both control reasons apply.",
        control={"cancelled": True, "zero_duration": True})
    for callback, call in [("metadata", 1), ("initial_state", 1), ("check_state", 1),
                           ("observer_initial", 1), ("initial_feedback", 1),
                           ("generate", 1), ("is_enabled", 1), ("step", 1),
                           ("check_state", 2), ("check_transition", 1),
                           ("observer_transition", 1), ("transition_feedback", 1)]:
        add(f"cancel-after-{callback}-{call}", "Exact cooperative callback boundary and feedback admission order.",
            control={"cancel_after": dict(callback=callback, call=call)})
    add("initial-property-before-control", "Initial observer sees failure; failure wins over cancellation/feedback.",
        model={"checks": [rule("InitialState", 0, [check("initial", "Failed", "initial bad")])]},
        control={"cancel_after": dict(callback="initial_state", call=1)})
    add("transition-property-before-control-feedback", "Both check phases run; observer precedes failure; no feedback.",
        model={"checks": [rule("State", 1, [check("s", "Failed", "state bad")]),
                          rule("Transition", 1, [check("t", "Failed", "edge bad")])]},
        control={"cancel_after": dict(callback="step", call=1)},
        feedback={"kind": "custom", "transitions": [row(1, [], error="must not run")]})
    add("skipped-checks", "Skipped checks count across initial/state/transition; ordered failures retain phases.",
        model={"checks": [rule("InitialState", None, [check("i", "Skipped", "initial skip")]),
        rule("State", None, [check("s", "Skipped", "state skip")]),
        rule("Transition", None, [check("t", "Skipped", "edge skip")])]})
    for phase in ["InitialState", "State", "Transition"]:
        depth = 0 if phase == "InitialState" else 1
        add("checker-error-" + phase.lower(), "Partial/complete check_error observation, no normal edge observation.",
            model={"checks": [rule(phase, depth, [check("partial", "Skipped", "emitted")], "checker broke")]})
    add("transition-error-over-state-failure", "Transition callback Err supersedes emitted state failure and cancellation.",
        model={"checks": [rule("State", 1, [check("bad", "Failed", "failed state")]),
        rule("Transition", 1, [check("partial", "Skipped", "partial")], "edge checker broke")]},
        control={"cancel_after": dict(callback="check_state", call=2)})
    for callback in ["initial_state", "generate", "is_enabled", "step"]:
        add("callback-error-" + callback, "Callback errors win over cancellation from the same callback.",
            model={"fault": dict(callback=callback, call=1, message="callback broke")},
            control={"cancel_after": dict(callback=callback, call=1)})
    add("disabled-generated", "Generated disabled input is rejected before reduction.", model={"generate": [[-1]]})
    add("disabled-generated-cancel", "Post-enabled cancellation wins over disabled-input diagnostic.",
        model={"generate": [[-1]]}, control={"cancel_after": dict(callback="is_enabled", call=1)})
    add("feedback-error-before-cancel", "Feedback callback Err wins over cancellation it sets.",
        feedback={"kind": "custom", "transitions": [row(1, [], error="feedback broke")]},
        control={"cancel_after": dict(callback="transition_feedback", call=1)})
    for seed in ["0", "1", "2", "42", str((-2 * GAMMA) & MASK)]:
        add("mutation-seed-" + seed, "Guided mutation arms, corpus selection, exact draws and one-position replacement.",
            config={"seed": seed, "cases": 5, "max_steps": 4, "mutation_percent": 100},
            model={"generate": [[1, 2, 3, 4]], "rng_mod": 7}, guidance={"max_corpus_entries": 3})
    add("mutation-zero-still-draws", "Nonempty corpus consumes percent selection even when chance is zero.",
        config={"cases": 3, "max_steps": 2, "mutation_percent": 0})
    add("mutation-rejection-index", "Second raw draw is zero: index100 rejects it (threshold16), then draws again.",
        config={"seed": str((-2 * GAMMA) & MASK), "cases": 3, "max_steps": 1, "mutation_percent": 100},
        model={"rng_mod": 7})
    add("empty-corpus-no-draws", "No mutation draw when feedback never admits a prefix.",
        config={"cases": 3, "max_steps": 2, "mutation_percent": 100}, feedback={"kind": "custom"})
    add("mutation-disabled-reuse", "Invalid retained input uses state-aware generation; later reuse remains eligible.",
        config={"seed": "42", "cases": 5, "max_steps": 4, "mutation_percent": 100},
        model={"generate": [[1, 2, 4, 8]], "enabled": "successor"})
    add("property-failure-shrink", "Retained prefixes replay; failing [1,1,5] shrinks causally to [5].",
        model={"generate": [[1, 1, 5]], "fail_total": 5}, shrink_config={"max_attempts": 100})
    add("property-failure-shrink-budget", "Validation counts as one attempt; bounded shrink preserves original.",
        model={"generate": [[1, 1, 5]], "fail_total": 5}, shrink_config={"max_attempts": 1})
    for seed in ["0", "42"]:
        add("ordinary-mutation-seed-" + seed, "Ordinary fuzz keeps previous passing sequence and its four-draw mutation policy.",
            mode="ordinary", config={"seed": seed, "cases": 4, "max_steps": 4, "mutation_percent": 100},
            model={"generate": [[1, 2, 3, 4]], "rng_mod": 7})
    add("ordinary-zero-steps", "Ordinary fuzz zero steps preserves cases without guidance.",
        mode="ordinary", config={"cases": 2, "max_steps": 0, "max_transitions": 0})
    add("ordinary-check-error", "Shared ordinary kernel preserves complete and partial observer error batches.",
        mode="ordinary", model={"checks": [rule("Transition", 1, [check("partial")], "ordinary checker")]})
    add("ordinary-cancel-after-step", "Ordinary reduction/check observation ordering stays intact.",
        mode="ordinary", control={"cancel_after": dict(callback="step", call=1)})
    return out


def model_checks(model, case, phase, state):
    batch, error = [], None
    for r in model["checks"]:
        if r["phase"] == phase and r["case"] in (None, case) and r["depth"] in (None, state[0]):
            batch, error = copy.deepcopy(r["checks"]), r["error"]
            break
    if error is None and phase != "Transition" and model["fail_total"] is not None and state[1] >= model["fail_total"]:
        batch.append(check("limit", "Failed", "total reached limit"))
    return batch, error


class Author:
    def __init__(self, case):
        self.case = case
        self.cfg, self.gc = case["config"], case["guidance"]
        self.m, self.fb, self.ctrl = case["model"], case["feedback"], case["control"]
        self.rng, self.trace, self.draws = SplitMix(self.cfg["seed"]), [], []
        self.calls, self.seen = {}, []  # Linear equality deliberately ignores hashes.
        self.cancelled = self.ctrl["cancelled"]
        self.report = dict(termination="CasesCompleted", seed=self.cfg["seed"], cases=0,
                           transitions=0, skipped_checks=0, failure=None, guidance=None)
        self.g = dict(algorithm="feature-corpus-v2", config=copy.deepcopy(self.gc),
                      feedback=copy.deepcopy(EXACT if self.fb["kind"] == "exact" else META),
                      unique_features=0, corpus_inputs=0, corpus=[], mutation_cases=0,
                      dropped_prefixes=0, evicted_prefixes=0, feature_limit_reached=False,
                      corpus_limit_reached=False)
        self.c = 0

    def event(self, callback, *args):
        self.trace.append(copy.deepcopy([callback, *args]))
        self.calls[callback] = self.calls.get(callback, 0) + 1
        hook = self.ctrl["cancel_after"]
        if hook == dict(callback=callback, call=self.calls[callback]):
            self.cancelled = True

    def fault(self, callback):
        f = self.m["fault"]
        if f and f["callback"] == callback and f["call"] == self.calls.get(callback):
            raise CallbackError(f"{callback}: {f['message']}")

    def stop(self):
        if self.cancelled:
            raise Halt("Cancelled")
        if self.ctrl["zero_duration"]:
            raise Halt("Deadline")

    def checks(self, state, phase, before=None, input_=None, history=None):
        batch, error = model_checks(self.m, self.c, phase, state)
        self.event("check_state", self.c, state, batch, error)
        if error:
            self.event("observer_check_error", self.c, phase, batch, False)
            prefix = "initial check_state" if phase == "InitialState" else "check_state"
            raise CallbackError(f"{prefix}: {error}")
        paired = [(phase, batch)]
        if phase == "InitialState":
            self.event("observer_initial", self.c, state, batch)
        else:
            transition, error = model_checks(self.m, self.c, "Transition", state)
            self.event("check_transition", self.c, before, input_, state, transition, error)
            if error:
                self.event("observer_check_error", self.c, "State", batch, True)
                self.event("observer_check_error", self.c, "Transition", transition, False)
                raise CallbackError(f"check_transition: {error}")
            self.event("observer_transition", self.c, before, input_, state, [state[1]], batch, transition, history)
            paired.append(("Transition", transition))
        failures = []
        for p, checks in paired:
            for item in checks:
                if item["status"] == "Failed":
                    failures.append(dict(phase=p, check=item))
                elif item["status"] == "Skipped":
                    self.report["skipped_checks"] = min(MASK, self.report["skipped_checks"] + 1)
        if failures:
            self.report["failure"] = dict(inputs=copy.deepcopy(history or []), violations=failures)
            raise Halt("FailureFound")

    def feedback(self, state, inputs, before=None, input_=None):
        initial = before is None
        if self.fb["kind"] == "custom":
            if initial and self.fb["initial"] is None:
                return
            keys, error = (self.fb["initial"], self.fb["initial_error"]) if initial else ([], None)
            if not initial:
                for r in self.fb["transitions"]:
                    if r["case"] in (None, self.c) and r["depth"] in (None, state[0]):
                        keys, error = r["keys"], r["error"]
                        break
        else:
            keys = [copy.deepcopy(state) if self.fb["kind"] == "exact" else state[self.fb["project"]]]
            error = None
        callback = "initial_feedback" if initial else "transition_feedback"
        # Record invocation first, defer cancellation hook until pushes complete.
        old_hook = self.ctrl["cancel_after"]
        self.ctrl["cancel_after"] = None
        if initial:
            self.event(callback, self.c, state)
        else:
            self.event(callback, self.c, before, input_, state, [state[1]])
        self.ctrl["cancel_after"] = old_hook
        accepted, failed = [], False
        for key in keys:
            ok = not failed and len(accepted) < self.gc["max_features_per_observation"]
            if self.fb["kind"] == "custom":
                self.trace.append(["push", copy.deepcopy(key), ok])
            if ok:
                accepted.append(key)
            else:
                failed = True
                if not self.fb["ignore_overrun"]:
                    error = OVERRUN
                    break
        if old_hook == dict(callback=callback, call=self.calls[callback]):
            self.cancelled = True
        if error or failed:
            prefix = "initial feedback" if initial else "transition feedback"
            raise CallbackError(f"{prefix}: {error or OVERRUN}")
        self.learn(accepted, inputs)

    def learn(self, keys, inputs):
        novel, recovered = 0, False
        retainable = len(inputs) <= self.gc["max_corpus_inputs"]
        for key in keys:
            matches = [x for x in self.seen if x[0] == key]
            if matches:
                if retainable and not matches[0][1]:
                    matches[0][1], recovered = True, True
            elif len(self.seen) == self.gc["max_features"]:
                self.g["feature_limit_reached"] = True
            else:
                self.seen.append([copy.deepcopy(key), retainable])
                novel += 1
        self.g["unique_features"] = len(self.seen)
        if (novel == 0 and not recovered) or not inputs:
            return
        if not retainable:
            self.g["corpus_limit_reached"] = True
            self.g["dropped_prefixes"] += 1
            return
        while (len(self.g["corpus"]) >= self.gc["max_corpus_entries"] or
               self.g["corpus_inputs"] + len(inputs) > self.gc["max_corpus_inputs"]):
            removed = self.g["corpus"].pop(0)
            self.g["corpus_inputs"] -= len(removed["inputs"])
            self.g["evicted_prefixes"] += 1
            self.g["corpus_limit_reached"] = True
        self.g["corpus"].append(dict(inputs=inputs[:], new_features=novel,
                                    discovered_case=self.c, discovered_transition=self.report["transitions"]))
        self.g["corpus_inputs"] += len(inputs)

    def candidate(self, previous):
        guided = self.case["mode"] == "guided"
        pool = self.g["corpus"] if guided else previous
        if not pool or self.rng.index(100, "mutation-percent") >= self.cfg["mutation_percent"]:
            return [], None
        if guided:
            self.g["mutation_cases"] += 1
            index = self.rng.index(len(pool), "corpus-index")
            candidate = pool[index]["inputs"][:]
            arm = self.rng.index(3, "mutation-arm")
            if arm == 0:
                return candidate, None
            if arm == 2:
                return candidate, self.rng.index(len(candidate), "replacement")
        else:
            candidate = previous[:]
        start = self.rng.index(len(candidate), "delete-start")
        length = 1 + self.rng.index(len(candidate) - start, "delete-length")
        del candidate[start:start+length]
        return candidate, self.rng.index(len(candidate) + 1, "replacement")

    def enabled(self, state, input_):
        enabled = input_ >= 0 and (self.m["enabled"] != "successor" or input_ == state[1] + 1)
        self.event("is_enabled", self.c, state, input_, enabled)
        self.fault("is_enabled")
        return enabled

    def generate(self, state):
        rows = self.m["generate"]
        row_ = rows[min(self.c - 1, len(rows) - 1)]
        value = row_[state[0]] if state[0] < len(row_) else self.m["fallback"]
        raw = [str(self.rng.next("generate")) for _ in range(self.m["draws"])]
        if value is not None and self.m["rng_mod"] is not None:
            value = 1 + int(raw[-1]) % self.m["rng_mod"]
        self.draws.append(dict(case=self.c, state=state[:], values=raw))
        self.event("generate", self.c, state, value, raw)
        self.fault("generate")
        return value

    def run(self):
        error = None
        try:
            guided = self.case["mode"] == "guided"
            if guided and any(x == 0 for x in self.gc.values()):
                raise CallbackError("guidance limits must be positive")
            if self.cfg["mutation_percent"] > 100:
                raise CallbackError("mutation_percent must be in 0..=100")
            if guided:
                self.event("metadata")
            self.stop()
            previous = []
            for self.c in range(1, self.cfg["cases"] + 1):
                self.stop()
                self.event("initial_state", self.c)
                self.fault("initial_state")
                state = [0, 0]
                self.report["cases"] += 1
                self.checks(state, "InitialState")
                self.stop()
                if guided:
                    self.feedback(state, [])
                self.stop()
                candidate, replacement = self.candidate(previous)
                inputs = []
                for depth in range(self.cfg["max_steps"]):
                    if self.report["transitions"] == self.cfg["max_transitions"]:
                        raise Halt("TransitionLimit")
                    self.stop()
                    reuse = depth != replacement and depth < len(candidate) and self.enabled(state, candidate[depth])
                    self.stop()
                    input_ = candidate[depth] if reuse else self.generate(state)
                    self.stop()
                    if input_ is None:
                        break
                    enabled = reuse or self.enabled(state, input_)
                    self.stop()
                    if not enabled:
                        raise CallbackError("generate returned a causally disabled input")
                    self.event("step", self.c, state, input_)
                    self.fault("step")
                    after = [state[0] + 1, state[1] + input_]
                    self.report["transitions"] += 1
                    inputs.append(input_)
                    self.checks(after, "State", state, input_, inputs)
                    self.stop()
                    if guided:
                        self.feedback(after, inputs, state, input_)
                    self.stop()
                    state = after
                previous = inputs
        except Halt as halt:
            self.report["termination"] = str(halt)
        except CallbackError as err:
            error = str(err)
        if self.case["mode"] == "guided":
            self.report["guidance"] = self.g
        return dict(report=None if error else self.report, error=error, trace=self.trace,
                    generator_rng=self.draws, rng_audit=self.rng.audit,
                    witness_replays=[] if error else [witness(self.m, e["inputs"]) for e in self.g["corpus"]],
                    shrink=shrink_expectation(self.case, self.report) if not error else None)


def witness(model, inputs):
    states, outputs, batches = [[0, 0]], [], []
    initial, error = model_checks(model, 1, "InitialState", states[0])
    assert error is None and all(c["status"] != "Failed" for c in initial)
    batches.append(dict(phase="InitialState", checks=initial))
    for input_ in inputs:
        before = states[-1]
        assert input_ >= 0 and (model["enabled"] != "successor" or input_ == before[1] + 1)
        after = [before[0] + 1, before[1] + input_]
        states.append(after)
        outputs.append([after[1]])
        for phase in ["State", "Transition"]:
            batch, error = model_checks(model, 1, phase, after)
            assert error is None and all(c["status"] != "Failed" for c in batch)
            batches.append(dict(phase=phase, checks=batch))
    return dict(inputs=inputs[:], states=states, outputs=outputs, checks=batches, termination="Completed")


def shrink_expectation(case, report):
    if case["shrink_config"] is None:
        return None
    # Hand-derived for the two explicitly selected [1,1,5] cases, not a second
    # implementation of the shrinker. See source contract for attempt arithmetic.
    original = copy.deepcopy(report["failure"])
    assert original["inputs"] == [1, 1, 5]
    budget = case["shrink_config"]["max_attempts"]
    history = [dict(attempt=1, candidate_length=3, operation={"kind": "ValidateOriginal"}, outcome="Retained")]
    minimized, termination, transitions = copy.deepcopy(original), "AttemptLimit", 3
    if budget > 1:
        minimized["inputs"] = [5]
        history += [dict(attempt=2, candidate_length=1, operation=dict(kind="Remove", start=0, end=2), outcome="Retained"),
                    dict(attempt=3, candidate_length=0, operation=dict(kind="Remove", start=0, end=1), outcome="NoFailure")]
        termination, transitions = "SearchComplete", 4
    return dict(original=original, minimized=minimized, attempts=len(history), termination=termination,
                history=history, replayed_transitions=transitions, validated_original=True)


def build():
    cases = authored_cases()
    assert len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        case["expected"] = Author(copy.deepcopy(case)).run()
    manual_anchors(cases)
    return dict(schema="stateless-guided-v2/1", source_commit=SOURCE, cases=cases)


def manual_anchors(cases):
    """Small hand-calculated independent anchors beyond self-recomputation."""
    expected = {c["id"]: c["expected"] for c in cases}
    def report(name):
        return expected[name]["report"]
    def guidance(name):
        return report(name)["guidance"]
    assert expected["zero-cases"]["trace"] == [["metadata"]]
    assert report("zero-steps")["cases"] == 2
    assert report("zero-steps")["termination"] == "CasesCompleted"
    assert report("zero-transitions")["cases"] == 1
    assert guidance("zero-transitions")["unique_features"] == 1
    assert report("exact-last-transition")["termination"] == "CasesCompleted"
    assert report("cap-at-next-step")["termination"] == "TransitionLimit"
    assert report("cap-after-next-case-setup")["cases"] == 2
    assert guidance("default-empty-initial")["unique_features"] == 1
    assert guidance("duplicates-collisions")["unique_features"] == 5
    assert [e["new_features"] for e in guidance("duplicates-collisions")["corpus"]] == [2, 1]
    assert not guidance("feature-cap-exact-fill")["feature_limit_reached"]
    assert guidance("feature-cap-reject")["feature_limit_reached"]
    assert not guidance("corpus-exact-fill")["corpus_limit_reached"]
    assert guidance("fifo-entry-limit")["evicted_prefixes"] == 2
    assert guidance("fifo-input-limit")["corpus_inputs"] == 4
    assert guidance("fifo-input-limit")["evicted_prefixes"] == 3
    assert guidance("fifo-no-readmission")["corpus"][-1]["inputs"] == [1, 1]
    assert guidance("overlong-novel-once")["dropped_prefixes"] == 2
    recovery = guidance("recover-at-feature-cap")
    assert recovery["unique_features"] == 1 and recovery["dropped_prefixes"] == 1
    assert recovery["corpus"] == [dict(inputs=[1], new_features=0, discovered_case=2, discovered_transition=3)]
    assert guidance("projection-state-independence")["unique_features"] == 1
    assert report("projection-state-independence")["transitions"] == 4
    assert report("skipped-checks")["skipped_checks"] == 7
    assert expected["transition-sticky-overrun-own-error"]["error"] == "transition feedback: feedback own error"
    assert report("cancel-after-step-1")["transitions"] == 1
    assert guidance("cancel-after-step-1")["corpus"] == []
    assert len(guidance("cancel-after-transition_feedback-1")["corpus"]) == 1
    assert report("transition-property-before-control-feedback")["termination"] == "FailureFound"
    assert expected["transition-error-over-state-failure"]["error"] == "check_transition: edge checker broke"
    rng = expected["mutation-rejection-index"]["rng_audit"]
    assert rng[1] == {"reason": "mutation-percent", "value": "0"}
    assert rng[2]["reason"] == "mutation-percent" and int(rng[2]["value"]) >= 16
    assert expected["property-failure-shrink"]["shrink"]["minimized"]["inputs"] == [5]
    assert expected["property-failure-shrink"]["shrink"]["replayed_transitions"] == 4
    assert expected["property-failure-shrink-budget"]["shrink"]["termination"] == "AttemptLimit"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--author", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.author == args.check:
        parser.error("select exactly one of --author or --check")
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    built = build()
    payload = (json.dumps(built, indent=2, ensure_ascii=False) + "\n").encode()
    destination = ROOT / "corpus.json"
    freeze_path = ROOT / "FREEZE.json"
    if args.author:
        if freeze_path.exists():
            raise SystemExit("refusing to author after FREEZE.json; document a new independent revision first")
        temporary = ROOT / "corpus.json.tmp"
        temporary.write_bytes(payload)
        temporary.replace(destination)
        assert destination.read_bytes() == payload
        assert json.loads(destination.read_bytes()) == built
        assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == script_hash, "oracle changed during authoring"
        paths = ["conformance/guided-v2/corpus.json", "conformance/guided-v2/oracle.py",
                 "conformance/guided-v2/README.md", "docs/native-parity/N05-SOURCE-CONTRACT.md"]
        files = {p: hashlib.sha256((ROOT.parents[1] / p).read_bytes()).hexdigest() for p in paths}
        freeze = dict(schema="stateless-guided-v2-freeze/1", source_commit=SOURCE,
                      derivation="Independent source-only stdlib oracle plus hand-derived assertions; no candidate output",
                      candidate_output_used=False, cases=len(built["cases"]), files=files)
        temporary = ROOT / "FREEZE.json.tmp"
        temporary.write_text(json.dumps(freeze, indent=2) + "\n")
        temporary.replace(freeze_path)
    else:
        assert destination.read_bytes() == payload, "frozen corpus differs from independent derivation"
        freeze = json.loads(freeze_path.read_text())
        assert freeze["source_commit"] == SOURCE
        for path, expected_hash in freeze["files"].items():
            actual = hashlib.sha256((ROOT.parents[1] / path).read_bytes()).hexdigest()
            assert actual == expected_hash, f"sealed file changed: {path}"
    corpus = json.loads(payload)
    print(json.dumps(dict(mode="author" if args.author else "check", cases=len(corpus["cases"]),
                          corpus_sha256=hashlib.sha256(payload).hexdigest(), source_commit=SOURCE,
                          freeze_sha256=hashlib.sha256(freeze_path.read_bytes()).hexdigest())))


if __name__ == "__main__":
    main()
