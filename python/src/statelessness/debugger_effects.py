"""Optional host effect lifecycle observations, with explicit clock provenance.

Private token fields are API integrity boundaries, not a reflection sandbox.
Hooks record host facts; cancellation does not terminate physical work. Tokens
are local capabilities. Retention bounds cover diagnostic records, not RSS or
application allocations. Call publication reservation under the host queue lock.
"""
from collections import deque
from dataclasses import dataclass, field, replace
from copy import deepcopy
from threading import RLock, get_ident
from weakref import ref
from .core import _natural, U64_MAX

class LifecycleError(ValueError): pass
class MeasurementError(ValueError): pass

@dataclass(frozen=True)
class LocalInstant:
    domain: int
    nanos: int
    def __post_init__(self):
        _natural(self.domain,'clock domain'); _natural(self.nanos,'nanoseconds')
    def elapsed_since(self,start):
        if type(start) is not LocalInstant: raise MeasurementError('MissingEndpoint')
        if self.domain!=start.domain: raise MeasurementError('DifferentClockDomain')
        if self.nanos<start.nanos: raise MeasurementError('Reversed')
        return self.nanos-start.nanos

class MonotonicClock:
    """Explicitly constructed monotonic clock with a host chosen domain."""
    def __init__(self,domain):
        _natural(domain,'clock domain')
        import time
        self._domain=domain; self._read=time.monotonic_ns; self._start=self._read()
    def now(self): return LocalInstant(self._domain,self._read()-self._start)

@dataclass(frozen=True)
class EffectId:
    run: int
    epoch: int
    serial: int
    def __post_init__(self):
        for name in ('run','epoch','serial'): _natural(getattr(self,name),name)

@dataclass(frozen=True)
class AttemptId:
    effect: EffectId
    serial: int
    def __post_init__(self):
        if type(self.effect) is not EffectId: raise ValueError('expected EffectId')
        _natural(self.serial,'serial')

@dataclass(frozen=True)
class DeliveryId:
    effect: EffectId
    serial: int
    def __post_init__(self):
        if type(self.effect) is not EffectId: raise ValueError('expected EffectId')
        _natural(self.serial,'serial')

@dataclass(frozen=True)
class RequestOrigin:
    run: int
    epoch: int
    machine: int
    transition_sequence: int
    output_index: int
    def __post_init__(self):
        for name in ('run','epoch','machine','transition_sequence','output_index'): _natural(getattr(self,name),name)
        if self.output_index>0xffffffff: raise ValueError('output_index exceeds u32')

@dataclass(frozen=True)
class EffectOptions:
    run: int = 1
    epoch: int = 1
    origin: str = 'Live'
    counters: bool = True
    timings: bool = True
    details: bool = True
    max_details: int = 4096
    max_detail_bytes: int = 8*1024*1024
    max_events_per_effect: int = 128
    max_attempts_per_effect: int = 1024
    max_deliveries_per_effect: int = 1024
    max_pause_intervals: int = 64
    labels: object = None
    histogram: object = None
    metric_limits: object = None
    gauge_scope: str = 'ObservedSinceAttach'
    def __post_init__(self):
        for name in ('run','epoch','max_events_per_effect','max_attempts_per_effect','max_deliveries_per_effect','max_pause_intervals'): _natural(getattr(self,name),name,1)
        for name in ('max_details','max_detail_bytes'): _natural(getattr(self,name),name)
        for name in ('counters','timings','details'):
            if type(getattr(self,name)) is not bool: raise ValueError('expected bool '+name)
        if self.origin not in ('Live','DebugControlledLive','TestClock','ReplayHarness','ImportedLive'): raise ValueError('invalid measurement origin')

@dataclass(frozen=True)
class LifecycleEvent:
    sequence: int
    at: LocalInstant | None
    kind: str
    detail: object
    debugger_affected: bool

@dataclass(frozen=True)
class TimingObservation:
    family: object
    duration_ns: int | None
    error: str | None
    attempt: AttemptId | None = None
    delivery: DeliveryId | None = None
    outcome: str = 'None'
    debugger_affected: bool = False

@dataclass
class EffectDetails:
    id: EffectId
    origin: RequestOrigin
    labels: object
    requested: LocalInstant | None
    admission: str | None = None
    resolution: str | None = None
    settled: bool = False
    cancellation_requested: bool = False
    cancellation_acknowledged: bool = False
    running_attempts: int = 0
    deliveries_observed: int = 0
    debugger_affected: bool = False
    abandoned: bool = False
    complete: bool = True
    events: list = field(default_factory=list)
    timings: list = field(default_factory=list)
    def pending_age(self,now):
        if self.resolution is not None or self.admission=='Rejected': return None
        if self.requested is None: raise MeasurementError('MissingEndpoint')
        return now.elapsed_since(self.requested)

