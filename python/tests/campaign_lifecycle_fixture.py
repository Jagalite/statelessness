"""N06 test-only protocol codec and audited model of the real decision core.

The wire dictionaries/bytes follow N06-RUST-CONTRACT.md. The reference reducer
below reduces *observations into a ledger*, not a second scheduler. Decisions
always execute statelessness._campaign_lifecycle.Lifecycle.step. No production
checker, serializer, or Summary.is_valid is used to establish expectations.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json

from statelessness import Check, Metadata, ModelError, Transition
from statelessness._campaign_lifecycle import Effect, Event, Lifecycle, State, Summary
from statelessness.campaign import CampaignReport

U64_MAX = (1 << 64) - 1
COUNTS = ("requested", "started", "finished", "not_started", "delivered",
          "failures", "errors", "reported_transitions", "reported_cases",
          "reported_skipped_checks")
OUTCOMES = ("Completed", "Failure", "Cancelled", "Deadline", "FactoryError",
            "ModelError", "Panicked")
TERMINATIONS = ("JobsCompleted", "FailureFound", "Cancelled", "Deadline",
                "CallbackError", "WorkerError")
STOPS = (None, "Failure", "Cancelled", "Deadline", "Sink", "Worker")
EVENTS = ("Claim", "Complete", "Receive", "SinkReturned", "WorkerFailed", "Finish")
EFFECTS = ("RunJob", "Deliver", "Discard")
NATIVE = dict(zip(OUTCOMES + TERMINATIONS + EVENTS + EFFECTS + ("Sink", "Worker"),
    ("completed", "failure", "cancelled", "deadline", "factory_error", "model_error", "panicked",
     "jobs_completed", "failure_found", "cancelled", "deadline", "callback_error", "worker_error",
     "claim", "complete", "receive", "sink_returned", "worker_failed", "finish",
     "run_job", "deliver", "discard", "sink", "worker")))
WIRE = {value: key for key, value in NATIVE.items()}


def summary(outcome="completed", **counts):
    error = outcome in ("factory_error", "model_error", "panicked")
    values = dict(failure=outcome == "failure", transitions=0 if error else 2,
                  cases=0 if error else 1, skipped_checks=0 if error else 1)
    values.update(counts)
    return Summary(outcome, **values)


def complete(job_id, outcome="completed", **counts):
    return Event("complete", job_id=job_id, summary=summary(outcome, **counts))


def sink(job_id, error=None):
    return Event("sink_returned", job_id=job_id, error=error)


def normalize_summary(value):
    return {"outcome": WIRE[value.outcome], "failure": value.failure,
            **{name: str(getattr(value, name)) for name in ("transitions", "cases", "skipped_checks")}}


def normalize_event(event):
    kind = WIRE[event.kind]
    body = {}
    if kind == "Claim":
        body["stop"] = None if event.stop is None else WIRE[event.stop]
    elif kind in ("Complete", "Receive", "SinkReturned"):
        body["job_id"] = str(event.job_id)
        if kind == "Complete":
            body["summary"] = normalize_summary(event.summary)
        elif kind == "SinkReturned":
            body["error"] = event.error
    elif kind == "WorkerFailed":
        body.update(error=event.error, lost_work=event.lost_work)
    return {kind: body}


def native_event(value):
    kind, body = _variant(value, EVENTS)
    fields = dict(body)
    if "job_id" in fields:
        fields["job_id"] = _number(fields["job_id"])
    if kind == "Complete":
        fields["summary"] = _native_summary(fields["summary"])
    if kind == "Claim" and fields["stop"] is not None:
        fields["stop"] = NATIVE[fields["stop"]]
    return Event(NATIVE[kind], **fields)


def normalize_effect(effect):
    return {WIRE[effect.kind]: str(effect.job_id)}


def normalize_state(state):
    report = {name: str(getattr(state.report, name)) for name in COUNTS}
    report.update(accounting_complete=state.report.accounting_complete,
                  termination=WIRE[state.report.termination], error=state.report.error)
    return dict(report=report, stop=None if state.stop is None else WIRE[state.stop],
                pending=[dict(job_id=str(job_id), status="Running" if value is None else "Ready",
                              summary=None if value is None else normalize_summary(value))
                         for job_id, value in sorted(state.pending.items())],
                delivering=None if state.delivering is None else str(state.delivering),
                lost_worker=state.lost_worker, finalized=state.finalized)


def _native_summary(value):
    return Summary(NATIVE[value["outcome"]], value["failure"],
                   *(_number(value[name]) for name in ("transitions", "cases", "skipped_checks")))


def native_state(value):
    report = value["report"]
    return State(CampaignReport(**{name: _number(report[name]) for name in COUNTS},
        accounting_complete=report["accounting_complete"],
        termination=NATIVE[report["termination"]], error=report["error"]),
        stop=None if value["stop"] is None else NATIVE[value["stop"]],
        pending={_number(item["job_id"]): None if item["status"] == "Running"
                 else _native_summary(item["summary"]) for item in value["pending"]},
        delivering=None if value["delivering"] is None else _number(value["delivering"]),
        lost_worker=value["lost_worker"], finalized=value["finalized"])


def _invalid():
    return ModelError("invalid campaign lifecycle encoding")


def _number(value):
    if type(value) is str and value.isascii() and value.isdecimal():
        number = int(value)
        if str(number) == value and number <= U64_MAX:
            return number
    raise _invalid()


def _variant(value, variants):
    if type(value) is not dict or len(value) != 1:
        raise _invalid()
    kind = next(iter(value))
    if kind not in variants:
        raise _invalid()
    return kind, value[kind]


class _Writer:
    def __init__(self):
        self.data = bytearray()

    def tag(self, value):
        self.data.append(value)

    def boolean(self, value):
        if type(value) is not bool:
            raise _invalid()
        self.tag(int(value))

    def number(self, value):
        self.data.extend(_number(value).to_bytes(8, "little"))

    def string(self, value):
        try:
            encoded = value.encode("utf-8", "strict")
        except (AttributeError, UnicodeError) as error:
            raise _invalid() from error
        self.number(str(len(encoded)))
        self.data.extend(encoded)

    def summary(self, value):
        self.tag(OUTCOMES.index(value["outcome"]))
        self.boolean(value["failure"])
        for name in ("transitions", "cases", "skipped_checks"):
            self.number(value[name])


class _Reader:
    def __init__(self, data):
        if type(data) is not bytes:
            raise _invalid()
        self.data, self.offset = data, 0

    def take(self, size):
        if size > len(self.data) - self.offset:
            raise _invalid()
        result = self.data[self.offset:self.offset + size]
        self.offset += size
        return result

    def tag(self, variants):
        value = self.take(1)[0]
        if value >= len(variants):
            raise _invalid()
        return variants[value]

    def boolean(self):
        return self.tag((False, True))

    def number(self):
        return str(int.from_bytes(self.take(8), "little"))

    def string(self):
        try:
            return self.take(int(self.number())).decode("utf-8", "strict")
        except UnicodeError as error:
            raise _invalid() from error

    def summary(self):
        return dict(outcome=self.tag(OUTCOMES), failure=self.boolean(),
                    transitions=self.number(), cases=self.number(), skipped_checks=self.number())

    def finish(self):
        if self.offset != len(self.data):
            raise _invalid()


def _coherent(value):
    """Protocol rule, intentionally independent of runtime Summary.is_valid."""
    if value["outcome"] in ("FactoryError", "ModelError", "Panicked"):
        return not value["failure"] and all(value[name] == "0"
            for name in ("transitions", "cases", "skipped_checks"))
    return value["failure"] == (value["outcome"] == "Failure")


def state_properties(value, first_job, jobs, stop_on_failure):
    """Rust codec admission properties; no runtime checker is consulted."""
    r = value["report"]
    n = {name: _number(r[name]) for name in COUNTS}
    ids = [_number(item["job_id"]) for item in value["pending"]]
    delivering, stop, termination = value["delivering"], value["stop"], r["termination"]
    error = termination in ("CallbackError", "WorkerError")
    accounting = (n["requested"] == jobs and n["started"] <= jobs
        and n["not_started"] == jobs - n["started"]
        and n["delivered"] <= n["finished"] <= n["started"]
        and n["failures"] + n["errors"] <= n["finished"])
    pending = (n["started"] - n["finished"] == len(ids)
        and all(first_job <= job_id < first_job + n["started"] for job_id in ids)
        and all(item["summary"] is None if item["status"] == "Running"
                else _coherent(item["summary"]) for item in value["pending"]))
    delivery = (n["finished"] > n["delivered"] if termination == "CallbackError"
                else n["finished"] - n["delivered"] == int(delivering is not None))
    if delivering is not None:
        job_id = _number(delivering)
        delivery &= (job_id not in ids and first_job <= job_id < first_job + n["started"]
                     and n["delivered"] < n["finished"] and termination != "CallbackError")
    finalized = (not value["finalized"] or (delivering is None
        and (not ids or value["lost_worker"] and not r["accounting_complete"])
        and (termination != "JobsCompleted" or n["started"] == jobs)))
    if termination == "CallbackError":
        reason = stop in ("Sink", "Worker", "Cancelled", "Deadline")
    elif termination == "WorkerError":
        reason = stop in ("Worker", "Cancelled", "Deadline")
    elif not value["finalized"]:
        reason = termination == "JobsCompleted" and stop not in ("Sink", "Worker")
    else:
        reason = (stop, termination) in ((None, "JobsCompleted"), ("Failure", "FailureFound"),
                                        ("Cancelled", "Cancelled"), ("Deadline", "Deadline"))
    reason &= (not value["lost_worker"] or error and not r["accounting_complete"])
    observed_failure = n["failures"] > 0 or any(item["summary"] is not None
        and item["summary"]["failure"] for item in value["pending"])
    reason &= stop != "Failure" or stop_on_failure and observed_failure
    return dict(accounting=accounting, pending=pending, delivery=delivery,
                finalized=finalized, termination=reason)


class LifecycleCodec:
    """Independent test-only Rust byte contract over portable dictionaries."""
    def __init__(self, first_job=50, jobs=2, stop_on_failure=False):
        self.first_job, self.jobs, self.stop_on_failure = first_job, jobs, stop_on_failure

    def encode_input(self, value):
        kind, body = _variant(value, EVENTS)
        out = _Writer()
        out.tag(EVENTS.index(kind))
        if kind == "Claim":
            out.tag((None, "Cancelled", "Deadline").index(body["stop"]))
        elif kind in ("Complete", "Receive", "SinkReturned"):
            out.number(body["job_id"])
            if kind == "Complete":
                out.summary(body["summary"])
            elif kind == "SinkReturned":
                out.boolean(body["error"] is not None)
                if body["error"] is not None:
                    out.string(body["error"])
        elif kind == "WorkerFailed":
            out.string(body["error"])
            out.boolean(body["lost_work"])
        return bytes(out.data)

    def decode_input(self, data):
        source, body = _Reader(data), {}
        kind = source.tag(EVENTS)
        if kind == "Claim":
            body["stop"] = source.tag((None, "Cancelled", "Deadline"))
        elif kind in ("Complete", "Receive", "SinkReturned"):
            body["job_id"] = source.number()
            if kind == "Complete":
                body["summary"] = source.summary()
            elif kind == "SinkReturned":
                body["error"] = source.string() if source.boolean() else None
        elif kind == "WorkerFailed":
            body.update(error=source.string(), lost_work=source.boolean())
        source.finish()
        return {kind: body}

    def encode_output(self, value):
        kind, job_id = _variant(value, EFFECTS)
        out = _Writer()
        out.tag(EFFECTS.index(kind))
        out.number(job_id)
        return bytes(out.data)

    def decode_output(self, data):
        source = _Reader(data)
        kind, job_id = source.tag(EFFECTS), source.number()
        source.finish()
        return {kind: job_id}

    def encode_state(self, value):
        out, report = _Writer(), value["report"]
        for name in COUNTS:
            out.number(report[name])
        out.boolean(report["accounting_complete"])
        out.tag(TERMINATIONS.index(report["termination"]))
        if report["termination"] in ("CallbackError", "WorkerError"):
            out.string(report["error"])
        out.tag(STOPS.index(value["stop"]))
        out.number(str(len(value["pending"])))
        for item in value["pending"]:
            out.number(item["job_id"])
            out.tag(("Running", "Ready").index(item["status"]))
            if item["status"] == "Ready":
                out.summary(item["summary"])
        out.boolean(value["delivering"] is not None)
        if value["delivering"] is not None:
            out.number(value["delivering"])
        out.boolean(value["lost_worker"])
        out.boolean(value["finalized"])
        return bytes(out.data)

    def decode_state(self, data):
        source = _Reader(data)
        report = {name: source.number() for name in COUNTS}
        report.update(accounting_complete=source.boolean(), termination=source.tag(TERMINATIONS))
        report["error"] = source.string() if report["termination"] in TERMINATIONS[4:] else None
        stop, count = source.tag(STOPS), int(source.number())
        if count > self.jobs or count > (len(data) - source.offset) // 9:
            raise _invalid()
        pending, previous = [], -1
        for _ in range(count):
            job_id = source.number()
            if int(job_id) <= previous:
                raise _invalid()
            previous = int(job_id)
            status = source.tag(("Running", "Ready"))
            pending.append(dict(job_id=job_id, status=status,
                                summary=source.summary() if status == "Ready" else None))
        value = dict(report=report, stop=stop, pending=pending,
                     delivering=source.number() if source.boolean() else None,
                     lost_worker=source.boolean(), finalized=source.boolean())
        source.finish()
        if not all(state_properties(value, self.first_job, self.jobs, self.stop_on_failure).values()):
            raise ModelError("decoded campaign state violates lifecycle properties")
        return value


@dataclass
class Ledger:
    claims: set = field(default_factory=set)
    completions: dict = field(default_factory=dict)
    receipts: set = field(default_factory=set)
    attempts: set = field(default_factory=set)
    returns: set = field(default_factory=set)
    successes: set = field(default_factory=set)
    discards: set = field(default_factory=set)
    first_sink_error: str | None = None
    last_worker_error: str | None = None
    cancelled: bool = False
    deadline: bool = False
    failure_stop: bool = False
    lost_work: bool = False
    finalized: bool = False
    invalid_protocol: bool = False

    def stopped(self):
        return (self.cancelled or self.deadline or self.failure_stop
                or self.first_sink_error is not None or self.last_worker_error is not None)


def reduce_ledger(before, event, actual, stop_on_failure):
    """Reference observation reducer: record identity facts, never schedule jobs."""
    result = deepcopy(before)
    if actual.disposition.kind == "accepted":
        job_id = event.job_id
        if event.kind == "claim" and event.stop:
            setattr(result, event.stop, True)
        elif event.kind == "complete":
            result.invalid_protocol |= job_id not in result.claims or job_id in result.completions
            result.completions[job_id] = normalize_summary(event.summary)
            result.cancelled |= event.summary.outcome == "cancelled"
            result.deadline |= event.summary.outcome == "deadline"
            result.failure_stop |= stop_on_failure and event.summary.outcome == "failure"
        elif event.kind == "receive":
            result.invalid_protocol |= job_id not in result.completions or job_id in result.receipts
            result.receipts.add(job_id)
        elif event.kind == "sink_returned":
            result.invalid_protocol |= job_id not in result.attempts or job_id in result.returns
            result.returns.add(job_id)
            if event.error is None:
                result.invalid_protocol |= job_id in result.successes
                result.successes.add(job_id)
            elif result.first_sink_error is None:
                result.first_sink_error = event.error
        elif event.kind == "worker_failed":
            result.last_worker_error = event.error
            result.lost_work |= event.lost_work
        elif event.kind == "finish":
            result.finalized = True
    for effect in actual.outputs:
        job_id = effect.job_id
        if effect.kind == "run_job":
            result.invalid_protocol |= job_id in result.claims or before.stopped() or before.finalized
            result.claims.add(job_id)
        elif effect.kind == "deliver":
            result.invalid_protocol |= (job_id not in result.receipts or job_id in result.attempts
                or job_id in result.discards or result.first_sink_error is not None)
            result.attempts.add(job_id)
        elif effect.kind == "discard":
            result.invalid_protocol |= (job_id not in result.receipts or job_id in result.discards
                or job_id in result.attempts or result.first_sink_error is None)
            result.discards.add(job_id)
        else:
            result.invalid_protocol = True
    return result


def admissible(ledger, event, jobs):
    if ledger.finalized:
        return False
    active = ledger.attempts - ledger.returns
    if event.kind == "claim":
        return not ledger.stopped() and len(ledger.claims) < jobs
    if event.kind == "complete":
        return (event.job_id in ledger.claims and event.job_id not in ledger.completions
                and _coherent(normalize_summary(event.summary)))
    if event.kind == "receive":
        return event.job_id in ledger.completions and event.job_id not in ledger.receipts and not active
    if event.kind == "sink_returned":
        return active == {event.job_id}
    if event.kind == "worker_failed":
        return True
    if event.kind == "finish":
        return (not active and (ledger.claims == ledger.receipts or ledger.lost_work)
                and (ledger.stopped() or len(ledger.claims) == jobs))
    return False


def _check(name, condition):
    return Check(name) if condition else Check(name, "failed", "campaign lifecycle contract violated")


def _ledger_value(ledger):
    value = dict(vars(ledger))
    for name in ("claims", "receipts", "attempts", "returns", "successes", "discards"):
        value[name] = [str(job_id) for job_id in sorted(value[name])]
    value["completions"] = [[str(job_id), item] for job_id, item in sorted(ledger.completions.items())]
    return value


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


@dataclass
class AuditedState:
    core: State
    ledger: Ledger

    def __hash__(self):
        # History participates in graph identity; rejected inputs add no counters.
        return hash((_json(normalize_state(self.core)), _json(_ledger_value(self.ledger))))


class AuditedLifecycle:
    def __init__(self, jobs=2, stop_on_failure=False, first_job=50, *, duplicate_delivery=False):
        self.core = Lifecycle(first_job, jobs, stop_on_failure)
        self.codec = LifecycleCodec(first_job, jobs, stop_on_failure)
        self.duplicate_delivery = duplicate_delivery

    def metadata(self):
        return Metadata(f"python-campaign-audit:{self.core.first_job}:{self.core.jobs}:{self.core.stop_on_failure}",
            properties_version=2, codec_version=1,
            build="n06-ledger-v1" + (":duplicate-delivery" if self.duplicate_delivery else ""))

    def initial_state(self):
        return AuditedState(self.core.initial_state(), Ledger())

    def observe(self, before, event, actual):
        ledger = reduce_ledger(before.ledger, event, actual, self.core.stop_on_failure)
        return Transition(AuditedState(actual.state, ledger), actual.outputs, actual.disposition)

    def step(self, before, event):
        actual = self.core.step(before.core, event)
        if self.duplicate_delivery and actual.outputs and actual.outputs[0].kind == "deliver":
            actual = Transition(actual.state, actual.outputs + (actual.outputs[0],), actual.disposition)
        return self.observe(before, event, actual)

    def check_state(self, state):
        ledger, report = state.ledger, state.core.report
        checks = [_check("campaign." + key, value) for key, value in state_properties(
            normalize_state(state.core), self.core.first_job, self.core.jobs, self.core.stop_on_failure).items()]
        received = [ledger.completions[job_id] for job_id in ledger.receipts if job_id in ledger.completions]
        totals = [sum(int(item[name]) for item in received) for name in ("transitions", "cases", "skipped_checks")]
        accounting = (not ledger.lost_work and all(item["outcome"] not in ("ModelError", "Panicked")
            for item in received) and all(total <= U64_MAX for total in totals)
            and (not ledger.finalized or ledger.claims == ledger.receipts))
        termination, error = "jobs_completed", None
        if ledger.first_sink_error is not None:
            termination, error = "callback_error", ledger.first_sink_error
        elif ledger.last_worker_error is not None:
            termination, error = "worker_error", ledger.last_worker_error
        elif ledger.finalized:
            termination = ("cancelled" if ledger.cancelled else "deadline" if ledger.deadline
                           else "failure_found" if ledger.failure_stop else "jobs_completed")
        pending = {job_id: ledger.completions.get(job_id) for job_id in ledger.claims - ledger.receipts}
        actual_pending = {job_id: None if item is None else normalize_summary(item)
                          for job_id, item in state.core.pending.items()}
        checks.extend(_check(name, value) for name, value in (
            ("audit.protocol", not ledger.invalid_protocol),
            ("audit.identities", ledger.successes <= ledger.returns <= ledger.attempts <= ledger.receipts
                <= ledger.completions.keys() <= ledger.claims),
            ("audit.claim_order", sorted(ledger.claims) == list(range(self.core.first_job,
                self.core.first_job + len(ledger.claims)))),
            ("audit.pending", actual_pending == pending),
            ("audit.stop_observed", (state.core.stop is not None) == ledger.stopped()),
            ("audit.loss_observed", state.core.lost_worker == ledger.lost_work),
            ("audit.active_sink", ledger.attempts - ledger.returns ==
                (set() if state.core.delivering is None else {state.core.delivering})),
            ("audit.identity_accounting", (report.started, report.finished, report.delivered,
                report.failures, report.errors) == (len(ledger.claims), len(ledger.receipts),
                len(ledger.successes), sum(item["failure"] for item in received),
                sum(item["outcome"] in OUTCOMES[4:] for item in received))),
            ("audit.work_accounting", [report.reported_transitions, report.reported_cases,
                report.reported_skipped_checks] == [min(total, U64_MAX) for total in totals]
                and report.accounting_complete == accounting),
            ("audit.receipt_has_one_destination", not ledger.attempts & ledger.discards
                and ledger.attempts | ledger.discards == ledger.receipts),
            ("audit.stop_precedence", (report.termination, report.error) == (termination, error)),
            ("audit.finalized", state.core.finalized == ledger.finalized),
        ))
        return tuple(checks)

    def check_transition(self, before, event, transition):
        accepted = transition.disposition.kind == "accepted"
        allowed = admissible(before.ledger, event, self.core.jobs)
        expected = ()
        # Stop identity is observable even when an infrastructure error masks
        # ordinary terminal status. Check its event-local law, not just presence
        # or a final priority projection. This never constructs a runtime state.
        expected_stop = before.core.stop
        if allowed:
            if event.kind == "claim" and event.stop is not None:
                expected_stop = event.stop
            elif event.kind == "worker_failed":
                expected_stop = "worker"
            elif event.kind == "sink_returned" and event.error is not None:
                expected_stop = "sink"
            elif event.kind == "complete":
                outcome = event.summary.outcome
                if outcome == "cancelled":
                    expected_stop = "cancelled"
                elif outcome == "deadline" and before.core.stop != "cancelled":
                    expected_stop = "deadline"
                elif outcome == "failure" and self.core.stop_on_failure and not before.ledger.stopped():
                    expected_stop = "failure"
        if allowed and event.kind == "claim" and event.stop is None:
            expected = (Effect("run_job", self.core.first_job + len(before.ledger.claims)),)
        elif allowed and event.kind == "receive":
            expected = (Effect("discard" if before.ledger.first_sink_error is not None else "deliver", event.job_id),)
        return tuple(_check(name, value) for name, value in (
            ("campaign.admission", accepted == allowed),
            ("campaign.effects", transition.outputs == expected),
            ("campaign.stop_reason", transition.state.core.stop == expected_stop),
            ("campaign.rejection_is_inert", accepted or transition.state == before and not transition.outputs),
            ("campaign.accounting_loss_is_sticky", before.core.report.accounting_complete
                or not transition.state.core.report.accounting_complete),
            ("campaign.finalization_is_terminal", not before.ledger.finalized or transition.state == before),
        ))

    def alphabet(self):
        events = [Event("claim"), Event("claim", stop="cancelled"), Event("claim", stop="deadline"),
                  Event("worker_failed", error="worker", lost_work=False),
                  Event("worker_failed", error="worker", lost_work=True), Event("finish")]
        ids = range(self.core.first_job, self.core.first_job + self.core.jobs)
        # Pick an unallocated ID even when the configured range ends at u64::MAX.
        unknown = 0 if self.core.first_job else self.core.jobs
        for job_id in (*ids, unknown):
            events.extend(complete(job_id, NATIVE[outcome]) for outcome in OUTCOMES)
            events.extend((Event("receive", job_id=job_id), sink(job_id), sink(job_id, "sink"),
                           complete(job_id, "completed", failure=True)))
        return tuple(events)

    def inputs(self, state):
        return () if state.ledger.finalized else self.alphabet()

    def generate(self, state, rng):
        alphabet = self.alphabet()
        choices = tuple(event for event in alphabet if admissible(state.ledger, event, self.core.jobs))
        if not choices or rng.index(4) == 0:
            choices = alphabet
        return choices[rng.index(len(choices))]

    def is_enabled(self, state, event):
        # Hostile protocol events are legal model inputs that can reject inertly.
        return True

    def simpler_inputs(self, event):
        return ()

    def encode_input(self, event):
        return self.codec.encode_input(normalize_event(event))

    def decode_input(self, data):
        return native_event(self.codec.decode_input(data))

    def encode_output(self, effect):
        return self.codec.encode_output(normalize_effect(effect))

    def encode_state(self, state):
        # Audited-state envelope is fixture-local; core bytes alone match Rust.
        core = self.codec.encode_state(normalize_state(state.core))
        return b"N06A\x01" + len(core).to_bytes(8, "little") + core + _json(_ledger_value(state.ledger))

    def decode_state(self, data):
        source = _Reader(data)
        if source.take(5) != b"N06A\x01":
            raise _invalid()
        core = native_state(self.codec.decode_state(source.take(int(source.number()))))
        try:
            fields = json.loads(source.take(len(data) - source.offset).decode("ascii"))
            for name in ("claims", "receipts", "attempts", "returns", "successes", "discards"):
                fields[name] = {_number(job_id) for job_id in fields[name]}
            fields["completions"] = {_number(job_id): item for job_id, item in fields["completions"]}
            state = AuditedState(core, Ledger(**fields))
            if self.encode_state(state) != data:
                raise _invalid()
        except (ValueError, TypeError, KeyError, UnicodeError) as error:
            raise _invalid() from error
        # Fault evidence may contain a failed ledger property; preserve it for
        # replay, just as a property-failing application state is serializable.
        return state


def consume_lifecycle_case(row):
    """Shared integration hook: execute a native-campaign-v1 lifecycle row.

    Returns the exact portable row shape. Expected steps are never read; a caller
    compares the returned steps with independent Rust/manual expectations.
    """
    if row["schema"] != "native-campaign-v1" or row["kind"] != "lifecycle":
        raise ValueError("expected native-campaign-v1 lifecycle row")
    config = row["config"]
    model = AuditedLifecycle(first_job=_number(config["first_job"]), jobs=_number(config["jobs"]),
                             stop_on_failure=config["stop_on_failure"])
    state, steps = model.initial_state(), []
    initial = normalize_state(state.core)
    for value in row["events"]:
        event = native_event(value)
        transition = model.step(state, event)
        failed = [check for check in (*model.check_state(transition.state),
            *model.check_transition(state, event, transition)) if check.status == "failed"]
        if failed:
            raise ModelError(f"lifecycle consumer invariant failure: {failed}")
        state = transition.state
        normalized = normalize_state(state.core)
        steps.append(dict(accepted=transition.disposition.kind == "accepted",
                          reason=transition.disposition.reason or None,
                          effects=[normalize_effect(effect) for effect in transition.outputs],
                          state=normalized,
                          input_hex=model.codec.encode_input(value).hex(),
                          state_hex=model.codec.encode_state(normalized).hex(),
                          output_hex=[model.encode_output(effect).hex() for effect in transition.outputs]))
    return dict(schema=row["schema"], case=row["case"], kind="lifecycle",
                config=deepcopy(config), initial=initial, events=deepcopy(row["events"]), steps=steps)
