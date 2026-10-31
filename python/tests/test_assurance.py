import unittest
from dataclasses import replace
from statelessness import (Check, Metadata, ModelError, Transition, enumerate_states, SearchConfig,
                           FuzzConfig, RunLimits, GuidanceConfig, FeedbackMetadata, CampaignConfig, ProcessWorkers, PickleInputTransfer, InputTransfer, assured_campaign, assured_guided_campaign,
                           assured_enumerate, assured_fuzz, assured_guided_fuzz)
from statelessness.assurance import (Manifest, Report, MergedReport, Provenance, RequiredProperty, InputFamily, _evidence_size,
                                    CoverGoal, Observer, CheckCounts, merge_reports, search_outcome)

class Model:
    def metadata(self): return Metadata('counter')
    def initial_state(self): return 0
    def inputs(self, state): return (1,) if state < 2 else ()
    def step(self, state, input): return Transition(state + input)
    def check_state(self, state): return (Check('state'),)
    def check_transition(self, before, input, transition): return (Check('edge'),)

class Coverage:
    def observe(self, before, input, after, history, hits):
        hits.family('increment')
        if after.state == 2: hits.goal('two')

class WorkerModel(Model):
    def generate(self, state, rng): return 1

class FailureWorker(WorkerModel):
    def check_state(self, state):
        return (Check('state', 'failed', 'failure') if state else Check('state'),)

class WorkerFeedback:
    def metadata(self): return FeedbackMetadata('worker')
    def initial_features(self, state, out): out.push(state)
    def transition_features(self, before, input, after, out): out.push(after.state)

def worker_factory(id): return WorkerModel()
def failure_factory(id): return FailureWorker()
def worker_coverage(id, model): return Coverage()
def worker_feedback(id, model): return WorkerFeedback()
def fail_encode(value): raise ValueError('encode failed')
def unused_decode(value): return 1
def fail_decode(value): raise ValueError('decode failed')

