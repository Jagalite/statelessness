"""N06 real callbacks, process scheduling and failure boundaries.

Run via Kyoto only. Timing waits are bounded watchdogs for barriers, never claims
of callback preemption. Deterministic reference goldens are a separate consumer.
"""
from dataclasses import dataclass, replace
import multiprocessing
import os
import struct
import threading
import time
import unittest
from unittest.mock import patch

from statelessness import (CampaignConfig, CampaignCancellation, Check, FuzzConfig,
    GuidanceConfig, InputTransfer, Metadata, ModelError, PickleInputTransfer,
    ProcessWorkers, Rng, RunLimits, Transition, exact_state, fuzz, guided_fuzz,
    job_seed, run_campaign, run_guided_campaign)
from statelessness import campaign

MAX = (1 << 64) - 1
TINY = FuzzConfig(cases=1, max_steps=2, mutation_percent=0)
PROCESS = ProcessWorkers(PickleInputTransfer())


class Counter:
    def __init__(self, job=0, *, fail=False, log=None):
        self.job, self.fail, self.log = job, fail, log
        # Models do not cross the process boundary, even if not pickleable.
        self.lock = threading.Lock()
        self.steps = 0

    def note(self, value):
        if self.log is not None:
            self.log.append(value)

    def metadata(self):
        self.note("metadata")
        return Metadata("counter:" + str(self.job), build="n06-test-v1")

    def initial_state(self):
        self.note("initial")
        return 0

    def generate(self, state, rng):
        self.note("generate")
        return 1 + rng.index(3)

    def is_enabled(self, state, input):
        return True

    def step(self, state, input):
        self.steps += 1
        self.note("step")
        return Transition(state + input, (Opaque(state + input),))

    def check_state(self, state):
        return (Check("skip", "skipped"), Check("safe", "failed" if self.fail and state else "passed"))

    def campaign_close(self):
        self.note("model-close")


def make_counter(job):
    return Counter(job)


def make_feedback(job, model):
    return exact_state()


@dataclass
class ParallelFactory:
    barrier: object = None
    started: object = None
    threshold: int = 0
    saturated: object = None
    forbidden: object = None
    release_zero: object = None
    failing: bool = False
    exit_zero: bool = False

    def __call__(self, job):
        if self.started is not None:
            with self.started.get_lock():
                self.started[job] = os.getpid()
                count = sum(bool(p) for p in self.started[:])
            if self.saturated is not None and count == self.threshold:
                self.saturated.set()
            if self.forbidden is not None and count > self.threshold:
                self.forbidden.set()
        if self.barrier is not None and job < 2:
            self.barrier.wait(10)
        if self.release_zero is not None and job == 0:
            if not self.release_zero.wait(10):
                raise RuntimeError("zero release timed out")
        if self.exit_zero and job == 0:
            os._exit(29)
        return Counter(job, fail=self.failing)


@dataclass
class Opaque:
    value: int

    def __reduce__(self):
        raise TypeError("input requires application transfer")

    def __deepcopy__(self, memo):
        return Opaque(self.value)


class OpaqueModel(Counter):
    def generate(self, state, rng):
        return Opaque(1 + rng.index(3))

    def step(self, state, input):
        return super().step(state, input.value)


def opaque_factory(job):
    return OpaqueModel(job)


def failing_opaque_factory(job):
    return OpaqueModel(job, fail=True)


def encode_opaque(value):
    return struct.pack("<Q", value.value)


def decode_opaque(value):
    return Opaque(struct.unpack("<Q", value)[0])


def bad_encode(value):
    raise RuntimeError("encode exploded")


def bad_decode(value):
    raise RuntimeError("decode exploded")


@dataclass
class AfterCompleteEncode:
    completed: object

    def __call__(self, value):
        if not self.completed.is_set():
            raise RuntimeError("input transfer ran before lifecycle Complete")
        return campaign._pickle_encode(value)


class HostileMeta(type):
    def __getattribute__(cls, name):
        if name == "__name__":
            raise RuntimeError("hostile exception type name")
        return super().__getattribute__(name)


class HostileError(Exception, metaclass=HostileMeta):
    def __str__(self):
        raise RuntimeError("hostile exception string")


@dataclass
class ReverseFailureFactory:
    barrier: object
    last_exited: object
    initial_success: bool = False
    failure_ready: object = None
    allow_exit: object = None

    def __call__(self, job):
        if self.initial_success and job == 0:
            return Counter(job)
        slot = int(multiprocessing.current_process().name.rsplit("-", 1)[1])
        self.barrier.wait(10)
        if self.failure_ready is not None:
            self.failure_ready.set()
        if self.allow_exit is not None and not self.allow_exit.wait(10):
            raise RuntimeError("sink did not release failing workers")
        if slot == 0:
            if not self.last_exited.wait(10):
                raise RuntimeError("reverse failure synchronization timed out")
        os._exit(41 + slot)


