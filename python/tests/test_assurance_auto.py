import unittest
from statelessness import (Metadata, Check, Transition, FuzzConfig, RunLimits, GuidanceConfig,
    FeedbackMetadata, CampaignConfig, assured_fuzz_auto, assured_guided_fuzz_auto,
    assured_auto_campaign, assured_guided_auto_campaign)
from statelessness.assurance import Manifest, Provenance, RequiredProperty, InputFamily

class Coverage:
    # Every checked transition is classified so a completed run can be adequate.
    def observe(self, before, input, after, history, hits): hits.family('step')

class Feedback:
    def metadata(self): return FeedbackMetadata('features')
    def initial_features(self, state, out): out.push(state)
    def transition_features(self, before, input, after, out): out.push(after.state)

class Model:
    def __init__(self, cancel=None): self.cancel = cancel
    def metadata(self): return Metadata('auto-assurance')
    def initial_state(self): return 0
    def inputs(self, state):
        yield 1
        if self.cancel is not None: self.cancel[0] = True
        yield 2
    def step(self, state, input): return Transition(state + input)
    def check_state(self, state): return (Check('state'),)
    def check_transition(self, before, input, transition): return ()

class Tests(unittest.TestCase):
    def test_auto_assurance_reports_interrupted_and_completed(self):
        manifest = Manifest('auto-assurance', Provenance('m', 'model', 'p'),
            (RequiredProperty('state', 'state'),), (InputFamily('step'),), required_search_outcome='completed')
        config = FuzzConfig(cases=1, max_steps=1)
        for guided in (False, True):
            flag = [False]
            args = (Model(flag), manifest, Coverage())
            run = (lambda: assured_guided_fuzz_auto(*args, Feedback(), config, limits=RunLimits(cancellation=lambda: flag[0]))) if guided else (lambda: assured_fuzz_auto(*args, config, limits=RunLimits(cancellation=lambda: flag[0])))
            result = run()
            self.assertEqual(result.search.termination, 'cancelled')
            self.assertEqual(result.assurance.outcome, 'interrupted')
            self.assertFalse(result.assurance.assess().adequate)
            self.assertNotIn('auto.max_candidates', dict(result.assurance.budgets))
            completed = (assured_guided_fuzz_auto(Model(), manifest, Coverage(), Feedback(), config) if guided
                         else assured_fuzz_auto(Model(), manifest, Coverage(), config))
            self.assertEqual(completed.assurance.outcome, 'cases_completed')
            self.assertEqual(completed.assurance.observed_families, frozenset({'step'}))
            self.assertEqual(completed.assurance.unclassified_transitions, 0)
            self.assertTrue(completed.assurance.assess().adequate, completed.assurance.assess().reasons)

    def test_auto_campaign_retains_interrupted_assurance(self):
        manifest = Manifest('auto-assurance', Provenance('m', 'model', 'p'),
            (RequiredProperty('state', 'state'),), (InputFamily('step'),), required_search_outcome='completed')
        for guided in (False, True):
            flag = [False]
            factory = lambda job: Model(flag)
            coverage = lambda job, model: Coverage()
            limits = RunLimits(cancellation=lambda: flag[0])
            config = CampaignConfig(jobs=1, fuzz=FuzzConfig(cases=1, max_steps=1))
            if guided:
                result = assured_guided_auto_campaign(config, limits, manifest, Metadata('auto-assurance'), GuidanceConfig(), factory, coverage, lambda job, model: Feedback(), lambda job: None)
            else:
                result = assured_auto_campaign(config, limits, manifest, Metadata('auto-assurance'), factory, coverage, lambda job: None)
            self.assertEqual(result.assurance.report.outcome, 'interrupted')
            self.assertFalse(result.assurance.report.assess().adequate)
            self.assertEqual(len(result.assurance.jobs), 1)

if __name__ == '__main__': unittest.main()
