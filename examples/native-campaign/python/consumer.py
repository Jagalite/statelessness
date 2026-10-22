#!/usr/bin/env python3
"""Installed public campaign application; run under Kyoto with Python -I -B.

No checkout imports, private scheduler access, ModelCodec, or expected-output
reader. The one-worker projection follows native-campaign-public.rs. Python
callback error messages are emitted verbatim, including native stage prefixes.
"""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, fields, replace
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import pickle
import sys
import tempfile
import threading

import statelessness
from statelessness import (
    CampaignCancellation, CampaignConfig, Check, FeedbackMetadata, FuzzConfig,
    GuidanceConfig, InputTransfer, Metadata, ModelError, ProcessWorkers,
    RunLimits, Transition, job_seed, run_campaign, run_guided_campaign,
)

U64_MAX = (1 << 64) - 1
PORTABLE_MAX = (1 << 53) - 1  # Common count operands in the independent Rust fixture.
SCHEMA = "native-campaign-v1"
CAMPAIGN_CASES = (
    "ordinary-basic", "guided-basic", "factory-error", "feedback-error",
    "factory-cancelled", "pre-cancelled", "deadline-zero", "cases-zero",
    "steps-zero", "transitions-zero", "initial-error", "step-error",
    "check-error", "metadata-panic", "feedback-metadata-panic", "drop-panic",
    "sink-error", "sink-panic", "property-stop",
)
# Inputs transcribed from Rust SEEDS; no expected seed values are embedded.
SEED_INPUTS = (
    (0, 0), (0, 1), (0, 2), (0, 7), (0, 42), (0, U64_MAX),
    (U64_MAX, 0), (U64_MAX, 1), (1, U64_MAX), (U64_MAX, 2), (2, U64_MAX),
    (1 << 63, 1 << 63), (1 << 63, (1 << 63) + 1),
    (1 << 53, U64_MAX - (1 << 53) + 1),
    ((1 << 53) + 1, U64_MAX - (1 << 53)),
)
VALIDATION_CASES = (
    "jobs-zero", "workers-zero", "workers-257", "buffer-1025", "workers-256",
    "buffer-1024", "max-id", "id-overflow", "mutation-101", "mutation-100",
    "aggregate-cases", "aggregate-transitions", "wide-product-capped",
    "max-job-count-valid", "validation-first-error",
)
ALL_CASES = tuple(f"seed-{i:02}" for i in range(len(SEED_INPUTS))) + VALIDATION_CASES + CAMPAIGN_CASES


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def installed_identity():
    """Reject source/editable imports and verify loaded modules against RECORD."""
    require(sys.flags.isolated and sys.dont_write_bytecode, "use Python -I -B")
    require(sys.prefix != sys.base_prefix, "use a fresh installed virtual environment")
    prefix = Path(sys.prefix).resolve()
    dist = importlib.metadata.distribution("statelessness")
    require(Path(dist.locate_file("")).resolve().is_relative_to(prefix),
            "distribution is outside the runtime environment")
    require(not dist.requires, "consumer expects no third-party runtime dependencies")
    direct = json.loads(dist.read_text("direct_url.json") or "null")
    require(isinstance(direct, dict) and "archive_info" in direct
            and "dir_info" not in direct, "installation must come from a package archive")
    archive_hash = direct["archive_info"].get("hashes", {}).get("sha256")
    require(isinstance(archive_hash, str) and len(archive_hash) == 64,
            "installed archive lacks SHA-256 provenance")
    records = {Path(dist.locate_file(item)).resolve(): item for item in dist.files or ()}
    verified = {}
    for name, module in tuple(sys.modules.items()):
        if name != "statelessness" and not name.startswith("statelessness."):
            continue
        filename = getattr(module, "__file__", None)
        require(filename is not None, f"package module lacks origin: {name}")
        path = Path(filename).resolve()
        require(path.is_relative_to(prefix), f"checkout or external import: {path}")
        item = records.get(path)
        require(item is not None and item.hash is not None and item.hash.mode == "sha256",
                f"missing wheel RECORD hash: {name}")
        digest = hashlib.sha256(path.read_bytes()).digest()
        actual = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        require(actual == item.hash.value, f"installed module differs from RECORD: {name}")
        verified[name] = digest.hex()
    return {"package": str(Path(statelessness.__file__).resolve()),
            "version": dist.version, "python": sys.version, "prefix": str(prefix),
            "executable": sys.executable, "archive_sha256": archive_hash,
            "verified_modules": verified}


