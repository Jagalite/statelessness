"""Private campaign decision law. Application reports live only in the executor."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from .core import Disposition, ModelError, Transition, U64_MAX
from .campaign import CampaignReport


@dataclass(frozen=True)
class Summary:
    outcome: str
    failure: bool = False
    transitions: int = 0
    cases: int = 0
    skipped_checks: int = 0

    def is_valid(self):
        if self.outcome not in ("completed", "failure", "cancelled", "deadline",
                                "factory_error", "model_error", "panicked"):
            return False
        if type(self.failure) is not bool or any(type(n) is not int or not 0 <= n <= U64_MAX
                for n in (self.transitions, self.cases, self.skipped_checks)):
            return False
        if self.outcome in ("factory_error", "model_error", "panicked"):
            return not self.failure and not (self.transitions or self.cases or self.skipped_checks)
        return self.failure == (self.outcome == "failure")

    @classmethod
    def from_job(cls, job):
        outcome = job.outcome
        if outcome.kind != "completed":
            return cls(outcome.kind)
        report = outcome.report
        kind = {"failure_found": "failure", "cancelled": "cancelled",
                "deadline": "deadline"}.get(report.termination, "completed")
        return cls(kind, report.failure is not None, report.transitions,
                   report.cases, report.skipped_checks)


@dataclass(frozen=True)
class Event:
    kind: str
    job_id: int | None = None
    summary: Summary | None = None
    stop: str | None = None
    error: str | None = None
    lost_work: bool = False


@dataclass(frozen=True)
class Effect:
    kind: str
    job_id: int


@dataclass
class State:
    report: CampaignReport
    stop: str | None = None
    pending: dict[int, Summary | None] = field(default_factory=dict)
    delivering: int | None = None
    lost_worker: bool = False
    finalized: bool = False


class Lifecycle:
    def __init__(self, first_job, jobs, stop_on_failure):
        self.first_job, self.jobs, self.stop_on_failure = first_job, jobs, stop_on_failure

    def initial_state(self):
        if not 0 <= self.first_job <= U64_MAX or not 1 <= self.jobs <= U64_MAX or self.first_job + self.jobs - 1 > U64_MAX:
            raise ModelError("invalid campaign lifecycle job range")
        return State(CampaignReport(self.jobs, not_started=self.jobs))

    def step(self, before, event):
        return self.step_owned(deepcopy(before), event)

    def step_owned(self, state, event):
        def reject(reason):
            return Transition(state, (), Disposition("rejected", reason))
        if state.finalized:
            return reject("campaign is finalized")
        r, outputs, kind, job_id = state.report, (), event.kind, event.job_id
        if kind == "claim":
            if state.stop is not None or r.started == self.jobs:
                return reject("campaign cannot claim more jobs")
            if event.stop is not None:
                if event.stop not in ("cancelled", "deadline"):
                    return reject("invalid external stop")
                state.stop = event.stop
            else:
                job_id = self.first_job + r.started
                state.pending[job_id] = None
                r.started += 1
                r.not_started -= 1
                outputs = (Effect("run_job", job_id),)
        elif kind == "complete":
            if not isinstance(event.summary, Summary) or not event.summary.is_valid():
                return reject("inconsistent job summary")
            if job_id not in state.pending or state.pending[job_id] is not None:
                return reject("job is not running")
            state.pending[job_id] = event.summary
            if event.summary.outcome == "cancelled":
                state.stop = "cancelled"
            elif event.summary.outcome == "deadline" and state.stop != "cancelled":
                state.stop = "deadline"
            elif event.summary.outcome == "failure" and self.stop_on_failure and state.stop is None:
                state.stop = "failure"
        elif kind == "receive":
            summary = state.pending.get(job_id)
            if summary is None:
                return reject("job has no completed report")
            if state.delivering is not None:
                return reject("sink is already delivering a report")
            del state.pending[job_id]
            r.finished += 1
            r.failures += int(summary.failure)
            if summary.outcome in ("factory_error", "model_error", "panicked"):
                r.errors += 1
                if summary.outcome != "factory_error":
                    r.accounting_complete = False
            for name, count in (("reported_transitions", summary.transitions),
                                ("reported_cases", summary.cases),
                                ("reported_skipped_checks", summary.skipped_checks)):
                total = getattr(r, name) + count
                if total > U64_MAX:
                    r.accounting_complete = False
                setattr(r, name, min(total, U64_MAX))
            if r.termination == "callback_error":
                outputs = (Effect("discard", job_id),)
            else:
                state.delivering = job_id
                outputs = (Effect("deliver", job_id),)
        elif kind == "sink_returned":
            if state.delivering is None or state.delivering != job_id:
                return reject("job is not awaiting a sink result")
            state.delivering = None
            if event.error is not None:
                r.termination, r.error, state.stop = "callback_error", event.error, "sink"
            else:
                r.delivered += 1
        elif kind == "worker_failed":
            if r.termination != "callback_error":
                r.termination, r.error = "worker_error", event.error
            state.stop = "worker"
            state.lost_worker |= event.lost_work
            r.accounting_complete &= not event.lost_work
        elif kind == "finish":
            if state.delivering is not None or (state.pending and not state.lost_worker) or (state.stop is None and r.started != self.jobs):
                return reject("campaign has unfinished work")
            r.accounting_complete &= r.started == r.finished
            if r.termination not in ("callback_error", "worker_error"):
                r.termination = {"cancelled": "cancelled", "deadline": "deadline",
                                 "failure": "failure_found"}.get(state.stop, "jobs_completed")
            state.finalized = True
        else:
            return reject("unknown campaign event")
        return Transition(state, outputs)
