"""Opt-in cooperative host gate. No reducer, transport, clock or effect is run.

Private Python fields and identity checks are API integrity boundaries, not a
security sandbox against reflection. Exclusive host ownership is required. Permits bind to one controller and are
consumed exactly once. A lost dispatch permit leaves uncertain work in flight.
"""
from collections import deque
from dataclasses import dataclass
from .core import U64_MAX, _natural

class LiveError(ValueError): pass

@dataclass(frozen=True)
class ControllerOptions:
    pause_and_step: bool = False
    inject: bool = False
    detach: str = 'RemainPaused'
    overflow: str = 'Backpressure'
    max_arrivals: int = 1024
    max_arrival_bytes: int = 1024 * 1024
    max_outputs: int = 4096
    def __post_init__(self):
        for key in ('max_arrivals','max_arrival_bytes','max_outputs'): _natural(getattr(self,key),key,1)
        if type(self.pause_and_step) is not bool or type(self.inject) is not bool: raise LiveError('InvalidLimits')
        if self.detach not in ('RemainPaused','Resume','Terminate') or self.overflow not in ('Backpressure','Disconnect'): raise LiveError('InvalidLimits')

@dataclass(frozen=True)
class EffectOrigin:
    epoch: int
    transition: int
    output_index: int
    def __post_init__(self):
        for name in ('epoch','transition','output_index'): _natural(getattr(self,name),name)

@dataclass(frozen=True)
class PauseAck:
    generation: int
    epoch: int
    revision: int
    effect_phase: str

class _Permit:
    __slots__ = ('_owner','_epoch','_revision','_serial','_used')
    def __init__(self, owner, epoch, revision, serial):
        self._owner,self._epoch,self._revision,self._serial,self._used = owner,epoch,revision,serial,False
    @property
    def origin(self): return EffectOrigin(self._epoch,self._revision,self._serial)
    def __copy__(self): raise LiveError('permits cannot be copied')
    def __deepcopy__(self,memo): raise LiveError('permits cannot be copied')

