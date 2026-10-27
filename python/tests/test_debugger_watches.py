"""Local configuration ack, no-interest, sampling and explicit-loss goldens."""
import unittest
from dataclasses import replace
from statelessness import ModelError
from statelessness.debugger_inspect import SnapshotId, DisplaySchema
from statelessness.debugger_watches import (WatchRegistry, WatchAuthorization,
    WatchConfig, WatchLimits, WatchSelector, ObservationContext)


class WatchesTests(unittest.TestCase):
    def registry(self,**kwargs): return WatchRegistry(authorization=WatchAuthorization.local_all(),**kwargs)
    def context(self,sequence,**kwargs): return ObservationContext(SnapshotId(1,sequence,sequence),**kwargs)
    def test_no_interest_and_authorization(self):
        class Never:
            def schema(self): raise AssertionError('must not project')
        registry=self.registry()
        self.assertEqual(registry.observe(Never(),self.context(0)).evaluated,0)
        with self.assertRaisesRegex(ModelError,'unauthorized'): WatchRegistry().add(WatchConfig())
    def test_revisions_baselines_sampling_and_gap(self):
        registry=self.registry()
        ack=registry.add(WatchConfig(evaluation_interval=2))
        self.assertEqual((ack.generation,ack.capture_revision,ack.effective_sequence),(1,1,0))
        self.assertEqual(registry.observe(1,self.context(0)).evaluated,1)
        self.assertEqual(registry.observe(2,self.context(1)).evaluated,0)
        registry.observe(3,self.context(2))
        event=registry.snapshot()['events'][-1]
        self.assertEqual(event.kind,'change-since-last-sample')
        self.assertEqual(event.difference,'unknown')
        self.assertTrue(event.sampled_changed)
        registry.observe(4,self.context(5))
        registry.observe(4,self.context(6))
        self.assertIn('observation-gap',registry.snapshot()['events'][-1].kind)
        self.assertTrue(registry.observe(4,self.context(6)).stale)
        update=registry.set_state(ack.watch_id,'disabled')
        self.assertEqual(update.capture_revision,2)
        self.assertEqual(update.effective_sequence,7)
        self.assertFalse(registry.interested)
    def test_loss_and_failure_snapshot_independent(self):
        registry=self.registry(limits=WatchLimits(max_ring_records=2))
        registry.add(WatchConfig(trigger='every'))
        registry.observe(1,self.context(0))
        registry.observe(2,self.context(1,property_failure=True,failing_properties=('p',)))
        failure=registry.failure_snapshot
        registry.observe(3,self.context(2))
        self.assertGreater(registry.health['dropped'],0)
        self.assertGreater(registry.snapshot()['events'][-1].dropped_before,0)
        self.assertEqual(failure['at'].sequence,1)
        self.assertEqual(registry.failure_snapshot['at'].sequence,1)
    def test_snapshot_now_does_not_change_baseline_or_sequence(self):
        registry=self.registry(); ack=registry.add(WatchConfig())
        registry.observe(1,self.context(0))
        registry.snapshot_now(ack.watch_id,99,self.context(10))
        registry.observe(1,self.context(1))
        self.assertEqual(registry.snapshot()['at'].sequence,1)
        self.assertEqual(registry.snapshot()['events'][-1].kind,'snapshot')

    def test_validated_schema_and_unknown_typed_selectors(self):
        registry=self.registry()
        ack=registry.add_validated(WatchConfig(selector=WatchSelector(machine='m')),1)
        self.assertEqual(ack.resolved_type,'scalar')
        self.assertEqual(registry.observe(2,self.context(0)).evaluated,0)
        self.assertEqual(registry.observe(2,self.context(1,machine='m')).evaluated,1)
        registry.observe('different-schema',self.context(2,machine='m'))
        self.assertEqual(registry.snapshot()['events'][-1].kind,'schema-changed')
        with self.assertRaises(ValueError): WatchSelector(machine=3)

    def test_expired_watch_reactivation_gets_full_ttl_and_preserves_history(self):
        registry=self.registry()
        ack=registry.add_validated(WatchConfig(trigger='every',ttl_observations=2),0)
        for sequence in range(2): registry.observe(sequence,self.context(sequence))
        self.assertEqual(registry.list()[0]['state'],'expired')
        history=registry.snapshot()['events']
        activated=registry.set_state(ack.watch_id,'active')
        self.assertEqual((activated.watch_id,activated.generation,activated.capture_revision,activated.effective_sequence),(ack.watch_id,2,2,2))
        self.assertEqual((activated.display_schema,activated.resolved_type),(ack.display_schema,ack.resolved_type))
        self.assertEqual(registry.snapshot()['events'][:len(history)],history)
        first=registry.observe(2,self.context(2))
        self.assertEqual((first.selected,first.evaluated,first.emitted),(1,1,1))
        self.assertEqual(registry.current(ack.watch_id).kind,'baseline:reenabled')
        self.assertEqual(registry.list()[0]['state'],'active')
        second=registry.observe(3,self.context(3))
        self.assertEqual((second.selected,second.evaluated,second.emitted),(1,1,1))
        self.assertEqual(registry.list()[0]['state'],'expired')
        self.assertIsNone(registry.current(ack.watch_id))
        self.assertEqual(registry.observe(4,self.context(4)).selected,0)

    def test_reactivation_restarts_evaluation_and_delivery_sampling(self):
        for state in ('paused','disabled'):
            with self.subTest(state=state):
                registry=self.registry()
                ack=registry.add(WatchConfig(trigger='every',evaluation_interval=3,delivery_interval=2))
                registry.observe(0,self.context(0))
                self.assertEqual(registry.observe(1,self.context(1)).evaluated,0)
                registry.set_state(ack.watch_id,state)
                registry.set_state(ack.watch_id,'active')
                first=registry.observe(2,self.context(2))
                self.assertEqual((first.evaluated,first.emitted),(1,1))
                event=registry.current(ack.watch_id)
                self.assertEqual(event.kind,'baseline:reenabled')
                self.assertIsNone(event.sampled_changed)
                self.assertEqual(registry.observe(3,self.context(3)).evaluated,0)
                self.assertEqual(registry.observe(4,self.context(4)).evaluated,0)
                second=registry.observe(5,self.context(5))
                self.assertEqual((second.evaluated,second.emitted),(1,0))
                self.assertEqual(registry.current(ack.watch_id).kind,'change-since-last-sample')

    def test_schema_callback_failure_is_captured_and_recoverable(self):
        class Mutable:
            calls=0
            fail=True
            def schema(self):
                self.calls+=1
                if self.fail: raise RuntimeError('schema callback secret' * 10000)
                return DisplaySchema('Mutable')
            def inspect(self,path,cx): return cx.scalar(path,('python-int','1'))
        value=Mutable(); registry=self.registry()
        ack=registry.add(WatchConfig(trigger='every',evaluation_interval=3,delivery_interval=3))
        summary=registry.observe(value,self.context(0,property_failure=True,failing_properties=('p',)))
        self.assertEqual((summary.selected,summary.evaluated,summary.emitted),(1,1,1))
        self.assertEqual(value.calls,1)
        event=registry.current(ack.watch_id)
        self.assertEqual((event.kind,event.completeness,event.difference),('inspector-error','unavailable','unknown'))
        self.assertIsNone(event.value)
        self.assertEqual(registry.failure_snapshot['at'].sequence,0)
        self.assertEqual(registry.failure_snapshot['events'][-1],event)
        value.fail=False
        registry.observe(value,self.context(1,property_failure=True))
        self.assertIsNone(registry.current(ack.watch_id).previous_snapshot)
        self.assertEqual(registry.current(ack.watch_id).difference,'unknown')
        self.assertEqual(registry.current(ack.watch_id).kind,'property-failure')
        value.fail=True
        registry.observe(value,self.context(2,property_failure=True))
        self.assertEqual(registry.current(ack.watch_id).kind,'inspector-error')
        self.assertFalse(registry.list()[0]['has_baseline'])
        value.fail=False
        registry.observe(value,self.context(3,property_failure=True))
        self.assertIsNone(registry.current(ack.watch_id).previous_snapshot)

    def test_metadata_fault_is_shared_bounded_and_expires(self):
        class HostError(Exception):
            def __str__(self): raise AssertionError('must not format host error')
        class Broken:
            calls=0
            def schema(self):
                self.calls+=1
                raise HostError()
        registry=self.registry(); value=Broken()
        for _ in range(2): registry.add(WatchConfig(ttl_observations=1))
        summary=registry.observe(value,self.context(0,property_failure=True,failing_properties=('p',)))
        self.assertEqual((summary.selected,summary.evaluated,summary.emitted),(2,2,2))
        self.assertEqual(value.calls,1)
        errors=[e for e in registry.failure_snapshot['events'] if e.kind=='inspector-error']
        self.assertEqual(len(errors),2)
        self.assertTrue(all(e.property_failure and e.failing_properties==('p',) for e in errors))
        self.assertTrue(all(e['state']=='expired' for e in registry.list()))
        self.assertEqual(registry.observe(value,self.context(1,property_failure=True)).evaluated,0)
        self.assertEqual(value.calls,1)

    def test_mutable_projection_failure_clears_baseline_without_false_change(self):
        class Mutable:
            calls=0
            fail=False
            def schema(self): return DisplaySchema('Mutable')
            def inspect(self,path,cx):
                self.calls+=1
                if self.fail: raise RuntimeError('path unavailable')
                return cx.scalar(path,('python-int','1'))
        registry=self.registry(); value=Mutable()
        ids=[registry.add(WatchConfig()).watch_id for _ in range(2)]
        registry.observe(value,self.context(0))
        self.assertEqual(value.calls,1)
        value.fail=True
        registry.observe(value,self.context(1,property_failure=True))
        self.assertEqual(value.calls,2)
        for id in ids:
            self.assertEqual(registry.current(id).kind,'inspector-error')
            self.assertEqual(registry.current(id).difference,'unknown')
            self.assertFalse(next(e for e in registry.list() if e['id']==id)['has_baseline'])
        value.fail=False
        registry.observe(value,self.context(2))
        self.assertEqual(value.calls,3)
        for id in ids:
            self.assertEqual(registry.current(id).kind,'baseline:initial')
            self.assertIsNone(registry.current(id).previous_snapshot)

    def test_unprintable_projection_error_does_not_escape_failure_capture(self):
        class HostError(Exception):
            def __str__(self): raise HostError()
        class Broken:
            calls=0
            def schema(self): return DisplaySchema('Broken')
            def inspect(self,path,cx):
                self.calls+=1
                raise HostError()
        registry=self.registry(); value=Broken()
        ids=[registry.add(WatchConfig()).watch_id for _ in range(2)]
        summary=registry.observe(value,self.context(0,property_failure=True))
        self.assertEqual((summary.selected,summary.evaluated,summary.emitted),(2,2,2))
        self.assertEqual(value.calls,1)
        for id in ids:
            self.assertEqual(registry.current(id).kind,'inspector-error')
            self.assertEqual(registry.current(id).difference,'unknown')
        self.assertEqual(registry.failure_snapshot['at'].sequence,0)

    def test_projection_error_text_is_not_a_schema_control_signal(self):
        class Broken:
            def schema(self): return DisplaySchema('Broken')
            def inspect(self,path,cx): raise RuntimeError('schema mismatch')
        registry=self.registry(); ack=registry.add(WatchConfig())
        registry.observe(Broken(),self.context(0,property_failure=True))
        self.assertEqual(registry.current(ack.watch_id).kind,'inspector-error')
        self.assertEqual(registry.failure_snapshot['at'].sequence,0)

    def test_structured_projection_schema_mismatch_remains_schema_change(self):
        class Mutable:
            calls=0
            def schema(self):
                self.calls+=1
                return DisplaySchema('Mutable',version=1 if self.calls==1 else 2)
            def inspect(self,path,cx): return cx.scalar(path,('unit',None))
        registry=self.registry(); ack=registry.add(WatchConfig())
        registry.observe(Mutable(),self.context(0,property_failure=True))
        self.assertEqual(registry.current(ack.watch_id).kind,'schema-changed')
        self.assertEqual(registry.failure_snapshot['at'].sequence,0)
