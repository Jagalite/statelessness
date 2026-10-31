"""Authored service lifecycle expectations; never generated from a host response."""
import unittest
from dataclasses import replace
from statelessness.debugger import InputPolicy
from statelessness.debugger_session import DebugSession
from statelessness.debugger_protocol import ProtocolSession,ProtocolAuthority,Request,ProtocolLimits,ProtocolError
from statelessness.debugger_protocol_services import ProbeProfile
from statelessness.debugger_watches import WatchRegistry,WatchAuthorization
from statelessness.debugger_diagnostic import DiagnosticHub,Subscription,SinkPermissions
from statelessness.debugger_inspect import DisplaySchema
from test_debugger import Counter

class U64:
    def __init__(self,value): self.value=value
    def schema(self): return DisplaySchema('counter',1)
    def inspect(self,path,cx): return cx.scalar(path,('u64',str(self.value)))

class ServicesTests(unittest.TestCase):
    def make(self,**grants):
        s=DebugSession('services',Counter(),InputPolicy('out-of-domain-injection',unrestricted=True))
        p=ProtocolSession(s,decode_input=lambda b:int(b),authority=ProtocolAuthority(**grants))
        self.serial=0
        return p
    def request(self,p,command,*args,revision=None,configuration=None):
        self.serial+=1
        return p.handle(Request(p.session.id,p.epoch,self.serial,p.session.revision if revision is None else revision,p.configuration if configuration is None else configuration,command,tuple(args)))
    def test_watch_boundaries_and_reconnect(self):
        p=self.make(configure_watches=True,deliver_inputs=True);calls=[]
        registry=WatchRegistry(authorization=WatchAuthorization.local_all())
        p.enable_watches(registry,project=lambda value:(calls.append(value),U64(value))[1])
        self.assertEqual(calls,[])
        ack=self.request(p,'watch','u64:1','u64:0');self.assertIsNone(ack.error)
        self.assertEqual(dict(ack.fields)['watch.effective_sequence'],'u64:1')
        self.assertEqual(self.request(p,'watch','u64:1','u64:0',configuration=0).error,'stale_configuration')
        self.assertIsNone(self.request(p,'step','bytes:31').error)
        current=self.request(p,'watch_current','u64:1');self.assertIsNone(current.error)
        fields=dict(current.fields)
        self.assertEqual(fields['watch.snapshot_revision'],'u64:1')
        self.assertEqual(fields['watch.kind'],'enum:Baseline(Initial)')
        self.assertEqual(fields['watch.node.value'],'u64:1')
        ordered=self.request(p,'watch_status').fields
        # Rust protocol.rs Command::WatchStatus field order.
        self.assertEqual([k for k,_ in ordered],['watches.total','watches.truncated','watches.dropped_events','watches.observation_gaps','watches.inspection_errors','watches.0.id','watches.0.generation','watches.0.enabled','watches.0.has_baseline','watches.0.effective_sequence'])
        status=dict(ordered)
        self.assertEqual(status['watches.0.effective_sequence'],'u64:1')
        p.disconnect();p.reconnect()
        self.assertEqual(dict(self.request(p,'watch_status').fields)['watches.total'],'u64:1')
        self.assertIsNone(self.request(p,'unwatch','u64:1').error)
        self.assertEqual(dict(self.request(p,'watch_status').fields)['watches.total'],'u64:0')

    def test_watch_failure_captures_actual_failing_check_ids(self):
        p=self.make(configure_watches=True,deliver_inputs=True)
        registry=WatchRegistry(authorization=WatchAuthorization.local_all())
        p.enable_watches(registry,project=U64)
        self.assertIsNone(self.request(p,'watch','u64:1','u64:0').error)
        self.assertIsNone(self.request(p,'step','bytes:36').error)
        event=registry.current(1)
        self.assertTrue(event.property_failure)
        self.assertEqual(event.failing_properties,('ceiling',))
        self.assertEqual(event.phase,'post-turn')

    def test_watch_marks_unrepresentable_and_truncated_failure_ids(self):
        from statelessness import Check
        class FailedCounter(Counter):
            def check_state(self,state):
                return () if state==0 else (Check('bad\ud800id','failed'),Check('é'*300,'failed'))
        s=DebugSession('services',FailedCounter(),InputPolicy('out-of-domain-injection',unrestricted=True))
        p=ProtocolSession(s,decode_input=lambda b:int(b),authority=ProtocolAuthority(configure_watches=True,deliver_inputs=True))
        self.serial=0
        registry=WatchRegistry(authorization=WatchAuthorization.local_all())
        p.enable_watches(registry,project=U64)
        self.assertIsNone(self.request(p,'watch','u64:1','u64:0').error)
        self.assertIsNone(self.request(p,'step','bytes:31').error)
        event=registry.current(1)
        self.assertEqual(event.failing_properties,('é'*128,))
        self.assertEqual(event.completeness,'value-limit')

    def test_watch_marks_incomplete_when_check_scan_is_bounded(self):
        from statelessness import Check
        class ManyChecks(Counter):
            def check_state(self,state):
                return () if state==0 else ((Check('ok'),)*512 + (Check('\ud800','failed'),)*512 + (Check('late','failed'),))
        s=DebugSession('services',ManyChecks(),InputPolicy('out-of-domain-injection',unrestricted=True))
        p=ProtocolSession(s,decode_input=lambda b:int(b),authority=ProtocolAuthority(configure_watches=True,deliver_inputs=True))
        self.serial=0
        registry=WatchRegistry(authorization=WatchAuthorization.local_all())
        p.enable_watches(registry,project=U64)
        self.assertIsNone(self.request(p,'watch','u64:1','u64:0').error)
        self.assertIsNone(self.request(p,'step','bytes:31').error)
        event=registry.current(1)
        self.assertEqual(event.failing_properties,())
        self.assertEqual(event.completeness,'value-limit')

    def test_watch_status_returns_complete_prefix_under_field_and_frame_limits(self):
        p=self.make(configure_watches=True)
        registry=WatchRegistry(authorization=WatchAuthorization.local_all())
        p.enable_watches(registry,project=U64)
        for _ in range(20):
            self.assertIsNone(self.request(p,'watch','u64:1','u64:0').error)
        p.limits=replace(p.limits,max_response_fields=11)
        response=self.request(p,'watch_status');self.assertIsNone(response.error)
        fields=dict(response.fields)
        self.assertEqual(fields['watches.total'],'u64:20')
        self.assertEqual(fields['watches.truncated'],'bool:true')
        self.assertEqual(fields['watches.0.id'],'u64:1')
        self.assertIn('watches.0.effective_sequence',fields)
        self.assertNotIn('watches.1.id',fields)
        for name in ('dropped_events','observation_gaps','inspection_errors'):
            self.assertIn('watches.'+name,fields)
        p.limits=replace(p.limits,max_response_fields=4096,max_frame_bytes=2048)
        response=self.request(p,'watch_status');self.assertIsNone(response.error)
        fields=dict(response.fields)
        self.assertEqual(fields['watches.truncated'],'bool:true')
        indices={int(key.split('.')[1]) for key in fields if key.startswith('watches.') and key.split('.')[1].isdigit()}
        self.assertTrue(0<len(indices)<20)
        self.assertEqual(indices,set(range(len(indices))))
        for index in indices:
            for name in ('id','generation','enabled','has_baseline','effective_sequence'):
                self.assertIn(f'watches.{index}.{name}',fields)
        self.assertEqual(len(registry.list()),20)

    def test_watch_ack_retains_individually_bounded_metadata(self):
        class LongSchema(U64):
            def schema(self): return DisplaySchema('s'*70,1)
        p=self.make(configure_watches=True)
        p.limits=replace(p.limits,max_field_bytes=64)
        p.enable_watches(WatchRegistry(authorization=WatchAuthorization.local_all()),project=LongSchema)
        response=self.request(p,'watch','u64:1','u64:0')
        self.assertIsNone(response.error)
        fields=dict(response.fields)
        self.assertEqual(fields['watch.metadata_complete'],'bool:false')
        self.assertEqual(fields['watch.display_schema_version'],'u64:1')
        self.assertNotIn('watch.display_schema',fields)
        self.assertEqual(fields['watch.resolved_type'],'enum:u64')
        self.assertEqual(dict(self.request(p,'watch_status').fields)['watches.total'],'u64:1')
    def test_install_projection_exception_does_not_format_host_error(self):
        class HostError(Exception):
            def __str__(self): raise AssertionError('exception was formatted')
        p=self.make(configure_watches=True)
        def project(_): raise HostError()
        p.enable_watches(WatchRegistry(authorization=WatchAuthorization.local_all()),project=project)
        self.assertEqual(self.request(p,'watch','u64:1','u64:0').error,'callback')
        self.assertEqual(dict(self.request(p,'watch_status').fields)['watches.total'],'u64:0')
        from statelessness import ModelError
        p=self.make(configure_watches=True)
        def forged(_): raise ModelError('unauthorized watch path')
        p.enable_watches(WatchRegistry(authorization=WatchAuthorization.local_all()),project=forged)
        self.assertEqual(self.request(p,'watch','u64:1','u64:0').error,'callback')
        self.assertEqual(dict(self.request(p,'watch_status').fields)['watches.total'],'u64:0')

    def test_watch_callback_failure_preserves_delivery(self):
        p=self.make(configure_watches=True,deliver_inputs=True)
        def project(value):
            if value: raise RuntimeError('private projection failure')
            return U64(value)
        p.enable_watches(WatchRegistry(authorization=WatchAuthorization.local_all()),project=project)
        self.assertIsNone(self.request(p,'watch','u64:1','u64:0').error)
        result=self.request(p,'step','bytes:31')
        self.assertIsNone(result.error);self.assertEqual(dict(result.fields)['delivered'],'bool:true')
    def test_probe_configuration_does_not_acknowledge_host(self):
        p=self.make(configure_diagnostics=True)
        h=DiagnosticHub();h.add_sink(7,SinkPermissions.local_all());h.register_producer(3,1)
        p.attach_probes(h,[ProbeProfile(2,'markers',Subscription(sink=7,site='work',kind='marker'))],[7])
        response=self.request(p,'probe_configure','u64:0','u64:2');self.assertIsNone(response.error)
        self.assertEqual(h.pending_producers,(3,))
        self.assertEqual(self.request(p,'probe_configure','u64:0','u64:2').error,'stale_configuration')
        self.assertEqual(self.request(p,'probe_configure','u64:1','u64:99').error,'unauthorized')
        h.acknowledge(3,1,1)
        scope=h.begin_turn(3,1,1)
        self.assertEqual(self.request(p,'probe_events','u64:7','u64:1','bool:false').error,'busy')
        scope.marker('work');scope.finish()
        self.assertEqual(h.queued(7)[0],1)
        response=self.request(p,'probe_events','u64:7','u64:1','bool:false');self.assertIsNone(response.error)
        fields=dict(response.fields);self.assertEqual(fields['probes.events.returned'],'u64:1')
        keys=[key for key,_ in response.fields]
        self.assertEqual(keys[keys.index('probes.events.0.baseline'):keys.index('probes.events.0.metadata_omitted')],
            ['probes.events.0.baseline','probes.events.0.baseline_reason',
             'probes.events.0.change_unknown','probes.events.0.incomplete_turn'])
        self.assertEqual(fields['probes.events.0.baseline'],'bool:true');self.assertEqual(fields['probes.events.0.baseline_reason'],'enum:Initial')
        self.assertEqual(fields['probes.events.0.payload'],'omitted:by_request');self.assertNotIn('probes.events.0.site',fields)
        self.assertEqual(h.queued(7)[0],0)
        self.assertEqual(self.request(p,'probe_events','u64:8','u64:1','bool:true').error,'unauthorized')

    def test_python_integer_probe_payload_is_lossless_and_not_a_character(self):
        p=self.make(configure_diagnostics=True)
        h=DiagnosticHub();h.add_sink(7,SinkPermissions.local_all());h.register_producer(3,1)
        p.attach_probes(h,[ProbeProfile(2,'values',Subscription(sink=7,site='work'))],[7])
        self.assertIsNone(self.request(p,'probe_configure','u64:0','u64:2').error)
        h.acknowledge(3,1,1)
        values=[0,-1,(1<<200)+123]
        for index,value in enumerate(values,1):
            with h.begin_turn(3,1,index) as scope: scope.probe('work',lambda value=value:value)
        response=self.request(p,'probe_events','u64:7','u64:3','bool:true')
        self.assertIsNone(response.error)
        fields=dict(response.fields)
        for index,value in enumerate(values):
            self.assertEqual(fields[f'probes.events.{index}.payload.kind'],'enum:integer')
            self.assertEqual(fields[f'probes.events.{index}.payload.value'],f'python-int:{value}')
        self.assertEqual(h.queued(7)[0],0)

    def test_watch_reports_native_python_integer_type(self):
        p=self.make(configure_watches=True,deliver_inputs=True)
        p.enable_watches(WatchRegistry(authorization=WatchAuthorization.local_all()))
        self.assertIsNone(self.request(p,'watch','u64:1','u64:0').error)
        self.assertIsNone(self.request(p,'step','bytes:31').error)
        response=self.request(p,'watch_current','u64:1')
        self.assertIsNone(response.error)
        fields=dict(response.fields)
        self.assertEqual(fields['watch.node.kind'],'enum:integer')
        self.assertEqual(fields['watch.node.value'],'python-int:1')

    def test_node_wire_preserves_reported_page_count(self):
        from statelessness.debugger_inspect import ChildNode, InspectNode, PageInfo, PathSegment
        from statelessness.debugger_protocol_services import node_fields
        child=ChildNode(PathSegment('field','x'),'x',InspectNode('scalar',('u64','1')))
        node=InspectNode('object','demo',children=(child,),child_count=1,page=PageInfo(0,0,1,1))
        self.assertFalse(node.is_complete)
        fields=dict(node_fields('node',node))
        self.assertEqual(fields['node.page.returned'],'u64:0')
        self.assertEqual(fields['node.children.returned'],'u64:1')
    def test_probe_configuration_truncates_producer_acknowledgement(self):
        p=self.make(configure_diagnostics=True)
        p.limits=replace(p.limits,max_response_fields=32)
        h=DiagnosticHub();h.add_sink(7,SinkPermissions.local_all())
        for producer in range(40): h.register_producer(producer,1)
        p.attach_probes(h,[ProbeProfile(2,'markers',Subscription(sink=7,site='work',kind='marker'))],[7])
        response=self.request(p,'probe_configure','u64:0','u64:2')
        self.assertIsNone(response.error)
        fields=dict(response.fields)
        self.assertEqual(fields['probes.pending_producers.total'],'u64:40')
        self.assertEqual(fields['probes.pending_producers.truncated'],'bool:true')
        self.assertEqual(len(h.pending_producers),40)

    def test_probe_event_preflight_failure_does_not_drain_queue(self):
        p=self.make(configure_diagnostics=True)
        h=DiagnosticHub();h.add_sink(7,SinkPermissions.local_all());h.register_producer(3,1)
        p.attach_probes(h,[ProbeProfile(2,'values',Subscription(sink=7,site='work'))],[7])
        self.assertIsNone(self.request(p,'probe_configure','u64:0','u64:2').error)
        h.acknowledge(3,1,1)
        for seq in (1,2):
            with h.begin_turn(3,1,seq) as turn: turn.probe('work',lambda:U64(7))
        original=p.limits
        p.limits=replace(original,max_response_fields=32)
        self.assertEqual(self.request(p,'probe_events','u64:7','u64:2','bool:true').error,'limit')
        self.assertEqual(h.queued(7)[0],2)
        p.limits=original
        response=self.request(p,'probe_events','u64:7','u64:2','bool:true')
        self.assertIsNone(response.error)
        self.assertEqual(dict(response.fields)['probes.events.0.payload.value'],'u64:7')
        self.assertEqual(h.queued(7)[0],0)

    def test_profile_bound_before_attachment(self):
        p=self.make();p.limits=replace(p.limits,max_probe_profile_bytes=8)
        h=DiagnosticHub();h.add_sink(1,SinkPermissions.local_all())
        with self.assertRaises(ProtocolError): p.attach_probes(h,[ProbeProfile(1,'a',Subscription(sink=1,site='s',kind='marker'))],[1])
        self.assertIsNone(p._probes)

    def test_profile_identity_rejects_boolean_float_and_busy_hub(self):
        h=DiagnosticHub();h.add_sink(1,SinkPermissions.local_all());h.register_producer(1,1)
        for identity in (True,1.5,-1,1<<64):
            p=self.make()
            with self.assertRaises(ProtocolError): p.attach_probes(h,[ProbeProfile(identity,'invalid',Subscription(sink=1,site='s',kind='marker'))],[1])
            self.assertIsNone(p._probes)
        for sink in (True,1.0):
            p=self.make()
            with self.assertRaises(ProtocolError): p.attach_probes(h,[],[sink])
            self.assertIsNone(p._probes)
        scope=h.begin_turn(1,1,1)
        try:
            p=self.make()
            with self.assertRaises(ProtocolError) as error: p.attach_probes(h,[],[1])
            self.assertEqual(error.exception.code,'busy');self.assertIsNone(p._probes)
        finally: scope.finish()

    def test_watch_schema_callback_failure_is_inspector_error(self):
        class BrokenSchema(U64):
            def schema(self):
                if self.value:
                    raise RuntimeError('private host detail')
                return super().schema()
        p=self.make(configure_watches=True,deliver_inputs=True)
        p.enable_watches(WatchRegistry(authorization=WatchAuthorization.local_all()),project=BrokenSchema)
        self.assertIsNone(self.request(p,'watch','u64:1','u64:0').error)
        self.assertIsNone(self.request(p,'step','bytes:31').error)
        current=self.request(p,'watch_current','u64:1')
        self.assertIsNone(current.error)
        self.assertEqual(dict(current.fields)['watch.kind'],'enum:InspectorError(InvalidMetadata)')
        self.assertEqual(dict(self.request(p,'watch_status').fields)['watches.0.enabled'],'bool:true')

    def test_watch_current_retains_normalized_inspection_failure(self):
        from statelessness.debugger_inspect import InspectError
        class Disappearing(U64):
            def inspect(self,path,cx):
                if self.value: raise InspectError('path not found')
                return super().inspect(path,cx)
        p=self.make(configure_watches=True,deliver_inputs=True)
        p.enable_watches(WatchRegistry(authorization=WatchAuthorization.local_all()),project=Disappearing)
        self.assertIsNone(self.request(p,'watch','u64:1','u64:0').error)
        self.assertIsNone(self.request(p,'step','bytes:31').error)
        current=self.request(p,'watch_current','u64:1');self.assertIsNone(current.error)
        self.assertEqual(dict(current.fields)['watch.kind'],'enum:InspectorError(PathNotFound)')
        self.assertEqual(dict(current.fields)['watch.snapshot_revision'],'u64:1')
