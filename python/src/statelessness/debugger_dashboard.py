"""Passive payload-free metric reports and numeric evidence correlations."""
from dataclasses import dataclass, asdict, is_dataclass
from copy import deepcopy
import json
from .debugger_metrics import MetricFamily as F, Histogram, MetricSnapshot, metric_catalog, _kind, _uint

FAMILIES=tuple(F(n) for n in ('Requests','Admissions','Attempts','QueueWait','AttemptElapsed','DeliveryLag','CompletionProcessing','Settlement','Unresolved','Running','ReadyDepth','CompletionDepth','ScheduledBackoff','InterAttemptGap','CancellationRequests','CancellationAcknowledgements','Dropped','InvalidMeasurements'))
_HEALTH=('detail_event_drops','detail_evictions','detail_rejections','clock_failures','callback_faults','destructor_hook_losses','invalid_lifecycle','active_effect_handles','duplicate_observations','duplicate_deliveries','abandoned','pause_interval_evictions','debugger_affected_measurements','retained_detail_records','retained_detail_bytes','effect_handle_bytes','attempt_handle_bytes','delivery_handle_bytes')
_INCOMPLETE=('pause_interval_evictions','abandoned','duplicate_observations','callback_faults','clock_failures','destructor_hook_losses','invalid_lifecycle','detail_evictions','detail_rejections','detail_event_drops')

def _get(value,key,default=0):return value.get(key,default) if isinstance(value,dict) else getattr(value,key,default)
def _health(value):return {k:_uint(_get(value,k)) for k in ('rejected_series','invalid_labels','arithmetic_errors','omitted_measurements','invalid_measurements','imported_ignored','snapshots_evicted','snapshot_rejections')}

def _time(value):return None if value is None else dict(clock_domain=value.domain,nanos=value.nanos)


@dataclass(frozen=True)
class Row:
    family: F
    labels: object
    value: object
    gauge_scope: str | None
    p50: object
    p95: object
    p99: object


@dataclass(frozen=True)
class Dashboard:
    epoch: int
    revision: int
    temporality: str
    start: object
    end: object
    health: dict
    metric_health: dict
    rows: tuple
    truncated: bool
    observation_complete: bool
    @classmethod
    def from_snapshot(cls,snapshot,health,labels,max_rows):
        if type(snapshot) is not MetricSnapshot:raise ValueError('expected metric snapshot')
        _uint(max_rows)
        # Consume only the bounded prefix, including a one-item lookahead.
        rows=[];truncated=False
        for label in labels:
            for family in FAMILIES:
                if len(rows)==max_rows:truncated=True;break
                value=deepcopy(snapshot.get(family,label));k=(family,label)
                quantiles=[value.quantile(snapshot.schema,n,100) if isinstance(value,Histogram) else None for n in (50,95,99)]
                rows.append(Row(family,label,value,snapshot.gauge_scopes.get(k,snapshot.gauge_scope) if value is not None and _kind(family)=='gauge' else None,*quantiles))
            if truncated:break
        mh=_health(snapshot.health);hh={k:_uint(_get(health,k)) for k in _HEALTH}
        interference=_get(health,'unobserved_native_debugger_interference_possible',True)
        if type(interference) is not bool:raise ValueError('invalid interference flag')
        hh['unobserved_native_debugger_interference_possible']=interference
        hh['host_metric_health']=_health(_get(health,'metric',{}))
        complete=snapshot.complete and not truncated and not any(mh.values()) and not any(hh['host_metric_health'].values()) and not any(hh[k] for k in _INCOMPLETE)
        return cls(snapshot.epoch,snapshot.revision,snapshot.temporality,snapshot.start,snapshot.end,hh,mh,tuple(rows),truncated,complete)
    def to_json(self,max_bytes):
        """Numeric JSON preserves integer values; consumers must use lossless parsing."""
        _uint(max_bytes)
        names={d['family']:d['name'] for d in metric_catalog()}
        result=dict(schema='statelessness.effect-dashboard/1',epoch=self.epoch,revision=self.revision,temporality=self.temporality,start=_time(self.start),end=_time(self.end),truncated=self.truncated,observation_complete=self.observation_complete,health={**self.health,**self.metric_health},rows=[])
        result['health']['dropped_detail_events']=result['health'].pop('detail_event_drops')
        encoder=json.JSONEncoder(separators=(',',':'),ensure_ascii=True,allow_nan=False)
        chunks=[];size=0
        def emit(chunk):
            nonlocal size
            size+=len(chunk.encode())
            if size>max_bytes:raise ValueError('dashboard byte budget exceeded')
            chunks.append(chunk)
        # Project each row only after its predecessor fits; tiny budgets stop in
        # the header without allocating a second complete row representation.
        del result['rows']
        header=iter(encoder.iterencode(result));previous=next(header)
        for chunk in header:emit(previous);previous=chunk
        emit(previous[:-1]);emit(',"rows":[')
        for i,row in enumerate(self.rows):
            if i:emit(',')
            l=row.labels;v=row.value
            record=dict(metric=names[row.family],operation=l.operation,component=l.component,worker_pool=l.worker_pool,outcome=l.outcome,measurement_origin=l.origin,debugger_affected=l.debugger_affected,value=dict(count=v.count,sum_ns=v.sum_ns) if isinstance(v,Histogram) else v,gauge_scope=row.gauge_scope)
            for name,q in zip(('p50','p95','p99'),(row.p50,row.p95,row.p99)):
                record[name]=None if q is None else dict(upper_bound_ns=q.upper_bound_ns,overflow_bucket=q.upper_bound_ns is None,observations=q.observations,low_sample_warning=q.low_sample_warning)
            for chunk in encoder.iterencode(record):emit(chunk)
        emit(']}')
        return ''.join(chunks)


@dataclass(frozen=True)
class Correlation:
    kind: str
    local_sequence: int | None = None
    output: int | None = None
    detail_complete: bool | None = None
    origin: object = None
    reason: str | None = None


def correlate(evidence,timeline):
    """Validate retained numeric host labels, never external execution truth."""
    origin=_get(timeline,'origin',None);effect=_get(timeline,'effect',None)
    if not evidence.provenance.run:return Correlation('missing_identity')
    if effect is None or origin is None:raise ValueError('timeline identity required')
    if _get(effect,'run')!=_get(origin,'run') or _get(effect,'epoch')!=_get(origin,'epoch') or evidence.provenance.run!=str(_get(origin,'run')) or evidence.provenance.epoch!=_get(origin,'epoch'):return Correlation('conflict',reason='run or epoch')
    for key in ('run','epoch','machine','transition_sequence'):_uint(_get(origin,key))
    _uint(_get(origin,'output_index'),(1<<32)-1)
    for key in ('run','epoch'):_uint(_get(effect,key))
    sequence=_get(origin,'transition_sequence')
    if not sequence:return Correlation('conflict',reason='zero transition sequence')
    if sequence<=evidence.checkpoint_after:return Correlation('before_checkpoint',origin=deepcopy(origin))
    index=sequence-evidence.checkpoint_after-1
    if index>=len(evidence.steps):return Correlation('conflict',reason='sequence outside evidence')
    step=evidence.steps[index]
    if step.causal.machine is None:return Correlation('missing_identity')
    if step.causal.machine!=str(_get(origin,'machine')):return Correlation('conflict',reason='machine')
    output=_get(origin,'output_index')
    if step.sequence!=sequence or index>=len(evidence.trace.steps) or output>=len(evidence.trace.steps[index].outputs):return Correlation('conflict',reason='output outside evidence')
    return Correlation('retained',index+1,output,bool(_get(timeline,'complete',False)))
