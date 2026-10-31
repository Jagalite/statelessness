"""Canonical public codec conformance; arithmetic-only rows are labeled separately."""
import json
import os
from pathlib import Path
import unittest
from statelessness import EncodeBuffer, ModelError, OracleState
from statelessness import value_codec as vc


CORPUS = Path(os.environ.get('STATELESS_VALUE_CORPUS', str(Path(__file__).resolve().parents[2] / 'conformance' / 'canonical-value-codec-v1.json')))


class LengthSchema:
    def __init__(self, base, width): self.base, self.width = base, width
    @property
    def allocation_layout(self): return self.base.allocation_layout
    def encode_into(self, value, sink): return self.base.encode_length(value, self.width, sink)
    def decode(self, reader): return self.base.decode_length(self.width, reader)
    def encode_length(self, value, width, sink): return self.base.encode_length(value, width, sink)
    def decode_length(self, width, reader): return self.base.decode_length(width, reader)


def schema(raw):
    kind = raw['kind']
    layout = raw.get('layout')
    if layout is not None:
        layout = vc.Rust64Layout(int(layout['size']), int(layout.get('alignment', 1)), layout.get('profile', 'corpus-explicit-rust64'))
    kwargs = {} if layout is None else dict(layout=layout)
    if kind in ('u8','u16','u32','u64','u128','i8','i16','i32','i64','i128','bool','unit','string'):
        result = getattr(vc, kind.upper())
    elif kind == 'option':
        # These finite corpus descriptors are explicit source expectations; their
        # target measurements are a separate Rust reference qualification gate.
        key = raw['element']['kind']
        weights = {'unit':1, 'bool':1, 'u8':2, 'u64':16, 'string':24}
        if layout is None and key in weights: kwargs = dict(layout=vc.Rust64Layout(weights[key], profile='corpus-explicit-rust64'))
        result = vc.OptionOf(schema(raw['element']), **kwargs)
    elif kind == 'vector': result = vc.VectorOf(schema(raw['element']), **kwargs)
    elif kind == 'array':
        if layout is None and raw['element']['kind'] == 'u16' and raw['length'] == 3:
            kwargs = dict(layout=vc.Rust64Layout(6,alignment=2,profile='corpus-explicit-rust64'))
        result = vc.ArrayOf(schema(raw['element']), raw['length'], **kwargs)
    elif kind == 'tuple': result = vc.TupleOf(*(schema(field) for field in raw['fields']), **kwargs)
    elif kind == 'oracle_state': result = vc.OracleStateOf(schema(raw['model']), schema(raw['oracle']), **kwargs)
    else: raise AssertionError('unknown corpus schema ' + kind)
    if kind in ('string', 'vector') and raw.get('length_width', 4) != 4:
        result = LengthSchema(result, raw['length_width'])
    return result


def value(raw, encoded):
    kind = raw['kind']
    if kind[0] in 'ui' and kind[1:].isdigit(): return int(encoded) if type(encoded) is str else encoded
    if kind == 'unit': return ()
    if kind == 'option': return vc.Some(value(raw['element'], encoded['value'])) if encoded['some'] else None
    if kind in ('vector', 'array'): return [value(raw['element'], item) for item in encoded]
    if kind == 'tuple': return tuple(value(field, item) for field,item in zip(raw['fields'], encoded))
    if kind == 'oracle_state': return OracleState(value(raw['model'], encoded['model']), value(raw['oracle'], encoded['oracle']))
    return encoded


def normalized(raw, decoded):
    kind = raw['kind']
    if kind[0] in 'ui' and kind[1:].isdigit(): return str(decoded)
    if kind == 'unit': return None
    if kind == 'option': return dict(some=False) if decoded is None else dict(some=True, value=normalized(raw['element'], decoded.value))
    if kind in ('vector', 'array'): return [normalized(raw['element'], item) for item in decoded]
    if kind == 'tuple': return [normalized(field,item) for field,item in zip(raw['fields'], decoded)]
    if kind == 'oracle_state': return dict(model=normalized(raw['model'], decoded.model), oracle=normalized(raw['oracle'], decoded.oracle))
    return decoded


def limits(raw): return vc.DecodeLimits(**{key:int(number) for key,number in raw.items()})


def decode(row, data, selected=None):
    descriptor = schema(row['schema'])
    selected = limits(row.get('limits', {})) if selected is None else selected
    if row.get('entry', 'guarded_value') == 'guarded_value': return vc.decode_canonical(descriptor, data, limits=selected)
    reader = vc.Decoder(data, selected)
    operation = lambda current: descriptor.decode_length(row['schema'].get('length_width',4), current)
    result = reader.value(operation) if row['entry'] == 'guarded_length' else operation(reader)
    reader.finish()
    return result


