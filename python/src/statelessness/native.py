"""Explicit ABI 2 backend for application-owned Python models.

Load a matching library explicitly: ``with NativeEngine(path).session(model) as s``.
Methods are synchronous and owner-thread confined. No backend fallback occurs.
The ordinary ``statelessness`` entry point remains the independent implementation
until runtime, platform, deployment and migration gates have passed.
"""
from __future__ import annotations
import ctypes as C
import atexit
from copy import deepcopy
import hashlib
from pathlib import Path
import threading
from typing import Any
from .core import (Check, CheckPolicy, Disposition, Failure, Metadata, ModelError,
                   ReplayReport, SearchConfig, SearchReport, Trace, TraceLimits,
                   Transition, Violation, _natural)
from . import _native_packet

U64 = C.c_uint64
U32 = C.c_uint32
I32 = C.c_int32
BYTE = C.POINTER(C.c_uint8)
P64, P32 = C.POINTER(U64), C.POINTER(U32)
MAX = (1 << 64) - 1
DISPATCH = C.CFUNCTYPE(I32, U64, U64, U64, U32, P64, U32, BYTE, U64)
RELEASE = C.CFUNCTYPE(None, U64, U32, U64)
STATE, INPUT, OUTPUT, CURSOR = 1, 2, 3, 4
_LIVE_SESSIONS: dict[tuple[int, int], Any] = {}


def _close_owner_sessions() -> None:
    for session in list(_LIVE_SESSIONS.values()):
        if session.thread == threading.get_ident():
            try: session.close()
            except BaseException: pass


atexit.register(_close_owner_sessions)
KINDS = ("accepted", "rejected", "ignored")
STATUSES = ("passed", "failed", "skipped")


class NativeError(ModelError):
    def __init__(self, message: str, status: int = 11):
        super().__init__(message)
        self.status = status


class _Callbacks(C.Structure):
    _fields_ = [("abi_version", U32), ("struct_size", U32), ("capabilities", U64),
                ("context", U64), ("dispatch", DISPATCH), ("release", RELEASE),
                ("max_values", U64), ("max_items", U64), ("max_bytes", U64)]


def _diagnostic(error: BaseException) -> str:
    try: return str(error)[:16000]
    except BaseException: return "callback failed (diagnostic unavailable)"


def _span(value: bytes) -> tuple[Any, int]:
    if type(value) is not bytes: raise TypeError("expected bytes")
    buf = C.create_string_buffer(value, max(1, len(value)))
    return C.cast(buf, BYTE), len(value)


def _text(value: str) -> tuple[Any, int]:
    if type(value) is not str: raise TypeError("expected string")
    return _span(value.encode("utf-8", "strict"))


