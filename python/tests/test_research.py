"""Finite-fixture experiments: exact materialization, observables and bounded claims."""
from dataclasses import replace
import unittest
from statelessness import Check, Disposition, Metadata, ModelError, Transition
from statelessness import research as r


def proposal(*, established=True, properties=None):
    assumptions = r.Assumptions(established, 'explicit closed fixture', 'exact labeled equivalence',
        'ordered effects retained', 'named transition observations retained', 'exact queue order',
        'oracle history retained', 'represented logical time only')
    return r.Proposal('finite-example-projection',1,'native-test-proposal',assumptions,
        (r.RequiredProperty('state','state'),r.RequiredProperty('transition','transition')) if properties is None else properties)


def enabled(**changes): return r.ExperimentConfig(enabled=True,**changes)


class Toggle:
    def __init__(self, *, ordered=False, skipped=False, error=False):
        self.calls = []; self.ordered,self.skipped,self.error = ordered,skipped,error
    def metadata(self): self.calls.append(('metadata',)); return Metadata('toggle-research',build='native-test')
    def initial_state(self): self.calls.append(('initial',)); return 0
    def inputs(self,state): self.calls.append(('inputs',state)); return ((),)
    def step(self,state,input):
        self.calls.append(('step',state,input))
        if self.error and state == 1: raise ModelError('fixture error')
        return Transition(1-state,(2,1) if self.ordered and state == 1 else (1,2))
    def check_state(self,state):
        self.calls.append(('state',state))
        return (Check('state','skipped','not evaluated') if self.skipped else Check('state'),)
    def check_transition(self,before,input,transition):
        self.calls.append(('transition',before,input,transition.state)); return (Check('transition'),)


class Concurrent(Toggle):
    def inputs(self,state): self.calls.append(('inputs',state)); return ('left','right','observe')
    def step(self,state,input):
        self.calls.append(('step',state,input))
        return Transition(state | 1 if input == 'left' else state | 2 if input == 'right' else state)
    def check_transition(self,before,input,transition):
        self.calls.append(('transition',before,input,transition.state))
        return (Check('transition','failed','right machine published before left dependency')
                if before == 2 and input == 'observe' else Check('transition'),)