def encode(row):
    descriptor = schema(row['schema']); actual = value(row['schema'], row['value'])
    if row.get('entry', 'guarded_value') == 'guarded_value':
        return vc.encode_canonical(descriptor, actual, maximum=int(row['encode_max_bytes']))
    data = bytearray(); sink = EncodeBuffer(data, int(row['encode_max_bytes']))
    descriptor.encode_length(actual, row['schema'].get('length_width',4), sink); sink.finish()
    return bytes(data)


class SharedValueCodecTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.corpus = json.loads(CORPUS.read_text(encoding="utf-8"))

    def test_shared_values_and_automatic_ownership_prefixes_and_finish(self):
        for row in self.corpus['value_cases']:
            with self.subTest(case=row['case']):
                data = bytes.fromhex(row['bytes_hex'])
                self.assertEqual(encode(row), data)
                self.assertEqual(normalized(row['schema'], decode(row, data)), row['expected']['value'])
                for count in range(len(data)):
                    with self.subTest(prefix=count), self.assertRaisesRegex(ModelError, 'truncated value'):
                        decode(row, data[:count])
                with self.assertRaisesRegex(ModelError, 'trailing value bytes'):
                    decode(row, data+b'\0', vc.DecodeLimits())
                backing = bytearray(b'prefix'+data+b'suffix')
                result = decode(row, memoryview(backing)[6:6+len(data)])
                backing[:] = b'x' * len(backing)
                self.assertEqual(normalized(row['schema'], result), row['expected']['value'])

    def test_shared_decode_errors(self):
        for row in self.corpus['decode_error_cases']:
            with self.subTest(case=row['case']):
                with self.assertRaises(ModelError) as caught:
                    decode(row, bytes.fromhex(row['bytes_hex']))
                self.assertEqual(str(caught.exception), row['expected']['error'])

    def test_shared_encode_caps(self):
        for row in self.corpus['encode_error_cases']:
            with self.subTest(case=row['case']):
                with self.assertRaises(ModelError) as caught: encode(row)
                self.assertEqual(str(caught.exception), row['expected']['error'])

    def test_native_admission_at_public_encoder(self):
        categories = {'integer_range': 'integer', 'integer_type': 'integer', 'boolean_type': 'bool', 'invalid_unicode': 'UTF-8', 'array_length': 'array', 'integer_type_or_unsafe_number':'integer'}
        for row in self.corpus['native_admission_cases']:
            with self.subTest(case=row['case']):
                if row.get('host_value',{}).get('kind') == 'typescript_number':
                    # Python has no TypeScript Number admission boundary. This
                    # row is classified, never coerced into a different host type.
                    self.assertEqual(row['scope'],'native_only')
                    self.assertEqual(row['expected_error'],'integer_type_or_unsafe_number')
                    continue
                if 'utf16_code_units' in row: actual = ''.join(chr(c) for c in row['utf16_code_units'])
                elif 'host_value' in row: actual = float(row['host_value']['decimal'])
                else: actual = value(row['schema'], row['value'])
                with self.assertRaises(ModelError) as caught: vc.encode_canonical(schema(row['schema']), actual)
                self.assertIn(categories[row['expected_error']], str(caught.exception))

    def test_arithmetic_only_length_ledger_not_public_codec_execution(self):
        for row in self.corpus['length_cases']:
            with self.subTest(case=row['case']):
                if row['operation'] == 'encode_length_empty':
                    sink = EncodeBuffer(bytearray(),8)
                    with self.assertRaises(ModelError) as caught:
                        schema(row['schema']).encode_length('',row['width'],sink)
                    self.assertEqual(str(caught.exception),row['expected']['error'])
                    continue
                self.assertEqual(row['scope'], 'arithmetic_only_not_an_exported_rust_primitive')
                count, width = int(row['count']), row['width']
                expected = row['expected']
                if width not in (1,2,4): self.assertEqual(expected['error'], 'unsupported length width')
                elif count >= 1 << (width*8): self.assertEqual(expected['error'], 'length overflow')
                else: self.assertEqual(count.to_bytes(width,'little').hex(), expected['bytes_hex'])

    def test_shared_public_reader_scripts(self):
        for row in self.corpus['reader_cases']:
            with self.subTest(case=row['case']):
                events, results = [], []
                try: reader = vc.Decoder(bytes.fromhex(row['bytes_hex']), limits(row.get('limits',{})))
                except ModelError as error:
                    self.assertEqual(str(error), row['expected']['construction_error'])
                    self.assertEqual(row['expected']['results'], [])
                    self.assertEqual(row['expected']['callback_events'], [])
                    continue
                self.assertNotIn('construction_error', row['expected'])
                def execute(action, current):
                    kind = action['kind']
                    if kind == 'take': return bytes(current.take(int(action['count']))).hex()
                    if kind == 'collection': current.collection(int(action['count']), int(action['element_size'])); return None
                    if kind == 'finish': current.finish(); return None
                    if kind in ('value','direct_decode','direct_length'):
                        descriptor = schema(action['schema'])
                        if kind == 'value': actual = current.value(descriptor)
                        elif kind == 'direct_decode': actual = descriptor.decode(current)
                        else: actual = descriptor.decode_length(action['width'], current)
                        return normalized(action['schema'], actual)
                    if kind == 'value_custom':
                        def callback(nested):
                            events.append('enter')
                            for operation in action['body']:
                                try: execute(operation,nested)
                                except ModelError as error:
                                    events.append(operation['kind']+':error:'+str(error))
                                    if operation.get('on_error') == 'swallow': events.append('swallow')
                                    else: events.append('propagate:'+str(error)); raise
                                else: events.append(operation['kind']+':ok')
                            if 'error' in action['returns']:
                                events.append('return_error:'+action['returns']['error']); raise ModelError(action['returns']['error'])
                            events.append('return_ok'); return action['returns']['ok']
                        return current.value(callback)
                    raise AssertionError('unknown reader operation '+kind)
                for action in row['actions']:
                    try: results.append(dict(value=execute(action,reader), error=None))
                    except ModelError as error: results.append(dict(value=None,error=str(error)))
                self.assertEqual(results,row['expected']['results'])
                self.assertEqual(events,row['expected']['callback_events'])