@dataclass(frozen=True)
class LocalValue:
    """Faithfully deepcopyable application data, deliberately not pickleable."""
    value: int

    def __post_init__(self):
        if type(self.value) is not int or not 0 <= self.value <= U64_MAX:
            raise ValueError("application value must be u64")

    def __deepcopy__(self, memo):
        return type(self)(self.value)

    def __reduce_ex__(self, protocol):
        raise TypeError("application values require explicit InputTransfer")


class Command(LocalValue):
    """The application's input type, independent of every library codec."""


class CounterState(LocalValue):
    pass


class CounterOutput(LocalValue):
    pass


class DepthFeature(LocalValue):
    """Hash/equality depend only on the depth, never process identity."""


@dataclass
class Events:
    rows: list[str] | None = None
    directory: str | None = None

    def emit(self, job_id, event):
        if self.rows is not None:
            self.rows.append(f"{job_id}:{event}")
        if self.directory is not None:
            row = {"job_id": str(job_id), "event": event, "pid": os.getpid(),
                   "thread": threading.get_ident()}
            # One writer per job; sink reads only after the job's finalizer.
            with (Path(self.directory) / f"job-{job_id}.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row) + "\n")


class Counter:
    def __init__(self, job_id, mode, events):
        self.job_id, self.mode, self.events = job_id, mode, events
        self.owner = (os.getpid(), threading.get_ident())
        self.local_resource = threading.Lock()  # Cannot be serialized into a worker.

    def __reduce_ex__(self, protocol):
        raise TypeError("models stay in their factory process")

    def event(self, value):
        require(self.owner == (os.getpid(), threading.get_ident()), "model moved between workers")
        self.events.emit(self.job_id, value)

    def metadata(self):
        self.event("metadata")
        if self.mode == "metadata-panic":
            raise RuntimeError("metadata panic")
        return Metadata(f"n06-counter:{self.job_id}", 1, 1, 1, "n06-counter-v1")

    def initial_state(self):
        self.event("initial")
        if self.mode == "initial-error":
            raise ModelError("initial error")
        return CounterState(0)

    def generate(self, state, rng):
        value = rng.next_u64() if self.mode == "random" else state.value + 1
        self.event(f"generate:{state.value}:{value}")
        return Command(value)

    def is_enabled(self, state, command):
        self.event(f"enabled:{state.value}:{command.value}")
        return True

    def step(self, before, command):
        self.event(f"step:{before.value}:{command.value}")
        if self.mode == "step-error":
            raise ModelError("step error")
        return Transition(CounterState(before.value + 1), (CounterOutput(command.value),))

    def check_state(self, state):
        self.event(f"check_state:{state.value}")
        if state.value > 0 and self.mode == "check-error":
            raise ModelError("check error")
        check = (Check("n06.counter", "failed", "second transition")
                 if state.value == 2 and self.mode == "property-stop" else Check("n06.counter"))
        return (check, Check("n06.optional", "skipped", "fixture"))

    def check_transition(self, before, command, after):
        self.event(f"check_transition:{before.value}:{command.value}")
        return (Check("n06.edge"),)

    def campaign_close(self):
        self.event("model_drop")
        if self.mode == "drop-panic":
            raise RuntimeError("model drop")


class DepthFeedback:
    def __init__(self, model):
        self.model = model
        self.local_resource = threading.Lock()

    def __reduce_ex__(self, protocol):
        raise TypeError("feedback stays with its model")

    def metadata(self):
        self.model.event("feedback_metadata")
        if self.model.mode == "feedback-metadata-panic":
            raise RuntimeError("feedback metadata panic")
        return FeedbackMetadata("n06-depth", 1, "n06-depth-v1")

    def initial_features(self, state, out):
        self.model.event("feedback_initial")
        out.push(DepthFeature(state.value))

    def transition_features(self, before, command, after, out):
        self.model.event("feedback_transition")
        out.push(DepthFeature(after.state.value))

    def campaign_close(self):
        self.model.event("feedback_drop")


@dataclass
class Factory:
    mode: str
    events: Events
    cancellation: CampaignCancellation | None = None

    def __call__(self, job_id):
        self.events.emit(job_id, "factory")
        if self.mode == "factory-error":
            raise ModelError("factory error")
        if self.mode == "factory-cancelled":
            self.cancellation.cancel()
        return Counter(job_id, self.mode, self.events)


@dataclass
class FeedbackFactory:
    mode: str

    def __call__(self, job_id, model):
        require(job_id == model.job_id, "feedback attached to the wrong model")
        model.event("feedback_factory")
        if self.mode == "feedback-error":
            raise ModelError("feedback factory error")
        return DepthFeedback(model)


@dataclass(frozen=True)
class CommandTransfer:
    """Explicit eight-byte report transport, with optional evidence per process."""
    directory: str | None = None

    def note(self, operation, value):
        if self.directory is not None:
            with (Path(self.directory) / f"transfer-{os.getpid()}.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"operation": operation, "value": str(value),
                                         "pid": os.getpid()}) + "\n")

    def encode(self, command):
        if type(command) is not Command:
            raise TypeError("only application Command inputs may cross workers")
        self.note("encode", command.value)
        return command.value.to_bytes(8, "little")

    def decode(self, data):
        if type(data) is not bytes or len(data) != 8:
            raise ValueError("Command transfer requires exactly eight bytes")
        command = Command(int.from_bytes(data, "little"))
        self.note("decode", command.value)
        return command


