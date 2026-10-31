import unittest
from unittest.mock import patch
from statelessness import (Auto, AutoOptions, CampaignCancellation, CampaignConfig, FuzzConfig, GuidanceConfig,
    Metadata, ModelError, PickleInputTransfer, ProcessWorkers, Rng, RunLimits, Transition, exact_state, fuzz, fuzz_auto,
    guided_fuzz, guided_fuzz_auto, run_auto_campaign, run_guided_auto_campaign)
from statelessness.search import _AutoScanStopped, _Control, _ControlledAuto

class Model:
    def metadata(self): return Metadata('auto-test')
    def initial_state(self): return 0
    def inputs(self, state): return (1, 2, 3)
    def step(self, state, value): return Transition(state + value)
    def check_state(self, state): return ()

def make_model(job): return Model()
def make_feedback(job, model): return exact_state()

class ProcessCancelModel(Model):
    def __init__(self, cancel): self.cancel = cancel
    def inputs(self, state):
        yield 1
        self.cancel.cancel()
        yield 2

class ProcessCancelFactory:
    def __init__(self, cancel): self.cancel = cancel
    def __call__(self, job): return ProcessCancelModel(self.cancel)

class CooperativeAutoTests(unittest.TestCase):
    def test_cancel_during_sampling_preserves_rng_and_does_not_step(self):
        cancelled = [False]
        class Cancel(Model):
            def inputs(self, state):
                yield 1
                yield 2
                cancelled[0] = True
                yield 3
            def step(self, state, value): raise AssertionError('must not step')
        limits = RunLimits(cancellation=lambda: cancelled[0])
        result = fuzz_auto(Cancel(), FuzzConfig(cases=1, max_steps=1), limits=limits)
        self.assertEqual((result.termination, result.transitions), ('cancelled', 0))
        cancelled[0] = False
        model = _ControlledAuto(Cancel(), AutoOptions(), _Control(limits))
        rng = Rng(123)
        with self.assertRaises(_AutoScanStopped): model.generate(0, rng)
        self.assertEqual(rng.state, 123)

    def test_cancel_during_membership_and_callback_error_precedence(self):
        cancelled = [False]
        class Cancel(Model):
            def inputs(self, state):
                yield 1
                cancelled[0] = True
                yield 2
        model = _ControlledAuto(Cancel(), AutoOptions(), _Control(RunLimits(cancellation=lambda: cancelled[0])))
        with self.assertRaises(_AutoScanStopped): model.is_enabled(0, 3)
        cancelled[0] = False
        class Broken(Model):
            def inputs(self, state):
                cancelled[0] = True
                raise RuntimeError('iterator failure')
                yield 1
        with self.assertRaisesRegex(ModelError, 'iterator failure'):
            fuzz_auto(Broken(), FuzzConfig(cases=1, max_steps=1), limits=RunLimits(cancellation=lambda: cancelled[0]))

    def test_completed_equivalence(self):
        config = FuzzConfig(seed=99, cases=5, max_steps=4)
        self.assertEqual(fuzz_auto(Model(), config), fuzz(Auto(Model()), config))

    def test_cancellation_during_membership_is_reported(self):
        cancelled = [False]
        calls = [0]
        class Cancel(Model):
            def inputs(self, state):
                calls[0] += 1
                if calls[0] == 1:
                    yield 1
                else:
                    yield 2
                    cancelled[0] = True
                    yield 1
            def step(self, state, value): raise AssertionError('must not step')
        result = fuzz_auto(Cancel(), FuzzConfig(cases=1, max_steps=1),
                           limits=RunLimits(cancellation=lambda: cancelled[0]))
        self.assertEqual((result.termination, result.transitions), ('cancelled', 0))

    def test_deadline_during_scan_is_reported(self):
        clock = [0.0]
        class Deadline(Model):
            def inputs(self, state):
                yield 1
                clock[0] = 2.0
                yield 2
            def step(self, state, value): raise AssertionError('must not step')
        with patch('statelessness.search.time.monotonic', side_effect=lambda: clock[0]):
            result = fuzz_auto(Deadline(), FuzzConfig(cases=1, max_steps=1),
                               limits=RunLimits(max_duration=1.0))
        self.assertEqual((result.termination, result.transitions), ('deadline', 0))

    def test_guided_and_campaign_paths(self):
        config = FuzzConfig(seed=99, cases=3, max_steps=2)
        self.assertEqual(guided_fuzz_auto(Model(), exact_state(), config),
                         guided_fuzz(Auto(Model()), exact_state(), config))
        cancelled = [False]
        closed = []
        class Cancel(Model):
            def inputs(self, state):
                yield 1
                cancelled[0] = True
                yield 2
            def campaign_close(self): closed.append(True)
        for guided in (False, True):
            cancelled[0] = False
            jobs = []
            campaign = CampaignConfig(jobs=1, fuzz=config)
            limits = RunLimits(cancellation=lambda: cancelled[0])
            if guided:
                result = run_guided_auto_campaign(campaign, limits, GuidanceConfig(),
                    lambda job: Cancel(), lambda job, model: exact_state(), jobs.append)
            else:
                result = run_auto_campaign(campaign, limits, lambda job: Cancel(), jobs.append)
            self.assertEqual(result.finished, 1)
            self.assertEqual(jobs[0].outcome.report.termination, 'cancelled')
        self.assertEqual(len(closed), 2)

    def test_campaign_process_workers(self):
        config = CampaignConfig(jobs=2, workers=2, fuzz=FuzzConfig(cases=1, max_steps=1))
        workers = ProcessWorkers(PickleInputTransfer())
        for guided in (False, True):
            jobs = []
            if guided:
                result = run_guided_auto_campaign(config, RunLimits(), GuidanceConfig(),
                    make_model, make_feedback, jobs.append, process=workers)
            else:
                result = run_auto_campaign(config, RunLimits(), make_model, jobs.append, process=workers)
            self.assertEqual((result.finished, result.delivered), (2, 2))
            self.assertTrue(all(job.outcome.kind == 'completed' for job in jobs))

    def test_campaign_process_cancellation_during_scan(self):
        for guided in (False, True):
            cancel = CampaignCancellation()
            config = CampaignConfig(jobs=1, workers=2, fuzz=FuzzConfig(cases=1, max_steps=1))
            jobs = []
            args = (config, RunLimits(cancellation=cancel))
            worker = ProcessWorkers(PickleInputTransfer())
            if guided:
                result = run_guided_auto_campaign(*args, GuidanceConfig(), ProcessCancelFactory(cancel),
                    make_feedback, jobs.append, process=worker)
            else:
                result = run_auto_campaign(*args, ProcessCancelFactory(cancel), jobs.append, process=worker)
            self.assertEqual((result.finished, result.delivered), (1, 1))
            self.assertEqual(jobs[0].outcome.report.termination, 'cancelled')
