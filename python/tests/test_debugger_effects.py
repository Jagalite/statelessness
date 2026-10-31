import unittest
import asyncio
from statelessness.debugger_effects import (EffectObserver,EffectOptions,RequestOrigin,LocalInstant,MeasurementError,LifecycleError,SlowTimingQuery)
from statelessness.debugger_metrics import MetricFamily as F,Labels

class Clock:
    def __init__(self): self.t=0; self.calls=0
    def now(self): self.calls+=1; self.t+=10; return LocalInstant(1,self.t)

class EffectTests(unittest.TestCase):
    def setup_effect(self,**kw):
        clock=Clock(); o=EffectObserver(EffectOptions(**kw),clock)
        e=o.requested(RequestOrigin(1,1,2,2**63+5,0)); o.admission(e,'Accepted'); return o,e,clock
    def test_manual_sequential_timing_and_fast_publication(self):
        o,e,c=self.setup_effect(); a=o.attempt_created(e); o.ready_queued(a); o.attempt_started(a); o.attempt_finished(a,'Success'); o.resolved(e,'Success',a)
        d=o.reserve_publication(e); o.delivery_begun(d); o.publication_committed(d); o.delivery_observed(d,'Rejected'); o.settled(e)
        detail=o.effect_details(e.id)
        self.assertEqual(detail.origin.transition_sequence,2**63+5)
        timings={t.family:t.duration_ns for t in detail.timings}
        self.assertEqual(timings[F.QUEUE_WAIT],10)
        self.assertEqual(timings[F.ATTEMPT_ELAPSED],10)
        self.assertEqual(timings[F.RESOLUTION_OVERHEAD],10)
        self.assertEqual(timings[F.COMPLETION_PREPARATION],10)
        self.assertEqual(timings[F.COMPLETION_PROCESSING],10)
        self.assertEqual(o.metric_snapshot().get(F.RUNNING),0)
        self.assertEqual(o.metric_snapshot().get(F.DELIVERIES,Labels(outcome='Rejected')),1)
        with self.assertRaisesRegex(LifecycleError,'Duplicate'): o.delivery_observed(d,'Rejected')
        result=o.slow_timings(SlowTimingQuery(0,F.ATTEMPT_ELAPSED,10))
        self.assertEqual((result['matched'],len(result['matches'])),(1,1))
    def test_cancellation_does_not_terminate_running_attempt(self):
        o,e,c=self.setup_effect(); a=o.attempt_created(e); o.attempt_started(a)
        o.cancellation_requested(e); o.cancellation_acknowledged(e); o.resolved(e,'Cancelled')
        s=o.metric_snapshot(); self.assertEqual(s.get(F.RUNNING),1); self.assertEqual(s.get(F.UNRESOLVED),0)
        o.attempt_finished(a,'Success'); self.assertEqual(o.metric_snapshot().get(F.RUNNING),0)
    def test_foreign_tokens_phase_and_bounded_retention(self):
        o,e,c=self.setup_effect(max_details=1,max_events_per_effect=2,max_attempts_per_effect=1)
        other=EffectObserver(EffectOptions(),c)
        with self.assertRaisesRegex(LifecycleError,'Foreign'): other.admission(e,'Accepted')
        a=o.attempt_created(e)
        with self.assertRaisesRegex(LifecycleError,'Limit'): o.attempt_created(e)
        with self.assertRaisesRegex(LifecycleError,'WrongPhase'): o.attempt_finished(a,'Success')
        o.attempt_started(a); o.attempt_finished(a,'Success')
        self.assertFalse(o.effect_details(e.id).complete)
        e2=o.requested(RequestOrigin(1,1,2,1,0)); self.assertIsNone(o.effect_details(e.id))
        self.assertEqual(o.telemetry_health()['detail_evictions'],1)
    def test_counters_only_never_reads_clock(self):
        class Forbidden:
            def now(self): raise AssertionError('clock called')
        o=EffectObserver(EffectOptions(timings=False),Forbidden()); e=o.requested(RequestOrigin(1,1,0,0,0)); o.admission(e,'Accepted')
        a=o.attempt_created(e); o.attempt_started(a); o.attempt_finished(a,'Failure'); o.resolved(e,'Failure')
        self.assertEqual(o.telemetry_health()['clock_failures'],0)
        self.assertEqual(o.effect_details(e.id).timings,[])
    def test_clock_errors_unknown_not_zero_and_pause_provenance(self):
        o,e,c=self.setup_effect(); a=o.attempt_created(e); o.attempt_started(a); o.debugger_pause(True); o.debugger_pause(False)
        c.now=lambda: LocalInstant(2,0)
        o.attempt_finished(a,'Success'); d=o.effect_details(e.id)
        t=next(t for t in d.timings if t.family==F.ATTEMPT_ELAPSED)
        self.assertIsNone(t.duration_ns); self.assertEqual(t.error,'DifferentClockDomain'); self.assertTrue(t.debugger_affected)
        self.assertFalse(o.metric_snapshot().complete)
        self.assertEqual(o.slow_timings(SlowTimingQuery(0,F.ATTEMPT_ELAPSED,0,include_debugger_affected=True))['unknown_measurements'],1)
    def test_failed_publication_and_duplicate_deliveries(self):
        o,e,c=self.setup_effect(); o.resolved(e,'Success'); d=o.reserve_publication(e); o.publication_failed(d)
        with self.assertRaises(LifecycleError): o.delivery_begun(d)
        for _ in range(2):
            d=o.reserve_publication(e); o.delivery_begun(d); o.delivery_observed(d,'Ignored')
        self.assertEqual(o.telemetry_health()['duplicate_deliveries'],1)
        self.assertEqual(o.metric_snapshot().get(F.COMPLETION_DEPTH),0)
    def test_awaitable_classifier_fault_preserves_result(self):
        o,e,c=self.setup_effect(); a=o.attempt_created(e)
        async def work(): return object()
        def broken(v): raise ValueError('classifier')
        async def run(): return await o.observe_awaitable(a,work,broken)
        value=asyncio.run(run())
        self.assertIsNotNone(value); self.assertEqual(o.telemetry_health()['callback_faults'],1)
    def test_retry_backoff_and_removed_ready_accounting(self):
        o,e,c=self.setup_effect(); a=o.attempt_created(e); o.attempt_started(a); o.attempt_finished(a,'Failure')
        b=o.attempt_created(e); o.retry_scheduled(b,2**63+1); o.ready_queued(b); o.ready_removed(b); o.ready_queued(b); o.attempt_started(b); o.attempt_finished(b,'Success'); o.resolved(e,'Success',b)
        detail=o.effect_details(e.id)
        self.assertTrue(any(t.family==F.INTER_ATTEMPT_GAP and t.duration_ns>0 for t in detail.timings))
        self.assertEqual(o.metric_snapshot().get(F.READY_DEPTH),0)
        self.assertEqual(o.metric_snapshot().get(F.SCHEDULED_BACKOFF).sum_ns,2**63+1)
    def test_raw_dropped_attempt_does_not_invent_termination(self):
        o,e,c=self.setup_effect(); a=o.attempt_created(e); o.attempt_started(a); del a
        import gc
        gc.collect()
        self.assertEqual(o.telemetry_health()['abandoned'],1)
        self.assertEqual(o.metric_snapshot().get(F.RUNNING),1)
        self.assertFalse(o.effect_details(e.id).complete)
    def test_clock_missing_is_omitted_and_no_zero_sample(self):
        o,e,c=self.setup_effect(); a=o.attempt_created(e); o.attempt_started(a)
        def unavailable(): raise OSError('no clock')
        c.now=unavailable; o.attempt_finished(a,'Success')
        snap=o.metric_snapshot()
        self.assertIsNone(snap.get(F.ATTEMPT_ELAPSED,Labels(outcome='Success')))
        self.assertGreater(snap.health.omitted_measurements,0)
        self.assertEqual(o.effect_details(e.id).timings[-1].error,'MissingEndpoint')
    def test_slow_query_limits_and_snapshot_copy(self):
        o,e,c=self.setup_effect(); a=o.attempt_created(e); o.attempt_started(a); o.attempt_finished(a,'Success')
        calls=c.calls
        result=o.slow_timings(SlowTimingQuery(0,F.ATTEMPT_ELAPSED,0,max_results=0))
        self.assertTrue(result['truncated']); self.assertFalse(result['complete']); self.assertEqual(c.calls,calls)
        d=o.effect_details(e.id); d.events.clear(); self.assertGreater(len(o.effect_details(e.id).events),0)
    def test_clock_reentrancy_is_rejected_without_nested_request(self):
        o,e,c=self.setup_effect(); a=o.attempt_created(e)
        def reentrant():
            o.requested(RequestOrigin(1,1,0,0,0))
            return LocalInstant(1,9)
        c.now=reentrant; o.attempt_started(a)
        self.assertEqual(o.telemetry_health()['retained_detail_records'],1)
        self.assertGreater(o.telemetry_health()['clock_failures'],0)
    def test_concurrent_admission_is_once_only(self):
        from concurrent.futures import ThreadPoolExecutor
        c=Clock(); o=EffectObserver(EffectOptions(timings=False),c); e=o.requested(RequestOrigin(1,1,0,0,0))
        def admit(_):
            try: o.admission(e,'Accepted'); return True
            except LifecycleError: return False
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(admit,range(2)))
        self.assertEqual(sorted(results),[False,True]); self.assertEqual(o.metric_snapshot().get(F.UNRESOLVED),1)
    def test_lazy_adapter_close_never_executes_factory(self):
        o,e,c=self.setup_effect(); a=o.attempt_created(e); called=[]
        adapter=o.observe_awaitable(a,lambda:called.append(True),lambda _: 'Success')
        self.assertEqual(called,[]); adapter.close(); self.assertEqual(called,[])
        self.assertTrue(o.effect_details(e.id).abandoned)
        self.assertIsNone(o.metric_snapshot().get(F.RUNNING))
    def test_adapter_hook_errors_do_not_replace_operation(self):
        o,e,c=self.setup_effect(); other,foreign,c2=self.setup_effect(); a=other.attempt_created(foreign); calls=[]
        async def success(): calls.append(1); return 42
        async def run(): return await o.observe_awaitable(a,success,lambda _: 'Success')
        self.assertEqual(asyncio.run(run()),42); self.assertEqual(calls,[1])
        original=ValueError('host error')
        async def failure(): raise original
        async def run_failure(): return await o.observe_awaitable(a,failure,lambda _: 'Success')
        with self.assertRaises(ValueError) as found: asyncio.run(run_failure())
        self.assertIs(found.exception,original)
    def test_passive_effect_protocol_preserves_ids_and_no_clock(self):
        from statelessness.debugger_effects import effect_protocol_adapters
        o,e,c=self.setup_effect(); a=o.attempt_created(e); o.attempt_started(a); o.attempt_finished(a,'Success'); o.resolved(e,'Success',a)
        adapters=effect_protocol_adapters(o); calls=c.calls
        raw=adapters['effect_details']((e.id.run,e.id.epoch,e.id.serial))
        fields=dict(raw)
        self.assertEqual([key for key,_ in raw].count('effect.debugger_affected'),1)
        self.assertEqual(fields['effect.labels.debugger_affected'],'bool:false')
        self.assertEqual(fields['effect.origin.transition'],'u64:'+str(2**63+5))
        timeline=dict(adapters['effect_timeline']((e.id.run,e.id.epoch,e.id.serial,1)))
        self.assertEqual(timeline['timeline.truncated'],'bool:true')
        self.assertTrue(adapters['telemetry_health'](())); self.assertEqual(c.calls,calls)

    def test_effect_wire_distinguishes_original_label_from_aggregate(self):
        from statelessness.debugger_effects import effect_protocol_adapters
        o,e,_=self.setup_effect(); a=o.attempt_created(e); o.attempt_started(a)
        o.debugger_pause(True); o.debugger_pause(False); o.attempt_finished(a,'Success')
        adapters=effect_protocol_adapters(o); args=(e.id.run,e.id.epoch,e.id.serial)
        for result in (adapters['effect_details'](args),adapters['effect_timeline']((*args,10))):
            fields=dict(result); keys=[key for key,_ in result]
            self.assertEqual(fields['effect.labels.debugger_affected'],'bool:false')
            self.assertEqual(fields['effect.debugger_affected'],'bool:true')
            self.assertEqual(len(keys),len(set(keys)))
            self.assertLess(keys.index('effect.labels.debugger_affected'),keys.index('effect.complete'))
            self.assertGreater(keys.index('effect.debugger_affected'),keys.index('effect.cancellation_acknowledged'))

    def test_effect_timeline_wire_orders_kind_after_event_payload(self):
        from statelessness.debugger_effects import effect_protocol_adapters
        o,e,c=self.setup_effect(); o.attempt_created(e)
        result=effect_protocol_adapters(o)['effect_timeline']((e.id.run,e.id.epoch,e.id.serial,10))
        keys=[key for key,_ in result]
        for key in keys:
            if key.startswith('timeline.events.') and key.endswith('.attempt'):
                self.assertLess(keys.index(key),keys.index(key.rsplit('.',1)[0]+'.kind'))
                break
        else: self.fail('expected attempt event')

    def test_telemetry_health_wire_order_and_unknown_pause_boundaries(self):
        from statelessness.debugger_effects import effect_protocol_adapters
        o,e,c=self.setup_effect()
        o.pause_intervals=lambda: [{'generation':3,'start':None,'end':None}]
        result=effect_protocol_adapters(o)['telemetry_health'](())
        expected=('destructor_hook_losses','callback_faults','pause_interval_evictions',
            'detail_evictions','detail_rejections','detail_event_drops','duplicate_observations',
            'duplicate_deliveries','invalid_lifecycle','abandoned','clock_failures',
            'debugger_affected_measurements','active_effect_handles','retained_detail_records',
            'retained_detail_bytes','metric.rejected_series','metric.invalid_labels',
            'metric.arithmetic_errors','metric.omitted_measurements','metric.invalid_measurements',
            'metric.imported_ignored','metric.snapshots_evicted','metric.snapshot_rejections',
            'unobserved_native_debugger_interference_possible')
        self.assertEqual([key for key,_ in result[:len(expected)]],['telemetry.'+key for key in expected])
        fields=dict(result)
        self.assertEqual(fields['pauses.0.start'],'unknown:clock_or_open_interval')
        self.assertEqual(fields['pauses.0.end'],'unknown:clock_or_open_interval')
