import unittest
from statelessness.core import Metadata,Check,Transition,Disposition,ModelError,_state_checks
from statelessness import WithOracle
from statelessness.lifecycle import *
from statelessness.network import Network,NetworkInput,NetworkState,Address,Route,Message,QueuePolicy
from statelessness.composition import Pair,PairInput,PairState,Address as PairAddress
from statelessness.value_codec import U8

class Spec:
    def metadata(self):return Metadata('independent-lifecycle')
    def input_events(self,i,d):return i
    def output_events(self,o):return o
class Child:
    def metadata(self):return Metadata('token')
    def initial_state(self):return 0
    def inputs(self,s):return (1,)
    def step(self,s,i):return Transition(s+i,(i,),Disposition('ignored','observed'))
    def check_state(self,s):return (Check('nonnegative','passed' if s>=0 else 'failed','' if s>=0 else 'negative'),)
class Wiring:
    def metadata(self):return Metadata('ring')
    def routes(self,o):return (Route((o.machine+1)%3,7,o.value),)
    def check_state(self,s):return (Check('sum'),)
    def messages(self,o):return (PairAddress('right' if o.side=='left' else 'left',o.value),)

class StatefulTests(unittest.TestCase):
    def test_pair_transition_outputs_are_mapped_in_order(self):
        from statelessness.core import CheckSink
        class Bad:
            def __deepcopy__(self, memo): raise ValueError('output copy failed')
        pair=Pair(Child(),Child(),Wiring())
        state=PairState(0,0)
        action=PairInput('local',PairAddress('left',1))
        with self.assertRaisesRegex(ModelError,'output copy failed'):
            pair.check_transition_into(state,action,Transition(state,(PairAddress('left',Bad()),PairAddress('right',2))),CheckSink([]))
        with self.assertRaisesRegex(ModelError,'wrong child output address'):
            pair.check_transition_into(state,action,Transition(state,(PairAddress('right',2),PairAddress('left',Bad()))),CheckSink([]))
        class Invalid:
            @property
            def side(self): raise AssertionError('invalid object inspected')
        with self.assertRaisesRegex(ModelError,'output copy failed'):
            pair.check_transition_into(state,action,Transition(state,(PairAddress('left',Bad()),Invalid())),CheckSink([]))

    def test_pair_transition_pins_check_before_output_copy(self):
        from statelessness.core import CheckSink
        class Checked(Child):
            def check_transition(self,*args): return (Check('original'),)
        child=Checked()
        class Replace:
            def __deepcopy__(self,memo):
                child.check_transition=lambda *args: (Check('replacement'),)
                return self
        state=PairState(0,0); pair=Pair(child,Child(),Wiring()); checks=[]
        pair.check_transition_into(state,PairInput('local',PairAddress('left',1)),Transition(state,(PairAddress('left',Replace()),)),CheckSink(checks))
        self.assertEqual([c.id for c in checks],['left::original'])

    def test_pair_transition_reads_original_input_tag_once(self):
        from statelessness.core import CheckSink
        reads=[]
        class OneShot(PairInput):
            def __getattribute__(self,name):
                if name=='kind' and self is action:
                    reads.append(name)
                    if len(reads)>1: raise AssertionError('original tag read twice')
                return super().__getattribute__(name)
        action=OneShot('local',PairAddress('left',1))
        state=PairState(0,0)
        Pair(Child(),Child(),Wiring()).check_transition_into(state,action,Transition(state),CheckSink([]))
        self.assertEqual(reads,['kind'])

    def test_pair_transition_without_checks_maps_output_once(self):
        from statelessness.core import CheckSink
        class Once:
            def __init__(self): self.copies=0
            def __deepcopy__(self, memo):
                self.copies+=1
                if self.copies>1: raise ValueError('copied twice')
                return self
        value=Once(); pair=Pair(Child(),Child(),Wiring()); state=PairState(0,0)
        pair.check_transition_into(state,PairInput('local',PairAddress('left',1)),Transition(state,(PairAddress('left',value),)),CheckSink([]))
        self.assertEqual(value.copies,1)

    def test_network_transition_outputs_are_mapped_in_order(self):
        from statelessness.core import CheckSink
        class Bad:
            def __deepcopy__(self, memo): raise ValueError('output copy failed')
        network=Network([(0,Child()),(1,Child())],Wiring())
        state=NetworkState(((0,0),(1,0)))
        action=NetworkInput('local',Address(0,1))
        with self.assertRaisesRegex(ModelError,'output copy failed'):
            network.check_transition_into(state,action,Transition(state,(Address(0,Bad()),Address(1,2))),CheckSink([]))
        with self.assertRaisesRegex(ModelError,'wrong network child output address'):
            network.check_transition_into(state,action,Transition(state,(Address(1,2),Address(0,Bad()))),CheckSink([]))

    def test_network_transition_without_checks_maps_output_once(self):
        from statelessness.core import CheckSink
        class Once:
            def __init__(self): self.copies=0
            def __deepcopy__(self, memo):
                self.copies+=1
                if self.copies>1: raise ValueError('copied twice')
                return self
        value=Once()
        network=Network([(0,Child())],Wiring())
        state=NetworkState(((0,0),))
        network.check_transition_into(state,NetworkInput('local',Address(0,1)),Transition(state,(Address(0,value),)),CheckSink([]))
        self.assertEqual(value.copies,1)

    def test_network_queue_transition_no_checker_does_not_copy(self):
        from statelessness.core import CheckSink
        copies=[]
        class Uncopyable:
            def __deepcopy__(self, memo):
                copies.append(1)
                raise AssertionError('unexpected copy')
        policy=QueuePolicy(max_pending=3,max_per_channel=3,allow_drop=True,allow_duplicate=True)
        network=Network([(0,Child())],Wiring(),policy)
        state=NetworkState(((0,Uncopyable()),),(Message(0,Route(0,0,Uncopyable())),))
        for kind in ('drop','duplicate'):
            result=[]
            network.check_transition_into(state,NetworkInput(kind,0),Transition(state),CheckSink(result))
            self.assertEqual(result,[])
            with self.assertRaisesRegex(ModelError,'queue action emitted'):
                network.check_transition_into(state,NetworkInput(kind,0),Transition(state,(Address(0,1),)),CheckSink([]))
        with self.assertRaisesRegex(ModelError,'pending index out of range'):
            network.check_transition_into(state,NetworkInput('drop',1),Transition(state),CheckSink([]))
        with self.assertRaisesRegex(ModelError,'machine IDs differ'):
            network.check_transition_into(state,NetworkInput('drop',0),Transition(NetworkState(())),CheckSink([]))
        self.assertEqual(copies,[])

    def test_network_queue_transition_resolves_wiring_hook_once(self):
        from statelessness.core import CheckSink
        class Once(Wiring):
            def __init__(self): self.lookups=0
            @property
            def check_transition(self):
                self.lookups+=1
                return (lambda *_: (Check('queue'),)) if self.lookups==1 else None
        policy=QueuePolicy(allow_drop=True)
        state=NetworkState(((0,0),),(Message(0,Route(0,0,1)),))
        wiring=Once(); network=Network([(0,Child())],wiring,policy)
        checks=[]
        network.check_transition_into(state,NetworkInput('drop',0),Transition(state),CheckSink(checks))
        self.assertEqual([c.id for c in checks],['global::queue'])
        self.assertEqual(wiring.lookups,1)
        class Broken(Wiring):
            @property
            def check_transition(self): raise RuntimeError('lookup failed')
        with self.assertRaisesRegex(ModelError,'composed transition: lookup failed'):
            Network([(0,Child())],Broken(),policy).check_transition_into(state,NetworkInput('drop',0),Transition(state),CheckSink([]))

    def test_pair_equality_captures_pending_before_child_comparison(self):
        pending = [1]
        class MutatingState:
            def __deepcopy__(self, memo): return self
            def __eq__(self, other):
                pending.append(2)
                return True
        pair = Pair(Child(), Child(), Wiring())
        a = PairState(MutatingState(), 0, (PairAddress('right', pending),))
        b = PairState(MutatingState(), 0, (PairAddress('right', [1]),))
        self.assertTrue(pair.equal_states(a, b))
        self.assertEqual(pending, [1, 2])

    def test_pair_equality_defers_unused_pending_copy_failure(self):
        class Uncopyable:
            def __deepcopy__(self, memo): raise ValueError('unavailable')
        pair = Pair(Child(), Child(), Wiring())
        a = PairState(1, 0, (PairAddress('left', Uncopyable()),))
        b = PairState(2, 0, ())
        self.assertFalse(pair.equal_states(a, b))
        self.assertFalse(pair.equal_states(a, PairState(1, 0, ())))
        self.assertFalse(pair.equal_states(PairState(1, Uncopyable()), PairState(2, 0)))
        with self.assertRaises(ModelError):
            pair.equal_states(a, PairState(1, 0, (PairAddress('left', 3),)))

    def test_pair_equality_captures_children_before_pending_copy(self):
        class Payload:
            def __deepcopy__(self, memo):
                object.__setattr__(b, 'right', 99)
                return 3
        pair = Pair(Child(), Child(), Wiring())
        a = PairState(1, 2, (PairAddress('left', Payload()),))
        b = PairState(1, 2, (PairAddress('left', 3),))
        self.assertTrue(pair.equal_states(a, b))
        self.assertEqual(b.right, 99)

    def test_pair_equality_captures_pending_addresses_before_copy(self):
        later = PairAddress('right', 7)
        class Payload:
            def __deepcopy__(self, memo):
                object.__setattr__(later, 'side', 'left')
                return 3
        pair = Pair(Child(), Child(), Wiring())
        a = PairState(1, 2, (PairAddress('left', Payload()), later))
        b = PairState(1, 2, (PairAddress('left', 3), PairAddress('right', 7)))
        self.assertTrue(pair.equal_states(a, b))
        self.assertEqual(later.side, 'left')

    def test_pair_step_captures_pending_structure_before_input_copy(self):
        address = PairAddress('right', 7)
        queue = [address]
        class Input:
            def __deepcopy__(self, memo):
                object.__setattr__(address, 'side', 'left')
                queue.clear()
                return 1
        pair = Pair(Child(), Child(), Wiring())
        result = pair.step(PairState(0, 0, queue), PairInput('local', PairAddress('left', Input())))
        self.assertEqual(result.state.pending, (PairAddress('right', 7), PairAddress('right', 1)))

    def test_pair_step_captures_pending_before_child_state_copy(self):
        for action in ('local', 'deliver'):
            with self.subTest(action=action):
                payload = [1]
                seen = []
                class State:
                    def __deepcopy__(self, memo):
                        payload.append(2)
                        return State()
                class Recorder(Child):
                    def step(self, state, value):
                        seen.append(value)
                        return Transition(state)
                class EmptyWiring(Wiring):
                    def messages(self, output): return ()
                pair = Pair(Recorder(), Recorder(), EmptyWiring())
                state = PairState(State(), 0, (PairAddress('right', payload),))
                input = PairInput('local', PairAddress('left', 1)) if action == 'local' else PairInput('deliver', 0)
                result = pair.step(state, input)
                if action == 'local':
                    self.assertEqual(result.state.pending[0].value, [1])
                else:
                    self.assertEqual(result.state.pending, ())
                    self.assertEqual(seen, [[1]])

    def test_network_equality_discards_failed_queue_copy_memo(self):
        observed=[]
        class Poison:
            def __deepcopy__(self, memo):
                memo[id(self)]='partial'
                raise ValueError('queue copy failed')
        poison=Poison()
        class State:
            def __init__(self, value): self.value=value
            def __deepcopy__(self, memo):
                observed.append(id(poison) not in memo)
                return State(self.value)
            def __eq__(self, other): return isinstance(other,State) and self.value==other.value
        left=NetworkState(((0,State(1)),),(Message(0,Route(0,0,poison)),))
        right=NetworkState(((0,State(2)),),(Message(0,Route(0,0,poison)),))
        self.assertFalse(Network([(0,Child())],Wiring()).equal_states(left,right))
        self.assertEqual(observed,[True,True])

    def test_network_equality_preserves_queue_machine_copy_aliases(self):
        from copy import deepcopy
        queue_copies=[]
        machine_copies=[]
        class Payload:
            def __deepcopy__(self, memo):
                result=Payload()
                memo[id(self)]=result
                queue_copies.append(result)
                return result
            def __eq__(self, other): return isinstance(other, Payload)
        class State:
            def __init__(self, payload): self.payload=payload
            def __deepcopy__(self, memo):
                payload=deepcopy(self.payload,memo)
                machine_copies.append(payload)
                return State(payload)
            def __eq__(self, other): return isinstance(other, State)
        a,b=Payload(),Payload()
        left=NetworkState(((0,State(a)),),(Message(0,Route(0,0,a)),))
        right=NetworkState(((0,State(b)),),(Message(0,Route(0,0,b)),))
        self.assertTrue(Network([(0,Child())],Wiring()).equal_states(left,right))
        self.assertEqual(len(queue_copies),2)
        self.assertIs(machine_copies[0],queue_copies[0])
        self.assertIs(machine_copies[1],queue_copies[1])

    def test_network_equality_short_circuits_structural_mismatch_before_copy(self):
        class Uncopyable:
            def __deepcopy__(self, memo): raise AssertionError('unexpected copy')
        network = Network([(0, Child())], Wiring())
        state = NetworkState(((0, 1),), (Message(0, Route(0, 0, Uncopyable())),))
        self.assertTrue(network.equal_states(state, state))
        self.assertFalse(network.equal_states(state, NetworkState(((1, 1),), state.pending)))
        self.assertFalse(network.equal_states(state, NetworkState(((0, 1),), ())))
        self.assertFalse(network.equal_states(state, NetworkState(((0, 2),), state.pending)))

    def test_network_equality_captures_pending_before_machine_comparison(self):
        payload = [1]
        class State:
            def __deepcopy__(self, memo): return self
            def __eq__(self, other):
                payload.append(2)
                return isinstance(other, State)
        network = Network([(0, Child())], Wiring())
        left = NetworkState(((0, State()),), (Message(0, Route(0, 0, [1])),))
        right = NetworkState(((0, State()),), (Message(0, Route(0, 0, payload)),))
        self.assertTrue(network.equal_states(left, right))
        self.assertEqual(payload, [1, 2])

    def test_network_rejects_forged_action_tags_before_copy(self):
        calls=[]
        class Impostor:
            def __eq__(self, other): return True
        class Uncopyable:
            def __deepcopy__(self, memo):
                calls.append(1)
                raise AssertionError('copy called for invalid action')
        network=Network([(0,Child())],Wiring())
        state=NetworkState(((0,Uncopyable()),))
        with self.assertRaisesRegex(ModelError,'invalid network action tag'):
            network.step(state,NetworkInput(Impostor(),Address(0,1)))
        with self.assertRaisesRegex(ModelError,'invalid network action tag'):
            network.step(state,object())
        from statelessness.core import CheckSink
        with self.assertRaisesRegex(ModelError,'invalid network action tag'):
            network.check_transition_into(state,NetworkInput(Impostor(),Address(0,1)),Transition(state),CheckSink([]))
        invalid=Address(0,1)
        object.__setattr__(invalid,'machine',False)
        with self.assertRaisesRegex(ModelError,'machine id'):
            network.step(state,NetworkInput('local',invalid))
        self.assertEqual(calls,[])

    def test_network_step_captures_pending_before_machine_copy_hooks(self):
        payload = [7]
        class MutatingState:
            def __deepcopy__(self, memo):
                payload.append(9)
                return 0
        class QuietWiring(Wiring):
            def routes(self, output): return ()
        policy = QueuePolicy(max_pending=4, max_per_channel=4, allow_duplicate=True)
        network = Network([(0, Child())], QuietWiring(), policy)
        state = NetworkState(((0, MutatingState()),), (Message(0, Route(0, 0, payload)),))
        result = network.step(state, NetworkInput('duplicate', 0))
        self.assertEqual([message.route.input for message in result.state.pending], [[7], [7]])
        self.assertGreater(len(payload), 1)

    def test_network_step_rejects_invalid_action_before_copy(self):
        calls = []
        class Uncopyable:
            def __deepcopy__(self, memo):
                calls.append(1)
                raise AssertionError('copy called for invalid action')
        network = Network([(0, Child())], Wiring())
        state = NetworkState(((0, Uncopyable()),))
        for action, message in ((NetworkInput('deliver', 0), 'pending index out of range'),
                                (NetworkInput('drop', 0), 'pending index out of range'),
                                (NetworkInput('local', Address(1, 0)), 'unknown network local machine')):
            with self.assertRaisesRegex(ModelError, message): network.step(state, action)
        self.assertEqual(calls, [])

    def test_pair_rejects_forged_address_tags_before_callbacks(self):
        from statelessness.core import _transition_checks
        class Counting(Child):
            def __init__(self): self.calls=0
            def step(self,s,i): self.calls+=1; return super().step(s,i)
            def inputs(self,s): self.calls+=1; return super().inputs(s)
            def check_state(self,s): self.calls+=1; return super().check_state(s)
        child=Counting(); pair=Pair(child,Child(),Wiring())
        invalid=PairAddress('left',1); object.__setattr__(invalid,'side','other')
        bad=PairState(0,0,(invalid,)); good=PairState(0,0)
        for operation in (
            lambda: pair.step(bad,PairInput('deliver',0)),
            lambda: pair.step(good,PairInput('local',invalid)),
            lambda: pair.inputs(bad),
            lambda: _state_checks(pair,bad),
            lambda: _transition_checks(pair,good,PairInput('local',PairAddress('left',1)),Transition(bad)),
            lambda: _transition_checks(pair,good,PairInput('local',PairAddress('left',1)),Transition(good,(invalid,))),
        ):
            with self.assertRaisesRegex(ModelError,'invalid address tag'): operation()
        self.assertEqual(child.calls,0)
        class InvalidWiring(Wiring):
            def messages(self,output): return (invalid,)
        with self.assertRaisesRegex(ModelError,'invalid address tag'):
            Pair(Child(),Child(),InvalidWiring()).step(good,PairInput('local',PairAddress('left',1)))
        class Impostor:
            def __eq__(self,other): return True
        object.__setattr__(invalid,'side',Impostor())
        with self.assertRaisesRegex(ModelError,'invalid address tag'):
            pair.step(good,PairInput('local',invalid))
        with self.assertRaisesRegex(ModelError,'invalid composition input tag'):
            pair.step(good,PairInput(Impostor(),PairAddress('left',1)))

    def test_composed_transition_child_cannot_mutate_global_disposition(self):
        from statelessness.core import _transition_checks
        class MutatingChild(Child):
            def check_transition(self, before, input, transition):
                object.__setattr__(transition.disposition, 'kind', 'ignored')
                object.__setattr__(transition.disposition, 'reason', 'changed')
                return ()
        class CapturingWiring(Wiring):
            def __init__(self): self.seen = None
            def check_transition(self, before, input, transition):
                self.seen = transition.disposition
                return ()
        for is_network in (False, True):
            wiring = CapturingWiring()
            if is_network:
                model = Network([(0, MutatingChild())], wiring)
                state = NetworkState(((0, 0),))
                input = NetworkInput('local', Address(0, 1))
            else:
                model = Pair(MutatingChild(), Child(), wiring)
                state = PairState(0, 0)
                input = PairInput('local', PairAddress('left', 1))
            disposition = Disposition('accepted', '')
            _transition_checks(model, state, input, Transition(state, (), disposition))
            self.assertEqual(wiring.seen, Disposition('accepted', ''))
            self.assertEqual(disposition, Disposition('accepted', ''))

    def test_lifecycle_and_oracle_isolate_disposition_from_classifier(self):
        class Mutating(Spec):
            def input_events(self, input, disposition):
                object.__setattr__(disposition, 'kind', 'accepted')
                object.__setattr__(disposition, 'reason', '')
                return ()
            def output_events(self, outputs): return ()
        monitor = LifecycleMonitor(Mutating())
        disposition = Disposition('ignored', 'original')
        monitor.advance(monitor.initial_state(), (), (), disposition)
        self.assertEqual(disposition, Disposition('ignored', 'original'))
        wrapped = WithOracle(Child(), monitor)
        actual = Transition(1, (7,), disposition)
        result = wrapped.observe_transition(wrapped.attach(0, monitor.initial_state()), 1, actual)
        self.assertEqual(actual.disposition, Disposition('ignored', 'original'))
        self.assertEqual(result.disposition, Disposition('ignored', 'original'))

    def test_lifecycle_event_budget_is_atomic_and_counts_both_classifiers(self):
        class Many(Spec):
            def __init__(self): self.pulls = 0; self.output_called = False; self.monitor = None
            def input_events(self, input, disposition):
                self.monitor.max_events = 100
                for n in range(10):
                    self.pulls += 1
                    yield Event('begin', n, 10)
            def output_events(self, outputs):
                self.output_called = True
                return ()
        for cls in (LifecycleMonitor, IndexedLifecycleMonitor):
            spec = Many(); monitor = cls(spec, max_events=2); spec.monitor = monitor
            before = monitor.initial_state()
            with self.subTest(cls=cls), self.assertRaisesRegex(ModelError, 'event limit exceeded'):
                monitor.advance(before, (), (), Disposition())
            self.assertEqual(spec.pulls, 3)
            self.assertFalse(spec.output_called)
            self.assertEqual(before, monitor.initial_state())
        class Split(Spec):
            def input_events(self, input, disposition): return (Event('begin', 1, 10),)
            def output_events(self, outputs): return (Event('begin', 2, 10),)
        monitor = LifecycleMonitor(Split(), max_events=1)
        with self.assertRaisesRegex(ModelError, 'event limit exceeded'):
            monitor.advance(monitor.initial_state(), (), (), Disposition())
        exact = LifecycleMonitor(Split(), max_events=2)
        self.assertEqual(len(exact.advance(exact.initial_state(), (), (), Disposition()).obligations), 2)
        self.assertNotEqual(monitor.metadata().build, exact.metadata().build)

    def test_composed_input_iterators_capture_state_at_call(self):
        class MutableChild(Child):
            def inputs(self, state): return (state['value'],)
        child = MutableChild()
        pair = Pair(child, child, Wiring())
        left, right = {'value': 1}, {'value': 2}
        pending = [PairAddress('left', 3)]
        stream = pair.inputs(PairState(left, right, pending))
        left['value'] = 10
        self.assertEqual(next(stream), PairInput('local', PairAddress('left', 1)))
        right['value'] = 20; pending.clear()
        self.assertEqual(list(stream), [PairInput('local', PairAddress('right', 2)), PairInput('deliver', 0)])

        network = Network([(0, child), (1, child)], Wiring())
        left, right = {'value': 1}, {'value': 2}
        pending = [Message(0, Route(1, 7, 'message'))]
        stream = network.inputs(NetworkState(((0, left), (1, right)), pending))
        left['value'] = 10
        self.assertEqual(next(stream), NetworkInput('local', Address(0, 1)))
        right['value'] = 20; pending.clear()
        self.assertEqual(list(stream), [NetworkInput('local', Address(1, 2)), NetworkInput('deliver', 0)])
        class NoCopy:
            def __deepcopy__(self, memo): raise AssertionError('invalid queue must fail before copy')
        bounded = Network([(0, child), (1, child)], Wiring(), QueuePolicy(max_pending=0))
        invalid = NetworkState(((0, NoCopy()), (1, {'value': 2})), (Message(0, Route(1, 7, 'x')),))
        with self.assertRaisesRegex(ModelError, 'pending bound'): bounded.inputs(invalid)

    def test_pair_inputs_never_copy_pending_payloads(self):
        class Uncopyable:
            def __deepcopy__(self, memo): raise AssertionError('queued payload copied')
        pending = [PairAddress('left', Uncopyable())]
        class MutatingState:
            def __deepcopy__(self, memo):
                pending.clear()
                return 0
        pair = Pair(Child(), Child(), Wiring())
        stream = pair.inputs(PairState(MutatingState(), 0, pending))
        self.assertEqual(list(stream), [PairInput('local', PairAddress('left', 1)),
                                       PairInput('local', PairAddress('right', 1)),
                                       PairInput('deliver', 0)])

    def test_pair_inputs_pins_child_enumerators(self):
        right = Child()
        class MutatingLeft(Child):
            def inputs(self, state):
                right.inputs = lambda state: (99,)
                return (2,)
        pair = Pair(MutatingLeft(), right, Wiring())
        self.assertEqual(list(pair.inputs(PairState(0, 0))),
                         [PairInput('local', PairAddress('left', 2)),
                          PairInput('local', PairAddress('right', 1))])

    def test_pair_inputs_freezes_queue_before_resolving_enumerators(self):
        pending = [PairAddress('left', 3)]
        class DescriptorChild(Child):
            @property
            def inputs(self):
                pending.clear()
                object.__setattr__(state, 'right', 99)
                return lambda value: (value,)
        state = PairState(1, 2, pending)
        pair = Pair(DescriptorChild(), DescriptorChild(), Wiring())
        self.assertEqual(list(pair.inputs(state)),
                         [PairInput('local', PairAddress('left', 1)),
                          PairInput('local', PairAddress('right', 2)),
                          PairInput('deliver', 0)])

        invalid = PairState(0, 0, [object()])
        pending[:] = [PairAddress('left', 3)]
        with self.assertRaisesRegex(ModelError, 'invalid address tag'):
            pair.inputs(invalid)
        self.assertEqual(len(pending), 1)

    def test_lifecycle_owns_events_before_later_classifier_runs(self):
        class AliasSpec(Spec):
            def __init__(self): self.key = ['original']
            def input_events(self, input, disposition):
                return (Event('begin', self.key, 10),)
            def output_events(self, outputs):
                self.key.clear()
                return ()
        spec = AliasSpec()
        monitor = LifecycleMonitor(spec)
        before = monitor.initial_state()
        after = monitor.advance(before, (), (), Disposition())
        self.assertEqual(before.obligations, [])
        self.assertEqual(spec.key, [])
        self.assertEqual(after.obligations[0][0], ['original'])
        spec.key.append('later')
        self.assertEqual(after.obligations[0][0], ['original'])

    def test_lifecycle_snapshots_outputs_before_input_classifier(self):
        outputs = [Event('begin', 'original', 10)]
        class AliasSpec(Spec):
            def input_events(self, input, disposition):
                outputs[:] = [Event('begin', 'changed', 10)]
                return ()
            def output_events(self, values): return values
        monitor = LifecycleMonitor(AliasSpec())
        after = monitor.advance(monitor.initial_state(), (), outputs, Disposition())
        self.assertEqual(after.obligations[0][0], 'original')
        self.assertEqual(outputs[0].key, 'changed')

        class BadCopy:
            def __deepcopy__(self, memo): raise ValueError('output copy failed')
        class BadInput(Spec):
            def input_events(self, input, disposition): raise ValueError('input failed')
        with self.assertRaisesRegex(ModelError, 'input failed'):
            LifecycleMonitor(BadInput()).advance(History(), (), [BadCopy()], Disposition())

    def test_lifecycle_rejects_untyped_restored_history_before_classifier(self):
        class Calls(Spec):
            calls=0
            def input_events(self,i,d): self.calls+=1; return ()
        spec=Calls(); monitor=IndexedLifecycleMonitor(spec,key_schema=U8)
        key=ScopedKey(0,0,7)
        malformed=(History(obligations=[(key,Obligation(-1))]),
                   History(obligations=[(key,Obligation(1,cancelled=1))]),
                   History(resources=[(key,1)]),
                   History(violations=[Violation(key,'code','event',-1,None)]),
                   History(violations=[Violation(key,'code','event',0,Obligation(1,settled=1))]))
        for history in malformed:
            with self.subTest(history=history):
                with self.assertRaises(ModelError): monitor.advance(history,(),(),Disposition())
                with self.assertRaises(ModelError): monitor.encode_history(history)
        self.assertEqual(spec.calls,0)
        legacy=LifecycleMonitor(spec,key_schema=U8)
        with self.assertRaises(ModelError): legacy.encode_history(History(resources=[(7,1)]))

    def test_legacy_lifecycle_rejects_unrepresentable_indexed_state(self):
        monitor = LifecycleMonitor(Spec(), key_schema=U8)
        for history in (History(retired_before=1),
                        History(obligations=[(7, Obligation(1, settled=True, published=True))])):
            with self.subTest(history=history):
                with self.assertRaises(ModelError): monitor.encode_history(history)
                with self.assertRaises(ModelError): monitor.advance(history, (), (), Disposition())

    def test_legacy_codec_encodes_representable_history_above_monitor_limit(self):
        limited = LifecycleMonitor(Spec(), max_history=1, key_schema=U8)
        permissive = LifecycleMonitor(Spec(), max_history=2, key_schema=U8)
        history = History(obligations=[(7, Obligation(10)), (8, Obligation(11))])
        encoded = limited.encode_history(history)
        self.assertEqual(permissive.decode_history(encoded), history)
        with self.assertRaises(ModelError): limited.decode_history(encoded)
        with self.assertRaises(ModelError): limited.advance(history, (), (), Disposition())

    def test_legacy_lifecycle_duplicate_restored_keys_use_all_obligations(self):
        monitor = LifecycleMonitor(Spec(), key_schema=U8)
        pending = History(obligations=[(7, Obligation(10, settled=True)),
                                       (7, Obligation(10))], resources=[(7, True)])
        restored = monitor.decode_history(monitor.encode_history(pending))
        released = monitor.advance(restored, [Event('release', 7)], [], Disposition())
        self.assertIn('resource released before settlement', released.violations)
        publishable = History(obligations=[(7, Obligation(10)),
                                           (7, Obligation(10, settled=True))])
        published = monitor.advance(publishable, [Event('publish', 7)], [], Disposition())
        self.assertNotIn('stale or premature publication', published.violations)

    def test_network_rejects_non_u32_address_route_and_state_ids(self):
        n=Network([(0,Child()),(1,Child()),(2,Child())],Wiring())
        for invalid in (True,False,-1,1<<32,1.0):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError): Address(invalid,1)
                with self.assertRaises(ValueError): Route(invalid,0,1)
                with self.assertRaises(ValueError): Route(1,invalid,1)
                with self.assertRaises(ValueError): Message(invalid,Route(1,0,1))
        with self.assertRaises(ValueError): n.validate(NetworkState(((False,0),(1,0),(2,0))))
        with self.assertRaisesRegex(ModelError,'invalid network route'): Message(0,object())
        self.assertEqual(n.initial_state().machines,((0,0),(1,0),(2,0)))

    def test_deadlines_none_and_prior(self):
        m=LifecycleMonitor(Spec())
        h=m.advance(m.initial_state(),[Event('begin',None,0)],[],Disposition())
        self.assertEqual(m.check_state(h)[0].status,'failed')
        h=m.advance(m.initial_state(),[Event('begin',7,10),Event('tick',time=11),Event('cancel',7)],[],Disposition())
        self.assertIn('required settlement missing before time advance',h.violations)
        h=m.advance(m.initial_state(),[Event('begin',7,10),Event('tick',time=10)],[Event('settle',7)],Disposition())
        self.assertEqual(m.check_state(h)[0].status,'passed')
    def test_legacy_and_indexed_codecs(self):
        for cls,size in ((LifecycleMonitor,20),(IndexedLifecycleMonitor,29)):
            m=cls(Spec(),key_schema=U8);h=m.initial_state();b=m.encode_history(h)
            self.assertEqual(b,(b'\1'+bytes(28)) if cls is IndexedLifecycleMonitor else bytes(20));self.assertEqual(m.decode_history(b),h)
            k=ScopedKey(2,3,7) if cls is IndexedLifecycleMonitor else 7
            h=m.advance(h,[Event('begin',k,10)],[],Disposition());b=m.encode_history(h)
            self.assertEqual(len(b),57 if cls is IndexedLifecycleMonitor else 31);self.assertEqual(m.decode_history(b),h)
            with self.assertRaises(ModelError):m.decode_history(b+b'\0')
    def test_fences_reuse_retention_restart(self):
        m=IndexedLifecycleMonitor(Spec(),RetentionLimits(2,2,8),key_schema=U8);h=m.initial_state()
        for epoch in range(1000):
            k=ScopedKey(epoch,0,7)
            h=m.advance(h,[Event('begin',k,10),Event('acquire',k)],[Event('settle',k),Event('release',k),Event('fence',time=epoch+1)],Disposition())
            self.assertEqual(h.retention()['historical_obligations'],0)
        h=m.decode_history(m.encode_history(h));h=m.advance(h,[Event('settle',ScopedKey(0,0,7))],[],Disposition())
        self.assertEqual(h.violations[-1].code,'retired_epoch_reference')
        h=m.advance(m.initial_state(),[Event('begin',ScopedKey(0,0,1),10),Event('fence',time=1)],[],Disposition())
        self.assertEqual(h.retired_before,0);self.assertEqual(h.violations[-1].code,'fence_pending_obligation')
    def test_indexed_rust_debug_diagnostics_opt_in(self):
        import json
        m=IndexedLifecycleMonitor(Spec(),key_schema=U8,rust_debug_key=str,
            rust_debug_string=lambda s:json.dumps(s,ensure_ascii=False),rust_debug_identity='u8-json-ascii-v1')
        key=ScopedKey(2,3,7)
        h=History(now=4,obligations=[(key,Obligation(9))],resources=[(key,True)],
                  violations=[Violation(key,'duplicate_publication','publish',4,Obligation(9,False,True,True)),
                              Violation(None,'time_backwards','tick',4,None)])
        checks=m.check_state(h)
        retention=('RetentionStatus { active_obligations: 1, historical_obligations: 0, acquired_resources: 1, '
                   'historical_resources: 0, oldest_unresolved: Some((ScopedKey { epoch: 2, generation: 3, key: 7 }, 9)), retired_before: 0 }')
        self.assertEqual(checks[0].details,
            'violations=[Violation { key: Some(ScopedKey { epoch: 2, generation: 3, key: 7 }), code: "duplicate_publication", event: "publish", observed_at: 4, prior: Some(Obligation { deadline: 9, cancelled: false, settled: true, published: true }) }, '
            'Violation { key: None, code: "time_backwards", event: "tick", observed_at: 4, prior: None }]; retention='+retention)
        self.assertEqual(checks[1].details,'finite history: '+retention)
        self.assertEqual(m.check_state(m.decode_history(m.encode_history(h))),checks)
        self.assertIn(';diagnostics=rust-debug-v1;formatter=16:u8-json-ascii-v1',m.metadata().name)
        self.assertIn(';diagnostics=native-codes-v1',IndexedLifecycleMonitor(Spec()).metadata().name)
        h.violations=[]; h.now=9
        self.assertTrue(m.check_state(h)[0].details.startswith('violations=[]; retention='))
        with self.assertRaises(ModelError): IndexedLifecycleMonitor(Spec(),rust_debug_key=str)
        broken=IndexedLifecycleMonitor(Spec(),rust_debug_key=lambda _:1,rust_debug_string=str,rust_debug_identity='bad')
        with self.assertRaisesRegex(ModelError,'must return a string'): broken.check_state(h)
        def explode(_): raise ValueError('formatter failed')
        broken.rust_debug_key=explode
        with self.assertRaisesRegex(ModelError,'formatter failed'): broken.check_state(h)
    def test_indexed_rust_debug_reference_strings(self):
        import json
        debug=lambda s:json.dumps(s,ensure_ascii=False)
        m=IndexedLifecycleMonitor(Spec(),rust_debug_key=debug,rust_debug_string=debug,rust_debug_identity='reference')
        k=ScopedKey(2,3,'a"\n')
        h=History(now=5,retired_before=1,
            obligations=[(k,Obligation(8)),(ScopedKey(3,0,'b'),Obligation(9,True)),(ScopedKey(4,0,'c'),Obligation(10,False,True))],
            resources=[(ScopedKey(i,0,'r'),i<4) for i in range(1,8)],
            violations=[Violation(None,'bad\n"\\','é\t',4,None),Violation(k,'duplicate','publish',5,Obligation(9,False,True,True))])
        expected=('violations=[Violation { key: None, code: "bad\\n\\"\\\\", event: "é\\t", observed_at: 4, prior: None }, '
            'Violation { key: Some(ScopedKey { epoch: 2, generation: 3, key: "a\\"\\n" }), code: "duplicate", event: "publish", observed_at: 5, '
            'prior: Some(Obligation { deadline: 9, cancelled: false, settled: true, published: true }) }]; '
            'retention=RetentionStatus { active_obligations: 1, historical_obligations: 2, acquired_resources: 3, historical_resources: 4, '
            'oldest_unresolved: Some((ScopedKey { epoch: 2, generation: 3, key: "a\\"\\n" }, 8)), retired_before: 1 }')
        self.assertEqual(m.check_state(h)[0].details,expected)
    def test_indexed_rust_debug_actual_monitor_reference(self):
        import json
        m=IndexedLifecycleMonitor(Spec(),rust_debug_key=str,
            rust_debug_string=lambda s:json.dumps(s,ensure_ascii=False),rust_debug_identity='u8')
        k=ScopedKey(2,3,7)
        h=m.advance(m.initial_state(),[Event('begin',k,9),Event('begin',k,9)],[],Disposition())
        retention=('RetentionStatus { active_obligations: 1, historical_obligations: 0, acquired_resources: 0, '
            'historical_resources: 0, oldest_unresolved: Some((ScopedKey { epoch: 2, generation: 3, key: 7 }, 9)), retired_before: 0 }')
        self.assertEqual(m.check_state(h),(
            Check('lifecycle.indexed.safety','failed','violations=[Violation { key: Some(ScopedKey { epoch: 2, generation: 3, key: 7 }), '
                'code: "key_reused", event: "begin", observed_at: 0, prior: Some(Obligation { deadline: 9, cancelled: false, settled: false, published: false }) }]; retention='+retention),
            Check('lifecycle.indexed.obligations','skipped','finite history: '+retention)))
    def test_duplicate_publication_difference(self):
        for cls in (LifecycleMonitor,IndexedLifecycleMonitor):
            m=cls(Spec());k=ScopedKey(0,0,1) if m.indexed else 1
            h=m.advance(m.initial_state(),[Event('begin',k,10)],[Event('settle',k),Event('publish',k),Event('publish',k)],Disposition())
            self.assertEqual(bool(h.violations),m.indexed)
    def test_network_fifo_cycle_drop_and_saturation(self):
        n=Network([(2,Child()),(0,Child()),(1,Child())],Wiring(),QueuePolicy(max_pending=2,max_per_channel=2,allow_drop=True,allow_duplicate=True))
        s=n.initial_state();t=n.step(s,NetworkInput('local',Address(0,1)));self.assertEqual(s.pending,());self.assertEqual(t.disposition.kind,'ignored');s=t.state
        self.assertEqual([c.id for c in _state_checks(n,s)],['machine/0::nonnegative','machine/1::nonnegative','machine/2::nonnegative','global::sum'])
        d=n.step(s,NetworkInput('duplicate',0)).state
        with self.assertRaisesRegex(ModelError,'FIFO'):n.step(d,NetworkInput('deliver',1))
        with self.assertRaisesRegex(ModelError,'incomplete'):n.step(d,NetworkInput('duplicate',0))
        self.assertEqual(len(n.step(d,NetworkInput('drop',1)).state.pending),1)
        for _ in range(100):s=n.step(s,NetworkInput('deliver',0)).state;self.assertEqual(len(s.pending),1)
        self.assertEqual(sum(v for _,v in s.machines),101)
    def test_pair_no_drain(self):
        p=Pair(Child(),Child(),Wiring(),1);s=p.step(p.initial_state(),PairInput('local',PairAddress('left',1))).state
        self.assertEqual((s.left,s.right,len(s.pending)),(1,0,1));s=p.step(s,PairInput('deliver',0)).state
        self.assertEqual((s.left,s.right,len(s.pending)),(1,1,1))