class NativeEngine:
    """One loaded engine copy. Supply expected_sha256 for artifact identity checks."""
    def __init__(self, library: str | Path, *, expected_sha256: str | None = None):
        path = Path(library).expanduser().resolve(strict=True)
        self.path = path
        self.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if expected_sha256 is not None and self.sha256 != expected_sha256:
            raise NativeError("native library hash mismatch", 3)
        self.api = C.CDLL(str(path))
        signatures = {
            'session_new': [C.POINTER(_Callbacks), BYTE,U64,BYTE,U64,U32,U32,U32,P64],
            'session_close': [U64], 'session_capabilities':[U64,P64],
            'cancel':[U64], 'cancel_reset':[U64],
            'value_import':[U64,U32,U64,P64], 'value_release':[U64,U64],
            'value_host_token':[U64,U64,P32,P64],
            'callback_value':[U64,U64,U32,U64], 'callback_scalar':[U64,U64,U64],
            'callback_check':[U64,U64,U32,BYTE,U64,BYTE,U64],
            'callback_bytes':[U64,U64,BYTE,U64], 'callback_disposition':[U64,U64,U32,BYTE,U64],
            'callback_error':[U64,U64,BYTE,U64], 'callback_rng':[U64,U64,P64],
            'callback_rng_index':[U64,U64,U64,P32,P64],
            'check_initial':[U64,U64,P64],
            'check_observed':[U64,U64,U64,U64,P64,U64,U32,BYTE,U64,U64,U64,U32,P64],
            'enumerate':[U64,U64,U64,U64,U64,U64,P64],
            'record_cursor':[U64,U64,U64,P64], 'record_cursor_limited':[U64,U64,U64,U64,U64,U64,P64], 'replay':[U64,BYTE,U64,U32,P64],
            'fuzz':[U64,U64,U64,U64,U64,U32,U64,P64],
            'shrink':[U64,U64,U64,U64,U64,P64],
            'result_scalar':[U64,U64,U32,P64], 'result_check':[U64,U64,U64,P32,P32],
            'result_input':[U64,U64,U64,P64], 'result_release':[U64,U64],
            'result_copy':[U64,U64,U32,U64,BYTE,U64,P64],
            'last_error':[C.POINTER(I32),P32,P32,P64,BYTE,U64,P64],
            'trace_convert':[U64,U32,BYTE,U64,P64],
            'ring_new':[U64,U64,U64,U64,P64],
            'ring_observe':[U64,U64,U64,U64,U64,P64,U64,U32,BYTE,U64,P64],
            'ring_snapshot':[U64,U64,P64], 'ring_release':[U64,U64],
            'rng_next':[P64,P64], 'rng_index':[P64,U64,P32,P64],
        }
        try:
            self.api.stateless_v2_abi_version.argtypes = []
            self.api.stateless_v2_abi_version.restype = U32
            if self.api.stateless_v2_abi_version() != 2:
                raise NativeError("unsupported native ABI; expected version 2", 3)
            self.api.stateless_v2_capabilities.argtypes = []
            self.api.stateless_v2_capabilities.restype = U64
            self.capabilities = int(self.api.stateless_v2_capabilities())
            for name, args in signatures.items():
                fn = getattr(self.api, 'stateless_v2_' + name)
                fn.argtypes, fn.restype = args, I32
        except AttributeError as error:
            raise NativeError("native library lacks the required ABI 2 symbols", 3) from error

    def _error(self, status: int) -> NativeError:
        code, op, phase, sequence, size = I32(), U32(), U32(), U64(), U64()
        fn = self.api.stateless_v2_last_error
        fn(C.byref(code), C.byref(op), C.byref(phase), C.byref(sequence), None, 0, C.byref(size))
        if size.value > 1024 * 1024: return NativeError("native error exceeds diagnostic limit", status)
        buffer = (C.c_uint8 * max(1, size.value))()
        fn(C.byref(code), C.byref(op), C.byref(phase), C.byref(sequence), buffer, size.value, C.byref(size))
        return NativeError(bytes(buffer[:size.value]).decode('utf-8', 'replace') or f"native status {status}", status)

    def _ok(self, status: int) -> None:
        if status != 0: raise self._error(status)

    def session(self, model: Any, *, max_values: int = 1_000_000,
                max_items: int = 250_000, max_bytes: int = 64 * 1024 * 1024,
                automatic_generation: bool = False) -> Session:
        return Session(self, model, max_values, max_items, max_bytes, automatic_generation)

    def rng(self, seed: int) -> NativeRng:
        return NativeRng(self, seed)


class NativeRng:
    def __init__(self, engine: NativeEngine, seed: int):
        self.engine, self._state = engine, U64(_natural(seed, 'seed'))
    @property
    def state(self) -> int: return self._state.value
    def next_u64(self) -> int:
        value = U64()
        self.engine._ok(self.engine.api.stateless_v2_rng_next(C.byref(self._state), C.byref(value)))
        return value.value
    def index(self, upper: int) -> int | None:
        upper = _natural(upper, 'upper'); present, value = U32(), U64()
        self.engine._ok(self.engine.api.stateless_v2_rng_index(C.byref(self._state), upper, C.byref(present), C.byref(value)))
        return value.value if present.value else None


class _CallbackRng:
    def __init__(self, session: Session, invocation: int): self.session, self.invocation = session, invocation
    def next_u64(self) -> int:
        value = U64(); s = self.session
        s.engine._ok(s.api.stateless_v2_callback_rng(s.handle, self.invocation, C.byref(value)))
        return value.value
    def index(self, upper: int) -> int | None:
        s = self.session; present, value = U32(), U64()
        s.engine._ok(s.api.stateless_v2_callback_rng_index(s.handle, self.invocation,
                    _natural(upper, 'upper'), C.byref(present), C.byref(value)))
        return value.value if present.value else None


