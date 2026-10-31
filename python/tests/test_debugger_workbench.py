"""Independent branch isolation and concrete candidate identity goldens."""
import unittest
from dataclasses import replace
from statelessness import Artifact, ModelError
from statelessness.debugger import EvidenceViewer, InputPolicy, VerificationLimits
from statelessness.debugger_workbench import EnumeratedSession, CandidateToken, fork_verified
from statelessness.debugger_session import DebugError, SessionLimits
from test_debugger import Counter, fixture


class Domain(Counter):
    def inputs(self, state): return (1, 2, 3)


class WorkbenchTests(unittest.TestCase):
    def session(self, id='domain', model=None):
        return EnumeratedSession(id, Domain() if model is None else model, InputPolicy.enumerated())

    def test_token_bound_to_session_epoch_revision_value(self):
        first, second = self.session(), self.session()
        page = first.inputs(0, 1, 1)
        self.assertEqual(page.candidates[0].input, 2)
        self.assertEqual(page.next_offset, 2)
        with self.assertRaisesRegex(DebugError, 'another session'): second.select(page.candidates[0])
        with self.assertRaises(TypeError): CandidateToken()
        first.select(page.candidates[0])
        self.assertEqual(first.state, 2)
        with self.assertRaisesRegex(DebugError, 'stale'): first.select(page.candidates[0])

    def test_unrestricted_selection_still_revalidates_domain(self):
        class Mutable(Domain):
            domain = (1, 2)
            def inputs(self, state): return self.domain
        model = Mutable()
        session = EnumeratedSession('u', model, InputPolicy('out-of-domain-injection', unrestricted=True))
        token = session.inputs(0).candidates[1]
        model.domain = (1,)
        with self.assertRaisesRegex(DebugError, 'not permitted'): session.select(token)
        self.assertEqual(session.revision, 0)

    def test_fork_prefix_and_branch_isolation(self):
        viewer = EvidenceViewer(Artifact(fixture()))
        branch = fork_verified(viewer, 'branch', Counter(), 1, InputPolicy('positive', lambda m,s,i: i > 0))
        self.assertEqual(branch.session.state, 5)
        self.assertEqual(branch.session.revision, 0)
        branch.provenance.validate_parent(fixture())
        branch.session.step(0, 1)
        self.assertEqual(branch.session.state, 6)
        self.assertEqual(viewer.boundary(1).after, b'5')
        self.assertEqual(viewer.boundary(2).after, b'6')
        self.assertTrue(any(k == 'stateless.debug.parent.digest' for k,v in branch.session.export_trace().config.parameters))
        with self.assertRaisesRegex(ModelError, 'sidecar'):
            branch.provenance.validate_parent(replace(fixture(), initial_state=b'3'))
        with self.assertRaisesRegex(ModelError, 'before property failure'):
            fork_verified(viewer, 'bad', Counter(), 2, InputPolicy('positive', lambda *args: True))

    def test_fork_divergence_budget_and_environment(self):
        viewer, model = EvidenceViewer(Artifact(fixture())), Counter()
        with self.assertRaisesRegex(ModelError, 'budget'):
            fork_verified(viewer, 'bad', model, 1, InputPolicy('positive', lambda *args: True), verification=VerificationLimits(0))
        self.assertEqual(model.calls, 0)
        class Changed(Counter):
            def step(self, before, input): return super().step(before, input+1)
        with self.assertRaisesRegex(ModelError, 'not verified'):
            fork_verified(viewer, 'bad', Changed(), 1, InputPolicy('positive', lambda *args: True))

    def test_fork_requires_unambiguous_complete_parent_policy_before_replay(self):
        environment='stateless.debug.environment';origin='stateless.debug.origin'
        cases=[(((environment,'positive'),),'incomplete'),
               (((origin,'simulation'),),'incomplete'),
               (((environment,'positive'),(environment,'positive'),(origin,'simulation')),'ambiguous'),
               (((environment,'positive'),(origin,'unknown')),'unknown')]
        for params,message in cases:
            with self.subTest(message=message,params=params):
                trace=fixture();trace=replace(trace,config=replace(trace.config,parameters=params))
                model=Counter()
                with self.assertRaisesRegex(ModelError,message):
                    fork_verified(EvidenceViewer(Artifact(trace)),'bad',model,1,InputPolicy('out-of-domain-injection',unrestricted=True))
                self.assertEqual(model.calls,0)
        trace=fixture();trace=replace(trace,config=replace(trace.config,parameters=((environment,'out-of-domain-injection'),(origin,'out-of-domain-injection'))))
        model=Counter()
        with self.assertRaisesRegex(ModelError,'environment differs'):
            fork_verified(EvidenceViewer(Artifact(trace)),'bad',model,1,InputPolicy('out-of-domain-injection',lambda *args:True))
        self.assertEqual(model.calls,0)
        branch=fork_verified(EvidenceViewer(Artifact(trace)),'ok',Counter(),1,InputPolicy('out-of-domain-injection',unrestricted=True))
        self.assertEqual(branch.session.state,5)
        self.assertEqual(dict(replace(branch.provenance,parent_prefix_verified=False).parameters())['stateless.debug.parent_prefix_verified'],'false')

