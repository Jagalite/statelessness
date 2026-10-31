"""Explicit ordered codec recipes. Labels are diagnostics, never wire identity."""
from dataclasses import dataclass
from .core import ModelError, U64_MAX
from .codec import EncodeBuffer
from .value_codec import (Schema, Rust64Layout, DecodeLimits, U8, U16, U32,
    _available, _require_direction, _sync, encode_raw, encode_length_raw,
    decode_length_raw, decode_canonical)

DECLARATION_API_V1 = 1
_DIRECTIONS = ('encode', 'decode', 'encode_length', 'decode_length')
_LEGACY = object()


def _label(value):
    if type(value) is not str: raise ModelError('declaration label must be string')
    return value


def _callback(value):
    if value is not None and not callable(value): raise ModelError('callback must be callable')
    return value


class _Captured(Schema):
    __slots__ = ('callbacks', 'capabilities', 'allocation_layout')
    def __init__(self, source):
        self.capabilities = tuple(_available(source, d) for d in _DIRECTIONS)
        self.callbacks = tuple(getattr(source, m, None) if ok else None for m, ok in zip(
            ('encode_into', 'decode', 'encode_length', 'decode_length'), self.capabilities))
        layout = getattr(source, 'allocation_layout', None)
        self.allocation_layout = layout if type(layout) is Rust64Layout else None
        self._seal()
    supports_encode = property(lambda s: s.capabilities[0])
    supports_decode = property(lambda s: s.capabilities[1])
    supports_encode_length = property(lambda s: s.capabilities[2])
    supports_decode_length = property(lambda s: s.capabilities[3])
    def encode_into(self, value, sink): return self.callbacks[0](value, sink)
    def decode(self, reader): return self.callbacks[1](reader)
    def encode_length(self, value, width, sink): return self.callbacks[2](value, width, sink)
    def decode_length(self, width, reader): return self.callbacks[3](width, reader)


@dataclass(frozen=True, slots=True)
class Adapter:
    encode: object = None
    decode: object = None
    name: str = 'adapter'
    def __post_init__(self):
        _callback(self.encode); _callback(self.decode); _label(self.name)


def adapter(*, encode=None, decode=None, name='adapter'):
    return Adapter(encode, decode, name)


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    schema: object
    get: object
    length: object = None
    adapted: bool = False
    def __post_init__(self):
        _label(self.name); _callback(self.get)
        if type(self.adapted) is not bool: raise ModelError('adapted flag must be bool')
        if self.adapted:
            if type(self.schema) is not Adapter or self.length is not None: raise ModelError('invalid adapter field route')
        else:
            if self.length is not None and (type(self.length) is not int or self.length not in (1, 2, 4)):
                raise ModelError('length width must be 1, 2 or 4')
            if type(self.schema) is not _Captured: object.__setattr__(self, 'schema', _Captured(self.schema))
    def available(self, direction):
        if direction == 'encode' and self.get is None: return False
        if self.adapted: return getattr(self.schema, direction) is not None
        return _available(self.schema, direction + ('_length' if self.length is not None else ''))
    def write(self, value, sink):
        self.write_captured(_sync(self.get(value)), sink)
    def write_captured(self, value, sink):
        if self.adapted:
            child = EncodeBuffer._raw_child(sink)
            try:
                if _sync(self.schema.encode(value, child)) is not None: raise ModelError('encode callback must return None')
            finally: child._revoke()
        elif self.length is not None: encode_length_raw(self.schema, value, self.length, sink)
        else: encode_raw(self.schema, value, sink)
    def read(self, reader):
        if self.adapted: return _sync(self.schema.decode(reader))
        if self.length is not None: return decode_length_raw(self.schema, self.length, reader)
        return reader.value(self.schema)


def field(name, schema, *, get=None, length=None):
    _label(name); _callback(get)
    if length is not None and (type(length) is not int or length not in (1, 2, 4)):
        raise ModelError('length width must be 1, 2 or 4')
    return Field(name, _Captured(schema), get, length)


def positional_field(schema, *, get=None, length=None):
    return field('', schema, get=get, length=length)


def adapted_field(name, adapter, *, get=None):
    _label(name); _callback(get)
    if type(adapter) is not Adapter: raise ModelError('adapter required')
    return Field(name, adapter, get, adapted=True)