class ResearchTests(unittest.TestCase):
    def compare(self, model=None, states=(0,1), *, p=None, config=None, projection=lambda state: (), equality=None):
        kwargs = {} if equality is None else dict(equality=equality)
        return r.compare_equivalence(model or Toggle(),states,p or proposal(),config or enabled(),projection,**kwargs)

    def test_disabled_and_unestablished_do_no_execution_projection_or_equality(self):
        for config,p,status in ((r.ExperimentConfig(),proposal(),r.ExperimentStatus.DISABLED),
                                (enabled(),proposal(established=False),r.ExperimentStatus.ASSUMPTIONS_NOT_ESTABLISHED)):
            with self.subTest(status=status):
                model = Toggle()
                def forbidden(*args): raise AssertionError('disabled callback')
                report = self.compare(model,p=p,config=config,projection=forbidden,
                    equality=r.ResearchEquality(state=forbidden,input=forbidden,output=forbidden,projection=forbidden))
                self.assertEqual(report.status,status)
                self.assertEqual(model.calls,[('metadata',)])
                self.assertEqual(report.materialized_edges,0)
                self.assertEqual(report.compared_pairs,0)
                self.assertIsNone(report.exact); self.assertIsNone(report.reduced)
                self.assertEqual(tuple(report.counterexamples),())

    def test_toggle_agreement_materializes_each_edge_once_and_records_qualification(self):
        model = Toggle(); projections = []
        report = self.compare(model,projection=lambda state: projections.append(state) or ())
        self.assertEqual(report.status,r.ExperimentStatus.AGREES_WITHIN_DECLARED_FIXTURE)
        self.assertEqual([c for c in model.calls if c[0]=='step'],[('step',0,()),('step',1,())])
        self.assertEqual([c for c in model.calls if c[0]=='metadata'],[('metadata',)])
        self.assertEqual(projections,[0,1])
        self.assertEqual(tuple(report.exact.states),(0,1))
        self.assertEqual(tuple(report.reduced.states),(0,))
        self.assertEqual((report.materialized_edges,report.exact.edges,report.reduced.edges,report.compared_pairs),(2,2,1,1))
        parameters = r.qualification_parameters(report)
        self.assertEqual(parameters['research.production_pruning_enabled'],'false')
        self.assertEqual(parameters['research.status'],'AgreesWithinDeclaredFixture')
        self.assertEqual(parameters['research.model_build'],'native-test')
        self.assertEqual(parameters['research.proposal_build'],'native-test-proposal')

    def test_counterexample_ordered_effects_and_exact_prefixes(self):
        report = self.compare(Toggle(ordered=True))
        self.assertEqual(report.status,r.ExperimentStatus.COUNTEREXAMPLE)
        counterexample, = report.counterexamples
        self.assertEqual((counterexample.left_state,counterexample.right_state),(0,1))
        self.assertEqual(tuple(counterexample.left_prefix),())
        self.assertEqual(tuple(counterexample.right_prefix),((),))
        self.assertEqual(counterexample.reason,'ordered effects differ')

    def test_concurrent_projection_exposes_shortest_replayable_missing_failure(self):
        model = Concurrent()
        report = self.compare(model,states=(0,1,2,3),projection=int.bit_count)
        self.assertEqual(report.status,r.ExperimentStatus.COUNTEREXAMPLE)
        self.assertEqual(report.materialized_edges,12)
        self.assertEqual(sum(c[0]=='step' for c in model.calls),12)
        witness, = report.missing_violations
        self.assertEqual(witness.property,r.RequiredProperty('transition','transition'))
        self.assertEqual(tuple(witness.inputs),('right','observe'))
        self.assertEqual(tuple(report.reduced.violations),())
        state,failed = 0,False
        # This explicitly replays the exported witness; the harness itself did
        # not execute either traversal's transitions again.
        for input in witness.inputs:
            turn = model.step(state,input)
            failed |= any(check.status=='failed' for check in model.check_transition(state,input,turn))
            state = turn.state
        self.assertTrue(failed)
        exact = self.compare(Concurrent(),states=(0,1,2,3),projection=lambda state: state)
        self.assertEqual(exact.status,r.ExperimentStatus.AGREES_WITHIN_DECLARED_FIXTURE)
        self.assertEqual(exact.exact.violations,exact.reduced.violations)
        self.assertEqual(len(exact.exact.violations),1)

    def test_all_declared_unreachable_states_are_materialized_before_projection(self):
        class Fixture(Toggle):
            def step(self,state,input): self.calls.append(('step',state,input)); return Transition(state)
        model = Fixture(); projected = []
        report = self.compare(model,states=(0,1,2),projection=lambda state: projected.append(state) or state)
        self.assertEqual(report.status,r.ExperimentStatus.AGREES_WITHIN_DECLARED_FIXTURE)
        self.assertEqual(report.materialized_edges,3)
        self.assertEqual(tuple(report.exact.states),(0,))
        self.assertEqual(projected,[0,1,2])
        self.assertEqual([c[1] for c in model.calls if c[0]=='state'],[0,1,2])
        class UnreachableError(Fixture):
            def step(self,state,input):
                if state == 2: raise ModelError('unreachable fixture error')
                return super().step(state,input)
        projected.clear()
        report = self.compare(UnreachableError(),states=(0,1,2),projection=lambda state: projected.append(state) or state)
        self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertEqual(report.materialized_edges,2)
        self.assertIsNone(report.exact)
        self.assertEqual(projected,[])
        self.assertIn('unreachable fixture error',report.limitation)

    def test_domain_and_each_budget_preserve_incomplete_boundaries(self):
        for states,config,executed,has_reach in (((),enabled(),0,False),((0,1),enabled(max_states=1),0,False),
                ((0,1),enabled(max_edges=1),1,False),((0,1),enabled(max_equivalence_pairs=0),2,True),
                ((0,),enabled(),1,False)):
            with self.subTest(states=states,config=config):
                report = self.compare(states=states,config=config)
                self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
                self.assertEqual(report.materialized_edges,executed)
                self.assertEqual(report.exact is not None,has_reach)
        with self.assertRaises(ModelError): self.compare(states=(0,0,1))
        class OutsideInitial(Toggle):
            def initial_state(self): return 9
        report = self.compare(OutsideInitial())
        self.assertEqual(report.materialized_edges,0)
        self.assertIn('initial state is outside',report.limitation)

    def test_callback_failures_retain_exact_materialized_counter_and_no_partial_traversal(self):
        report = self.compare(Toggle(error=True))
        self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertEqual(report.materialized_edges,1)
        self.assertIsNone(report.exact); self.assertIsNone(report.reduced)
        self.assertIn('fixture error',report.limitation)
        class TransitionError(Toggle):
            def check_transition(self,*args): raise ModelError('transition checker error')
        report = self.compare(TransitionError())
        self.assertEqual(report.materialized_edges,1)
        self.assertIn('transition checker error',report.limitation)

    def test_missing_skipped_and_absent_required_properties_never_qualify(self):
        report = self.compare(Toggle(skipped=True))
        self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertEqual((report.exact.skipped_checks,report.reduced.skipped_checks),(3,2))
        self.assertEqual(tuple(report.missing_properties),())
        p = proposal(properties=(r.RequiredProperty('forgotten','state'),r.RequiredProperty('state','state')))
        report = self.compare(p=p)
        self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertEqual(tuple(report.missing_properties),(r.RequiredProperty('forgotten','state'),))
        self.assertEqual(self.compare(p=proposal(properties=())).status,r.ExperimentStatus.INCOMPLETE)

    def test_counterexample_wins_over_pair_limit_and_missing_failure_can_win_with_zero_pairs(self):
        class Three(Toggle):
            def step(self,state,input):
                self.calls.append(('step',state,input))
                return Transition((state+1)%3,(2,1) if state==1 else (1,2))
        report = self.compare(Three(),states=(0,1,2),config=enabled(max_equivalence_pairs=1))
        self.assertEqual(report.status,r.ExperimentStatus.COUNTEREXAMPLE)
        self.assertEqual(report.compared_pairs,1)
        self.assertEqual(report.counterexamples[0].reason,'ordered effects differ')
        report = self.compare(Concurrent(),states=(0,1,2,3),config=enabled(max_equivalence_pairs=0),projection=int.bit_count)
        self.assertEqual(report.status,r.ExperimentStatus.COUNTEREXAMPLE)
        self.assertEqual(report.compared_pairs,0)
        self.assertEqual(len(report.missing_violations),1)

    def test_observable_comparison_reasons_and_order_are_preserved(self):
        class StateOrder(Toggle):
            def check_state(self,state):
                return (Check('other'),Check('state')) if state else (Check('state'),Check('other'))
        class InputCounts(Toggle):
            def inputs(self,state): return ((),()) if state else ((),)
        class InputOrder(Toggle):
            def inputs(self,state): return ('b','a') if state else ('a','b')
        class ChangedDisposition(Toggle):
            def step(self,state,input):
                return Transition(1-state,(1,2),Disposition('rejected','reason') if state else Disposition())
        class ChangedTransitionCheck(Toggle):
            def check_transition(self,before,input,transition):
                return (Check('different'),) if before else (Check('transition'),)
        class SuccessorClass(Toggle):
            def step(self,state,input): return Transition((state+1)%3,(1,2))
        for model,states,projection,reason in ((StateOrder(),(0,1),lambda s:(),'state checks differ'),
                (InputCounts(),(0,1),lambda s:(),'enabled input counts differ'),
                (InputOrder(),(0,1),lambda s:(),'enabled input labels or order differ'),
                (ChangedDisposition(),(0,1),lambda s:(),'transition disposition differs'),
                (ChangedTransitionCheck(),(0,1),lambda s:(),'transition checks differ'),
                (SuccessorClass(),(0,1,2),lambda s:0 if s<2 else 1,'successor equivalence classes differ')):
            with self.subTest(reason=reason):
                report = self.compare(model,states,projection=projection)
                self.assertEqual(report.status,r.ExperimentStatus.COUNTEREXAMPLE)
                self.assertEqual(report.counterexamples[0].reason,reason)

    def test_required_property_identity_is_phase_aware_and_deterministically_ordered(self):
        expected = (r.RequiredProperty('a','state'),r.RequiredProperty('a','transition'),r.RequiredProperty('z','state'))
        self.assertEqual(tuple(sorted(reversed(expected))),expected)
        p = proposal(properties=(r.RequiredProperty('state','transition'),r.RequiredProperty('state','state')))
        report = self.compare(p=p)
        self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertEqual(tuple(report.missing_properties),(r.RequiredProperty('state','transition'),))

    def test_mutable_callback_values_and_exported_witnesses_are_owned(self):
        domain = [{'n':0},{'n':1}]
        shared_input,shared_output = {'event':1},{'effect':1}
        retained_checks = []
        class Mutable:
            def metadata(self): return Metadata('mutable-research',build='native-test')
            def initial_state(self): return {'n':0}
            def inputs(self,state): return (shared_input,)
            def step(self,state,input):
                n = state['n']; state['n'] = 999; input['event'] = 999
                return Transition({'n':1-n},(shared_output,))
            def check_state(self,state):
                retained_checks.append(state); state['n'] = 999
                return (Check('state'),)
            def check_transition(self,before,input,transition):
                return (Check('transition','failed','edge failure') if before['n']==1 else Check('transition'),)
        model = Mutable()
        def projection(state):
            state['n'] = 999; shared_output['effect'] = 999
            return []
        report = self.compare(model,domain,projection=projection,
            equality=r.ResearchEquality(state=lambda left,right:left['n']==right['n']))
        self.assertEqual(report.status,r.ExperimentStatus.COUNTEREXAMPLE)
        self.assertEqual(domain,[{'n':0},{'n':1}])
        self.assertEqual(shared_input,{'event':1})
        self.assertEqual(report.counterexamples[0].reason,'transition checks differ')
        witness, = report.missing_violations
        self.assertEqual(tuple(witness.inputs),({'event':1},{'event':1}))
        witness.inputs[0]['event'] = 7
        self.assertEqual(witness.inputs[1],{'event':1})
        self.assertEqual(shared_input,{'event':1})
        self.assertEqual(report.exact.violations[0].inputs[0],{'event':1})
        self.assertEqual(report.counterexamples[0].right_prefix[0],{'event':1})

    def test_native_configuration_proposal_and_comparator_admission(self):
        invalid = [
            lambda: enabled(max_states=-1), lambda: enabled(max_edges=True),
            lambda: enabled(max_equivalence_pairs=1.5), lambda:r.ExperimentConfig(enabled=1),
            lambda:replace(proposal(),name='  '), lambda:replace(proposal(),version=0),
            lambda:replace(proposal(),version=True), lambda:replace(proposal(),version=1<<32),
            lambda:replace(proposal(),build=''),
            lambda:replace(proposal(),assumptions=replace(proposal().assumptions,effects='  ')),
            lambda:proposal(properties=(r.RequiredProperty('state','state'),r.RequiredProperty('state','state'))),
            lambda:proposal(properties=(r.RequiredProperty(' ','state'),)),
            lambda:proposal(properties=(r.RequiredProperty('state','unknown'),)),
        ]
        for construct in invalid:
            with self.subTest(configuration=construct):
                with self.assertRaises((ModelError,ValueError,TypeError)):
                    value = construct()
                    self.compare(config=value) if isinstance(value,r.ExperimentConfig) else self.compare(p=value)
        for equality in (r.ResearchEquality(state=lambda left,right:1),r.ResearchEquality(input=lambda left,right:1)):
            report = self.compare(equality=equality)
            self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
            self.assertIn('bool',report.limitation)

    def test_all_native_equality_directions_support_explicit_value_semantics(self):
        counts = dict(state=0,input=0,output=0,projection=0)
        class Box:
            def __init__(self,n): self.n = n
        class Fixture(Toggle):
            def initial_state(self): return Box(0)
            def inputs(self,state): return ('step' if state.n==0 else 'STEP',)
            def step(self,state,input): return Transition(Box(1-state.n),('effect' if state.n==0 else 'EFFECT',))
        def compare(kind,left,right):
            counts[kind] += 1
            if kind=='state': return left.n==right.n
            if kind=='projection': return left['key']==right['key']
            return left.lower()==right.lower()
        equality = r.ResearchEquality(**{kind:(lambda left,right,kind=kind:compare(kind,left,right)) for kind in counts})
        report = self.compare(Fixture(),states=(Box(0),Box(1)),projection=lambda state:{'key':0},equality=equality)
        self.assertEqual(report.status,r.ExperimentStatus.AGREES_WITHIN_DECLARED_FIXTURE)
        self.assertTrue(all(count>0 for count in counts.values()))

    def test_append_checkers_and_lazy_inputs_are_preferred_and_leases_expire(self):
        retained,enumerated = [],[]
        class Preferred(Toggle):
            def inputs(self,state): raise AssertionError('legacy input collection called')
            def input_iter(self,state):
                enumerated.append(state)
                yield ()
            def check_state(self,state): raise AssertionError('legacy state checker called')
            def check_transition(self,*args): raise AssertionError('legacy transition checker called')
            def check_state_into(self,state,sink): retained.append(sink); sink.append(Check('state'))
            def check_transition_into(self,before,input,transition,sink): retained.append(sink); sink.append(Check('transition'))
        report = self.compare(Preferred())
        self.assertEqual(report.status,r.ExperimentStatus.AGREES_WITHIN_DECLARED_FIXTURE)
        self.assertEqual(enumerated,[0,1])
        self.assertEqual(len(retained),4)
        for sink in retained:
            with self.assertRaisesRegex(ModelError,'expired'): sink.append(Check('late'))

    def test_metadata_failure_is_a_primary_error_before_model_execution(self):
        class Broken(Toggle):
            def metadata(self): self.calls.append(('metadata',)); raise ModelError('metadata unavailable')
        model = Broken()
        with self.assertRaisesRegex(ModelError,'metadata unavailable'): self.compare(model)
        self.assertEqual(model.calls,[('metadata',)])

    def test_append_error_and_lazy_iterator_error_leave_only_completed_edge_count(self):
        retained = []
        class BadAppend(Toggle):
            def check_state_into(self,state,sink):
                retained.append(sink); sink.append(Check('prefix')); raise ModelError('append checker failure')
            def check_state(self,state): raise AssertionError('fallback after append error')
        model = BadAppend()
        report = self.compare(model)
        self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertEqual(report.materialized_edges,0)
        self.assertIn('append checker failure',report.limitation)
        self.assertFalse(any(c[0]=='step' for c in model.calls))
        with self.assertRaisesRegex(ModelError,'expired'): retained[0].append(Check('late'))
        class BadIterator(Toggle):
            def input_iter(self,state):
                yield ()
                raise ModelError('lazy input failure')
        report = self.compare(BadIterator())
        self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertEqual(report.materialized_edges,1)
        self.assertIsNone(report.exact)
        self.assertIn('lazy input failure',report.limitation)

    def test_disabled_payloads_are_not_snapshotted_and_enabled_snapshot_failure_is_incomplete(self):
        class Unsnapshotable:
            def __deepcopy__(self,memo): raise RuntimeError('fixture snapshot failure')
        model = Toggle(); payload = Unsnapshotable()
        report = self.compare(model,states=(payload,),config=r.ExperimentConfig())
        self.assertEqual(report.status,r.ExperimentStatus.DISABLED)
        self.assertEqual(model.calls,[('metadata',)])
        report = self.compare(model,states=(payload,))
        self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertEqual(report.materialized_edges,0)
        self.assertIn('fixture snapshot failure',report.limitation)

    def test_one_shortest_witness_per_property_and_phase_preserves_observation_order(self):
        class Multiple(Toggle):
            def check_state(self,state):
                return (Check('z','failed','checkpoint first'),Check('a','failed','second checkpoint'))
            def check_transition(self,before,input,transition):
                return (Check('a','failed','transition failure'),Check('z','failed','transition failure'))
        report = self.compare(Multiple(),projection=lambda state:state,p=proposal(properties=(
            r.RequiredProperty('z','state'),r.RequiredProperty('a','state'),
            r.RequiredProperty('a','transition'),r.RequiredProperty('z','transition'))))
        self.assertEqual(report.status,r.ExperimentStatus.AGREES_WITHIN_DECLARED_FIXTURE)
        self.assertEqual(tuple(w.property for w in report.exact.violations),(
            r.RequiredProperty('z','state'),r.RequiredProperty('a','state'),
            r.RequiredProperty('a','transition'),r.RequiredProperty('z','transition')))
        self.assertEqual(tuple(report.exact.violations[0].inputs),())
        self.assertEqual(tuple(report.exact.violations[1].inputs),())
        self.assertEqual(tuple(report.exact.violations[2].inputs),((),))
        self.assertEqual(tuple(report.exact.observed_properties),tuple(sorted((
            r.RequiredProperty('z','state'),r.RequiredProperty('a','state'),
            r.RequiredProperty('a','transition'),r.RequiredProperty('z','transition')))))

    def test_budget_limits_are_inclusive_and_zero_pair_allowance_is_not_a_blanket_failure(self):
        exact = self.compare(config=enabled(max_states=2,max_edges=2,max_equivalence_pairs=1))
        self.assertEqual(exact.status,r.ExperimentStatus.AGREES_WITHIN_DECLARED_FIXTURE)
        identity = self.compare(config=enabled(max_equivalence_pairs=0),projection=lambda state:state)
        self.assertEqual(identity.status,r.ExperimentStatus.AGREES_WITHIN_DECLARED_FIXTURE)
        self.assertEqual(identity.compared_pairs,0)
        model = Toggle()
        report = self.compare(model,config=enabled(max_edges=0))
        self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertEqual(report.materialized_edges,0)
        self.assertFalse(any(call[0]=='step' for call in model.calls))

    def test_nonreflexive_nan_and_custom_projection_equality_are_incomplete(self):
        cases = ((lambda state:float('nan'),r.ResearchEquality()),
                 (lambda state:state,r.ResearchEquality(projection=lambda left,right:left!=right)))
        for projection,equality in cases:
            with self.subTest(equality=equality):
                report = self.compare(projection=projection,equality=equality)
                self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
                self.assertEqual(report.materialized_edges,2)
                self.assertIn('projection equality must be reflexive',report.limitation)
                self.assertIsNone(report.exact); self.assertIsNone(report.reduced)

    def test_nonreflexive_and_asymmetric_exact_equality_are_incomplete(self):
        for callback, diagnostic in ((lambda left,right: left != right, 'reflexive'),
                                     (lambda left,right: left <= right, 'symmetric')):
            with self.subTest(diagnostic=diagnostic):
                report = self.compare(equality=r.ResearchEquality(state=callback))
                self.assertEqual(report.status, r.ExperimentStatus.INCOMPLETE)
                self.assertIn('state equality must be '+diagnostic, report.limitation)
                self.assertIsNone(report.exact); self.assertIsNone(report.reduced)

    def test_successor_matching_multiple_exact_states_is_incomplete(self):
        class Bridge(Toggle):
            def step(self,state,input): return Transition(1,(1,2))
        report=self.compare(Bridge(),states=(0,2),projection=lambda state:state,
                            equality=r.ResearchEquality(state=lambda left,right:abs(left-right)<=1))
        self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertIn('matches multiple declared states',report.limitation)

    def test_asymmetric_and_nontransitive_projection_equality_are_incomplete(self):
        class Three(Toggle):
            def step(self,state,input): return Transition((state+1)%3,(1,2))
        for callback, diagnostic in ((lambda left,right: left<=right,'symmetric'),
                                     (lambda left,right: abs(left-right)<=1,'transitive')):
            with self.subTest(diagnostic=diagnostic):
                report=self.compare(Three(),states=(0,1,2),projection=lambda state:state,
                                    equality=r.ResearchEquality(projection=callback))
                self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
                self.assertIn('projection equality must be '+diagnostic,report.limitation)

    def test_asymmetric_output_equality_cannot_qualify_changed_effects(self):
        class ChangedEffect(Toggle):
            def step(self,state,input): return Transition(1-state,(state,))
        report=self.compare(ChangedEffect(),equality=r.ResearchEquality(output=lambda left,right:left<=right))
        self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertIn('output equality must be symmetric',report.limitation)

    def test_projection_comparison_budget_is_fail_closed(self):
        from unittest.mock import patch
        with patch.object(r, '_MAX_PROJECTION_COMPARISONS', 2):
            report=self.compare()
        self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertIn('comparison budget reached',report.limitation)
        with patch.object(r, '_MAX_STATE_COMPARISONS', 1):
            report=self.compare()
        self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertIn('state equality comparison budget reached',report.limitation)

    def test_explicit_projection_budget_reports_usage_and_exact_boundary(self):
        def run(limit):
            return r.compare_equivalence_bounded(Toggle(),(0,1),proposal(),enabled(),lambda state:(),
                limits=r.ResearchComparisonLimits(limit))
        baseline=run(100)
        self.assertEqual(baseline.report.status,r.ExperimentStatus.AGREES_WITHIN_DECLARED_FIXTURE)
        self.assertGreater(baseline.projection_comparisons,0)
        exact=run(baseline.projection_comparisons)
        self.assertEqual(exact.report.status,baseline.report.status)
        self.assertEqual(exact.projection_comparisons,baseline.projection_comparisons)
        short=run(baseline.projection_comparisons-1)
        self.assertEqual(short.report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertEqual(short.projection_comparisons,baseline.projection_comparisons-1)
        self.assertEqual(short.comparison_limits.max_projection_comparisons,short.projection_comparisons)
        with self.assertRaises(ValueError): r.ResearchComparisonLimits(-1)
        state_exact=r.compare_equivalence_bounded(Toggle(),(0,1),proposal(),enabled(),lambda state:(),
            limits=r.ResearchComparisonLimits(100,baseline.state_comparisons))
        self.assertEqual(state_exact.report.status,baseline.report.status)
        state_short=r.compare_equivalence_bounded(Toggle(),(0,1),proposal(),enabled(),lambda state:(),
            limits=r.ResearchComparisonLimits(100,baseline.state_comparisons-1))
        self.assertEqual(state_short.report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertEqual(state_short.state_comparisons,baseline.state_comparisons-1)
        self.assertIn('state equality comparison budget reached',state_short.report.limitation)
        with self.assertRaises(ValueError): r.ResearchComparisonLimits(1,-1)

    def test_changing_projection_relation_cannot_reenqueue_exact_states_forever(self):
        comparisons = []
        def changing(left,right):
            comparisons.append((left,right))
            if len(comparisons)>32: raise ModelError('test comparison guard reached')
            # The two up-front self checks pass; subsequent answers violate
            # reflexivity/determinism. Exact-index visitation must still bound
            # traversal independently of this cooperative application relation.
            return len(comparisons)<=2
        report = self.compare(equality=r.ResearchEquality(projection=changing))
        self.assertLess(len(comparisons),32)
        self.assertNotIn('test comparison guard reached',report.limitation)
        self.assertEqual(report.status,r.ExperimentStatus.COUNTEREXAMPLE)
        self.assertEqual(tuple(report.reduced.states),(0,1))
        self.assertEqual(report.reduced.edges,2)

    def test_successfully_returned_transition_counts_before_snapshot_failure(self):
        class FailingTransition(Transition):
            def __deepcopy__(self,memo): raise RuntimeError('returned transition snapshot failed')
        class SnapshotFailure(Toggle):
            def step(self,state,input):
                self.calls.append(('step',state,input))
                return FailingTransition(1-state)
        model = SnapshotFailure()
        report = self.compare(model)
        self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
        self.assertEqual(report.materialized_edges,1)
        self.assertEqual(sum(call[0]=='step' for call in model.calls),1)
        self.assertIn('returned transition snapshot failed',report.limitation)
        self.assertIsNone(report.exact); self.assertIsNone(report.reduced)

    def test_rust_nonblank_control_and_bom_statements_are_accepted_without_normalization(self):
        assumption_fields = ('finite_domain','equivalence','effects','transition_properties',
                             'queue_order','oracle_history','visible_timing')
        for text in ('\x1c','\x1d','\x1e','\x1f','\ufeff'):
            proposals = [replace(proposal(),name=text),replace(proposal(),build=text),
                         proposal(properties=(r.RequiredProperty(text,'state'),))]
            proposals += [replace(proposal(),assumptions=replace(proposal().assumptions,**{field:text}))
                          for field in assumption_fields]
            for p in proposals:
                with self.subTest(text=repr(text),proposal=p):
                    model = Toggle()
                    report = self.compare(model,p=p,config=r.ExperimentConfig())
                    self.assertEqual(report.status,r.ExperimentStatus.DISABLED)
                    self.assertEqual(report.proposal,p)
                    self.assertEqual(model.calls,[('metadata',)])

    def test_rust_unicode_next_line_is_blank_for_proposal_assumptions_and_property_ids(self):
        text = '\x85'
        assumption_fields = ('finite_domain','equivalence','effects','transition_properties',
                             'queue_order','oracle_history','visible_timing')
        proposals = [replace(proposal(),name=text),replace(proposal(),build=text),
                     proposal(properties=(r.RequiredProperty(text,'state'),))]
        proposals += [replace(proposal(),assumptions=replace(proposal().assumptions,**{field:text}))
                      for field in assumption_fields]
        for p in proposals:
            with self.subTest(proposal=p):
                model = Toggle()
                with self.assertRaises(ModelError): self.compare(model,p=p,config=r.ExperimentConfig())
                self.assertEqual(model.calls,[])

class SharedResearchTests(unittest.TestCase):
    @staticmethod
    def corpus():
        import json,os
        from pathlib import Path
        path = Path(os.environ.get('STATELESS_RESEARCH_CORPUS',str(Path(__file__).resolve().parents[2] / 'conformance' / 'research-equivalence-v1.json')))
        return json.loads(path.read_text(encoding="utf-8"))

    def test_shared_table_fixtures_exact_reports_callbacks_and_qualification(self):
        from dataclasses import fields,is_dataclass
        corpus = self.corpus()
        counts = {'declared_states','materialized_edges','compared_pairs','edges','skipped_checks',
                  'max_states','max_edges','max_equivalence_pairs'}
        def normalize(value,name=None):
            if value is None: return None
            if name in counts: return str(value)
            if isinstance(value,r.ExperimentStatus): return value.value
            if is_dataclass(value): return {field.name:normalize(getattr(value,field.name),field.name) for field in fields(value)}
            if type(value) in (tuple,list): return [normalize(item) for item in value]
            if type(value) is dict: return {key:normalize(item,key) for key,item in value.items()}
            return value
        def merge(defaults,override):
            result = dict(defaults)
            for key,value in override.items():
                result[key] = merge(defaults[key],value) if type(value) is dict else value
            return result
        def checks(raw): return tuple(Check(item['id'],item['status'],item['details'] or '') for item in raw)
        for case in corpus['cases']:
            with self.subTest(case=case['case']):
                self.assertEqual(case['classification'],'shared_public_rust')
                callback_log = []
                scenario = case['scenario']
                rows = {row['state']:row for row in scenario['rows']}
                error_at = case.get('error_at')
                def visit(event):
                    callback_log.append(event)
                    if event == error_at: raise ModelError('fixture error at '+event)
                class Inputs:
                    def __init__(self,state): self.state,self.index = state,0
                    def __iter__(self): return self
                    def __next__(self):
                        visit('next:'+str(self.state)+':'+str(self.index))
                        inputs = rows[self.state]['inputs']
                        if self.index == len(inputs): raise StopIteration
                        input = inputs[self.index]['input']; self.index += 1
                        return input
                class TableModel:
                    def metadata(self): visit('metadata'); return Metadata(**corpus['defaults']['metadata'])
                    def initial_state(self): visit('initial'); return scenario['initial']
                    def input_iter(self,state): visit('inputs:'+str(state)); return Inputs(state)
                    def step(self,state,input):
                        visit('step:'+str(state)+':'+input)
                        edge = next(item for item in rows[state]['inputs'] if item['input']==input)
                        disposition = edge['disposition']
                        return Transition(edge['target'],tuple(edge['outputs']),Disposition(disposition['kind'],disposition['reason'] or ''))
                    def check_state_into(self,state,sink):
                        visit('state:'+str(state))
                        for check in checks(rows[state]['checks']): sink.append(check)
                    def check_transition_into(self,state,input,transition,sink):
                        visit('transition:'+str(state)+':'+input)
                        edge = next(item for item in rows[state]['inputs'] if item['input']==input)
                        for check in checks(edge['checks']): sink.append(check)
                def project(state):
                    visit('projection:'+str(state))
                    return scenario['keys'][scenario['states'].index(state)]
                actual = dict(error=None,report=None,callback_log=callback_log,qualification_parameters=None)
                try:
                    raw = merge(corpus['defaults']['proposal'],case.get('proposal_overrides',{}))
                    declared = r.Proposal(raw['name'],raw['version'],raw['build'],r.Assumptions(**raw['assumptions']),
                        tuple(r.RequiredProperty(**item) for item in raw['required_properties']))
                    raw = merge(corpus['defaults']['config'],case.get('config_overrides',{}))
                    config = r.ExperimentConfig(raw['enabled'],int(raw['max_states']),int(raw['max_edges']),int(raw['max_equivalence_pairs']))
                    report = r.compare_equivalence(TableModel(),scenario['states'],declared,config,project)
                    actual['report'] = normalize(report)
                    actual['qualification_parameters'] = r.qualification_parameters(report)
                except ModelError as error: actual['error'] = str(error)
                self.assertEqual(actual,case['expected'])

    def test_native_host_cases_execute_public_boundaries_or_declare_language_inapplicability(self):
        corpus = self.corpus()
        classified = []
        for case in corpus['native_admission_cases']:
            with self.subTest(case=case['case']):
                name = case['case']; model = Toggle()
                if case['kind']=='typescript_number':
                    self.assertEqual(case['expected'],'reject_or_explicit_language_inapplicable')
                    classified.append((name,'not_applicable_python_has_no_typescript_number'))
                    continue
                if name in ('negative-config-budget','config-over-u64','proposal-version-over-u32'):
                    with self.assertRaises((ModelError,ValueError,TypeError)):
                        if name=='proposal-version-over-u32':
                            r.compare_equivalence(model,(0,1),replace(proposal(),version=int(case['value'])),enabled(),lambda state:())
                        else:
                            r.compare_equivalence(model,(0,1),proposal(),enabled(max_edges=int(case['value'])),lambda state:())
                    self.assertEqual(model.calls,[])
                    classified.append((name,'public_rejection'))
                    continue
                if case['kind']=='metadata':
                    class BadMetadata(Toggle):
                        def metadata(self): raise RuntimeError('native metadata failure')
                    with self.assertRaisesRegex(ModelError,'native metadata failure'):
                        r.compare_equivalence(BadMetadata(),(0,1),proposal(),enabled(),lambda state:())
                    classified.append((name,'public_rejection'))
                    continue
                projection = lambda state:()
                equality = r.ResearchEquality()
                if name=='projection-nonreflexive-key': projection = lambda state:float('nan')
                elif name=='projection-callback-throws':
                    def projection(state): raise RuntimeError('native projection failure')
                elif case['kind']=='equality':
                    def equal(left,right): raise RuntimeError('native equality failure')
                    equality = r.ResearchEquality(state=equal)
                elif case['kind']=='iterator_next':
                    class BadNext(Toggle):
                        def input_iter(self,state):
                            def values():
                                raise RuntimeError('native next failure')
                                yield ()
                            return values()
                    model = BadNext()
                elif case['kind']=='async':
                    async def projection(state): return ()
                else: raise AssertionError('unhandled native fixture '+name)
                report = r.compare_equivalence(model,(0,1),proposal(),enabled(),projection,equality=equality)
                self.assertEqual(report.status,r.ExperimentStatus.INCOMPLETE)
                self.assertIsNone(report.exact); self.assertIsNone(report.reduced)
                classified.append((name,'incomplete_native_callback_failure'))
        self.assertEqual(len(classified),len(corpus['native_admission_cases']))
        self.assertEqual(sum(classification.startswith('not_applicable') for _,classification in classified),1)