class AssuranceTests(unittest.TestCase):
    def test_package_exports_and_example(self):
        import statelessness
        import runpy
        from pathlib import Path
        self.assertIs(statelessness.AssuranceObserver, Observer)
        self.assertIs(statelessness.AssuranceProvenance, Provenance)
        self.assertIsNot(statelessness.Provenance, Provenance)
        example = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'examples' / 'assurance.py'))
        self.assertTrue(example['main']())

    def manifest(self):
        return Manifest('counter', Provenance('catalog-v1', 'model-v1', 'properties-v1'),
                        (RequiredProperty('state', 'state'), RequiredProperty('edge', 'transition')),
                        (InputFamily('increment'),), cover_goals=(CoverGoal('two'),))
    def run_model(self, model=None, coverage=None, manifest=None, config=None):
        model = model or Model()
        observer = Observer(manifest or self.manifest(), model.metadata(), coverage or Coverage())
        result = enumerate_states(model, config or SearchConfig(), observer=observer)
        return observer.finish(search_outcome(result.termination), result.termination)
    def test_actual_bfs_and_missing_requirements(self):
        report = self.run_model()
        self.assertTrue(report.assess().adequate)
        self.assertEqual(report.checked_transitions, 2)
        class Omitted(Model):
            def check_transition(self, *args): return ()
        self.assertFalse(self.run_model(Omitted()).assess().adequate)
        class Skipped(Model):
            def check_transition(self, *args): return (Check('edge', 'skipped', 'not applicable'),)
        self.assertFalse(self.run_model(Skipped()).assess().adequate)
        report = self.run_model(config=SearchConfig(max_depth=1))
        self.assertFalse(report.assess().adequate)
    def test_failed_callback_does_not_publish_partial_hits(self):
        class BadCoverage:
            def observe(self, before, input, after, history, hits):
                hits.family('increment'); hits.goal('two')
                raise ValueError('broken')
        report = self.run_model(coverage=BadCoverage())
        self.assertEqual(report.observed_families, frozenset())
        self.assertEqual(report.observed_goals, frozenset())
        self.assertEqual(report.unclassified_transitions, 2)
        self.assertFalse(report.assess().adequate)

    def test_coverage_hit_limits_are_latched_and_leases_expire(self):
        retained = []
        class TooMany:
            def observe(self, before, input, after, history, hits):
                retained.append(hits)
                hits.family('increment')
                for n in range(4096):
                    try: hits.goal(str(n))
                    except ModelError: pass
        report = self.run_model(coverage=TooMany())
        self.assertEqual(report.observed_families, frozenset())
        self.assertEqual(report.unclassified_transitions, 2)
        self.assertTrue(any('hit limit exceeded' in error for error in report.callback_errors))
        with self.assertRaisesRegex(ModelError, 'expired'): retained[0].family('increment')
        class Invalid:
            def observe(self, before, input, after, history, hits):
                try: hits.family(123)
                except ModelError: pass
                return None
        report = self.run_model(coverage=Invalid())
        self.assertEqual(report.unclassified_transitions, 2)
        self.assertFalse(report.assess().adequate)

    def test_coverage_cannot_reset_failure_or_write_from_worker(self):
        import threading
        class Tamper:
            def observe(self, before, input, after, history, hits):
                try: hits.family(123)
                except ModelError: pass
                try: hits._failure = None
                except AttributeError: pass
                try: hits._families = {'increment'}
                except AttributeError: pass
        report = self.run_model(coverage=Tamper())
        self.assertEqual(report.observed_families, frozenset())
        self.assertFalse(report.assess().adequate)
        class Worker:
            def observe(self, before, input, after, history, hits):
                def use():
                    try: hits.family('increment')
                    except ModelError: pass
                thread = threading.Thread(target=use)
                thread.start(); thread.join()
        report = self.run_model(coverage=Worker())
        self.assertEqual(report.observed_families, frozenset())
        self.assertTrue(any('owner thread' in e for e in report.callback_errors))
        self.assertFalse(report.assess().adequate)

    def test_observer_reentry_and_detached_evidence(self):
        holder = {}
        class Reentrant:
            def observe(self, before, input, after, history, hits):
                observer = holder['observer']
                observer.properties.clear()
                try: observer.transitions = 50
                except AttributeError: pass
                try: observer.finish('graph_exhausted', 'graph_exhausted')
                except ModelError: pass
                hits.family('increment')
        observer = Observer(self.manifest(), Model().metadata(), Reentrant())
        holder['observer'] = observer
        observer.initial(0, (Check('state'),))
        observer.transition(0, 1, Transition(1), (Check('state'),), (Check('edge'),), ())
        report = observer.finish('graph_exhausted', 'graph_exhausted')
        self.assertEqual(report.checked_transitions, 1)
        self.assertEqual(report.observed_families, frozenset())
        self.assertTrue(any('reentry rejected' in error for error in report.callback_errors))
        self.assertFalse(report.assess().adequate)

    def test_concurrent_observer_mutation_fails_closed(self):
        import threading
        holder = {}
        class Concurrent:
            def observe(self, before, input, after, history, hits):
                def mutate():
                    try: holder['observer'].initial(99, (Check('state'),))
                    except ModelError: pass
                thread = threading.Thread(target=mutate)
                thread.start(); thread.join()
                hits.family('increment')
        observer = Observer(self.manifest(), Model().metadata(), Concurrent())
        holder['observer'] = observer
        observer.initial(0, (Check('state'),))
        observer.transition(0, 1, Transition(1), (Check('state'),), (Check('edge'),), ())
        report = observer.finish('graph_exhausted', 'graph_exhausted')
        self.assertEqual(dict(report.observed_properties)[RequiredProperty('state', 'state')].passed, 2)
        self.assertFalse(report.assess().adequate)
        self.assertEqual(report.observed_families, frozenset())

    def test_incomplete_check_batch_cannot_be_completed_by_claim(self):
        report = self.run_model()
        observer = Observer(report.manifest, report.metadata, Coverage())
        observer.initial(0, (Check('state'),))
        observer.transition(0, 1, Transition(1), (Check('state'),), (Check('edge'),), ())
        observer.check_error('transition', (), False)
        result = observer.finish('graph_exhausted', 'graph_exhausted')
        self.assertIn('assurance check batch incomplete', result.callback_errors)
        self.assertFalse(result.assess().adequate)

    def test_observer_finalization_is_single_use(self):
        observer = Observer(self.manifest(), Model().metadata(), Coverage())
        observer.initial(0, (Check('state'),))
        observer.transition(0, 1, Transition(1), (Check('state'),), (Check('edge'),), ())
        report = observer.finish('graph_exhausted', 'graph_exhausted')
        saved = report.to_json()
        for callback in (lambda: observer.initial(0, (Check('state'),)),
                         lambda: observer.transition(0, 1, Transition(1), (), (), ()),
                         lambda: observer.check_error('state', (), False),
                         lambda: observer.finish('graph_exhausted', 'graph_exhausted')):
            with self.assertRaisesRegex(ModelError, 'already finalized'): callback()
        self.assertEqual(report.to_json(), saved)

    def test_invalid_finish_does_not_finalize(self):
        observer = Observer(self.manifest(), Model().metadata(), Coverage())
        with self.assertRaises(ModelError): observer.finish('bogus', 'bad')
        observer.initial(0, (Check('state'),))
        self.assertEqual(observer.finish('not_started', 'not started').outcome, 'not_started')

    def test_manifest_preflight_is_bounded_before_snapshot(self):
        class NoCopy(list):
            def __deepcopy__(self, memo): raise AssertionError('must not copy')
        base = self.manifest()
        with self.assertRaises(ModelError): Observer(replace(base, required_properties=NoCopy()), Model().metadata(), Coverage())
        with self.assertRaises(ModelError): Observer(replace(base, required_properties=[RequiredProperty(str(n), 'state') for n in range(8193)]), Model().metadata(), Coverage())
        with self.assertRaises(ModelError): Observer(replace(base, model='x' * 4097), Metadata('x' * 4097), Coverage())
        huge_bounds = tuple((str(n), 'x' * 300) for n in range(4000))
        with self.assertRaises(ModelError): Observer(replace(base, bounds=huge_bounds), Model().metadata(), Coverage())

    def test_hostile_coverage_errors_remain_bounded_and_nonfatal(self):
        class BadError(Exception):
            def __str__(self): raise RuntimeError('bad error text')
        class BadCoverage:
            def observe(self, *args): raise BadError()
        report = self.run_model(coverage=BadCoverage())
        self.assertEqual(report.unclassified_transitions, 2)
        self.assertTrue(any('exception text unavailable' in error for error in report.callback_errors))
        class Distinct:
            def __init__(self): self.n = 0
            def observe(self, before, input, after, history, hits):
                self.n += 1
                hits.family('unknown-' + str(self.n))
        observer = Observer(self.manifest(), Model().metadata(), Distinct())
        for _ in range(1100): observer.transition(0, 1, Transition(1), (), (), ())
        report = observer.finish('failed', 'failure_found')
        self.assertLessEqual(len(report.callback_errors), 1025)
        self.assertIn('assurance error limit exceeded', report.callback_errors)

    def test_property_accounting_bounds_fail_closed(self):
        class NoCoverage:
            def observe(self, before, input, after, history, hits): hits.family('increment')
        observer = Observer(self.manifest(), Model().metadata(), NoCoverage())
        observer.initial(0, tuple(Check('property-' + str(n)) for n in range(4097)))
        report = observer.finish('graph_exhausted', 'graph_exhausted')
        self.assertLessEqual(len(report.observed_properties), 4096)
        self.assertTrue(any('property' in error and 'limit exceeded' in error for error in report.callback_errors))
        self.assertFalse(report.assess().adequate)
        observer = Observer(self.manifest(), Model().metadata(), NoCoverage())
        observer.initial(0, (Check('x' * 4097),))
        report = observer.finish('graph_exhausted', 'graph_exhausted')
        self.assertIn('assurance property identity exceeds limit or is invalid', report.callback_errors)
        self.assertFalse(report.assess().adequate)
        observer = Observer(self.manifest(), Model().metadata(), NoCoverage())
        for n in range(4097): observer.initial(0, (Check('id-' + str(n)),))
        report = observer.finish('graph_exhausted', 'graph_exhausted')
        self.assertLessEqual(len(report.observed_properties), 4096)
        self.assertIn('assurance observed property limit exceeded', report.callback_errors)
        self.assertFalse(report.assess().adequate)
    def test_excluded_and_undeclared_hits_are_not_adequate(self):
        manifest = replace(self.manifest(), input_families=(InputFamily('increment', 'not allowed'),))
        self.assertFalse(self.run_model(manifest=manifest).assess().adequate)
        class Undeclared:
            def observe(self, before, input, after, history, hits): hits.family('other')
        report = self.run_model(coverage=Undeclared())
        self.assertEqual(report.unclassified_transitions, 2)
        self.assertFalse(report.assess().adequate)
    def test_assess_reasons_use_rust_debug_wording(self):
        # Rust src/assurance.rs AssuranceReport::assess formats statuses with {:?}.
        manifest = replace(self.manifest(), input_families=(InputFamily('increment', 'not "allowed"'),))
        class NoGoal:
            def observe(self, before, input, after, history, hits): hits.family('increment')
        class Omitted(Model):
            def check_transition(self, *args): return ()
        reasons = self.run_model(Omitted(), NoGoal(), manifest).assess().reasons
        self.assertIn('no required input families declared', reasons)
        self.assertIn('property edge Transition: Missing', reasons)
        self.assertIn('input family increment: ExclusionViolated { reason: "not \\"allowed\\"" }', reasons)
        self.assertIn('cover goal two: UnreachableUnderDeclaredModel', reasons)
        stopped = self.run_model(coverage=NoGoal(), config=SearchConfig(max_depth=1))
        self.assertIn('required search outcome not met: DepthLimited', stopped.assess().reasons)
        self.assertIn('cover goal two: NotReachedBeforeStop', stopped.assess().reasons)
        broken = replace(self.run_model(), termination='nonsense', outcome='graph_exhausted')
        self.assertTrue(broken.assess().reasons[0].startswith('inconsistent assurance report: '))

    def test_json_termination_and_escaping_follow_rust_writer(self):
        import json
        report = self.run_model()
        self.assertEqual(report.termination, 'GraphExhausted')
        self.assertEqual(json.loads(report.to_json())['search']['termination'], 'GraphExhausted')
        snake = replace(report, termination='graph_exhausted')
        self.assertIn('"termination":"GraphExhausted"', snake.to_json())
        manifest = replace(self.manifest(), bounds=(('env', 'a\bb\fc\x01d\x7f"\\\n\r\t'),))
        text = self.run_model(manifest=manifest).to_json()
        self.assertIn('"env":"a\\u0008b\\u000cc\\u0001d\x7f\\"\\\\\\n\\r\\t"', text)
        self.assertNotIn('\\b', text.replace('\\\\', ''))
        self.assertEqual(Report.from_json(text).manifest.bounds, manifest.bounds)
        from statelessness.assurance import _campaign_termination
        self.assertEqual(_campaign_termination('jobs_completed', None), 'Campaign::JobsCompleted')
        self.assertEqual(_campaign_termination('callback_error', 'sink "x"\n'),
                         'Campaign::CallbackError(ModelError("sink \\"x\\"\\n"))')
        self.assertLessEqual(len(_campaign_termination('worker_error', '\x01' * 8000).encode()), 8192)

    def test_merged_job_errors_are_bounded(self):
        report = replace(self.run_model(), callback_errors=('x' * 8190,))
        merged = merge_reports(report.manifest, report.metadata, [123456], [(123456, report)])
        merged.report.validate()
        self.assertTrue(merged.report.callback_errors[0].startswith('job 123456: x'))
        self.assertLessEqual(len(merged.report.callback_errors[0]), 4096)
        self.assertEqual(MergedReport.from_json(merged.to_json()), merged)

    def test_empty_catalog_cannot_pass(self):
        report = self.run_model(manifest=Manifest('counter', Provenance('a','b','c')))
        self.assertFalse(report.assess().adequate)

    def test_explicit_requirements_and_domain_helpers_are_immutable(self):
        from statelessness import DomainDescriptor
        base = Manifest('counter', Provenance('a', 'b', 'c'), bounds=(('manual', 'kept'),))
        updated = base.require('state', 'state').require('state', 'transition')
        descriptor = DomainDescriptor('requests', 'bounded identifiers', 12)
        updated = updated.add_domain(descriptor)
        self.assertEqual(base.required_properties, ())
        self.assertEqual(dict(base.bounds), {'manual': 'kept'})
        self.assertEqual(dict(updated.bounds), {'manual': 'kept', 'domain.requests.assumptions': 'bounded identifiers',
                                                'domain.requests.max_entries': '12'})
        replacement = updated.add_domain(DomainDescriptor('requests', 'new bound', 4))
        self.assertEqual(dict(replacement.bounds)['domain.requests.max_entries'], '4')
        self.assertEqual(dict(updated.bounds)['domain.requests.max_entries'], '12')
        with self.assertRaises(ModelError): updated.require('state', 'state')
        with self.assertRaises(ModelError): base.require('', 'state')
        with self.assertRaises(ModelError): base.add_domain(object())
        missing = self.run_model(manifest=updated)
        self.assertEqual({p.property.phase: p.status for p in missing.properties}, {'state': 'passed', 'transition': 'missing'})
        self.assertFalse(missing.assess().adequate)

    def test_derived_catalogs_distinguish_goal_scope_and_check_status(self):
        manifest = replace(self.manifest(),
            required_properties=(RequiredProperty('absent', 'state'), RequiredProperty('state', 'state')),
            input_families=(InputFamily('unused', 'not supported'), InputFamily('increment')),
            cover_goals=(CoverGoal('missing-edge'), CoverGoal('missing-history', 'history'), CoverGoal('two')))
        report = self.run_model(manifest=manifest)
        self.assertEqual([(p.property.id, p.status) for p in report.properties], [('absent', 'missing'), ('state', 'passed')])
        self.assertEqual([(f.name, f.status, f.reason) for f in report.families],
                         [('increment', 'observed', None), ('unused', 'excluded', 'not supported')])
        self.assertEqual([(g.goal.id, g.status) for g in report.coverage],
                         [('missing-edge', 'unreachable_under_declared_model'),
                          ('missing-history', 'not_reached_before_stop'), ('two', 'observed')])
        incomplete = replace(report, callback_errors=('coverage failed',))
        self.assertEqual(incomplete.coverage[0].status, 'not_reached_before_stop')

    def test_json_export_matches_rust_schema_and_retains_controls(self):
        import json
        report = self.run_model()
        raw = json.loads(report.to_json())
        self.assertEqual(raw['assurance_version'], 1)
        self.assertEqual(raw['manifest']['required_search_outcome'], 'exhausted_within_declared_bounds')
        self.assertEqual(raw['manifest']['input_families'], [{'name': 'increment', 'coverage': 'required'}])
        self.assertEqual(raw['search']['outcome'], 'GraphExhausted')
        self.assertEqual(raw['search']['checked_transitions'], 2)
        self.assertEqual(raw['properties'], [
            {'property': {'id': 'edge', 'phase': 'transition'}, 'status': 'Passed',
             'counts': {'passed': 2, 'failed': 0, 'skipped': 0, 'missing': 0}},
            {'property': {'id': 'state', 'phase': 'state'}, 'status': 'Passed',
             'counts': {'passed': 3, 'failed': 0, 'skipped': 0, 'missing': 0}}])
        self.assertEqual(raw['families'], [{'name': 'increment', 'status': 'Observed', 'reason': None}])
        self.assertEqual(raw['coverage'], [{'goal': {'id': 'two', 'scope': 'transition'}, 'status': 'Observed'}])
        merged = merge_reports(report.manifest, report.metadata, [7, 8], [(8, report)])
        encoded = json.loads(merged.to_json())
        self.assertEqual(encoded['missing_jobs'], [7])
        self.assertEqual(list(encoded['jobs']), ['8'])
        self.assertEqual(encoded['report']['search']['outcome'], 'Interrupted')
        invalid = replace(report, termination='nonsense')
        with self.assertRaises(ModelError): invalid.to_json()
        self.assertEqual(json.loads(report.manifest.to_json()), raw['manifest'])

    def test_json_export_preserves_exclusions_and_unicode(self):
        import json
        manifest = replace(self.manifest(), input_families=(InputFamily('increment', 'excluded\n理由'),))
        report = self.run_model(manifest=manifest)
        raw = json.loads(report.to_json())
        self.assertEqual(raw['manifest']['input_families'][0]['reason'], 'excluded\n理由')
        self.assertEqual(raw['families'][0]['status'], 'ExclusionViolated')
        self.assertEqual(raw['families'][0]['reason'], 'excluded\n理由')

    def test_json_import_validates_catalogs_and_rust_termination(self):
        import json
        report = self.run_model()
        self.assertEqual(Manifest.from_json(report.manifest.to_json()), report.manifest)
        self.assertEqual(Report.from_json(report.to_json()), report)
        raw = json.loads(report.to_json())
        raw['search']['termination'] = 'GraphExhausted'
        self.assertTrue(Report.from_json(json.dumps(raw)).assess().adequate)
        for mutation in ('status', 'coverage', 'bool_count', 'unknown', 'extra'):
            data = json.loads(report.to_json())
            if mutation == 'status': data['properties'][0]['status'] = 'Failed'
            elif mutation == 'coverage': data['observed_goals'] = []
            elif mutation == 'bool_count': data['search']['checked_transitions'] = True
            elif mutation == 'unknown': data['search']['termination'] = 'mystery'
            else: data['extra'] = 'field'
            with self.subTest(mutation=mutation), self.assertRaises(ModelError):
                Report.from_json(json.dumps(data))
        with self.assertRaises(ModelError): Report.from_json('{"a":1,"a":2}')
        with self.assertRaises(ModelError): Report.from_json(report.to_json(), max_bytes=20)
        with self.assertRaises(ModelError): Report.from_json(b'\xff')
        with self.assertRaises(ModelError): Manifest.from_json('{"assurance_version":NaN}')
        raw = json.loads(report.to_json())
        raw['metadata']['build'] = chr(0xd800)
        with self.assertRaises(ModelError): Report.from_json(json.dumps(raw))
        raw = json.loads(report.to_json())
        raw['unexpected'] = {chr(0xd800): 'x'}
        with self.assertRaises(ModelError): Report.from_json(json.dumps(raw))
        raw = json.loads(report.to_json())
        raw['metadata']['build'] = chr(0x1f600)
        self.assertEqual(Report.from_json(json.dumps(raw)).metadata.build, chr(0x1f600))

    def test_merged_json_import_validates_job_aggregates(self):
        import json
        report = self.run_model()
        merged = merge_reports(report.manifest, report.metadata, [1, 2, 3], [(1, report), (3, report)])
        self.assertEqual(MergedReport.from_json(merged.to_json()), merged)
        with self.assertRaises(ModelError): Report.from_json(merged.report.to_json())
        for mutation in ('count', 'missing', 'identity', 'jobcount', 'outcome', 'evidence', 'limit'):
            raw = json.loads(merged.to_json())
            if mutation == 'count': raw['report']['search']['checked_transitions'] += 1
            elif mutation == 'missing': raw['missing_jobs'] = [1, 2]
            elif mutation == 'identity': raw['jobs']['1']['metadata']['build'] = 'different'
            elif mutation == 'jobcount': raw['report']['search']['budgets']['reported_jobs'] = '1'
            elif mutation == 'outcome': raw['report']['search']['outcome'] = 'GraphExhausted'
            elif mutation == 'evidence': raw['jobs']['1']['properties'][0]['status'] = 'Failed'
            with self.subTest(mutation=mutation), self.assertRaises(ModelError):
                MergedReport.from_json(json.dumps(raw), max_jobs=2 if mutation == 'limit' else 100)
        raw = json.loads(merged.to_json())
        raw['jobs']['01'] = raw['jobs'].pop('1')
        with self.assertRaises(ModelError): MergedReport.from_json(json.dumps(raw))
        raw = json.loads(merged.to_json())
        raw['jobs']['1']['search']['termination'] = 'MergedJobs'
        with self.assertRaises(ModelError): MergedReport.from_json(json.dumps(raw))
        raw['jobs']['1']['search']['termination'] = 'Campaign::jobs_completed'
        with self.assertRaises(ModelError): MergedReport.from_json(json.dumps(raw))

    def test_merge_preserves_jobs_counts_and_missing(self):
        report = self.run_model()
        merged = merge_reports(report.manifest, report.metadata, [2, 1], [(2, report), (1, report)])
        self.assertEqual(tuple(id for id, _ in merged.jobs), (1, 2))
        self.assertEqual(merged.report.checked_transitions, 4)
        self.assertEqual(dict(merged.report.observed_properties)[RequiredProperty('state', 'state')].passed, 6)
        self.assertTrue(merged.report.assess().adequate)
        missing = merge_reports(report.manifest, report.metadata, [1, 2], [(1, report)])
        self.assertEqual((missing.missing_jobs, missing.report.outcome), ((2,), 'interrupted'))
        self.assertFalse(missing.report.assess().adequate)
        failed = replace(report, outcome='failed', termination='failure_found')
        self.assertEqual(merge_reports(report.manifest, report.metadata, [1, 2], [(1, failed)]).report.outcome, 'failed')
        self.assertEqual(merge_reports(report.manifest, report.metadata, [], []).report.outcome, 'not_started')

    def test_merge_rejects_bad_identity_and_evidence(self):
        report = self.run_model()
        manifest, metadata = report.manifest, report.metadata
        for expected, jobs in (([1, 1], []), ([1], [(2, report)]),
                               ([1], [(1, report), (1, report)]), ([True], []),
                               ([1], [(1, replace(report, metadata=replace(metadata, build='different')))]),
                               ([1], [(1, replace(report, manifest=replace(manifest, provenance=Provenance('x','b','c'))))]),
                               ([1], [(1, replace(report, checked_transitions=0))])):
            with self.subTest(expected=expected, jobs=jobs), self.assertRaises(ModelError):
                merge_reports(manifest, metadata, expected, jobs)

    def test_merge_saturates_counts_and_retains_job_errors(self):
        report = self.run_model()
        maximum = (1 << 64) - 1
        prop = RequiredProperty('state', 'state')
        report = replace(report, observed_properties=((prop, CheckCounts(passed=maximum)),),
                         checked_transitions=maximum, callback_errors=('problem',))
        merged = merge_reports(report.manifest, report.metadata, [1, 2], [(2, report), (1, report)])
        self.assertEqual(dict(merged.report.observed_properties)[prop].passed, maximum)
        self.assertEqual(merged.report.checked_transitions, maximum)
        self.assertEqual(merged.report.callback_errors, ('job 1: problem', 'job 2: problem'))
        self.assertFalse(merged.report.assess().adequate)

    def test_merge_caps_aggregate_without_discarding_job_evidence(self):
        report = self.run_model()
        left = tuple((RequiredProperty('a-' + str(n), 'state'), CheckCounts(passed=1)) for n in range(3000))
        right = tuple((RequiredProperty('b-' + str(n), 'state'), CheckCounts(passed=1)) for n in range(3000))
        first = replace(report, observed_properties=left, callback_errors=tuple('a-' + str(n) for n in range(600)))
        second = replace(report, observed_properties=right, callback_errors=tuple('b-' + str(n) for n in range(600)))
        merged = merge_reports(report.manifest, report.metadata, [1, 2], [(2, second), (1, first)])
        self.assertEqual(len(merged.report.observed_properties), 4096)
        self.assertEqual(len(merged.jobs[1][1].observed_properties), 3000)
        self.assertLessEqual(len(merged.report.callback_errors), 1025)
        self.assertIn('assurance merged error limit exceeded', merged.report.callback_errors)
        self.assertFalse(merged.report.assess().adequate)

    def test_cumulative_coverage_is_bounded_across_observations_and_jobs(self):
        families = tuple(InputFamily('family-' + str(n)) for n in range(6000))
        catalog = replace(self.manifest(), input_families=families, cover_goals=())
        class Many:
            def __init__(self): self.start = 0
            def observe(self, before, input, after, history, hits):
                for n in range(self.start, self.start + 3000): hits.family('family-' + str(n))
                self.start += 3000
        observer = Observer(catalog, Model().metadata(), Many())
        observer.transition(0, 1, Transition(1), (), (), ())
        observer.transition(0, 1, Transition(1), (), (), ())
        report = observer.finish('graph_exhausted', 'graph_exhausted')
        self.assertEqual(len(report.observed_families), 4096)
        self.assertIn('assurance retained coverage limit exceeded', report.callback_errors)
        self.assertFalse(report.assess().adequate)
        base = self.run_model(manifest=catalog)
        left = replace(base, observed_families=frozenset('family-' + str(n) for n in range(3000)), observed_goals=frozenset())
        right = replace(base, observed_families=frozenset('family-' + str(n) for n in range(3000, 6000)), observed_goals=frozenset())
        merged = merge_reports(catalog, base.metadata, [1, 2], [(1, left), (2, right)])
        self.assertEqual(len(merged.report.observed_families), 4096)
        self.assertIn('assurance merged coverage limit exceeded', merged.report.callback_errors)
        self.assertEqual(len(merged.jobs[1][1].observed_families), 3000)
        self.assertFalse(merged.report.assess().adequate)

    def test_report_rejects_contradictory_known_termination(self):
        report = replace(self.run_model(), termination='failure_found')
        self.assertFalse(report.assess().adequate)
        with self.assertRaises(ModelError):
            merge_reports(report.manifest, report.metadata, [1], [(1, report)])

    def test_unknown_termination_cannot_claim_completion(self):
        report = self.run_model()
        for outcome in ('graph_exhausted', 'cases_completed'):
            forged = replace(report, outcome=outcome, termination='unknown')
            self.assertFalse(forged.assess().adequate)
            with self.assertRaises(ModelError):
                merge_reports(report.manifest, report.metadata, [1], [(1, forged)])
        incomplete = replace(report, outcome='failed', termination='external failure')
        incomplete.validate()
        self.assertFalse(incomplete.assess().adequate)
        merged = merge_reports(report.manifest, report.metadata, [1], [(1, report)])
        merged.report.validate()
        self.assertTrue(merged.report.assess().adequate)

    def test_assured_wrappers_capture_outcome_and_model_errors(self):
        manifest = self.manifest()
        result = assured_enumerate(Model(), manifest, Coverage())
        self.assertEqual(result.search.termination, 'graph_exhausted')
        self.assertTrue(result.assurance.assess().adequate)

        self.assertEqual(dict(result.assurance.budgets)['max_depth'], '100')
        class Broken(Model):
            def step(self, state, input): raise ValueError('broken')
        result = assured_enumerate(Broken(), manifest, Coverage())
        self.assertIsNone(result.search)
        self.assertIsInstance(result.error, ModelError)
        self.assertEqual(result.assurance.outcome, 'failed')
        self.assertFalse(result.assurance.assess().adequate)
        class Generator(Model):
            def generate(self, state, rng): return 'increment'
            def step(self, state, input): return Transition(min(state + 1, 2))
        sampled = replace(manifest, required_search_outcome='completed')
        result = assured_fuzz(Generator(), sampled, Coverage(), FuzzConfig(cases=1, max_steps=2))
        self.assertEqual(result.search.termination, 'cases_completed')
        self.assertTrue(result.assurance.assess().adequate)


    def test_assured_guided_captures_identity_and_failure(self):
        class Generator(Model):
            def generate(self, state, rng): return 1
        class Feedback:
            def __init__(self, fail=False): self.calls, self.fail = 0, fail
            def metadata(self):
                self.calls += 1
                return FeedbackMetadata('test', 3, 'build')
            def initial_features(self, state, out): out.push(state)
            def transition_features(self, before, input, after, out):
                if self.fail: raise ValueError('feedback failed')
                out.push(after.state)
        manifest = replace(self.manifest(), required_search_outcome='completed')
        feedback = Feedback()
        result = assured_guided_fuzz(Generator(), manifest, Coverage(), feedback, FuzzConfig(cases=1, max_steps=2))
        self.assertEqual(feedback.calls, 1)
        self.assertTrue(result.assurance.assess().adequate)
        self.assertEqual(dict(result.assurance.budgets)['guidance.feedback.name'], 'test')
        self.assertEqual(dict(result.assurance.budgets)['guidance.feedback.version'], '3')
        feedback = Feedback(True)
        failed = assured_guided_fuzz(Generator(), manifest, Coverage(), feedback, FuzzConfig(cases=1, max_steps=2))
        self.assertEqual(feedback.calls, 1)
        self.assertIsInstance(failed.error, ModelError)
        self.assertEqual(failed.assurance.checked_transitions, 1)
        self.assertEqual(dict(failed.assurance.budgets)['guidance.feedback.build'], 'build')
        self.assertFalse(failed.assurance.assess().adequate)
        feedback = Feedback()
        cancelled = assured_guided_fuzz(Generator(), manifest, Coverage(), feedback,
                                       limits=RunLimits(cancellation=lambda: True))
        self.assertEqual(feedback.calls, 1)
        self.assertEqual(cancelled.assurance.outcome, 'interrupted')

    def test_assured_campaign_evidence_survives_delivery_failure(self):
        class Generator(Model):
            def generate(self, state, rng): return 1
        manifest = replace(self.manifest(), required_search_outcome='completed')
        config = CampaignConfig(jobs=2, fuzz=FuzzConfig(cases=1, max_steps=2))
        delivered = []
        result = assured_campaign(config, RunLimits(), manifest, Model().metadata(),
                                  lambda id: Generator(), lambda id, model: Coverage(), delivered.append)
        self.assertEqual(len(delivered), 2)
        self.assertTrue(result.assurance.report.assess().adequate)
        self.assertEqual(result.assurance.report.checked_transitions, 4)
        self.assertEqual(MergedReport.from_json(result.assurance.to_json()), result.assurance)
        with self.assertRaises(ModelError): Report.from_json(result.assurance.report.to_json())
        import json
        forged = json.loads(result.assurance.to_json())
        forged['report']['search']['budgets']['campaign.started'] = '0'
        forged['report']['search']['budgets']['campaign.finished'] = '0'
        with self.assertRaises(ModelError): MergedReport.from_json(json.dumps(forged))
        def bad_sink(job): raise ModelError('sink failed')
        failed = assured_campaign(config, RunLimits(), manifest, Model().metadata(),
                                  lambda id: Generator(), lambda id, model: Coverage(), bad_sink)
        self.assertEqual(failed.campaign.termination, 'callback_error')
        self.assertEqual(failed.assurance.missing_jobs, (1,))
        self.assertEqual(len(failed.assurance.jobs), 1)
        self.assertFalse(failed.assurance.report.assess().adequate)
        self.assertEqual(MergedReport.from_json(failed.assurance.to_json()), failed.assurance)
        import json
        forged = json.loads(failed.assurance.to_json())
        forged['report']['callback_errors'] = [e for e in forged['report']['callback_errors'] if not e.startswith('not every requested')]
        with self.assertRaises(ModelError): MergedReport.from_json(json.dumps(forged))

    def test_campaign_snapshots_caller_owned_manifest_before_sink(self):
        class Generator(Model):
            def generate(self, state, rng): return 1
        required = list(self.manifest().required_properties)
        catalog = replace(self.manifest(), required_properties=required, required_search_outcome='completed')
        config = CampaignConfig(jobs=2, fuzz=FuzzConfig(cases=1, max_steps=2))
        def sink(job): required.clear()
        result = assured_campaign(config, RunLimits(), catalog, Model().metadata(),
                                  lambda id: Generator(), lambda id, model: Coverage(), sink)
        self.assertEqual(required, [])
        self.assertEqual(len(result.assurance.report.manifest.required_properties), 2)
        self.assertTrue(result.assurance.report.assess().adequate)

    def test_direct_report_rejects_untyped_budgets_and_errors(self):
        report = self.run_model()
        for broken in (replace(report, budgets=(('a', '1'), ('a', '2'))),
                       replace(report, budgets=(('a', 1),)), replace(report, callback_errors=(42,)),
                       replace(report, observed_families={'increment'})):
            with self.subTest(broken=broken):
                self.assertFalse(broken.assess().adequate)
                with self.assertRaises(ModelError): merge_reports(report.manifest, report.metadata, [1], [(1, broken)])

    def test_assured_campaign_rejects_multiworker_and_tracks_factory_failure(self):
        manifest = replace(self.manifest(), required_search_outcome='completed')
        config = CampaignConfig(jobs=1, workers=2)
        with self.assertRaisesRegex(ModelError, 'explicit ProcessWorkers'):
            assured_campaign(config, RunLimits(), manifest, Model().metadata(), lambda id: Model(),
                             lambda id, model: Coverage(), lambda job: None)
        def bad_factory(id): raise ModelError('factory failed')
        result = assured_campaign(replace(config, workers=1), RunLimits(), manifest, Model().metadata(),
                                  bad_factory, lambda id, model: Coverage(), lambda job: None)
        self.assertEqual(result.assurance.missing_jobs, (0,))
        self.assertFalse(result.assurance.report.assess().adequate)

    def test_assurance_job_limit_rejects_before_callbacks(self):
        manifest = self.manifest()
        called = []
        huge = CampaignConfig(jobs=(1 << 64) - 1, fuzz=FuzzConfig(cases=0))
        def cancelled(): called.append('cancel'); return True
        def factory(id): called.append('factory'); return Model()
        def coverage(id, model): called.append('coverage'); return Coverage()
        def sink(job): called.append('sink')
        with self.assertRaisesRegex(ModelError, 'assurance job limit exceeded'):
            assured_campaign(huge, RunLimits(cancellation=cancelled), manifest, Model().metadata(), factory, coverage, sink)
        with self.assertRaisesRegex(ModelError, 'assurance job limit exceeded'):
            assured_guided_campaign(huge, RunLimits(cancellation=cancelled), manifest, Model().metadata(),
                                    GuidanceConfig(), factory, coverage, lambda id, model: WorkerFeedback(), sink)
        self.assertEqual(called, [])

        with self.assertRaisesRegex(ModelError, 'assurance job limit exceeded'):
            merge_reports(manifest, Model().metadata(), range(1 << 63), (), max_jobs=2)
        with self.assertRaisesRegex(ModelError, 'max_jobs'):
            merge_reports(manifest, Model().metadata(), (), (), max_jobs=True)
        merged = merge_reports(manifest, Model().metadata(), (3, 4), (), max_jobs=2)
        self.assertEqual(merged.missing_jobs, (3, 4))
        with self.assertRaisesRegex(ModelError, 'assurance job limit exceeded'):
            assured_campaign(CampaignConfig(jobs=2), RunLimits(), manifest, Model().metadata(), factory, coverage, sink, max_jobs=1)
        self.assertEqual(called, [])


    def test_aggregate_evidence_byte_budget(self):
        report = self.run_model()
        size = _evidence_size(report)
        with self.assertRaisesRegex(ModelError, 'evidence byte limit exceeded'):
            merge_reports(report.manifest, report.metadata, [1], [(1, report)], max_evidence_bytes=size - 1)
        merged = merge_reports(report.manifest, report.metadata, [1], [(1, report)], max_evidence_bytes=size)
        self.assertTrue(merged.report.assess().adequate)
        with self.assertRaises(ModelError):
            MergedReport.from_json(merged.to_json(), max_evidence_bytes=size - 1)
        self.assertEqual(MergedReport.from_json(merged.to_json(), max_evidence_bytes=size), merged)
        for policy, workers in ((None, 1), (ProcessWorkers(PickleInputTransfer()), 2)):
            with self.subTest(workers=workers):
                catalog = replace(self.manifest(), required_search_outcome='completed')
                config = CampaignConfig(jobs=2, workers=workers, fuzz=FuzzConfig(cases=1, max_steps=2))
                delivered = []
                result = assured_campaign(config, RunLimits(), catalog, Model().metadata(), worker_factory,
                                          worker_coverage, delivered.append, process=policy, max_evidence_bytes=0)
                self.assertEqual(len(delivered), 2)
                self.assertEqual(result.assurance.missing_jobs, (0, 1))
                self.assertIn('assurance evidence byte limit exceeded', result.assurance.report.callback_errors)
                self.assertFalse(result.assurance.report.assess().adequate)
                self.assertEqual(MergedReport.from_json(result.assurance.to_json()), result.assurance)
        with self.assertRaises(ModelError):
            merge_reports(report.manifest, report.metadata, [], [], max_evidence_bytes=True)
        huge_error = replace(report, callback_errors=('x' * 8193,))
        self.assertFalse(huge_error.assess().adequate)
        with self.assertRaises(ModelError):
            merge_reports(report.manifest, report.metadata, [1], [(1, huge_error)])

    def test_assured_process_campaign_transfers_evidence(self):
        manifest = replace(self.manifest(), required_search_outcome='completed')
        config = CampaignConfig(jobs=3, workers=2, fuzz=FuzzConfig(cases=1, max_steps=2))
        policy = ProcessWorkers(PickleInputTransfer())
        delivered = []
        result = assured_campaign(config, RunLimits(), manifest, Model().metadata(), worker_factory,
                                  worker_coverage, delivered.append, process=policy)
        self.assertTrue(result.assurance.report.assess().adequate)
        self.assertEqual(len(result.assurance.jobs), 3)
        guided = assured_guided_campaign(config, RunLimits(), manifest, Model().metadata(), GuidanceConfig(),
                                        worker_factory, worker_coverage, worker_feedback, delivered.append, process=policy)
        self.assertTrue(guided.assurance.report.assess().adequate)
        self.assertEqual(len(guided.assurance.jobs), 3)

    def test_assured_process_evidence_survives_input_encoding_failure(self):
        manifest = replace(self.manifest(), required_search_outcome='completed')
        config = CampaignConfig(jobs=2, workers=2, fuzz=FuzzConfig(cases=1, max_steps=2))
        result = assured_campaign(config, RunLimits(), manifest, Model().metadata(), failure_factory,
                                  worker_coverage, lambda job: None,
                                  process=ProcessWorkers(InputTransfer(fail_encode, unused_decode)))
        self.assertEqual(result.campaign.termination, 'worker_error')
        self.assertGreaterEqual(len(result.assurance.jobs), 1)
        self.assertTrue(all(job.checked_transitions > 0 for _, job in result.assurance.jobs))
        self.assertFalse(result.assurance.report.assess().adequate)

    def test_assured_process_evidence_survives_sink_and_decode_failure(self):
        manifest = replace(self.manifest(), required_search_outcome='completed')
        config = CampaignConfig(jobs=3, workers=2, fuzz=FuzzConfig(cases=1, max_steps=2))
        def fail_sink(job): raise ModelError('sink failed')
        result = assured_campaign(config, RunLimits(), manifest, Model().metadata(), worker_factory,
                                  worker_coverage, fail_sink, process=ProcessWorkers(PickleInputTransfer()))
        self.assertEqual(result.campaign.termination, 'callback_error')
        self.assertGreaterEqual(len(result.assurance.jobs), 1)
        self.assertFalse(result.assurance.report.assess().adequate)
        import pickle
        result = assured_campaign(config, RunLimits(), manifest, Model().metadata(), failure_factory,
                                  worker_coverage, lambda job: None,
                                  process=ProcessWorkers(InputTransfer(pickle.dumps, fail_decode)))
        self.assertEqual(result.campaign.termination, 'callback_error')
        self.assertGreaterEqual(len(result.assurance.jobs), 1)
        self.assertFalse(result.assurance.report.assess().adequate)

    def test_assured_guided_campaign_fresh_feedback_and_cleanup(self):
        class Generator(Model):
            def generate(self, state, rng): return 1
        made = []
        class Feedback:
            def __init__(self, job): self.job, self.calls, self.closed = job, 0, False
            def metadata(self):
                self.calls += 1
                return FeedbackMetadata('guided', 2, 'test')
            def initial_features(self, state, out): out.push(state)
            def transition_features(self, before, input, after, out): out.push(after.state)
            def campaign_close(self): self.closed = True
        def feedback_factory(id, model):
            feedback = Feedback(id); made.append(feedback); return feedback
        manifest = replace(self.manifest(), required_search_outcome='completed')
        config = CampaignConfig(jobs=2, fuzz=FuzzConfig(cases=1, max_steps=2))
        delivered = []
        result = assured_guided_campaign(config, RunLimits(), manifest, Model().metadata(), GuidanceConfig(),
                                        lambda id: Generator(), lambda id, model: Coverage(), feedback_factory, delivered.append)
        self.assertTrue(result.assurance.report.assess().adequate)
        self.assertEqual([(f.job, f.calls, f.closed) for f in made], [(0, 1, True), (1, 1, True)])
        for _, report in result.assurance.jobs:
            self.assertEqual(dict(report.budgets)['guidance.feedback.name'], 'guided')
        self.assertEqual(len(delivered), 2)

    def test_assured_guided_campaign_retains_failed_feedback_and_sink(self):
        class Generator(Model):
            def generate(self, state, rng): return 1
        class Broken:
            def metadata(self): return FeedbackMetadata('broken')
            def initial_features(self, state, out): pass
            def transition_features(self, *args): raise ValueError('feedback failed')
        manifest = replace(self.manifest(), required_search_outcome='completed')
        config = CampaignConfig(jobs=2, fuzz=FuzzConfig(cases=1, max_steps=2))
        def sink(job): raise ModelError('sink failed')
        result = assured_guided_campaign(config, RunLimits(), manifest, Model().metadata(), GuidanceConfig(),
                                        lambda id: Generator(), lambda id, model: Coverage(), lambda id, model: Broken(), sink)
        self.assertEqual(result.campaign.termination, 'callback_error')
        self.assertEqual(result.assurance.missing_jobs, (1,))
        job = result.assurance.jobs[0][1]
        self.assertEqual(job.outcome, 'failed')
        self.assertEqual(job.checked_transitions, 1)
        self.assertFalse(result.assurance.report.assess().adequate)

if __name__ == '__main__': unittest.main()
