"""Optional bounded cumulative metrics, without implicit clocks or transports.

Limits bound retained diagnostic representation, not process RSS. Snapshots are
owned copies; caller-established disjointness is required for population merges.
"""
from dataclasses import dataclass, field
from enum import Enum
from copy import deepcopy
from bisect import bisect_left
U64_MAX=(1<<64)-1
U128_MAX=(1<<128)-1

def _uint(n,maximum=U64_MAX):
    if type(n) is not int or not 0<=n<=maximum:raise ValueError('unsigned integer out of range')
    return n
class MetricError(ValueError):pass
_NAMES='Requests Admissions Attempts AttemptOutcomes Resolutions Deliveries Unresolved Running ReadyDepth CompletionDepth QueueWait AttemptElapsed Resolution DeliveryLag EndToEnd CompletionProcessing AdmissionDelay ResolutionOverhead CompletionPreparation CompletionQueueWait EndToEndObserved Settlement ScheduledBackoff EligibilityWait InterAttemptGap DuplicateObservations DuplicateDeliveries CancellationRequests CancellationAcknowledgements Abandoned Dropped InvalidMeasurements'.split()
MetricFamily=Enum('MetricFamily',{''.join(('_'+c if i and c.isupper() else c) for i,c in enumerate(n)).upper():n for n in _NAMES},type=str)
_GAUGES=set(_NAMES[6:10]);_HISTOGRAMS=set(_NAMES[10:25])
_METRIC_NAMES='effect_requests_total effect_admissions_total effect_attempts_total effect_attempt_outcomes_total effect_resolutions_total effect_deliveries_total effects_unresolved effect_attempts_running effect_ready_queue_depth completion_queue_depth effect_queue_wait_seconds effect_attempt_elapsed_seconds effect_resolution_seconds effect_delivery_lag_seconds effect_end_to_end_seconds completion_processing_seconds effect_admission_delay_seconds effect_resolution_overhead_seconds completion_preparation_seconds completion_queue_wait_seconds effect_end_to_end_observed_seconds effect_settlement_seconds effect_scheduled_backoff_seconds effect_eligibility_wait_seconds effect_inter_attempt_gap_seconds effect_duplicate_observations_total effect_duplicate_deliveries_total effect_cancellation_requests_total effect_cancellation_acknowledgements_total effect_abandoned_total telemetry_dropped_total measurement_invalid_total'.split()
def _family(f):
    try:return MetricFamily(f)
    except ValueError:raise MetricError('unknown family') from None
def _kind(f):return 'gauge' if f.value in _GAUGES else 'histogram' if f.value in _HISTOGRAMS else 'counter'
_POPULATIONS=(
'observed host output requests','host admission decisions','actual first execution starts','known attempt terminations','logical effect resolutions','observed completion inputs including ignored/rejected/duplicates','admitted unresolved logical effects','started attempts without known termination','synchronized ready entries','reserved visible completion entries','ready to actual start per attempt','known terminated attempts, including async waits','request to logical resolution','resolution to each completion delivery start','request to first completion delivery start','delivery begin to immediately after reducer, excluding checks/recording','request to first ready queue boundary','selected finished attempt to logical resolution','resolution to completion publication reservation','publication reservation to delivery begin','request to first observed completion','request to explicit application acknowledgement','host-declared scheduled retry delay','retry schedule observation to ready eligibility','previous attempt finish to next actual start; not just backoff','repeated notification for the same token boundary','additional observed delivery identities for one effect','host cancellation requests; no running-gauge decrement','confirmed logical cancellation, distinct from attempt termination','local observation/future abandonment; remote termination unknown','diagnostic loss; aggregate health remains independent','unknown/reversed/cross-domain measurements')
def metric_catalog():return tuple(dict(family=f,name=n,kind=_kind(f),unit='seconds' if _kind(f)=='histogram' else 'count',population=p,allowed_labels=('operation','component','worker_pool','outcome','measurement_origin','debugger_affected'),reset='cumulative per process epoch; never sum repeated snapshots') for f,n,p in zip(MetricFamily,_METRIC_NAMES,_POPULATIONS))
@dataclass(frozen=True)
class Labels:
    operation:int=0
    component:int=0
    worker_pool:int=0
    outcome:str='None'
    origin:str='Live'
    debugger_affected:bool=False
    def __post_init__(self):
        for n in (self.operation,self.component,self.worker_pool):_uint(n,65535)
        if self.outcome not in ('None','Accepted','Rejected','Success','Failure','Cancelled','Abandoned','Ignored') or self.origin not in ('Live','DebugControlledLive','TestClock','ReplayHarness','ImportedLive') or type(self.debugger_affected) is not bool:raise ValueError('invalid labels')
