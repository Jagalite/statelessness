"""Wire cases independently hand authored, including real OS pipes."""
import io
import os
import threading
import unittest
from unittest.mock import patch
from dataclasses import replace
from statelessness.debugger_protocol import *
from statelessness.debugger import InputPolicy
from statelessness.debugger_session import DebugSession
from test_debugger import Counter

class ProtocolTests(unittest.TestCase):
    def test_frame_admission_precedes_escaping_and_argument_expansion(self):
        from statelessness import debugger_protocol as protocol
        limits=ProtocolLimits(max_frame_bytes=2048,max_field_bytes=8192)
        # Each field is within its limit, but the escaped frame is not.
        with patch.object(protocol,'escape',side_effect=AssertionError('escaped before admission')):
            with self.assertRaisesRegex(ProtocolError,'limit'):
                protocol._encode(('DDBG','é'*400),limits)
            with self.assertRaisesRegex(ProtocolError,'limit'):
                encode_request(Request('s',1,1,0,0,'status',('x',)*56),limits)
        exact=protocol._encode(('DDBG','é'*340),limits)
        self.assertEqual(len(exact),2045)
        self.assertEqual(protocol._decode_fields(exact,limits,2),['DDBG','é'*340])

    def test_decoder_checks_count_without_splitting_entire_payload(self):
        from statelessness import debugger_protocol as protocol
        class UnsplitBytes(bytes):
            def split(self,*args,**kwargs): raise AssertionError('whole payload split')
        limits=ProtocolLimits()
        with self.assertRaisesRegex(ProtocolError,'limit'):
            protocol._decode_fields(UnsplitBytes(b'a\t'*500),limits,2)
        # Earlier malformed fields still win over a later field-count limit.
        with self.assertRaisesRegex(ProtocolError,'malformed'):
            protocol._decode_fields(b'%XX\ta\tb',limits,2)

    def test_encoder_caps_caller_iterators_before_expansion(self):
        class MoreThanLength(tuple):
            def __iter__(self):
                yield from super().__iter__()
                yield 'extra'
        request=Request('s',1,1,0,0,'status',MoreThanLength(('x',)))
        with self.assertRaisesRegex(ProtocolError,'limit'): encode_request(request)
        response=Response('s',1,1,0,0,None,MoreThanLength((('k','v'),)))
        with self.assertRaisesRegex(ProtocolError,'limit'): encode_response(response)
        response=Response('s',1,1,0,0,None,(MoreThanLength(('k','v')),))
        with self.assertRaisesRegex(ProtocolError,'limit'): encode_response(response)
        class ChangingLength:
            def __init__(self): self.calls=0
            def __len__(self): self.calls+=1; return 0 if self.calls==1 else 1000000
            def __iter__(self): return iter(())
        arguments=ChangingLength()
        self.assertEqual(decode_request(encode_request(Request('s',1,1,0,0,'status',arguments))).arguments,())
        self.assertEqual(arguments.calls,1)
        class MisleadingString(str):
            def __len__(self): return 0
            def encode(self,*args,**kwargs): return b'x'*100000
        response=Response('s',1,1,0,0,None,((MisleadingString('k'),MisleadingString('v')),))
        self.assertEqual(decode_response(encode_response(response)).fields,(('k','v'),))

    def test_rust_inspection_wire_differential(self):
        from pathlib import Path
        from statelessness.debugger_inspect import ObjectView, FieldView, IntegerView
        class Slice:
            def inspect(self, path, cx): return cx.sequence(path, (), name='slice')
        def projection(_):
            return ObjectView('request-lifecycle.State', (
                FieldView('generation', 'generation', IntegerView('u8', '0')),
                FieldView('pending', 'pending', Slice()),
                FieldView('unused1', 'unused1', False), FieldView('unused2', 'unused2', False)))
        session=DebugSession('counter', Counter(), InputPolicy('positive', lambda m,s,i:i>0))
        p=ProtocolSession(session, adapters=inspection_adapters(session, project=projection))
        handle=dict(p.handle(self.request(p, command='snapshot')).fields)['handle']
        cases=[('root','u64:1','u64:0',()), ('scalar','u64:1','u64:0',('field:generation',)),
               ('page','u64:1','u64:0',('field:pending',)), ('object_past_end','u64:1','u64:9',()),
               ('sequence_past_end','u64:1','u64:9',('field:pending',)),
               ('missing','u64:1','u64:0',('field:missing',)), ('schema','u64:2','u64:0',())]
        actual=[]
        for index,(name,schema,offset,path) in enumerate(cases,2):
            response=p.handle(self.request(p,index,command='inspect',arguments=(handle,schema,offset,'u64:1',*path)))
            actual.append(name+'\t'+'\t'.join(encode_response(response).decode().split('\t')[8:]))
        default=Path(__file__).resolve().parents[2]/'conformance'/'swift-inspection-wire-v1.tsv'
        fixture=Path(os.environ.get('STATELESS_RUST_INSPECTION_WIRE', str(default)))
        expected=[]
        for line in fixture.read_text().splitlines():
            parts=line.split('\t'); expected.append(parts[0]+'\t'+'\t'.join(parts[9:]))
        self.assertEqual(actual,expected)

    def test_response_limit_counts_pairs_not_wire_tokens(self):
        limits=ProtocolLimits(max_response_fields=32)
        fields=tuple((f'k{i}',f'string:{i}') for i in range(32))
        response=Response('s',1,1,0,0,None,fields)
        encoded=encode_response(response,limits)
        self.assertEqual(decode_response(encoded,limits),response)
        with self.assertRaisesRegex(ProtocolError,'limit'):
            encode_response(replace(response,fields=fields+(('extra','string:x'),)),limits)
        malformed=encoded.replace(b'u64:32\tk0',b'u64:33\tk0',1)
        with self.assertRaisesRegex(ProtocolError,'malformed'): decode_response(malformed,limits)

    def test_unpaired_surrogate_is_malformed_without_consuming_request_id(self):
        p=self.adapter()
        request=self.request(p,command='snapshot',arguments=('\ud800',))
        with self.assertRaisesRegex(ProtocolError,'malformed'): encode_request(request)
        self.assertEqual(p.handle(request).error,'malformed')
        self.assertIsNone(p.handle(replace(request,arguments=())).error)
        with self.assertRaisesRegex(ProtocolError,'malformed'):
            encode_response(Response('counter',1,1,0,0,None,(('value','string:\ud800'),)))

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

    def test_step_reserves_acknowledgement_before_delivery(self):
        def make(name):
            session=DebugSession(name,Counter(),InputPolicy('all',lambda *args:True))
            return ProtocolSession(session,decode_input=lambda data:int(data),authority=ProtocolAuthority(deliver_inputs=True))
        p=make('x'*1900)
        fields=(('delivered','bool:false'),('sequence','u64:18446744073709551615'))
        worst=replace(p._response(1,fields=fields),revision=(1<<64)-1,configuration=(1<<64)-1)
        p=make('x'*(1900+2049-len(encode_response(worst))))
        request=replace(self.request(p,command='step',arguments=('bytes:31',)),session=p.session.id)
        p.limits=replace(p.limits,max_frame_bytes=2048)
        self.assertLessEqual(len(encode_request(request,p.limits)),2048)
        self.assertEqual(p.handle(request).error,'limit')
        self.assertEqual((p.session.state,p.session.revision,p.session.sequence),(0,0,0))
        self.assertEqual(p.handle(request).error,'limit')
        self.assertEqual(p.session.state,0)

    def test_select_and_cancel_reserve_acknowledgements(self):
        from statelessness.debugger_workbench import EnumeratedSession
        class Enumerated(Counter):
            def inputs(self,state): return (1,)
        def make(name):
            session=EnumeratedSession(name,Enumerated(),InputPolicy.enumerated())
            return ProtocolSession(session,encode_input=lambda value:str(value).encode(),decode_input=lambda data:int(data),authority=ProtocolAuthority(deliver_inputs=True))
        for command in ('select','cancel'):
            with self.subTest(command=command):
                p=make('x'*1900)
                fields=(('delivered','bool:false'),('sequence','u64:18446744073709551615')) if command=='select' else ()
                worst=replace(p._response(2,fields=fields),revision=(1<<64)-1,configuration=(1<<64)-1)
                p=make('x'*(1900+2049-len(encode_response(worst))))
                args=()
                if command=='select':
                    inputs=replace(self.request(p,command='inputs',arguments=('u64:0','u64:1')),session=p.session.id)
                    result=p.handle(inputs);self.assertIsNone(result.error)
                    args=(dict(result.fields)['candidates.0.token'],)
                request=replace(self.request(p,2,command=command,arguments=args),session=p.session.id)
                p.limits=replace(p.limits,max_frame_bytes=2048)
                self.assertLessEqual(len(encode_request(request,p.limits)),2048)
                self.assertEqual(p.handle(request).error,'limit')
                self.assertEqual((p.session.state,p.session.revision,p.session.sequence,p.session.phase),(0,0,0,'ready'))

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
    def test_framing_rejects_invalid_io_counts(self):
        class Reader:
            def __init__(self, chunks): self.chunks=iter(chunks)
            def read(self, n): return next(self.chunks)
        for chunks in ((b'xx',), (b'\x00',b'xxxx'), (b'\x00',b'\x00\x00\x01',b'xx'), (None,)):
            with self.subTest(chunks=chunks), self.assertRaises(OSError): read_frame(Reader(chunks))
        class OddBytes(bytes):
            def __len__(self): return 0
            def __getitem__(self, key): raise AssertionError('overridden slice')
        class OddArray(bytearray):
            def __len__(self): return 0
            def __getitem__(self, key): raise AssertionError('overridden slice')
        with self.assertRaises(OSError): read_frame(Reader((OddBytes(b'xx'),)))
        self.assertEqual(read_frame(Reader((OddBytes(b'\x00'), OddArray(b'\x00'), b'\x00', b'\x03', b'a', bytearray(b'b'), b'c'))), b'abc')
        class Writer:
            def __init__(self, count): self.count=count; self.flushed=False
            def write(self, data): return self.count(data)
            def flush(self): self.flushed=True
        for count in (lambda data: len(data)+1, lambda data: True, lambda data: None, lambda data: 0):
            writer=Writer(count)
            with self.assertRaises(OSError): write_frame(writer,b'x')
            self.assertFalse(writer.flushed)
        class FragmentWriter:
            def __init__(self): self.data=bytearray(); self.flushed=False
            def write(self, data): self.data.extend(data[:1]); return 1
            def flush(self): self.flushed=True
        writer=FragmentWriter(); write_frame(writer,b'abc')
        self.assertEqual(writer.data,b'\x00\x00\x00\x03abc'); self.assertTrue(writer.flushed)
        class Count(int):
            def __le__(self, other): raise AssertionError('overridden comparison')
        writer=Writer(lambda data: Count(len(data))); write_frame(writer,b'x'); self.assertTrue(writer.flushed)
    def test_admission_dedup_retirement_reconnect(self):
        p=self.adapter(authority=ProtocolAuthority(deliver_inputs=True),limits=ProtocolLimits(max_cache_entries=1))
        request=self.request(p,command='step',arguments=('bytes:01',))
        first=p.handle(request); self.assertIsNone(first.error); self.assertEqual(p.session.state,1)
        self.assertEqual(p.handle(request),first); self.assertEqual(p.session.state,1)
        self.assertEqual(p.handle(replace(request,arguments=('bytes:02',))).error,'duplicate_conflict')
        self.assertIsNone(p.handle(self.request(p,3)).error)
        self.assertEqual(p.handle(request).error,'retired_request')
        self.assertEqual(p.handle(self.request(p,2)).error,'retired_request')
        old=p.epoch; p.disconnect(); p.reconnect(); self.assertNotEqual(old,p.epoch); self.assertEqual(p.session.state,1)
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

    def test_select_revalidates_mutable_domain_with_unrestricted_policy(self):
        from statelessness.debugger_workbench import EnumeratedSession
        from statelessness.debugger_session import SessionLimits
        allowed=[1]; calls=[]; fault=[False]
        class Enumerated(Counter):
            def inputs(self,state):
                calls.append(state)
                if fault[0]: raise RuntimeError('domain failed')
                return iter(allowed)
        s=EnumeratedSession('counter',Enumerated(),InputPolicy('all',lambda *args:True),limits=SessionLimits(max_candidates=3))
        p=ProtocolSession(s,encode_input=lambda i:str(i).encode(),decode_input=int,authority=ProtocolAuthority(deliver_inputs=True))
        token=dict(p.handle(self.request(p,command='inputs',arguments=('u64:0','u64:1'))).fields)['candidates.0.token']
        allowed[:]=[2]
        request=self.request(p,2,command='select',arguments=(token,))
        self.assertEqual(p.handle(request).error,'invalid_input')
        before=len(calls);self.assertEqual(p.handle(request).error,'invalid_input');self.assertEqual(len(calls),before)
        self.assertEqual((s.state,s.revision,s.sequence),(0,0,0))
        fault[0]=True
        self.assertEqual(p.handle(replace(request,request_id=3)).error,'callback')
        fault[0]=False;allowed[:]=[2,3,4,1]
        self.assertEqual(p.handle(replace(request,request_id=4)).error,'callback')
        self.assertEqual((s.state,s.revision),(0,0))
        allowed[:]=[1]
        self.assertIsNone(p.handle(replace(request,request_id=5)).error)
        self.assertEqual((s.state,s.revision),(1,1))
        before=len(calls)
        self.assertEqual(p.handle(replace(request,request_id=6)).error,'stale_revision')
        self.assertEqual(len(calls),before)

    def test_candidate_eviction_authority_decode_and_epoch_boundaries(self):
        from statelessness.debugger_workbench import EnumeratedSession
        calls=[];bad_decode=[False]
        class Enumerated(Counter):
            def inputs(self,state): calls.append(state);return (1,2)
        def decode(value):
            if bad_decode[0]: raise ValueError('invalid canonical input')
            return int(value)
        s=EnumeratedSession('counter',Enumerated(),InputPolicy('all',lambda *args:True))
        p=ProtocolSession(s,encode_input=lambda i:str(i).encode(),decode_input=decode,limits=ProtocolLimits(max_candidates=1),authority=ProtocolAuthority(deliver_inputs=True))
        def candidates(id,offset=0):
            return p.handle(self.request(p,id,command='inputs',arguments=(f'u64:{offset}','u64:1')))
        old=dict(candidates(1).fields)['candidates.0.token']
        page=candidates(2,1);token=dict(page.fields)['candidates.0.token']
        self.assertEqual(dict(page.fields)['next_offset'],'none')
        before=len(calls)
        self.assertEqual(p.handle(self.request(p,3,command='select',arguments=(old,))).error,'stale_handle')
        p.authority=ProtocolAuthority()
        self.assertEqual(p.handle(self.request(p,4,command='select',arguments=(token,))).error,'unauthorized')
        p.authority=ProtocolAuthority(deliver_inputs=True);bad_decode[0]=True
        self.assertEqual(p.handle(self.request(p,5,command='select',arguments=(token,))).error,'invalid_input')
        self.assertEqual(len(calls),before)
        self.assertEqual((s.state,s.revision),(0,0))
        bad_decode[0]=False;p.disconnect();p.reconnect()
        self.assertEqual(p.handle(self.request(p,1,command='select',arguments=(token,))).error,'stale_handle')
        new=dict(candidates(2).fields)['candidates.0.token'];self.assertNotIn(new,(old,token))
        self.assertIsNone(p.handle(self.request(p,3,command='select',arguments=(new,))).error)

    def test_reconnect_resets_metric_captures_and_pending_transactions(self):
        from statelessness.debugger_metrics import MetricsStore,protocol_adapters
        store=MetricsStore(1);adapters=protocol_adapters(store)
        p=self.adapter(adapters=adapters)
        capture=self.request(p,command='metric_snapshot',arguments=('none','u64:0','u64:1'))
        response=p.handle(capture);old=dict(response.fields)['metrics.handle']
        self.assertEqual(p.handle(capture),response)
        staged=adapters['metric_snapshot']((None,0,1))
        state=(p.session.state,p.session.revision,p.authority,store.current(),store.available_windows())
        p.disconnect();p.reconnect()
        self.assertEqual((p.session.state,p.session.revision,p.authority,store.current(),store.available_windows()),state)
        with self.assertRaisesRegex(ProtocolError,'stale_handle'): staged.commit()
        self.assertEqual(p.handle(capture).error,'wrong_epoch')
        request=self.request(p,command='metric_snapshot',arguments=(old,'u64:0','u64:1'))
        self.assertEqual(p.handle(request).error,'stale_handle')
        new=p.handle(self.request(p,2,command='metric_snapshot',arguments=('none','u64:0','u64:1')))
        new_handle=dict(new.fields)['metrics.handle'];self.assertNotEqual(old,new_handle)
        self.assertIsNone(p.handle(self.request(p,3,command='metric_snapshot',arguments=(new_handle,'u64:0','u64:1'))).error)
        self.assertEqual(p.handle(self.request(p,4,command='metric_snapshot',arguments=(old,'u64:0','u64:1'))).error,'stale_handle')

    def test_reconnect_reset_hooks_once_refusal_failure_and_reentrancy(self):
        calls=[];fail=[False];holder={}
        def reset():
            calls.append('shared')
            p=holder['p']
            with self.assertRaisesRegex(ProtocolError,'busy'): p.reconnect()
            with self.assertRaisesRegex(ProtocolError,'busy'): p.disconnect()
            self.assertEqual(p.handle(self.request(p)).error,'busy')
            if fail[0]: raise RuntimeError('reset failed')
        def first(args): return ()
        def second(args): return ()
        first.reset_connection=reset;second.reset_connection=reset
        def other(args): return ()
        other.reset_connection=lambda:calls.append('other')
        p=self.adapter(adapters={'metric_catalog':first,'metric_snapshot':second,'metric_window':other});holder['p']=p
        self.assertEqual(calls,[])
        p.handle(self.request(p,command='snapshot'))
        original=(p.epoch,p._high,dict(p._handles),dict(p._cache),p._next_handle)
        with self.assertRaisesRegex(ProtocolError,'invalid_input'): p.reconnect()
        self.assertEqual(calls,[])
        p.disconnect();fail[0]=True
        with self.assertRaisesRegex(ProtocolError,'callback'): p.reconnect()
        self.assertFalse(p.connected);self.assertFalse(p._busy)
        self.assertEqual((p.epoch,p._high,dict(p._handles),dict(p._cache),p._next_handle),original)
        self.assertEqual(calls,['shared'])
        fail[0]=False;p.reconnect()
        self.assertEqual(calls,['shared','shared','other'])
        self.assertTrue(p.connected);self.assertNotEqual(p.epoch,original[0])
        self.assertEqual((p._high,p._handles,p._next_handle),(0,{},original[4]))
        self.assertEqual(dict(next(iter(p._events.values()))[0])['kind'],'enum:reconnected')

    def test_reconnect_deduplicates_bound_hooks_without_user_equality(self):
        calls=[]
        class Hook:
            def __eq__(self,other): raise AssertionError('user equality invoked')
            def __call__(self): calls.append('callable')
        class Owner:
            def reset(self): calls.append('method')
        class Adapter:
            def __call__(self,args): return ()
        owner=Owner();hook=Hook();adapters={}
        for command,reset in zip(('metric_catalog','metric_snapshot','metric_window','telemetry_health'),(owner.reset,owner.reset,hook,hook)):
            adapter=Adapter();adapter.reset_connection=reset;adapters[command]=adapter
        p=self.adapter(adapters=adapters);p.disconnect();p.reconnect()
        self.assertEqual(calls,['method','callable'])

    def test_reconnect_preflights_configuration_and_invalid_reset_hooks(self):
        fail=[False];calls=[]
        def configuration():
            if fail[0]: raise RuntimeError('configuration failed')
            return 3
        def adapter(args): return ()
        adapter.reset_connection=lambda:calls.append('reset')
        p=self.adapter(adapters={'metric_catalog':adapter},configuration_revision=configuration)
        p.disconnect();old=p.epoch;fail[0]=True
        with self.assertRaisesRegex(ProtocolError,'callback'):p.reconnect()
        self.assertEqual((p.connected,p.epoch,calls),(False,old,[]))
        fail[0]=False;adapter.reset_connection=1
        with self.assertRaisesRegex(ProtocolError,'callback'):p.reconnect()
        self.assertEqual((p.connected,p.epoch,calls),(False,old,[]))
        adapter.reset_connection=lambda:True
        with self.assertRaisesRegex(ProtocolError,'callback'):p.reconnect()
        self.assertEqual((p.connected,p.epoch),(False,old))
        adapter.reset_connection=lambda:calls.append('reset')
        p.reconnect();self.assertEqual(calls,['reset'])
        self.assertEqual(dict(next(iter(p._events.values()))[0])['configuration'],'u64:3')

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
        self.assertIn(('node.children.1.node.kind','enum:redacted'),response.fields)
        self.assertEqual(dict(response.fields)['snapshot.revision'],'u64:0')
        self.assertNotIn('repr',str(response.fields))

    def test_inspection_uses_current_protocol_budgets_and_host_caps(self):
        from statelessness.debugger_inspect import DisplaySchema, InspectLimits
        seen=[]
        class Projected:
            def schema(self): return DisplaySchema('projected',1)
            def inspect(self,path,cx):
                seen.append(cx.limits)
                return cx.scalar(path,('u64','7'))
        s=DebugSession('counter',Counter(),InputPolicy('positive',lambda m,s,i:i>0))
        p=ProtocolSession(s,adapters=inspection_adapters(s,project=lambda _:Projected()))
        handle=dict(p.handle(self.request(p,command='snapshot')).fields)['handle']
        args=(handle,'u64:1','u64:0','u64:1')
        self.assertIsNone(p.handle(self.request(p,2,command='inspect',arguments=args)).error)
        self.assertEqual(seen[-1],InspectLimits(max_work=10000,max_depth=16,max_nodes=128,max_bytes=8192,max_value_bytes=2048,max_page_size=100))
        p.limits=replace(p.limits,max_frame_bytes=4096,max_field_bytes=256,max_page_size=4)
        self.assertIsNone(p.handle(self.request(p,3,command='inspect',arguments=args)).error)
        self.assertEqual((seen[-1].max_bytes,seen[-1].max_value_bytes,seen[-1].max_page_size),(512,64,4))
        custom=InspectLimits(max_work=50,max_depth=2,max_nodes=3,max_bytes=512,max_value_bytes=32,max_page_size=2)
        p=ProtocolSession(s,adapters=inspection_adapters(s,project=lambda _:Projected(),limits=custom))
        handle=dict(p.handle(self.request(p,command='snapshot')).fields)['handle']
        self.assertIsNone(p.handle(self.request(p,2,command='inspect',arguments=(handle,'u64:1','u64:0','u64:1'))).error)
        self.assertEqual(seen[-1],custom)

    def test_inspection_uses_reference_scalar_wire_layout(self):
        from statelessness.debugger_inspect import IntegerView
        s=DebugSession('counter',Counter(),InputPolicy('positive',lambda m,s,i:i>0))
        p=ProtocolSession(s,adapters=inspection_adapters(s,project=lambda _:IntegerView('u64','7')))
        handle=dict(p.handle(self.request(p,command='snapshot')).fields)['handle']
        response=p.handle(self.request(p,2,command='inspect',arguments=(handle,'u64:1','u64:0','u64:10')))
        self.assertIsNone(response.error)
        self.assertEqual(response.fields,(('schema.name','string:IntegerView'),('schema.version','u64:1'),
            ('snapshot.revision','u64:0'),('snapshot.sequence','u64:0'),('node.completeness','enum:Complete'),
            ('node.child_count','u64:0'),('node.kind','enum:integer'),('node.value','u64:7'),('node.children.returned','u64:0')))

    def test_inspection_projection_cannot_mutate_borrowed_session(self):
        from statelessness.debugger_inspect import IntegerView
        from statelessness.debugger_session import DebugError
        s=DebugSession('counter',Counter(),InputPolicy('positive',lambda m,state,i:i>0))
        attempts=[]
        def project(value):
            try: s.step(s.revision,1)
            except DebugError as error: attempts.append(str(error))
            return IntegerView('u64',str(value))
        p=ProtocolSession(s,adapters=inspection_adapters(s,project=project))
        handle=dict(p.handle(self.request(p,command='snapshot')).fields)['handle']
        response=p.handle(self.request(p,2,command='inspect',arguments=(handle,'u64:1','u64:0','u64:10')))
        self.assertIsNone(response.error)
        self.assertEqual(dict(response.fields)['node.value'],'u64:0')
        self.assertEqual((s.state,s.sequence,s.revision),(0,0,0))
        self.assertEqual(len(attempts),1)
        self.assertIn('owned',attempts[0])

    def test_inspection_errors_use_reference_invalid_input_code(self):
        from statelessness.debugger_inspect import ObjectView,FieldView
        s=DebugSession('counter',Counter(),InputPolicy('positive',lambda m,state,i:i>0))
        p=ProtocolSession(s,adapters=inspection_adapters(s,project=lambda value:ObjectView('Counter',(FieldView('value','Value',value),))))
        handle=dict(p.handle(self.request(p,command='snapshot')).fields)['handle']
        missing=p.handle(self.request(p,2,command='inspect',arguments=(handle,'u64:1','u64:0','u64:10','field:missing')))
        self.assertEqual(missing.error,'invalid_input')
        mismatch=p.handle(self.request(p,3,command='inspect',arguments=(handle,'u64:2','u64:0','u64:10')))
        self.assertEqual(mismatch.error,'invalid_input')
        self.assertEqual((s.state,s.revision),(0,0))

    def test_concrete_map_keys_through_wire_and_child_metadata(self):
        from statelessness.debugger_inspect import DisplaySchema
        entries=[('seven','string'),(True,'boolean'),(False,'false boolean'),(('char','λ'),'character'),
            (('u128',str((1<<128)-1)),'maximum u128'),(('i128',str(-(1<<127))),'minimum i128')]
        for signed in ('u','i'):
            for width in ('8','16','32','64','128','size'):
                kind=signed+width
                entries.append(((kind,'7' if signed=='u' else '-7'),kind))
        class Map:
            def schema(self): return DisplaySchema('Map')
            def inspect(self,path,cx): return cx.map(path,entries)
        s=DebugSession('counter',Counter(),InputPolicy('positive',lambda m,s,i:i>0))
        p=ProtocolSession(s,adapters=inspection_adapters(s,project=lambda _:Map()))
        def wire(request):
            reader=io.BytesIO(); write_frame(reader,encode_request(request)); reader.seek(0)
            response=p.handle(decode_request(read_frame(reader)))
            writer=io.BytesIO(); write_frame(writer,encode_response(response)); writer.seek(0)
            return decode_response(read_frame(writer))
        handle=dict(wire(self.request(p,command='snapshot')).fields)['handle']
        response=wire(self.request(p,2,command='inspect',arguments=(handle,'u64:1','u64:0','u64:30')))
        self.assertIsNone(response.error)
        fields=dict(response.fields)
        for index,(key,value) in enumerate(entries):
            if type(key) is str: token='string:'+key
            elif type(key) is bool: token='bool:'+str(key).lower()
            else: token=key[0]+':'+key[1]
            child=fields[f'node.children.{index}.segment']
            self.assertEqual(child,'key.'+token)
            response=wire(self.request(p,index+3,command='inspect',arguments=(handle,'u64:1','u64:0','u64:10','key.'+token)))
            self.assertIsNone(response.error)
            self.assertEqual(dict(response.fields)['node.value'],'string:'+value)

    def test_float_and_enum_projection_preserves_typed_wire_values(self):
        import struct
        from statelessness.debugger_inspect import FloatView, EnumView
        values=[(FloatView('f32','-0',1<<31),'f32','-0',1<<31),
                (FloatView('u8','not-a-number',(1<<64)-1),'u8','not-a-number',(1<<64)-1),
                (FloatView('f32','NaN',0x7fc00001),'f32','NaN',0x7fc00001),
                (FloatView('f64','NaN',0x7ff8000000000001),'f64','NaN',0x7ff8000000000001),
                (-0.0,'f64','-0',1<<63),
                (struct.unpack('>d',bytes.fromhex('7ff8000000000001'))[0],'f64','NaN',0x7ff8000000000001)]
        for value,kind,decimal,bits in values:
            for nested in (False,True):
                with self.subTest(kind=kind,decimal=decimal,nested=nested):
                    s=DebugSession('counter',Counter(),InputPolicy('all',lambda *args:True))
                    p=ProtocolSession(s,adapters=inspection_adapters(s,project=lambda _:[value] if nested else value))
                    handle=dict(p.handle(self.request(p,command='snapshot')).fields)['handle']
                    response=p.handle(self.request(p,2,command='inspect',arguments=(handle,'u64:1','u64:0','u64:10')))
                    self.assertIsNone(response.error)
                    fields=dict(decode_response(encode_response(response)).fields)
                    prefix='node.children.0.node' if nested else 'node'
                    self.assertEqual(fields[prefix+'.kind'],'enum:float')
                    self.assertEqual(fields[prefix+'.value'],kind+':'+decimal)
                    self.assertEqual(fields[prefix+'.bits'],'u64:'+str(bits))
        s=DebugSession('counter',Counter(),InputPolicy('all',lambda *args:True))
        p=ProtocolSession(s,adapters=inspection_adapters(s,project=lambda _:EnumView('Choice','some','Some')))
        handle=dict(p.handle(self.request(p,command='snapshot')).fields)['handle']
        response=p.handle(self.request(p,2,command='inspect',arguments=(handle,'u64:1','u64:0','u64:10')))
        self.assertIsNone(response.error)
        fields=dict(decode_response(encode_response(response)).fields)
        self.assertEqual(fields['node.value'],'string:Choice')
        self.assertEqual(fields['node.variant_id'],'string:some')
        self.assertEqual(fields['node.variant_label'],'string:Some')
