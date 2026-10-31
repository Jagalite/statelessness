"""Actual domain iteration must respect product quotas before mapper execution."""
import unittest
from statelessness import Check, CheckSink, EXHAUSTED, ModelError, Transition, check_observed_into
from statelessness.modeling import Domain, DomainDescriptor, ModelAdapter, PropertyMetadata


class MisreportedSequence:
    def __init__(self, values): self.values=values; self.pulls=0
    def __len__(self): return 1
    def __iter__(self):
        for value in self.values:
            self.pulls+=1
            yield value


class DomainProductTests(unittest.TestCase):
    def test_runtime_build_identity_is_stable_and_content_sensitive(self):
        import subprocess,sys,tempfile
        from pathlib import Path
        from statelessness import runtime_build_id
        from statelessness.modeling import _runtime_resource_digest
        identity=runtime_build_id()
        self.assertRegex(identity,r'^python-package-sha256:[0-9a-f]{64}$')
        result=subprocess.run([sys.executable,'-c','from statelessness import runtime_build_id; print(runtime_build_id())'],
                              capture_output=True,text=True,timeout=20,check=True)
        self.assertEqual(result.stdout.strip(),identity)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); (root/'first.py').write_text('alpha'); (root/'second.json').write_text('{}')
            before=_runtime_resource_digest(root)
            (root/'first.py').write_text('beta')
            self.assertNotEqual(_runtime_resource_digest(root),before)
            (root/'ignored.pyc').write_bytes(b'noise')
            after=_runtime_resource_digest(root); (root/'ignored.pyc').write_bytes(b'other')
            self.assertEqual(_runtime_resource_digest(root),after)

    def test_product_checks_actual_lengths_before_mapper(self):
        for left_values,right_values,limit in ((range(5),(1,),4),((1,2),range(3),4)):
            with self.subTest(left=left_values,right=right_values):
                left=MisreportedSequence(left_values); right=MisreportedSequence(right_values)
                mapped=[]
                with self.assertRaisesRegex(ModelError,'product limit'):
                    Domain.product(left,right,limit,lambda a,b:mapped.append((a,b)))
                self.assertEqual(mapped,[])
                self.assertLessEqual(left.pulls,limit+1)
                self.assertLessEqual(right.pulls,limit+1)

    def test_product_order_and_empty(self):
        self.assertEqual(Domain.product([1,2],['a','b'],4,lambda a,b:(a,b)),
                         [(1,'a'),(1,'b'),(2,'a'),(2,'b')])
        self.assertEqual(Domain.product([],['a'],0,lambda a,b:self.fail('mapped empty product')),[])
        self.assertEqual(Domain.product([1],[],0,lambda a,b:self.fail('mapped empty product')),[])

    def test_product_captures_builtin_slots_before_copy_hooks(self):
        left = []
        right = ['original', 'second']
        class Mutator:
            def __deepcopy__(self, memo):
                left[1] = 'changed-left'
                right[0] = 'changed-right'
                return 'first'
        left.extend([Mutator(), 'original-left'])
        self.assertEqual(Domain.product(left, right, 4, lambda a, b: (a, b)),
                         [('first', 'original'), ('first', 'second'),
                          ('original-left', 'original'), ('original-left', 'second')])

    def test_product_bounds_snapshot_after_custom_length_grows_other_side(self):
        left = [1]
        class GrowingSequence:
            def __len__(self):
                left.extend(range(10000))
                return 1
            def __iter__(self): return iter(('right',))
        with self.assertRaisesRegex(ModelError, 'product limit'):
            Domain.product(left, GrowingSequence(), 1, lambda *_: self.fail('mapped'))

    def test_empty_builtin_product_does_not_inspect_other_side(self):
        class Unusable:
            def __len__(self): raise AssertionError('unused length')
            def __iter__(self): raise AssertionError('unused iterator')
        for empty in ([], ()):
            self.assertEqual(Domain.product(empty, Unusable(), 0, lambda *_: self.fail('mapped')), [])
            self.assertEqual(Domain.product(Unusable(), empty, 0, lambda *_: self.fail('mapped')), [])
        with self.assertRaises(ValueError):
            Domain.product([], Unusable(), -1, lambda *_: self.fail('mapped'))

    def test_domain_equality_cannot_mutate_caller_input(self):
        class Value:
            def __init__(self, value): self.value = value
            def __eq__(self, other):
                other.value.append('mutated')
                return False
        domain = Domain(DomainDescriptor('items', 'bounded', 1))
        domain.push(Value([]))
        candidate = Value([])
        self.assertFalse(domain.contains(candidate))
        self.assertEqual(candidate.value, [])

    def test_domain_contains_preserves_query_between_comparisons(self):
        class Entry:
            def __init__(self, match): self.match = match
            def __eq__(self, other):
                if self.match:
                    return other == ['original']
                other.append('changed')
                return False
        domain = Domain(DomainDescriptor('items', 'bounded', 2))
        domain.extend((Entry(False), Entry(True)))
        query = ['original']
        self.assertTrue(domain.contains(query))
        self.assertEqual(query, ['original'])

        class Uncopyable:
            def __deepcopy__(self, memo): raise AssertionError('unexpected copy')
        empty = Domain(DomainDescriptor('empty', 'bounded', 0))
        self.assertFalse(empty.contains(Uncopyable()))

    def test_domain_contains_defers_unused_entry_copy_error_and_freezes_sequence(self):
        class Entry:
            fail = False
            def __init__(self, value): self.value = value
            def __deepcopy__(self, memo):
                if self.fail and self.value == 2: raise ValueError('later copy')
                return Entry(self.value)
            def __eq__(self, other):
                if self.value == 1:
                    domain.into_entries()
                return self.value == other
        domain = Domain(DomainDescriptor('items', 'bounded', 2))
        domain.extend((Entry(1), Entry(2)))
        Entry.fail = True
        self.assertTrue(domain.contains(1))
        self.assertEqual(domain.entries, ())
        Entry.fail = False
        domain.extend((Entry(1), Entry(2)))
        Entry.fail = True
        with self.assertRaisesRegex(ModelError, 'later copy'):
            domain.contains(2)

    def test_entries_freezes_slots_before_copy_callbacks(self):
        class Entry:
            mutate = False
            def __init__(self, value): self.value = value
            def __deepcopy__(self, memo):
                if self.mutate and self.value == 1:
                    removed = domain.into_entries()
                    removed[1] = Entry(99)
                return Entry(self.value)

        domain = Domain(DomainDescriptor('items', 'bounded', 2))
        domain.extend((Entry(1), Entry(2)))
        Entry.mutate = True
        self.assertEqual([item.value for item in domain.entries], [1, 2])
        self.assertEqual(domain.entries, ())

    def test_indexed_sample_uses_owned_snapshot_during_rng_callback(self):
        domain = Domain(DomainDescriptor('items', 'bounded', 2))
        domain.extend((['first'], ['second']))
        class MutatingRng:
            def index(self, length):
                self.length = length
                domain.into_entries()
                return 0
        rng = MutatingRng()
        value = domain.sample_indexed(rng)
        self.assertEqual((rng.length, value, domain.entries), (2, ['first'], ()))
        class BadRng:
            def __init__(self, result): self.result = result
            def index(self, length): return self.result
        self.assertIs(domain.sample_indexed(BadRng(None)), EXHAUSTED)
        for value in (True, -1, 0, 1.5):
            with self.subTest(value=value), self.assertRaises(ModelError): domain.sample_indexed(BadRng(value))

    def test_sample_and_contains_copy_only_reached_entries(self):
        copies = []
        class Entry:
            def __init__(self, value): self.value = value
            def __deepcopy__(self, memo):
                copies.append(self.value)
                return Entry(self.value)
            def __eq__(self, other): return self.value == other
        domain = Domain(DomainDescriptor('items', 'bounded', 100))
        domain.extend(Entry(n) for n in range(100))
        copies.clear()
        class Rng:
            def index(self, length): return 7
        self.assertEqual(domain.sample_indexed(Rng()).value, 7)
        self.assertEqual(copies, [7])
        copies.clear()
        self.assertTrue(domain.contains(2))
        self.assertEqual(copies, [0, 1, 2])

    def test_indexed_sample_defers_unselected_copy_failure_until_after_draw(self):
        class Entry:
            fail = False
            def __init__(self, value): self.value = value
            def __deepcopy__(self, memo):
                if self.fail and self.value == 2: raise ValueError('entry copy')
                return Entry(self.value)
        domain = Domain(DomainDescriptor('items', 'bounded', 2))
        domain.extend((Entry(1), Entry(2)))
        Entry.fail = True
        class Rng:
            def __init__(self, selected): self.selected = selected; self.draws = 0
            def index(self, length):
                self.draws += 1
                domain.into_entries()
                return self.selected
        rng = Rng(0)
        self.assertEqual(domain.sample_indexed(rng).value, 1)
        self.assertEqual(rng.draws, 1)
        Entry.fail = False
        domain.extend((Entry(1), Entry(2)))
        Entry.fail = True
        rng = Rng(1)
        with self.assertRaisesRegex(ModelError, 'entry copy'):
            domain.sample_indexed(rng)
        self.assertEqual(rng.draws, 1)


