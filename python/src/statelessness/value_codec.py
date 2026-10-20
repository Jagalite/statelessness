"""Candidate canonical value codecs with explicit logical Rust64 allocation weights."""
from dataclasses import dataclass
import inspect
from .core import ModelError, U64_MAX
from .codec import EncodeBuffer
from .oracle import OracleState


def _u64(value, name):
    if type(value) is not int or not 0 <= value <= U64_MAX:
        raise ModelError(name + ' must be an unsigned u64 integer')
    return value

@dataclass(frozen=True)
class DecodeLimits:
    bytes: int = 16 * 1024 * 1024
    allocation: int = 32 * 1024 * 1024
    elements: int = 1_000_000
    depth: int = 64
    work: int = 2_000_000
    def __post_init__(self):
        for name in ('bytes','allocation','elements','depth','work'): _u64(getattr(self,name),name)

@dataclass(frozen=True)
class Rust64Layout:
    """Explicit logical allocation request; optional alignment is descriptive only."""
    size: int
    alignment: int | None = None
    profile: str = 'explicit-cooperative-weight'
    def __post_init__(self):
        _u64(self.size,'layout size')
        if self.alignment is not None:
            _u64(self.alignment,'layout alignment')
            if not self.alignment or self.alignment & (self.alignment-1): raise ModelError('layout alignment must be a power of two')
        if type(self.profile) is not str: raise ModelError('layout profile must be string')


def _sync(value):
    if inspect.isawaitable(value) or inspect.isgenerator(value) or inspect.isasyncgen(value):
        close = getattr(value,'close',None)
        if close is not None: close()
        raise ModelError('codec callbacks must be synchronous')
    return value

class Decoder:
    def __init__(self, data, limits=DecodeLimits()):
        if type(limits) is not DecodeLimits: raise ModelError('limits must be DecodeLimits')
        selected = DecodeLimits(**{n:object.__getattribute__(limits,n) for n in ('bytes','allocation','elements','depth','work')})
        if type(data) is not bytes and type(data) is not bytearray and type(data) is not memoryview: raise ModelError('decoder input must be bytes or contiguous byte view')
        view = memoryview(data)
        if not view.c_contiguous: raise ModelError('decoder input must be contiguous')
        if view.nbytes > selected.bytes: raise ModelError('decode byte limit')
        self._data = data if type(data) is bytes else view.tobytes()
        self._position = 0
        self._allocation, self._elements, self._work = selected.allocation,selected.elements,selected.work
        self._depth, self._depth_limit, self._failure = 0,selected.depth,None
    def _check(self):
        if self._failure is not None: raise ModelError(self._failure)
    def _reject(self,message):
        if self._failure is None: self._failure = message
        raise ModelError(self._failure)
    def take(self,count):
        self._check(); _u64(count,'byte count')
        if count > len(self._data)-self._position: self._reject('truncated value')
        start=self._position; self._position += count
        return memoryview(self._data)[start:self._position]
    def collection(self,count,element_size):
        self._check(); _u64(count,'element count'); _u64(element_size,'element size')
        if count > self._elements: self._reject('decode element limit')
        self._elements -= count
        amount=count*element_size
        if amount > U64_MAX: self._reject('decode allocation overflow')
        if amount > self._allocation: self._reject('decode allocation limit')
        self._allocation -= amount
        if count > self._work: self._reject('decode work limit')
    def value(self,schema):
        self._check()
        if not callable(schema): _require_direction(schema,'decode')
        if not self._work: self._reject('decode work limit')
        self._work -= 1
        if self._depth >= self._depth_limit: self._reject('decode nesting limit')
        self._depth += 1
        try:
            callback = schema if callable(schema) else schema.decode
            result = _sync(callback(self))
            self._check()
            return result
        except BaseException as error:
            if self._failure is None:
                self._failure = 'decoder callback failed'
                try: self._failure = 'decode allocation failed' if isinstance(error,MemoryError) else str(error)
                except BaseException: pass
            if isinstance(error,Exception): raise ModelError(self._failure) from error
            raise
        finally: self._depth -= 1
    def finish(self):
        self._check()
        if self._position != len(self._data): raise ModelError('trailing value bytes')