def _fields(fields, positional=False):
    fields = tuple(fields)
    if any(type(f) is not Field for f in fields): raise ModelError('field declaration required')
    names = [f.name for f in fields]
    if not positional and len(set(names)) != len(names): raise ModelError('duplicate field name')
    return fields


def _require(recipe, require):
    for direction in tuple(require):
        if direction not in _DIRECTIONS: raise ModelError('unknown required direction')
        _require_direction(recipe, direction)


class Record(Schema):
    __slots__ = ('name', 'value_type', 'fields', 'construct', 'allocation_layout', 'form')
    def __init__(self, name, value_type, fields, construct, layout, form, require):
        if form not in ('named', 'tuple', 'unit'): raise ModelError('unknown record form')
        self.name, self.value_type = _label(name), value_type
        if not isinstance(value_type, type): raise ModelError('value_type must be a type')
        self.fields = _fields(fields, form == 'tuple')
        self.construct = _callback(construct)
        if layout is not None and type(layout) is not Rust64Layout: raise ModelError('layout must be Rust64Layout')
        self.allocation_layout, self.form = layout, form
        self._directions = (all(f.available('encode') for f in self.fields),
                            construct is not None and all(f.available('decode') for f in self.fields))
        _require(self, require); self._seal()
    def encode_into(self, value, sink):
        _require_direction(self, 'encode')
        if type(value) is not self.value_type: raise ModelError('record value has wrong exact type')
        # Capture shallow field references before an encoder can mutate later
        # fields through an alias. Getters execute eagerly; their effects or
        # failures can precede an earlier encoder's effects or failure.
        values = tuple(_sync(f.get(value)) for f in self.fields)
        for f, field_value in zip(self.fields, values): f.write_captured(field_value, sink)
    def decode(self, reader):
        _require_direction(self, 'decode')
        values = tuple(f.read(reader) for f in self.fields)
        value = _sync(self.construct(values))
        if type(value) is not self.value_type: raise ModelError('constructor returned wrong exact type')
        return value
    def trace_schema(self): return self.name + ':' + self.form + '(' + ','.join(f.name for f in self.fields) + ')'


def ordered_record(name, *, value_type, fields, construct=None, layout=None, require=()):
    return Record(name, value_type, fields, construct, layout, 'named', require)


def tuple_record(name, *, value_type, fields, construct=None, layout=None, require=()):
    return Record(name, value_type, fields, construct, layout, 'tuple', require)


def unit_record(name, *, value_type, construct=None, layout=None, require=()):
    return Record(name, value_type, (), construct, layout, 'unit', require)


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    tag: int
    fields: tuple
    construct: object
    def __post_init__(self):
        _label(self.name); _callback(self.construct)
        if type(self.tag) is not int or self.tag < 0: raise ModelError('enum tag must be an exact unsigned integer')
        fields = tuple(self.fields)
        positional = all(type(f) is Field and f.name == '' for f in fields)
        object.__setattr__(self, 'fields', _fields(fields, positional))


def case(name, *, tag, fields=(), construct=None):
    if type(tag) is not int or tag < 0: raise ModelError('enum tag must be an exact unsigned integer')
    return Case(_label(name), tag, tuple(fields), _callback(construct))