@dataclass(frozen=True)
class LabelCatalog:
    operations:tuple=('unspecified',)
    components:tuple=('host',)
    worker_pools:tuple=('default',)
    def __post_init__(self):
        for name in ('operations','components','worker_pools'):
            v=tuple(getattr(self,name));object.__setattr__(self,name,v)
            if not 1<=len(v)<=256 or any(type(s) is not str or not 1<=len(s.encode())<=64 for s in v) or len(set(v))!=len(v):raise ValueError('invalid label catalog')
    def accepts(self,l):return type(l) is Labels and l.operation<len(self.operations) and l.component<len(self.components) and l.worker_pool<len(self.worker_pools)
@dataclass(frozen=True)
class HistogramSchema:
    id:int=1
    finite_bounds_ns:tuple=(1000,10000,100000,500000,1000000,2000000,5000000,10000000,20000000,50000000,100000000,200000000,500000000,1000000000,2000000000,5000000000,10000000000,30000000000,60000000000)
    def __post_init__(self):
        _uint(self.id);v=tuple(self.finite_bounds_ns);object.__setattr__(self,'finite_bounds_ns',v)
        for n in v:_uint(n)
        if not self.id or len(v)>64 or any(a>=b for a,b in zip(v,v[1:])):raise ValueError('invalid schema')
@dataclass(frozen=True)
class Quantile:
    upper_bound_ns:int|None
    observations:int
    low_sample_warning:bool
