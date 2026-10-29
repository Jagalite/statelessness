"""Bounded independent campaigns with caller-local sinks and explicit process transfer.

This module makes no serialization guesses for application inputs. Multiworker
campaigns require ProcessWorkers; spawn must be used under the usual __main__ guard.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import inspect
import multiprocessing
import pickle
import threading
import time
from typing import Any, Callable, Generic, TypeVar

from .core import Metadata, ModelError, Rng, U64_MAX, _call, _natural
from .search import FuzzConfig, FuzzReport, RunLimits, fuzz
from .guided import GuidanceConfig, guided_fuzz

I = TypeVar("I")


@dataclass(frozen=True)
class CampaignConfig:
    master_seed: int = 0
    first_job: int = 0
    jobs: int = 16
    workers: int = 1
    result_buffer: int = 1
    fuzz: FuzzConfig = FuzzConfig()
    stop_on_failure: bool = False

    def validate(self):
        # Admission happens at invocation, permitting guidance-before-campaign
        # validation just as Rust does. Python-only type guards are explicit.
        if type(self.jobs) is not int or self.jobs <= 0:
            raise ModelError("campaign jobs must be positive")
        if type(self.workers) is not int or not 1 <= self.workers <= 256:
            raise ModelError("campaign workers must be in 1..=256")
        if type(self.result_buffer) is not int or not 0 <= self.result_buffer <= 1024:
            raise ModelError("campaign result_buffer must be in 0..=1024")
        if not isinstance(self.fuzz, FuzzConfig):
            raise ModelError("campaign fuzz must be FuzzConfig")
        if type(self.fuzz.mutation_percent) is not int or not 0 <= self.fuzz.mutation_percent <= 100:
            raise ModelError("mutation_percent must be in 0..=100")
        if self.jobs > U64_MAX:
            raise ModelError("campaign job count exceeds u64")
        for name in ("master_seed", "first_job"):
            try:
                _natural(getattr(self, name), name)
            except ValueError as e:
                raise ModelError(str(e)) from e
        if self.first_job + self.jobs - 1 > U64_MAX:
            raise ModelError("campaign job ID range overflows u64")
        if type(self.fuzz.cases) is not int or not 0 <= self.fuzz.cases <= U64_MAX:
            raise ModelError("campaign per-job cases exceed u64")
        if type(self.fuzz.max_steps) is not int or not 0 <= self.fuzz.max_steps <= U64_MAX:
            raise ModelError("campaign per-job steps exceed u64")
        if self.jobs * self.fuzz.cases > U64_MAX:
            raise ModelError("campaign aggregate cases overflow u64")
        if type(self.fuzz.max_transitions) is not int or not 0 <= self.fuzz.max_transitions <= U64_MAX:
            raise ModelError("campaign per-job transitions exceed u64")
        if self.jobs * min(self.fuzz.cases * self.fuzz.max_steps, self.fuzz.max_transitions) > U64_MAX:
            raise ModelError("campaign aggregate transitions overflow u64")
        if type(self.stop_on_failure) is not bool:
            raise ModelError("campaign stop_on_failure must be bool")


def job_seed(master_seed: int, job_id: int) -> int:
    _natural(master_seed, "master_seed")
    _natural(job_id, "job_id")
    return Rng((master_seed + job_id) & U64_MAX).next_u64()


@dataclass(frozen=True)
class JobOutcome(Generic[I]):
    kind: str
    report: FuzzReport[I] | None = None
    error: str | None = None


@dataclass(frozen=True)
class JobReport(Generic[I]):
    job_id: int
    seed: int
    config: FuzzConfig
    metadata: Metadata | None
    outcome: JobOutcome[I]


@dataclass
class CampaignReport:
    requested: int
    started: int = 0
    finished: int = 0
    not_started: int = 0
    delivered: int = 0
    failures: int = 0
    errors: int = 0
    reported_transitions: int = 0
    reported_cases: int = 0
    reported_skipped_checks: int = 0
    accounting_complete: bool = True
    termination: str = "jobs_completed"
    error: str | None = None


class CampaignCancellation:
    """Spawn-safe shared cooperative cancellation. Set is sticky; no reset API."""
    def __init__(self):
        self._event = multiprocessing.get_context("spawn").Event()

    def cancel(self):
        self._event.set()

    def __call__(self):
        return self._event.is_set()


@dataclass(frozen=True)
class InputTransfer:
    """Application-owned bytes mapping used only for report inputs across workers."""
    encode: Callable[[Any], bytes]
    decode: Callable[[bytes], Any]


def _pickle_encode(value):
    return pickle.dumps(value, protocol=5)


def _pickle_decode(value):
    return pickle.loads(value)


class PickleInputTransfer(InputTransfer):
    """Explicit trusted-local pickle transfer; never use for untrusted artifacts."""
    def __init__(self):
        super().__init__(_pickle_encode, _pickle_decode)


@dataclass(frozen=True)
class ProcessWorkers:
    """Real CPU workers using multiprocessing spawn, never fork or GIL threads.

    Factories/hooks obey standard spawn pickling. Inputs need the explicit
    transfer; model/state/output/feedback feature values remain local.
    """
    input_transfer: InputTransfer

    def __post_init__(self):
        if not isinstance(self.input_transfer, InputTransfer):
            raise ValueError("input_transfer must be InputTransfer")
        if not callable(self.input_transfer.encode) or not callable(self.input_transfer.decode):
            raise ValueError("input transfer hooks must be callable")


def _message(error):
    # Avoid invoking arbitrary __str__ while containing a failed callback.
    try:
        args = BaseException.args.__get__(error)
        if type(args) is tuple and args and type(args[0]) is str:
            return args[0]
    except BaseException:
        pass
    # Even type(error).__name__ can execute a hostile metaclass accessor.
    return "non-string exception"


def _sync(value, stage):
    if inspect.isawaitable(value) or inspect.isgenerator(value) or inspect.isasyncgen(value):
        if inspect.iscoroutine(value):
            value.close()
        raise ModelError(stage + " must be synchronous")
    return value


class _CampaignControl:
    def __init__(self, limits, *, deadline=None):
        self.cancellation = limits.cancellation
        self.deadline = (deadline if deadline is not None else
                         None if limits.max_duration is None else
                         time.monotonic() + limits.max_duration)

    def remaining(self):
        return RunLimits(None if self.deadline is None else max(0.0, self.deadline - time.monotonic()),
                         self.cancellation)

    def stop(self):
        # Match the public native cancellation error policy and cancel-first order.
        if self.cancellation is not None:
            cancelled = _call("cancellation", self.cancellation)
            if type(cancelled) is not bool:
                raise ModelError("cancellation must return bool")
            if cancelled:
                return "cancelled"
        if self.deadline is not None and time.monotonic() >= self.deadline:
            return "deadline"
        return None


class _CleanupPanic(BaseException):
    """Cleanup unwinds are job panics, including a hook raising ModelError."""


def _cleanup(value):
    """Optional deterministic counterpart of job-local Rust Drop.

    Python __del__ is intentionally not repurposed: its exceptions are unraisable.
    """
    try:
        hook = getattr(value, "campaign_close", None)
        if hook is not None:
            result = _sync(hook(), "campaign_close")
            if result is not None:
                raise ModelError("campaign_close must return None")
    except BaseException as error:
        raise _CleanupPanic(_message(error)) from error


def _execute_job(config, control, factory, job_id, guidance, feedback_factory):
    seed = job_seed(config.master_seed, job_id)
    fuzz_config = replace(config.fuzz, seed=seed)
    metadata = None
    model = None
    try:
        try:
            model = _sync(factory(job_id), "campaign factory")
        except ModelError as error:
            return JobReport(job_id, seed, fuzz_config, None,
                             JobOutcome("factory_error", error=_message(error)))
        try:
            value = _sync(model.metadata(), "model metadata")
            if not isinstance(value, Metadata):
                raise ModelError("model metadata must return Metadata")
            metadata = Metadata(value.name, value.model_version, value.properties_version,
                                value.codec_version, value.build)
            try:
                if guidance is None:
                    report = fuzz(model, fuzz_config, limits=control.remaining())
                else:
                    runner_control = _CampaignControl(control.remaining())
                    feedback = _sync(feedback_factory(job_id, model), "feedback factory")
                    try:
                        report = guided_fuzz(model, feedback, fuzz_config, guidance,
                                             limits=runner_control.remaining())
                    finally:
                        _cleanup(feedback)
                outcome = JobOutcome("completed", report)
            except ModelError as error:
                outcome = JobOutcome("model_error", error=_message(error))
        finally:
            _cleanup(model)
    except BaseException as error:
        outcome = JobOutcome("panicked", error=_message(error))
    return JobReport(job_id, seed, fuzz_config, metadata, outcome)


class _Coordinator:
    def __init__(self, config):
        from ._campaign_lifecycle import Lifecycle
        self.core = Lifecycle(config.first_job, config.jobs, config.stop_on_failure)
        self.state = self.core.initial_state()
        self.lock = threading.Lock()

    def apply(self, event):
        with self.lock:
            transition = self.core.step_owned(self.state, event)
            if transition.disposition.kind != "accepted" and event.kind != "claim":
                raise AssertionError("invalid campaign lifecycle event: " + repr(event))
            self.state = transition.state
            return transition.outputs

    def claim(self, control):
        from ._campaign_lifecycle import Event
        outputs = self.apply(Event("claim", stop=control.stop()))
        return outputs[0].job_id if outputs else None


def _deliver(job, coordinator, sink, transfer=None):
    from ._campaign_lifecycle import Event
    job_id = job.job_id
    effect, = coordinator.apply(Event("receive", job_id=job_id))
    if effect.kind == "discard":
        return False
    error = None
    try:
        if transfer is not None:
            try:
                job = _map_report_inputs(job, lambda b: _sync(transfer.decode(b),
                                                              "input transfer decode"))
            except BaseException as caught:
                raise ModelError("campaign input transfer decode failed: " + _message(caught)) from caught
        result = _sync(sink(job), "campaign sink")
        if result is not None:
            raise ModelError("campaign sink must return None")
    except ModelError as caught:
        error = _message(caught)
    except BaseException as caught:
        error = "campaign callback panicked: " + _message(caught)
    coordinator.apply(Event("sink_returned", job_id=job_id, error=error))
    return error is None


def _map_report_inputs(job, convert):
    outcome = job.outcome
    if outcome.kind != "completed":
        return job
    report = outcome.report
    failure = report.failure
    if failure is not None:
        failure = replace(failure, inputs=tuple(convert(i) for i in failure.inputs))
    guidance = report.guidance
    if guidance is not None:
        guidance = replace(guidance, corpus=tuple(replace(entry, inputs=tuple(convert(i)
                           for i in entry.inputs)) for entry in guidance.corpus))
    return replace(job, outcome=replace(outcome, report=replace(report, failure=failure,
                                                              guidance=guidance)))


def _process_main(connection, config, deadline, cancellation, factory, guidance,
                  feedback_factory, encode_input):
    from ._campaign_lifecycle import Summary
    control = _CampaignControl(RunLimits(cancellation=cancellation), deadline=deadline)
    try:
        while True:
            connection.send(("claim",))
            job_id = connection.recv()
            if job_id is None:
                break
            job = _execute_job(config, control, factory, job_id, guidance, feedback_factory)
            # Complete is a separate acknowledged RPC BEFORE any transfer/send.
            connection.send(("complete", job_id, Summary.from_job(job)))
            connection.recv()
            def encode(value):
                encoded = _sync(encode_input(value), "input transfer encode")
                if type(encoded) is not bytes:
                    raise ModelError("input transfer encode must return bytes")
                return encoded
            wire = _map_report_inputs(job, encode)
            connection.send(("report", wire))
            connection.recv()  # bounded channel admission (rendezvous: receipt)
        connection.send(("done",))
    except BaseException as error:
        try:
            connection.send(("failed", "campaign worker failed: " + _message(error)))
        except BaseException:
            pass
    finally:
        connection.close()


class _Channel:
    """Bounded synchronous channel, including true zero-capacity rendezvous.

    Bridges own at most one report each. Application hooks never run under this
    condition or the independent lifecycle lock. No multiprocessing.Queue feeder.
    """
    def __init__(self, capacity):
        self.capacity = capacity
        self.condition = threading.Condition()
        self.queue = deque()
        self.active = 0
        self.failures = {}

    def put(self, value):
        with self.condition:
            while len(self.queue) >= max(1, self.capacity):
                self.condition.wait()
            ticket = [value, False]
            self.queue.append(ticket)
            self.condition.notify_all()
            if self.capacity == 0:
                while not ticket[1]:
                    self.condition.wait()

    def receive(self):
        with self.condition:
            while not self.queue and self.active:
                self.condition.wait()
            if not self.queue:
                return None
            ticket = self.queue.popleft()
            ticket[1] = True
            self.condition.notify_all()
            return ticket[0]


def _bridge(connection, coordinator, control, channel, slot):
    from ._campaign_lifecycle import Event
    failure = None
    try:
        while True:
            message = connection.recv()
            if message[0] == "claim":
                connection.send(coordinator.claim(control))
            elif message[0] == "complete":
                coordinator.apply(Event("complete", job_id=message[1], summary=message[2]))
                connection.send(None)
            elif message[0] == "report":
                channel.put(message[1])
                connection.send(None)
            elif message[0] == "done":
                break
            elif message[0] == "failed":
                failure = message[1]
                break
            else:
                raise RuntimeError("unknown campaign worker message")
    except BaseException as error:
        failure = "campaign worker connection failed: " + _message(error)
    finally:
        connection.close()
        with channel.condition:
            if failure is not None:
                channel.failures[slot] = failure
            channel.active -= 1
            channel.condition.notify_all()


def _run_processes(config, control, factory, sink, guidance, feedback_factory,
                   policy, coordinator):
    from ._campaign_lifecycle import Event
    context = multiprocessing.get_context("spawn")
    channel = _Channel(config.result_buffer)
    workers, bridges = [], []
    for _ in range(min(config.jobs, config.workers)):
        slot = len(workers)
        parent = child = process = None
        active = False
        try:
            parent, child = context.Pipe()
            process = context.Process(target=_process_main, args=(child, config, control.deadline,
                control.cancellation, factory, guidance, feedback_factory, policy.input_transfer.encode))
            process.start()
            workers.append((slot, process))
            child.close()
            with channel.condition:
                channel.active += 1
                active = True
            bridge = threading.Thread(target=_bridge, args=(parent, coordinator, control, channel, slot),
                                      name="statelessness-campaign-ipc")
            bridge.start()
        except BaseException as error:
            if parent is not None:
                parent.close()
            if child is not None:
                child.close()
            if active:
                with channel.condition:
                    channel.active -= 1
            # An owned process started without a bridge has received no job;
            # closing the pipe releases its initial request. It is joined below.
            coordinator.apply(Event("worker_failed", error="unable to start campaign worker: " +
                                    _message(error), lost_work=False))
            break
        bridges.append(bridge)
    while True:
        wire = channel.receive()
        if wire is None:
            break
        # Receive accounts the known summary before any application decode. A
        # discarded wire report requires no decoder at all. Healthy decode
        # failure is a delivery callback error, preserving known work counters.
        _deliver(wire, coordinator, sink, policy.input_transfer)
    # Observation of missing/crashed workers occurs after channel drain. Healthy
    # workers can keep claiming until that point, exactly as the Rust join path.
    for bridge in bridges:
        bridge.join()
    for slot, process in workers:
        process.join()
        message = channel.failures.get(slot)
        if process.exitcode:
            message = "campaign worker exited with code " + str(process.exitcode)
        if message is not None:
            # One observed failure per owned worker, in launch/join order. Pipe
            # EOF and nonzero process exit describe the same missing outcome.
            coordinator.apply(Event("worker_failed", error=message, lost_work=True))
        process.close()


def _run(config, limits, factory, sink, guidance, feedback_factory, process):
    from ._campaign_lifecycle import Event, Summary
    if not isinstance(config, CampaignConfig):
        raise ModelError("config must be CampaignConfig")
    config.validate()
    # Capture immutable base scalars before any callbacks or worker startup.
    f = config.fuzz
    config = CampaignConfig(config.master_seed, config.first_job, config.jobs, config.workers,
                            config.result_buffer, FuzzConfig(f.seed, f.cases, f.max_steps,
                            f.max_transitions, f.mutation_percent), config.stop_on_failure)
    if not isinstance(limits, RunLimits):
        raise ModelError("limits must be RunLimits")
    limits = RunLimits(limits.max_duration, limits.cancellation)
    if config.workers > 1:
        if not isinstance(process, ProcessWorkers):
            raise ModelError("multiple campaign workers require an explicit ProcessWorkers policy")
        if limits.cancellation is not None and not isinstance(limits.cancellation, CampaignCancellation):
            raise ModelError("process workers require CampaignCancellation for shared cancellation")
        process = ProcessWorkers(InputTransfer(process.input_transfer.encode, process.input_transfer.decode))
    control, coordinator = _CampaignControl(limits), _Coordinator(config)
    stop = control.stop()
    if stop is not None:
        coordinator.apply(Event("claim", stop=stop))
    elif config.workers == 1:
        while True:
            job_id = coordinator.claim(control)
            if job_id is None:
                break
            job = _execute_job(config, control, factory, job_id, guidance, feedback_factory)
            coordinator.apply(Event("complete", job_id=job_id, summary=Summary.from_job(job)))
            if not _deliver(job, coordinator, sink):
                break
    else:
        _run_processes(config, control, factory, sink, guidance, feedback_factory, process, coordinator)
    coordinator.apply(Event("finish"))
    return coordinator.state.report


def run_campaign(config: CampaignConfig, limits: RunLimits, factory, sink, *,
                 process: ProcessWorkers | None = None) -> CampaignReport:
    """Stream ordinary independent jobs in host receipt order, draining on sink error."""
    return _run(config, limits, factory, sink, None, None, process)


def run_guided_campaign(config: CampaignConfig, limits: RunLimits,
                        guidance: GuidanceConfig, factory, feedback_factory, sink, *,
                        process: ProcessWorkers | None = None) -> CampaignReport:
    """Fresh model, feedback and corpus per job using the public guided_fuzz API."""
    if not isinstance(guidance, GuidanceConfig):
        raise ModelError("guidance must be GuidanceConfig")
    guidance.validate()
    guidance = replace(guidance)
    return _run(config, limits, factory, sink, guidance, feedback_factory, process)
