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

    def test_hostile_exception_formatter_keeps_model_error_classification(self):
        class Hostile(Exception):
            def __str__(self): raise Hostile()
        class Broken(Counter):
            def step(self,state,input): raise Hostile()
        session=self.session(Broken())
        result=session.step(0,1)
        self.assertFalse(result.delivered)
        self.assertEqual(result.stop.kind,'model_error')
        self.assertEqual(result.stop.detail,'transition: exception detail unavailable')
        self.assertEqual((session.state,session.revision,session.sequence),(0,0,0))
        self.assertEqual(session.phase,'ended')

    def test_hostile_snapshot_exception_remains_owned_snapshot_failure(self):
        from statelessness.core import _copy, _SnapshotFailure
        class Hostile(Exception):
            def __str__(self): raise Hostile()
        class Value:
            def __deepcopy__(self,memo): raise Hostile()
        with self.assertRaisesRegex(_SnapshotFailure,'snapshot: exception detail unavailable'):
            _copy(Value())

    def test_hostile_recorder_error_does_not_lose_completed_turn(self):
        class Hostile(Exception):
            def __str__(self): raise Hostile()
        class Recorder:
            def observe_checked(self,checked): raise Hostile()
        session=self.session();session._recorder=Recorder()
        result=session.step(0,1)
        self.assertTrue(result.delivered)
        self.assertEqual((session.state,session.revision,session.sequence),(1,1,1))
        self.assertEqual((session.stop.kind,session.stop.detail),('recording_error','exception detail unavailable'))
        self.assertEqual(session.phase,'ended')

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

    def test_admission_captures_query_before_state_callbacks(self):
        query = [1]
        class State:
            def __deepcopy__(self, memo):
                query[0] = 9
                return self
        class Domain:
            def inputs(self, state): return iter(([1],))
        self.assertTrue(InputPolicy.enumerated().permits(Domain(), State(), query))
        query[0] = 1
        policy = InputPolicy('declared', lambda model, state, value: value == [1])
        self.assertTrue(policy.permits(Domain(), State(), query))

        class Uncopyable:
            def __deepcopy__(self, memo): raise ValueError('copy failed')
        class Empty:
            def inputs(self, state): return iter(())
        self.assertFalse(InputPolicy.enumerated().permits(Empty(), 0, Uncopyable()))
        with self.assertRaisesRegex(ModelError, 'copy failed'):
            InputPolicy.enumerated().permits(Domain(), 0, Uncopyable())

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
        self.assertEqual(session.sequence, 0)
        self.assertEqual(session.state, 0)
        self.assertIsNone(session.observation)

    def test_post_predicate_fault_commits_recorded_second_turn(self):
        from statelessness.monitor import RecorderOptions
        for fault in ('raise', 'nonbool'):
            with self.subTest(fault=fault):
                session = self.session(recorder_options=RecorderOptions())
                session.step(0, 1)
                seen = []
                def broken(actual):
                    seen.append((session.state, session.observation.sequence,
                                 actual.before, actual.transition.state))
                    if fault == 'raise': raise ModelError('predicate failed')
                    return 'truthy'
                session.add_breakpoint(broken, post=True)
                with self.assertRaisesRegex(ModelError, 'predicate failed|bool'):
                    session.step(1, 2)
                self.assertEqual(seen, [(1, 1, 1, 3)])
                self.assertEqual((session.sequence, session.revision, session.state), (2, 2, 3))
                self.assertEqual(session.phase, 'ended')
                self.assertEqual(session.stop.kind, 'observer_error')
                self.assertEqual(len(session.diagnostic_errors), 1)
                actual = session.observation
                self.assertEqual((actual.sequence, actual.before, actual.input, actual.transition.state), (2, 1, 2, 3))
                trace = session.export_trace()
                self.assertEqual(trace.initial_state, b'0')
                self.assertEqual(tuple(t.post_state for t in trace.steps), (b'1', b'3'))
                self.assertEqual(trace.steps[-1].input, b'2')
                with self.assertRaisesRegex(DebugError, 'ended'): session.step(2, 1)
                self.assertEqual(session.export_trace(), trace)

    def test_successful_post_predicate_sees_precommit_state(self):
        session = self.session()
        seen = []
        def predicate(actual):
            seen.append((session.state, session.observation, actual.transition.state))
            return True
        session.add_breakpoint(predicate, post=True)
        result = session.step(0, 2)
        self.assertEqual(seen, [(0, None, 2)])
        self.assertTrue(result.delivered)
        self.assertEqual(result.stop.kind, 'post_breakpoint')
        self.assertEqual((session.state, session.observation.transition.state, session.phase), (2, 2, 'paused'))

class ReadOwnershipTests(unittest.TestCase):
    def test_copy_callbacks_cannot_cancel_read_owner(self):
        owner = None
        attempts = []
        class Value:
            def __deepcopy__(value, memo):
                if owner is not None:
                    with self.assertRaises(DebugError): owner.cancel(owner.revision)
                    attempts.append(1)
                return Value()
            def __eq__(value, other): return isinstance(other, Value)
        class Model:
            def initial_state(self): return Value()
            def check_state(self, state): return []
            def check_transition(self, before, input, transition): return []
            def step(self, state, input): return Transition(state, [input])
            def inputs(self, state): return [Value()]
        from statelessness.debugger_workbench import EnumeratedSession
        owner = EnumeratedSession('copy-owner', Model(), InputPolicy('out-of-domain-injection', unrestricted=True))
        owner.state
        token = owner.inputs(0, limit=1).candidates[0]
        token.input
        owner.select(token)
        owner.observation
        self.assertGreater(len(attempts), 3)
        self.assertEqual(owner.revision, 1)
        self.assertIsNone(owner.stop)
