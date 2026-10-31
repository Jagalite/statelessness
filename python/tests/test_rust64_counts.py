"""Bounded independent integer witnesses, derived from pinned Rust source.

No native candidate result supplies an expectation and no huge work is iterated.
"""
import unittest

from statelessness import (Auto, AutoOptions, CampaignConfig, Check, EXHAUSTED, Feedback,
    FeedbackMetadata, FuzzConfig, GuidanceConfig, Metadata, ModelError, Rng, RunLimits,
    ShrinkConfig, ShrinkLimits, Transition, exact_state, fuzz, guided_fuzz, run_campaign, shrink)
from statelessness._campaign_lifecycle import Event, Lifecycle, Summary
from campaign_lifecycle_fixture import LifecycleCodec, native_state, normalize_state

MAX = (1 << 64) - 1


class Empty:
    def metadata(self): return Metadata("rust64", 1, 1, 1)
    def initial_state(self): return 0
    def check_state(self, state): return ()
    def generate(self, state, rng): return EXHAUSTED
    def is_enabled(self, state, value): return True
    def step(self, state, value): return Transition(state + 1)


class InitialFailure(Empty):
    def check_state(self, state): return (Check("boundary", "failed", "stop"),)


class OneStepFailure(Empty):
    def generate(self, state, rng): return 1
    def check_state(self, state):
        return (Check("boundary", "failed", "stop"),) if state else ()


class Rust64Counts(unittest.TestCase):
    def test_generation_and_shrink_wide_limits_bounded_work(self):
        class Domain(InitialFailure):
            def inputs(self, state): return ()
        model = Auto(Domain(), AutoOptions(max_candidates=MAX))
        rng = Rng(42)
        self.assertIs(model.generate(0, rng), EXHAUSTED)
        self.assertEqual(rng.state, 42)
        report = fuzz(model, FuzzConfig(cases=1, max_steps=MAX))
        result = shrink(model, report.failure, ShrinkConfig(max_attempts=MAX))
        self.assertEqual((result.termination, result.attempts, result.replayed_transitions),
                         ("search_complete", 1, 0))
        self.assertTrue(result.validated_original)
        result = shrink(model, report.failure, ShrinkConfig(max_attempts=MAX),
                        limits=ShrinkLimits(control=RunLimits(cancellation=lambda: True)))
        self.assertEqual((result.termination, result.attempts), ("cancelled", 1))
        self.assertFalse(result.validated_original)

    def test_fuzz_zero_and_cancel_before_callbacks(self):
        for cases in (0, (1 << 53) + 1, 1 << 63, MAX):
            report = fuzz(None, FuzzConfig(cases=cases, max_steps=MAX),
                          limits=RunLimits(cancellation=lambda: True))
            self.assertEqual((report.termination, report.cases, report.transitions),
                             ("cancelled", 0, 0))
        self.assertEqual(fuzz(None, FuzzConfig(cases=0, max_steps=MAX)).termination,
                         "cases_completed")

    def test_empty_domain_and_failure_bound_work(self):
        report = fuzz(Empty(), FuzzConfig(cases=1, max_steps=MAX))
        self.assertEqual((report.termination, report.cases, report.transitions),
                         ("cases_completed", 1, 0))
        for model, steps in ((InitialFailure(), 0), (OneStepFailure(), 1)):
            report = fuzz(model, FuzzConfig(cases=MAX, max_steps=MAX))
            self.assertEqual((report.termination, report.cases, report.transitions),
                             ("failure_found", 1, steps))
            self.assertEqual(report.failure.inputs, (1,) * steps)

    def test_guidance_limits_do_not_preallocate(self):
        calls = []
        class Keys(Feedback):
            def metadata(self):
                calls.append("metadata")
                return FeedbackMetadata("wide", 1, "integer")
            def transition_features(self, before, value, after, out):
                raise AssertionError("cancelled before feedback")
        report = guided_fuzz(None, Keys(), FuzzConfig(cases=MAX, max_steps=MAX),
            GuidanceConfig(MAX, MAX, MAX, MAX),
            limits=RunLimits(cancellation=lambda: True))
        self.assertEqual(calls, ["metadata"])
        self.assertEqual((report.termination, report.cases), ("cancelled", 0))
        report = guided_fuzz(Empty(), exact_state(), FuzzConfig(cases=1, max_steps=MAX),
                            GuidanceConfig(MAX, MAX, MAX, MAX))
        self.assertEqual((report.guidance.unique_features, report.guidance.corpus_inputs), (1, 0))

    def test_campaign_wide_cancel_and_one_failure(self):
        config = CampaignConfig(jobs=MAX, first_job=1,
            fuzz=FuzzConfig(cases=1, max_steps=MAX, max_transitions=1), stop_on_failure=True)
        report = run_campaign(config, RunLimits(cancellation=lambda: True), None, None)
        self.assertEqual((report.requested, report.not_started, report.started, report.delivered),
                         (MAX, MAX, 0, 0))
        seen = []
        report = run_campaign(config, RunLimits(), lambda job: InitialFailure(), seen.append)
        self.assertEqual((report.started, report.finished, report.delivered, report.failures,
                          report.not_started), (1, 1, 1, 1, MAX - 1))
        self.assertEqual([job.job_id for job in seen], [1])

    def test_campaign_validation_precedes_cancellation(self):
        for config, message in (
            (CampaignConfig(jobs=0, workers=0, fuzz=FuzzConfig(mutation_percent=101)), "jobs must"),
            (CampaignConfig(jobs=MAX, workers=0), "workers must"),
            (CampaignConfig(jobs=MAX, result_buffer=1025), "result_buffer must"),
            (CampaignConfig(jobs=MAX, fuzz=FuzzConfig(mutation_percent=101)), "mutation_percent"),
            (CampaignConfig(jobs=MAX, first_job=2), "job ID range"),
            (CampaignConfig(jobs=2, fuzz=FuzzConfig(cases=MAX)), "aggregate cases"),
            (CampaignConfig(jobs=2, fuzz=FuzzConfig(cases=1, max_steps=MAX,
                                                  max_transitions=MAX)), "aggregate transitions")):
            with self.subTest(message=message), self.assertRaisesRegex(ModelError, message):
                run_campaign(config, RunLimits(cancellation=lambda: True), None, None)

    def test_lifecycle_roundtrip_ack_and_saturation(self):
        core = Lifecycle(1, MAX, False)
        codec = LifecycleCodec(1, MAX, False)
        state = core.initial_state()
        def apply(event):
            nonlocal state
            transition = core.step(state, event)
            self.assertEqual(transition.disposition.kind, "accepted")
            wire = codec.encode_state(normalize_state(transition.state))
            state = native_state(codec.decode_state(wire))
            self.assertEqual(state, transition.state)
        for job, count in ((1, MAX), (2, 1)):
            apply(Event("claim"))
            apply(Event("complete", job_id=job,
                        summary=Summary("completed", transitions=count, cases=count,
                                        skipped_checks=count)))
            self.assertEqual(state.report.delivered, job - 1)
            apply(Event("receive", job_id=job))
            self.assertEqual(state.report.delivered, job - 1)
            apply(Event("sink_returned", job_id=job))
        self.assertEqual((state.report.reported_transitions, state.report.reported_cases,
                          state.report.reported_skipped_checks), (MAX, MAX, MAX))
        self.assertFalse(state.report.accounting_complete)
        apply(Event("claim", stop="cancelled"))
        apply(Event("finish"))
        self.assertEqual((state.report.termination, state.report.delivered,
                          state.report.not_started), ("cancelled", 2, MAX - 2))