class Session:
    def __init__(self, engine: NativeEngine, model: Any, max_values: int, max_items: int, max_bytes: int, automatic_generation: bool = False):
        self.engine, self.api, self.model = engine, engine.api, model
        self.thread, self.busy, self.handle = threading.get_ident(), False, 0
        self._roots: dict[int, tuple[int, Any]] = {}
        self._next = 1
        self.cleanup_errors: list[str] = []
        for name, value in [('max_values',max_values),('max_items',max_items),('max_bytes',max_bytes)]:
            _natural(value, name, 1)
        self.max_values, self.max_items, self.max_bytes = max_values, max_items, max_bytes
        capabilities = 1
        if callable(getattr(model, 'inputs', None)): capabilities |= 2
        codecs = ('metadata','encode_state','decode_state','encode_input','decode_input','encode_output')
        if all(callable(getattr(model, name, None)) for name in codecs): capabilities |= 4 | 128
        if callable(getattr(model, 'generate', None)) and callable(getattr(model, 'is_enabled', None)): capabilities |= 8
        if type(automatic_generation) is not bool: raise TypeError('automatic_generation must be boolean')
        if automatic_generation:
            if not capabilities & 2: raise TypeError('automatic generation requires inputs')
            capabilities |= 8 | 256
        if capabilities & 8 and callable(getattr(model, 'simpler_inputs', None)): capabilities |= 16
        if callable(getattr(model, 'hash_state', None)): capabilities |= 32
        if callable(getattr(model, 'estimated_state_bytes', None)): capabilities |= 64
        metadata = model.metadata() if capabilities & 4 else Metadata(type(model).__qualname__)
        if not isinstance(metadata, Metadata): raise TypeError("metadata must be Metadata")
        self.metadata = metadata
        self._dispatch_fn, self._release_fn = DISPATCH(self._dispatch), RELEASE(self._release)
        callbacks = _Callbacks(2,C.sizeof(_Callbacks),capabilities,0,self._dispatch_fn,self._release_fn,
                               max_values,max_items,max_bytes)
        out = U64(); name, build = _text(metadata.name), _text(metadata.build)
        engine._ok(self.api.stateless_v2_session_new(C.byref(callbacks),*name,*build,
                    metadata.model_version,metadata.properties_version,metadata.codec_version,C.byref(out)))
        self.handle, self.capabilities = out.value, capabilities
        # Rust holds callback addresses, not Python GC roots. Keep the complete
        # callback/library graph live until explicit owner-thread closure.
        _LIVE_SESSIONS[(id(engine), self.handle)] = self

    def __enter__(self) -> Session: self._ready(); return self
    def __exit__(self, *_: Any) -> None: self.close()
    @property
    def retained_roots(self) -> int: return len(self._roots)
    def _ready(self) -> None:
        if not self.handle: raise NativeError("session is closed", 14)
        if threading.get_ident() != self.thread: raise NativeError("session belongs to another thread", 15)
        if self.busy: raise NativeError("session operation cannot reenter a callback", 16)
    def close(self) -> None:
        if not self.handle: return
        self._ready(); self.busy = True
        try:
            self.engine._ok(self.api.stateless_v2_session_close(self.handle))
            _LIVE_SESSIONS.pop((id(self.engine), self.handle), None)
            self.handle = 0
        finally: self.busy = False
    def cancel(self) -> None:
        if not self.handle: raise NativeError("session is closed", 14)
        self.engine._ok(self.api.stateless_v2_cancel(self.handle))
    def reset_cancellation(self) -> None:
        self._ready(); self.engine._ok(self.api.stateless_v2_cancel_reset(self.handle))
    def _copy(self, kind: int, value: Any) -> Any:
        hook = getattr(self.model, {STATE:'clone_state',INPUT:'clone_input',OUTPUT:'clone_output'}[kind], None)
        busy = self.busy
        self.busy = True
        try: return hook(value) if hook else deepcopy(value)
        finally: self.busy = busy
    def _root(self, kind: int, value: Any) -> int:
        if len(self._roots) >= self.max_values or self._next > MAX:
            if kind == CURSOR and callable(getattr(value, 'close', None)):
                busy, self.busy = self.busy, True
                try: value.close()
                except BaseException as error:
                    if len(self.cleanup_errors) < 16: self.cleanup_errors.append(_diagnostic(error))
                finally: self.busy = busy
            raise NativeError("host root limit", 18)
        token = self._next; self._next += 1; self._roots[token] = (kind,value); return token
    def _release(self, _context: int, kind: int, token: int) -> None:
        busy, self.busy = self.busy, True
        try:
            actual, value = self._roots.pop(token)
            if actual != kind: raise NativeError("host root kind mismatch")
            if kind == CURSOR and callable(getattr(value, 'close', None)): value.close()
        except BaseException as error:
            # Release cannot throw across C. Diagnostics remain available to caller.
            try:
                if len(self.cleanup_errors) < 16: self.cleanup_errors.append(_diagnostic(error))
            except BaseException: pass
        finally: self.busy = busy
    def _value(self, token: int, kind: int, copy: bool = True) -> Any:
        actual, value = self._roots[token]
        if actual != kind: raise NativeError("host token kind mismatch",14)
        return self._copy(kind,value) if copy else value
    def _publish(self, invocation: int, kind: int, value: Any) -> None:
        token = self._root(kind, value if kind == CURSOR else self._copy(kind,value))
        status = self.api.stateless_v2_callback_value(self.handle,invocation,kind,token)
        if status:
            self._release(0,kind,token)
            raise self.engine._error(status)
    def _checks(self, invocation: int, checks: Any) -> None:
        for check in checks:
            if not isinstance(check, Check): raise TypeError("checker must return Check values")
            self.engine._ok(self.api.stateless_v2_callback_check(self.handle,invocation,
                            STATUSES.index(check.status),*_text(check.id),*_text(check.details)))
    def _dispatch(self, _ctx: int, session: int, invocation: int, op: int,
                  args: Any, count: int, data: Any, size: int) -> int:
        try:
            if session != self.handle or threading.get_ident() != self.thread:
                raise NativeError("callback session/thread mismatch",15)
            a = [int(args[i]) for i in range(count)]
            if size > self.max_bytes: raise NativeError("callback byte limit",18)
            raw = C.string_at(data,size) if size else b''
            value = lambda index,kind: self._value(a[index],kind)
            scalar = lambda v: self.engine._ok(self.api.stateless_v2_callback_scalar(session,invocation,_natural(v,'callback scalar')))
            if op == 1: self._publish(invocation,STATE,self.model.initial_state())
            elif op == 2:
                t = self.model.step(value(0,STATE),value(1,INPUT))
                if not isinstance(t,Transition): raise TypeError("step must return Transition")
                self._publish(invocation,STATE,t.state)
                for output in t.outputs: self._publish(invocation,OUTPUT,output)
                self.engine._ok(self.api.stateless_v2_callback_disposition(session,invocation,
                                KINDS.index(t.disposition.kind),*_text(t.disposition.reason)))
            elif op == 3: self._checks(invocation,self.model.check_state(value(0,STATE)))
            elif op == 4:
                callback = getattr(self.model,'check_transition',None)
                if callback:
                    t = Transition(value(2,STATE),tuple(self._value(token,OUTPUT) for token in a[4:]),
                                   Disposition(KINDS[a[3]],raw.decode('utf-8','strict')))
                    self._checks(invocation,callback(value(0,STATE),value(1,INPUT),t))
            elif op in (5,17):
                kind = STATE if op == 5 else INPUT
                callback = getattr(self.model,'equal_states' if op == 5 else 'equal_inputs',lambda x,y:x==y)
                answer = callback(value(0,kind),value(1,kind))
                if type(answer) is not bool: raise TypeError("equality must return bool")
                scalar(int(answer))
            elif op == 6:
                hashed = self.model.hash_state(value(0,STATE))
                if type(hashed) is not int: raise TypeError("state hash must be an integer")
                scalar(hashed & MAX)
            elif op == 7:
                estimate = self.model.estimated_state_bytes(value(0,STATE))
                if estimate is not None: scalar(estimate)
            elif op == 8: self._publish(invocation,CURSOR,iter(self.model.inputs(value(0,STATE))))
            elif op == 9:
                cursor = self._value(a[0],CURSOR,False)
                try: item = next(cursor)
                except StopIteration: pass
                else: self._publish(invocation,INPUT,item)
            elif op in (10,12,14):
                kind, name = {10:(STATE,'encode_state'),12:(INPUT,'encode_input'),14:(OUTPUT,'encode_output')}[op]
                blob = getattr(self.model,name)(value(0,kind))
                if type(blob) is not bytes or len(blob)>self.max_bytes: raise TypeError("codec must return bounded bytes")
                self.engine._ok(self.api.stateless_v2_callback_bytes(session,invocation,*_span(blob)))
            elif op in (11,13):
                kind, name = (STATE,'decode_state') if op==11 else (INPUT,'decode_input')
                self._publish(invocation,kind,getattr(self.model,name)(raw))
            elif op == 15:
                item = self.model.generate(value(0,STATE),_CallbackRng(self,invocation))
                if item is not None: self._publish(invocation,INPUT,item)
            elif op == 16:
                enabled = self.model.is_enabled(value(0,STATE),value(1,INPUT))
                if type(enabled) is not bool: raise TypeError("is_enabled must return bool")
                scalar(int(enabled))
            elif op == 18: self._publish(invocation,CURSOR,iter(self.model.simpler_inputs(value(0,INPUT))))
            else: raise NativeError(f"unknown callback operation {op}",17)
            return 0
        except BaseException as error:
            try: self.api.stateless_v2_callback_error(session,invocation,*_span(_diagnostic(error).encode('utf-8','replace')[:65536].decode('utf-8','ignore').encode('utf-8')))
            except BaseException: pass
            return 11
    def _import(self, kind: int, value: Any) -> int:
        token = self._root(kind,value if kind==CURSOR else self._copy(kind,value))
        transferred = False
        try:
            out=U64()
            status=self.api.stateless_v2_value_import(self.handle,kind,token,C.byref(out))
            if status: raise self.engine._error(status)
            transferred = True
            return out.value
        finally:
            if not transferred: self._release(0,kind,token)
    def _require(self, capability: int) -> None:
        self._ready()
        if self.capabilities & capability != capability:
            raise NativeError("model lacks required capability",17)
    def _result(self, name: str, *args: Any) -> Result:
        self._ready(); self.busy=True; out=U64()
        try:
            status=getattr(self.api,'stateless_v2_'+name)(self.handle,*args,C.byref(out))
            if status not in (0,1,2,3) and not (status == 11 and out.value and name in ('record_cursor','record_cursor_limited','ring_snapshot')):
                error = self.engine._error(status)
                if out.value: self.api.stateless_v2_result_release(self.handle,out.value)
                raise error
            if not out.value: raise NativeError("engine returned no completed result")
            return Result(self,out.value,status)
        finally: self.busy=False
    def search(self, config: SearchConfig = SearchConfig(), *, max_state_bytes: int | None = None,
               max_duration_millis: int = 0) -> Result:
        return self._result('enumerate',config.max_states,config.max_transitions,config.max_depth,
                            MAX if max_state_bytes is None else _natural(max_state_bytes,'max_state_bytes'),
                            _natural(max_duration_millis,'max_duration_millis'))
    def enumerate_states(self, config: SearchConfig = SearchConfig(), **limits: Any) -> SearchReport:
        with self.search(config,**limits) as result: return result.search_report()
    def check_initial(self, state: Any) -> tuple[Check,...]:
        self._ready(); value=self._import(STATE,state)
        try:
            with self._result('check_initial',value) as result: return tuple(c.check for c in result.checks())
        finally: self.engine._ok(self.api.stateless_v2_value_release(self.handle,value))
    def check_observed(self,before:Any,input:Any,transition:Transition,sequence:int,
                       policy:CheckPolicy=CheckPolicy()) -> tuple[Check,...]:
        self._ready(); _natural(sequence,'sequence',1); values=[]
        def own(kind: int,value: Any) -> int:
            handle=self._import(kind,value); values.append(handle); return handle
        try:
            b,i,a=own(STATE,before),own(INPUT,input),own(STATE,transition.state)
            outputs=[own(OUTPUT,o) for o in transition.outputs]; array=(U64*len(outputs))(*outputs)
            with self._result('check_observed',b,i,a,array,len(outputs),KINDS.index(transition.disposition.kind),
                              *_text(transition.disposition.reason),sequence,policy.state_every,int(policy.transition_checks)) as result:
                return tuple(c.check for c in result.checks())
        finally:
            for handle in reversed(values): self.engine._ok(self.api.stateless_v2_value_release(self.handle,handle))
    def record_binary(self,inputs:Any,max_steps:int=100_000) -> bytes:
        self._require(4); _natural(max_steps,'max_steps'); cursor=self._import(CURSOR,iter(inputs))
        try:
            with self._result('record_cursor',cursor,max_steps) as result: return result.bytes()
        finally: self.engine._ok(self.api.stateless_v2_value_release(self.handle,cursor))
    def record(self,inputs:Any,max_steps:int=100_000,*,limits:TraceLimits=TraceLimits()) -> Trace:
        self._require(4); _natural(max_steps,'max_steps')
        if not isinstance(limits,TraceLimits): raise TypeError("limits must be TraceLimits")
        cursor=self._import(CURSOR,iter(inputs))
        try:
            with self._result('record_cursor_limited',cursor,min(max_steps,limits.max_steps),
                              limits.max_blob_bytes,limits.max_items,limits.max_payload_bytes) as result:
                artifact=result.bytes()
        finally: self.engine._ok(self.api.stateless_v2_value_release(self.handle,cursor))
        with self._result('trace_convert',1,*_span(artifact)) as result:
            return _native_packet.decode(result.bytes(),limits)
    def replay(self,trace:Trace|bytes,allow_build_mismatch:bool=False) -> ReplayReport:
        self._require(4)
        if type(allow_build_mismatch) is not bool: raise ValueError("allow_build_mismatch must be boolean")
        if isinstance(trace,Trace):
            with self._result('trace_convert',2,*_span(_native_packet.encode(trace))) as converted: trace=converted.bytes()
        with self._result('replay',*_span(trace),int(allow_build_mismatch)) as result:
            return ReplayReport('exact' if result.status in (0,1) else 'diverged' if result.status==2 else 'incompatible',
                                result.scalar(14),bool(result.scalar(16)),bool(result.scalar(15)),
                                result.scalar(21) if result.scalar(20) else None,result.text(5) or None)
    def ring(self,initial_state:Any,*,max_steps:int=100,max_retained_bytes:int=32*1024*1024) -> Ring:
        self._require(128); state=self._import(STATE,initial_state); out=U64()
        try:
            self.busy=True
            try: self.engine._ok(self.api.stateless_v2_ring_new(self.handle,state,
                     _natural(max_steps,'max_steps'),_natural(max_retained_bytes,'max_retained_bytes'),C.byref(out)))
            finally: self.busy=False
            return Ring(self,out.value)
        finally: self.engine._ok(self.api.stateless_v2_value_release(self.handle,state))
    def fuzz(self,*,seed:int=0,cases:int=100,max_steps:int=100,max_transitions:int=1_000_000,
             mutation_percent:int=0,max_duration_millis:int=0) -> Result:
        values=[_natural(v,n) for n,v in [('seed',seed),('cases',cases),('max_steps',max_steps),('max_transitions',max_transitions)]]
        return self._result('fuzz',*values,_natural(mutation_percent,'mutation_percent',0,100),_natural(max_duration_millis,'max_duration_millis'))
    def shrink(self,failure:Result,*,max_attempts:int=10_000,max_transitions:int=1_000_000,
               max_duration_millis:int=0) -> Result:
        if failure.session is not self: raise NativeError("result belongs to another session",14)
        failure._ready()
        return self._result('shrink',failure.handle,_natural(max_attempts,'max_attempts'),
                            _natural(max_transitions,'max_transitions'),_natural(max_duration_millis,'max_duration_millis'))