class Schema:
    __slots__ = ('_sealed','_directions')
    allocation_layout = None
    def __setattr__(self,name,value):
        try: sealed=object.__getattribute__(self,'_sealed')
        except AttributeError: sealed=False
        if sealed: raise AttributeError('builtin codec schemas are immutable')
        object.__setattr__(self,name,value)
    def _seal(self): object.__setattr__(self,'_sealed',True)
    def _compose_directions(self,*children):
        self._directions = (all(_available(child,'encode') for child in children),
                            all(_available(child,'decode') for child in children))
    @property
    def supports_encode(self):
        try: return object.__getattribute__(self,'_directions')[0]
        except AttributeError: return type(self).encode_into is not Schema.encode_into
    @property
    def supports_decode(self):
        try: return object.__getattribute__(self,'_directions')[1]
        except AttributeError: return type(self).decode is not Schema.decode
    @property
    def supports_encode_length(self):
        if type(self).encode_length is Schema.encode_length: return False
        try: return object.__getattribute__(self,'_directions')[0]
        except AttributeError: return True
    @property
    def supports_decode_length(self):
        if type(self).decode_length is Schema.decode_length: return False
        try: return object.__getattribute__(self,'_directions')[1]
        except AttributeError: return True
    def encode_into(self,value,sink): raise ModelError('encoding is not implemented')
    def decode(self,reader): raise ModelError('decoding is not implemented')
    def encode_length(self,value,width,sink): raise ModelError('length override is unsupported for this schema')
    def decode_length(self,width,reader): raise ModelError('length override is unsupported for this schema')
    def trace_schema(self): return self.__class__.__name__
    def trace_variant(self,value=None): return None


def _available(schema,direction):
    declared=getattr(schema,'supports_'+direction,None)
    if declared is not None:
        if type(declared) is not bool: raise ModelError('schema direction metadata must be bool')
        if not declared: return False
    method=getattr(schema,{'encode':'encode_into','decode':'decode','encode_length':'encode_length','decode_length':'decode_length'}[direction],None)
    return callable(method)

def _require_direction(schema,direction):
    if not _available(schema,direction):
        raise ModelError('schema does not support '+('encoding' if direction.startswith('encode') else 'decoding'))


def encode_into(schema,value,sink):
    _require_direction(schema,'encode')
    child=EncodeBuffer._child(sink)
    try:
        result=_sync(schema.encode_into(value,child))
        if result is not None: raise ModelError('encode callback must return None')
        child.finish()
    except BaseException as error:
        child._fail(error)
        raise
    finally: child._revoke()

def encode_raw(schema,value,sink):
    """Raw trait call: returned errors alone do not poison accepted prefix bytes."""
    _require_direction(schema,'encode')
    child=EncodeBuffer._raw_child(sink)
    try:
        result=_sync(schema.encode_into(value,child))
        if result is not None: raise ModelError('encode callback must return None')
    finally: child._revoke()

def _length_width(width):
    if type(width) is not int or not 0 <= width <= 255:
        raise ModelError('length width must be an unsigned byte integer')
    return width

def encode_length_raw(schema,value,width,sink):
    """Direct raw length override with an independently revoked lease."""
    _length_width(width)
    _require_direction(schema,'encode_length')
    child=EncodeBuffer._raw_child(sink)
    try:
        result=_sync(schema.encode_length(value,width,child))
        if result is not None: raise ModelError('encode callback must return None')
    finally: child._revoke()

def decode_length_raw(schema,width,reader):
    """Direct LengthDecode call, without an outer value guard or finish."""
    _length_width(width)
    _require_direction(schema,'decode_length')
    return _sync(schema.decode_length(width,reader))

def encode_canonical(schema,value,maximum=U64_MAX):
    _u64(maximum,'maximum'); data=bytearray(); sink=EncodeBuffer(data,maximum)
    encode_into(schema,value,sink); sink.finish()
    return bytes(data)

def decode_canonical(schema,data,limits=DecodeLimits()):
    _require_direction(schema,'decode')
    reader=Decoder(data,limits); result=reader.value(schema); reader.finish(); return result

