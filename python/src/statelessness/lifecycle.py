"""Independent legacy and explicitly fenced lifecycle oracles.

Classifiers observe delivered inputs, disposition and outputs only. They must not
consult next application state. Keys must be immutable, ordered values for indexed
history; include generation/epoch when a name denotes a new operation.
"""
from dataclasses import dataclass, field, replace
from .core import Check, Metadata, ModelError, _copy, _natural

@dataclass(frozen=True, order=True)
class ScopedKey:
    epoch: int
    generation: int
    key: object
    def __post_init__(self):
        _natural(self.epoch, 'epoch'); _natural(self.generation, 'generation')

@dataclass(frozen=True)
class Event:
    kind: str
    key: object = None
    time: int = 0
    def __post_init__(self):
        if self.kind not in ('begin','cancel','settle','publish','acquire','release','tick','fence'):
            raise ModelError('invalid lifecycle event')
        _natural(self.time, 'logical time')

@dataclass
class Obligation:
    deadline: int
    cancelled: bool = False
    settled: bool = False
    published: bool = False
    @property
    def pending(self): return not self.cancelled and not self.settled

@dataclass(frozen=True)
class Violation:
    key: object
    code: str
    event: str
    observed_at: int
    prior: object

@dataclass(frozen=True)
class RetentionLimits:
    obligations: int = 1024
    resources: int = 1024
    violations: int = 1024
    def __post_init__(self):
        for k in ('obligations','resources','violations'): _natural(getattr(self,k),k)

@dataclass
class History:
    now: int = 0
    obligations: list = field(default_factory=list)
    resources: list = field(default_factory=list)
    violations: list = field(default_factory=list)
    retired_before: int = 0
    def pending(self): return sum(v.pending for _,v in self.obligations)
    def retention(self):
        active = [(k,v.deadline) for k,v in self.obligations if v.pending]
        return dict(active_obligations=len(active), historical_obligations=len(self.obligations)-len(active),
                    acquired_resources=sum(v for _,v in self.resources),
                    historical_resources=sum(not v for _,v in self.resources),
                    oldest_unresolved=min(active,key=lambda v:v[1],default=None), retired_before=self.retired_before)

_LEGACY = dict(time_backwards='logical time moved backwards',missing_before_advance='required settlement missing before time advance',
 key_reused='correlation key reused',began_after_deadline='obligation began after its deadline',cancel_without_obligation='cancellation without obligation',
 settlement_after_cancel='settlement after cancellation',duplicate_settlement='duplicate settlement',settlement_after_deadline='settlement after deadline',
 settlement_without_obligation='settlement without obligation',stale_or_premature_publication='stale or premature publication',
 resource_already_acquired='resource already acquired',release_before_settlement='resource released before settlement',duplicate_release='duplicate resource release',
 release_without_acquisition='release without acquisition',missing_in_prior_history='required settlement missing in prior history',missing_at_deadline='required settlement missing at deadline')