class Result:
    """Session-owned result. Extract owned Python values before closing the session."""
    def __init__(self,session:Session,handle:int,status:int): self.session,self.handle,self.status=session,handle,status
    def __enter__(self) -> Result: self._ready(); return self
    def __exit__(self,*_:Any) -> None: self.close()
    def _ready(self) -> None:
        self.session._ready()
        if not self.handle: raise NativeError("result is closed",14)
    def close(self) -> None:
        if not self.handle: return
        if not self.session.handle: self.handle=0; return
        self._ready(); self.session.engine._ok(self.session.api.stateless_v2_result_release(self.session.handle,self.handle));self.handle=0
    def scalar(self,field:int) -> int:
        self._ready(); value=U64();s=self.session
        s.engine._ok(s.api.stateless_v2_result_scalar(s.handle,self.handle,field,C.byref(value)));return value.value
    def bytes(self,field:int=1,index:int=0) -> bytes:
        self._ready();s=self.session;size=U64();fn=s.api.stateless_v2_result_copy
        status=fn(s.handle,self.handle,field,index,None,0,C.byref(size))
        if status not in (0,18):raise s.engine._error(status)
        if size.value>s.max_bytes:raise NativeError("result exceeds byte limit",18)
        value=(C.c_uint8*max(1,size.value))()
        s.engine._ok(fn(s.handle,self.handle,field,index,value,size.value,C.byref(size)))
        return bytes(value[:size.value])
    def text(self,field:int,index:int=0) -> str:return self.bytes(field,index).decode('utf-8','strict')
    def checks(self) -> tuple[Violation,...]:
        result=[];s=self.session
        for i in range(self.scalar(10)):
            status,phase=U32(),U32()
            s.engine._ok(s.api.stateless_v2_result_check(s.handle,self.handle,i,C.byref(status),C.byref(phase)))
            result.append(Violation(('unspecified','initial_state','state','transition')[phase.value],
                                   Check(self.text(2,i),STATUSES[status.value],self.text(3,i))))
        return tuple(result)
    def inputs(self) -> tuple[Any,...]:
        values=[];s=self.session
        for i in range(self.scalar(11)):
            value,token,kind=U64(),U64(),U32()
            s.engine._ok(s.api.stateless_v2_result_input(s.handle,self.handle,i,C.byref(value)))
            try:
                s.engine._ok(s.api.stateless_v2_value_host_token(s.handle,value.value,C.byref(kind),C.byref(token)))
                values.append(s._value(token.value,INPUT))
            finally:s.engine._ok(s.api.stateless_v2_value_release(s.handle,value.value))
        return tuple(values)
    def search_report(self) -> SearchReport:
        if self.scalar(2)!=2:raise TypeError("not an enumeration result")
        violations=tuple(v for v in self.checks() if v.check.status=='failed')
        failure=Failure(self.inputs(),violations) if violations else None
        termination=('graph_exhausted','depth_bound','state_limit','transition_limit','failure_found',
                     'estimated_state_byte_limit','deadline','cancelled')[self.scalar(3)]
        return SearchReport(termination,self.scalar(4),self.scalar(5),self.scalar(6),self.scalar(7),failure)