class LiveController:
    def __init__(self, epoch, revision=0, options=ControllerOptions()):
        _natural(epoch,'epoch',1); _natural(revision,'revision')
        if type(options) is not ControllerOptions: raise LiveError('InvalidLimits')
        self._options=options
        self._owner=object(); self._epoch=epoch; self._origin_epoch=epoch; self._revision=revision
        self._phase='Running'; self._connected=True; self._effects='Dispatched'
        self._arrivals=deque(); self._bytes=0; self._overflow=0; self._serial=0; self._active=None
        self._repause=False; self._generation=0; self._terminal=False; self._staged=[]
    @property
    def options(self): return self._options
    def status(self):
        return dict(epoch=self._epoch,origin_epoch=self._origin_epoch,revision=self._revision,phase=self._phase,
                    connected=self._connected,effect_phase=self._effects,buffered_arrivals=len(self._arrivals),
                    buffered_bytes=self._bytes,overflow_count=self._overflow,pause_generation=self._generation)
    def _command(self,epoch,revision,inject=False):
        _natural(epoch,'epoch'); _natural(revision,'revision')
        if not self._connected: raise LiveError('Disconnected')
        if epoch!=self._epoch: raise LiveError('WrongEpoch')
        if revision!=self._revision: raise LiveError('StaleRevision')
        if not (self.options.inject if inject else self.options.pause_and_step): raise LiveError('Unauthorized')
    def terminate(self):
        self._terminal=True
        if self._phase!='Delivering': self._phase='Terminated'
    def request_pause(self,epoch,revision):
        self._command(epoch,revision)
        if self._phase=='Terminated': raise LiveError('WrongPhase')
        if self._phase=='Delivering': self._repause=True
        elif self._phase!='Paused': self._phase='PauseRequested'
    def safe_point(self):
        if any(type(s) is _Permit for s in self._staged): raise LiveError('WrongPhase')
        if self._phase=='PauseRequested':
            if self._generation==U64_MAX: raise LiveError('Exhausted')
            self._generation+=1; self._phase='Paused'
        elif self._phase in ('Running','OneTurnAuthorized'): return None
        elif self._phase!='Paused': raise LiveError('WrongPhase')
        return PauseAck(self._generation,self._epoch,self._revision,self._effects)
    def authorize_one(self,epoch,revision):
        self._command(epoch,revision)
        if self._phase!='Paused': raise LiveError('WrongPhase')
        self._phase='OneTurnAuthorized'
    def resume(self,epoch,revision):
        self._command(epoch,revision)
        if self._phase not in ('Paused','PauseRequested','OneTurnAuthorized'): raise LiveError('WrongPhase')
        self._phase='Running'
    def external_arrival(self,input,owned_bytes):
        _natural(owned_bytes,'owned_bytes')
        if self._phase=='Terminated': raise LiveError('WrongPhase')
        if len(self._arrivals)>=self.options.max_arrivals or owned_bytes>self.options.max_arrival_bytes-self._bytes:
            self._overflow=min(U64_MAX,self._overflow+1)
            if self.options.overflow=='Disconnect': self.disconnect()
            raise LiveError('Overflow')
        self._arrivals.append((input,owned_bytes)); self._bytes+=owned_bytes
    def inject(self,epoch,revision,input,owned_bytes):
        self._command(epoch,revision,True); self.external_arrival(input,owned_bytes)
    def begin_next(self):
        if self._phase not in ('Running','OneTurnAuthorized') or self._effects!='Dispatched': raise LiveError('WrongPhase')
        if not self._arrivals: return None
        if self._serial==U64_MAX or self._revision==U64_MAX: raise LiveError('Exhausted')
        input,size=self._arrivals.popleft(); self._bytes-=size; self._serial+=1
        self._repause=self._phase=='OneTurnAuthorized'; self._phase='Delivering'
        permit=_Permit(self._owner,self._epoch,self._revision,self._serial); self._active=permit
        return permit,input
    def _delivery(self,permit):
        if type(permit) is not _Permit or permit is not self._active or permit._used or permit._owner is not self._owner or self._phase!='Delivering': raise LiveError('WrongPhase')
        permit._used=True; self._active=None
    def delivered(self,permit,outputs):
        _natural(outputs,'outputs'); self._delivery(permit); self._revision+=1
        self._effects='Staged' if outputs else 'Dispatched'
        if outputs>self.options.max_outputs:
            self._phase='Terminated'; raise LiveError('Overflow')
        self._staged=['Staged']*outputs
        self._phase='Terminated' if self._terminal else ('PauseRequested' if self._repause else 'Running'); self._repause=False
    def delivery_failed(self,permit): self._delivery(permit); self._phase='Terminated'
    def take_dispatch(self,index):
        _natural(index,'index')
        if self._phase not in ('Running','PauseRequested','OneTurnAuthorized'): raise LiveError('WrongPhase')
        if index>=len(self._staged): raise LiveError('UnknownEffect')
        if self._staged[index]!='Staged': raise LiveError('Duplicate')
        permit=_Permit(self._owner,self._origin_epoch,self._revision,index)
        self._staged[index]=permit; self._effects='Dispatching'
        return permit
    def _dispatch(self,permit):
        if type(permit) is not _Permit or permit._used or permit._owner is not self._owner or permit._revision!=self._revision or permit._epoch!=self._origin_epoch or permit._serial>=len(self._staged) or self._staged[permit._serial] is not permit: raise LiveError('WrongPhase')
        return permit._serial
    def dispatched(self,permit):
        index=self._dispatch(permit); permit._used=True; self._staged[index]='Dispatched'
        self._effects='Dispatched' if all(s=='Dispatched' for s in self._staged) else ('Dispatching' if any(type(s) is _Permit for s in self._staged) else 'Staged')
    def dispatch_failed(self,permit):
        self._dispatch(permit); permit._used=True; self._phase='Terminated'
    def disconnect(self):
        if not self._connected: return
        self._connected=False
        if self.options.detach=='Terminate': self.terminate()
        elif self._phase=='Delivering': self._repause=self.options.detach=='RemainPaused'
        elif self._phase!='Terminated':
            if self.options.detach=='Resume': self._phase='Running'
            elif self._phase!='Paused': self._phase='PauseRequested'
    def reconnect(self):
        if self._connected or any(type(s) is _Permit for s in self._staged) or self._phase in ('Delivering','Terminated'): raise LiveError('WrongPhase')
        if self._epoch==U64_MAX: raise LiveError('Exhausted')
        self._epoch+=1; self._connected=True; return self._epoch

@dataclass(frozen=True)
class LiveLimits:
    max_events: int = 1024
    max_event_bytes: int = 256*1024
    max_effect_origins: int = 4096
    def __post_init__(self):
        for key in ('max_events','max_effect_origins'): _natural(getattr(self,key),key,1)
        _natural(self.max_event_bytes,'max_event_bytes',128)