class _Handle:
    def __copy__(self): return self
    def __deepcopy__(self,memo): return self
    def __reduce_ex__(self,protocol): raise TypeError('lifecycle tokens cannot be serialized as authority')
    def __del__(self):
        try:
            effect=self if type(self) is _Effect else self.effect
            observer=effect._observer()
            if observer is not None: observer._drop_handle(self)
        except Exception:
            pass

class _Effect(_Handle):
    def __init__(self,owner,id,origin,labels,at,generation,paused):
        self.owner=owner; self.id=id; self.origin=origin; self.labels=labels; self.requested=at
        self._observer=lambda:None; self.admission=None; self.resolution=None; self.resolved_at=None; self.settled=False
        self.cancel_requested=False; self.cancel_ack=False; self.attempts=0; self.deliveries=0
        self.running=0; self.first_ready=False; self.first_delivery=False; self.observed=0
        self.last_finish=None; self.generation=generation; self.affected=paused; self.abandoned=False

class _Attempt(_Handle):
    def __init__(self,effect,serial):
        self.effect=effect; self.id=AttemptId(effect.id,serial); self.ready=None; self.queued=False
        self.started=False; self.start=None; self.finished=False; self.finish=None
        self.scheduled=None; self.has_schedule=False; self.abandoned=False; self.previous_finish=effect.last_finish

class _Delivery(_Handle):
    def __init__(self,effect,serial,posted):
        self.effect=effect; self.id=DeliveryId(effect.id,serial); self.posted=posted
        self.confirmed=False; self.failed=False; self.begun=False; self.begin=None; self.observed=False