@dataclass
class Histogram:
    buckets:list=field(default_factory=lambda:[0]*65)
    count:int=0
    sum_ns:int=0
    def add(self,schema,nanos):
        _uint(nanos);i=bisect_left(schema.finite_bounds_ns,nanos)
        if self.count==U64_MAX or self.buckets[i]==U64_MAX or self.sum_ns+nanos>U128_MAX:return False
        self.count+=1;self.buckets[i]+=1;self.sum_ns+=nanos;return True
    def quantile(self,schema,numerator,denominator):
        _uint(numerator);_uint(denominator)
        if not self.count or not denominator or numerator>denominator:return None
        rank=max(1,(self.count*numerator+denominator-1)//denominator);total=0
        for i,n in enumerate(self.buckets[:len(schema.finite_bounds_ns)+1]):
            total+=n
            if total>=rank:return Quantile(schema.finite_bounds_ns[i] if i<len(schema.finite_bounds_ns) else None,self.count,self.count<100)
        return None
@dataclass(frozen=True)
class SnapshotTime:
    domain:int
    nanos:int
    def __post_init__(self):_uint(self.domain);_uint(self.nanos)
@dataclass
class MetricHealth:
    rejected_series:int=0
    invalid_labels:int=0
    arithmetic_errors:int=0
    omitted_measurements:int=0
    invalid_measurements:int=0
    imported_ignored:int=0
    snapshots_evicted:int=0
    snapshot_rejections:int=0
@dataclass(frozen=True)
class MetricLimits:
    max_families:int=128
    max_series:int=2048
    max_bytes:int=8*1024*1024
    max_snapshots:int=12
    max_snapshot_bytes:int=16*1024*1024
    def __post_init__(self):
        for n in vars(self).values():_uint(n)
        if not 1<=self.max_families<=128 or not self.max_series or not self.max_snapshots:raise ValueError('invalid limits')
_SCOPES=('CompleteParticipation','HostSnapshot','ObservedSinceAttach','Unknown')
@dataclass
class MetricSnapshot:
    revision:int
    epoch:int
    start:SnapshotTime|None
    end:SnapshotTime|None
    schema:HistogramSchema
    labels:LabelCatalog
    series:dict
    gauge_scope:str
    gauge_scopes:dict
    health:MetricHealth
    limits:MetricLimits
    complete:bool=True
    temporality:str='cumulative'
    def get(self,family,labels=Labels()):return self.series.get((_family(family),labels))
    def histogram(self,family,labels=Labels()):
        """Return the stored histogram for this series, or None for another kind."""
        value=self.get(family,labels)
        return value if isinstance(value,Histogram) else None
    def estimated_bytes(self):return 512+sum(32+len(s.encode()) for v in vars(self.labels).values() for s in v)+8*len(self.schema.finite_bounds_ns)+sum(768 if isinstance(v,Histogram) else 256 for v in self.series.values())
    def production(self):
        s=deepcopy(self);s.series={k:v for k,v in s.series.items() if k[1].origin=='Live' and not k[1].debugger_affected};s.gauge_scopes={k:v for k,v in s.gauge_scopes.items() if k[1].origin=='Live' and not k[1].debugger_affected};return s
    @staticmethod
    def window(start,end):
        if (start.epoch,start.schema,start.labels,start.start,start.gauge_scope)!=(end.epoch,end.schema,end.labels,end.start,end.gauge_scope) or start.temporality!='cumulative' or end.temporality!='cumulative' or any(end.gauge_scopes.get(k)!=v for k,v in start.gauge_scopes.items()):raise MetricError('incompatible')
        if start.revision>end.revision:raise MetricError('reversed window')
        if start.end is not None and end.end is not None:
            if start.end.domain!=end.end.domain:raise MetricError('incompatible')
            if start.end.nanos>end.end.nanos:raise MetricError('reversed window')
        s=deepcopy(end);s.start=start.end;s.temporality='delta';s.complete &= start.complete
        if any(k not in end.series for k in start.series):raise MetricError('counter reset')
        for k,v in s.series.items():
            if k not in start.series:continue
            p=start.series[k]
            if _kind(k[0])=='gauge':
                if type(v) is not int or type(p) is not int:raise MetricError('incompatible')
                continue
            if isinstance(v,Histogram) and isinstance(p,Histogram):
                values=[v.count-p.count,v.sum_ns-p.sum_ns]+[a-b for a,b in zip(v.buckets,p.buckets)]
                if min(values)<0:raise MetricError('counter reset')
                v.count,v.sum_ns=values[:2];v.buckets=values[2:]
            elif type(v) is int and type(p) is int:
                if v<p:raise MetricError('counter reset')
                s.series[k]=v-p
            else:raise MetricError('incompatible')
        return s
    def merge_disjoint(self,other):
        if (self.epoch,self.start,self.end,self.schema,self.labels,self.temporality,self.gauge_scope)!=(other.epoch,other.start,other.end,other.schema,other.labels,other.temporality,other.gauge_scope):raise MetricError('incompatible')
        s=deepcopy(self)
        for k,v in other.gauge_scopes.items():
            if k in s.gauge_scopes and s.gauge_scopes[k]!=v:raise MetricError('incompatible')
            s.gauge_scopes[k]=v
        for k,v in other.series.items():
            if k not in s.series:s.series[k]=deepcopy(v);continue
            p=s.series[k]
            if isinstance(p,Histogram) and isinstance(v,Histogram):
                count=p.count+v.count;total=p.sum_ns+v.sum_ns;buckets=[a+b for a,b in zip(p.buckets,v.buckets)]
                if count>U64_MAX or total>U128_MAX or max(buckets)>U64_MAX:raise MetricError('overflow')
                p.count=count;p.sum_ns=total;p.buckets=buckets
            elif type(p) is int and type(v) is int:
                if p+v>U64_MAX:raise MetricError('overflow')
                s.series[k]=p+v
            else:raise MetricError('incompatible')
        if len(s.series)>s.limits.max_series or len({k[0] for k in s.series})>s.limits.max_families or s.estimated_bytes()>s.limits.max_bytes:raise MetricError('overflow')
        for name in vars(s.health):
            n=getattr(s.health,name)+getattr(other.health,name)
            if n>U64_MAX:raise MetricError('overflow')
            setattr(s.health,name,n)
        s.complete &= other.complete;self.__dict__.update(s.__dict__)
class MetricsStore:
    def __init__(self,epoch,start=None,labels=LabelCatalog(),schema=HistogramSchema(),limits=MetricLimits(),gauge_scope='Unknown'):
        _uint(epoch)
        if not epoch or type(labels) is not LabelCatalog or type(schema) is not HistogramSchema or type(limits) is not MetricLimits or gauge_scope not in _SCOPES or (start is not None and type(start) is not SnapshotTime):raise MetricError('invalid config')
        self._snapshot=MetricSnapshot(0,epoch,start,start,schema,labels,{},gauge_scope,{},MetricHealth(),limits)
        if self._snapshot.estimated_bytes()>limits.max_bytes:raise MetricError('invalid config')
        self._history=[];self._last_valid_end=start;self._default_scope=gauge_scope;self._snapshot_busy=False
    def _ensure_mutable(self):
        if self._snapshot_busy:raise MetricError('snapshot in progress')
    @property
    def health(self):return deepcopy(self._snapshot.health)
    @property
    def revision(self):return self._snapshot.revision
    def mark_incomplete(self):self._ensure_mutable();self._snapshot.complete=False
    def labels_valid(self,l):return self._snapshot.labels.accepts(l)
    def record_health(self,name,n=1):
        self._ensure_mutable()
        _uint(n)
        if name not in vars(self._snapshot.health):raise ValueError('unknown health field')
        h=self._snapshot.health;setattr(h,name,min(U64_MAX,getattr(h,name)+n))
    def _loss(self,name):self.record_health(name);self.mark_incomplete()
    def _value(self,f,l):
        s=self._snapshot
        if type(l) is not Labels:self._loss('invalid_labels');return None
        if l.origin in ('ReplayHarness','ImportedLive'):self.record_health('imported_ignored');return None
        if not self.labels_valid(l):self._loss('invalid_labels');return None
        k=(f,l)
        if k not in s.series:
            v=Histogram() if _kind(f)=='histogram' else 0;extra=768 if isinstance(v,Histogram) else 256
            if len(s.series)>=s.limits.max_series or s.estimated_bytes()+extra>s.limits.max_bytes or (f not in {k[0] for k in s.series} and len({k[0] for k in s.series})>=s.limits.max_families):self._loss('rejected_series');return None
            s.series[k]=v
            if _kind(f)=='gauge':s.gauge_scopes[k]=self._default_scope;self._refresh_scope()
        s.revision=min(U64_MAX,s.revision+1);s.end=None;return k
    def _refresh_scope(self):
        scopes=set(self._snapshot.gauge_scopes.values());self._snapshot.gauge_scope=next(iter(scopes)) if len(scopes)==1 else 'Unknown' if scopes else self._default_scope
    def increment(self,family,labels=Labels(),n=1):
        self._ensure_mutable()
        _uint(n);f=_family(family)
        if _kind(f)!='counter':self._loss('arithmetic_errors');return
        k=self._value(f,labels)
        if k is None:return
        if self._snapshot.series[k]+n>U64_MAX:self._loss('arithmetic_errors');return
        self._snapshot.series[k]+=n
    def set_gauge(self,family,labels,value):
        self._ensure_mutable()
        _uint(value);f=_family(family)
        if _kind(f)!='gauge':self._loss('arithmetic_errors');return
        k=self._value(f,labels)
        if k is not None:self._snapshot.series[k]=value
    def gauge_delta(self,family,labels,delta):
        self._ensure_mutable()
        if type(delta) is not int or not -(1<<63)<=delta<(1<<63):raise ValueError('signed delta out of range')
        f=_family(family)
        if _kind(f)!='gauge':self._loss('arithmetic_errors');return
        k=self._value(f,labels)
        if k is None:return
        v=self._snapshot.series[k]+delta
        if not 0<=v<=U64_MAX:self._loss('arithmetic_errors');self._snapshot.gauge_scopes[k]='Unknown';self._refresh_scope();return
        self._snapshot.series[k]=v
    def observe(self,family,labels,nanos):
        self._ensure_mutable()
        _uint(nanos);f=_family(family)
        if _kind(f)!='histogram':self._loss('arithmetic_errors');return
        k=self._value(f,labels)
        if k is not None and not self._snapshot.series[k].add(self._snapshot.schema,nanos):self._loss('arithmetic_errors')
    def set_gauge_scope(self,scope):
        self._ensure_mutable()
        if scope not in _SCOPES:raise ValueError('invalid scope')
        self._default_scope=scope
        for k in self._snapshot.gauge_scopes:self._snapshot.gauge_scopes[k]=scope
        self._refresh_scope()
    def set_gauge_scope_for(self,labels,scope):
        self._ensure_mutable()
        if scope not in _SCOPES:raise ValueError('invalid scope')
        for k in self._snapshot.gauge_scopes:
            if k[1]==labels:self._snapshot.gauge_scopes[k]=scope
        self._refresh_scope()
    def current(self):return deepcopy(self._snapshot)
    def snapshot(self,end=None,*,validate=None):
        self._ensure_mutable()
        self._snapshot_busy=True
        try:return self._snapshot_transaction(end,validate)
        finally:self._snapshot_busy=False
    def _snapshot_transaction(self,end,validate):
        if end is not None and type(end) is not SnapshotTime:raise ValueError('invalid endpoint')
        s=deepcopy(self._snapshot);s.revision=min(U64_MAX,s.revision+1);last=self._last_valid_end
        if end is not None and any(t is not None and (t.domain!=end.domain or t.nanos>end.nanos) for t in (s.start,last)):
            s.health.invalid_measurements=min(U64_MAX,s.health.invalid_measurements+1);s.complete=False;s.end=None
        else:
            s.end=end
            if end is not None:last=end
        size=s.estimated_bytes();history=list(self._history)
        if size>s.limits.max_snapshot_bytes:s.health.snapshot_rejections=min(U64_MAX,s.health.snapshot_rejections+1)
        else:
            retained=sum(x.estimated_bytes() for x in history)
            while history and (len(history)>=s.limits.max_snapshots or retained+size>s.limits.max_snapshot_bytes):
                retained-=history.pop(0).estimated_bytes();s.health.snapshots_evicted=min(U64_MAX,s.health.snapshots_evicted+1)
        if validate is not None:validate(deepcopy(s))
        self._snapshot=s;self._last_valid_end=last
        if size<=s.limits.max_snapshot_bytes:history.append(deepcopy(s))
        self._history=history;return deepcopy(s)
    def available_windows(self):return tuple(s.revision for s in self._history)
    def window(self,start,end):
        by_id={}
        for snapshot in self._history:by_id.setdefault(snapshot.revision,snapshot)
        if start not in by_id or end not in by_id:raise MetricError('unknown window')
        return MetricSnapshot.window(by_id[start],by_id[end])


def protocol_adapters(store,*,max_page_size=100,max_capture_bytes=16*1024*1024):
    """One connection-owned immutable capture, published after wire preflight."""
    from .debugger_protocol import ProtocolPrepared, ProtocolError
    _uint(max_page_size);_uint(max_capture_bytes)
    if not max_page_size or type(store) is not MetricsStore:raise ValueError('invalid adapter configuration')
    state=dict(next=0,capture=None,generation=0)
    def reset():state.update(capture=None,generation=state['generation']+1)
    def page(offset,limit):
        _uint(offset);_uint(limit)
        if not 1<=limit<=max_page_size:raise ProtocolError('limit')
    def catalog(args):
        offset,limit=args;page(offset,limit);rows=metric_catalog();result=[('catalog.total',f'u64:{len(rows)}'),('catalog.next_offset',f'u64:{offset+limit}' if offset+limit<len(rows) else 'none')]
        for i,d in enumerate(rows[offset:offset+limit]):
            result.append((f'catalog.{i}.family','enum:'+d['family'].value))
            for key in ('name','kind','unit','population','reset'):result.append((f'catalog.{i}.{key}',('enum:'+d[key].title()) if key=='kind' else 'string:'+d[key]))
        return tuple(result)
    def sort_key(item):
        (f,l),_=item
        return (_NAMES.index(f.value),l.operation,l.component,l.worker_pool,('None','Accepted','Rejected','Success','Failure','Cancelled','Abandoned','Ignored').index(l.outcome),('Live','DebugControlledLive','TestClock','ReplayHarness','ImportedLive').index(l.origin),l.debugger_affected)
    def fields(handle,s,offset,limit):
        rows=sorted(s.series.items(),key=sort_key);result=[('metrics.handle',f'u64:{handle}'),('metrics.revision',f'u64:{s.revision}'),('metrics.epoch',f'u64:{s.epoch}'),('metrics.complete','bool:'+str(s.complete).lower()),('metrics.temporality','enum:'+s.temporality.title()),('metrics.gauge_scope','enum:'+s.gauge_scope),('metrics.histogram_schema',f'u64:{s.schema.id}'),('metrics.histogram_storage_unit','enum:nanoseconds'),('metrics.series.total',f'u64:{len(rows)}'),('metrics.series.offset',f'u64:{offset}'),('metrics.series.next_offset',f'u64:{offset+limit}' if offset+limit<len(rows) else 'none')]
        for name,t in (('start',s.start),('end',s.end)):
            if t is None:result.append(('metrics.'+name,'unknown:clock'))
            else:result.extend(((f'metrics.{name}.clock_domain',f'u64:{t.domain}'),(f'metrics.{name}.nanos',f'u64:{t.nanos}')))
        result.extend((('metrics.limits.series',f'u64:{s.limits.max_series}'),('metrics.limits.bytes',f'u64:{s.limits.max_bytes}')))
        for name in ('rejected_series','invalid_measurements','omitted_measurements'):result.append(('metrics.health.'+name,f'u64:{getattr(s.health,name)}'))
        result.append(('metrics.series.returned',f'u64:{len(rows[offset:offset+limit])}'))
        descriptors={d['family']:d for d in metric_catalog()}
        for i,((f,l),v) in enumerate(rows[offset:offset+limit]):
            prefix=f'metrics.series.{i}';d=descriptors[f]
            for name in ('name','kind','unit','population'):result.append((prefix+'.'+name,('enum:'+d[name].title()) if name=='kind' else 'string:'+d[name]))
            for name,n in (('operation',l.operation),('component',l.component),('worker_pool',l.worker_pool)):
                result.append((prefix+'.'+name+'.id',f'u64:{n}'))
            result.extend(((prefix+'.outcome','enum:'+l.outcome),(prefix+'.measurement_origin','enum:'+l.origin),(prefix+'.debugger_affected','bool:'+str(l.debugger_affected).lower())))
            for name,n,names in (('operation',l.operation,s.labels.operations),('component',l.component,s.labels.components),('worker_pool',l.worker_pool,s.labels.worker_pools)):
                if n<len(names):result.append((prefix+'.'+name+'.name','string:'+names[n]))
            if isinstance(v,Histogram):
                result.extend(((prefix+'.count',f'u64:{v.count}'),(prefix+'.sum_ns',f'u128:{v.sum_ns}')))
                for j,n in enumerate(v.buckets[:len(s.schema.finite_bounds_ns)+1]):result.extend(((f'{prefix}.buckets.{j}.count',f'u64:{n}'),(f'{prefix}.buckets.{j}.upper_bound_ns',f'u64:{s.schema.finite_bounds_ns[j]}' if j<len(s.schema.finite_bounds_ns) else 'infinity')))
                result.append((prefix+'.quantile_method','enum:histogram_bucket_upper_bound'))
                for name,n in (('p50',50),('p95',95),('p99',99)):
                    q=v.quantile(s.schema,n,100)
                    if q is None:result.append((prefix+'.'+name,'unavailable:no_observations'))
                    else:result.extend(((f'{prefix}.{name}.upper_bound_ns',f'u64:{q.upper_bound_ns}' if q.upper_bound_ns is not None else 'infinity'),(f'{prefix}.{name}.observations',f'u64:{q.observations}'),(f'{prefix}.{name}.low_sample_warning','bool:'+str(q.low_sample_warning).lower())))
            else:
                result.append((prefix+'.value',f'u64:{v}'))
                if _kind(f)=='gauge':result.append((prefix+'.gauge_scope','enum:'+s.gauge_scopes.get((f,l),'Unknown')))
        return tuple(result)
    def prepare(s,offset,limit,staged=None):
        if s.estimated_bytes()>max_capture_bytes:raise ProtocolError('limit')
        if state['next']==U64_MAX:raise ProtocolError('exhausted')
        handle=state['next']+1;generation=state['generation'];baseline=deepcopy(store.__dict__) if staged is not None else None
        def commit():
            if generation!=state['generation'] or state['next']+1!=handle:raise ProtocolError('stale_handle')
            if staged is not None:
                store._ensure_mutable()
                if store.__dict__!=baseline:raise ProtocolError('stale_revision')
                store.__dict__.update(staged.__dict__)
            state.update(next=handle,capture=(handle,deepcopy(s)))
        return ProtocolPrepared(fields(handle,s,offset,limit),commit)
    def snapshot(args):
        handle,offset,limit=args;page(offset,limit)
        if handle is None:
            if offset:raise ProtocolError('invalid_input')
            staged=deepcopy(store);s=staged.snapshot(None);return prepare(s,offset,limit,staged)
        _uint(handle)
        if state['capture'] is None or state['capture'][0]!=handle:raise ProtocolError('stale_handle')
        return fields(handle,state['capture'][1],offset,limit)
    def window(args):
        start,end,limit=args;page(0,limit)
        try:s=store.window(start,end)
        except MetricError:raise ProtocolError('stale_handle') from None
        return prepare(s,0,limit)
    for callback in (catalog,snapshot,window):callback.reset_connection=reset
    return dict(metric_catalog=catalog,metric_snapshot=snapshot,metric_window=window)