class NativeValueCodecTests(unittest.TestCase):
    def test_mutable_input_is_frozen_before_custom_callbacks_and_take_is_readonly(self):
        source = bytearray(b'\x01\x02')
        reader = vc.Decoder(source)
        source[:] = b'\xff\xff'
        first = reader.take(1)
        self.assertTrue(first.readonly)
        self.assertEqual(bytes(first), b'\x01')
        with self.assertRaises(TypeError): first[0] = 9
        self.assertEqual(reader.value(vc.U8), 2)
        reader.finish()

    def test_custom_guard_depth_and_exception_first_failure(self):
        reader = vc.Decoder(b'\x01', vc.DecodeLimits(depth=2,work=3))
        self.assertEqual(reader.value(lambda nested: nested.value(vc.U8)), 1)
        self.assertEqual(reader.value(vc.UNIT), ())
        reader.finish()
        reader = vc.Decoder(b'')
        def broken(current):
            try: current.take(1)
            except ModelError: pass
            raise RuntimeError('secondary callback error')
        with self.assertRaisesRegex(ModelError,'truncated value'): reader.value(broken)
        with self.assertRaisesRegex(ModelError,'truncated value'): reader.finish()
        with self.assertRaisesRegex(ModelError,'truncated value'): reader.value(lambda current: 'must not run')

    def test_unknown_compound_layout_requires_explicit_descriptor_only_for_collection_storage(self):
        compound = vc.TupleOf(vc.U8,vc.U16,vc.U32)
        raw = b'\x01\x02\x00\x03\x00\x00\x00'
        self.assertEqual(vc.decode_canonical(compound,raw,limits=vc.DecodeLimits(allocation=0)), (1,2,3))
        with self.assertRaisesRegex(ModelError,'allocation layout'):
            vc.decode_canonical(vc.VectorOf(compound),b'\x01\x00\x00\x00'+raw)
        declared = vc.TupleOf(vc.U8,vc.U16,vc.U32,layout=vc.Rust64Layout(8,alignment=4,profile='native-test-explicit'))
        self.assertEqual(vc.decode_canonical(vc.VectorOf(declared),b'\x01\x00\x00\x00'+raw,limits=vc.DecodeLimits(allocation=8)), [(1,2,3)])
        with self.assertRaisesRegex(ModelError,'decode allocation limit'):
            vc.decode_canonical(vc.VectorOf(declared),b'\x01\x00\x00\x00'+raw,limits=vc.DecodeLimits(allocation=7))

    def test_custom_codec_nested_composition_and_revocable_encoding_sink(self):
        escaped = []
        class Pair:
            allocation_layout = vc.Rust64Layout(4,alignment=2,profile='native-test-explicit')
            def encode_into(self,pair,out):
                escaped.append(out)
                vc.U16.encode_into(pair[0],out)
                vc.U16.encode_into(pair[1],out)
            def decode(self,reader): return (reader.value(vc.U16),reader.value(vc.U16))
        data = vc.encode_canonical(Pair(),(1,2),maximum=4)
        self.assertEqual(data,b'\x01\x00\x02\x00')
        with self.assertRaisesRegex(ModelError,'expired'): escaped[0].append(b'')
        self.assertEqual(vc.decode_canonical(Pair(),data),(1,2))
        self.assertEqual(vc.decode_canonical(vc.VectorOf(Pair()),b'\x01\x00\x00\x00'+data,limits=vc.DecodeLimits(allocation=4)),[(1,2)])
        storage = bytearray(); sink = EncodeBuffer(storage,5); sink.append(b'p')
        vc.encode_into(Pair(),(1,2),sink); sink.finish()
        self.assertEqual(bytes(storage),b'p'+data)
        with self.assertRaisesRegex(ModelError,'expired'): escaped[-1].finish()

    def test_ignored_encoder_failure_is_sticky_and_explicit_later_error_stays_primary(self):
        class Broken:
            def encode_into(self,value,out):
                try: out.append(b'oversized')
                except ModelError: pass
        with self.assertRaisesRegex(ModelError,'encoded blob exceeds byte limit'):
            vc.encode_canonical(Broken(),(),maximum=1)
        class Explicit(Broken):
            def encode_into(self,value,out): super().encode_into(value,out); raise ModelError('explicit later error')
        with self.assertRaisesRegex(ModelError,'explicit later error'):
            vc.encode_canonical(Explicit(),(),maximum=1)

    def test_strict_scalar_unit_option_and_schema_shapes(self):
        class Integer(int): pass
        for descriptor, actual in ((vc.U8,Integer(1)),(vc.U8,True),(vc.BOOL,1),(vc.UNIT,None),
                                    (vc.OptionOf(vc.UNIT),()),(vc.TupleOf(vc.U8),(1,2)),
                                    (vc.ArrayOf(vc.U8,2),[1])):
            with self.subTest(descriptor=descriptor,actual=actual), self.assertRaises((ModelError,ValueError)):
                vc.encode_canonical(descriptor,actual)
        for bad in (-1, True, 1.5):
            with self.assertRaises((ModelError,ValueError)): vc.DecodeLimits(work=bad)
        with self.assertRaises((ModelError,ValueError)): vc.TupleOf()
        with self.assertRaises((ModelError,ValueError)): vc.TupleOf(vc.U8,vc.U8,vc.U8,vc.U8,vc.U8)

    def test_limits_are_snapshotted_before_decoder_callbacks(self):
        selected = vc.DecodeLimits(work=1)
        reader = vc.Decoder(b'',selected)
        object.__setattr__(selected,'work',100)
        self.assertEqual(reader.value(vc.UNIT),())
        with self.assertRaisesRegex(ModelError,'decode work limit'): reader.value(vc.UNIT)

    def test_nonguarded_utf8_error_does_not_hide_prior_take_failure(self):
        reader = vc.Decoder(b'\x01\x00\x00\x00')
        def custom(current):
            try: vc.STRING.decode(current)
            except ModelError: pass
            return ()
        with self.assertRaisesRegex(ModelError,'truncated value'): reader.value(custom)

    def test_async_callbacks_do_not_escape_as_decoded_values_or_encoder_success(self):
        async def decode_async(reader): return ()
        with self.assertRaises(ModelError): vc.Decoder(b'').value(decode_async)
        class Async:
            async def encode_into(self,value,out): return None
        with self.assertRaises(ModelError): vc.encode_canonical(Async(),())

    def test_builtin_schema_configuration_is_immutable(self):
        for descriptor, field, replacement in ((vc.U8,'bits',128), (vc.BOOL,'allocation_layout',vc.Rust64Layout(0)),
                                                (vc.VectorOf(vc.U8),'element',vc.UNIT),
                                                (vc.ArrayOf(vc.U8,2),'length',3),
                                                (vc.TupleOf(vc.U8),'fields',(vc.UNIT,)),
                                                (vc.OptionOf(vc.U8),'element',vc.UNIT)):
            with self.subTest(field=field), self.assertRaises((AttributeError,TypeError,ModelError)):
                setattr(descriptor,field,replacement)
        self.assertEqual(vc.encode_canonical(vc.U8,1),b'\x01')

