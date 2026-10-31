"""Local configuration ack, no-interest, sampling and explicit-loss goldens."""
import unittest
from dataclasses import replace
from statelessness import ModelError
from statelessness.debugger_inspect import SnapshotId, DisplaySchema, InspectLimits, PathSegment, PageRequest
from statelessness.debugger_watches import (WatchRegistry, WatchAuthorization,
    WatchConfig, WatchLimits, WatchSelector, ObservationContext)


class WatchesTests(unittest.TestCase):
    def registry(self,**kwargs): return WatchRegistry(authorization=WatchAuthorization.local_all(),**kwargs)
    def context(self,sequence,**kwargs): return ObservationContext(SnapshotId(1,sequence,sequence),**kwargs)
    def test_arbitrary_float_kind_is_distinct_and_resolves(self):
        from statelessness.debugger_inspect import FloatView
        registry=self.registry()
        value=FloatView('u8','label',(1<<64)-1)
        config=WatchConfig(trigger='equals',equals=('float','u8','label',(1<<64)-1))
        ack=registry.add_validated(config,value)
        self.assertEqual(registry.list()[0]['resolved_wire_type'],'u8')
        registry.observe(value,self.context(0))
        self.assertEqual(registry.current(ack.watch_id).kind,'value-matched')
        self.assertEqual(registry.current(ack.watch_id).value.value,config.equals)
        for kind in ('f32','f64'):
            with self.subTest(alias=kind), self.assertRaises(ModelError):
                WatchConfig(trigger='equals',equals=('float',kind,'1',0))
    def test_local_integer_key_and_scalar_text_are_preserved(self):
        from statelessness.debugger_inspect import IntegerView
        class Model:
            def inspect(self,path,cx): return cx.map(path,((('u8','label'),IntegerView('u8','256')),))
        registry=self.registry()
        path=(PathSegment('map-key',('u8','label')),)
        watch=registry.add(WatchConfig(selector=WatchSelector(path=path),trigger='equals',equals=('u8','256'))).watch_id
        registry.observe(Model(),self.context(0))
        self.assertEqual(registry.current(watch).value.value,('u8','256'))
        self.assertEqual(registry.current(watch).kind,'value-matched')
    def test_baseline_budget_accounts_for_full_retained_schema(self):
        class Model:
            def schema(self): return DisplaySchema('x'*3500)
            def inspect(self,path,cx): return cx.scalar(path,('bool',True))
        registry=self.registry(limits=WatchLimits(max_baseline_bytes=2500))
        watch=registry.add(WatchConfig(trigger='every')).watch_id
        registry.observe(Model(),self.context(0))
        self.assertIsNone(registry.current(watch))
        self.assertEqual(registry.health['baseline_evictions'],1)
    def test_sampled_observation_does_not_retain_invalid_schema(self):
        class Model:
            name='valid'
            def schema(self): return DisplaySchema(self.name)
            def inspect(self,path,cx): return cx.scalar(path,('bool',True))
        value=Model(); registry=self.registry()
        registry.add(WatchConfig(trigger='every',evaluation_interval=2))
        registry.observe(value,self.context(0))
        value.name='x'*100_000
        registry.observe(value,self.context(1))
        self.assertEqual(registry.list()[0]['validated_schema'].name,'valid')
    def test_large_projection_uses_explicit_budget_and_evicts_exactly(self):
        value='é'*(256*1024+1)
        limits=WatchLimits(max_event_bytes=600_000,max_ring_bytes=600_000,max_baseline_bytes=600_000)
        registry=self.registry(limits=limits)
        config=WatchConfig(trigger='every',limits=InspectLimits(max_work=300_000,max_bytes=550_000,max_value_bytes=550_000))
        watch=registry.add(config).watch_id
        registry.clear_ring()
        registry.observe(value,self.context(0))
        event=registry.current(watch)
        self.assertEqual(event.value.value,value)
        self.assertTrue(event.value.complete_with_budget(550_000))
        first=registry.snapshot()
        self.assertEqual(len(first['events']),1)
        registry.observe(value,self.context(1))
        self.assertEqual(len([e for e in registry.snapshot()['events'] if e.watch_id==watch]),1)
        self.assertEqual(registry.current(watch).snapshot.sequence,1)
    def test_revision_exhaustion_rejects_mutation_at_u64_max(self):
        maximum=(1<<64)-1
        registry=self.registry()
        first=registry.add(WatchConfig()).watch_id
        registry.observe(1,self.context(maximum-1))
        self.assertEqual(registry.set_state(first,'active').effective_sequence,maximum)
        registry.observe(1,self.context(maximum))
        before=(registry.capture_revision,registry.list(),registry.snapshot())
        for action in (lambda: registry.add(WatchConfig()),
                       lambda: registry.update(first,WatchConfig()),
                       lambda: registry.set_state(first,'paused'),
                       lambda: registry.remove(first)):
            with self.assertRaises(ModelError): action()
            self.assertEqual((registry.capture_revision,registry.list(),registry.snapshot()),before)
    def test_lifecycle_events_include_prepared_schema_and_selector(self):
        class Model:
            def schema(self): return DisplaySchema('é'*600,source=('model.py',2,3))
            def inspect(self,path,cx): return cx.scalar(path,('bool',True))
        registry=self.registry()
        config=WatchConfig(selector=WatchSelector(model='m',machine='worker'),trigger='every')
        ack=registry.add_validated(config,Model())
        enabled=registry.snapshot()['events'][-1]
        self.assertEqual((enabled.kind,enabled.model,enabled.machine,enabled.phase,enabled.trigger),
                         ('watch-enabled','m','worker','control','every'))
        self.assertEqual(enabled.schema.name,'é'*512)
        self.assertEqual(enabled.schema.source,('model.py',2,3))
        registry.update_validated(ack.watch_id,config,Model())
        self.assertEqual(registry.snapshot()['events'][-1].schema.name,'é'*512)
        registry.set_state(ack.watch_id,'paused')
        self.assertEqual(registry.snapshot()['events'][-1].schema.name,'é'*512)
        registry.update(ack.watch_id,config)
        self.assertIsNone(registry.snapshot()['events'][-1].schema)
    def test_no_interest_and_authorization(self):
        class Never:
            def schema(self): raise AssertionError('must not project')
        registry=self.registry()
        self.assertEqual(registry.observe(Never(),self.context(0)).evaluated,0)
        with self.assertRaisesRegex(ModelError,'unauthorized'): WatchRegistry().add(WatchConfig())

    def test_watch_admission_rejects_invalid_limits_without_mutation(self):
        registry=self.registry()
        ack=registry.add(WatchConfig())
        before=(registry.capture_revision,registry.list(),registry.snapshot())
        zero_page=PageRequest(); object.__setattr__(zero_page,'limit',0)
        bad=(WatchConfig(selector=WatchSelector(schema_version=2)),
             WatchConfig(ttl_observations=0),
             WatchConfig(limits=InspectLimits(max_depth=33,max_bytes=32768)),
             WatchConfig(selector=WatchSelector(path=(PathSegment('field','x'),)),
                         limits=InspectLimits(max_depth=0,max_bytes=32768)),
             WatchConfig(limits=InspectLimits(max_bytes=0)),
             WatchConfig(limits=InspectLimits(max_bytes=32769)),
             # Rust WatchRegistry::validate rejects page.limit == 0 or > max_page_size.
             WatchConfig(page=PageRequest(0,101)),
             WatchConfig(page=zero_page))
        for config in bad:
            with self.subTest(config=config):
                with self.assertRaises(ModelError): registry.add(config)
                with self.assertRaises(ModelError): registry.update(ack.watch_id,config)
                self.assertEqual((registry.capture_revision,registry.list(),registry.snapshot()),before)
        custom=self.registry(schema_version=2)
        custom.add(WatchConfig(selector=WatchSelector(schema_version=2)))
        with self.assertRaises(ModelError): custom.add(WatchConfig())

    def test_disabled_observations_advance_without_gap_or_failure_mutation(self):
        class Never:
            def schema(self): raise AssertionError('disabled watches must not inspect')
        registry=self.registry()
        ack=registry.add(WatchConfig(enabled=False))
        registry.clear_ring()
        self.assertFalse(registry.observe(Never(),self.context(0)).stale)
        self.assertFalse(registry.observe(Never(),self.context(3,property_failure=True)).stale)
        self.assertTrue(registry.observe(Never(),self.context(3)).stale)
        self.assertEqual(registry.health['observation_gaps'],0)
        self.assertEqual(registry.snapshot()['events'],())
        self.assertIsNone(registry.failure_snapshot)
        self.assertEqual(registry.set_state(ack.watch_id,'active').effective_sequence,4)
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

    def test_health_and_summary_account_for_sampling_gaps_and_errors(self):
        registry = self.registry()
        registry.add(WatchConfig(trigger='every', evaluation_interval=2, delivery_interval=2))
        registry.add(WatchConfig(selector=WatchSelector(machine='other')))
        first = registry.observe(1, self.context(0))
        second = registry.observe(2, self.context(1))
        third = registry.observe(3, self.context(2))
        gap = registry.observe(4, self.context(4))
        self.assertEqual((first.evaluated, first.emitted, second.evaluated, third.evaluated, third.emitted), (1, 1, 0, 1, 0))
        self.assertTrue(gap.observation_gap)
        health = registry.health
        self.assertEqual((health['evaluated'], health['filtered'], health['evaluation_sampled'], health['delivery_sampled']), (2, 4, 2, 1))
        self.assertEqual(health['observation_gaps'], 1)
        self.assertEqual(health['dropped_events'], health['dropped'])
        class Broken:
            def schema(self): raise ValueError('broken')
        errors = self.registry()
        errors.add(WatchConfig(trigger='every'))
        summary = errors.observe(Broken(), self.context(0))
        self.assertEqual((summary.inspection_errors, errors.health['inspection_errors']), (1, 1))

    def test_sampled_source_change_is_sticky_and_expiry_takes_precedence(self):
        for ttl, expected in ((None, 'baseline:source-changed'), (1, 'baseline:expired')):
            with self.subTest(ttl=ttl):
                registry=self.registry()
                watch=registry.add(WatchConfig(trigger='every',evaluation_interval=2,
                    baseline_ttl_sequences=ttl)).watch_id
                registry.observe(1,self.context(0,model='A'))
                self.assertEqual(registry.observe(1,self.context(1,model='B')).evaluated,0)
                registry.observe(1,self.context(2,model='A'))
                self.assertEqual(registry.current(watch).kind,expected)

    def test_expiry_uses_last_event_after_incomplete_or_failed_projection(self):
        class Mutable:
            mode='incomplete'
            def schema(self): return DisplaySchema('Mutable')
            def inspect(self,path,cx):
                if self.mode=='error': raise RuntimeError('unavailable')
                return cx.string(path,'long' if self.mode=='incomplete' else 'x')
        for initial in ('incomplete','error'):
            with self.subTest(initial=initial):
                registry=self.registry(); value=Mutable(); value.mode=initial
                watch=registry.add(WatchConfig(trigger='every',evaluation_interval=2,
                    baseline_ttl_sequences=1,limits=InspectLimits(max_bytes=32768,max_value_bytes=1))).watch_id
                registry.observe(value,self.context(0))
                self.assertIn(registry.current(watch).kind,('unknown','inspector-error'))
                registry.observe(value,self.context(1))
                value.mode='complete'; registry.observe(value,self.context(2))
                self.assertEqual(registry.current(watch).kind,'baseline:expired')

    def test_schema_source_location_does_not_change_identity(self):
        class Mutable:
            line=1
            def schema(self): return DisplaySchema('Mutable',source=('model.py',self.line,0))
            def inspect(self,path,cx): return cx.scalar(path,('u64','1'))
        registry=self.registry(); value=Mutable()
        watch=registry.add(WatchConfig(trigger='every')).watch_id
        registry.observe(value,self.context(0)); value.line=2
        registry.observe(value,self.context(1))
        self.assertEqual(registry.current(watch).kind,'unchanged')

    def test_health_tracks_truncated_nodes_and_baseline_evictions(self):
        registry = self.registry(limits=WatchLimits(max_baseline_bytes=0))
        registry.add(WatchConfig(trigger='every', limits=InspectLimits(max_value_bytes=1,max_bytes=32768)))
        registry.observe('abcdef', self.context(0))
        self.assertEqual(registry.health['truncated'], 1)
        self.assertEqual(registry.health['baseline_evictions'], 1)

    def test_lifecycle_reports_effective_boundary_and_removal(self):
        registry = self.registry()
        ack = registry.add_validated(WatchConfig(trigger='every'), 1)
        registry.observe(1, self.context(4))
        paused = registry.set_state(ack.watch_id, 'paused')
        status = registry.list()[0]
        self.assertEqual((paused.effective_sequence, status['effective_sequence']), (5, 5))
        self.assertFalse(status['config'].enabled)
        registry.observe(1, self.context(8))
        updated = registry.update(ack.watch_id, WatchConfig(trigger='every'))
        self.assertEqual((updated.effective_sequence, registry.list()[0]['effective_sequence']), (9, 9))
        removed = registry.remove(ack.watch_id)
        self.assertEqual((removed.effective_sequence, removed.display_schema, removed.resolved_type), (9, None, None))
        self.assertEqual(registry.snapshot()['events'][-1].kind, 'watch-removed')
        self.assertEqual(registry.list(), ())

    def test_configuration_snapshot_failure_does_not_advance_revision(self):
        from unittest.mock import patch
        registry = self.registry()
        ack = registry.add(WatchConfig())
        before = registry.list()
        revision = registry.capture_revision
        with patch('statelessness.debugger_watches._copy', side_effect=ModelError('snapshot failed')):
            with self.assertRaises(ModelError): registry.add(WatchConfig())
            with self.assertRaises(ModelError): registry.update(ack.watch_id, WatchConfig(trigger='every'))
        self.assertEqual(registry.capture_revision, revision)
        self.assertEqual(registry.list(), before)
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

    def test_drop_marker_waits_for_space_without_recursive_eviction(self):
        registry=self.registry(limits=WatchLimits(max_ring_records=2))
        registry.add(WatchConfig(trigger='every'))
        registry.observe(1,self.context(0))
        registry.observe(2,self.context(1))
        self.assertGreater(registry.health['dropped'],0)
        self.assertFalse(any(e.kind=='events-dropped' for e in registry.snapshot()['events']))
        lost=registry.health['dropped']
        registry.clear_ring()
        summary=registry.observe(3,self.context(2))
        events=registry.snapshot()['events']
        self.assertEqual(summary.emitted,1)
        self.assertEqual([e.kind for e in events],['changed','events-dropped'])
        self.assertEqual(events[-1].dropped_count,lost)
        self.assertEqual(registry.health['dropped'],lost)
    def test_snapshot_now_does_not_change_baseline_or_sequence(self):
        registry=self.registry(); ack=registry.add(WatchConfig())
        registry.observe(1,self.context(0))
        registry.snapshot_now(ack.watch_id,99,self.context(10))
        registry.observe(1,self.context(1))
        self.assertEqual(registry.snapshot()['at'].sequence,1)
        self.assertEqual(registry.snapshot()['events'][-1].kind,'snapshot')

    def test_failure_property_metadata_is_utf8_bounded_and_marked_partial(self):
        registry=self.registry(); ack=registry.add(WatchConfig(trigger='every'))
        properties=('é'*129,)+tuple(str(i) for i in range(16))
        context=self.context(0,property_failure=True,failing_properties=properties)
        registry.observe(1,context)
        event=registry.snapshot()['events'][-1]
        self.assertEqual(len(event.failing_properties),16)
        self.assertEqual(event.failing_properties[0],'é'*128)
        self.assertEqual(event.completeness,'value-limit')
        explicit=registry.snapshot_now(ack.watch_id,1,self.context(1,property_failure=True,failing_properties=properties))
        self.assertTrue(explicit.property_failure)
        self.assertEqual(explicit.failing_properties,event.failing_properties)
        self.assertEqual(explicit.completeness,'value-limit')
        omitted=registry.snapshot_now(ack.watch_id,1,self.context(2,property_failure=True,
            failing_properties=(),failing_properties_complete=False))
        self.assertEqual(omitted.failing_properties,())
        self.assertEqual(omitted.completeness,'value-limit')
        complete=registry.snapshot_now(ack.watch_id,1,self.context(3,property_failure=True,
            failing_properties=('p',)))
        self.assertEqual(complete.completeness,'complete')
        with self.assertRaises(ValueError): self.context(2,failing_properties=('bad\ud800',))
        self.assertEqual(registry.snapshot()['at'].sequence,0)

    def test_observation_and_snapshot_bound_long_schema_metadata(self):
        class Model:
            def schema(self): return DisplaySchema('é'*513)
            def inspect(self,path,cx): return cx.scalar(path,('bool',True))
        registry=self.registry(); ack=registry.add(WatchConfig(trigger='every'))
        registry.observe(Model(),self.context(0))
        event=registry.snapshot()['events'][-1]
        self.assertEqual(event.schema.name,'é'*512)
        self.assertEqual(event.completeness,'value-limit')
        explicit=registry.snapshot_now(ack.watch_id,Model(),self.context(1))
        self.assertEqual(explicit.schema.name,'é'*512)
        self.assertEqual(explicit.completeness,'value-limit')

    def test_failure_metadata_does_not_allow_oversized_current_or_snapshot(self):
        registry=self.registry(limits=WatchLimits(max_event_bytes=700))
        ack=registry.add(WatchConfig(trigger='every',limits=InspectLimits(max_bytes=700)))
        context=self.context(0,property_failure=True,failing_properties=('x'*256,))
        with self.assertRaises(ModelError): registry.snapshot_now(ack.watch_id,1,context)
        summary=registry.observe(1,context)
        self.assertEqual(summary.inspection_errors,1)
        event=registry.current(ack.watch_id)
        self.assertTrue(event is None or event.kind=='inspector-error')

    def test_status_and_returned_snapshots_are_detached(self):
        registry = self.registry()
        ack = registry.add_validated(WatchConfig(), 1)
        object.__setattr__(ack.display_schema, 'name', 'caller mutation')
        context = self.context(0)
        registry.observe(1, context)
        object.__setattr__(context.snapshot, 'sequence', 888)
        status = registry.list()[0]
        self.assertTrue(status['enabled'])
        self.assertNotEqual(status['validated_schema'].name, 'caller mutation')
        self.assertEqual((status['resolved_type'], status['resolved_wire_type']), ('scalar', 'python-int'))
        object.__setattr__(status['validated_schema'], 'name', 'status mutation')
        snapshot = registry.snapshot()
        object.__setattr__(snapshot['at'], 'sequence', 999)
        self.assertEqual(registry.snapshot()['at'].sequence, 0)
        self.assertNotEqual(registry.list()[0]['validated_schema'].name, 'status mutation')
        context = self.context(1)
        event = registry.snapshot_now(ack.watch_id, 2, context)
        object.__setattr__(context.snapshot, 'sequence', 777)
        self.assertEqual(event.snapshot.sequence, 1)
        object.__setattr__(event.value, 'value', ('python-int', '999'))
        self.assertEqual(registry.snapshot()['events'][-1].value.value, ('python-int', '2'))
        self.assertEqual(registry.snapshot()['retained_bytes'], registry.health['retained_bytes'])

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
        # Rust WatchRegistry::observe: post-evaluation expiry keeps the current value.
        self.assertEqual(registry.current(ack.watch_id).snapshot.sequence,3)
        self.assertTrue(registry.list()[0]['has_baseline'])
        self.assertEqual(registry.observe(4,self.context(4)).selected,0)
        self.assertEqual(registry.current(ack.watch_id).snapshot.sequence,3)

    def test_ttl_expires_on_final_sampled_out_observation(self):
        registry=self.registry()
        ack=registry.add(WatchConfig(trigger='every',evaluation_interval=2,ttl_observations=2))
        first=registry.observe(0,self.context(0))
        self.assertEqual((first.selected,first.evaluated),(1,1))
        second=registry.observe(1,self.context(1))
        self.assertEqual((second.selected,second.evaluated,second.emitted),(1,0,0))
        self.assertEqual(registry.list()[0]['state'],'expired')
        # Sampled-out expiry keeps the last evaluated value (Rust watches.rs observe).
        self.assertEqual(registry.current(ack.watch_id).snapshot.sequence,0)
        self.assertEqual([event.kind for event in registry.snapshot()['events']].count('watch-expired'),1)
        self.assertEqual(registry.observe(2,self.context(2)).selected,0)

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
        self.assertEqual(registry.current(ack.watch_id).previous_snapshot.sequence,0)
        self.assertEqual(registry.current(ack.watch_id).difference,'unknown')
        self.assertEqual(registry.current(ack.watch_id).kind,'property-failure')
        value.fail=True
        registry.observe(value,self.context(2,property_failure=True))
        self.assertEqual(registry.current(ack.watch_id).kind,'inspector-error')
        self.assertFalse(registry.list()[0]['has_baseline'])
        value.fail=False
        registry.observe(value,self.context(3,property_failure=True))
        self.assertEqual(registry.current(ack.watch_id).previous_snapshot.sequence,2)

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
            self.assertEqual(registry.current(id).kind,'baseline:observation-gap')
            self.assertEqual(registry.current(id).previous_snapshot.sequence,1)

    def test_failed_projection_obeys_trigger_and_delivery_sampling(self):
        class Mutable:
            def __init__(self): self.fail=True
            def schema(self): return DisplaySchema('Mutable')
            def inspect(self,path,cx):
                if self.fail: raise RuntimeError('inspection unavailable')
                return cx.scalar(path,('u64','7'))
        registry=self.registry(); value=Mutable()
        changed=registry.add(WatchConfig(trigger='changed',delivery_interval=2)).watch_id
        equals=registry.add(WatchConfig(trigger='equals',equals=('u64','1'))).watch_id
        registry.clear_ring()
        first=registry.observe(value,self.context(0))
        self.assertEqual((first.evaluated,first.emitted),(2,1))
        self.assertEqual([event.watch_id for event in registry.snapshot()['events']],[changed])
        self.assertEqual(registry.current(equals).kind,'inspector-error')
        second=registry.observe(value,self.context(1))
        self.assertEqual((second.evaluated,second.emitted),(2,0))
        self.assertEqual(len(registry.snapshot()['events']),1)
        value.fail=False
        third=registry.observe(value,self.context(2))
        self.assertEqual((third.evaluated,third.emitted),(2,1))
        for id in (changed,equals):
            event=registry.current(id)
            self.assertEqual(event.kind,'baseline:observation-gap')
            self.assertEqual(event.previous_snapshot.sequence,1)

    def test_failure_forced_equals_watch_retains_inspector_error(self):
        class Broken:
            def schema(self): return DisplaySchema('Broken')
            def inspect(self,path,cx): raise RuntimeError('projection failed')
        registry=self.registry()
        watch=registry.add(WatchConfig(trigger='equals',equals=('u64','7'),evaluation_interval=5,delivery_interval=5)).watch_id
        summary=registry.observe(Broken(),self.context(0,property_failure=True,failing_properties=('p',)))
        event=registry.current(watch)
        self.assertEqual(summary.emitted,1)
        self.assertEqual(event.kind,'inspector-error')
        self.assertIsNone(event.value)
        self.assertTrue(event.property_failure)
        self.assertEqual(event.failing_properties,('p',))
        self.assertEqual(registry.failure_snapshot['events'][-1].kind,'inspector-error')

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
