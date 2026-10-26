"""Optional bounded local stdio adapter. No transport is loaded by the engine.

Callbacks are cooperative. Framing/storage bounds are not process memory bounds.
Unsupported host adapters are explicit; no dynamic model loading or code evaluation.
"""
from dataclasses import dataclass
from collections import OrderedDict
from itertools import count
import re

_U64 = (1 << 64) - 1
_epochs = count(1)
_SAFE = frozenset(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.:')

class ProtocolError(ValueError):
    def __init__(self, code): self.code = code; super().__init__(code)

@dataclass(frozen=True)
class ProtocolLimits:
    max_frame_bytes: int = 65536
    max_fields: int = 64
    max_response_fields: int = 4096
    max_page_size: int = 100
    max_field_bytes: int = 8192
    max_input_bytes: int = 8192
    max_cache_entries: int = 64
    max_cache_bytes: int = 4 * 1024 * 1024
    max_handles: int = 64
    max_candidates: int = 256
    max_candidate_bytes: int = 256 * 1024
    max_telemetry_snapshot_bytes: int = 8 * 1024 * 1024
    max_event_entries: int = 1024
    max_event_bytes: int = 256 * 1024
    def __post_init__(self):
        for name in self.__dataclass_fields__:
            if type(getattr(self, name)) is not int or getattr(self, name) < 1: raise ValueError(name)
        if not 2048 <= self.max_frame_bytes <= (1 << 32) - 1 or self.max_field_bytes < 64 or self.max_fields < 9 or self.max_response_fields < 11: raise ValueError('protocol minimum budgets')

@dataclass(frozen=True)
class ProtocolAuthority:
    deliver_inputs: bool = False
    configure_watches: bool = False
    configure_diagnostics: bool = False
    read_exact_values: bool = False
    export_exact_trace: bool = False
    def __post_init__(self):
        if any(type(getattr(self,n)) is not bool for n in self.__dataclass_fields__): raise ValueError('authority must be boolean')

def _u64(value):
    if type(value) is not int or not 0 <= value <= _U64: raise ProtocolError('malformed')
    return value

def _number(field):
    if not re.fullmatch(r'u64:[0-9]+', field): raise ProtocolError('malformed')
    # Leading zeroes are valid Rust u64 syntax, even beyond Python's integer
    # string conversion limit. Bound the significant digits before conversion.
    decimal=field[4:].lstrip('0') or '0'
    if len(decimal)>20: raise ProtocolError('malformed')
    try: return _u64(int(decimal))
    except ValueError: raise ProtocolError('malformed') from None

def escape(value):
    return ''.join(chr(b) if b in _SAFE else '%%%02X' % b for b in value.encode('utf-8'))

def _unescape(value, maximum):
    if len(value)>maximum*3: raise ProtocolError('limit')
    output = bytearray(); index = 0
    while index < len(value):
        b = value[index]
        if b == 37:
            if index + 2 >= len(value): raise ProtocolError('malformed')
            pair = value[index+1:index+3]
            if not re.fullmatch(b'[0-9a-fA-F]{2}', pair): raise ProtocolError('malformed')
            output.append(int(pair,16)); index += 3
        else:
            if b not in _SAFE: raise ProtocolError('malformed')
            output.append(b); index += 1
        if len(output) > maximum: raise ProtocolError('limit')
    try: return output.decode('utf-8', errors='strict')
    except UnicodeError: raise ProtocolError('malformed') from None

def _encode(fields, limits):
    if any(len(f.encode('utf-8')) > limits.max_field_bytes for f in fields): raise ProtocolError('limit')
    result = '\t'.join(map(escape, fields)).encode('ascii')
    if not result or len(result) > limits.max_frame_bytes: raise ProtocolError('limit')
    return result

def _decode_fields(payload, limits, maximum):
    if len(payload)>limits.max_frame_bytes: raise ProtocolError('limit')
    # Rust validates raw UTF-8 before any per-field length/syntax checks, then
    # visits each field in order. Preserve which error wins on mixed faults.
    try: payload.decode('utf-8',errors='strict')
    except UnicodeError: raise ProtocolError('malformed') from None
    fields=[]
    for raw in payload.split(b'\t'):
        if len(fields)==maximum: raise ProtocolError('limit')
        fields.append(_unescape(raw,limits.max_field_bytes))
    return fields

@dataclass(frozen=True)
class Request:
    session: str
    epoch: int
    request_id: int
    expected_revision: int
    expected_configuration: int
    command: str
    arguments: tuple[str, ...] = ()
    version: int = 1


def encode_request(request, limits=ProtocolLimits()):
    if type(request.version) is not int or not 0<=request.version<(1<<32) or type(request.session) is not str or type(request.command) is not str or any(type(a) is not str for a in request.arguments): raise ProtocolError('malformed')
    for n in (request.epoch,request.request_id,request.expected_revision,request.expected_configuration): _u64(n)
    fields = ('DDBG', 'u32:'+str(request.version), 'request', 'string:'+request.session,
        *(f'u64:{n}' for n in (request.epoch,request.request_id,request.expected_revision,request.expected_configuration)),request.command,*request.arguments)
    if len(fields) > limits.max_fields: raise ProtocolError('limit')
    return _encode(fields, limits)


def decode_request(payload, limits=ProtocolLimits()):
    f = _decode_fields(payload,limits,limits.max_fields)
    if len(f)<9 or f[0]!='DDBG' or f[2]!='request': raise ProtocolError('malformed')
    if not re.fullmatch(r'u32:\+?[0-9]+',f[1]): raise ProtocolError('malformed')
    decimal=f[1][4:].lstrip('+').lstrip('0') or '0'
    if len(decimal)>10 or int(decimal)>(1<<32)-1: raise ProtocolError('malformed')
    version=int(decimal)
    if version!=1: raise ProtocolError('unsupported_version')
    arguments=_decode_arguments(f[8],tuple(f[9:]),limits)
    if not f[3].startswith('string:'): raise ProtocolError('malformed')
    return Request(f[3][7:],*map(_number,f[4:8]),f[8],arguments,version)

@dataclass(frozen=True)
class Response:
    session: str
    epoch: int
    request_id: int
    revision: int
    configuration: int
    error: str | None
    fields: tuple[tuple[str,str], ...] = ()

def encode_response(response, limits=ProtocolLimits()):
    if len(response.fields)*2+11 > limits.max_response_fields: raise ProtocolError('limit')
    return _encode(('DDBG','u32:1','response','string:'+response.session,
        *(f'u64:{_u64(n)}' for n in (response.epoch,response.request_id,response.revision,response.configuration)),
        'error' if response.error else 'ok',response.error or 'none',f'u64:{len(response.fields)}',
        *(v for pair in response.fields for v in pair)), limits)

def decode_response(payload, limits=ProtocolLimits()):
    f=_decode_fields(payload,limits,limits.max_response_fields)
    if len(f)<11 or f[:3]!=['DDBG','u32:1','response'] or not f[3].startswith('string:') or f[8] not in ('ok','error'): raise ProtocolError('malformed')
    n=_number(f[10])
    if len(f)!=11+2*n or (f[8]=='ok') != (f[9]=='none'): raise ProtocolError('malformed')
    return Response(f[3][7:],*map(_number,f[4:8]),None if f[9]=='none' else f[9],tuple(zip(f[11::2],f[12::2])))

def _read_exact(reader, size):
    parts = bytearray()
    while len(parts)<size:
        chunk=reader.read(size-len(parts))
        if not chunk: raise EOFError('partial protocol frame')
        parts.extend(chunk)
    return bytes(parts)

def read_frame(reader, maximum=65536):
    first=reader.read(1)
    if not first: return None
    size=int.from_bytes(first+_read_exact(reader,3),'big')
    if not 0<size<=maximum: raise ProtocolError('limit')
    return _read_exact(reader,size)

def write_frame(writer,payload,maximum=65536):
    if not 0<len(payload)<=min(maximum,(1<<32)-1): raise ProtocolError('limit')
    data=len(payload).to_bytes(4,'big')+payload
    view=memoryview(data)
    while view:
        n=writer.write(view)
        if n is None or n<=0: raise OSError('partial protocol write')
        view=view[n:]
    writer.flush()

@dataclass(frozen=True)
class ProtocolPrepared:
    """Staged host transaction. Commit runs once only after response preflight.

    A host must not mutate while preparing. A failed commit may have changed host
    state; callbacks remain cooperative and reconciliation uses actual revisions.
    """
    fields: object
    commit: object = None
    def __post_init__(self):
        if self.commit is not None and not callable(self.commit): raise ValueError('commit callback')

# Host adapters receive validated typed arguments, never executable expressions.
# A callback owns its typed library adapter and any configuration transaction.
_ADAPTER_COMMANDS = {
 'inspect': ('nnnn*',None), 'checks': ('n',None), 'outputs': ('n',None),
 'export_trace': ('nnn','export_exact_trace'), 'inputs': ('nn',None),
 'select': ('n','deliver_inputs'), 'events': ('n',None),
 'watch': ('nn*','configure_watches'), 'unwatch': ('n','configure_watches'),
 'watch_status': ('',None), 'watch_current': ('n',None),
 'metric_catalog': ('nn',None), 'metric_snapshot': ('hnn',None),
 'metric_window': ('nnn',None), 'effect_details': ('nnn',None),
 'effect_timeline': ('nnnn',None), 'telemetry_health': ('',None),
 'probe_status': ('nn',None), 'probe_configure': ('n+','configure_diagnostics'),
 'probe_events': ('nnb',None),
}
_HANDLE_COMMANDS = frozenset(('inspect','checks','outputs','export_trace'))
_FRESH_READS = frozenset(('watch_status','watch_current','metric_catalog','metric_snapshot','metric_window','effect_details','effect_timeline','telemetry_health','probe_status','probe_events'))

def _path_integer(text, bits, signed=False):
    # Match Rust integer FromStr, including leading plus/zeroes, but never
    # pass an unbounded decimal to Python's int conversion. Native-size path
    # tags follow the qualified Rust64 protocol profile.
    if not re.fullmatch(r'[+-]?[0-9]+' if signed else r'\+?[0-9]+', text):
        raise ProtocolError('malformed')
    negative = text.startswith('-')
    digits = text.lstrip('+-').lstrip('0') or '0'
    if len(digits) > 39: raise ProtocolError('malformed')
    number = int(digits) * (-1 if negative else 1)
    if not (-(1 << (bits-1)) if signed else 0) <= number < (1 << (bits-1 if signed else bits)):
        raise ProtocolError('malformed')
    return number


def _path(value):
    kind,sep,text=value.partition(':')
    if not sep: raise ProtocolError('malformed')
    if kind in ('field','variant','key.string'): return (kind,text)
    if kind=='key.bool' and text in ('true','false'): return (kind,text=='true')
    if kind=='key.char' and len(text)==1: return (kind,text)
    if kind=='index': return (kind,_path_integer(text,64))
    match=re.fullmatch(r'key\.([ui])(8|16|32|64|128|size)',kind)
    if match:
        return (kind,_path_integer(text,64 if match[2]=='size' else int(match[2]),match[1]=='i'))
    raise ProtocolError('malformed')


def _canonical_path(value):
    kind,parsed=_path(value)
    return kind+':'+str(parsed) if type(parsed) is int else value

def _parse_adapter(command,arguments,limits,*,check_limits=True):
    spec=_ADAPTER_COMMANDS[command][0]; rest=spec[-1:] in ('*','+'); base=spec[:-1] if rest else spec
    if len(arguments)<len(base) or (not rest and len(arguments)!=len(base)): raise ProtocolError('malformed')
    values=[]
    for tag,value in zip(base,arguments):
        if tag=='n': values.append(_number(value))
        elif tag=='h': values.append(None if value=='none' else _number(value))
        elif tag=='b' and value in ('bool:true','bool:false'): values.append(value=='bool:true')
        else: raise ProtocolError('malformed')
    if rest: values.append(tuple((_path(v) if spec[-1]=='*' else _number(v)) for v in arguments[len(base):]))
    if command in ('inspect','watch') and values[1 if command=='inspect' else 0]>(1<<32)-1: raise ProtocolError('malformed')
    if command=='watch' and values[1]>_U64: raise ProtocolError('malformed')
    if spec.endswith('*') and len(values[-1])>32: raise ProtocolError('limit')
    if check_limits:
        pages={'inputs':(1,), 'events':(0,), 'inspect':(3,), 'metric_catalog':(1,), 'metric_snapshot':(2,), 'metric_window':(2,), 'effect_timeline':(3,), 'probe_status':(1,), 'probe_events':(1,)}
        for index in pages.get(command,()):
            if not 0<values[index]<=limits.max_page_size: raise ProtocolError('limit')
        if command=='export_trace' and (not values[2] or values[2]>(limits.max_field_bytes-6)//2 or values[1]+values[2]>_U64): raise ProtocolError('limit')
        if command=='probe_configure' and len(values[-1])!=len(set(values[-1])): raise ProtocolError('malformed')
    return tuple(values)

def _decode_arguments(command,arguments,limits):
    """Decode command shape before admission, as Rust's typed Command does.

    Page/revision/authority checks belong to execution. Invalid command syntax
    instead produces a request-zero response and never retires an envelope ID.
    Canonical scalar arguments preserve typed-command duplicate identity.
    """
    if command in ('status','snapshot','cancel'):
        if arguments: raise ProtocolError('malformed')
        return ()
    if command=='step':
        if len(arguments)!=1 or not arguments[0].startswith('bytes:'): raise ProtocolError('malformed')
        encoded=arguments[0][6:]
        if len(encoded)%2: raise ProtocolError('malformed')
        if len(encoded)//2>limits.max_input_bytes: raise ProtocolError('limit')
        if not re.fullmatch(r'[0-9a-fA-F]*',encoded): raise ProtocolError('malformed')
        return ('bytes:'+encoded.lower(),)
    if command not in _ADAPTER_COMMANDS: raise ProtocolError('unsupported')
    _parse_adapter(command,arguments,limits,check_limits=False)
    spec=_ADAPTER_COMMANDS[command][0]
    path_start=len(spec)-1 if spec.endswith('*') else len(arguments)
    return tuple(_canonical_path(value) if index>=path_start else
                 'u64:'+str(_number(value)) if value.startswith('u64:') else value
                 for index,value in enumerate(arguments))

class _TraceWindow:
    def __init__(self,offset,limit): self.offset,self.limit,self.position,self.chunk=offset,limit,0,bytearray()
    def write(self,data):
        end=_u64(self.position+len(data))
        lo=max(self.offset,self.position); hi=min(self.offset+self.limit,end)
        if lo<hi: self.chunk.extend(data[lo-self.position:hi-self.position])
        self.position=end
        return len(data)

class ProtocolSession:
    def __init__(self, session, *, decode_input=None, encode_input=None, encode_output_into=None, authority=ProtocolAuthority(), limits=ProtocolLimits(), adapters=None, configuration_revision=None):
        if type(authority) is not ProtocolAuthority or type(limits) is not ProtocolLimits: raise ValueError('typed protocol configuration required')
        if decode_input is not None and not callable(decode_input): raise ValueError('input decoder')
        if encode_input is not None and not callable(encode_input): raise ValueError('input encoder')
        if encode_output_into is not None and not callable(encode_output_into): raise ValueError('output encoder')
        self._adapters=dict(adapters or {})
        if any(k not in _ADAPTER_COMMANDS or not callable(v) for k,v in self._adapters.items()): raise ValueError('unknown/noncallable host adapter')
        if configuration_revision is not None and not callable(configuration_revision): raise ValueError('configuration getter')
        self._configuration_revision=configuration_revision
        self.session,self.decode_input,self.authority,self.limits=session,decode_input,authority,limits
        self.encode_input=encode_input
        self.encode_output_into=encode_output_into
        self._busy=False; self.reconnect()
        encode_response(self._response(0),self.limits)
    @property
    def configuration(self): return _u64(0 if self._configuration_revision is None else self._configuration_revision())
    def reconnect(self):
        if getattr(self,'_busy',False): raise ProtocolError('busy')
        if getattr(self,'connected',False): raise ProtocolError('invalid_input')
        reconnecting=hasattr(self,'epoch')
        self.epoch=_u64(next(_epochs)); self.connected=True; self._cache=OrderedDict(); self._cache_bytes=0; self._high=0; self._handles={}; self._next_handle=getattr(self,'_next_handle',1); self._candidates={}; self._candidate_bytes=0; self._next_candidate=getattr(self,'_next_candidate',1)
        self._events=OrderedDict(); self._event_bytes=0; self._event_sequence=0
        self._event_drops=getattr(self,'_event_drops',0)
        if reconnecting: self._publish('reconnected')
        return self.epoch
    def disconnect(self):
        if self._busy: raise ProtocolError('busy')
        self.connected=False
    def _response(self,id,error=None,fields=()):
        return Response(self.session.id,self.epoch,id,self.session.revision,self.configuration,error,tuple(fields))
    def _publish(self,kind):
        if self._event_sequence==_U64:
            self._event_drops=min(_U64,self._event_drops+1)
            return
        self._event_sequence+=1
        # Logical retained-byte accounting matches the public Rust 64-bit
        # ProtocolEvent layout (88 bytes plus owned string bytes), not Python RSS.
        size=88+len(self.session.id.encode('utf-8'))+len(kind.encode('utf-8'))
        if len(self._events)>=self.limits.max_event_entries or self._event_bytes+size>self.limits.max_event_bytes:
            self._event_drops=min(_U64,self._event_drops+1)
            return
        fields=(('version','u64:1'),('session','string:'+self.session.id),
                ('epoch',f'u64:{self.epoch}'),('sequence',f'u64:{self._event_sequence}'),
                ('revision',f'u64:{self.session.revision}'),('configuration',f'u64:{self.configuration}'),
                ('kind','enum:'+kind))
        self._events[self._event_sequence]=(fields,size)
        self._event_bytes+=size
    def _encode_owned(self,callback,*args):
        # Rust holds the typed session borrow while codecs run. Preserve that
        # ownership against direct reentrant Python session mutation too.
        self.session._guard()
        self.session._busy=True
        try: return callback(*args)
        finally: self.session._busy=False
    def handle(self,request):
        if self._busy: return self._response(request.request_id,'busy')
        if request.version!=1: return self._response(request.request_id,'unsupported_version')
        if request.session!=self.session.id: return self._response(request.request_id,'wrong_session')
        if request.epoch==0 and request.command=='status':
            from dataclasses import replace
            request=replace(request,epoch=self.epoch)
        if request.epoch!=self.epoch: return self._response(request.request_id,'wrong_epoch')
        if not self.connected: return self._response(request.request_id,'disconnected')
        try:
            if request.request_id==0: raise ProtocolError('malformed')
            encoded=encode_request(request,self.limits)
        except ProtocolError as e: return self._response(request.request_id,e.code)
        cached=self._cache.get(request.request_id)
        if cached: return cached[1] if cached[0]==encoded else self._response(request.request_id,'duplicate_conflict')
        if request.request_id<=self._high: return self._response(request.request_id,'retired_request')
        self._high=request.request_id; self._busy=True
        try:
            try: response=self._response(request.request_id,fields=self._execute(request))
            except ProtocolError as e: response=self._response(request.request_id,e.code)
            except Exception: response=self._response(request.request_id,'callback')
            try: wire=encode_response(response,self.limits)
            except ProtocolError: response=self._response(request.request_id,'limit'); wire=encode_response(response,self.limits)
            size=len(encoded)+len(wire)+128
            if size<=self.limits.max_cache_bytes:
                while self._cache and (len(self._cache)>=self.limits.max_cache_entries or self._cache_bytes+size>self.limits.max_cache_bytes):
                    _,old=self._cache.popitem(last=False); self._cache_bytes-=old[2]
                self._cache[request.request_id]=(encoded,response,size); self._cache_bytes+=size
            return response
        finally: self._busy=False
    def _execute(self,r):
        c,a=r.command,r.arguments
        _decode_arguments(c,a,self.limits)
        if c=='status':
            boolean=lambda value:'bool:'+str(bool(value)).lower()
            probes='probe_status' in self._adapters
            telemetry='telemetry_health' in self._adapters
            return (
                ('mode','enum:simulation'),('phase','enum:'+self.session.phase.capitalize()),
                ('sequence',f'u64:{self.session.sequence}'),('environment','string:'+self.session.policy.label),
                ('capability.input_delivery',boolean(self.authority.deliver_inputs)),
                ('capability.inspection',boolean('inspect' in self._adapters)),
                ('capability.watch_configuration',boolean(self.authority.configure_watches and 'watch' in self._adapters)),
                ('capability.exact_values',boolean(self.authority.read_exact_values)),
                ('capability.live_control','bool:false'),('capability.live_restore','bool:false'),
                ('capability.effects','bool:false'),('capability.probe_queries',boolean(probes)),
                ('capability.probe_configuration',boolean(probes and self.authority.configure_diagnostics)),
                ('capability.trace_export',boolean(self.authority.export_exact_trace and self.session.capabilities['exact_recording'])),
                ('capability.effect_telemetry',boolean(telemetry)),('capability.metric_queries',boolean('metric_catalog' in self._adapters)),
                ('limits.telemetry_snapshot_bytes',f'u64:{self.limits.max_telemetry_snapshot_bytes}'),
                ('limits.frame_bytes',f'u64:{self.limits.max_frame_bytes}'),
                ('limits.dedup_entries',f'u64:{self.limits.max_cache_entries}'),
                ('limits.dedup_bytes',f'u64:{self.limits.max_cache_bytes}'),
                ('limits.page_size',f'u64:{self.limits.max_page_size}'),
                ('events.dropped',f'u64:{self._event_drops}'),('stop','enum:'+('none' if self.session.stop is None else self.session.stop.kind)),
            )
        permission=('deliver_inputs' if c in ('step','cancel') else
                    _ADAPTER_COMMANDS.get(c,('',None))[1])
        if permission and not getattr(self.authority,permission): raise ProtocolError('unauthorized')
        if c in ('watch','unwatch') and r.expected_configuration!=self.configuration: raise ProtocolError('stale_configuration')
        if c in self._adapters:
            typed=_parse_adapter(c,a,self.limits)
            if c in _HANDLE_COMMANDS and self._handles.get(typed[0])!=self.session.revision: raise ProtocolError('stale_handle')
            if c in ('inspect','export_trace','inputs','select','watch') and r.expected_revision!=self.session.revision: raise ProtocolError('stale_revision')
            if c=='watch' and typed[1]!=self.session.revision: raise ProtocolError('stale_revision')
            prepared=self._adapters[c](typed)
            if c in ('watch','unwatch','probe_configure','select') and type(prepared) is not ProtocolPrepared: raise ProtocolError('callback')
            source=prepared.fields if type(prepared) is ProtocolPrepared else prepared
            fields=[]
            total=0
            for pair in source:
                if len(fields)*2+13>self.limits.max_response_fields: raise ProtocolError('limit')
                if type(pair) is not tuple or len(pair)!=2 or any(type(v) is not str for v in pair): raise ProtocolError('callback')
                fields.append(pair)
                total+=sum(len(v.encode('utf-8')) for v in pair)
                if total>self.limits.max_frame_bytes: raise ProtocolError('limit')
            response=self._response(r.request_id,fields=fields)
            if type(prepared) is ProtocolPrepared and prepared.commit is not None:
                # The commit may advance either revision across a decimal-width
                # boundary. Reserve its complete acknowledgement before mutation.
                from dataclasses import replace
                response=replace(response,revision=_U64,configuration=_U64)
            encode_response(response,self.limits)
            if type(prepared) is ProtocolPrepared and prepared.commit is not None:
                if prepared.commit() is not None: raise ProtocolError('callback')
                if c in ('watch','unwatch'):
                    self._publish('watch_configured' if c=='watch' else 'watch_removed')
            return tuple(fields)
        if c in ('snapshot','inputs','select','step','cancel','watch') and r.expected_revision!=self.session.revision: raise ProtocolError('stale_revision')
        if c=='watch' and _number(a[1])!=self.session.revision: raise ProtocolError('stale_revision')
        if c=='inputs':
            offset,limit=_parse_adapter(c,a,self.limits)
            if self.encode_input is None or self.decode_input is None or not hasattr(self.session,'inputs'): raise ProtocolError('unsupported')
            page=self.session.inputs(self.session.revision,offset,limit)
            records=[]; fields=[('complete','bool:'+str(page.complete).lower()),('next_offset','none' if page.next_offset is None else f'u64:{page.next_offset}')]
            total=0
            for index,token in enumerate(page.candidates):
                encoded=self._encode_owned(self.encode_input,token.input)
                if type(encoded) is not bytes: raise ProtocolError('callback')
                if len(encoded)>self.limits.max_input_bytes: raise ProtocolError('limit')
                id=self._next_candidate+index
                if id>_U64: raise ProtocolError('exhausted')
                records.append((id,self.session.revision,encoded)); total+=len(encoded)+64
                fields.append((f'candidates.{index}.token',f'u64:{id}'))
                if self.authority.read_exact_values: fields.append((f'candidates.{index}.encoded','bytes:'+encoded.hex()))
            fields.append(('candidates.returned',f'u64:{len(records)}'))
            if len(records)>self.limits.max_candidates or total>self.limits.max_candidate_bytes: raise ProtocolError('limit')
            encode_response(self._response(r.request_id,fields=fields),self.limits)
            while self._candidates and (len(self._candidates)+len(records)>self.limits.max_candidates or self._candidate_bytes+total>self.limits.max_candidate_bytes):
                old=self._candidates.pop(next(iter(self._candidates))); self._candidate_bytes-=len(old[1])+64
            for id,revision,encoded in records: self._candidates[id]=(revision,encoded)
            self._next_candidate+=len(records); self._candidate_bytes+=total
            return tuple(fields)
        if c=='select':
            (token,)=_parse_adapter(c,a,self.limits)
            if not self.authority.deliver_inputs: raise ProtocolError('unauthorized')
            candidate=self._candidates.get(token)
            if candidate is None or candidate[0]!=self.session.revision: raise ProtocolError('stale_handle')
            # Decode the retained canonical value and re-run session environmental policy.
            try: value=self._encode_owned(self.decode_input,candidate[1])
            except Exception: raise ProtocolError('invalid_input') from None
            if self.session.phase in ('ended','disconnected'): raise ProtocolError('ended')
            try: result=self.session.step(r.expected_revision,value)
            except Exception as error:
                if 'not permitted' in str(error): raise ProtocolError('invalid_input') from None
                raise
            if result.delivered: self._publish('transition')
            return (('delivered','bool:'+str(result.delivered).lower()),('sequence',f'u64:{result.sequence}'))
        if c=='events':
            (limit,)=_parse_adapter(c,a,self.limits)
            count=min(limit,len(self._events))
            fields=[('events.dropped',f'u64:{self._event_drops}')]
            for index,(event,_) in enumerate(self._events.values()):
                if index==count: break
                fields.extend((f'events.{index}.{key}',value) for key,value in event)
            fields.append(('events.returned',f'u64:{count}'))
            encode_response(self._response(r.request_id,fields=fields),self.limits)
            for _ in range(count):
                _,(_,size)=self._events.popitem(last=False)
                self._event_bytes-=size
            return tuple(fields)
        if c=='snapshot':
            if self._next_handle>_U64: raise ProtocolError('exhausted')
            h=_u64(self._next_handle)
            # Reserve the complete acknowledgement before publishing a handle.
            encode_response(self._response(r.request_id,fields=(('handle',f'u64:{h}'),('snapshot.revision',f'u64:{self.session.revision}'),('snapshot.sequence',f'u64:{self.session.sequence}'))),self.limits)
            if len(self._handles)>=self.limits.max_handles: self._handles.pop(next(iter(self._handles)))
            h=_u64(self._next_handle); self._next_handle+=1; self._handles[h]=self.session.revision
            return (('handle',f'u64:{h}'),('snapshot.revision',f'u64:{self.session.revision}'),('snapshot.sequence',f'u64:{self.session.sequence}'))
        if c in ('checks','outputs','inspect','export_trace'):
            if not a: raise ProtocolError('malformed')
            handle=_number(a[0])
            if self._handles.get(handle)!=self.session.revision: raise ProtocolError('stale_handle')
            if c in ('inspect','export_trace') and r.expected_revision!=self.session.revision: raise ProtocolError('stale_revision')
            if c=='checks':
                observation=self.session.observation
                checks=self.session.initial_checks if observation is None else observation.checks
                fields=[('checks.complete','bool:'+str(self.session.checks_complete).lower()),('checks.total',f'u64:{len(checks)}')]
                first=next(((i,check) for i,check in enumerate(checks) if check.status=='failed'),None)
                if first is None: fields.append(('checks.first_failure','none'))
                else:
                    index,check=first
                    phase='initial_state' if observation is None else ('post_state' if index<observation.state_check_count else 'transition')
                    fields.extend((('checks.first_failure.id','string:'+check.id),('checks.first_failure.index',f'u64:{index}'),('checks.first_failure.sequence',f'u64:{self.session.sequence}'),('checks.first_failure.phase','enum:'+phase)))
                for i,check in enumerate(checks[:self.limits.max_page_size]): fields.extend(((f'checks.{i}.id','string:'+check.id),(f'checks.{i}.status','enum:'+check.status)))
                fields.append(('checks.returned',f'u64:{min(len(checks),self.limits.max_page_size)}'))
                fields.append(('checks.truncated','bool:'+str(len(checks)>self.limits.max_page_size).lower()))
                return tuple(fields)
            if c=='outputs':
                observation=self.session.observation
                if observation is None: return (('outputs.total','u64:0'),)
                outputs=observation.transition.outputs
                exact=self.authority.read_exact_values and self.encode_output_into is not None
                fields=[('outputs.total',f'u64:{len(outputs)}'),('disposition','enum:'+observation.transition.disposition.kind),('outputs.provenance','enum:simulated'),('outputs.payloads','enum:exact_sensitive' if exact else 'enum:unavailable')]
                if exact:
                    from .codec import encode_value
                    from types import SimpleNamespace
                    adapter=SimpleNamespace(encode_output_into=self.encode_output_into)
                    for index,output in enumerate(outputs[:self.limits.max_page_size]):
                        try: encoded=self._encode_owned(lambda:encode_value(adapter,'output',output,maximum=self.limits.max_input_bytes))
                        except Exception as error:
                            if 'encoded blob exceeds byte limit' in str(error): raise ProtocolError('limit') from None
                            raise ProtocolError('callback') from None
                        fields.append((f'outputs.{index}.encoded','bytes:'+encoded.hex()))
                    fields.append(('outputs.truncated','bool:'+str(len(outputs)>self.limits.max_page_size).lower()))
                return tuple(fields)
            if c=='export_trace':
                if not self.authority.export_exact_trace: raise ProtocolError('unauthorized')
                _,offset,limit=_parse_adapter(c,a,self.limits)
                if not self.session.capabilities['exact_recording']: raise ProtocolError('unsupported')
                window=_TraceWindow(offset,limit)
                self.session.write_trace(window)
                if offset>window.position: raise ProtocolError('invalid_input')
                next_offset=offset+len(window.chunk)
                return (('export.kind','enum:exact_trace_sensitive'),('export.redacted','bool:false'),('export.snapshot_revision',f'u64:{self.session.revision}'),('export.snapshot_sequence',f'u64:{self.session.sequence}'),('export.total_bytes',f'u64:{window.position}'),('export.offset',f'u64:{offset}'),('export.returned_bytes',f'u64:{len(window.chunk)}'),('export.next_offset','none' if next_offset==window.position else f'u64:{next_offset}'),('export.complete','bool:'+str(next_offset==window.position).lower()),('export.bytes','bytes:'+window.chunk.hex()))
            raise ProtocolError('unsupported')
        if c in ('step','cancel'):
            if not self.authority.deliver_inputs: raise ProtocolError('unauthorized')
            if c=='cancel':
                if self.session.phase in ('ended','disconnected'): raise ProtocolError('ended')
                self.session.cancel(r.expected_revision)
                self._publish('cancelled')
                return ()
            if len(a)!=1 or not re.fullmatch(r'bytes:(?:[0-9a-fA-F]{2})*',a[0]): raise ProtocolError('malformed')
            if len(a[0][6:])//2>self.limits.max_input_bytes: raise ProtocolError('limit')
            if self.decode_input is None: raise ProtocolError('unsupported')
            try: value=self._encode_owned(self.decode_input,bytes.fromhex(a[0][6:]))
            except Exception: raise ProtocolError('invalid_input') from None
            if self.session.phase in ('ended','disconnected'): raise ProtocolError('ended')
            try: result=self.session.step(r.expected_revision,value)
            except Exception as e:
                if 'not permitted' in str(e): raise ProtocolError('invalid_input') from None
                raise
            if result.delivered: self._publish('transition')
            return (('delivered','bool:'+str(result.delivered).lower()),('sequence',f'u64:{result.sequence}'))
        raise ProtocolError('unsupported')

def serve(session,reader,writer):
    try:
        while True:
            payload=read_frame(reader,session.limits.max_frame_bytes)
            if payload is None: return
            try: response=session.handle(decode_request(payload,session.limits))
            except ProtocolError as e: response=session._response(0,e.code)
            write_frame(writer,encode_response(response,session.limits),session.limits.max_frame_bytes)
    finally: session.disconnect()


def inspection_adapters(session, *, project=None, limits=None):
    """Explicitly opt one simulation into its approved structural projection.

    Integer map keys retain their type tag as ``(kind, decimal_string)``; a host map adapter
    must use the same identities. This does not infer typed maps from Python dicts.
    No raw state codec, repr(), or error messages are exposed.
    """
    from .debugger_inspect import inspect, InspectQuery, InspectLimits, SnapshotId, PathSegment, PageRequest
    bounds=InspectLimits() if limits is None else limits
    if type(bounds) is not InspectLimits or (project is not None and not callable(project)): raise ValueError('inspection configuration')
    def callback(args):
        _,schema,offset,limit,path=args
        segments=tuple(PathSegment(kind,value) if kind in ('field','variant','index')
            else PathSegment('map-key',value if kind in ('key.string','key.bool')
                else (kind[4:],str(value) if type(value) is int else value)) for kind,value in path)
        value=session.state
        if project is not None: value=project(value)
        result=inspect(value,InspectQuery(SnapshotId(session.epoch,session.revision,session.sequence),segments,schema,PageRequest(offset,limit),bounds))
        fields=[('inspect.schema.name','string:'+result.schema.name),('inspect.schema.version',f'u64:{result.schema.version}')]
        pending=[('inspect.root',result.node)]
        while pending:
            prefix,node=pending.pop()
            wire_kind='float' if node.kind=='scalar' and node.value[0] in ('f32','f64') else node.kind
            fields.extend(((prefix+'.kind','enum:'+wire_kind),(prefix+'.completeness','enum:'+node.completeness),(prefix+'.children',f'u64:{len(node.children)}'),(prefix+'.child_count','none' if node.child_count is None else f'u64:{node.child_count}')))
            if node.page is not None:
                fields.extend(((prefix+'.page.offset',f'u64:{node.page.offset}'),(prefix+'.page.total',f'u64:{node.page.total}'),(prefix+'.page.next_offset','none' if node.page.next_offset is None else f'u64:{node.page.next_offset}')))
            value=node.value
            if node.kind=='scalar' and type(value) is tuple and len(value)==3 and value[0] in ('f32','f64'):
                kind,decimal,bits=value
                fields.extend(((prefix+'.value',kind+':'+decimal),(prefix+'.bits',f'u64:{bits}')))
            elif node.kind=='enum':
                name,variant_id,variant_label=value
                fields.extend(((prefix+'.value','string:'+name),(prefix+'.variant_id','string:'+variant_id),
                               (prefix+'.variant_label','string:'+variant_label)))
            elif value is None: fields.append((prefix+'.value','none'))
            elif type(value) is str: fields.append((prefix+'.value','string:'+value))
            elif type(value) is bytes: fields.append((prefix+'.value','bytes:'+value.hex()))
            elif type(value) is tuple and len(value)==2 and type(value[0]) is str:
                kind,payload=value
                fields.append((prefix+'.value.type','enum:'+kind))
                if type(payload) is bool: fields.append((prefix+'.value','bool:'+str(payload).lower()))
                elif type(payload) is str: fields.append((prefix+'.value','string:'+payload))
                elif payload is None: fields.append((prefix+'.value','none'))
                else: fields.append((prefix+'.value','enum:unavailable'))
            else: fields.append((prefix+'.value','enum:unavailable'))
            for index,child in reversed(tuple(enumerate(node.children))):
                key=f'{prefix}.child.{index}'
                fields.extend(((key+'.label','string:'+child.label),(key+'.segment.kind','enum:'+child.segment.kind)))
                # Segment values are bounded structural metadata, never expressions.
                if type(child.segment.value) is str: fields.append((key+'.segment.value','string:'+child.segment.value))
                elif type(child.segment.value) is bool: fields.append((key+'.segment.value','bool:'+str(child.segment.value).lower()))
                elif type(child.segment.value) is int: fields.append((key+'.segment.value',f'u64:{child.segment.value}'))
                elif type(child.segment.value) is tuple and len(child.segment.value)==2 and type(child.segment.value[0]) is str:
                    kind,payload=child.segment.value
                    if re.fullmatch(r'[ui](8|16|32|64|128|size)',kind) and type(payload) is str: fields.append((key+'.segment.value',kind+':'+str(payload)))
                    elif kind=='bool' and type(payload) is bool: fields.append((key+'.segment.value','bool:'+str(payload).lower()))
                    elif kind in ('string','char') and type(payload) is str: fields.append((key+'.segment.value','string:'+payload))
                    else: fields.append((key+'.segment.value','enum:unavailable'))
                else: fields.append((key+'.segment.value','enum:unavailable'))
                pending.append((key,child.node))
        return tuple(fields)
    return {'inspect':callback}
