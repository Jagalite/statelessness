"""Scoped capture ack, lazy fanout and independent bounded exporters."""
import unittest
from statelessness import ModelError
from statelessness.debugger_diagnostic import (DiagnosticHub, DiagnosticLimits,
    SinkPermissions, SinkLimits, Subscription)


class DiagnosticTests(unittest.TestCase):
    def hub(self):
        hub=DiagnosticHub(); hub.add_sink(1,SinkPermissions.local_all()); hub.register_producer(1,1)
        return hub
    def test_no_interest_lazy_and_ack_revisions(self):
        hub=self.hub(); calls=[]
        ack=hub.configure(0,(Subscription(1,'value'),))
        self.assertEqual(ack.pending_producers,(1,))
        with hub.begin_turn(1,1,0) as turn: turn.probe('value',lambda: calls.append(1))
        self.assertEqual(calls,[])
        hub.acknowledge(1,1,1)
        with hub.begin_turn(1,1,1) as turn: turn.probe('value',lambda: calls.append(1) or 42)
        self.assertEqual(calls,[1])
        self.assertEqual(hub.peek(1).capture_revision,1)
        with self.assertRaisesRegex(ModelError,'stale'): hub.configure(0,())
    def test_probe_once_for_fanout_and_incomplete_turn(self):
        hub=self.hub(); hub.add_sink(2,SinkPermissions.local_all())
        hub.configure(0,(Subscription(1,'value'),Subscription(2,'value'))); hub.acknowledge(1,1,0)
        calls=[]
        with self.assertRaisesRegex(ValueError,'host failed'):
            with hub.begin_turn(1,1,0) as turn:
                turn.probe('value',lambda: calls.append(1) or 1)
                raise ValueError('host failed')
        self.assertEqual(calls,[1])
        self.assertTrue(hub.peek(1).incomplete_turn)
        self.assertTrue(hub.peek(2).incomplete_turn)
    def test_independent_queues_and_exporter_failure_consumes_record(self):
        hub=self.hub(); hub.add_sink(2,SinkPermissions.local_all(),SinkLimits(records=1))
        hub.configure(0,(Subscription(1,'value'),Subscription(2,'value'))); hub.acknowledge(1,1,0)
        for n in range(3):
            with hub.begin_turn(1,1,n) as turn: turn.probe('value',lambda: n)
        self.assertEqual(hub.queued(1)[0],3)
        self.assertEqual(hub.queued(2)[0],1)
        self.assertEqual(hub.health(2)['dropped'],2)
        def broken(event): raise OSError('export failed')
        self.assertEqual(hub.drain_to(1,broken,2),0)
        self.assertEqual(hub.queued(1)[0],2)
        self.assertEqual(hub.health(1)['exporter_failed'],1)
    def test_changed_sampling_baseline_and_threshold(self):
        hub=self.hub()
        hub.configure(0,(Subscription(1,'value',trigger='changed',sample_every=2),))
        hub.acknowledge(1,1,0)
        for n,value in enumerate((1,2,2,3)):
            with hub.begin_turn(1,1,n) as turn: turn.probe('value',lambda: value)
        self.assertEqual(hub.queued(1)[0],2)
        self.assertEqual(hub.health(1)['sampled'],1)
        hub.configure(1,(Subscription(1,'value',trigger='above-u128',threshold=10,hysteresis=2),))
        hub.acknowledge(1,2,4)
        for n,value in enumerate((11,12,8,11),4):
            with hub.begin_turn(1,1,n) as turn: turn.probe('value',lambda: value)
        self.assertEqual(hub.queued(1)[0],4)
    def test_scope_authority_and_reentry(self):
        hub=DiagnosticHub(); hub.add_sink(1); hub.register_producer(1,1)
        with self.assertRaisesRegex(ModelError,'unauthorized'): hub.configure(0,(Subscription(1,'private'),))
        hub=self.hub(); hub.configure(0,(Subscription(1,'value',selector=(('machine','m'),)),)); hub.acknowledge(1,1,0)
        with hub.begin_turn(1,1,0,context=(('machine','other'),)) as turn:
            turn.probe('value',lambda: self.fail('selector should avoid callback'))
        with hub.begin_turn(1,1,1,context=(('machine','m'),)) as turn:
            def value():
                with self.assertRaisesRegex(ModelError,'owned'): hub.configure(1,())
                return 1
            turn.probe('value',value)
        self.assertEqual(hub.queued(1)[0],1)

    def test_jsonl_bound_parent_and_explicit_export(self):
        import io
        from statelessness.debugger_diagnostic import DiagnosticSidecar, JsonlSink
        from test_debugger import fixture
        hub=self.hub(); hub.configure(0,(Subscription(1,'value'),)); hub.acknowledge(1,1,0)
        with hub.begin_turn(1,1,0) as turn: turn.probe('value',lambda: 1)
        sidecar=DiagnosticSidecar.for_trace(fixture()); writer=io.BytesIO()
        sidecar.write_jsonl(writer,(hub.peek(1),),max_records=1)
        reader=io.BytesIO(writer.getvalue())
        self.assertEqual(DiagnosticSidecar.read_header(reader),sidecar)
        sidecar.validate(fixture())
        self.assertIn(b'"producer_sequence":1',reader.readline())
        self.assertEqual(hub.drain_to(1,JsonlSink(io.BytesIO()),1),1)

    def test_and_selector_missing_boolean_stays_unknown(self):
        hub=self.hub(); hub.configure(0,(Subscription(1,'value',selector=(('machine','m'),('property_failed',False))),)); hub.acknowledge(1,1,0)
        with hub.begin_turn(1,1,0,context=(('machine','m'),)) as turn:
            turn.probe('value',lambda: self.fail('missing flag must not be fabricated false'))
        self.assertEqual(hub.queued(1)[0],0)
        with hub.begin_turn(1,1,1,context=(('machine','m'),('property_failed',False))) as turn: turn.probe('value',lambda: 1)
        self.assertEqual(hub.queued(1)[0],1)
