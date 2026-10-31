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
    def test_field_slots_are_captured_before_encoder_callbacks(self):
        class Value:
            def __init__(self): self.first, self.second = 1, 2
        value = Value()
        def first(number, sink):
            value.second = 9
            sink.append(bytes([number]))
        fields = (adapted_field('first', adapter(encode=first), get=lambda v:v.first),
                  field('second', U8, get=lambda v:v.second))
        positional = (adapted_field('', adapter(encode=first), get=lambda v:v.first),
                      positional_field(U8, get=lambda v:v.second))
        for schema, prefix in (
            (ordered_record('R', value_type=Value, fields=fields), b''),
            (tuple_record('T', value_type=Value, fields=positional), b''),
            (tagged_enum('E', select=lambda v:'A', cases=(case('A',tag=3,fields=fields),)), b'\x03'),
        ):
            value.second = 2
            self.assertEqual(encode_canonical(schema,value), prefix+b'\x01\x02')

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
        prefix=bytearray(); existing_sink=EncodeBuffer(prefix,4); existing_sink.extend(b'p')
        hooks=[lambda:bound.encode_state(None),lambda:bound.encode_input(None),lambda:bound.encode_output(None),lambda:bound.encode_state_into(None,existing_sink),lambda:bound.encode_input_into(None,existing_sink),lambda:bound.encode_output_into(None,existing_sink),lambda:bound.decode_state(None),lambda:bound.decode_input(None)]
        for hook in hooks:
            with self.assertRaisesRegex(ModelError,'version does not match'): hook()
        self.assertEqual(m.calls,8); self.assertEqual(limits,[]);self.assertEqual(prefix,b'p');existing_sink.finish()
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