class EffectObserver:
    def __init__(self,options,clock):
        from .debugger_metrics import MetricsStore,LabelCatalog,HistogramSchema,MetricLimits,SnapshotTime
        if type(options) is not EffectOptions: raise ValueError('expected EffectOptions')
        for name,kind in (('labels',LabelCatalog),('histogram',HistogramSchema),('metric_limits',MetricLimits)):
            if getattr(options,name) is not None and type(getattr(options,name)) is not kind: raise ValueError('invalid '+name)
        self._options=options; self._clock=clock; self._owner=object(); self._lock=RLock()
        self._clock_thread=None; self._active_handles=0; self._reported_drop_losses=0; self._serial=0; self._sequence=0; self._generation=0; self._paused=False; self._pauses=deque()
        self._details=deque(); self._detail_bytes=0
        self._health={key:0 for key in ('clock_failures','detail_evictions','detail_rejections','detail_event_drops','duplicate_observations','duplicate_deliveries','invalid_lifecycle','abandoned','callback_faults','pause_interval_evictions','destructor_hook_losses','debugger_affected_measurements')}
        at=self._now()
        start=None if at is None else SnapshotTime(at.domain,at.nanos)
        self._metrics=MetricsStore(options.epoch,start,options.labels or LabelCatalog(),options.histogram or HistogramSchema(),options.metric_limits or MetricLimits(),options.gauge_scope)
    @property
    def options(self): return self._options
    def _now(self):
        if not self.options.timings: return None
        if self._clock_thread==get_ident(): raise LifecycleError('ReentrantClock')
        self._clock_thread=get_ident()
        try:
            at=self._clock.now() if hasattr(self._clock,'now') else self._clock()
            if type(at) is not LocalInstant: raise MeasurementError('ClockUnavailable')
            return at
        except Exception:
            self._health['clock_failures']+=1; return None
        finally: self._clock_thread=None
    def _same(self,token,kind=None):
        if self._clock_thread==get_ident(): raise LifecycleError('ReentrantClock')
        if type(token) not in (_Effect,_Attempt,_Delivery) or (kind is not None and type(token) is not kind): raise LifecycleError('ForeignToken')
        e=token if type(token) is _Effect else token.effect
        if type(e) is not _Effect or e.owner is not self._owner: raise LifecycleError('ForeignToken')
        return e
    def _fail(self,duplicate=False,e=None):
        self._health['duplicate_observations' if duplicate else 'invalid_lifecycle']+=1
        if duplicate and e is not None: self._count('DUPLICATE_OBSERVATIONS',e)
        raise LifecycleError('DuplicateObservation' if duplicate else 'WrongPhase')
    def _f(self,name):
        from .debugger_metrics import MetricFamily
        return getattr(MetricFamily,name)
    def _count(self,name,e,outcome='None'):
        if self.options.counters: self._metrics.increment(self._f(name),replace(e.labels,outcome=outcome),1)
    def _gauge(self,name,e,delta):
        if self.options.counters: self._metrics.gauge_delta(self._f(name),e.labels,delta)
    def _detail(self,e): return next((d for d in self._details if d.id==e.id),None)
    def _affected(self,e):
        e.affected |= self._paused or e.generation!=self._generation
    def _event(self,e,kind,detail=None,at=None):
        self._affected(e)
        if not self.options.details: return
        self._sequence=min(U64_MAX,self._sequence+1)
        d=self._detail(e)
        if d is None: return
        d.admission=e.admission; d.resolution=e.resolution; d.settled=e.settled
        d.cancellation_requested=e.cancel_requested; d.cancellation_acknowledged=e.cancel_ack
        d.running_attempts=e.running; d.deliveries_observed=e.observed; d.debugger_affected=e.affected; d.abandoned=e.abandoned
        d.complete &= not e.abandoned
        if len(d.events)>=self.options.max_events_per_effect or self._detail_bytes+128>self.options.max_detail_bytes:
            d.complete=False; self._health['detail_event_drops']+=1; return
        d.events.append(LifecycleEvent(self._sequence,at,kind,detail,e.affected)); self._detail_bytes+=128
    def _measure(self,e,name,start,end,attempt=None,delivery=None,outcome='None'):
        if not self.options.timings: return
        self._affected(e); error=None; duration=None
        try:
            if start is None or end is None: raise MeasurementError('MissingEndpoint')
            duration=end.elapsed_since(start)
        except MeasurementError as err:
            error=str(err); self._metrics.record_health('omitted_measurements' if error=='MissingEndpoint' else 'invalid_measurements'); self._metrics.mark_incomplete()
        family=self._f(name)
        labels=replace(e.labels,outcome=outcome,debugger_affected=e.affected)
        if error is not None and error!='MissingEndpoint' and self.options.counters: self._metrics.increment(self._f('INVALID_MEASUREMENTS'),labels,1)
        if duration is not None:
            self._metrics.observe(family,labels,duration)
            if e.affected: self._health['debugger_affected_measurements']+=1
        d=self._detail(e)
        if d is not None:
            if len(d.timings)>=self.options.max_events_per_effect or self._detail_bytes+128>self.options.max_detail_bytes:
                d.complete=False; self._health['detail_event_drops']+=1
            else:
                d.timings.append(TimingObservation(family,duration,error,attempt,delivery,outcome,e.affected)); self._detail_bytes+=128
                if error: d.complete=False
    def requested(self,origin,labels=None):
        from .debugger_metrics import Labels
        if self._clock_thread==get_ident(): raise LifecycleError('ReentrantClock')
        if type(origin) is not RequestOrigin or origin.run!=self.options.run or origin.epoch!=self.options.epoch: raise LifecycleError('WrongEpoch')
        labels=Labels() if labels is None else labels
        if type(labels) is not Labels: raise LifecycleError('InvalidLabels')
        labels=replace(labels,origin=self.options.origin,outcome='None',debugger_affected=False)
        if not self._metrics.labels_valid(labels): raise LifecycleError('InvalidLabels')
        with self._lock:
            if self._serial==U64_MAX: raise LifecycleError('Exhausted')
            self._serial+=1; at=self._now(); e=_Effect(self._owner,EffectId(origin.run,origin.epoch,self._serial),origin,labels,at,self._generation,self._paused)
            e._observer=ref(self); self._active_handles+=1
            self._count('REQUESTS',e)
            if self.options.details:
                if not self.options.max_details or self.options.max_detail_bytes<512: self._health['detail_rejections']+=1
                else:
                    while self._details and (len(self._details)>=self.options.max_details or self._detail_bytes+512>self.options.max_detail_bytes):
                        old=self._details.popleft(); self._detail_bytes-=512+128*(len(old.events)+len(old.timings)); self._health['detail_evictions']+=1
                    self._details.append(EffectDetails(e.id,origin,labels,at)); self._detail_bytes+=512
            self._event(e,'Requested',at=at); return e
    def admission(self,e,admission):
        self._same(e,_Effect)
        if admission not in ('Accepted','Rejected'): raise ValueError('invalid admission')
        with self._lock:
            if e.admission is not None: self._fail(True,e)
            if e.resolution is not None: self._fail()
            e.admission=admission; self._count('ADMISSIONS',e,admission)
            if admission=='Accepted': self._gauge('UNRESOLVED',e,1)
            self._event(e,'Admission',admission,self._now())
    def attempt_created(self,e):
        self._same(e,_Effect)
        with self._lock:
            if e.admission!='Accepted' or e.resolution is not None: self._fail()
            if e.attempts>=self.options.max_attempts_per_effect: raise LifecycleError('Limit')
            e.attempts+=1; a=_Attempt(e,e.attempts); self._event(e,'AttemptCreated',a.id); return a
    def retry_scheduled(self,a,delay_ns):
        e=self._same(a,_Attempt); _natural(delay_ns,'delay_ns')
        with self._lock:
            if a.started or a.queued or a.has_schedule or a.abandoned or e.resolution is not None: self._fail()
            a.scheduled=self._now(); a.has_schedule=True
            if self.options.timings:
                self._affected(e); self._metrics.observe(self._f('SCHEDULED_BACKOFF'),replace(e.labels,debugger_affected=e.affected),delay_ns)
                if e.affected: self._health['debugger_affected_measurements']+=1
            self._event(e,'RetryScheduled',(a.id,delay_ns),a.scheduled)
    def ready_queued(self,a):
        e=self._same(a,_Attempt)
        with self._lock:
            if a.queued: self._fail(True,e)
            if a.started or a.finished or a.abandoned or e.resolution is not None: self._fail()
            at=self._now(); a.queued=True; a.ready=at; self._gauge('READY_DEPTH',e,1)
            if not e.first_ready: self._measure(e,'ADMISSION_DELAY',e.requested,at,a.id); e.first_ready=True
            if a.has_schedule: self._measure(e,'ELIGIBILITY_WAIT',a.scheduled,at,a.id)
            self._event(e,'Ready',a.id,at)
    def ready_removed(self,a):
        e=self._same(a,_Attempt)
        with self._lock:
            if not a.queued or a.started: self._fail()
            a.queued=False; self._gauge('READY_DEPTH',e,-1)
    def attempt_started(self,a):
        e=self._same(a,_Attempt)
        with self._lock:
            if a.started: self._fail(True,e)
            if a.finished or a.abandoned or e.resolution is not None: self._fail()
            a.start=self._now(); a.started=True; e.running+=1
            self._count('ATTEMPTS',e); self._gauge('RUNNING',e,1)
            if a.queued:
                a.queued=False; self._gauge('READY_DEPTH',e,-1); self._measure(e,'QUEUE_WAIT',a.ready,a.start,a.id)
            if a.id.serial>1: self._measure(e,'INTER_ATTEMPT_GAP',a.previous_finish,a.start,a.id)
            self._event(e,'AttemptStarted',a.id,a.start)
    def attempt_finished(self,a,outcome):
        e=self._same(a,_Attempt); self._outcome(outcome)
        with self._lock:
            if a.finished: self._fail(True,e)
            if not a.started: self._fail()
            a.finish=self._now(); a.finished=True; e.last_finish=a.finish; e.running-=1
            self._count('ATTEMPT_OUTCOMES',e,self._label(outcome)); self._gauge('RUNNING',e,-1)
            self._measure(e,'ATTEMPT_ELAPSED',a.start,a.finish,a.id,outcome=self._label(outcome)); self._event(e,'AttemptFinished',(a.id,outcome),a.finish)
    @staticmethod
    def _outcome(value):
        if value not in ('Unclassified','Success','Failure','Cancelled'): raise ValueError('invalid runtime outcome')
    @staticmethod
    def _label(value): return 'None' if value=='Unclassified' else value
    def resolved(self,e,outcome,selected_attempt=None):
        self._same(e,_Effect); self._outcome(outcome)
        with self._lock:
            if selected_attempt is not None and (self._same(selected_attempt,_Attempt) is not e or not selected_attempt.finished): self._fail()
            if e.resolution is not None: self._fail(True,e)
            if e.admission!='Accepted': self._fail()
            at=self._now(); e.resolution=outcome; e.resolved_at=at; label=self._label(outcome)
            self._count('RESOLUTIONS',e,label); self._gauge('UNRESOLVED',e,-1); self._measure(e,'RESOLUTION',e.requested,at,outcome=label)
            if selected_attempt: self._measure(e,'RESOLUTION_OVERHEAD',selected_attempt.finish,at,selected_attempt.id,outcome=label)
            self._event(e,'Resolved',outcome,at)
    def cancellation_requested(self,e):
        self._same(e,_Effect)
        with self._lock:
            if e.cancel_requested: self._fail(True,e)
            e.cancel_requested=True; self._count('CANCELLATION_REQUESTS',e); self._event(e,'CancellationRequested',at=self._now())
    def cancellation_acknowledged(self,e):
        self._same(e,_Effect)
        with self._lock:
            if e.cancel_ack: self._fail(True,e)
            if not e.cancel_requested: self._fail()
            e.cancel_ack=True; self._count('CANCELLATION_ACKNOWLEDGEMENTS',e); self._event(e,'CancellationAcknowledged',at=self._now())
    def reserve_publication(self,e):
        self._same(e,_Effect)
        with self._lock:
            if e.resolution is None: self._fail()
            if e.deliveries>=self.options.max_deliveries_per_effect: raise LifecycleError('Limit')
            e.deliveries+=1; d=_Delivery(e,e.deliveries,self._now()); self._gauge('COMPLETION_DEPTH',e,1)
            self._event(e,'PublicationReserved',d.id,d.posted); return d
    def publication_committed(self,d):
        e=self._same(d,_Delivery)
        with self._lock:
            if d.failed: self._fail()
            if not d.confirmed:
                d.confirmed=True; self._measure(e,'COMPLETION_PREPARATION',e.resolved_at,d.posted,delivery=d.id)
    def publication_failed(self,d):
        e=self._same(d,_Delivery)
        with self._lock:
            if d.failed: self._fail(True,e)
            if d.begun or d.confirmed: self._fail()
            d.failed=True; self._gauge('COMPLETION_DEPTH',e,-1); self._event(e,'PublicationFailed',d.id,self._now())
    def delivery_begun(self,d):
        e=self._same(d,_Delivery)
        with self._lock:
            if d.begun: self._fail(True,e)
            if d.failed: self._fail()
            at=self._now(); self.publication_committed(d); d.begun=True; d.begin=at; self._gauge('COMPLETION_DEPTH',e,-1)
            self._measure(e,'COMPLETION_QUEUE_WAIT',d.posted,at,delivery=d.id); self._measure(e,'DELIVERY_LAG',e.resolved_at,at,delivery=d.id)
            if not e.first_delivery: self._measure(e,'END_TO_END',e.requested,at,delivery=d.id); e.first_delivery=True
            self._event(e,'DeliveryBegun',d.id,at)
    def delivery_observed(self,d,disposition):
        e=self._same(d,_Delivery)
        if disposition not in ('Accepted','Rejected','Ignored'): raise ValueError('invalid disposition')
        with self._lock:
            if d.observed: self._fail(True,e)
            if not d.begun or d.failed: self._fail()
            at=self._now(); d.observed=True; self._count('DELIVERIES',e,disposition)
            if e.observed: self._count('DUPLICATE_DELIVERIES',e); self._health['duplicate_deliveries']+=1
            else: self._measure(e,'END_TO_END_OBSERVED',e.requested,at,delivery=d.id,outcome=disposition)
            e.observed+=1; self._measure(e,'COMPLETION_PROCESSING',d.begin,at,delivery=d.id,outcome=disposition); self._event(e,'DeliveryObserved',(d.id,disposition),at)
    def settled(self,e):
        self._same(e,_Effect)
        with self._lock:
            if e.settled: self._fail(True,e)
            at=self._now(); e.settled=True; self._measure(e,'SETTLEMENT',e.requested,at); self._event(e,'Settled',at=at)
    def attempt_abandoned(self,a):
        e=self._same(a,_Attempt)
        with self._lock:
            if a.abandoned or a.finished: return
            a.abandoned=True; e.abandoned=True; self._metrics.mark_incomplete(); self._health['abandoned']+=1; self._count('ABANDONED',e); self._event(e,'Abandoned',a.id)
    def debugger_pause(self,paused):
        if type(paused) is not bool: raise ValueError('expected bool')
        with self._lock:
            if self._clock_thread==get_ident(): raise LifecycleError('ReentrantClock')
            if self._paused==paused: return
            at=self._now(); self._paused=paused
            self._generation=min(U64_MAX,self._generation+1)
            if paused:
                if len(self._pauses)>=self.options.max_pause_intervals: self._pauses.popleft(); self._health['pause_interval_evictions']+=1
                self._pauses.append(dict(generation=self._generation,start=at,end=None))
            elif self._pauses: self._pauses[-1]['end']=at
    def effect_details(self,id):
        if type(id) is not EffectId: raise ValueError('expected EffectId')
        with self._lock: return deepcopy(next((d for d in self._details if d.id==id),None))
    def effect_timeline(self,id):
        d=self.effect_details(id)
        if d is None: return None
        return dict(effect=d.id,origin=d.origin,measurement_origin=self.options.origin,complete=d.complete,events=d.events,timings=d.timings)
    def pause_intervals(self):
        with self._lock: return deepcopy(list(self._pauses))
    def _flush_losses(self):
        delta=self._health['destructor_hook_losses']-self._reported_drop_losses
        if delta:
            self._metrics.record_health('omitted_measurements',delta); self._metrics.mark_incomplete()
            self._reported_drop_losses+=delta
    def telemetry_health(self):
        with self._lock:
            self._flush_losses()
            return dict(self._health,metric=self._metrics.health,active_effect_handles=self._active_handles,retained_detail_records=len(self._details),retained_detail_bytes=self._detail_bytes,unobserved_native_debugger_interference_possible=True)
    def metric_snapshot(self):
        from .debugger_metrics import SnapshotTime
        with self._lock:
            revision=self._metrics.revision; at=self._now()
            if self._metrics.revision!=revision:
                at=None; self._metrics.record_health('omitted_measurements'); self._metrics.mark_incomplete()
            self._flush_losses()
            return self._metrics.snapshot(None if at is None else SnapshotTime(at.domain,at.nanos))
    def metric_window(self,from_revision,to_revision):
        with self._lock: return self._metrics.window(from_revision,to_revision)
    def available_windows(self):
        with self._lock: return self._metrics.available_windows()
    def _drop_handle(self,token):
        e=token if type(token) is _Effect else token.effect
        if e.owner is not self._owner: return
        if type(token) is _Effect:
            self._active_handles=max(0,self._active_handles-1)
            if e.admission=='Rejected' or e.resolution is not None or e.abandoned: return
        elif type(token) is _Attempt:
            if token.finished or token.abandoned: return
        elif token.failed or token.observed: return
        if not self._lock.acquire(blocking=False):
            self._health['destructor_hook_losses']+=1
            return
        try:
            e.abandoned=True
            if type(token) is _Attempt: token.abandoned=True
            self._health['abandoned']+=1; self._metrics.mark_incomplete()
            self._count('ABANDONED',e); self._event(e,'Abandoned',token.id if type(token) is _Attempt else None)
        finally: self._lock.release()
    def host_gauge_snapshot(self,labels,unresolved,running,ready,completion):
        labels=replace(labels,origin=self.options.origin,outcome='None',debugger_affected=False)
        if not self._metrics.labels_valid(labels): raise LifecycleError('InvalidLabels')
        for n in (unresolved,running,ready,completion): _natural(n,'host gauge')
        with self._lock:
            if self.options.counters:
                for family,n in zip(('UNRESOLVED','RUNNING','READY_DEPTH','COMPLETION_DEPTH'),(unresolved,running,ready,completion)):
                    self._metrics.set_gauge(self._f(family),labels,n)
                    self._metrics.set_gauge_scope_for(labels,'HostSnapshot')
    def metric_catalog(self):
        from .debugger_metrics import metric_catalog
        return metric_catalog()
    def slow_timings(self,query):
        from .debugger_metrics import MetricFamily
        if type(query) is not SlowTimingQuery: raise ValueError('expected SlowTimingQuery')
        family=MetricFamily(query.family)
        if not 0<=query.operation<len(self._metrics.current().labels.operations): raise LifecycleError('InvalidLabels')
        if family.value not in ('QueueWait','AttemptElapsed','Resolution','DeliveryLag','EndToEnd','CompletionProcessing','AdmissionDelay','ResolutionOverhead','CompletionPreparation','CompletionQueueWait','EndToEndObserved','Settlement','ScheduledBackoff','EligibilityWait','InterAttemptGap'): raise LifecycleError('WrongPhase')
        with self._lock:
            availability='TimingDisabled' if not self.options.timings else ('DetailsDisabled' if not self.options.details else 'Available')
            result=dict(run=self.options.run,epoch=self.options.epoch,detail_revision=self._sequence,query=query,availability=availability,matches=[],matched=0,inspected=0,unknown_measurements=0,result_bytes=256,truncated=False,complete=availability=='Available' and not any(self._health[k] for k in ('detail_evictions','detail_rejections','detail_event_drops','destructor_hook_losses')),retained_detail_only=True)
            if availability!='Available': return result
            for d in self._details:
                if d.labels.operation!=query.operation or (query.measurement_origin is not None and d.labels.origin!=query.measurement_origin): continue
                result['complete'] &= d.complete
                for t in d.timings:
                    if t.family!=family or (t.debugger_affected and not query.include_debugger_affected) or (query.outcome is not None and t.outcome!=query.outcome): continue
                    result['inspected']+=1
                    if t.duration_ns is None:
                        result['unknown_measurements']+=1; result['complete']=False; continue
                    if t.duration_ns<query.threshold_ns: continue
                    result['matched']+=1
                    if len(result['matches'])>=query.max_results or result['result_bytes']+256>query.max_result_bytes:
                        result['truncated']=True; result['complete']=False; continue
                    result['matches'].append(dict(effect=d.id,origin=d.origin,labels=replace(d.labels,outcome=t.outcome,debugger_affected=t.debugger_affected),timing=t)); result['result_bytes']+=256
            return deepcopy(result)
    def observe_awaitable(self,attempt,factory,classify):
        """Create a lazy, once-only factory adapter with honest abandonment.

        Start is the host boundary immediately before invoking the factory.
        Already scheduled Task/Future objects are rejected. Closing/dropping the
        adapter does not assert that remote work or executor threads terminated.
        """
        if not callable(factory): raise TypeError('expected deferred operation factory')
        return ObservedAwaitable(self,attempt,factory,classify)