class RawEncoderBoundaryTests(unittest.TestCase):
    def test_shared_raw_encoder_scripts(self):
        corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
        for row in corpus['encoder_cases']:
            with self.subTest(case=row['case']):
                storage = bytearray(); sink = EncodeBuffer(storage,int(row['max_bytes']))
                results = []
                for action in row['actions']:
                    try:
                        kind = action['kind']
                        if kind == 'append': sink.append(bytes.fromhex(action['bytes_hex']))
                        elif kind == 'finish': sink.finish()
                        elif kind == 'direct_length': vc.encode_length_raw(schema(action['schema']),value(action['schema'],action['value']),action['width'],sink)
                        elif kind == 'direct_encode': vc.encode_raw(schema(action['schema']),value(action['schema'],action['value']),sink)
                        elif kind == 'direct_encode_custom':
                            class Custom:
                                def encode_into(self,actual,out):
                                    try: out.append(bytes.fromhex(action['prefix_hex']))
                                    except ModelError:
                                        if not action.get('ignore_prefix_error',False): raise
                                    if action.get('returns_error') is not None:
                                        raise ModelError(action['returns_error'])
                            vc.encode_raw(Custom(),(),sink)
                        else: raise AssertionError('unknown encoder action '+kind)
                        results.append(dict(value=None,error=None,error_family=None))
                    except ModelError as error:
                        message = str(error)
                        # Rust finish has a combined byte/allocation diagnostic;
                        # Python retains its original narrower byte error text.
                        if action['kind'] == 'finish' and message == 'encoded blob exceeds byte limit':
                            message = 'encoded blob exceeds byte or allocation limit'
                        if message.startswith('encoded blob exceeds '): family = 'encode_limit'
                        elif message == 'unsupported length width': family = 'unsupported_length_width'
                        elif message == 'length overflow': family = 'length_overflow'
                        else: family = 'custom_callback'
                        results.append(dict(value=None,error=message,error_family=family))
                self.assertEqual(results,row['expected']['results'])
                self.assertEqual(bytes(storage).hex(),row['expected']['bytes_hex'])

    def test_raw_nested_returned_validation_error_keeps_prefix_and_allows_recovery(self):
        nested = vc.TupleOf(vc.U8,vc.VectorOf(vc.STRING))
        # No string UTF-8 admission can succeed for this second nested element.
        actual = (7,['ok','\ud800'])
        storage = bytearray(); sink = EncodeBuffer(storage,64)
        with self.assertRaisesRegex(ModelError,'invalid UTF-8'):
            vc.encode_raw(nested,actual,sink)
        sink.append(b'\xaa'); sink.finish()
        self.assertEqual(bytes(storage),b'\x07\x02\x00\x00\x00\x02\x00\x00\x00ok\xaa')
        # Guarded invocation applies the stronger native sticky callback policy.
        guarded = EncodeBuffer(bytearray(),64)
        with self.assertRaisesRegex(ModelError,'invalid UTF-8'): vc.encode_into(nested,actual,guarded)
        with self.assertRaisesRegex(ModelError,'invalid UTF-8'): guarded.finish()

    def test_raw_custom_encoder_and_length_callback_leases_expire_on_error(self):
        retained = []
        class Custom:
            def encode_into(self,value,out): retained.append(out); out.append(b'x'); raise ModelError('custom failure')
            def encode_length(self,value,width,out): retained.append(out); out.append(b'y'); raise ModelError('length failure')
        storage = bytearray(); sink = EncodeBuffer(storage,4)
        with self.assertRaisesRegex(ModelError,'custom failure'): vc.encode_raw(Custom(),(),sink)
        with self.assertRaisesRegex(ModelError,'length failure'): vc.encode_length_raw(Custom(),(),1,sink)
        for lease in retained:
            for operation in (lambda: lease.append(b''), lease.finish, lease.snapshot):
                with self.assertRaisesRegex(ModelError,'expired'): operation()
        sink.append(b'z'); sink.finish()
        self.assertEqual(bytes(storage),b'xyz')

    def test_raw_callbacks_execute_on_failed_buffer_without_implicit_finish(self):
        storage = bytearray(); sink = EncodeBuffer(storage,0)
        with self.assertRaisesRegex(ModelError,'encoded blob exceeds byte limit'): sink.append(b'x')
        events, retained = [], []
        class Custom:
            def encode_into(self,value,out):
                retained.append(out); events.append('encode')
                if value: raise ModelError('different callback error')
            def encode_length(self,value,width,out): retained.append(out); events.append('length')
        # Unit and user callbacks need no writes and may return success even
        # though the retained sink error will still be reported by finish.
        vc.encode_raw(vc.UNIT,(),sink)
        vc.encode_raw(Custom(),False,sink)
        vc.encode_length_raw(Custom(),(),1,sink)
        with self.assertRaisesRegex(ModelError,'different callback error'):
            vc.encode_raw(Custom(),True,sink)
        self.assertEqual(events,['encode','length','encode'])
        for out in retained:
            with self.assertRaisesRegex(ModelError,'expired'): out.append(b'')
        with self.assertRaisesRegex(ModelError,'encoded blob exceeds byte limit'): sink.finish()
        self.assertEqual(bytes(storage),b'')
        with self.assertRaisesRegex(ModelError,'encoded blob exceeds byte limit'):
            vc.encode_into(Custom(),False,sink)
        self.assertEqual(events,['encode','length','encode'])

    def test_raw_swallowed_write_failure_reports_only_at_explicit_finish(self):
        retained = []
        class Custom:
            def encode_into(self,value,out):
                retained.append(out)
                try: out.append(b'ab')
                except ModelError: pass
        storage = bytearray(); sink = EncodeBuffer(storage,1)
        vc.encode_raw(Custom(),(),sink)
        self.assertEqual(bytes(storage),b'')
        with self.assertRaisesRegex(ModelError,'expired'): retained[0].finish()
        with self.assertRaisesRegex(ModelError,'encoded blob exceeds byte limit'): sink.finish()