if __name__=='__main__':unittest.main()

class CodecIdentityTests(unittest.TestCase):
    def test_network_machine_accessors(self):
        n=Network([(9,Child()),(2,Child()),(7,Child())],Wiring())
        ids=n.machine_ids()
        self.assertEqual(ids,(2,7,9))
        state=NetworkState(((2,None),(7,['value']),(9,3)))
        self.assertIsNone(state.machine(2))
        self.assertIsNone(state.machine(1))
        self.assertIsNone(state.machine(8))
        self.assertIsNone(state.machine(10))
        self.assertIs(state.machine(7),state.machines[1][1])
        self.assertEqual(state.machine(9),3)
        with self.assertRaises(ValueError): state.machine(-1)
        n.machines[4]=Child()
        self.assertEqual(ids,(2,7,9))
        self.assertEqual(n.machine_ids(),(2,4,7,9))

    def test_network_canonical_order_after_registration_mapping_changes(self):
        n=Network([(7,Child()),(9,Child())],Wiring())
        n.machines[2]=Child()
        reference=Network([(2,Child()),(7,Child()),(9,Child())],Wiring())
        state=n.initial_state()
        self.assertEqual(tuple(id for id,_ in state.machines),(2,7,9))
        self.assertEqual(n.metadata(),reference.metadata())
        self.assertEqual([c.id for c in _state_checks(n,state)],
                         ['machine/2::nonnegative','machine/7::nonnegative',
                          'machine/9::nonnegative','global::sum'])
        self.assertEqual([i.value.machine for i in n.inputs(state) if i.kind=='local'],
                         [2,7,9])
        n.machines.clear()
        with self.assertRaisesRegex(ModelError,'empty'): n.initial_state()

    def test_network_codec_routing_and_queue_identity(self):
        from statelessness.stateful_codec import StatefulCodec,network_schemas
        m=StatefulCodec(Network([(0,Child()),(1,Child()),(2,Child())],Wiring()),network_schemas(U8,U8,U8))
        h=m.step(m.initial_state(),NetworkInput('local',Address(0,1))).state
        self.assertEqual(m.decode_state(m.encode_state(h)),h)
        self.assertEqual(m.decode_input(m.encode_input(NetworkInput('deliver',0))),NetworkInput('deliver',0))
        changed=NetworkState(h.machines,(Message(0,Route(2,7,1)),))
        self.assertNotEqual(m.encode_state(h),m.encode_state(changed))
        self.assertFalse(m.equal_states(h,changed))
    def test_stateful_codecs_reject_unknown_action_and_address_tags(self):
        from statelessness.stateful_codec import StatefulCodec, network_schemas, pair_schemas
        network=StatefulCodec(Network([(0,Child())],Wiring()),network_schemas(U8,U8,U8))
        pair=StatefulCodec(Pair(Child(),Child(),Wiring()),pair_schemas(U8,U8,U8,U8,U8,U8))
        for codec,value in ((network,NetworkInput('other',0)),(pair,PairInput('other',0))):
            with self.subTest(value=value),self.assertRaisesRegex(ModelError,'invalid stateful action tag'):
                codec.encode_input(value)
            with self.subTest(codec=codec),self.assertRaisesRegex(ModelError,'invalid stateful action tag'):
                codec.decode_input(b'\xff')
        with self.assertRaisesRegex(ModelError,'invalid stateful action tag'):
            pair.decode_input(b'\x00\xff')
        invalid=PairAddress('left',1)
        object.__setattr__(invalid,'side','other')
        with self.assertRaisesRegex(ModelError,'invalid stateful action tag'):
            pair.encode_input(PairInput('local',invalid))
        for value in (PairInput('local',PairAddress('left',1)),PairInput('deliver',0)):
            self.assertEqual(pair.decode_input(pair.encode_input(value)),value)
    def test_lossless_historical_text(self):
        m=LifecycleMonitor(Spec(),key_schema=U8);h=History(violations=['time_backwards'])
        self.assertEqual(m.decode_history(m.encode_history(h)).violations,['time_backwards'])

