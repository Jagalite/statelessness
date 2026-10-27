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
        self.assertIn(b'"producer_sequence":"1"',reader.readline())
        self.assertEqual(hub.drain_to(1,JsonlSink(io.BytesIO()),1),1)

    def test_and_selector_missing_boolean_stays_unknown(self):
        hub=self.hub(); hub.configure(0,(Subscription(1,'value',selector=(('machine','m'),('property_failed',False))),)); hub.acknowledge(1,1,0)
        with hub.begin_turn(1,1,0,context=(('machine','m'),)) as turn:
            turn.probe('value',lambda: self.fail('missing flag must not be fabricated false'))
        self.assertEqual(hub.queued(1)[0],0)
        with hub.begin_turn(1,1,1,context=(('machine','m'),('property_failed',False))) as turn: turn.probe('value',lambda: 1)
        self.assertEqual(hub.queued(1)[0],1)

    def test_transient_labels_preserve_changed_baseline_and_threshold_cooldown(self):
        for key,values in (('input_variant',('a','b')),('output_variant',('a','b')),
                ('property',('a','b')),('effect_kind',('a','b')),
                ('source_site',('a','b')),('property_failed',(False,True))):
            for trigger in ('changed','above-u128'):
                with self.subTest(label=key,trigger=trigger):
                    hub=self.hub()
                    hub.configure(0,(Subscription(1,'value',trigger=trigger,threshold=10,hysteresis=2,cooldown_turns=100),))
                    hub.acknowledge(1,1,0)
                    for n,value in enumerate(values):
                        with hub.begin_turn(1,1,n,context=((key,value),)) as turn: turn.probe('value',lambda: 11)
                    self.assertEqual(hub.queued(1)[0],1)
                    self.assertEqual(hub.pop(1).baseline_reason,'initial')
        hub=self.hub()
        hub.configure(0,(Subscription(1,'value',trigger='above-u128',threshold=10,hysteresis=2,cooldown_turns=4),))
        hub.acknowledge(1,1,0)
        for n,value in enumerate((11,8,11,8,11)):
            with hub.begin_turn(1,1,n,context=(('input_variant',str(n)),)) as turn: turn.probe('value',lambda: value)
        self.assertEqual([hub.pop(1).transition for _ in range(hub.queued(1)[0])],[0,4])

    def test_identity_changes_and_observation_gaps_still_reset_baselines(self):
        for key in ('model','machine','entity','correlation','run','origin','gap'):
            with self.subTest(identity=key):
                hub=self.hub(); hub.configure(0,(Subscription(1,'value',trigger='changed'),)); hub.acknowledge(1,1,0)
                for n in range(2):
                    context=((key,str(n)),) if key in ('model','machine','entity','correlation') else ()
                    with hub.begin_turn(1,1+n if key=='run' else 1,n*2 if key=='gap' else n,
                            'live' if key=='origin' and n else 'simulation',context=context) as turn:
                        turn.probe('value',lambda: 11)
                hub.pop(1); event=hub.pop(1)
                self.assertTrue(event.baseline and event.change_unknown)
                self.assertEqual(event.baseline_reason,'observation-gap' if key=='gap' else 'source-changed')

    def test_selector_gaps_reset_without_evaluating_filtered_probes(self):
        for key in ('input_variant','machine'):
            for trigger in ('changed','above-u128'):
                with self.subTest(label=key,trigger=trigger):
                    hub=self.hub()
                    hub.configure(0,(Subscription(1,'value',trigger=trigger,threshold=10,cooldown_turns=100,
                        selector=((key,'chosen'),)),)); hub.acknowledge(1,1,0)
                    for n,label in enumerate(('chosen','other','chosen')):
                        with hub.begin_turn(1,1,n,context=((key,label),)) as turn:
                            turn.probe('value',lambda: self.fail('filtered callback') if label=='other' else 11)
                    self.assertEqual(hub.queued(1)[0],2)
                    hub.pop(1); event=hub.pop(1)
                    self.assertTrue(event.baseline and event.change_unknown)
                    self.assertEqual(event.baseline_reason,'selector-gap')

    def test_json_exports_bytes_nested_values_and_lossless_integers(self):
        import io
        import json
        from statelessness.debugger_diagnostic import event_json, DiagnosticSidecar, JsonlSink
        from statelessness.debugger_inspect import ObjectView, FieldView, IntegerView, CharView, EnumView
        from test_debugger import fixture
        values=(b'\x00\xff',b'',b'x'*5000,
                ObjectView('Values',(FieldView('bytes','Bytes',[b'\x00\xff',b'']),
                    FieldView('integer','Integer',IntegerView('u128',str((1<<128)-1))),
                    FieldView('bool','Bool',True),FieldView('unit','Unit',None),
                    FieldView('char','Char',CharView('λ')),FieldView('text','Text','hi\n'),
                    FieldView('enum','Enum',EnumView('Choice','some','Some')),
                    FieldView.redacted('hidden','Hidden'))))
        for value in values:
            with self.subTest(value_type=type(value).__name__):
                hub=self.hub(); hub.configure(0,(Subscription(1,'value'),)); hub.acknowledge(1,1,0)
                with hub.begin_turn(1,(1<<64)-1,0) as turn: turn.probe('value',lambda:value)
                event=hub.peek(1); expected=json.loads(event_json(event))
                self.assertEqual(expected['run_id'],str((1<<64)-1))
                self.assertIs(expected['baseline'],event.baseline)
                def check(node):
                    self.assertIsInstance(node['child_count'],(str,type(None)))
                    if node['kind']=='bytes':
                        self.assertEqual(set(node['value']),{'hex','total_bytes'})
                        self.assertEqual(node['value']['total_bytes'],node['total_bytes'])
                        raw=bytes.fromhex(node['value']['hex'])
                        self.assertLessEqual(len(raw),int(node['total_bytes']))
                    for child in node['children']: check(child['node'])
                check(expected['payload'])
                if type(value) is bytes:
                    node=expected['payload']; self.assertEqual(bytes.fromhex(node['value']['hex']),value[:4096])
                    self.assertEqual(node['value']['total_bytes'],str(len(value)))
                    if len(value)>4096: self.assertEqual(node['completeness'],'value-limit')
                else:
                    children=expected['payload']['children']
                    self.assertEqual(children[1]['node']['value'],['u128',str((1<<128)-1)])
                    self.assertEqual(children[2]['node']['value'],['bool',True])
                    self.assertEqual(children[-1]['node']['kind'],'redacted')
                sink=io.BytesIO()
                self.assertEqual(hub.drain_to(1,JsonlSink(sink),1),1)
                self.assertEqual(json.loads(sink.getvalue()),expected)
                self.assertEqual(hub.health(1)['exporter_failed'],0)
                sidecar=io.BytesIO(); DiagnosticSidecar.for_trace(fixture()).write_jsonl(sidecar,(event,))
                self.assertEqual(json.loads(sidecar.getvalue().splitlines()[1]),expected)

    def test_json_float_bits_preserve_nan_and_signed_zero(self):
        import io
        import json
        from statelessness.debugger_diagnostic import event_json, JsonlSink
        from statelessness.debugger_inspect import FloatView
        for kind,decimal,bits in (('f32','-0',1<<31),('f32','NaN',0x7fc00001),
                                  ('f64','-0',1<<63),('f64','NaN',0x7ff8000000000001)):
            hub=self.hub(); hub.configure(0,(Subscription(1,'value'),)); hub.acknowledge(1,1,0)
            with hub.begin_turn(1,1,0) as turn: turn.probe('value',lambda:[FloatView(kind,decimal,bits)])
            node=json.loads(event_json(hub.peek(1)))['payload']['children'][0]['node']
            self.assertEqual(node['kind'],'float')
            self.assertEqual(node['value'],{'type':kind,'decimal':decimal,'bits':str(bits)})

            sink=io.BytesIO()
            self.assertEqual(hub.drain_to(1,JsonlSink(sink),1),1)
            self.assertEqual(json.loads(sink.getvalue())['payload']['children'][0]['node'],node)

    def test_json_structural_integer_metadata_stays_exact(self):
        import json
        from dataclasses import replace
        from statelessness.debugger_diagnostic import event_json
        from statelessness.debugger_inspect import PathSegment, DisplaySchema
        hub=self.hub(); hub.configure(0,(Subscription(1,'value'),)); hub.acknowledge(1,1,0)
        with hub.begin_turn(1,1,0) as turn: turn.probe('value',lambda:[1])
        event=replace(hub.peek(1),path=(PathSegment('index',(1<<64)-1),),
                      display_schema=DisplaySchema('Example',1,('file',3,4)))
        data=json.loads(event_json(event))
        self.assertEqual(data['path'],[{'kind':'index','value':str((1<<64)-1)}])
        self.assertEqual(data['display_schema'],{'name':'Example','version':'1','source':['file','3','4']})
        self.assertEqual(data['payload']['page'],{'offset':'0','returned':'1','total':'1','next_offset':None})
        self.assertEqual(data['payload']['children'][0]['segment'],{'kind':'index','value':'0'})