class CampaignSerialTests(unittest.TestCase):
    def test_seed_vectors_and_range_wrap(self):
        self.assertEqual(job_seed(0, 0), 16294208416658607535)
        self.assertEqual(job_seed(0, 1), 10451216379200822465)
        self.assertEqual(job_seed(0, 7), 7191089600892374487)
        self.assertEqual(job_seed(MAX, 1), job_seed(0, 0))
        self.assertEqual(job_seed(MAX, MAX), job_seed(MAX - 1, 0))
        self.assertEqual(job_seed(0, MAX), 16490336266968443936)
        reports = []
        result = run_campaign(CampaignConfig(first_job=MAX, jobs=1, fuzz=TINY), RunLimits(), make_counter, reports.append)
        self.assertEqual(result.finished, 1)
        self.assertEqual(reports[0].job_id, MAX)
        for a, b in ((-1, 0), (0, MAX + 1), (True, 0)):
            with self.assertRaises(ValueError):
                job_seed(a, b)

    def test_validation_order_and_wide_cap_before_multiply(self):
        calls = []
        def factory(job):
            calls.append(job)
            return Counter(job)
        for config, message in (
            (CampaignConfig(jobs=0, workers=0), "jobs must"),
            (CampaignConfig(workers=0, result_buffer=1025), "workers must"),
            (CampaignConfig(result_buffer=1025), "result_buffer"),
            (CampaignConfig(first_job=MAX, jobs=2), "ID range"),
            (CampaignConfig(jobs=2, fuzz=FuzzConfig(cases=MAX, max_steps=0)), "aggregate cases"),
            (CampaignConfig(jobs=2, fuzz=FuzzConfig(cases=1, max_steps=MAX, max_transitions=MAX)), "aggregate transitions"),
        ):
            with self.assertRaisesRegex(ModelError, message):
                run_campaign(config, RunLimits(cancellation=lambda: True), factory, calls.append)
        # The product exceeds u64 before applying the actual per-job cap.
        CampaignConfig(jobs=1, fuzz=FuzzConfig(cases=MAX, max_steps=MAX, max_transitions=2)).validate()
        invalid_guidance = GuidanceConfig()
        object.__setattr__(invalid_guidance, "max_features", 0)
        with self.assertRaisesRegex(ValueError, "max_features"):
            run_guided_campaign(CampaignConfig(jobs=0), RunLimits(), invalid_guidance,
                                factory, make_feedback, calls.append)
        self.assertEqual(calls, [])

    def test_ordinary_matches_per_job_public_reference_and_caller(self):
        reports, threads = [], []
        config = CampaignConfig(master_seed=MAX, first_job=6, jobs=4,
                                fuzz=FuzzConfig(cases=4, max_steps=5, mutation_percent=75))
        caller = threading.get_ident()
        def factory(job):
            threads.append(threading.get_ident())
            return Counter(job)
        result = run_campaign(config, RunLimits(), factory, reports.append)
        self.assertEqual(threads, [caller] * 4)
        for job in reports:
            expected = fuzz(Counter(job.job_id), replace(config.fuzz, seed=job.seed))
            self.assertEqual(job.outcome.report, expected)
            self.assertEqual(job.seed, job_seed(MAX, job.job_id))
        self.assertEqual((result.started, result.finished, result.delivered, result.errors), (4, 4, 4, 0))
        self.assertEqual((result.reported_cases, result.reported_transitions,
                          result.reported_skipped_checks), (16, 80, 96))
        self.assertTrue(result.accounting_complete)

    def test_guided_exact_reference_fresh_feedback_and_teardown_order(self):
        log, reports = [], []
        class Feedback:
            def __init__(self): self.inner = exact_state()
            def metadata(self):
                log.append("feedback-metadata")
                return self.inner.metadata()
            def initial_features(self, state, out): return self.inner.initial_features(state, out)
            def transition_features(self, before, input, after, out):
                return self.inner.transition_features(before, input, after, out)
            def campaign_close(self): log.append("feedback-close")
        def factory(job):
            log.append("factory")
            return Counter(job, log=log)
        def feedback(job, model):
            log.append("feedback-factory")
            return Feedback()
        def sink(job):
            log.append("sink")
            reports.append(job)
        config = CampaignConfig(jobs=2, fuzz=TINY)
        result = run_guided_campaign(config, RunLimits(), GuidanceConfig(), factory, feedback, sink)
        self.assertEqual(result.delivered, 2)
        for job in reports:
            self.assertEqual(job.outcome.report, guided_fuzz(Counter(job.job_id), exact_state(),
                replace(TINY, seed=job.seed)))
        self.assertEqual(log[:4], ["factory", "metadata", "feedback-factory", "feedback-metadata"])
        self.assertEqual([x for x in log if x.endswith("close") or x == "sink"],
                         ["feedback-close", "model-close", "sink"] * 2)

    def test_feedback_metadata_unwind_is_a_job_panic(self):
        # Rust Feedback::metadata is infallible: an unwind here is a panic,
        # even when the Python callback raises its model-error type.
        for error_type in (RuntimeError, ModelError):
            with self.subTest(error_type=error_type):
                log, reports = [], []
                class Feedback:
                    def metadata(self):
                        log.append("feedback-metadata")
                        raise error_type("metadata exploded")
                    def campaign_close(self): log.append("feedback-close")
                def factory(job): return Counter(job, log=log)
                def feedback(job, model): return Feedback()
                run_guided_campaign(CampaignConfig(jobs=1, fuzz=TINY), RunLimits(),
                                    GuidanceConfig(), factory, feedback, reports.append)
                self.assertEqual(reports[0].outcome.kind, "panicked")
                self.assertEqual(reports[0].outcome.error, "metadata exploded")
                self.assertEqual(log, ["metadata", "feedback-metadata", "feedback-close", "model-close"])

        # Attribute lookup is part of invoking the callback in Python and may
        # itself unwind, including via a property that raises ModelError.
        class LookupFailure:
            @property
            def metadata(self): raise ModelError("metadata lookup exploded")
        reports = []
        run_guided_campaign(CampaignConfig(jobs=1, fuzz=TINY), RunLimits(),
                            GuidanceConfig(), Counter, lambda job, model: LookupFailure(), reports.append)
        self.assertEqual(reports[0].outcome.kind, "panicked")
        self.assertEqual(reports[0].outcome.error, "metadata lookup exploded")

    def test_preflight_stop_has_no_callbacks_and_factory_cancellation_keeps_metadata(self):
        calls = []
        for limits, reason in ((RunLimits(cancellation=lambda: True), "cancelled"),
                               (RunLimits(max_duration=0), "deadline")):
            result = run_campaign(CampaignConfig(jobs=3), limits, calls.append, calls.append)
            self.assertEqual((result.termination, result.started, result.not_started), (reason, 0, 3))
        self.assertEqual(calls, [])
        token = CampaignCancellation()
        def factory(job):
            calls.append("factory")
            token.cancel()
            return Counter(job, log=calls)
        def feedback(job, model):
            calls.append("feedback-factory")
            return exact_state()
        reports = []
        result = run_guided_campaign(CampaignConfig(jobs=3), RunLimits(cancellation=token),
                                    GuidanceConfig(), factory, feedback, reports.append)
        self.assertEqual(calls, ["factory", "metadata", "feedback-factory", "model-close"])
        self.assertEqual(reports[0].outcome.report.termination, "cancelled")
        self.assertEqual((result.started, result.finished, result.reported_cases), (1, 1, 0))

    def test_factory_vs_feedback_vs_search_errors_and_failure_stop(self):
        reports = []
        def factory(job):
            if job == 0: raise ModelError("factory")
            if job == 1: raise RuntimeError("panic")
            return Counter(job)
        result = run_campaign(CampaignConfig(jobs=3, stop_on_failure=True, fuzz=TINY),
                              RunLimits(), factory, reports.append)
        self.assertEqual([j.outcome.kind for j in reports], ["factory_error", "panicked", "completed"])
        self.assertEqual([j.metadata is None for j in reports], [True, True, False])
        self.assertEqual((result.started, result.errors, result.failures, result.termination),
                         (3, 2, 0, "jobs_completed"))
        self.assertFalse(result.accounting_complete)
        def feedback(job, model): raise ModelError("feedback")
        reports.clear()
        result = run_guided_campaign(CampaignConfig(jobs=2, fuzz=TINY, stop_on_failure=True),
                                    RunLimits(), GuidanceConfig(), make_counter, feedback, reports.append)
        self.assertEqual([j.outcome.kind for j in reports], ["model_error"] * 2)
        self.assertTrue(all(j.metadata is not None for j in reports))
        self.assertFalse(result.accounting_complete)
        result = run_campaign(CampaignConfig(jobs=3, fuzz=TINY, stop_on_failure=True), RunLimits(),
                              lambda job: Counter(job, fail=True), lambda job: None)
        self.assertEqual((result.started, result.failures, result.termination), (1, 1, "failure_found"))

    def test_factory_error_known_zero_and_native_callback_exception_policy(self):
        def factory(job): raise ModelError("factory")
        result = run_campaign(CampaignConfig(jobs=2), RunLimits(), factory, lambda job: None)
        self.assertTrue(result.accounting_complete)
        self.assertEqual((result.errors, result.reported_cases), (2, 0))
        class Broken(Counter):
            def step(self, state, input): raise RuntimeError("step")
        jobs = []
        result = run_campaign(CampaignConfig(jobs=1, fuzz=TINY), RunLimits(), lambda job: Broken(), jobs.append)
        self.assertEqual(jobs[0].outcome.kind, "model_error")
        self.assertFalse(result.accounting_complete)

    def test_cleanup_errors_are_panics_for_model_and_feedback(self):
        for use_feedback in (False, True):
            log, reports = [], []
            class Closing(Counter):
                def campaign_close(self):
                    log.append("model")
                    if not use_feedback: raise ModelError("model cleanup")
            class ClosingFeedback:
                def metadata(self): return exact_state().metadata()
                def transition_features(self, before, input, after, out): pass
                def campaign_close(self):
                    log.append("feedback")
                    raise ModelError("feedback cleanup")
            config = CampaignConfig(jobs=1, fuzz=TINY)
            if use_feedback:
                result = run_guided_campaign(config, RunLimits(), GuidanceConfig(), lambda job: Closing(),
                                            lambda job, model: ClosingFeedback(), reports.append)
            else:
                result = run_campaign(config, RunLimits(), lambda job: Closing(), reports.append)
            self.assertEqual(reports[0].outcome.kind, "panicked")
            self.assertIn("cleanup", reports[0].outcome.error)
            self.assertIsNotNone(reports[0].metadata)
            self.assertFalse(result.accounting_complete)
            self.assertEqual(log, ["feedback", "model"] if use_feedback else ["model"])

    def test_sink_error_panic_and_last_sink_control_is_inert(self):
        for error in (ModelError("first"), RuntimeError("panic")):
            def sink(job): raise error
            result = run_campaign(CampaignConfig(jobs=3, fuzz=TINY), RunLimits(), make_counter, sink)
            self.assertEqual((result.started, result.finished, result.delivered, result.errors), (1, 1, 0, 0))
            self.assertEqual(result.termination, "callback_error")
            self.assertTrue(result.accounting_complete)
        flag = [False]
        result = run_campaign(CampaignConfig(jobs=1, fuzz=TINY), RunLimits(cancellation=lambda: flag[0]),
                              make_counter, lambda job: flag.__setitem__(0, True))
        self.assertEqual(result.termination, "jobs_completed")

    def test_factory_and_feedback_time_count_toward_deadline(self):
        clock, events, jobs = [0.0], [], []
        def factory(job):
            clock[0] = 2.0
            return Counter(job, log=events)
        with patch.object(campaign.time, "monotonic", lambda: clock[0]):
            result = run_guided_campaign(CampaignConfig(jobs=2), RunLimits(max_duration=1), GuidanceConfig(),
                                        factory, lambda job, model: exact_state(), jobs.append)
        self.assertEqual(events, ["metadata", "model-close"])
        self.assertEqual((result.termination, result.started, result.reported_cases), ("deadline", 1, 0))
        clock[0] = 0.0
        def feedback(job, model):
            clock[0] = 2.0
            return exact_state()
        with patch.object(campaign.time, "monotonic", lambda: clock[0]):
            result = run_guided_campaign(CampaignConfig(jobs=2), RunLimits(max_duration=1), GuidanceConfig(),
                                        make_counter, feedback, lambda job: None)
        self.assertEqual((result.termination, result.reported_cases), ("deadline", 0))

    def test_sink_cannot_change_the_protocol_acknowledgement_id(self):
        def sink(job):
            # Frozen dataclass escape must not affect coordinator identity.
            object.__setattr__(job, "job_id", 999)
        result = run_campaign(CampaignConfig(jobs=2, fuzz=TINY), RunLimits(), make_counter, sink)
        self.assertEqual((result.finished, result.delivered), (2, 2))

    def test_hostile_exception_diagnostic_does_not_escape_serial_sink(self):
        def sink(job): raise HostileError()
        result = run_campaign(CampaignConfig(jobs=2, fuzz=TINY), RunLimits(), make_counter, sink)
        self.assertEqual((result.started, result.finished, result.delivered), (1, 1, 0))
        self.assertEqual((result.termination, result.error),
                         ("callback_error", "campaign callback panicked: non-string exception"))


