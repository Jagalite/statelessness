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