class CompositeInputSnapshotTests(unittest.TestCase):
    def test_oracle_state_pins_history_before_model_encoder(self):
        state = OracleState(1, 2)
        class MutatingModel(vc.Schema):
            def encode_into(self, value, sink):
                object.__setattr__(state, 'oracle', 9)
                vc.U8.encode_into(value, sink)
        self.assertEqual(vc.encode_canonical(vc.OracleStateOf(MutatingModel(), vc.U8), state), b'\x01\x02')

    def test_mutating_element_encoder_cannot_change_later_sequence_slots(self):
        for kind in ('vector', 'array', 'tuple'):
            with self.subTest(kind=kind):
                values = [1, 2]
                class MutatingByte(vc.Schema):
                    def encode_into(self, value, sink):
                        if value == 1:
                            values[1] = 9
                            values.append(3)
                        vc.U8.encode_into(value, sink)
                element = MutatingByte()
                composite = (vc.VectorOf(element) if kind == 'vector' else
                             vc.ArrayOf(element, 2) if kind == 'array' else
                             vc.TupleOf(element, vc.U8))
                expected = b'\x02\x00\x00\x00\x01\x02' if kind == 'vector' else b'\x01\x02'
                self.assertEqual(vc.encode_canonical(composite, values), expected)


class AdmissionRegressionTests(unittest.TestCase):
    def test_raw_string_retains_length_prefix_before_rejected_body(self):
        for maximum in (4,5):
            with self.subTest(maximum=maximum):
                storage = bytearray(); sink = EncodeBuffer(storage,maximum)
                with self.assertRaisesRegex(ModelError,'encoded blob exceeds byte limit'):
                    vc.encode_raw(vc.STRING,'abc',sink)
                self.assertEqual(bytes(storage),b'\x03\x00\x00\x00')
                with self.assertRaisesRegex(ModelError,'encoded blob exceeds byte limit'): sink.finish()
        storage = bytearray(); sink = EncodeBuffer(storage,3)
        with self.assertRaisesRegex(ModelError,'encoded blob exceeds byte limit'):
            vc.encode_raw(vc.STRING,'abc',sink)
        self.assertEqual(bytes(storage),b'')

    def test_hostile_metaclass_cannot_spoof_closed_input_types_or_run_hooks(self):
        events = []
        class Spoof(type):
            def __eq__(cls,other): events.append('type equality'); return True
        class Hostile(metaclass=Spoof):
            def __iter__(self): events.append('iterate'); raise AssertionError('untrusted iteration')
            def __len__(self): events.append('length'); raise AssertionError('untrusted length')
            def __bytes__(self): events.append('bytes'); raise AssertionError('untrusted bytes')
            def __getitem__(self,key): events.append('index'); raise AssertionError('untrusted indexing')
        actual = Hostile()
        with self.assertRaises(ModelError): vc.Decoder(actual)
        for descriptor in (vc.VectorOf(vc.U8),vc.ArrayOf(vc.U8,1),vc.TupleOf(vc.U8)):
            with self.subTest(descriptor=descriptor), self.assertRaises(ModelError):
                vc.encode_canonical(descriptor,actual)
        self.assertEqual(events,[])

    def test_string_subclass_uses_intrinsic_payload_without_overridable_hooks(self):
        events = []
        class Text(str):
            def __iter__(self): events.append('iterate'); raise AssertionError('custom iteration')
            def __len__(self): events.append('length'); raise AssertionError('custom length')
            def __str__(self): events.append('str'); return 'wrong'
            def encode(self,*args,**kwargs): events.append('encode'); return b'wrong'
        text = Text('\ufeffé\x00')
        expected = b'\x06\x00\x00\x00\xef\xbb\xbf\xc3\xa9\x00'
        self.assertEqual(vc.encode_canonical(vc.STRING,text),expected)
        self.assertEqual(vc.decode_canonical(vc.STRING,expected),'\ufeffé\x00')
        self.assertEqual(events,[])
        with self.assertRaisesRegex(ModelError,'invalid UTF-8'):
            vc.encode_canonical(vc.STRING,Text('\ud800'))
        self.assertEqual(events,[])

    def test_python_float_rejected_but_exact_integer_above_javascript_safe_range_is_valid(self):
        # This is a Python-specific admission extension, distinct from the
        # corpus's inapplicable TypeScript Number representation policy row.
        with self.assertRaisesRegex(ModelError,'integer'):
            vc.encode_canonical(vc.U64,9007199254740992.0)
        exact = 9007199254740993
        encoded = vc.encode_canonical(vc.U64,exact)
        self.assertEqual(encoded,b'\x01\x00\x00\x00\x00\x00\x20\x00')
        self.assertEqual(vc.decode_canonical(vc.U64,encoded),exact)

