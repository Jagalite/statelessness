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
        status=dict(self.request(p,'watch_status').fields)
        self.assertEqual(status['watches.0.effective_sequence'],'u64:1')
        p.disconnect();p.reconnect()
        self.assertEqual(dict(self.request(p,'watch_status').fields)['watches.total'],'u64:1')
        self.assertIsNone(self.request(p,'unwatch','u64:1').error)
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
        self.assertEqual(fields['probes.events.0.baseline'],'bool:true');self.assertEqual(fields['probes.events.0.baseline_reason'],'enum:Initial')
        self.assertEqual(fields['probes.events.0.payload'],'omitted:by_request');self.assertNotIn('probes.events.0.site',fields)
        self.assertEqual(h.queued(7)[0],0)
        self.assertEqual(self.request(p,'probe_events','u64:8','u64:1','bool:true').error,'unauthorized')
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