class HandoffTests(unittest.TestCase):
    def test_pair_and_network_own_child_transition_before_routing(self):
        retained=[1]; disposition=Disposition()
        class Mutable(Child):
            def step(self,state,input): return Transition(retained,('effect',),disposition)
        class MutatingWiring(Wiring):
            def mutate(self):
                retained.append(2)
                object.__setattr__(disposition,'kind','ignored')
                object.__setattr__(disposition,'reason','changed')
            def messages(self,output): self.mutate(); return ()
            def routes(self,output): self.mutate(); return ()
        pair=Pair(Mutable(),Child(),MutatingWiring())
        result=pair.step(PairState([],0),PairInput('local',PairAddress('left',1)))
        self.assertEqual(result.state.left,[1]); self.assertEqual(result.disposition,Disposition())
        retained[:]=[1]; object.__setattr__(disposition,'kind','accepted'); object.__setattr__(disposition,'reason','')
        network=Network([(0,Mutable()),(1,Child())],MutatingWiring())
        result=network.step(NetworkState(((0,[]),(1,0))),NetworkInput('local',Address(0,1)))
        self.assertEqual(result.state.machine(0),[1]); self.assertEqual(result.disposition,Disposition())

    def test_pair_operations_capture_models_wiring_and_bounds(self):
        class Replacement(Child):
            def metadata(self): return Metadata('replacement')
            def initial_state(self): return 99
            def inputs(self,state): return (99,)
            def check_state(self,state): return (Check('replacement'),)
        class OtherWiring(Wiring):
            def metadata(self): return Metadata('changed')
            def messages(self,output): return ()
            def check_state(self,state): return (Check('changed'),)
            def check_transition(self,*args): return (Check('changed'),)
        class WiringWithChecks(Wiring):
            def check_transition(self,*args): return (Check('turn'),)
        class Mutating(Child):
            def change(self):
                pair.right=Replacement(); pair.wiring=OtherWiring(); pair.max_pending=0
            def metadata(self): self.change(); return Metadata('first')
            def initial_state(self): self.change(); return 7
            def inputs(self,state): self.change(); yield 1
            def step(self,state,input): self.change(); return Transition(state,(input,))
            def check_state(self,state): self.change(); return (Check('first'),)
            def check_transition(self,*args): self.change(); return (Check('first-turn'),)
        def setup(): return Pair(Mutating(),Child(),Wiring())
        pair=setup(); metadata=pair.metadata()
        self.assertIn('token',metadata.name); self.assertIn('ring',metadata.name); self.assertTrue(metadata.name.endswith(';pending=32'))
        pair=setup(); self.assertEqual(pair.initial_state(),PairState(7,0))
        pair=setup(); self.assertEqual(list(pair.inputs(PairState(0,0))),[PairInput('local',PairAddress('left',1)),PairInput('local',PairAddress('right',1))])
        pair=setup(); result=pair.step(PairState(0,0),PairInput('local',PairAddress('left',1)))
        self.assertEqual(result.state.pending,(PairAddress('right',1),))
        pair=setup(); self.assertEqual([c.id for c in _state_checks(pair,PairState(0,0))],['left::first','right::nonnegative','global::sum'])
        from statelessness.core import _transition_checks
        pair=Pair(Mutating(),Child(),WiringWithChecks())
        checks=_transition_checks(pair,PairState(0,0),PairInput('local',PairAddress('left',1)),Transition(PairState(0,0)))
        self.assertEqual([c.id for c in checks],['left::first-turn','global::turn'])

    def test_network_metadata_and_initial_state_capture_configuration(self):
        class OtherWiring(Wiring):
            def metadata(self): return Metadata('changed-wiring')
        class MutatingChild(Child):
            def metadata(self):
                network.machines.pop(1,None); network.wiring=OtherWiring()
                object.__setattr__(policy,'max_pending',999)
                return Metadata('first')
            def initial_state(self):
                network.machines.pop(1,None)
                return 7
        policy=QueuePolicy()
        network=Network([(0,MutatingChild()),(1,Child())],Wiring(),policy)
        metadata=network.metadata()
        self.assertIn('1=13:5:token/1/1/1;',metadata.name)
        self.assertIn('w=12:4:ring/1/1/1;',metadata.name)
        self.assertIn('policy=FifoPerChannel,32,32,8,false,false',metadata.name)
        network=Network([(0,MutatingChild()),(1,Child())],Wiring())
        self.assertEqual(network.initial_state(),NetworkState(((0,7),(1,0))))

    def test_network_check_callbacks_use_captured_registry_and_wiring(self):
        from statelessness.core import _transition_checks
        class Changed(Wiring):
            def check_state(self,s): return (Check('changed'),)
            def check_transition(self,*args): return (Check('changed'),)
        class ChildWithChecks(Child):
            def check_transition(self,*args): return (Check('child-turn'),)
        class MutatingChild(ChildWithChecks):
            def check_state(self,state):
                network.machines.pop(1,None); network.wiring=Changed()
                return (Check('first'),)
            def check_transition(self,*args):
                network.machines.pop(1,None); network.wiring=Changed()
                return (Check('first-turn'),)
        network=Network([(0,MutatingChild()),(1,ChildWithChecks())],Wiring())
        state=NetworkState(((0,0),(1,0)))
        self.assertEqual([c.id for c in _state_checks(network,state)],['machine/0::first','machine/1::nonnegative','global::sum'])
        network=Network([(0,MutatingChild()),(1,ChildWithChecks())],Wiring())
        transition=Transition(state)
        checks=_transition_checks(network,state,NetworkInput('local',Address(0,1)),transition)
        self.assertEqual([c.id for c in checks],['machine/0::first-turn'])

    def test_network_input_copy_uses_original_admission_snapshot(self):
        class MutatingState:
            def __deepcopy__(self,memo):
                network.machines.pop(1,None)
                object.__setattr__(policy,'max_pending',0)
                return 0
        policy=QueuePolicy()
        network=Network([(0,Child()),(1,Child())],Wiring(),policy)
        state=NetworkState(((0,MutatingState()),(1,0)),(Message(0,Route(1,0,1)),))
        inputs=network.inputs(state)
        self.assertEqual(list(inputs),[NetworkInput('local',Address(0,1)),NetworkInput('local',Address(1,1)),NetworkInput('deliver',0)])
    def test_network_inputs_capture_pending_before_machine_copy_hooks(self):
        second = Message(0,Route(1,0,2))
        class MutatingState:
            def __deepcopy__(self,memo):
                object.__setattr__(second.route,'channel',1)
                return 0
        network = Network([(0,Child()),(1,Child())],Wiring())
        state = NetworkState(((0,MutatingState()),(1,0)),
                             (Message(0,Route(1,0,1)),second))
        self.assertEqual(list(network.inputs(state)), [NetworkInput('local',Address(0,1)),
            NetworkInput('local',Address(1,1)),NetworkInput('deliver',0)])
    def test_network_inputs_do_not_copy_pending_payloads(self):
        class Uncopyable:
            def __deepcopy__(self,memo): raise AssertionError('payload should not be copied')
        network = Network([(0,Child()),(1,Child())],Wiring())
        state = NetworkState(((0,0),(1,0)),(Message(0,Route(1,0,Uncopyable())),))
        self.assertEqual(list(network.inputs(state)), [NetworkInput('local',Address(0,1)),
            NetworkInput('local',Address(1,1)),NetworkInput('deliver',0)])
    def test_network_route_and_queue_bounds_are_snapshotted_before_callbacks(self):
        for policy, expected in ((QueuePolicy(max_routes_per_output=1), 'fanout bound'),
                                 (QueuePolicy(max_pending=1), 'queue bound')):
            class Router(Wiring):
                def routes(self, output):
                    yield Route(1, 0, output.value)
                    object.__setattr__(policy, 'max_routes_per_output', 100)
                    object.__setattr__(policy, 'max_pending', 100)
                    yield Route(1, 0, output.value)
            network = Network([(0, Child()), (1, Child())], Router(), policy)
            with self.subTest(expected=expected), self.assertRaisesRegex(ModelError, expected):
                network.step(network.initial_state(), NetworkInput('local', Address(0, 1)))

    def test_network_input_stream_snapshots_policy_before_child_iterator(self):
        policy = QueuePolicy()
        class MutatingChild(Child):
            def inputs(self, state):
                yield 1
                object.__setattr__(policy, 'delivery', 'Unordered')
                object.__setattr__(policy, 'allow_drop', True)
                object.__setattr__(policy, 'allow_duplicate', True)
        network = Network([(0, MutatingChild()), (1, Child())], Wiring(), policy)
        state = NetworkState(((0, 0), (1, 0)), (Message(0, Route(1, 0, 1)), Message(0, Route(1, 0, 2))))
        stream = network.inputs(state)
        self.assertEqual(list(stream), [NetworkInput('local', Address(0, 1)), NetworkInput('local', Address(1, 1)), NetworkInput('deliver', 0)])

    def test_network_input_stream_retains_registered_models(self):
        class Replacement(Child):
            def inputs(self,state): return (99,)
        class MutatingChild(Child):
            def inputs(self,state):
                yield 1
                network.machines[1]=Replacement()
        network=Network([(0,MutatingChild()),(1,Child())],Wiring())
        inputs=network.inputs(NetworkState(((0,0),(1,0))))
        self.assertEqual(list(inputs),[NetworkInput('local',Address(0,1)),NetworkInput('local',Address(1,1))])

    def test_network_step_retains_registered_routes_and_wiring(self):
        class OtherWiring(Wiring):
            def routes(self,output): return (Route(999,0,output.value),)
        class MutatingChild(Child):
            def step(self,state,input):
                network.machines.pop(1)
                network.wiring=OtherWiring()
                return Transition(state,(input,))
        network=Network([(0,MutatingChild()),(1,Child())],Wiring())
        result=network.step(network.initial_state(),NetworkInput('local',Address(0,1)))
        self.assertEqual(result.state.pending,(Message(0,Route(1,7,1)),))

    def test_network_step_owns_input_before_copying_pending_payloads(self):
        input_value=['original']
        class MutatingPayload:
            def __deepcopy__(self,memo):
                input_value[:]=['changed']
                return 'queued'
        class Echo(Child):
            def step(self,state,input): return Transition(state,(input[0],))
        network=Network([(0,Echo()),(1,Child())],Wiring())
        state=NetworkState(((0,0),(1,0)),(Message(0,Route(1,0,MutatingPayload())),))
        result=network.step(state,NetworkInput('local',Address(0,input_value)))
        self.assertEqual(result.outputs,(Address(0,'original'),))

    def test_acknowledged_transfer_and_early_grant_fault(self):
        class Role:
            def __init__(self,id,faulty):self.id,self.faulty=id,faulty
            def initial_state(self):return int(self.id==20)
            def step(self,s,i):
                if self.id==10 and i==1:return Transition(1,(10,11) if self.faulty else (10,))
                if self.id==10 and i==4:return Transition(2,(11,))
                if self.id==20 and i==2:return Transition(0,(12,))
                if self.id==30 and i==3:return Transition(1,())
                return Transition(s,(),Disposition('ignored','not applicable'))
            def check_state(self,s):return []
        class Routes:
            def routes(self,o):
                destination,input={10:(20,2),11:(30,3),12:(10,4)}[o.value]
                return [Route(destination,0,input)]
            def check_state(self,s):
                return [Check('unique-owner','failed','two owners') if s.machine(20)+s.machine(30)>1 else Check('unique-owner')]
        def net(fault):return Network([(id,Role(id,fault)) for id in (10,20,30)],Routes())
        safe=net(False);s=safe.step(safe.initial_state(),NetworkInput('local',Address(10,1))).state
        for _ in range(3):s=safe.step(s,NetworkInput('deliver',0)).state;self.assertEqual(_state_checks(safe,s)[-1].status,'passed')
        self.assertEqual((s.machine(20),s.machine(30)),(0,1))
        bad=net(True);s=bad.step(bad.initial_state(),NetworkInput('local',Address(10,1))).state
        s=bad.step(s,NetworkInput('deliver',1)).state
        self.assertEqual(_state_checks(bad,s)[-1],Check('global::unique-owner','failed','two owners'))
    def test_routed_payload_snapshot(self):
        payload=[1]
        class Mutable(Child):
            def step(self,s,i):return Transition(s,(payload,))
        class Router(Wiring):
            def routes(self,o):return [Route(1,0,o.value)]
        n=Network([(0,Mutable()),(1,Mutable())],Router());t=n.step(n.initial_state(),NetworkInput('local',Address(0,0)))
        payload.append(2);self.assertEqual(t.state.pending[0].route.input,[1])
        t.outputs[0].value.append(3);self.assertEqual(t.state.pending[0].route.input,[1])