class _Integer(Schema):
    __slots__ = ('bits','signed','allocation_layout')
    def __init__(self,bits,signed=False):
        self.bits,self.signed=bits,signed
        self.allocation_layout=Rust64Layout(bits//8,profile='scalar-width'); self._seal()
    def encode_into(self,value,sink):
        if type(value) is not int: raise ModelError('integer value required')
        lower=-(1 << (self.bits-1)) if self.signed else 0
        upper=(1 << (self.bits-int(self.signed)))-1
        if not lower <= value <= upper: raise ModelError('integer out of range')
        sink.append(value.to_bytes(self.bits//8,'little',signed=self.signed))
    def decode(self,reader): return int.from_bytes(reader.take(self.bits//8),'little',signed=self.signed)
    def trace_schema(self): return ('i' if self.signed else 'u')+str(self.bits)

for _bits in (8,16,32,64,128):
    globals()['U'+str(_bits)]=_Integer(_bits)
    globals()['I'+str(_bits)]=_Integer(_bits,True)

class _Bool(Schema):
    __slots__ = ()
    allocation_layout=Rust64Layout(1,1,'scalar-width')
    def encode_into(self,value,sink):
        if type(value) is not bool: raise ModelError('bool value required')
        sink.append(b'\1' if value else b'\0')
    def decode(self,reader):
        tag=reader.value(U8)
        if tag > 1: raise ModelError('invalid boolean')
        return bool(tag)
    def trace_schema(self): return 'bool'
class _Unit(Schema):
    __slots__ = ()
    allocation_layout=Rust64Layout(0,1,'scalar-width')
    def encode_into(self,value,sink):
        if type(value) is not tuple or value: raise ModelError('unit value must be ()')
    def decode(self,reader): return ()
    def trace_schema(self): return '()'
BOOL,UNIT=_Bool(),_Unit()
BOOL._seal(); UNIT._seal()


def _length_schema(width):
    if type(width) is not int or width not in (1,2,4): raise ModelError('unsupported length width')
    return {1:U8,2:U16,4:U32}[width]
def _put_length(count,width,sink):
    descriptor=_length_schema(width)
    if count >= 1 << (8*width): raise ModelError('length overflow')
    descriptor.encode_into(count,sink)
def _capture_weight(element):
    # Custom descriptors are cooperative, but later rebinding must not change
    # an already constructed collection's logical allocation admission.
    try: layout=getattr(element,'allocation_layout',None)
    except Exception: return None
    return layout.size if type(layout) is Rust64Layout else None

def _weight(collection):
    weight=collection._element_weight
    if weight is None: raise ModelError('element allocation layout is required')
    return weight


def _utf8_size(value):
    if not issubclass(type(value),str): raise ModelError('UTF-8 string required')
    total=0
    for character in str.__iter__(value):
        code=ord(character)
        if 0xD800 <= code <= 0xDFFF: raise ModelError('invalid UTF-8')
        total += 1 if code<128 else 2 if code<2048 else 3 if code<65536 else 4
    return total

class _String(Schema):
    __slots__ = ()
    allocation_layout=Rust64Layout(24,8,'rust64-string-header')
    def encode_into(self,value,sink): self.encode_length(value,4,sink)
    def encode_length(self,value,width,sink):
        _length_schema(width)
        count=_utf8_size(value)
        if count >= 1 << (8*width): raise ModelError('length overflow')
        _put_length(count,width,sink)
        root=sink._require()
        if count > root.maximum-len(root.data):
            raise sink._fail(ModelError('encoded blob exceeds byte limit'))
        sink.append(str.encode(value,'utf-8','strict'))
    def decode(self,reader): return self.decode_length(4,reader)
    def decode_length(self,width,reader):
        count=reader.value(_length_schema(width)); reader.collection(count,1); reader._work -= count
        data=reader.take(count)
        try: return bytes(data).decode('utf-8','strict')
        except UnicodeDecodeError: raise ModelError('invalid UTF-8') from None
        except MemoryError: raise ModelError('decode allocation failed') from None
    def trace_schema(self): return 'String'
STRING=_String(); STRING._seal()

@dataclass(frozen=True)
class Some:
    value: object

class OptionOf(Schema):
    __slots__ = ('element','allocation_layout')
    def __init__(self,element,*,layout=None): self.element,self.allocation_layout=element,layout; self._compose_directions(element); self._seal()
    def encode_into(self,value,sink):
        _require_direction(self,'encode')
        if value is None: sink.append(b'\0')
        elif type(value) is Some:
            sink.append(b'\1'); encode_raw(self.element,value.value,sink)
        else: raise ModelError('option value must be None or Some')
    def decode(self,reader):
        _require_direction(self,'decode')
        tag=reader.value(U8)
        if tag==0:return None
        if tag==1:return Some(reader.value(self.element))
        raise ModelError('invalid option tag')
    def trace_schema(self): return 'Option<'+self.element.trace_schema()+'>'

class VectorOf(Schema):
    __slots__ = ('element','allocation_layout','_element_weight')
    def __init__(self,element,*,layout=None): self.element,self.allocation_layout=element,layout or Rust64Layout(24,8,'rust64-vector-header'); self._compose_directions(element); self._element_weight=_capture_weight(element); self._seal()
    def encode_into(self,value,sink): self.encode_length(value,4,sink)
    def encode_length(self,value,width,sink):
        _require_direction(self,'encode')
        _length_schema(width)
        if type(value) is not tuple and type(value) is not list: raise ModelError('vector value must be list or tuple')
        _put_length(len(value),width,sink)
        for item in value: encode_raw(self.element,item,sink)
    def decode(self,reader): return self.decode_length(4,reader)
    def decode_length(self,width,reader):
        _require_direction(self,'decode')
        count=reader.value(_length_schema(width)); reader.collection(count,_weight(self))
        try: return [reader.value(self.element) for _ in range(count)]
        except MemoryError: raise ModelError('decode allocation failed') from None
    def trace_schema(self): return 'Vec<'+self.element.trace_schema()+'>'

class ArrayOf(Schema):
    __slots__ = ('element','length','allocation_layout','_element_weight')
    def __init__(self,element,length,*,layout=None): self.element,self.length,self.allocation_layout=element,_u64(length,'array length'),layout; self._compose_directions(element); self._element_weight=_capture_weight(element); self._seal()
    def encode_into(self,value,sink):
        _require_direction(self,'encode')
        if (type(value) is not tuple and type(value) is not list) or len(value)!=self.length: raise ModelError('array length mismatch')
        for item in value: encode_raw(self.element,item,sink)
    def decode(self,reader):
        _require_direction(self,'decode')
        reader.collection(self.length,_weight(self))
        try: return tuple(reader.value(self.element) for _ in range(self.length))
        except MemoryError: raise ModelError('decode allocation failed') from None
    def trace_schema(self): return '['+self.element.trace_schema()+'; '+str(self.length)+']'

class TupleOf(Schema):
    __slots__ = ('fields','allocation_layout')
    def __init__(self,*fields,layout=None):
        if not 1 <= len(fields) <= 4: raise ModelError('tuple arity must be 1 through 4')
        self.fields,self.allocation_layout=fields,layout; self._compose_directions(*fields); self._seal()
    def encode_into(self,value,sink):
        _require_direction(self,'encode')
        if (type(value) is not tuple and type(value) is not list) or len(value)!=len(self.fields): raise ModelError('tuple arity mismatch')
        for field,item in zip(self.fields,value): encode_raw(field,item,sink)
    def decode(self,reader):
        _require_direction(self,'decode')
        return tuple(reader.value(field) for field in self.fields)
    def trace_schema(self): return '('+', '.join(field.trace_schema() for field in self.fields)+')'

class OracleStateOf(Schema):
    __slots__ = ('model','history','allocation_layout')
    def __init__(self,model,history,*,layout=None): self.model,self.history,self.allocation_layout=model,history,layout; self._compose_directions(model,history); self._seal()
    def encode_into(self,value,sink):
        _require_direction(self,'encode')
        if type(value) is not OracleState: raise ModelError('OracleState value required')
        encode_raw(self.model,value.model,sink); encode_raw(self.history,value.oracle,sink)
    def decode(self,reader):
        _require_direction(self,'decode')
        return OracleState(reader.value(self.model),reader.value(self.history))
    def trace_schema(self): return 'OracleState<'+self.model.trace_schema()+', '+self.history.trace_schema()+'>'