class LiveBridge:
    """Capture authentic checked host turns without executing or checking again.

    Metadata events are independent lossy observations. Exact export remains a
    potentially sensitive raw trace. Limits count retained diagnostic records.
    """
    def __init__(self,initial,host_sequence,epoch,config=None,recorder_options=None,limits=LiveLimits()):
        from .checked import _initial_context
        from .monitor import Recorder,RecorderOptions
        from .trace_binary import RunConfig
        _natural(epoch,'epoch',1); _natural(host_sequence,'host_sequence')
        model,state,checks=_initial_context(initial)
        config=RunConfig() if config is None else config
        options=RecorderOptions() if recorder_options is None else recorder_options
        if any(k.startswith('stateless.debug.') for k,v in config.parameters): raise LiveError('reserved provenance')
        params=config.parameters+tuple(('stateless.debug.live.'+key,str(getattr(limits,key))) for key in ('max_events','max_event_bytes','max_effect_origins'))+(('stateless.debug.origin','recorded-live-observation'),('stateless.debug.host_epoch',str(epoch)))
        self._recorder=Recorder.with_checked_initial_at(initial,host_sequence,RunConfig(config.strategy,config.seed,params),options)
        self._metadata=model.metadata(); self._epoch=epoch; self._attached=host_sequence; self._sequence=host_sequence
        self._busy=False; self._stream=0; self._events=deque(); self._limits=limits; self._complete=True
        self._max_checks=options.limits.max_checks_per_step; self._drops=0; self._origins={}; self._evictions=0; self._duplicates=0
        self._publish('Checkpoint',self._summary(checks))
    def _summary(self,checks):
        return dict(failed_checks=sum(c.status=='failed' for c in checks[:self._max_checks]),checks_omitted=max(0,len(checks)-self._max_checks))
    def _publish(self,kind,detail):
        if self._stream==U64_MAX: self._drops=min(U64_MAX,self._drops+1); return
        self._stream+=1
        if len(self._events)>=self._limits.max_events or (len(self._events)+1)*128>self._limits.max_event_bytes:
            self._drops=min(U64_MAX,self._drops+1); return
        self._events.append(dict(epoch=self._epoch,stream_sequence=self._stream,host_sequence=self._sequence,kind=kind,detail=detail))
    def _guard(self):
        if self._busy: raise LiveError('observation already in progress')
    def observe(self,host_sequence,checked):
        self._guard(); self._busy=True
        try: return self._observe(host_sequence,checked)
        finally: self._busy=False
    def _observe(self,host_sequence,checked):
        from .checked import _turn_context
        model,before,input,transition,sequence,checks,boundary,full=_turn_context(checked)
        _natural(host_sequence,'host_sequence')
        if model.metadata()!=self._metadata:
            self._stop_recording('live model identity changed'); raise LiveError('IdentityChanged')
        if sequence!=host_sequence: raise LiveError('SequenceMismatch')
        if host_sequence<=self._sequence:
            self._duplicates=min(U64_MAX,self._duplicates+1); raise LiveError('Duplicate')
        expected=self._sequence+1; gap=host_sequence!=expected; self._sequence=host_sequence
        if gap:
            self._stop_recording('host sequence gap'); self._publish('Gap',dict(expected=expected,actual=host_sequence))
        failed=False
        if self._complete and not self._recorder.is_frozen():
            try: self._recorder.observe_checked(checked)
            except Exception: self._complete=False; failed=True
        if self._recorder.is_frozen() or not self._complete: self._publish('RecordingFrozen',None)
        self._publish('Turn',dict(self._summary(checks),disposition=transition.disposition.kind,full_checking=full,outputs_requested=len(transition.outputs)))
        count=len(transition.outputs); retained=min(count,self._limits.max_effect_origins); skipped=count-retained
        evict=max(0,len(self._origins)-(self._limits.max_effect_origins-retained)); self._evictions=min(U64_MAX,self._evictions+skipped+evict)
        for _ in range(evict): del self._origins[next(iter(self._origins))]
        for i in range(skipped,count): self._origins[EffectOrigin(self._epoch,host_sequence,i)]=False
        if gap: raise LiveError('Gap')
        if failed: raise LiveError('RecordingFailed')
    def effect_dispatched(self,origin):
        self._guard()
        if type(origin) is not EffectOrigin or origin.epoch!=self._epoch: raise LiveError('WrongEpoch')
        if origin not in self._origins: raise LiveError('UnknownEffect')
        if self._origins[origin]: raise LiveError('Duplicate')
        self._origins[origin]=True; self._publish('EffectDispatched',origin)
    def stop_recording(self,error):
        self._guard(); self._busy=True
        try: self._stop_recording(error)
        finally: self._busy=False
    def _stop_recording(self,error):
        from .core import ModelError
        self._complete=False; self._recorder.stop_with_error(ModelError(str(error)))
    def export(self):
        self._guard(); return self._recorder.snapshot()
    def pop_event(self):
        self._guard(); return self._events.popleft() if self._events else None
    def health(self):
        self._guard()
        return dict(epoch=self._epoch,attached_after=self._attached,host_sequence=self._sequence,capture_complete=self._complete,
                    recorder_frozen=self._recorder.is_frozen() or not self._complete,recorded_steps=self._recorder.observed_steps(),
                    retained_steps=self._recorder.retained_steps(),evicted_steps=self._recorder.evicted_steps(),event_drops=self._drops,
                    origin_evictions=self._evictions,duplicate_observations=self._duplicates)