class CampaignProcessTests(unittest.TestCase):
    def setUp(self):
        self.context = multiprocessing.get_context("spawn")
        self.before = {p.pid for p in multiprocessing.active_children()}

    def tearDown(self):
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, self.before)
        self.assertFalse(any(t.name == "statelessness-campaign-ipc" for t in threading.enumerate()))

    def test_configured_many_jobs_one_still_spawns_and_sink_is_caller(self):
        pids = self.context.Array("q", 1)
        reports, threads = [], []
        def sink(job):
            reports.append(job)
            threads.append(threading.get_ident())
        result = run_campaign(CampaignConfig(jobs=1, workers=8, result_buffer=0, fuzz=TINY), RunLimits(),
                              ParallelFactory(started=pids), sink, process=PROCESS)
        self.assertEqual((result.delivered, result.started), (1, 1))
        self.assertNotEqual(pids[0], os.getpid())
        self.assertEqual(threads, [threading.get_ident()])
        self.assertEqual(reports[0].outcome.kind, "completed")

    def test_real_barrier_out_of_order_and_worker_local_factories(self):
        barrier = self.context.Barrier(2)
        release = self.context.Event()
        pids = self.context.Array("q", 2)
        reports = []
        def sink(job):
            reports.append(job)
            release.set()
        result = run_campaign(CampaignConfig(jobs=2, workers=2, result_buffer=0, fuzz=TINY), RunLimits(),
            ParallelFactory(barrier, pids, release_zero=release), sink, process=PROCESS)
        self.assertEqual([j.job_id for j in reports], [1, 0])
        self.assertEqual(len(set(pids[:])), 2)
        self.assertNotIn(os.getpid(), pids[:])
        self.assertEqual(result.delivered, 2)
        for job in reports:
            self.assertEqual(job.outcome.kind, "completed")
            self.assertEqual(job.outcome.report, fuzz(Counter(job.job_id), replace(TINY, seed=job.seed)))

    def test_blocked_sink_outstanding_bound_and_release_before_return(self):
        for capacity in (0, 1):
            with self.subTest(capacity=capacity):
                bound = capacity + 2 + 1
                pids = self.context.Array("q", 12)
                blocked = threading.Event()
                channels = []
                class ObservedChannel(campaign._Channel):
                    def __init__(self, capacity):
                        super().__init__(capacity)
                        channel = self
                        self.offers, self.waiting = {}, set()
                        self.sink_job = None
                        class Condition(threading.Condition):
                            def wait(self, timeout=None):
                                ident = threading.get_ident()
                                if ident not in channel.offers:
                                    return super().wait(timeout)
                                channel.waiting.add(ident)
                                try:
                                    if channel.bound_ready():
                                        blocked.set()
                                    return super().wait(timeout)
                                finally:
                                    channel.waiting.remove(ident)
                        self.condition = Condition()
                        channels.append(self)

                    def bound_ready(self):
                        offered = set(self.offers.values())
                        queued = {ticket[0].job_id for ticket in self.queue}
                        return (self.sink_job is not None and len(self.waiting) == 2
                            and len(self.queue) == max(1, self.capacity)
                            and len(offered) == 2 and (queued <= offered) == (self.capacity == 0)
                            and len(offered | queued | {self.sink_job}) == self.capacity + 3)

                    def put(self, value):
                        ident = threading.get_ident()
                        with self.condition:
                            self.offers[ident] = value.job_id
                        try:
                            return super().put(value)
                        finally:
                            with self.condition:
                                del self.offers[ident]
                calls = []
                def sink(job):
                    self.assertEqual(job.outcome.kind, "completed")
                    calls.append(job.job_id)
                    if len(calls) == 1:
                        channel, = channels
                        with channel.condition:
                            channel.sink_job = job.job_id
                            if channel.bound_ready():
                                blocked.set()
                        self.assertTrue(blocked.wait(10), "workers never reached real bounded-send waits")
                        # Inspect under the real transport lock: both worker
                        # bridges are in production put's admission/receipt wait.
                        with channel.condition:
                            self.assertTrue(channel.bound_ready())
                            self.assertEqual(len(channel.waiting), 2)
                            self.assertEqual(len(channel.queue), max(1, capacity))
                            offered = set(channel.offers.values())
                            queued = {ticket[0].job_id for ticket in channel.queue}
                            self.assertEqual(len(offered), 2)
                            self.assertEqual(queued <= offered, capacity == 0)
                            self.assertEqual(len(offered | queued | {job.job_id}), bound)
                        self.assertEqual(sum(bool(p) for p in pids[:]), bound)
                with patch.object(campaign, "_Channel", ObservedChannel):
                    result = run_campaign(CampaignConfig(jobs=12, workers=2, result_buffer=capacity, fuzz=TINY),
                        RunLimits(), ParallelFactory(started=pids), sink, process=PROCESS)
                self.assertEqual((result.started, result.finished, result.delivered), (12, 12, 12))

    def test_guided_application_input_transfer_without_model_codec(self):
        jobs = []
        result = run_guided_campaign(CampaignConfig(jobs=3, workers=2, result_buffer=0, fuzz=TINY),
            RunLimits(), GuidanceConfig(), opaque_factory, make_feedback, jobs.append,
            process=ProcessWorkers(InputTransfer(encode_opaque, decode_opaque)))
        self.assertEqual(result.delivered, 3)
        for job in jobs:
            self.assertEqual(job.outcome.kind, "completed")
            self.assertTrue(all(isinstance(i, Opaque) for e in job.outcome.report.guidance.corpus for i in e.inputs))
            self.assertEqual(job.outcome.report, guided_fuzz(OpaqueModel(job.job_id), exact_state(),
                                                          replace(TINY, seed=job.seed)))

    def test_completion_is_accepted_before_application_encode_or_send(self):
        completed = self.context.Event()
        apply = campaign._Coordinator.apply
        def observe(coordinator, event):
            result = apply(coordinator, event)
            if event.kind == "complete":
                completed.set()
            return result
        jobs = []
        with patch.object(campaign._Coordinator, "apply", observe):
            result = run_guided_campaign(CampaignConfig(jobs=1, workers=2, result_buffer=0, fuzz=TINY),
                RunLimits(), GuidanceConfig(), make_counter, make_feedback, jobs.append,
                process=ProcessWorkers(InputTransfer(AfterCompleteEncode(completed), campaign._pickle_decode)))
        self.assertEqual(result.delivered, 1)
        self.assertEqual(jobs[0].outcome.kind, "completed")

    def test_failing_application_input_witness_transfers_exactly(self):
        jobs = []
        result = run_campaign(CampaignConfig(jobs=2, workers=2, result_buffer=0, fuzz=TINY), RunLimits(),
            failing_opaque_factory, jobs.append,
            process=ProcessWorkers(InputTransfer(encode_opaque, decode_opaque)))
        self.assertEqual((result.delivered, result.failures, result.errors), (2, 2, 0))
        for job in jobs:
            self.assertEqual(job.outcome.kind, "completed")
            report = job.outcome.report
            self.assertEqual(report.termination, "failure_found")
            self.assertEqual([i.value for i in report.failure.inputs], [1 + Rng(job.seed).index(3)])
            self.assertEqual([(v.phase, v.check.id) for v in report.failure.violations], [("state", "safe")])
            self.assertEqual(report, fuzz(failing_opaque_factory(job.job_id), replace(TINY, seed=job.seed)))

    def test_failure_stop_does_not_cancel_already_claimed_jobs(self):
        pids = self.context.Array("q", 8)
        jobs = []
        result = run_campaign(CampaignConfig(jobs=8, workers=2, result_buffer=0, fuzz=TINY,
                                            stop_on_failure=True), RunLimits(),
            ParallelFactory(barrier=self.context.Barrier(2), started=pids, failing=True), jobs.append,
            process=PROCESS)
        self.assertEqual((result.started, result.finished, result.delivered, result.failures), (2, 2, 2, 2))
        self.assertEqual(result.termination, "failure_found")
        self.assertTrue(all(j.outcome.report.termination == "failure_found" for j in jobs))

    def test_sink_failure_drains_without_decoder_calls(self):
        barrier, release = self.context.Barrier(2), self.context.Event()
        pids = self.context.Array("q", 2)
        decoded = []
        def decode(value):
            self.assertEqual(threading.get_ident(), caller_thread)
            decoded.append(value)
            if len(decoded) > 3: raise RuntimeError("decoder must not run for discarded reports")
            return campaign._pickle_decode(value)
        # Decoder runs only in the parent; its closure need not cross spawn.
        transfer = InputTransfer(campaign._pickle_encode, decode)
        caller_thread = threading.get_ident()
        calls = []
        def sink(job):
            calls.append(job.job_id)
            release.set()
            raise ModelError("first sink error")
        result = run_guided_campaign(CampaignConfig(jobs=2, workers=2, result_buffer=0, fuzz=TINY),
            RunLimits(), GuidanceConfig(), ParallelFactory(barrier, pids, release_zero=release), make_feedback,
            sink, process=ProcessWorkers(transfer))
        self.assertEqual((result.started, result.finished, result.delivered, result.errors), (2, 2, 0, 0))
        self.assertEqual((result.termination, result.error), ("callback_error", "first sink error"))
        self.assertTrue(result.accounting_complete)
        self.assertEqual(len(decoded), 3)
        self.assertEqual(calls, [1])

    def test_decode_error_counts_received_work_without_sink(self):
        calls = []
        result = run_guided_campaign(CampaignConfig(jobs=1, workers=2, fuzz=TINY), RunLimits(), GuidanceConfig(),
            make_counter, make_feedback, calls.append,
            process=ProcessWorkers(InputTransfer(campaign._pickle_encode, bad_decode)))
        self.assertEqual(calls, [])
        self.assertEqual((result.finished, result.delivered, result.reported_transitions), (1, 0, 2))
        self.assertTrue(result.accounting_complete)
        self.assertEqual(result.termination, "callback_error")
        self.assertIn("decode exploded", result.error)

    def test_encode_error_is_missing_outcome_not_synthetic_job(self):
        jobs = []
        result = run_guided_campaign(CampaignConfig(jobs=1, workers=2, fuzz=TINY), RunLimits(), GuidanceConfig(),
            make_counter, make_feedback, jobs.append,
            process=ProcessWorkers(InputTransfer(bad_encode, campaign._pickle_decode)))
        self.assertEqual((result.started, result.finished, result.errors), (1, 0, 0))
        self.assertFalse(result.accounting_complete)
        self.assertEqual(result.termination, "worker_error")
        self.assertEqual(jobs, [])

    def test_hostile_sink_diagnostic_survives_later_worker_failure_and_join(self):
        barrier, release = self.context.Barrier(2), self.context.Event()
        calls = []
        def sink(job):
            self.assertEqual(job.outcome.kind, "completed")
            calls.append(job.job_id)
            release.set()
            raise HostileError()
        result = run_campaign(CampaignConfig(jobs=2, workers=2, result_buffer=0, fuzz=TINY), RunLimits(),
            ParallelFactory(barrier=barrier, release_zero=release, exit_zero=True), sink, process=PROCESS)
        self.assertEqual(calls, [1])
        self.assertEqual((result.started, result.finished, result.delivered, result.errors), (2, 1, 0, 0))
        self.assertFalse(result.accounting_complete)
        self.assertEqual((result.termination, result.error),
                         ("callback_error", "campaign callback panicked: non-string exception"))

    def test_worker_failures_observed_once_in_launch_order_after_reverse_exit(self):
        for sink_failure in (False, True):
            with self.subTest(sink_failure=sink_failure):
                context, made, events = self.context, [], []
                last_exited, ready, release = context.Event(), context.Event(), context.Event()
                bridge, apply = campaign._bridge, campaign._Coordinator.apply
                class Context:
                    Pipe = staticmethod(context.Pipe)
                    def Process(self, *args, **kwargs):
                        process = context.Process(*args, **kwargs)
                        process.name = "n06-test-slot-" + str(len(made))
                        made.append(process)
                        return process
                def observe_bridge(connection, coordinator, control, channel, slot):
                    try:
                        return bridge(connection, coordinator, control, channel, slot)
                    finally:
                        if slot == 1:
                            last_exited.set()  # EOF observed before slot0 exits.
                def observe_apply(coordinator, event):
                    if event.kind == "worker_failed": events.append(event.error)
                    return apply(coordinator, event)
                def sink(job):
                    self.assertEqual(job.outcome.kind, "completed")
                    self.assertTrue(ready.wait(10), "both workers did not claim before sink error")
                    release.set()
                    raise HostileError()
                factory = ReverseFailureFactory(context.Barrier(2), last_exited, sink_failure,
                    ready if sink_failure else None, release if sink_failure else None)
                with patch.object(campaign.multiprocessing, "get_context", return_value=Context()), \
                     patch.object(campaign, "_bridge", observe_bridge), \
                     patch.object(campaign._Coordinator, "apply", observe_apply):
                    result = run_campaign(CampaignConfig(jobs=3 if sink_failure else 2, workers=2,
                        result_buffer=0, fuzz=TINY), RunLimits(), factory, sink, process=PROCESS)
                self.assertEqual(events, ["campaign worker exited with code 41", "campaign worker exited with code 42"])
                self.assertEqual(result.termination, "callback_error" if sink_failure else "worker_error")
                self.assertEqual(result.error, "campaign callback panicked: non-string exception"
                                 if sink_failure else events[-1])
                self.assertEqual((result.started, result.finished, result.delivered, result.errors),
                                 (3, 1, 0, 0) if sink_failure else (2, 0, 0, 0))
                self.assertFalse(result.accounting_complete)

    def test_worker_process_death_keeps_survivor_sink_and_lost_accounting(self):
        jobs = []
        result = run_campaign(CampaignConfig(jobs=4, workers=2, result_buffer=0, fuzz=TINY), RunLimits(),
                              ParallelFactory(exit_zero=True), jobs.append, process=PROCESS)
        self.assertEqual((result.started, result.finished, result.delivered, result.errors), (4, 3, 3, 0))
        self.assertEqual(result.termination, "worker_error")
        self.assertFalse(result.accounting_complete)
        self.assertEqual({j.job_id for j in jobs}, {1, 2, 3})

    def test_cancelled_active_jobs_are_drained_and_joined(self):
        token, release = CampaignCancellation(), self.context.Event()
        barrier, pids = self.context.Barrier(2), self.context.Array("q", 8)
        jobs = []
        def sink(job):
            jobs.append(job)
            token.cancel()
            release.set()
        result = run_campaign(CampaignConfig(jobs=8, workers=2, result_buffer=0, fuzz=TINY),
            RunLimits(cancellation=token), ParallelFactory(barrier, pids, release_zero=release), sink, process=PROCESS)
        self.assertEqual(result.termination, "cancelled")
        self.assertEqual(result.finished, result.started)
        self.assertEqual(result.delivered, result.finished)
        self.assertLess(result.started, 8)
        self.assertTrue(result.accounting_complete)
        self.assertTrue(any(j.outcome.report.termination == "cancelled" for j in jobs))

    def test_later_pipe_process_and_thread_construction_failures_drain_rendezvous(self):
        context = self.context
        real_thread = threading.Thread
        for failing_stage in ("pipe", "process", "thread"):
            with self.subTest(stage=failing_stage):
                calls = {name: 0 for name in ("pipe", "process", "thread")}
                started = context.Event()
                pids = context.Array("q", 5)
                def setup(stage, callback, *args, **kwargs):
                    calls[stage] += 1
                    if stage == failing_stage and calls[stage] == 2:
                        if not started.wait(10):
                            raise RuntimeError("first worker never started")
                        raise OSError("injected " + stage + " construction failure")
                    return callback(*args, **kwargs)
                class Context:
                    def Pipe(self): return setup("pipe", context.Pipe)
                    def Process(self, *args, **kwargs):
                        return setup("process", context.Process, *args, **kwargs)
                jobs = []
                with patch.object(campaign.multiprocessing, "get_context", return_value=Context()), \
                     patch.object(campaign.threading, "Thread",
                                  lambda *a, **kw: setup("thread", real_thread, *a, **kw)):
                    result = run_campaign(CampaignConfig(jobs=5, workers=2, result_buffer=0, fuzz=TINY),
                        RunLimits(), ParallelFactory(started=pids, threshold=1, saturated=started),
                        jobs.append, process=PROCESS)
                self.assertEqual(result.termination, "worker_error")
                self.assertIn("injected " + failing_stage, result.error)
                self.assertEqual((result.started, result.finished, result.delivered), (1, 1, 1))
                self.assertTrue(result.accounting_complete)
                self.assertEqual([j.job_id for j in jobs], [0])

    def test_spawn_factory_and_policy_errors_are_explicit(self):
        with self.assertRaisesRegex(ModelError, "explicit ProcessWorkers"):
            run_campaign(CampaignConfig(workers=2), RunLimits(), make_counter, lambda job: None)
        with self.assertRaisesRegex(ModelError, "CampaignCancellation"):
            run_campaign(CampaignConfig(workers=2), RunLimits(cancellation=lambda: False),
                         make_counter, lambda job: None, process=PROCESS)
        result = run_campaign(CampaignConfig(workers=2), RunLimits(), lambda job: Counter(),
                              lambda job: None, process=PROCESS)
        self.assertEqual((result.termination, result.started, result.finished), ("worker_error", 0, 0))
        self.assertTrue(result.accounting_complete)


if __name__ == "__main__":
    unittest.main()