class LifecycleMonitor:
    """Legacy ordered history; no retirement. Uses the existing WithOracle API."""
    indexed = False
    def __init__(self, spec, max_history=1024, *, key_schema=None):
        self.spec, self.limits, self.key_schema = spec, RetentionLimits(max_history,max_history,max_history),key_schema
    def metadata(self):
        m=self.spec.metadata(); l=self.limits
        name=(f'indexed-lifecycle-v1:{len(m.name.encode())}:{m.name};limits={l.obligations},{l.resources},{l.violations};diagnostics=native-codes-v1' if self.indexed else f'{m.name};history-limit={l.obligations}')
        return replace(m,name=name,build=m.build+(';indexed-lifecycle-v1:native' if self.indexed else ';lifecycle-v1:native'))
    def initial_state(self): return History()
    def validate(self,h):
        _natural(h.now,'now'); _natural(h.retired_before,'fence')
        for name in ('obligations','resources','violations'):
            if len(getattr(h,name))>getattr(self.limits,name): raise ModelError('lifecycle retention limit; verification incomplete')
        if self.indexed:
            for entries in (h.obligations,h.resources):
                keys=[k for k,_ in entries]
                if any(not isinstance(k,ScopedKey) or k.epoch<h.retired_before for k in keys): raise ModelError('detail below retirement fence')
                if any(a>=b for a,b in zip(keys,keys[1:])): raise ModelError('noncanonical indexed map order')
            if any(v.published and not v.settled for _,v in h.obligations): raise ModelError('invalid publication history')
    def _find(self,entries,key):
        if not self.indexed:return next((n for n,(k,_) in enumerate(entries) if k==key),None)
        low,high=0,len(entries)
        while low<high:
            mid=(low+high)//2
            if entries[mid][0]<key:low=mid+1
            else:high=mid
        return low if low<len(entries) and entries[low][0]==key else None
    def _overdue(self,h,t,strict=False):
        return next(((k,v) for k,v in h.obligations if v.pending and (v.deadline<t if strict else v.deadline<=t)),None)
    def _violation(self,h,key,code,event):
        if len(h.violations)>=self.limits.violations: raise ModelError('lifecycle violation limit; verification incomplete')
        n=self._find(h.obligations,key) if key is not None else None
        prior=None if n is None else h.obligations[n][1]
        h.violations.append(Violation(_copy(key),code,event,h.now,_copy(prior)) if self.indexed else _LEGACY[code])
    def _apply(self,h,e):
        if type(e) is not Event: raise ModelError("classifier must return Event")
        kind,key=e.kind,e.key
        def fail(code,k=key): self._violation(h,k,code,kind)
        if kind=='fence':
            if not self.indexed: raise ModelError('legacy history has no fence')
            if e.time<h.retired_before: fail('fence_backwards',None); return
            for k,v in h.obligations:
                if k.epoch<e.time and v.pending: fail('fence_pending_obligation',k); return
            for k,v in h.resources:
                if k.epoch<e.time and v: fail('fence_acquired_resource',k); return
            h.obligations[:]=[(k,v) for k,v in h.obligations if k.epoch>=e.time]
            h.resources[:]=[(k,v) for k,v in h.resources if k.epoch>=e.time]
            h.retired_before=e.time; return
        if self.indexed and kind!='tick':
            if not isinstance(key,ScopedKey): raise ModelError('indexed event requires ScopedKey')
            if key.epoch<h.retired_before: fail('retired_epoch_reference'); return
        n=self._find(h.obligations,key) if kind!='tick' else None
        v=None if n is None else h.obligations[n][1]
        r=self._find(h.resources,key) if kind!='tick' else None
        if kind=='tick':
            if e.time<h.now: fail('time_backwards',None)
            else:
                overdue=self._overdue(h,e.time,True)
                if overdue is not None: fail('missing_before_advance',overdue[0])
                h.now=e.time
        elif kind=='begin':
            if v is not None: fail('key_reused')
            else:
                if len(h.obligations)>=self.limits.obligations: raise ModelError('obligation limit; explicit fence required; verification incomplete')
                h.obligations.append((_copy(key),Obligation(e.time)))
                if self.indexed: h.obligations.sort(key=lambda v:v[0])
                if e.time<h.now: fail('began_after_deadline')
        elif kind=='cancel':
            if v is None: fail('cancel_without_obligation')
            else: v.cancelled=True
        elif kind=='settle':
            if v is None: fail('settlement_without_obligation')
            elif v.cancelled: fail('settlement_after_cancel')
            elif v.settled: fail('duplicate_settlement')
            else:
                # Legacy records late settlement after setting the bit; indexed
                # evidence snapshots the prior obligation before changing it.
                if not self.indexed: v.settled=True
                if v.deadline<h.now: fail('settlement_after_deadline')
                v.settled=True
        elif kind=='publish':
            if v is None or v.cancelled or not v.settled: fail('stale_or_premature_publication')
            elif self.indexed:
                if v.published: fail('duplicate_publication')
                else: v.published=True
        elif kind=='acquire':
            if r is not None:
                if h.resources[r][1]: fail('resource_already_acquired')
                else: h.resources[r]=(_copy(key),True)
            else:
                if len(h.resources)>=self.limits.resources: raise ModelError('resource limit; explicit fence required; verification incomplete')
                h.resources.append((_copy(key),True))
                if self.indexed: h.resources.sort(key=lambda v:v[0])
        elif kind=='release':
            if v is not None and v.pending: fail('release_before_settlement')
            if r is None: fail('release_without_acquisition')
            elif not h.resources[r][1]: fail('duplicate_release')
            else: h.resources[r]=(_copy(key),False)
    def advance(self,before,input,outputs,disposition):
        self.validate(before); h=_copy(before)
        k=self._overdue(h,h.now)
        if k is not None: self._violation(h,k[0],'missing_in_prior_history','observation')
        # Materialize both classifiers before processing, as Rust does.
        events=list(self.spec.input_events(_copy(input),disposition))+list(self.spec.output_events(_copy(outputs)))
        for e in events: self._apply(h,e)
        k=self._overdue(h,h.now)
        if k is not None: self._violation(h,k[0],'missing_at_deadline','observation')
        return h
    def check_state(self,h,actual=None):
        self.validate(h); prefix='lifecycle.indexed' if self.indexed else 'lifecycle'
        bad=bool(h.violations) or self._overdue(h,h.now) is not None
        return (Check(prefix+'.safety','failed','; '.join(v.code for v in h.violations) or 'missing_at_deadline' if self.indexed else '; '.join(h.violations) or _LEGACY['missing_at_deadline']) if bad else Check(prefix+'.safety'),
                Check(prefix+'.obligations','skipped',f'{h.pending()} pending obligations in finite history') if h.pending() else Check(prefix+'.obligations'))
    def encode_history(self,h):
        from .value_codec import encode_canonical
        if self.indexed: self.validate(h)
        return encode_canonical(self._schema(),self._wire(h))
    def decode_history(self,b):
        from .value_codec import decode_canonical
        v=decode_canonical(self._schema(),b)
        if self.indexed:
            version,now,fence,(obs,res,violations)=v
            if version!=1: raise ModelError('indexed lifecycle codec version')
            key=lambda k: ScopedKey(*k)
            h=History(now,[(key(k),Obligation(*o)) for k,o in obs],[(key(k),r) for k,r in res],
                [Violation(None if k is None else key(k.value),c,e,t,None if p is None else Obligation(*p.value)) for k,c,e,(t,p) in violations],fence)
        else:
            now,obs,res,violations=v
            h=History(now,[(k,Obligation(d,c,s)) for k,d,c,s in obs],list(res),list(violations))
        self.validate(h); return h
    def _schema(self):
        from .value_codec import U8,U64,BOOL,STRING,TupleOf,VectorOf as Vec,OptionOf as Option,Rust64Layout
        def Tuple(*fields): return TupleOf(*fields,layout=Rust64Layout(weight))
        k=self.key_schema
        if k is None or k.allocation_layout is None: raise ModelError('explicit key schema and allocation layout required')
        weight=k.allocation_layout.size+256
        if not self.indexed: return Tuple(U64,Vec(Tuple(k,U64,BOOL,BOOL)),Vec(Tuple(k,BOOL)),Vec(STRING))
        k=Tuple(U64,U64,k); o=Tuple(U64,BOOL,BOOL,BOOL)
        return Tuple(U8,U64,U64,Tuple(Vec(Tuple(k,o)),Vec(Tuple(k,BOOL)),Vec(Tuple(Option(k),STRING,STRING,Tuple(U64,Option(o))))))
    def _wire(self,h):
        if not self.indexed: return (h.now,[(k,v.deadline,v.cancelled,v.settled) for k,v in h.obligations],h.resources,h.violations)
        key=lambda k:(k.epoch,k.generation,k.key)
        ob=lambda o:(o.deadline,o.cancelled,o.settled,o.published)
        from .value_codec import Some
        return (1,h.now,h.retired_before,([(key(k),ob(v)) for k,v in h.obligations],[(key(k),v) for k,v in h.resources],
            [(None if v.key is None else Some(key(v.key)),v.code,v.event,(v.observed_at,None if v.prior is None else Some(ob(v.prior)))) for v in h.violations]))

class IndexedLifecycleMonitor(LifecycleMonitor):
    indexed=True
    def __init__(self,spec,limits=RetentionLimits(),*,key_schema=None):
        self.spec,self.limits,self.key_schema=spec,limits,key_schema