class Ring:
    """Bounded runtime observation recorder. observe never invokes model.step."""
    def __init__(self,session:Session,handle:int): self.session,self.handle=session,handle
    def _ready(self) -> None:
        self.session._ready()
        if not self.handle: raise NativeError("recorder is closed",14)
    def __enter__(self) -> Ring: self._ready(); return self
    def __exit__(self,*_:Any) -> None: self.close()
    def close(self) -> None:
        if not self.handle: return
        if not self.session.handle: self.handle=0; return
        self._ready(); self.session.engine._ok(self.session.api.stateless_v2_ring_release(self.session.handle,self.handle)); self.handle=0
    def observe(self,before:Any,input:Any,transition:Transition) -> tuple[Check,...]:
        self._ready(); s=self.session; values=[]
        def own(kind:int,value:Any) -> int:
            h=s._import(kind,value);values.append(h);return h
        try:
            b,i,a=own(STATE,before),own(INPUT,input),own(STATE,transition.state)
            outputs=[own(OUTPUT,o) for o in transition.outputs]; array=(U64*len(outputs))(*outputs)
            with s._result('ring_observe',self.handle,b,i,a,array,len(outputs),
                KINDS.index(transition.disposition.kind),*_text(transition.disposition.reason)) as result:
                return tuple(c.check for c in result.checks())
        finally:
            for value in reversed(values): s.engine._ok(s.api.stateless_v2_value_release(s.handle,value))
    def snapshot(self) -> bytes:
        self._ready()
        with self.session._result('ring_snapshot',self.handle) as result: return result.bytes()
