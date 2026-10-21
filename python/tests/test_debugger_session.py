"""Typed simulation's ownership and committed-turn boundary."""
import unittest
from statelessness import Check, ModelError, Transition
from statelessness.debugger import InputPolicy
from statelessness.debugger_session import DebugSession, DebugError, SessionLimits
from test_debugger import Counter


class SessionTests(unittest.TestCase):
    def session(self, model=None, **kwargs):
        return DebugSession('counter', Counter() if model is None else model, InputPolicy('positive', lambda m,s,i: i > 0), **kwargs)

    def test_pre_breakpoint_pending_and_post_commit(self):
        session = self.session()
        id = session.add_breakpoint(lambda s,i: i == 1)
        first = session.step(0, 1)
        self.assertFalse(first.delivered)
        self.assertEqual(first.stop.kind, 'pre_breakpoint')
        self.assertEqual(session.revision, 0)
        seen = []
        second = session.step(0, 1, observer=lambda o: seen.append((session.state, o.sequence)))
        self.assertTrue(second.delivered)
        self.assertEqual(seen, [(1, 1)])
        self.assertEqual(session.revision, 1)
        self.assertTrue(session.remove_breakpoint(id))
        with self.assertRaisesRegex(DebugError, 'stale'): session.step(0, 1)

    def test_policy_and_observer_faults(self):
        session = self.session()
        with self.assertRaisesRegex(DebugError, 'not permitted'): session.step(0, -1)
        self.assertEqual(session.revision, 0)
        def observer(o):
            with self.assertRaisesRegex(DebugError, 'owned'): session.step(session.revision, 1)
            raise ModelError('exporter offline')
        result = session.step(0, 2, observer=observer)
        self.assertTrue(result.delivered)
        self.assertEqual(session.state, 2)
        self.assertEqual(session.stop.kind, 'observer_error')
        self.assertEqual(session.phase, 'ended')

    def test_property_failure_and_checker_error_commit(self):
        session = self.session(state=5)
        self.assertEqual(session.step(0, 1).stop.kind, 'property_failure')
        self.assertEqual(session.state, 6)
        class Broken(Counter):
            def check_state(self, state):
                if state: raise ModelError('checker failed')
                return ()
        session = self.session(Broken())
        result = session.step(0, 1)
        self.assertTrue(result.delivered)
        self.assertEqual(session.state, 1)
        self.assertFalse(session.checks_complete)
        self.assertEqual(result.stop.kind, 'checker_error')

    def test_bounded_continue_and_counters(self):
        session = self.session(limits=SessionLimits(max_turns=2))
        self.assertEqual(len(session.run_bounded(0, (1,1,1), 2)), 2)
        self.assertEqual(session.sequence, 2)
        self.assertFalse(session.step(2, 1).delivered)
        self.assertEqual(session.phase, 'ended')

    def test_enumerated_admission_bounds(self):
        class Domain(Counter):
            def inputs(self, state): return iter((1,2,3))
        model = Domain()
        self.assertTrue(InputPolicy.enumerated().permits(model, 0, 1, 1))
        with self.assertRaisesRegex(ModelError, 'budget'): InputPolicy.enumerated().permits(model, 0, 3, 1)
        self.assertFalse(InputPolicy.enumerated().permits(model, 0, 9, 3))

    def test_recording_checks_once_and_no_codecs_default(self):
        from statelessness.monitor import RecorderOptions
        class Counted(Counter):
            def __init__(self): super().__init__(); self.checks = 0
            def check_state(self, state): self.checks += 1; return super().check_state(state)
        model = Counted()
        session = self.session(model, recorder_options=RecorderOptions())
        self.assertEqual(model.checks, 1)
        self.assertTrue(session.capabilities['exact_recording'])
        session.step(0, 1)
        self.assertEqual(model.checks, 2)
        trace = session.export_trace()
        self.assertEqual(trace.steps[0].post_state, b'1')
        self.assertIn(('stateless.debug.environment', 'positive'), trace.config.parameters)
        class TypedOnly:
            def initial_state(self): return []
            def step(self, state, input): return Transition(state + [input])
            def check_state(self, state): return ()
            def check_transition(self, *args): return ()
        session = DebugSession('typed', TypedOnly(), InputPolicy('out-of-domain-injection', unrestricted=True))
        self.assertTrue(session.step(0, 'hello').delivered)
        self.assertEqual(session.state, ['hello'])

    def test_constructor_order_and_iterator_ownership(self):
        model = Counter()
        def initial(): model.calls += 1; return 0
        model.initial_state = initial
        with self.assertRaises(ValueError): DebugSession('', model, InputPolicy('ok', lambda *args: True))
        self.assertEqual(model.calls, 1)
        session = self.session()
        def inputs():
            with self.assertRaisesRegex(DebugError, 'owned'): session.cancel(0)
            yield 1
        session.run_bounded(0, inputs(), 1)
        self.assertEqual(session.state, 1)

    def test_read_only_identity_and_callback_unwind_boundary(self):
        session = self.session()
        for field in ('sequence', 'revision', 'phase', 'stop', 'policy', 'limits', 'epoch', 'id'):
            with self.assertRaises(AttributeError): setattr(session, field, 0)
        session.add_breakpoint(lambda *args: 'truthy')
        with self.assertRaisesRegex(ModelError, 'bool'): session.step(0, 1)
        self.assertEqual(session.phase, 'ended')
        session = self.session()
        def broken(actual): raise ModelError('predicate failed')
        session.add_breakpoint(broken, post=True)
        with self.assertRaisesRegex(ModelError, 'predicate failed'): session.step(0, 1)
        self.assertEqual(session.phase, 'ended')
        self.assertEqual(session.sequence, 1)
        self.assertEqual(session.state, 0)
        self.assertIsNone(session.observation)
