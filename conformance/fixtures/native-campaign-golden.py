"""Independent N06 public expected rows, authored before executing Rust.

No runtime import, Rust subprocess, source scraping, or adapter output is used.
Only this tiny counter's declared behavior and manual callback paths are modeled.
Invocation prints JSONL; redirect to a new evidence path through Kyoto if desired.
"""
import json
import sys

CASES = ["ordinary-basic", "guided-basic", "factory-error", "feedback-error",
         "factory-cancelled", "pre-cancelled", "deadline-zero", "cases-zero",
         "steps-zero", "transitions-zero", "initial-error", "step-error",
         "check-error", "metadata-panic", "feedback-metadata-panic", "drop-panic", "sink-error", "sink-panic",
         "property-stop"]
SEEDS = {7: "7191089600892374487", 8: "11409396526365357622"}
GUIDANCE = dict(max_corpus_entries="2", max_corpus_inputs="3", max_features="3",
                max_features_per_observation="1")
FEEDBACK = dict(name="n06-depth", version="1", build="n06-depth-v1")


def row(name):
    guided = name in ("guided-basic", "feedback-error", "factory-cancelled", "feedback-metadata-panic")
    jobs = 2 if name in ("factory-error", "feedback-error") else 3 if name == "property-stop" else 1
    cases = 0 if name == "cases-zero" else 2 if name == "steps-zero" else 1
    steps = 0 if name == "steps-zero" else 2
    cap = 0 if name in ("steps-zero", "transitions-zero") else 2
    fc = dict(seed="999", cases=str(cases), max_steps=str(steps), max_transitions=str(cap), mutation_percent="0")
    config = dict(master_seed="0", first_job="7", jobs=str(jobs), workers="1", result_buffer="0", fuzz=fc,
                  stop_on_failure=name in ("factory-error", "feedback-error", "property-stop"))
    stopped = name in ("pre-cancelled", "deadline-zero")
    started = 0 if stopped else 1 if name == "property-stop" else jobs
    collected, callbacks = [], []
    work_cases = work_steps = skipped = errors = failures = 0
    complete = True
    for job_id in range(7, 7 + started):
        trace = ["factory"]
        meta = dict(name=f"n06-counter:{job_id}", model_version="1", properties_version="1", codec_version="1", build="n06-counter-v1")
        outcome, error, fuzz = "Completed", None, None
        jcases = jsteps = jskips = 0
        termination, failure = "CasesCompleted", None
        if name == "factory-error":
            outcome, error, meta = "FactoryError", "factory error", None
        else:
            trace += ["metadata"]
            if name == "metadata-panic":
                outcome, error, meta = "Panicked", "metadata panic", None
            elif name == "feedback-error":
                trace += ["feedback_factory"]
                outcome, error = "ModelError", "feedback factory error"
            elif name == "feedback-metadata-panic":
                trace += ["feedback_factory", "feedback_metadata", "feedback_drop"]
                outcome, error = "Panicked", "feedback metadata panic"
            else:
                if guided:
                    trace += ["feedback_factory", "feedback_metadata"]
                if name == "factory-cancelled":
                    termination = "Cancelled"
                else:
                    for _ in range(cases):
                        trace += ["initial"]
                        if name == "initial-error":
                            outcome, error = "ModelError", "initial_state: initial error"
                            break
                        jcases += 1
                        trace += ["check_state:0"]
                        jskips += 1
                        if guided:
                            trace += ["feedback_initial"]
                        for state in range(steps):
                            if jsteps == cap:
                                termination = "TransitionLimit"
                                break
                            value = state + 1
                            trace += [f"generate:{state}:{value}", f"enabled:{state}:{value}", f"step:{state}:{value}"]
                            if name == "step-error":
                                outcome, error = "ModelError", "step: step error"
                                break
                            jsteps += 1
                            trace += [f"check_state:{state + 1}"]
                            if name == "check-error":
                                outcome, error = "ModelError", "check_state: check error"
                                break
                            trace += [f"check_transition:{state}:{value}"]
                            jskips += 1
                            if name == "property-stop" and state == 1:
                                termination = "FailureFound"
                                failure = dict(inputs=["1", "2"], violations=[dict(phase="State", id="n06.counter", status='Failed("second transition")')])
                                break
                            if guided:
                                trace += ["feedback_transition"]
                        if error or termination != "CasesCompleted":
                            break
                if guided:
                    trace += ["feedback_drop"]
            trace += ["model_drop"]
            if name == "drop-panic":
                outcome, error = "Panicked", "model drop"
        if outcome == "Completed":
            guide = None
            if guided:
                corpus = [] if name == "factory-cancelled" else [
                    dict(inputs=["1"], new_features="1", discovered_case="1", discovered_transition="1"),
                    dict(inputs=["1", "2"], new_features="1", discovered_case="1", discovered_transition="2")]
                guide = dict(algorithm="feature-corpus-v2", config=GUIDANCE, feedback=FEEDBACK,
                             unique_features="0" if name == "factory-cancelled" else "3",
                             corpus_inputs="0" if name == "factory-cancelled" else "3", corpus=corpus,
                             mutation_cases="0", dropped_prefixes="0", evicted_prefixes="0",
                             feature_limit_reached=False, corpus_limit_reached=False)
            fuzz = dict(termination=termination, seed=SEEDS[job_id], cases=str(jcases), transitions=str(jsteps),
                        skipped_checks=str(jskips), failure=failure, guidance=guide)
            work_cases += jcases
            work_steps += jsteps
            skipped += jskips
            failures += int(failure is not None)
        else:
            errors += 1
            complete &= outcome == "FactoryError"
        trace += ["sink"]
        callbacks += [f"{job_id}:{event}" for event in trace]
        collected.append(dict(job_id=str(job_id), seed=SEEDS[job_id], config=dict(fc, seed=SEEDS[job_id]),
                              metadata=meta, outcome=outcome, error=error, fuzz=fuzz))
    termination = {"pre-cancelled": "Cancelled", "deadline-zero": "Deadline", "factory-cancelled": "Cancelled",
                   "property-stop": "FailureFound", "sink-error": "CallbackError", "sink-panic": "CallbackError"}.get(name, "JobsCompleted")
    error = {"sink-error": "first sink error", "sink-panic": "campaign callback panicked: sink panic"}.get(name)
    report = dict(requested=str(jobs), started=str(started), finished=str(started), not_started=str(jobs-started),
                  delivered=str(0 if error else started), failures=str(failures), errors=str(errors),
                  reported_transitions=str(work_steps), reported_cases=str(work_cases), reported_skipped_checks=str(skipped),
                  accounting_complete=complete, termination=termination, error=error)
    return dict(schema="native-campaign-v1", kind="campaign", case=name, config=config, guided=guided,
                jobs=collected, callbacks=callbacks, report=report)


