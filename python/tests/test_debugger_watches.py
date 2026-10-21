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