class TaggedEnum(Schema):
    __slots__ = ('name', 'tag_type', 'select', 'cases', 'allocation_layout')
    def __init__(self, name, tag_type, select, cases, layout, require):
        self.name, self.select = _label(name), _callback(select)
        if not any(tag_type is t for t in (U8, U16, U32)): raise ModelError('enum tag type must be builtin U8/U16/U32')
        self.tag_type, self.cases = tag_type, tuple(cases)
        if any(type(c) is not Case for c in self.cases): raise ModelError('case declaration required')
        if len({c.name for c in self.cases}) != len(self.cases) or len({c.tag for c in self.cases}) != len(self.cases):
            raise ModelError('duplicate enum label or tag')
        if any(c.tag >= 1 << tag_type.bits for c in self.cases): raise ModelError('enum tag overflow')
        if layout is not None and type(layout) is not Rust64Layout: raise ModelError('layout must be Rust64Layout')
        self.allocation_layout = layout
        self._directions = (select is not None and all(f.available('encode') for c in self.cases for f in c.fields),
            all(c.construct is not None and all(f.available('decode') for f in c.fields) for c in self.cases))
        _require(self, require); self._seal()
    def trace_schema(self): return self.name + ':enum(' + ','.join(c.name for c in self.cases) + ')'
    def trace_variant(self, value):
        if self.select is None: raise ModelError('enum selector unavailable')
        label = _sync(self.select(value))
        if type(label) is not str or not any(c.name == label for c in self.cases): raise ModelError('unknown enum label')
        return label
    def encode_into(self, value, sink):
        _require_direction(self, 'encode')
        label = self.trace_variant(value)
        selected = next(c for c in self.cases if c.name == label)
        encode_raw(self.tag_type, selected.tag, sink)
        # Raw callers may retain the emitted tag if a getter fails.
        values = tuple(_sync(f.get(value)) for f in selected.fields)
        for f, field_value in zip(selected.fields, values): f.write_captured(field_value, sink)
    def decode(self, reader):
        _require_direction(self, 'decode')
        tag = reader.value(self.tag_type)
        selected = next((c for c in self.cases if c.tag == tag), None)
        if selected is None: raise ModelError('unknown enum tag')
        values = tuple(f.read(reader) for f in selected.fields)
        return _sync(selected.construct(values))


def tagged_enum(name, *, tag_type=U8, select=None, cases, layout=None, require=()):
    return TaggedEnum(name, tag_type, select, cases, layout, require)


@dataclass(frozen=True, slots=True)
class ModelCodec:
    state: object
    input: object
    output: object
    version: int = 1
    decode_limits: object = None
    def __post_init__(self):
        if type(self.version) is not int or not 1 <= self.version <= 0xffffffff: raise ModelError('codec version must be positive u32')
        for name in ('state', 'input', 'output'):
            captured = _Captured(getattr(self, name))
            _require_direction(captured, 'encode')
            if name != 'output': _require_direction(captured, 'decode')
            object.__setattr__(self, name, captured)
        if self.decode_limits is not None and type(self.decode_limits) is not DecodeLimits and not callable(self.decode_limits):
            raise ModelError('decode limits must be DecodeLimits or provider')
    def bind(self, model): return _BoundModel(self, model)


def model_codec(*, state, input, output, version=1, decode_limits=None):
    return ModelCodec(state, input, output, version, decode_limits)


class _BoundModel:
    __slots__ = ('_binding', '_model')
    def __init__(self, binding, model): self._binding, self._model = binding, model
    def __getattr__(self, name): return getattr(self._model, name)
    def _gate(self):
        if self._model.metadata().codec_version != self._binding.version:
            raise ModelError('ModelCodec derive version does not match Model metadata')
    def _encode(self, kind, value, sink=_LEGACY):
        self._gate()
        schema = getattr(self._binding, kind)
        if sink is not _LEGACY: return encode_raw(schema, value, sink)
        data = bytearray(); local = EncodeBuffer(data, U64_MAX)
        encode_raw(schema, value, local); local.finish(); return bytes(data)
    def _decode(self, kind, data):
        self._gate()
        provider = self._binding.decode_limits
        try:
            limits = _sync(provider(self._model)) if callable(provider) else provider
            if provider is None: limits = DecodeLimits()
            if type(limits) is not DecodeLimits: raise ModelError('decode limits provider must return DecodeLimits')
            limits = DecodeLimits(**{n: getattr(limits, n) for n in ('bytes', 'allocation', 'elements', 'depth', 'work')})
        except Exception as error: raise ModelError('decode limits: ' + str(error)) from error
        return decode_canonical(getattr(self._binding, kind), data, limits)
    def encode_state(self, value): return self._encode('state', value)
    def encode_input(self, value): return self._encode('input', value)
    def encode_output(self, value): return self._encode('output', value)
    def encode_state_into(self, value, sink): return self._encode('state', value, sink)
    def encode_input_into(self, value, sink): return self._encode('input', value, sink)
    def encode_output_into(self, value, sink): return self._encode('output', value, sink)
    def decode_state(self, data): return self._decode('state', data)
    def decode_input(self, data): return self._decode('input', data)