class DirectionalCodecTests(unittest.TestCase):
    def test_encode_only_compositions_encode_without_layout_and_reject_decode_before_consumption(self):
        events = []
        class EncodeOnly:
            def encode_into(self,value,out): events.append('encode'); out.append(bytes((value,)))
        only = EncodeOnly()
        cases = [
            (vc.OptionOf(only),None,b'\x00'),
            (vc.OptionOf(only),vc.Some(7),b'\x01\x07'),
            (vc.VectorOf(only),[],b'\x00\x00\x00\x00'),
            (vc.VectorOf(only),[7],b'\x01\x00\x00\x00\x07'),
            (vc.ArrayOf(only,0),(),b''),
            (vc.ArrayOf(only,1),(7,),b'\x07'),
            (vc.TupleOf(vc.U8,only),(1,7),b'\x01\x07'),
            (vc.OracleStateOf(vc.U8,only),OracleState(1,7),b'\x01\x07'),
        ]
        for descriptor,actual,expected in cases:
            with self.subTest(schema=type(descriptor).__name__,value=actual):
                self.assertTrue(descriptor.supports_encode)
                self.assertFalse(descriptor.supports_decode)
                self.assertEqual(vc.encode_canonical(descriptor,actual),expected)
                before = events[:]
                reader = vc.Decoder(expected)
                with self.assertRaises(ModelError): descriptor.decode(reader)
                self.assertEqual(bytes(reader.take(len(expected))),expected)
                reader.finish()
                with self.assertRaises(ModelError): vc.decode_canonical(descriptor,expected)
                self.assertEqual(events,before)

    def test_decode_only_compositions_decode_and_reject_encode_before_any_prefix(self):
        events = []
        class DecodeOnly:
            allocation_layout = vc.Rust64Layout(1,alignment=1,profile='native-test-explicit')
            def decode(self,reader): events.append('decode'); return reader.value(vc.U8)
        only = DecodeOnly()
        cases = [
            (vc.OptionOf(only),None,b'\x00'),
            (vc.OptionOf(only),vc.Some(7),b'\x01\x07'),
            (vc.VectorOf(only),[],b'\x00\x00\x00\x00'),
            (vc.VectorOf(only),[7],b'\x01\x00\x00\x00\x07'),
            (vc.ArrayOf(only,0),(),b''),
            (vc.ArrayOf(only,1),(7,),b'\x07'),
            (vc.TupleOf(vc.U8,only),(1,7),b'\x01\x07'),
            (vc.OracleStateOf(vc.U8,only),OracleState(1,7),b'\x01\x07'),
        ]
        for descriptor,expected,data in cases:
            with self.subTest(schema=type(descriptor).__name__,value=expected):
                self.assertFalse(descriptor.supports_encode)
                self.assertTrue(descriptor.supports_decode)
                decoded = vc.decode_canonical(descriptor,data)
                self.assertEqual(decoded,expected)
                before = events[:]
                storage = bytearray(); sink = EncodeBuffer(storage,32); sink.append(b'p')
                with self.assertRaises(ModelError): vc.encode_raw(descriptor,expected,sink)
                sink.finish()
                self.assertEqual(bytes(storage),b'p')
                with self.assertRaises(ModelError): vc.encode_canonical(descriptor,expected)
                self.assertEqual(events,before)

    def test_inherited_schema_placeholders_do_not_grant_direction_support(self):
        class EncodeOnly(vc.Schema):
            def encode_into(self,value,out): vc.U8.encode_into(value,out)
        class DecodeOnly(vc.Schema):
            def decode(self,reader): return reader.value(vc.U8)
        for only in (EncodeOnly(),DecodeOnly(),vc.Schema()):
            descriptor = vc.OptionOf(only)
            self.assertEqual(descriptor.supports_encode,type(only) is EncodeOnly)
            self.assertEqual(descriptor.supports_decode,type(only) is DecodeOnly)
        self.assertEqual(vc.encode_canonical(EncodeOnly(),7),b'\x07')
        self.assertEqual(vc.decode_canonical(DecodeOnly(),b'\x07'),7)

    def test_model_uses_bidirectional_state_input_and_encode_only_output_through_preferred_hooks(self):
        from statelessness import (Check,Metadata,Transition,RunConfig,record_audit,
                                   replay_with_observations_bounded)
        events = []
        class OutputCodec:
            def encode_into(self,value,out): events.append('output encode'); vc.U8.encode_into(value,out)
        output = OutputCodec()
        class Model:
            def metadata(self): return Metadata('directional-codecs',build='native-test')
            def initial_state(self): return 0
            def step(self,state,input): return Transition(state+input,(state+input,))
            def check_state(self,state): return (Check('s'),)
            def encode_state_into(self,state,out): events.append('state encode'); vc.encode_into(vc.U8,state,out)
            def encode_input_into(self,input,out): events.append('input encode'); vc.encode_into(vc.U8,input,out)
            def encode_output_into(self,value,out): vc.encode_into(output,value,out)
            def decode_state(self,data): events.append('state decode'); return vc.decode_canonical(vc.U8,data)
            def decode_input(self,data): events.append('input decode'); return vc.decode_canonical(vc.U8,data)
        model = Model()
        trace = record_audit(model,[1,2],RunConfig())
        self.assertEqual(trace.termination,'completed')
        self.assertEqual(len(trace.steps),2)
        report = replay_with_observations_bounded(model,trace)
        self.assertEqual(report.outcome,'exact')
        self.assertEqual(events.count('output encode'),4)
        self.assertEqual(events.count('state decode'),1)
        self.assertEqual(events.count('input decode'),2)
        self.assertFalse(hasattr(output,'decode'))
        for name in ('encode_state','encode_input','encode_output','decode_output'):
            self.assertFalse(hasattr(model,name))

    def test_custom_length_trait_is_independent_of_normal_encoding(self):
        class LengthOnly(vc.Schema):
            def encode_length(self,value,width,out):
                vc.STRING.encode_length(value,width,out)
        only = LengthOnly()
        self.assertTrue(only.supports_encode_length)
        self.assertFalse(only.supports_encode)
        self.assertFalse(only.supports_decode)
        self.assertFalse(only.supports_decode_length)
        storage = bytearray(); sink = EncodeBuffer(storage,4)
        vc.encode_length_raw(only,'abc',1,sink); sink.finish()
        self.assertEqual(bytes(storage),b'\x03abc')
        with self.assertRaises(ModelError): vc.encode_canonical(only,'abc')
        class PlainLengthOnly:
            def encode_length(self,value,width,out): vc.STRING.encode_length(value,width,out)
        self.assertFalse(hasattr(PlainLengthOnly(),'encode_into'))
        storage = bytearray(); sink = EncodeBuffer(storage,4)
        vc.encode_length_raw(PlainLengthOnly(),'abc',1,sink); sink.finish()
        self.assertEqual(bytes(storage),b'\x03abc')

    def test_collection_allocation_weights_are_bound_when_composed(self):
        class MutableLayout:
            allocation_layout = vc.Rust64Layout(8,alignment=8,profile='native-test-explicit')
            def encode_into(self,value,out): vc.U8.encode_into(value,out)
            def decode(self,reader): return reader.value(vc.U8)
        element = MutableLayout()
        vector, array = vc.VectorOf(element), vc.ArrayOf(element,1)
        element.allocation_layout = vc.Rust64Layout(0,profile='later-rebinding')
        for descriptor,data,expected in ((vector,b'\x01\x00\x00\x00\x07',[7]),(array,b'\x07',(7,))):
            with self.subTest(schema=type(descriptor).__name__):
                with self.assertRaisesRegex(ModelError,'decode allocation limit'):
                    vc.decode_canonical(descriptor,data,limits=vc.DecodeLimits(allocation=7))
                self.assertEqual(vc.decode_canonical(descriptor,data,limits=vc.DecodeLimits(allocation=8)),expected)
        class InitiallyMissing:
            def encode_into(self,value,out): vc.U8.encode_into(value,out)
            def decode(self,reader): return reader.value(vc.U8)
        missing = InitiallyMissing()
        vector, array = vc.VectorOf(missing), vc.ArrayOf(missing,1)
        missing.allocation_layout = vc.Rust64Layout(1,profile='too-late')
        for descriptor,actual,data in ((vector,[7],b'\x01\x00\x00\x00\x07'),(array,(7,),b'\x07')):
            with self.subTest(missing_schema=type(descriptor).__name__):
                self.assertEqual(vc.encode_canonical(descriptor,actual),data)
                with self.assertRaisesRegex(ModelError,'allocation layout'): vc.decode_canonical(descriptor,data)

    def test_custom_length_width_has_u8_boundary_and_independent_decode_trait(self):
        events = []
        class LengthOnly:
            def encode_length(self,value,width,out): events.append(('encode',width)); out.append(b'x')
            def decode_length(self,width,reader): events.append(('decode',width)); return bytes(reader.take(1))
        only = LengthOnly()
        self.assertFalse(hasattr(only,'encode_into'))
        self.assertFalse(hasattr(only,'decode'))
        for width in (True,-1,256,1.5):
            with self.subTest(width=width):
                sink = EncodeBuffer(bytearray(),1)
                with self.assertRaises(ModelError): vc.encode_length_raw(only,(),width,sink)
                reader = vc.Decoder(b'x')
                with self.assertRaises(ModelError): vc.decode_length_raw(only,width,reader)
                self.assertEqual(bytes(reader.take(1)),b'x')
                reader.finish()
        self.assertEqual(events,[])
        storage = bytearray(); sink = EncodeBuffer(storage,1)
        vc.encode_length_raw(only,(),3,sink); sink.finish()
        self.assertEqual(bytes(storage),b'x')
        reader = vc.Decoder(b'xy',vc.DecodeLimits(work=0,depth=0))
        self.assertEqual(vc.decode_length_raw(only,3,reader),b'x')
        # Raw length decode adds no value guard and does not call finish.
        self.assertEqual(bytes(reader.take(1)),b'y'); reader.finish()
        self.assertEqual(events,[('encode',3),('decode',3)])