class ModelAdapterOwnershipTests(unittest.TestCase):
    def test_engine_nested_append_sees_prefix_and_keeps_sticky_failure(self):
        class Model:
            def metadata(self): return None
            def initial_state(self): return 0
            def step(self, state, input): return Transition(state, ())
            def check_state_into(self, state, sink): sink.push(Check('state'))
            def check_transition_into(self, before, input, transition, sink):
                self.prefix = tuple(check.id for check in sink.snapshot())
                try: sink.push('invalid')
                except ModelError: pass
        raw = Model()
        model = ModelAdapter(raw, properties=(PropertyMetadata('state'),))
        checks = []
        with self.assertRaises(ModelError):
            check_observed_into(model, 0, 1, Transition(1, ()), 1, checks)
        self.assertEqual(raw.prefix, ('state',))
        self.assertEqual(checks, [])

    def test_direct_transition_check_detaches_arguments_and_prefers_append(self):
        class Model:
            def metadata(self): return None
            def initial_state(self): return []
            def step(self, state, input): return Transition(state, (input,))
            def check_transition(self, *args):
                self.fail_legacy = True
                return (Check('legacy'),)
            def check_transition_into(self, before, input, transition, sink):
                before.append('before'); input.append('input')
                transition.state.append('after'); transition.outputs[0].append('output')
                sink.push(Check('edge'))
        raw = Model()
        model = ModelAdapter(raw, properties=(PropertyMetadata('edge', 'transition'),))
        before, input, after, output = [], [], [], []
        transition = Transition(after, (output,))
        self.assertEqual(model.check_transition(before, input, transition), (Check('edge'),))
        self.assertEqual((before, input, after, output), ([], [], [], []))
        self.assertFalse(hasattr(raw, 'fail_legacy'))
        checks = []
        model.check_transition_into(before, input, transition, CheckSink(checks))
        self.assertEqual(checks, [Check('edge')])
        self.assertEqual((before, input, after, output), ([], [], [], []))

    def test_direct_transition_check_error_does_not_mutate_arguments(self):
        class Model:
            def metadata(self): return None
            def initial_state(self): return []
            def step(self, state, input): return Transition(state, ())
            def check_transition(self, before, input, transition):
                before.append(1); input.append(2); transition.state.append(3)
                raise ValueError('failure')
        model = ModelAdapter(Model(), properties=(PropertyMetadata('edge', 'transition'),))
        before, input, after = [], [], []
        with self.assertRaises(ModelError):
            model.check_transition(before, input, Transition(after, ()))
        self.assertEqual((before, input, after), ([], [], []))

    def test_direct_step_and_append_check_use_detached_values(self):
        class Model:
            def metadata(self): return None
            def initial_state(self): return []
            def step(self, state, input):
                state.append('state'); input.append('input')
                self.saved = state
                return Transition(state, (input,))
            def check_state_into(self, state, sink):
                state.append('checked')
                sink.push(Check('state'))
        raw = Model()
        model = ModelAdapter(raw, properties=(PropertyMetadata('state'),))
        state, input = [], []
        result = model.step(state, input)
        self.assertEqual((state, input), ([], []))
        self.assertEqual((result.state, result.outputs), (['state'], (['input'],)))
        raw.saved.append('later')
        self.assertEqual(result.state, ['state'])
        self.assertEqual(model.check_state(state), (Check('state'),))
        self.assertEqual(state, [])
        checks = []
        model.check_state_into(state, CheckSink(checks))
        self.assertEqual(checks, [Check('state')])
        self.assertEqual(state, [])

    def test_direct_step_error_does_not_mutate_inputs(self):
        class Model:
            def metadata(self): return None
            def initial_state(self): return []
            def step(self, state, input):
                state.append(1); input.append(2)
                raise ValueError('failed')
        model = ModelAdapter(Model(), unchecked=True)
        state, input = [], []
        with self.assertRaises(ModelError): model.step(state, input)
        self.assertEqual((state, input), ([], []))
        self.assertEqual(model.check_state(state), ())