# The immutable draft supplies independent literal expectations, never engine output.
# Its registry is translated into public explicit declarations (no application reflection).
class DraftRows(unittest.TestCase):
    @staticmethod
    def fixtures():
        import json
        from pathlib import Path
        draft=json.loads((Path(__file__).resolve().parents[2]/'docs/native-parity/derive-model-draft/cases.json').read_text(encoding="utf-8"))
        schemas={'u8':U8,'u16':U16,'bool':BOOL,'string':STRING}
        class LengthOnly(Schema):
            def encode_length(self,v,w,s): STRING.encode_length(v,w,s)
            def decode_length(self,w,r): return STRING.decode_length(w,r)
        class EncodeOnly(Schema):
            def encode_into(self,v,s): U8.encode_into(v,s)
        class DecodeOnly(Schema):
            def decode(self,r): return U8.decode(r)
        schemas.update(length_only_string=LengthOnly(),encode_only_u8=EncodeOnly(),decode_only_u8=DecodeOnly())
        def scalar(spec):
            return VectorOf(scalar(spec['vector'])) if isinstance(spec,dict) else build(spec)
        def fieldset(fields,form,enum=False):
            result=[]
            for i,f in enumerate(fields):
                def get(v,i=i,f=f,form=form,enum=enum):
                    p=v['fields'] if enum else v
                    return p[i] if form=='tuple' else p[f['name']]
                if f.get('adapter')=='be_u16':
                    result.append(adapted_field(f['name'],adapter(encode=lambda v,s:s.append(v.to_bytes(2,'big')),decode=lambda r:int.from_bytes(r.take(2),'big')),get=get))
                elif f.get('adapter'):
                    def fail(v,s): s.append(b'\xaa'); raise ModelError('adapter rejected')
                    result.append(adapted_field(f['name'],adapter(encode=fail),get=get))
                else: result.append(field(f['name'],scalar(f['codec']),get=get,length=f.get('length_width')))
            return result
        def construct(fields,form):
            return lambda xs: list(xs) if form=='tuple' else (None if form=='unit' else dict(zip([f['name'] for f in fields],xs)))
        def build(name):
            if name in schemas:return schemas[name]
            d=draft['schema_registry'][name]
            if d['kind']=='vector': s=VectorOf(build(d['element']))
            elif d['kind']=='enum':
                cases=[]
                for c in d['cases']:
                    make=construct(c['fields'],c['shape'])
                    cases.append(case(c['name'],tag=c['tag'],fields=fieldset(c['fields'],c['shape'],True),construct=lambda xs,make=make,c=c:{'case':c['name'],'fields':make(xs)}))
                s=tagged_enum(name,tag_type={1:U8,2:U16,4:U32}[d['tag_width']],select=None if d.get('requested_direction')=='decode' else lambda v:v['case'],cases=cases)
            else:
                form=d['shape']; factory=tuple_record if form=='tuple' else ordered_record
                typ=list if form=='tuple' else (type(None) if form=='unit' else dict)
                s=(unit_record(name,value_type=typ,construct=construct([],form)) if form=='unit' else factory(name,value_type=typ,fields=fieldset(d['fields'],form),construct=None if d.get('requested_direction')=='encode' else construct(d['fields'],form),layout=Rust64Layout(4) if name=='Pair' else None))
            schemas[name]=s; return s
        for name in draft['schema_registry']:build(name)
        return draft,schemas

    def test_all_positive_draft_rows_exact_wire_and_limits(self):
        draft,schemas=self.fixtures()
        for row in draft['positive_cases']:
            with self.subTest(row=row['case']):
                s=schemas[row['schema']]; wire=bytes.fromhex(row['bytes_hex']); expected=row['expected']; op=row.get('operation','roundtrip')
                if op!='decode':self.assertEqual(encode_canonical(s,row['value']),wire)
                if op!='encode':
                    l=DecodeLimits(bytes=len(wire),work=expected['min_work'],depth=expected['min_depth'],allocation=expected['allocation'],elements=expected['elements'])
                    self.assertEqual(decode_canonical(s,wire,l),expected['value'])
                    for n in range(len(wire)):
                        with self.assertRaises(ModelError):decode_canonical(s,wire[:n])
                    with self.assertRaises(ModelError):decode_canonical(s,wire+b'\0')
                for key in ('supports_encode','supports_decode'):
                    if key in expected:self.assertEqual(getattr(s,key),expected[key])

    def test_all_budget_draft_rows(self):
        draft,schemas=self.fixtures()
        for row in draft['budget_cases']:
            with self.subTest(row=row['case']):
                s=schemas[row['schema']]; wire=bytes.fromhex(row['bytes_hex']); limits=DecodeLimits(**row['limits'])
                def run():
                    if row.get('entry')=='raw':
                        r=Decoder(wire,limits); value=s.decode(r); r.finish(); return value
                    return decode_canonical(s,wire,limits)
                if 'error' in row['expected']:
                    with self.assertRaisesRegex(ModelError,row['expected']['error']):run()
                else: self.assertEqual(run(),row['expected']['value'])

    def test_field_errors_admission_and_overflow_before_prefix(self):
        draft,schemas=self.fixtures()
        for name in ('unknown-tag-before-payload','ordinary-bool-invalid','length-field-invalid-utf8','length-field-truncated','record-trailing'):
            row=next(r for r in draft['runtime_error_cases'] if r['case']==name)
            with self.subTest(row=name),self.assertRaises(ModelError):decode_canonical(schemas[row['schema']],bytes.fromhex(row['bytes_hex']))
        calls=[]
        f=adapted_field('x',adapter(decode=lambda r:calls.append('field')),get=lambda v:0)
        s=ordered_record('Admission',value_type=tuple,fields=(f,),construct=lambda xs:calls.append('construct') or xs)
        with self.assertRaisesRegex(ModelError,'byte limit'):decode_canonical(s,b'1234',DecodeLimits(bytes=3))
        self.assertEqual(calls,[])
        with self.assertRaises(ModelError):encode_canonical(schemas['Record'],{'byte':7,'ready':True,'count':4660},maximum=3)
        dest=bytearray(); sink=EncodeBuffer(dest,1000); sink.extend(b'p')
        with self.assertRaisesRegex(ModelError,'length overflow'):encode_raw(schemas['Text8'],{'text':'a'*256},sink)
        self.assertEqual(dest,b'p'); sink.finish()

    def test_complete_reader_scripts(self):
        _,schemas=self.fixtures(); e=schemas['Event8']
        r=Decoder(b'\10\11')
        with self.assertRaisesRegex(ModelError,'unknown enum tag'):e.decode(r)
        self.assertEqual(r.value(U8),9); r.finish()
        r=Decoder(b'\10\11')
        for action in (lambda:r.value(e),lambda:r.value(U8),r.finish):
            with self.assertRaisesRegex(ModelError,'unknown enum tag'):action()
        for first_raw,expected_calls in ((True,0),(False,1)):
            r=Decoder(b''); calls=[]
            if first_raw:
                with self.assertRaisesRegex(ModelError,'truncated value'):e.decode(r)
            def body(reader):
                calls.append('body')
                try:e.decode(reader)
                except ModelError:pass
                raise ModelError('later callback error')
            with self.assertRaisesRegex(ModelError,'truncated value'):r.value(body)
            with self.assertRaisesRegex(ModelError,'truncated value'):r.finish()
            self.assertEqual(len(calls),expected_calls)

    def test_diagnostics_and_builder_boundaries(self):
        _,s=self.fixtures()
        self.assertIsNone(s['Record'].trace_variant())
        self.assertEqual(s['Event8'].trace_variant({'case':'Idle','fields':None}),'Idle')
        self.assertEqual(s['Event8'].trace_variant({'case':'Ready','fields':{'ready':True,'byte':9}}),'Ready')
        renamed=tagged_enum('Renamed',select=lambda v:'Waiting',cases=(case('Waiting',tag=7,construct=lambda xs:None),))
        self.assertEqual(renamed.trace_variant(None),'Waiting');self.assertEqual(encode_canonical(renamed,None),b'\7')
        for tag in (-1,2**32):
            with self.assertRaises(ModelError):tagged_enum('E',tag_type=U32,cases=(case('X',tag=tag),))
        with self.assertRaises(TypeError):case('X')
        with self.assertRaises(ModelError):tagged_enum('E',cases=(case('A',tag=1),case('B',tag=1)))
        with self.assertRaises(ModelError):tagged_enum('E',tag_type=U64,cases=())
        for kw in ({'skip':True},{'default':0},{'unknown':True}):
            with self.assertRaises(TypeError):field('x',U8,**kw)
        with self.assertRaises(TypeError):ordered_record('R',value_type=tuple,fields=(),tag_type=U8)
        with self.assertRaises(TypeError):ordered_record('R',value_type=tuple,fields=(),unknown=True)
        with self.assertRaises(TypeError):model_codec(state=U8,input=U8,output=U8,unknown=True)
        for source in ("field('x', STRING, length=1, length=2)","model_codec(state=U8,input=U8,output=U8,version=1,version=2)"):
            with self.assertRaises(SyntaxError):compile(source,'<native-duplicate-option>','eval')
        with self.assertRaises(TypeError):field('x',STRING,length=1,adapter=adapter())
        with self.assertRaises(ModelError):tuple_record('D',value_type=tuple,fields=(positional_field(s['EncodeOnlyRecord']),),construct=tuple,require=('decode',))
        with self.assertRaises(ModelError):tagged_enum('D',cases=s['EncodeOnlyEvent'].cases,require=('decode',))
        class DecodeLengthOnly(Schema):
            def decode_length(self,w,r):return STRING.decode_length(w,r)
        with self.assertRaises(ModelError):tuple_record('D',value_type=tuple,fields=(positional_field(DecodeLengthOnly(),length=1,get=lambda v:v[0]),),require=('encode',))
        with self.assertRaises(ModelError):tuple_record('D',value_type=tuple,fields=(adapted_field('x',adapter(encode=lambda v,s:None),get=lambda v:0),),construct=tuple,require=('decode',))


    def test_unknown_tag_and_invalid_fields_never_construct(self):
        calls=[]
        payload=adapted_field('x',adapter(decode=lambda r:calls.append('payload')),get=lambda v:0)
        e=tagged_enum('E',cases=(case('Only',tag=7,fields=(payload,),construct=lambda xs:calls.append('construct')),))
        with self.assertRaisesRegex(ModelError,'unknown enum tag'):decode_canonical(e,b'\10\xff\xff')
        self.assertEqual(calls,[])
        for codec,bad in ((BOOL,b'\2'),(STRING,b'\1\xff')):
            f=field('x',codec,length=1 if codec is STRING else None)
            r=ordered_record('R',value_type=tuple,fields=(f,),construct=lambda xs:calls.append('construct') or xs)
            with self.assertRaises(ModelError):decode_canonical(r,bad)
            self.assertEqual(calls,[])

    def test_raw_draft_prefixes_and_sink_failure(self):
        _,s=self.fixtures()
        dest=bytearray();sink=EncodeBuffer(dest,100)
        with self.assertRaisesRegex(ModelError,'adapter rejected'):encode_raw(s['AdapterError'],{'prefix':7,'value':1},sink)
        self.assertEqual(dest,b'\7\xaa');sink.append(b'\xbb');sink.finish();self.assertEqual(dest,b'\7\xaa\xbb')
        for schema,value,cap,prefix in ((s['BigEndian'],[4660],1,b''),(s['Record'],{'byte':7,'ready':True,'count':4660},3,b'\7\1')):
            dest=bytearray();sink=EncodeBuffer(dest,cap)
            with self.assertRaises(ModelError):encode_raw(schema,value,sink)
            self.assertEqual(dest,prefix)
            with self.assertRaises(ModelError):sink.finish()

    def test_binder_exact_fresh_and_malformed_both_directions(self):
        _,s=self.fixtures(); calls=[]; work=[3]
        m=SimpleNamespace(metadata=lambda:SimpleNamespace(codec_version=1))
        def limits(model):calls.append(work[0]);return DecodeLimits(bytes=3,work=work[0],depth=2,allocation=0,elements=0)
        class BigEndianOutput(Schema):
            def encode_into(self,value,sink):encode_raw(s['BigEndian'],value,sink)
        output=BigEndianOutput();self.assertFalse(output.supports_decode)
        b=model_codec(state=s['Pair'],input=s['Pair'],output=output,decode_limits=limits).bind(m)
        for method in (b.decode_state,b.decode_input):
            self.assertEqual(method(b'\7\64\22'),[7,4660])
            for wire in (b'',b'\7\64',b'\7\64\22\0'):
                with self.assertRaises(ModelError):method(wire)
        work[0]=2
        with self.assertRaises(ModelError):b.decode_state(b'\7\64\22')
        work[0]=3;self.assertEqual(b.decode_state(b'\7\64\22'),[7,4660])
        before=len(calls)
        self.assertEqual(b.encode_output([4660]),b'\22\64')
        for fn,value in ((b.encode_state,[7,4660]),(b.encode_input,[7,4660])):self.assertEqual(fn(value),b'\7\64\22')
        for fn,value in ((b.encode_state_into,[7,4660]),(b.encode_input_into,[7,4660]),(b.encode_output_into,[4660])):
            sink=EncodeBuffer(bytearray(),8);fn(value,sink);sink.finish()
        self.assertEqual(len(calls),before);self.assertEqual(calls,[3]*8+[2,3])
        default=model_codec(state=s['Event8'],input=s['Event8'],output=s['BigEndian']).bind(m)
        for method in (default.decode_state,default.decode_input):
            with self.assertRaisesRegex(ModelError,'trailing value bytes'):method(b'\7\0')
            with self.assertRaisesRegex(ModelError,'truncated value'):method(b'')

if __name__ == '__main__': unittest.main()