def variant(value):
    return "".join(word.capitalize() for word in value.split("_"))


def scalars(value):
    return {field.name: (str(item) if type(item := getattr(value, field.name)) is int else item)
            for field in fields(value)}


def config_projection(config):
    return dict(scalars(config), fuzz=scalars(config.fuzz))


def campaign_projection(report):
    return dict(scalars(report), termination=variant(report.termination))


def check_status(check):
    # Rust's fixture projects CheckStatus's Debug string, including details.
    return ("Passed" if check.status == "passed" else
            f"{variant(check.status)}({json.dumps(check.details, ensure_ascii=False)})")


def fuzz_projection(report):
    failure, guidance = None, None
    if report.failure is not None:
        failure = {"inputs": [str(i.value) for i in report.failure.inputs],
                   "violations": [{"phase": variant(v.phase), "id": v.check.id,
                                   "status": check_status(v.check)} for v in report.failure.violations]}
    if report.guidance is not None:
        g = report.guidance
        guidance = dict(scalars(g), config=scalars(g.config), feedback=scalars(g.feedback),
                        corpus=[dict(scalars(entry), inputs=[str(i.value) for i in entry.inputs])
                                for entry in g.corpus])
    return dict(scalars(report), termination=variant(report.termination), failure=failure, guidance=guidance)


def job_projection(job):
    return {"job_id": str(job.job_id), "seed": str(job.seed), "config": scalars(job.config),
            "metadata": None if job.metadata is None else scalars(job.metadata),
            "outcome": variant(job.outcome.kind), "error": job.outcome.error,
            "fuzz": None if job.outcome.report is None else fuzz_projection(job.outcome.report)}


def base_config():
    return CampaignConfig(master_seed=0, first_job=7, jobs=1, workers=1, result_buffer=0,
                          fuzz=FuzzConfig(seed=999, cases=1, max_steps=2,
                                          max_transitions=2, mutation_percent=0))


def guidance():
    return GuidanceConfig(2, 3, 3, 1)


def reference_campaign(name):
    rows, jobs = [], []
    events = Events(rows=rows)
    cancellation = CampaignCancellation()
    if name == "pre-cancelled":
        cancellation.cancel()
    config = base_config()
    if name in ("factory-error", "feedback-error"):
        config = replace(config, jobs=2, stop_on_failure=True)
    if name == "property-stop":
        config = replace(config, jobs=3, stop_on_failure=True)
    fuzz_changes = {"cases-zero": {"cases": 0},
                    "steps-zero": {"cases": 2, "max_steps": 0, "max_transitions": 0},
                    "transitions-zero": {"max_transitions": 0}}.get(name, {})
    config = replace(config, fuzz=replace(config.fuzz, **fuzz_changes))
    guided = name.startswith("guided") or name in (
        "feedback-error", "factory-cancelled", "feedback-metadata-panic")
    limits = RunLimits(max_duration=0.0 if name == "deadline-zero" else None,
                       cancellation=cancellation)

    def sink(job):
        events.emit(job.job_id, "sink")
        jobs.append(job_projection(job))  # Preserve delivery order, including failed sink calls.
        if name == "sink-error":
            raise ModelError("first sink error")
        if name == "sink-panic":
            raise RuntimeError("sink panic")

    factory = Factory(name, events, cancellation)
    report = (run_guided_campaign(config, limits, guidance(), factory, FeedbackFactory(name), sink)
              if guided else run_campaign(config, limits, factory, sink))
    return {"schema": SCHEMA, "kind": "campaign", "case": name,
            "config": config_projection(config), "guided": guided, "jobs": jobs,
            "callbacks": rows, "report": campaign_projection(report)}


