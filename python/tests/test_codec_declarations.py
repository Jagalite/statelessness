"""N13 native declarations; independent literal bytes and budget expectations."""
from dataclasses import dataclass
from types import SimpleNamespace
import unittest
from statelessness import *
from statelessness.codec_declarations import *

@dataclass(frozen=True)
class Header:
    kind: int
    live: bool
    count: int


def header(construct=lambda xs: Header(*xs)):
    return ordered_record('Header', value_type=Header, fields=(
        field('kind', U8, get=lambda v: v.kind),
        field('live', BOOL, get=lambda v: v.live),
        field('count', U16, get=lambda v: v.count)), construct=construct)


class Declarations(unittest.TestCase):
    def test_order_and_exact_budgets(self):
        schema = header()
        self.assertEqual(encode_canonical(schema, Header(7, True, 0x1234)), bytes.fromhex('07013412'))
        self.assertEqual(decode_canonical(schema, bytes.fromhex('07013412'), DecodeLimits(work=5, depth=3)), Header(7, True, 0x1234))
        for limits in (DecodeLimits(work=4), DecodeLimits(depth=2)):
            with self.assertRaises(ModelError): decode_canonical(schema, bytes.fromhex('07013412'), limits)
        for data in (b'', b'\7', b'\7\1', b'\7\1\64', bytes.fromhex('0701341200')):
            with self.assertRaises(ModelError): decode_canonical(schema, data)
        with self.assertRaises(ModelError): encode_canonical(schema, (7, True, 0x1234))

    def test_construct_after_fields(self):
        calls=[]; schema=header(lambda xs: calls.append(xs) or Header(*xs))
        with self.assertRaises(ModelError): decode_canonical(schema,b'\7\2\0\0')
        self.assertEqual(calls,[])
        decode_canonical(schema,b'\7\1\0\0'); self.assertEqual(len(calls),1)
        wrong=unit_record('Wrong',value_type=tuple,construct=lambda xs: [])
        with self.assertRaises(ModelError): decode_canonical(wrong,b'')

    def test_forms_and_directions(self):
        self.assertEqual(encode_canonical(unit_record('U', value_type=tuple),()),b'')
        only_decode=tuple_record('D',value_type=tuple,fields=(positional_field(U8),),construct=tuple)
        self.assertFalse(only_decode.supports_encode)
        self.assertEqual(decode_canonical(only_decode,b'\11'),(9,))
        neither=tuple_record('N',value_type=tuple,fields=(positional_field(U8),))
        self.assertFalse(neither.supports_encode); self.assertFalse(neither.supports_decode)
        with self.assertRaises(ModelError): tuple_record('N',value_type=tuple,fields=(positional_field(U8),),require=('encode',))
        both=header(); self.assertTrue(both.supports_decode)
        self.assertFalse(both.supports_encode_length); self.assertFalse(both.supports_decode_length)
        required=ordered_record('H',value_type=Header,fields=both.fields,construct=lambda xs: Header(*xs),require=('encode',))
        self.assertTrue(required.supports_decode)

    def test_length_only_and_adapter_routes(self):
        class LengthOnly(Schema):
            def encode_length(self,v,w,s): STRING.encode_length(v,w,s)
            def decode_length(self,w,r): return STRING.decode_length(w,r)
        length=tuple_record('L',value_type=tuple,fields=(positional_field(LengthOnly(),get=lambda v:v[0],length=1),),construct=tuple)
        self.assertTrue(length.supports_encode); self.assertTrue(length.supports_decode)
        self.assertEqual(encode_canonical(length,('abc',)),b'\3abc')
        self.assertEqual(decode_canonical(length,b'\3abc',DecodeLimits(work=5,depth=2)),('abc',))
        direct=tuple_record('A',value_type=tuple,fields=(adapted_field('',adapter(encode=lambda v,s:s.append(v), decode=lambda r:bytes(r.take(3))),get=lambda v:v[0]),),construct=tuple)
        self.assertEqual(decode_canonical(direct,b'abc',DecodeLimits(work=1,depth=1)),(b'abc',))
        self.assertEqual(encode_canonical(direct,(b'abc',)),b'abc')
        for enc, dec in ((None,None),(lambda v,s:None,None),(None,lambda r:0),(lambda v,s:None,lambda r:0)):
            d=tuple_record('A',value_type=tuple,fields=(adapted_field('',adapter(encode=enc,decode=dec),get=lambda v:0),),construct=tuple)
            self.assertEqual(d.supports_encode,enc is not None); self.assertEqual(d.supports_decode,dec is not None)

    def test_public_configuration_validation_and_async(self):
        from statelessness.codec_declarations import Case, Field
        for tag in (-1, True, 1.0):
            with self.assertRaises(ModelError): Case('Bad',tag,(),tuple)
        with self.assertRaises(ModelError): Field('x',U8,None,3)
        with self.assertRaises(ModelError): Field('x',adapter(),None,1,True)
        async def bad_decode(reader): return 1
        async def bad_encode(value,sink): return None
        s=tuple_record('Async',value_type=tuple,fields=(adapted_field('',adapter(encode=bad_encode,decode=bad_decode),get=lambda v:0),),construct=tuple)
        with self.assertRaisesRegex(ModelError,'synchronous'): encode_canonical(s,())
        with self.assertRaisesRegex(ModelError,'synchronous'): decode_canonical(s,b'')
        e=tagged_enum('Positional',select=lambda v:'P',cases=(case('P',tag=3,fields=(positional_field(U8,get=lambda v:v[0]),positional_field(U8,get=lambda v:v[1])),construct=tuple),))
        self.assertEqual(encode_canonical(e,(4,5)),b'\3\4\5')
        self.assertEqual(decode_canonical(e,b'\3\4\5'),(4,5))

    def test_raw_error_priority_and_leases(self):
        held=[]
        def fail(v,s): held.append(s); s.append(b'x'); raise ValueError('callback wins')
        schema=tuple_record('A',value_type=tuple,fields=(adapted_field('',adapter(encode=fail),get=lambda v:0),))
        data=bytearray(); sink=EncodeBuffer(data,4)
        with self.assertRaisesRegex(ValueError,'callback wins'): encode_raw(schema,(),sink)
        sink.append(b'y'); sink.finish(); self.assertEqual(data,b'xy')
        with self.assertRaises(ModelError): held[0].append(b'z')
        def sticky(v,s):
            try: s.append(b'overflow')
            except ModelError: pass
            raise ValueError('callback wins')
        schema=tuple_record('S',value_type=tuple,fields=(adapted_field('',adapter(encode=sticky),get=lambda v:0),))
        sink=EncodeBuffer(bytearray(),1)
        with self.assertRaisesRegex(ValueError,'callback wins'): encode_raw(schema,(),sink)
        with self.assertRaisesRegex(ModelError,'byte limit'): sink.finish()

    def test_enums(self):
        logs=[]
        e=tagged_enum('E',tag_type=U16,select=lambda v:v[0],cases=(
            case('Reset',tag=65535,construct=lambda xs:('Reset',)),
            case('Set',tag=400,fields=(field('n',U16,get=lambda v:v[1]),),construct=lambda xs:('Set',xs[0]))))
        self.assertEqual(encode_canonical(e,('Set',0x1234)),bytes.fromhex('90013412'))
        self.assertEqual(encode_canonical(e,('Reset',)),b'\xff\xff')
        self.assertEqual(decode_canonical(e,bytes.fromhex('90013412'),DecodeLimits(work=3,depth=2)),('Set',0x1234))
        with self.assertRaisesRegex(ModelError,'unknown enum tag'): decode_canonical(e,b'\0\0')
        r=Decoder(b'\0\0\7')
        with self.assertRaises(ModelError): e.decode(r)
        self.assertEqual(r.value(U8),7); r.finish()
        r=Decoder(b'\0\0\7')
        with self.assertRaises(ModelError): r.value(e)
        with self.assertRaisesRegex(ModelError,'unknown enum tag'): r.value(U8)
        unavailable=tagged_enum('E',select=lambda v:'A',cases=(case('A',tag=0,construct=tuple),case('B',tag=1,fields=(adapted_field('x',adapter(encode=lambda v,s:None),get=lambda v:0),),construct=tuple)))
        self.assertFalse(unavailable.supports_decode)
        with self.assertRaises(ModelError): tagged_enum('E',select=lambda v:'A',cases=unavailable.cases,require=('decode',))

    def test_mutation_layout_and_negatives(self):
        fields=[field('x',U8,get=lambda v:v[0])]
        s=ordered_record('P',value_type=tuple,fields=fields,construct=tuple,layout=Rust64Layout(4))
        fields.clear(); self.assertEqual(encode_canonical(s,(7,)),b'\7')
        with self.assertRaises(AttributeError): s.name='Other'
        v=VectorOf(s)
        self.assertEqual(decode_canonical(v,b'\2\0\0\0\7\10',DecodeLimits(allocation=8)),[(7,),(8,)])
        with self.assertRaises(ModelError): decode_canonical(v,b'\2\0\0\0\7\10',DecodeLimits(allocation=7))
        class Custom:
            def encode_into(self,v,s): s.append(b'a')
            def decode(self,r): return 1
        c=Custom(); s=tuple_record('C',value_type=tuple,fields=(positional_field(c,get=lambda v:0),),construct=tuple)
        c.encode_into=lambda v,s:s.append(b'b'); self.assertEqual(encode_canonical(s,()),b'a')
        for width in (True,0,3,256):
            with self.assertRaises(ModelError): field('x',STRING,length=width)
        for tag in (True,-1,1.0):
            with self.assertRaises(ModelError): case('X',tag=tag)
        with self.assertRaises(ModelError): tagged_enum('E',cases=(case('A',tag=256),))
        with self.assertRaises(ModelError): tagged_enum('E',cases=(case('A',tag=1),case('A',tag=2)))
        with self.assertRaises(ModelError): ordered_record('P',value_type=tuple,fields=(field('x',U8),field('x',U8)))


