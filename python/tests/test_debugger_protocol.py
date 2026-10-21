"""Wire cases independently hand authored, including real OS pipes."""
import io
import os
import threading
import unittest
from dataclasses import replace
from statelessness.debugger_protocol import *
from statelessness.debugger import InputPolicy
from statelessness.debugger_session import DebugSession
from test_debugger import Counter

class ProtocolTests(unittest.TestCase):
    def test_adapter_commit_reserves_revision_digit_growth(self):
        committed=[]
        fields=(('watch.id','string:'+'x'*1900),)
        p=self.adapter(authority=ProtocolAuthority(configure_watches=True),adapters={'watch':lambda args:ProtocolPrepared(fields,lambda:committed.append(True))})
        request=self.request(p,command='watch',arguments=('u64:1','u64:0'))
        current=len(encode_response(p._response(request.request_id,fields=fields)))
        # Fill a valid 2048-byte frame exactly at the current small revisions.
        fields=(('watch.id','string:'+'x'*(1900+2048-current)),)
        p.limits=replace(p.limits,max_frame_bytes=2048)
        self.assertEqual(p.handle(request).error,'limit')
        self.assertEqual(committed,[])

    def test_output_encoding_is_optional_authorized_bounded_and_leased(self):
        from statelessness import Transition, ModelError
        class Outputs(Counter):
            def step(self,state,input): return Transition(state+input,outputs=(b'a',b'bc'))
        leases=[]
        def encode(value,sink): leases.append(sink); sink.append(value)
        def make(authority,**kwargs):
            session=DebugSession('counter',Outputs(),InputPolicy('all',lambda *args:True))
            session.step(0,1)
            p=ProtocolSession(session,authority=authority,**kwargs)
            handle=dict(p.handle(self.request(p,command='snapshot')).fields)['handle']
            return p,handle
        p,h=make(ProtocolAuthority(read_exact_values=True),encode_output_into=encode,limits=ProtocolLimits(max_page_size=1))
        response=p.handle(self.request(p,2,command='outputs',arguments=(h,)))
        self.assertIsNone(response.error)
        self.assertEqual(dict(response.fields)['outputs.0.encoded'],'bytes:61')
        self.assertEqual(dict(response.fields)['outputs.truncated'],'bool:true')
        with self.assertRaises(ModelError): leases[0].append(b'late')
        p,h=make(ProtocolAuthority(),encode_output_into=lambda *args:self.fail('unauthorized encoder called'))
        response=p.handle(self.request(p,2,command='outputs',arguments=(h,)))
        self.assertEqual(dict(response.fields)['outputs.payloads'],'enum:unavailable')
        p,h=make(ProtocolAuthority(read_exact_values=True))
        self.assertEqual(dict(p.handle(self.request(p,2,command='outputs',arguments=(h,))).fields)['outputs.payloads'],'enum:unavailable')
        p,h=make(ProtocolAuthority(read_exact_values=True),encode_output_into=encode,limits=ProtocolLimits(max_input_bytes=1))
        self.assertEqual(p.handle(self.request(p,2,command='outputs',arguments=(h,))).error,'limit')

    def test_output_encoder_cannot_mutate_typed_session(self):
        from statelessness import Transition
        class Outputs(Counter):
            def step(self,state,input): return Transition(state+input,outputs=(1,))
        session=DebugSession('counter',Outputs(),InputPolicy('all',lambda *args:True))
        session.step(0,1)
        def encode(value,sink): session.step(session.revision,1)
        p=ProtocolSession(session,authority=ProtocolAuthority(read_exact_values=True),encode_output_into=encode)
        handle=dict(p.handle(self.request(p,command='snapshot')).fields)['handle']
        self.assertEqual(p.handle(self.request(p,2,command='outputs',arguments=(handle,))).error,'callback')
        self.assertEqual(session.revision,1)
        session.step(1,1)
        self.assertEqual(session.revision,2)

    def test_input_decoder_cannot_mutate_typed_session(self):
        session=DebugSession('counter',Counter(),InputPolicy('all',lambda *args:True))
        def decode(value):
            session.step(session.revision,1)
            return 1
        p=ProtocolSession(session,authority=ProtocolAuthority(deliver_inputs=True),decode_input=decode)
        response=p.handle(self.request(p,command='step',arguments=('bytes:01',)))
        self.assertEqual(response.error,'invalid_input')
        self.assertEqual((session.revision,session.state),(0,0))
        session.step(0,1)
        self.assertEqual(session.revision,1)

    def adapter(self,**kwargs):
        return ProtocolSession(DebugSession('counter',Counter(),InputPolicy('positive',lambda m,s,i:i>0)),decode_input=lambda b:int.from_bytes(b,'big'),**kwargs)
    def request(self,p,id=1,**kwargs):
        return Request('counter',p.epoch,id,p.session.revision,0,kwargs.pop('command','status'),**kwargs)
    def test_manual_wire_fullwidth_and_controls(self):
        r=decode_request(b'DDBG\tu32:1\trequest\tstring:%3C%09%F0%9F%98%80\tu64:18446744073709551615\tu64:9\tu64:9007199254740993\tu64:0\tstatus')
        self.assertEqual(r.session,'<\t😀'); self.assertEqual(r.epoch,(1<<64)-1); self.assertEqual(r.expected_revision,9007199254740993)
        self.assertEqual(decode_request(encode_request(r)),r)
        for raw in (b'%FF',b'%0',b'<',b'%GG'):
            with self.assertRaises(ProtocolError): decode_request(b'DDBG\tu32:1\trequest\tstring:'+raw+b'\tu64:1\tu64:1\tu64:0\tu64:0\tstatus')
        response=Response('x',1,2,3,4,None,(('quantity','u128:340282366920938463463374607431768211455'),))
        self.assertEqual(decode_response(encode_response(response)),response)
    def test_framing_preallocation_and_partial(self):
        class Reader:
            def __init__(self): self.calls=[]
            def read(self,n): self.calls.append(n); return b'\xff' if n==1 else b'\xff\xff\xff'
        r=Reader()
        with self.assertRaises(ProtocolError): read_frame(r,64)
        self.assertEqual(r.calls,[1,3])
        for raw in (b'\x00',b'\x00\x00\x00\x02a'):
            with self.assertRaises(EOFError): read_frame(io.BytesIO(raw))
        self.assertIsNone(read_frame(io.BytesIO()))
        with self.assertRaises(ProtocolError): read_frame(io.BytesIO(bytes(4)))
    def test_admission_dedup_retirement_reconnect(self):
        p=self.adapter(authority=ProtocolAuthority(deliver_inputs=True),limits=ProtocolLimits(max_cache_entries=1))
        request=self.request(p,command='step',arguments=('bytes:01',))
        first=p.handle(request); self.assertIsNone(first.error); self.assertEqual(p.session.state,1)
        self.assertEqual(p.handle(request),first); self.assertEqual(p.session.state,1)
        self.assertEqual(p.handle(replace(request,arguments=('bytes:02',))).error,'duplicate_conflict')
        self.assertIsNone(p.handle(self.request(p,3)).error)
        self.assertEqual(p.handle(request).error,'retired_request')
        self.assertEqual(p.handle(self.request(p,2)).error,'retired_request')
        old=p.epoch; p.reconnect(); self.assertNotEqual(old,p.epoch); self.assertEqual(p.session.state,1)
        self.assertEqual(p.handle(request).error,'wrong_epoch')
        discovered=p.handle(replace(self.request(p),epoch=0))
        self.assertIsNone(discovered.error)
    def test_authority_stale_and_failed_requests_retire(self):
        p=self.adapter(); request=self.request(p,command='step',arguments=('bytes:01',))
        self.assertEqual(p.handle(request).error,'unauthorized'); self.assertEqual(p.session.state,0)
        self.assertEqual(p.handle(replace(request,command='status',arguments=())).error,'duplicate_conflict')
        bad=replace(self.request(p,2),session='other'); self.assertEqual(p.handle(bad).error,'wrong_session')
        self.assertIsNone(p.handle(self.request(p,2)).error)
        self.assertIsNone(p.handle(replace(self.request(p,3),expected_configuration=1)).error)
    def test_stale_handle_and_write_after_commit(self):
        p=self.adapter(authority=ProtocolAuthority(deliver_inputs=True)); response=p.handle(self.request(p,command='snapshot'))
        h=dict(response.fields)['handle']; step=self.request(p,2,command='step',arguments=('bytes:01',))
        class Broken(io.BytesIO):
            def write(self,b): raise OSError('closed output')
        wire=io.BytesIO(); write_frame(wire,encode_request(step)); wire.seek(0)
        with self.assertRaises(OSError): serve(p,wire,Broken())
        self.assertEqual(p.session.state,1); self.assertFalse(p.connected)
        p.reconnect()
        self.assertEqual(p.handle(self.request(p,command='checks',arguments=(h,))).error,'stale_handle')
    def test_os_pipe_serve(self):
        p=self.adapter(authority=ProtocolAuthority(deliver_inputs=True))
        ir,iw=os.pipe(); outr,outw=os.pipe(); errors=[]
        def worker():
            try:
                with os.fdopen(ir,'rb',buffering=0) as reader,os.fdopen(outw,'wb',buffering=0) as writer: serve(p,reader,writer)
            except BaseException as e: errors.append(e)
        t=threading.Thread(target=worker); t.start()
        with os.fdopen(iw,'wb',buffering=0) as writer,os.fdopen(outr,'rb',buffering=0) as reader:
            request=self.request(p,command='step',arguments=('bytes:01',))
            write_frame(writer,encode_request(request)); first=decode_response(read_frame(reader)); self.assertIsNone(first.error)
            write_frame(writer,encode_request(request)); self.assertEqual(decode_response(read_frame(reader)),first)
        t.join(5); self.assertFalse(t.is_alive()); self.assertEqual(errors,[]); self.assertEqual(p.session.state,1)

    def test_host_adapters_typed_authority_configuration_and_bounds(self):
        called=[]
        def watch(args):
            called.append(args)
            return ProtocolPrepared((('watch.id','u64:18446744073709551615'),))
        p=self.adapter(adapters={'watch':watch},configuration_revision=lambda:7)
        request=replace(self.request(p,command='watch',arguments=('u64:1','u64:0','key.u128:340282366920938463463374607431768211455')),expected_configuration=7)
        self.assertEqual(p.handle(request).error,'unauthorized'); self.assertEqual(called,[])
        p=self.adapter(authority=ProtocolAuthority(configure_watches=True),adapters={'watch':watch},configuration_revision=lambda:7)
        request=replace(request,epoch=p.epoch)
        self.assertEqual(p.handle(replace(request,expected_configuration=6)).error,'stale_configuration'); self.assertEqual(called,[])
        self.assertIsNone(p.handle(replace(request,request_id=2)).error)
        self.assertEqual(called,[(1,0,(('key.u128',(1<<128)-1),))])
        p=self.adapter(adapters={'metric_catalog':lambda args:called.append(args) or ()})
        self.assertEqual(p.handle(self.request(p,command='metric_catalog',arguments=('u64:0','u64:101'))).error,'limit')
        self.assertEqual(len(called),1)

    def test_preflight_prevents_host_commit(self):
        called=[]
        p=self.adapter(authority=ProtocolAuthority(configure_watches=True),adapters={'watch':lambda args:ProtocolPrepared((('watch.id','string:'+'x'*9000),),lambda:called.append(True))})
        result=p.handle(self.request(p,command='watch',arguments=('u64:1','u64:0')))
        self.assertEqual(result.error,'limit'); self.assertEqual(called,[])

    def test_concrete_candidates_and_check_phase(self):
        from statelessness.debugger_workbench import EnumeratedSession
        class Enumerated(Counter):
            def inputs(self,state): return (1,2)
        s=EnumeratedSession('counter',Enumerated(),InputPolicy.enumerated())
        p=ProtocolSession(s,encode_input=lambda i:str(i).encode(),decode_input=lambda b:int(b),authority=ProtocolAuthority(deliver_inputs=True))
        result=p.handle(self.request(p,command='inputs',arguments=('u64:0','u64:2')))
        self.assertIsNone(result.error)
        fields=dict(result.fields); self.assertNotIn('candidates.0.encoded',fields)
        token=fields['candidates.1.token']
        self.assertIsNone(p.handle(self.request(p,2,command='select',arguments=(token,))).error)
        self.assertEqual(s.state,2)
        self.assertEqual(p.handle(self.request(p,3,command='select',arguments=(fields['candidates.0.token'],))).error,'stale_handle')
        response=p.handle(self.request(p,4,command='snapshot')); handle=dict(response.fields)['handle']
        self.assertEqual(p.handle(self.request(p,5,command='checks',arguments=(handle,'u64:9'))).error,'malformed')
        response=p.handle(self.request(p,6,command='checks',arguments=(handle,)))
        self.assertEqual(dict(response.fields)['checks.first_failure'],'none')

    def test_concrete_metrics_binding(self):
        from statelessness.debugger_metrics import MetricsStore,protocol_adapters
        store=MetricsStore(1)
        p=self.adapter(adapters=protocol_adapters(store))
        response=p.handle(self.request(p,command='metric_snapshot',arguments=('none','u64:0','u64:1')))
        self.assertIsNone(response.error)
        self.assertEqual(dict(response.fields)['metrics.revision'],'u64:1')

    def test_exact_trace_streamed_chunks_and_independent_authority(self):
        from statelessness.monitor import RecorderOptions
        from statelessness.trace_binary import read_audit
        s=DebugSession('counter',Counter(),InputPolicy('positive',lambda m,s,i:i>0),recorder_options=RecorderOptions())
        p=ProtocolSession(s,authority=ProtocolAuthority(export_exact_trace=True))
        snapshot=p.handle(self.request(p,command='snapshot')); handle=dict(snapshot.fields)['handle']
        chunks=bytearray(); offset=0; request_id=2
        while True:
            response=p.handle(self.request(p,request_id,command='export_trace',arguments=(handle,f'u64:{offset}','u64:31')))
            self.assertIsNone(response.error)
            fields=dict(response.fields); chunks.extend(bytes.fromhex(fields['export.bytes'][6:]))
            if fields['export.complete']=='bool:true': break
            offset=int(fields['export.next_offset'][4:]); request_id+=1
        trace=read_audit(io.BytesIO(chunks)); self.assertEqual(trace.initial_state,b'0')
        p=ProtocolSession(s,authority=ProtocolAuthority(read_exact_values=True))
        handle=dict(p.handle(self.request(p,command='snapshot')).fields)['handle']
        self.assertEqual(p.handle(self.request(p,2,command='export_trace',arguments=(handle,'u64:0','u64:31'))).error,'unauthorized')

    def test_concrete_inspection_projection(self):
        from statelessness.debugger_inspect import ObjectView,FieldView
        s=DebugSession('counter',Counter(),InputPolicy('positive',lambda m,s,i:i>0))
        p=ProtocolSession(s,adapters=inspection_adapters(s,project=lambda value:ObjectView('Counter',(FieldView('value','Value',value),FieldView.redacted('secret','Secret')))))
        handle=dict(p.handle(self.request(p,command='snapshot')).fields)['handle']
        response=p.handle(self.request(p,2,command='inspect',arguments=(handle,'u64:1','u64:0','u64:10')))
        self.assertIsNone(response.error)
        self.assertIn(('inspect.root.child.1.kind','enum:redacted'),response.fields)
        self.assertNotIn('repr',str(response.fields))