def reference_validation(name):
    base = base_config()
    raw = {field.name: getattr(base, field.name) for field in fields(base)}
    fuzz = {field.name: getattr(base.fuzz, field.name) for field in fields(base.fuzz)}
    edits = {"jobs-zero": {"jobs": 0}, "workers-zero": {"workers": 0},
             "workers-257": {"workers": 257}, "buffer-1025": {"result_buffer": 1025},
             "workers-256": {"workers": 256}, "buffer-1024": {"result_buffer": 1024},
             "max-id": {"first_job": U64_MAX}, "id-overflow": {"first_job": U64_MAX, "jobs": 2},
             "aggregate-cases": {"jobs": 2049}, "aggregate-transitions": {"jobs": 2049},
             "wide-product-capped": {"jobs": 2},
             "max-job-count-valid": {"first_job": 0, "jobs": PORTABLE_MAX},
             "validation-first-error": {"jobs": 0, "workers": 0, "result_buffer": 1025}}
    raw.update(edits.get(name, {}))
    fuzz.update({"mutation-101": {"mutation_percent": 101}, "mutation-100": {"mutation_percent": 100},
                 "validation-first-error": {"mutation_percent": 101},
                 "aggregate-cases": {"cases": PORTABLE_MAX},
                 "aggregate-transitions": {"cases": 1, "max_steps": PORTABLE_MAX, "max_transitions": U64_MAX},
                 "wide-product-capped": {"cases": PORTABLE_MAX, "max_steps": PORTABLE_MAX, "max_transitions": 1},
                 "max-job-count-valid": {"cases": 1, "max_steps": 1, "max_transitions": 1}}.get(name, {}))
    config = {key: str(value) if type(value) is int else value for key, value in raw.items() if key != "fuzz"}
    config["fuzz"] = {key: str(value) for key, value in fuzz.items()}
    error = None
    try:
        # The real public constructor rejects mutation101 before campaign
        # validation. Do not bypass that N05 policy to manufacture Rust parity.
        CampaignConfig(**dict(raw, fuzz=FuzzConfig(**fuzz))).validate()
    except (ModelError, ValueError) as caught:
        error = str(caught)
    return {"schema": SCHEMA, "kind": "validation", "case": name, "config": config, "error": error}


def reference_case(name):
    if name in CAMPAIGN_CASES:
        return reference_campaign(name)
    if name in VALIDATION_CASES:
        return reference_validation(name)
    if name in ALL_CASES:
        master, identifier = SEED_INPUTS[int(name.removeprefix("seed-"))]
        return {"schema": SCHEMA, "kind": "seed", "case": name,
                "master_seed": str(master), "job_id": str(identifier),
                "seed": str(job_seed(master, identifier))}
    raise ValueError(f"unsupported Rust public fixture: {name}")


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def demo_run(directory, *, guided, workers, jobs):
    directory.mkdir()
    caller = (os.getpid(), threading.get_ident())
    config = replace(base_config(), jobs=jobs, workers=workers)
    factory = Factory("pass" if guided else "property-stop", Events(directory=str(directory)))
    transfer = CommandTransfer(str(directory))

    def decode_on_caller(data):
        # The encoder crosses spawn; this closure stays with the caller sink.
        require(caller == (os.getpid(), threading.get_ident()), "decoder left original caller")
        return transfer.decode(data)

    policy = ProcessWorkers(InputTransfer(transfer.encode, decode_on_caller))
    delivered, observed = [], []

    def sink(job):
        require(caller == (os.getpid(), threading.get_ident()), "sink left original caller")
        require(job.outcome.kind == "completed", f"job failed: {job.outcome}")
        rows = read_rows(directory / f"job-{job.job_id}.jsonl")
        owners = {(row["pid"], row["thread"]) for row in rows}
        require(len(owners) == 1, "job callbacks moved between owners")
        owner, = owners
        require((owner == caller) if workers == 1 else (owner[0] != caller[0]),
                "configured worker policy did not select caller/spawn")
        require(rows[0]["event"] == "factory" and rows[-1]["event"] == "model_drop",
                "model was not constructed and closed before sink")
        if guided:
            require(rows[-2]["event"] == "feedback_drop", "feedback must close before model")
            require(job.outcome.report.guidance is not None, "guided campaign lost its report")
            inputs = [i for entry in job.outcome.report.guidance.corpus for i in entry.inputs]
            require([[i.value for i in entry.inputs] for entry in job.outcome.report.guidance.corpus]
                    == [[1], [1, 2]], "guided report input values or prefix boundaries changed")
        else:
            require(job.outcome.report.failure is not None, "ordinary campaign missed fixture property")
            inputs = list(job.outcome.report.failure.inputs)
            require([i.value for i in inputs] == [1, 2], "ordinary failure inputs changed in transfer")
        require(inputs and all(type(i) is Command for i in inputs), "report inputs were not faithfully reconstructed")
        delivered.append(job_projection(job))
        observed.append({"job_id": str(job.job_id), "pid": owner[0], "thread": owner[1],
                         "callbacks": rows, "sink_pid": caller[0], "sink_thread": caller[1]})

    report = (run_guided_campaign(config, RunLimits(), guidance(), factory,
                                 FeedbackFactory("pass"), sink, process=policy)
              if guided else run_campaign(config, RunLimits(), factory, sink, process=policy))
    require(report.termination == "jobs_completed" and report.error is None,
            f"campaign did not finish: {report}")
    require((report.started, report.finished, report.delivered, report.errors) == (jobs, jobs, jobs, 0),
            "campaign accounting differs from observed jobs")
    require({row["job_id"] for row in delivered} == {str(i) for i in range(config.first_job, config.first_job + jobs)},
            "missing or duplicate job identity")
    transfers = [row for path in sorted(directory.glob("transfer-*.jsonl")) for row in read_rows(path)]
    if workers > 1:
        encoded = [row for row in transfers if row["operation"] == "encode"]
        decoded = [row for row in transfers if row["operation"] == "decode"]
        require(encoded and len(encoded) == len(decoded), "transfer hooks were not exercised")
        require(all(row["pid"] != caller[0] for row in encoded)
                and all(row["pid"] == caller[0] for row in decoded), "input transfer ran on the wrong side")
    else:
        require(not transfers, "caller-only execution unexpectedly serialized inputs")
    return {"guided": guided, "config": config_projection(config), "jobs": delivered,
            "report": campaign_projection(report), "observations": observed, "transfers": transfers}


