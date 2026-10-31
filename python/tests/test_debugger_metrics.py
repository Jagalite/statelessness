"""Manual arithmetic and boundedness expectations, independent of Rust execution."""
import json
import unittest
from dataclasses import replace
from statelessness.debugger_metrics import *
from statelessness.debugger_dashboard import Dashboard, correlate


class MetricsTests(unittest.TestCase):
    def store(self,**kw):return MetricsStore((1<<64)-1,SnapshotTime(9,0),schema=HistogramSchema((1<<64)-1,(10,20)),**kw)
    def test_histogram_manual_golden(self):
        s=self.store()
        for n in (0,10,11,30):s.observe(MetricFamily.QUEUE_WAIT,Labels(),n)
        h=s.current().get(MetricFamily.QUEUE_WAIT)
        self.assertEqual((h.buckets[:3],h.count,h.sum_ns),([2,1,1],4,51))
        self.assertEqual(h.quantile(s.current().schema,50,100),Quantile(10,4,True))
        self.assertEqual(h.quantile(s.current().schema,95,100),Quantile(None,4,True))
        self.assertIsNone(h.quantile(s.current().schema,101,100))
        self.assertIsNone(Histogram().quantile(s.current().schema,50,100))
    def test_histogram_lookup(self):
        s=self.store();s.observe(MetricFamily.QUEUE_WAIT,Labels(),10)
        s.increment(MetricFamily.REQUESTS)
        snapshot=s.current()
        self.assertIs(snapshot.histogram(MetricFamily.QUEUE_WAIT),snapshot.get(MetricFamily.QUEUE_WAIT))
        self.assertIsNone(snapshot.histogram(MetricFamily.REQUESTS))
        self.assertIsNone(snapshot.histogram(MetricFamily.QUEUE_WAIT,Labels(origin='ReplayHarness')))
    def test_fullwidth_overflow_import_and_caps(self):
        s=self.store(limits=MetricLimits(max_series=1))
        s.increment(MetricFamily.REQUESTS,n=U64_MAX)
        s.increment(MetricFamily.REQUESTS,n=1)
        self.assertEqual(s.current().get(MetricFamily.REQUESTS),U64_MAX)
        s.increment(MetricFamily.ATTEMPTS)
        s.increment(MetricFamily.REQUESTS,Labels(origin='ReplayHarness'))
        s.increment(MetricFamily.REQUESTS,Labels(operation=1))
        self.assertEqual((s.health.arithmetic_errors,s.health.rejected_series,s.health.imported_ignored,s.health.invalid_labels),(1,1,1,1))
        self.assertFalse(s.current().complete)
        self.assertEqual(s.current().epoch,U64_MAX)
    def test_windows_unknown_endpoints_watermark_and_retention(self):
        s=self.store(limits=MetricLimits(max_snapshots=2))
        s.increment(MetricFamily.REQUESTS,n=2);a=s.snapshot(SnapshotTime(9,10))
        s.increment(MetricFamily.REQUESTS,n=3);self.assertIsNone(s.current().end)
        b=s.snapshot(SnapshotTime(9,20));w=s.window(a.revision,b.revision)
        self.assertEqual((w.get(MetricFamily.REQUESTS),w.temporality,w.start),(3,'delta',a.end))
        s.snapshot(None);bad=s.snapshot(SnapshotTime(9,19))
        self.assertIsNone(bad.end);self.assertEqual(bad.health.invalid_measurements,1)
        with self.assertRaisesRegex(MetricError,'unknown window'):s.window(a.revision,b.revision)
        self.assertGreater(s.health.snapshots_evicted,0)
        with self.assertRaisesRegex(MetricError,'incompatible'):MetricSnapshot.window(a,replace(b,epoch=1))
    def test_transactional_snapshot_and_merge(self):
        s=self.store();a=s.snapshot(SnapshotTime(9,10));ids=s.available_windows()
        def reject(_):raise RuntimeError('transport refused')
        with self.assertRaises(RuntimeError):s.snapshot(SnapshotTime(9,11),validate=reject)
        self.assertEqual(s.available_windows(),ids);self.assertEqual(s.current(),a)
        x=self.store();x.increment(MetricFamily.REQUESTS,n=U64_MAX)
        y=x.current();before=x.current()
        with self.assertRaisesRegex(MetricError,'overflow'):y.merge_disjoint(before)
        self.assertEqual(y,before)
    def test_snapshot_rejects_reentrant_mutations_and_releases_guard(self):
        s=self.store();s.increment(MetricFamily.REQUESTS,n=2)
        before=s.current();windows=s.available_windows()
        def reenter(_):s.increment(MetricFamily.REQUESTS,n=3)
        with self.assertRaisesRegex(MetricError,'snapshot in progress'):
            s.snapshot(SnapshotTime(9,1),validate=reenter)
        self.assertEqual(s.current(),before);self.assertEqual(s.available_windows(),windows)
        def nested(_):s.snapshot(SnapshotTime(9,1))
        with self.assertRaisesRegex(MetricError,'snapshot in progress'):
            s.snapshot(SnapshotTime(9,1),validate=nested)
        prepared=protocol_adapters(s)['metric_snapshot']((None,0,1))
        with self.assertRaisesRegex(MetricError,'snapshot in progress'):
            s.snapshot(SnapshotTime(9,1),validate=lambda _:prepared.commit())
        self.assertEqual(s.current(),before);self.assertEqual(s.available_windows(),windows)
        s.increment(MetricFamily.REQUESTS,n=3)
        self.assertEqual(s.current().get(MetricFamily.REQUESTS),5)
    def test_saturated_revision_window_uses_first_retained_match(self):
        s=self.store();s._snapshot.revision=U64_MAX
        first=s.snapshot(SnapshotTime(9,1));s.snapshot(SnapshotTime(9,2))
        self.assertEqual(s.window(U64_MAX,U64_MAX).start,first.end)
    def test_gauge_scope_and_production(self):
        s=self.store(gauge_scope='CompleteParticipation');l=Labels()
        s.set_gauge(MetricFamily.RUNNING,l,2);a=s.snapshot(SnapshotTime(9,1))
        s.gauge_delta(MetricFamily.RUNNING,l,-1);b=s.snapshot(SnapshotTime(9,2))
        self.assertEqual(MetricSnapshot.window(a,b).get(MetricFamily.RUNNING),1)
        s.gauge_delta(MetricFamily.RUNNING,l,-2)
        self.assertEqual(s.current().gauge_scope,'Unknown');self.assertEqual(s.current().get(MetricFamily.RUNNING),1)
        s.increment(MetricFamily.REQUESTS,Labels(debugger_affected=True))
        self.assertIsNone(s.current().production().get(MetricFamily.REQUESTS,Labels(debugger_affected=True)))

    def test_window_rejects_changed_gauge_value_type(self):
        s=self.store();s.set_gauge(MetricFamily.RUNNING,Labels(),2)
        start=s.snapshot(SnapshotTime(9,1));end=s.snapshot(SnapshotTime(9,2))
        end.series[(MetricFamily.RUNNING,Labels())]=Histogram()
        with self.assertRaisesRegex(MetricError,'incompatible'):
            MetricSnapshot.window(start,end)
        self.assertEqual(start.get(MetricFamily.RUNNING),2)

    def test_production_filters_scope_by_labels_independently(self):
        snapshot=self.store().current()
        live=(MetricFamily.RUNNING,Labels())
        debug=(MetricFamily.RUNNING,Labels(debugger_affected=True))
        snapshot.gauge_scopes={live:'HostSnapshot',debug:'Unknown'}
        production=snapshot.production()
        self.assertEqual(production.gauge_scopes,{live:'HostSnapshot'})
        self.assertEqual(len(snapshot.gauge_scopes),2)
    def test_dashboard_unknowns_payload_free_cap_and_fullwidth(self):
        s=self.store();s.increment(MetricFamily.REQUESTS,n=U64_MAX)
        d=Dashboard.from_snapshot(s.current(),{},[Labels()],18)
        report=json.loads(d.to_json(30000));self.assertEqual(report['epoch'],U64_MAX)
        self.assertEqual(report['rows'][0]['value'],U64_MAX)
        self.assertIsNone(report['rows'][1]['value']);self.assertTrue(d.observation_complete)
        with self.assertRaisesRegex(ValueError,'byte budget'):d.to_json(20)
        self.assertFalse(Dashboard.from_snapshot(s.current(),{},[Labels()],1).observation_complete)
        self.assertEqual(len(Dashboard.from_snapshot(s.current(),{},iter([Labels()]*100),0).rows),0)
    def test_correlation_boundaries(self):
        from types import SimpleNamespace as N
        origin=N(run=U64_MAX,epoch=2,machine=3,transition_sequence=11,output_index=0)
        t=N(origin=origin,effect=N(run=U64_MAX,epoch=2),complete=False)
        e=N(provenance=N(run=str(U64_MAX),epoch=2),checkpoint_after=10,steps=[N(sequence=11,causal=N(machine='3'))],trace=N(steps=[N(outputs=[b'secret'])]))
        c=correlate(e,t);self.assertEqual((c.kind,c.local_sequence,c.output,c.detail_complete),('retained',1,0,False))
        origin.transition_sequence=10;self.assertEqual(correlate(e,t).kind,'before_checkpoint')
        origin.transition_sequence=11;origin.machine=4;self.assertEqual(correlate(e,t).reason,'machine')
    def test_concrete_protocol_bindings(self):
        s=self.store();s.increment(MetricFamily.REQUESTS,n=U64_MAX)
        a=protocol_adapters(s,max_page_size=2)
        prepared=a['metric_snapshot']((None,0,1));fields=dict(prepared.fields)
        self.assertEqual(s.available_windows(),());prepared.commit()
        self.assertEqual(fields['metrics.series.0.value'],f'u64:{U64_MAX}')
        keys=[key for key,_ in prepared.fields if key.startswith('metrics.series.0.')]
        self.assertEqual(keys[4:13],['metrics.series.0.operation.id','metrics.series.0.component.id',
            'metrics.series.0.worker_pool.id','metrics.series.0.outcome','metrics.series.0.measurement_origin',
            'metrics.series.0.debugger_affected','metrics.series.0.operation.name',
            'metrics.series.0.component.name','metrics.series.0.worker_pool.name'])
        revision=int(fields['metrics.revision'][4:]);s.increment(MetricFamily.ATTEMPTS)
        handle=int(fields['metrics.handle'][4:]);old=dict(a['metric_snapshot']((handle,0,1)))
        self.assertEqual(old['metrics.series.total'],'u64:1')
        with self.assertRaisesRegex(ValueError,'limit'):a['metric_snapshot']((None,0,3))
        self.assertEqual(dict(a['metric_catalog']((0,1)))['catalog.0.name'],'string:effect_requests_total')
    def test_manual_fixture_and_snapshot_rejection(self):
        from pathlib import Path
        golden=json.loads((Path(__file__).resolve().parents[2]/'conformance/debugger-diagnostics-v1.json').read_text(encoding="utf-8"))['histogram']
        s=MetricsStore(1,schema=HistogramSchema(1,tuple(map(int,golden['bounds_ns']))))
        for n in golden['samples_ns']:s.observe(MetricFamily.QUEUE_WAIT,Labels(),int(n))
        h=s.current().get(MetricFamily.QUEUE_WAIT)
        self.assertEqual(h.buckets[:3],list(map(int,golden['bucket_counts'])))
        r=self.store(limits=MetricLimits(max_snapshot_bytes=1));v=r.snapshot()
        self.assertEqual(v.health.snapshot_rejections,1);self.assertEqual(r.available_windows(),())
        # An empty finite-bound schema has a valid overflow-only histogram.
        h=Histogram();h.add(HistogramSchema(1,()),3)
        self.assertEqual(h.quantile(HistogramSchema(1,()),50,100),Quantile(None,1,True))
    def test_merge_disjoint_gauges_and_histograms(self):
        a=self.store();b=self.store()
        for s,n in ((a,10),(b,30)):
            s.increment(MetricFamily.REQUESTS,n=1)
            s.set_gauge(MetricFamily.RUNNING,Labels(),1)
            s.observe(MetricFamily.QUEUE_WAIT,Labels(),n)
        x=a.current();x.merge_disjoint(b.current())
        self.assertEqual(x.get(MetricFamily.REQUESTS),2)
        self.assertEqual(x.get(MetricFamily.RUNNING),2)
        self.assertEqual(x.get(MetricFamily.QUEUE_WAIT).buckets[:3],[1,0,1])
        self.assertEqual(x.get(MetricFamily.QUEUE_WAIT).sum_ns,40)
    def test_dashboard_full_wire_golden(self):
        s=self.store();s.increment(MetricFamily.REQUESTS,n=U64_MAX)
        report=json.loads(Dashboard.from_snapshot(s.current(),{},[Labels()],1).to_json(30000))
        metric_health=dict(rejected_series=0,invalid_labels=0,arithmetic_errors=0,omitted_measurements=0,invalid_measurements=0,imported_ignored=0,snapshots_evicted=0,snapshot_rejections=0)
        health=dict(dropped_detail_events=0,detail_evictions=0,detail_rejections=0,clock_failures=0,callback_faults=0,destructor_hook_losses=0,invalid_lifecycle=0,active_effect_handles=0,duplicate_observations=0,duplicate_deliveries=0,abandoned=0,pause_interval_evictions=0,debugger_affected_measurements=0,retained_detail_records=0,retained_detail_bytes=0,effect_handle_bytes=0,attempt_handle_bytes=0,delivery_handle_bytes=0,unobserved_native_debugger_interference_possible=True,host_metric_health=metric_health,**metric_health)
        row=dict(metric='effect_requests_total',operation=0,component=0,worker_pool=0,outcome='None',measurement_origin='Live',debugger_affected=False,value=U64_MAX,gauge_scope=None,p50=None,p95=None,p99=None)
        self.assertEqual(report,dict(schema='statelessness.effect-dashboard/1',epoch=U64_MAX,revision=1,temporality='cumulative',start=dict(clock_domain=9,nanos=0),end=None,truncated=True,observation_complete=False,health=health,rows=[row]))
        # Missing observations are represented by null, never invented zero.
        rows=json.loads(Dashboard.from_snapshot(s.current(),{},[Labels()],2).to_json(30000))['rows']
        self.assertEqual(rows[1],{**row,'metric':'effect_admissions_total','value':None})
    def test_protocol_one_capture_replacement_window_and_disconnect(self):
        from statelessness.debugger_protocol import ProtocolError
        s=self.store();s.increment(MetricFamily.REQUESTS,n=2)
        a=protocol_adapters(s);p=a['metric_snapshot']((None,0,1));p.commit();f=dict(p.fields);handle=int(f['metrics.handle'][4:]);first=int(f['metrics.revision'][4:])
        s.increment(MetricFamily.REQUESTS,n=3)
        self.assertEqual(dict(a['metric_snapshot']((handle,0,1)))['metrics.series.0.value'],'u64:2')
        p=a['metric_snapshot']((None,0,1));p.commit();second=int(dict(p.fields)['metrics.revision'][4:])
        with self.assertRaisesRegex(ProtocolError,'stale_handle'):a['metric_snapshot']((handle,0,1))
        w=a['metric_window']((first,second,1));self.assertEqual(dict(w.fields)['metrics.temporality'],'enum:Delta');self.assertEqual(dict(w.fields)['metrics.series.0.value'],'u64:3');w.commit()
        handle=int(dict(w.fields)['metrics.handle'][4:]);a['metric_snapshot'].reset_connection()
        with self.assertRaisesRegex(ProtocolError,'stale_handle'):a['metric_snapshot']((handle,0,1))
        before=s.available_windows()
        with self.assertRaisesRegex(ProtocolError,'invalid_input'):a['metric_snapshot']((None,1,1))
        self.assertEqual(s.available_windows(),before)
    def test_report_budget_stops_before_row_projection_and_rejects_payload_health(self):
        from statelessness.debugger_dashboard import Row
        class Poison:
            @property
            def operation(self):raise AssertionError('row projected despite exhausted header budget')
        s=self.store();d=Dashboard.from_snapshot(s.current(),{},[Labels()],1)
        d=replace(d,rows=(Row(MetricFamily.REQUESTS,Poison(),None,None,None,None,None),))
        with self.assertRaisesRegex(ValueError,'byte budget'):d.to_json(20)
        with self.assertRaises(ValueError):Dashboard.from_snapshot(s.current(),{'clock_failures':'payload-secret'},[Labels()],1)