class ObservedAwaitable:
    def __init__(self,observer,attempt,factory,classify):
        self._observer=observer; self._attempt=attempt; self._factory=factory; self._classify=classify
        self._claimed=False; self._finished=False; self._closed=False
    def __await__(self):
        if self._claimed or self._closed: raise LifecycleError('adapter already consumed')
        self._claimed=True
        return self._run().__await__()
    async def _run(self):
        o=self._observer
        try: o.attempt_started(self._attempt)
        except Exception: pass
        try: value=await self._factory()
        except BaseException:
            try: self.close()
            except Exception: pass
            raise
        try:
            outcome=self._classify(value); o._outcome(outcome)
        except Exception:
            o._health['callback_faults']+=1; o._metrics.mark_incomplete(); outcome='Unclassified'
        try: o.attempt_finished(self._attempt,outcome)
        except Exception: pass
        self._finished=True
        return value
    def close(self):
        if not self._finished and not self._closed:
            self._closed=True; self._observer.attempt_abandoned(self._attempt)
    def __del__(self):
        try:
            if not self._finished and not self._closed: self._observer._drop_handle(self._attempt)
        except Exception: pass


@dataclass(frozen=True)
class SlowTimingQuery:
    operation: int
    family: object
    threshold_ns: int
    measurement_origin: str | None = 'Live'
    outcome: str | None = None
    include_debugger_affected: bool = False
    max_results: int = 64
    max_result_bytes: int = 64*1024
    def __post_init__(self):
        _natural(self.operation,'operation'); _natural(self.threshold_ns,'threshold')
        _natural(self.max_results,'max_results'); _natural(self.max_result_bytes,'max_result_bytes',256)
        if self.max_results>4096 or self.max_result_bytes>4*1024*1024: raise LifecycleError('Limit')
        if self.measurement_origin not in (None,'Live','DebugControlledLive','TestClock','ReplayHarness','ImportedLive'): raise ValueError('invalid measurement origin')
        if self.outcome not in (None,'None','Accepted','Rejected','Success','Failure','Cancelled','Abandoned','Ignored'): raise ValueError('invalid outcome')
        if type(self.include_debugger_affected) is not bool: raise ValueError('expected bool')