def demo(evidence=None):
    # These are demonstrations of the explicit transfer boundary, not Rust
    # goldens or a full scheduler/barrier qualification suite.
    for value in (Command(1), CounterState(0), CounterOutput(1), DepthFeature(0),
                  Counter(0, "pass", Events()), DepthFeedback(Counter(0, "pass", Events()))):
        try:
            pickle.dumps(value)
        except TypeError:
            pass
        else:
            raise RuntimeError(f"{type(value).__name__} unexpectedly permits pickle transfer")
    if evidence is None:
        directory = Path(tempfile.mkdtemp(prefix="n06-python-application-")).resolve()
    else:
        directory = evidence.resolve()
        directory.mkdir(parents=True, exist_ok=False)
    print(f"Campaign evidence retained at {directory}", file=sys.stderr, flush=True)
    result = {"schema": "native-campaign-python-example-v1", "engine": "python",
              "evidence_directory": str(directory), "status": "running",
              "installed": installed_identity(), "runs": []}

    def save():
        (directory / "application.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    save()
    try:
        for guided in (False, True):
            for workers, jobs in ((1, 3), (2, 1), (2, 3)):
                name = f"{'guided' if guided else 'ordinary'}-{workers}-{jobs}"
                result["active_run"] = name
                save()
                run = demo_run(directory / name, guided=guided, workers=workers, jobs=jobs)
                (directory / name / "result.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
                result["runs"].append(run)
                save()
        result.update(status="completed", active_run=None, installed=installed_identity())
        save()
        return result
    except BaseException as error:
        result.update(status="failed", error=str(error))
        save()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--case", choices=ALL_CASES)
    group.add_argument("--all-reference", action="store_true")
    group.add_argument("--installed-proof", action="store_true")
    group.add_argument("--list-cases", action="store_true")
    group.add_argument("--demo", action="store_true", help="default: ordinary/guided caller and real spawn paths")
    parser.add_argument("--evidence", type=Path, help="new directory retaining demo callbacks, transfers and partial results")
    args = parser.parse_args()
    if args.evidence and (args.case or args.all_reference or args.installed_proof or args.list_cases):
        parser.error("--evidence applies only to --demo (the default)")
    identity = installed_identity()
    if args.list_cases:
        print(json.dumps({"schema": "native-campaign-case-inventory-v1", "cases": ALL_CASES}))
    elif args.installed_proof:
        print(json.dumps(identity, sort_keys=True))
    elif args.case or args.all_reference:
        for name in ALL_CASES if args.all_reference else (args.case,):
            print(json.dumps(reference_case(name), sort_keys=True), flush=True)
        installed_identity()  # Include modules imported lazily during campaign execution.
    else:
        print(json.dumps(demo(args.evidence), sort_keys=True), flush=True)


if __name__ == "__main__":
    # Spawn imports this file as __mp_main__; it must never recursively run CLI.
    main()