def strict_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON object key: {key}")
        obj[key] = value
    return obj


def reject_constant(value):
    raise ValueError(f"non-finite JSON value: {value}")


def strict_loads(text):
    return json.loads(text, object_pairs_hook=strict_object, parse_constant=reject_constant)


def equal(actual, expected, path="$"):
    if type(actual) is not type(expected):
        raise AssertionError(f"{path}: type {type(actual).__name__} != {type(expected).__name__}")
    if isinstance(expected, dict):
        if actual.keys() != expected.keys():
            raise AssertionError(f"{path}: keys {actual.keys()} != {expected.keys()}")
        for key in expected:
            equal(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        if len(actual) != len(expected):
            raise AssertionError(f"{path}: array length {len(actual)} != {len(expected)}")
        for index, (a, e) in enumerate(zip(actual, expected)):
            equal(a, e, f"{path}[{index}]")
    elif actual != expected:
        raise AssertionError(f"{path}: {actual!r} != {expected!r}")


if __name__ == "__main__":
    expected = [row(case) for case in CASES]
    if sys.argv[1:] == ["--compare"]:
        equal([strict_loads(line) for line in sys.stdin.read().splitlines()], expected)
    elif not sys.argv[1:]:
        for item in expected:
            print(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
    else:
        raise SystemExit("usage: native-campaign-golden.py [--compare]")