def effect_protocol_adapters(observer,*,max_page_size=100):
    """Passive typed local bindings for ProtocolSession(adapters=...).

    Read retained details only; never read a clock or dispatch application work.
    ProtocolSession owns wire quotas, request retirement and reply admission.
    """
    from .debugger_protocol import ProtocolError
    _natural(max_page_size,'max_page_size',1)
    if max_page_size>100: raise ValueError('page limit exceeds protocol maximum')
    def n(value): return 'u64:'+str(value)
    def b(value): return 'bool:'+str(value).lower()
    def snake(value): return ''.join(('_'+c.lower() if i and c.isupper() else c.lower()) for i,c in enumerate(value))
    def instant(prefix,at,reason='clock'):
        if at is None: return ((prefix,'unknown:'+reason),)
        return ((prefix+'.clock_domain',n(at.domain)),(prefix+'.nanos',n(at.nanos)))
    def lookup(args):
        d=observer.effect_details(EffectId(*args[:3]))
        if d is None: raise ProtocolError('stale_handle')
        return d
    def summary(d):
        fields=[('effect.run',n(d.id.run)),('effect.epoch',n(d.id.epoch)),('effect.serial',n(d.id.serial)),
            ('effect.origin.run',n(d.origin.run)),('effect.origin.epoch',n(d.origin.epoch)),('effect.origin.machine',n(d.origin.machine)),
            ('effect.origin.transition',n(d.origin.transition_sequence)),('effect.origin.output_index',n(d.origin.output_index)),
            ('effect.operation.id',n(d.labels.operation)),('effect.component.id',n(d.labels.component)),('effect.worker_pool.id',n(d.labels.worker_pool)),
            ('effect.outcome','enum:'+d.labels.outcome),('effect.measurement_origin','enum:'+d.labels.origin),('effect.labels.debugger_affected',b(d.labels.debugger_affected)),('effect.complete',b(d.complete)),
            ('effect.admission','unknown' if d.admission is None else 'enum:'+d.admission),('effect.resolution','unknown' if d.resolution is None else 'enum:'+d.resolution),
            ('effect.settled',b(d.settled)),('effect.running_attempts',n(d.running_attempts)),('effect.deliveries_observed',n(d.deliveries_observed)),
            ('effect.cancellation_requested',b(d.cancellation_requested)),('effect.cancellation_acknowledged',b(d.cancellation_acknowledged)),
            ('effect.debugger_affected',b(d.debugger_affected)),('effect.abandoned',b(d.abandoned))]
        fields.extend(instant('effect.requested',d.requested)); return fields
    def details(args):
        if len(args)!=3: raise ProtocolError('malformed')
        return tuple(summary(lookup(args)))
    def timeline(args):
        if len(args)!=4: raise ProtocolError('malformed')
        limit=args[3]; _natural(limit,'page limit',1)
        if limit>max_page_size: raise ProtocolError('limit')
        d=lookup(args); fields=summary(d)
        fields.extend((('timeline.events.total',n(len(d.events))),('timeline.timings.total',n(len(d.timings))),('timeline.truncated',b(len(d.events)>limit or len(d.timings)>limit))))
        for i,event in enumerate(d.events[:limit]):
            prefix=f'timeline.events.{i}'
            fields.extend(((prefix+'.sequence',n(event.sequence)),(prefix+'.debugger_affected',b(event.debugger_affected))))
            if event.at is not None: fields.extend(instant(prefix,event.at))
            value=event.detail
            if event.kind in ('AttemptCreated','Ready','AttemptStarted','Abandoned'):
                fields.append((prefix+'.attempt','none' if value is None else n(value.serial)))
            elif event.kind=='RetryScheduled': fields.extend(((prefix+'.attempt',n(value[0].serial)),(prefix+'.delay_ns',n(value[1]))))
            elif event.kind=='AttemptFinished': fields.extend(((prefix+'.attempt',n(value[0].serial)),(prefix+'.outcome','enum:'+value[1])))
            elif event.kind in ('PublicationReserved','PublicationFailed','DeliveryBegun'): fields.append((prefix+'.delivery',n(value.serial)))
            elif event.kind=='DeliveryObserved': fields.extend(((prefix+'.delivery',n(value[0].serial)),(prefix+'.disposition','enum:'+value[1])))
            elif event.kind=='Admission': fields.append((prefix+'.admission','enum:'+value))
            elif event.kind=='Resolved': fields.append((prefix+'.outcome','enum:'+value))
            fields.append((prefix+'.kind','enum:'+snake(event.kind)))
        for i,timing in enumerate(d.timings[:limit]):
            prefix=f'timeline.timings.{i}'
            fields.extend(((prefix+'.family','enum:'+timing.family.value),(prefix+'.outcome','enum:'+timing.outcome),
                (prefix+'.duration_ns',n(timing.duration_ns) if timing.duration_ns is not None else 'unknown:'+timing.error),
                (prefix+'.attempt','none' if timing.attempt is None else n(timing.attempt.serial)),
                (prefix+'.delivery','none' if timing.delivery is None else n(timing.delivery.serial)),(prefix+'.debugger_affected',b(timing.debugger_affected))))
        return tuple(fields)
    def health(args):
        if args: raise ProtocolError('malformed')
        snapshot=observer.telemetry_health()
        fields=[('telemetry.'+key,n(snapshot[key])) for key in (
            'destructor_hook_losses','callback_faults','pause_interval_evictions',
            'detail_evictions','detail_rejections','detail_event_drops',
            'duplicate_observations','duplicate_deliveries','invalid_lifecycle',
            'abandoned','clock_failures','debugger_affected_measurements',
            'active_effect_handles','retained_detail_records','retained_detail_bytes')]
        metric=snapshot['metric']
        fields.extend(('telemetry.metric.'+key,n(getattr(metric,key))) for key in (
            'rejected_series','invalid_labels','arithmetic_errors','omitted_measurements',
            'invalid_measurements','imported_ignored','snapshots_evicted','snapshot_rejections'))
        fields.append(('telemetry.unobserved_native_debugger_interference_possible',b(snapshot['unobserved_native_debugger_interference_possible'])))
        pauses=observer.pause_intervals(); fields.extend((('pauses.total',n(len(pauses))),('pauses.truncated',b(len(pauses)>max_page_size))))
        for i,pause in enumerate(pauses[:max_page_size]):
            prefix=f'pauses.{i}'; fields.append((prefix+'.generation',n(pause['generation'])))
            fields.extend(instant(prefix+'.start',pause['start'],'clock_or_open_interval')); fields.extend(instant(prefix+'.end',pause['end'],'clock_or_open_interval'))
        windows=observer.available_windows(); fields.append(('metric_windows.total',n(len(windows))))
        fields.extend((f'metric_windows.{i}',n(revision)) for i,revision in enumerate(windows[:max_page_size]))
        return tuple(fields)
    return dict(effect_details=details,effect_timeline=timeline,telemetry_health=health)