class Binding(unittest.TestCase):
    def test_all_eight_version_gates_and_fresh_limits(self):
        class Model:
            calls=0; version=2; limit=2
            def metadata(self): self.calls+=1; return SimpleNamespace(codec_version=self.version)
            def initial_state(self): return 42
        m=Model(); limits=[]
        def provider(model): limits.append(model.limit); return DecodeLimits(work=model.limit)
        output=unit_record('O',value_type=tuple)
        bound=model_codec(state=U8,input=U8,output=output,decode_limits=provider).bind(m)
        hooks=[lambda:bound.encode_state(None),lambda:bound.encode_input(None),lambda:bound.encode_output(None),lambda:bound.encode_state_into(None,None),lambda:bound.encode_input_into(None,None),lambda:bound.encode_output_into(None,None),lambda:bound.decode_state(None),lambda:bound.decode_input(None)]
        for hook in hooks:
            with self.assertRaisesRegex(ModelError,'version does not match'): hook()
        self.assertEqual(m.calls,8); self.assertEqual(limits,[])
        self.assertFalse(hasattr(bound,'decode_output')); self.assertFalse(hasattr(bound,'estimate'))
        self.assertEqual(bound.initial_state(),42)
        m.version=1
        self.assertEqual(bound.encode_state(7),b'\7'); self.assertEqual(bound.encode_output(()),b'')
        self.assertEqual(limits,[])
        self.assertEqual(bound.decode_state(b'\7'),7)
        m.limit=0
        with self.assertRaisesRegex(ModelError,'work limit'): bound.decode_input(b'\7')
        self.assertEqual(limits,[2,0])
        sink=EncodeBuffer(bytearray(),4); sink.append(b'p'); bound.encode_input_into(8,sink)
        self.assertEqual(sink.snapshot(),b'p\10')
        self.assertEqual(m.calls,13)

    def test_engine_recorder_and_causal_capture_encode_only_output(self):
        from statelessness import (Metadata, Transition, Check, record, replay,
            Recorder, EvidenceRecorder, RunConfig)
        class Model:
            def __init__(self): self.steps=0; self.checks=0
            def metadata(self): return Metadata('n13', build='test')
            def initial_state(self): return 0
            def step(self, state, input):
                self.steps+=1; return Transition(state+input, ((),))
            def check_state(self, state): self.checks+=1; return (Check('ok'),)
            def check_transition(self, *args): return ()
        output=unit_record('Event',value_type=tuple)
        native=Model(); bound=model_codec(state=U8,input=U8,output=output).bind(native)
        trace=record(bound,[1,2]); self.assertEqual(native.steps,2)
        self.assertEqual(trace.steps[0].outputs,(b'',))
        self.assertEqual(replay(bound,trace).outcome,'exact')
        recorder=Recorder(bound,0)
        recorder.observe(bound,0,1,Transition(1,((),)))
        self.assertEqual(replay(bound,recorder.snapshot()).outcome,'exact')
        from statelessness import check_initial, check_turn, RecorderOptions, Provenance, EvidenceLimits, CausalContext
        evidence=EvidenceRecorder(check_initial(bound,0),0,RunConfig(),RecorderOptions(),Provenance('n13'),EvidenceLimits())
        turn=check_turn(bound,0,1,Transition(1,((),)),1)
        count=(native.steps,native.checks)
        evidence.observe(turn,CausalContext())
        self.assertEqual((native.steps,native.checks),count)
        self.assertEqual(len(evidence.snapshot().steps),1)

    def test_binding_declarations_and_provider(self):
        for version in (0,True,2**32):
            with self.assertRaises(ModelError): model_codec(state=U8,input=U8,output=U8,version=version)
        with self.assertRaises(ModelError): model_codec(state=unit_record('S',value_type=tuple),input=U8,output=U8)
        m=SimpleNamespace(metadata=lambda:SimpleNamespace(codec_version=1))
        for provider in (lambda m: [],lambda m: None):
            b=model_codec(state=U8,input=U8,output=U8,decode_limits=provider).bind(m)
            with self.assertRaises(ModelError): b.decode_state(b'\1')

if __name__ == '__main__': unittest.main()
