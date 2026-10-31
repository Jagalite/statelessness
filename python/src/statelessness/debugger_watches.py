"""Optional local ordered watch capture. No reducer, recorder, or clock calls.

Capture acknowledgements concern one ordered producer. Hosts must acknowledge
remote/multi-producer changes explicitly; a local ack is not remote activation.
"""
from dataclasses import dataclass, replace
from .core import ModelError, U64_MAX, _natural, _copy
from .debugger_inspect import (SnapshotId, InspectLimits, PageRequest, InspectQuery,
    inspect, compare_nodes, _payload_bytes, _read_schema, PathSegment, _node_shape, _scalar_wire_type, InspectNode, DisplaySchema, InspectError)


def _node_bytes(node, max_chars=256*1024):
    if node is None: return 0
    return 128+_payload_bytes(node.value,max_chars)+sum(96+_payload_bytes(c.label)+_payload_bytes(c.segment.value)+_payload_bytes(c.source)+_node_bytes(c.node,max_chars) for c in node.children)


def _schema_bytes(schema):
    return 0 if schema is None else 64+_payload_bytes(schema.name)+_payload_bytes(schema.source)


def _result_bytes(result):
    return _node_bytes(result.node)+_schema_bytes(result.schema)+sum(32+_payload_bytes(s.value) for s in result.path)


@dataclass(frozen=True)
class DiagnosticMarker:
    id: str
    detail: str


@dataclass(frozen=True)
class MarkerBatch:
    markers: tuple[DiagnosticMarker, ...]
    dropped: int
    truncated: int
    retained_bytes: int


class MarkerCollector:
    """Bounded post-turn diagnostic markers, charged using Rust's 64-bit layout.

    The limit is logical retained size, not Python heap or callback allocations.
    Use from one thread; detail callbacks must not reenter this collector.
    """
    def __init__(self, enabled: bool, max_records: int, max_bytes: int):
        if type(enabled) is not bool: raise ValueError('enabled must be bool')
        _natural(max_records, 'max records'); _natural(max_bytes, 'max bytes')
        self._enabled=enabled; self._max_records=max_records; self._max_bytes=max_bytes
        self._markers=[]; self._dropped=0; self._truncated=0; self._bytes=0
        self._busy=False; self._finished=False

    @classmethod
    def disabled(cls): return cls(False,256,32*1024)

    def _ready(self):
        if self._finished: raise ModelError('marker collector already finished')
        if self._busy: raise ModelError('marker collector callback reentered')

    def interested(self):
        self._ready()
        return self._enabled and len(self._markers)<self._max_records and self._bytes<self._max_bytes

    def marker(self, id: str, detail):
        self._ready()
        if not self._enabled: return
        if type(id) is not str or not callable(detail): raise ValueError('marker requires text id and detail callback')
        if not self.interested() or 48>self._max_bytes-self._bytes:
            self._dropped=min(U64_MAX,self._dropped+1); return
        id_size=0; room=self._max_bytes-self._bytes-48
        for char in id:
            try: n=len(char.encode('utf-8'))
            except UnicodeEncodeError as e: raise ValueError('invalid marker id') from e
            if n>room-id_size:
                self._dropped=min(U64_MAX,self._dropped+1); return
            id_size+=n
        overhead=48+id_size
        self._busy=True
        try: value=detail()
        finally: self._busy=False
        if type(value) is not str: raise ValueError('marker detail must be text')
        available=self._max_bytes-self._bytes-overhead
        size=0; end=0; truncated=False
        for index,char in enumerate(value):
            try: n=len(char.encode('utf-8'))
            except UnicodeEncodeError as e: raise ValueError('invalid marker detail') from e
            if not truncated and n<=available-size:
                size+=n; end=index+1
            else:
                truncated=True
        if truncated:
            value=value[:end]
            self._truncated=min(U64_MAX,self._truncated+1)
        self._markers.append(DiagnosticMarker(id,value)); self._bytes+=overhead+size

    def finish(self):
        self._ready(); self._finished=True
        return MarkerBatch(tuple(self._markers),self._dropped,self._truncated,self._bytes)


@dataclass(frozen=True)
class WatchAuthorization:
    allowed_paths: tuple[tuple,...] = ()
    def __post_init__(self): object.__setattr__(self,'allowed_paths',tuple(tuple(p) for p in self.allowed_paths))
    @classmethod
    def local_all(cls): return cls(((),))
    def allows(self,path): return any(tuple(path[:len(p)])==p for p in self.allowed_paths)


@dataclass(frozen=True)
class WatchSelector:
    path: tuple = ()
    schema_version: int = 1
    model: str | None = None
    machine: str | None = None
    input_variant: str | None = None
    output_variant: str | None = None
    origin: str | None = None
    def __post_init__(self):
        path=tuple(self.path)
        if len(path)>32 or any(type(s) is not PathSegment for s in path): raise ValueError('invalid selector path')
        object.__setattr__(self,'path',path); _natural(self.schema_version,'schema version',maximum=(1<<32)-1)
        for name in ('model','machine','input_variant','output_variant','origin'):
            value=getattr(self,name)
            if value is not None and (type(value) is not str or not value or _payload_bytes(value)>256): raise ValueError('invalid selector label')
    def matches(self,context):
        return all(getattr(self,key) is None or getattr(self,key)==getattr(context,key) for key in ('model','machine','input_variant','origin')) and (self.output_variant is None or self.output_variant==context.output_variant or self.output_variant in context.output_variants)


@dataclass(frozen=True)
class WatchConfig:
    selector: WatchSelector = WatchSelector()
    trigger: str = 'changed'
    equals: object = None
    enabled: bool = True
    evaluation_interval: int = 1
    delivery_interval: int = 1
    baseline_ttl_sequences: int | None = None
    ttl_observations: int | None = None
    limits: InspectLimits = InspectLimits(max_bytes=32768)
    page: PageRequest = PageRequest()
    def __post_init__(self):
        if type(self.selector) is not WatchSelector or type(self.limits) is not InspectLimits or type(self.page) is not PageRequest: raise ValueError('invalid watch configuration types')
        if self.trigger not in ('changed','every','equals','property_failure'): raise ValueError('invalid watch trigger')
        if self.trigger=='equals': _node_shape(InspectNode('scalar',self.equals))
        elif self.equals is not None: raise ValueError('unexpected watch equals payload')
        if type(self.enabled) is not bool: raise ValueError('enabled must be bool')
        _natural(self.evaluation_interval,'evaluation interval',1); _natural(self.delivery_interval,'delivery interval',1)
        for name in ('baseline_ttl_sequences','ttl_observations'):
            if getattr(self,name) is not None: _natural(getattr(self,name),name)


@dataclass(frozen=True)
class WatchLimits:
    """Retained-schema identity is separately bounded per watch.

    ``max_baseline_bytes`` covers current events and retained projection
    schemas. The validated schema needed after eviction is limited by
    ``max_watches`` and the inspection metadata limits (4096 UTF-8 bytes
    each for its name and optional source file).
    """
    max_watches: int = 64
    max_ring_records: int = 1000
    max_ring_bytes: int = 4*1024*1024
    max_event_bytes: int = 32768
    max_baseline_bytes: int = 8*1024*1024
    max_config_bytes: int = 4096
    def __post_init__(self):
        for key in self.__dataclass_fields__: _natural(getattr(self,key),key)


@dataclass(frozen=True)
class ObservationContext:
    snapshot: SnapshotId
    origin: str = 'simulation'
    model: str | None = None
    machine: str | None = None
    input_variant: str | None = None
    output_variant: str | None = None
    property_failure: bool = False
    failing_properties: tuple[str,...] = ()
    phase: str = 'post-turn'
    output_variants: tuple[str,...] = ()
    failing_properties_complete: bool = True
    def __post_init__(self):
        if type(self.snapshot) is not SnapshotId or type(self.property_failure) is not bool or type(self.failing_properties_complete) is not bool: raise ValueError('invalid observation context')
        if type(self.failing_properties) not in (tuple,list) or len(self.failing_properties)>1024: raise ValueError('failing property list limit')
        if type(self.output_variants) not in (tuple,list) or len(self.output_variants)>1024: raise ValueError('output variant list limit')
        object.__setattr__(self,'failing_properties',tuple(self.failing_properties)); object.__setattr__(self,'output_variants',tuple(self.output_variants))
        for value in (self.model,self.machine,self.input_variant,self.output_variant):
            if value is not None and (type(value) is not str or _payload_bytes(value)>256): raise ValueError('invalid observation label')
        if any(type(p) is not str or any(0xD800 <= ord(c) <= 0xDFFF for c in p) for p in self.failing_properties): raise ValueError('invalid failing property identity')
        if any(type(v) is not str or _payload_bytes(v)>256 for v in self.output_variants): raise ValueError('invalid output variant label')
        if self.origin not in ('diagnostic-control','recorded-live','exact-replay','simulation','out-of-domain-injection','counterfactual-restore','edited-state-experiment'): raise ValueError('invalid watch origin')
        if self.phase not in ('initial','post-turn','control'): raise ValueError('invalid observation phase')


@dataclass(frozen=True)
class WatchAck:
    watch_id: int
    generation: int
    capture_revision: int
    effective_sequence: int
    display_schema: object = None
    resolved_type: str | None = None


@dataclass(frozen=True)
class WatchEvent:
    watch_id: int
    generation: int
    capture_revision: int
    snapshot: SnapshotId
    origin: str
    kind: str
    value: object = None
    previous_snapshot: SnapshotId | None = None
    difference: str = 'unknown'
    path: tuple = ()
    property_failure: bool = False
    failing_properties: tuple[str,...] = ()
    dropped_before: int = 0
    schema: object = None
    sampled_changed: bool | None = None
    model: str | None = None
    machine: str | None = None
    phase: str = 'post-turn'
    completeness: str = 'complete'
    trigger: str = 'changed'
    inspection_error: str | None = None
    dropped_count: int = 0


@dataclass(frozen=True)
class ObservationSummary:
    selected: int = 0
    evaluated: int = 0
    emitted: int = 0
    stale: bool = False
    observation_gap: bool = False
    inspection_errors: int = 0


def _event_bytes(event, max_chars=256*1024):
    return 256+len(event.inspection_error or '')+_node_bytes(event.value,max_chars)+_schema_bytes(event.schema)+_payload_bytes(event.model)+_payload_bytes(event.machine)+sum(_payload_bytes(s.value)+32 for s in event.path)+sum(len(p.encode('utf8')) for p in event.failing_properties)


def _baseline_bytes(entry, max_chars):
    current=entry.get('current')
    if current is None: return 0
    baseline=entry.get('baseline')
    # The current event and retained projection share their node, but the
    # event carries a separately truncated schema. Charge the full projection
    # schema too; it remains necessary for future identity comparisons.
    return _event_bytes(current,max_chars)+128+(0 if baseline is None else _schema_bytes(baseline.schema))


def _property_ids(context):
    # Rust retains at most 16 IDs, each truncated at a UTF-8 boundary.
    return tuple(p[:256].encode('utf8')[:256].decode('utf8', 'ignore') for p in context.failing_properties[:16])


def _event_schema(schema):
    # UTF-8 uses at least one byte per character. Do not encode more than the
    # prefix that can possibly fit in the retained event metadata.
    return replace(schema,name=schema.name[:1024].encode('utf8')[:1024].decode('utf8','ignore'))


def _metadata_completeness(context, completeness, schema):
    if completeness == 'complete' and (len(schema.name)>1024 or len(schema.name.encode('utf8'))>1024 or not context.failing_properties_complete or len(context.failing_properties)>16 or any(len(p)>256 or len(p.encode('utf8'))>256 for p in context.failing_properties)):
        return 'value-limit'
    return completeness


class WatchRegistry:
    def __init__(self, *, limits=WatchLimits(), authorization=WatchAuthorization(), schema_version=1):
        if type(limits) is not WatchLimits or type(authorization) is not WatchAuthorization: raise ValueError('invalid watch registry types')
        _natural(schema_version,'schema version',maximum=(1<<32)-1)
        self._limits,self._authorization=limits,authorization
        self._schema_version=schema_version
        self._revision,self._next=0,1
        self._entries,self._ring={},[]
        self._bytes,self._dropped,self._gaps,self._pending_dropped=0,0,0,0
        self._reported_drops=0
        self._inspection_errors=0
        self._evaluated,self._filtered,self._evaluation_sampled,self._delivery_sampled=0,0,0,0
        self._baseline_evictions,self._truncated=0,0
        self._last,self._failure=None,None
        self._busy=False
    @property
    def capture_revision(self): return self._revision
    @property
    def limits(self): return self._limits
    @property
    def authorization(self): return self._authorization
    @property
    def interested(self): return any(e['state']=='active' for e in self._entries.values())
    @property
    def health(self): return {'dropped':self._dropped,'dropped_events':self._dropped,'observation_gaps':self._gaps,'retained_bytes':self._bytes,'inspection_errors':self._inspection_errors,'evaluated':self._evaluated,'filtered':self._filtered,'evaluation_sampled':self._evaluation_sampled,'delivery_sampled':self._delivery_sampled,'baseline_evictions':self._baseline_evictions,'truncated':self._truncated}
    def _guard(self):
        if self._busy: raise ModelError('watch registry already owned')
    def _advance(self):
        if self._revision==U64_MAX or (self._last is not None and self._last.sequence==U64_MAX): raise ModelError('watch revision exhausted')
        self._revision+=1
    def _config(self,config):
        if type(config) is not WatchConfig: raise ValueError('expected WatchConfig')
        if not self._authorization.allows(config.selector.path): raise ModelError('unauthorized watch path')
        if config.selector.schema_version != self._schema_version: raise ModelError('watch schema mismatch')
        if (config.ttl_observations == 0 or config.limits.max_depth > 32
                or len(config.selector.path) > config.limits.max_depth
                or config.limits.max_bytes == 0
                or config.limits.max_bytes > self._limits.max_event_bytes):
            raise ModelError('invalid watch configuration limits')
        size=256+sum(_payload_bytes(s.value)+32 for s in config.selector.path)+_payload_bytes(config.equals)
        size+=sum(_payload_bytes(getattr(config.selector,k)) for k in ('model','machine','input_variant','output_variant','origin'))
        if size>self._limits.max_config_bytes: raise ModelError('watch config capacity')
        # Rust WatchRegistry::validate: page.limit == 0 || page.limit > max_page_size.
        if config.page.limit==0 or config.page.limit>config.limits.max_page_size: raise ModelError('invalid watch page')
    def _ack(self,id):
        e=self._entries[id]
        if self._last is not None and self._last.sequence==U64_MAX: raise ModelError('watch effective sequence exhausted')
        return WatchAck(id,e['generation'],self._revision,0 if self._last is None else self._last.sequence+1,_copy(e.get('validated_schema')),e.get('resolved_type'))
    def add(self,config):
        return self._add_prepared(config,None,None,None)
    def _add_prepared(self,config,schema,resolved_type,resolved_wire_type):
        self._guard(); self._config(config)
        if len(self._entries)>=self._limits.max_watches or self._next==U64_MAX: raise ModelError('watch capacity')
        config = _copy(config)
        self._advance(); id=self._next; self._next+=1
        self._entries[id]={'config':config,'generation':1,'state':'active' if config.enabled else 'disabled',
            'selected':0,'delivered':0,'baseline':None,'reset':'initial','validated_schema':schema,'resolved_type':resolved_type,'resolved_wire_type':resolved_wire_type,'effective_sequence':0 if self._last is None else self._last.sequence+1}
        self._lifecycle(id,'watch-enabled' if config.enabled else 'watch-disabled')
        return self._ack(id)
    def update(self,id,config):
        return self._update_prepared(id,config,None,None,None)
    def _update_prepared(self,id,config,schema,resolved_type,resolved_wire_type):
        self._guard(); _natural(id,'watch id'); self._config(config)
        e=self._entries.get(id)
        if e is None: raise ModelError('unknown watch')
        if e['generation']==U64_MAX: raise ModelError('watch generation exhausted')
        config = _copy(config)
        self._advance(); e.update(config=config,generation=e['generation']+1,state='active' if config.enabled else 'disabled',baseline=None,current=None,reset='reenabled',selected=0,delivered=0,sampled=False,validated_schema=schema,resolved_type=resolved_type,resolved_wire_type=resolved_wire_type,effective_sequence=0 if self._last is None else self._last.sequence+1)
        self._lifecycle(id,'watch-enabled' if config.enabled else 'watch-disabled')
        return self._ack(id)
    def add_validated(self,config,value):
        self._guard(); self._config(config)
        if len(self._entries)>=self._limits.max_watches or self._next==U64_MAX: raise ModelError('watch capacity')
        self._busy=True
        try:
            result=inspect(value,InspectQuery(path=config.selector.path,schema_version=config.selector.schema_version,page=config.page,limits=config.limits))
            if result.node.kind=='redacted': raise ModelError('redacted watch path')
        finally: self._busy=False
        return self._add_prepared(config,result.schema,result.node.kind,_scalar_wire_type(result.node) if result.node.kind=='scalar' else result.node.kind)
    def update_validated(self,id,config,value):
        self._guard(); _natural(id,'watch id'); self._config(config)
        if id not in self._entries: raise ModelError('unknown watch')
        self._busy=True
        try:
            result=inspect(value,InspectQuery(path=config.selector.path,schema_version=config.selector.schema_version,page=config.page,limits=config.limits))
            if result.node.kind=='redacted': raise ModelError('redacted watch path')
        finally: self._busy=False
        return self._update_prepared(id,config,result.schema,result.node.kind,_scalar_wire_type(result.node) if result.node.kind=='scalar' else result.node.kind)
    def set_state(self,id,state):
        self._guard(); _natural(id,'watch id')
        if state not in ('active','paused','disabled'): raise ValueError('invalid watch state')
        e=self._entries.get(id)
        if e is None: raise ModelError('unknown watch')
        if e['generation']==U64_MAX: raise ModelError('watch generation exhausted')
        self._advance(); e['generation']+=1; e['state']=state; e['baseline']=None; e['current']=None; e['reset']='reenabled'
        e['effective_sequence']=0 if self._last is None else self._last.sequence+1
        e['selected']=0; e['delivered']=0; e['sampled']=False
        e['config']=replace(e['config'],enabled=state=='active')
        self._lifecycle(id,'watch-'+('enabled' if state=='active' else state))
        return self._ack(id)
    def remove(self,id):
        self._guard(); _natural(id,'watch id')
        e=self._entries.get(id)
        if e is None: raise ModelError('unknown watch')
        if e['generation']==U64_MAX: raise ModelError('watch generation exhausted')
        self._advance(); e['generation']+=1; e['state']='removed'
        e['effective_sequence']=0 if self._last is None else self._last.sequence+1
        self._lifecycle(id,'watch-removed')
        ack=WatchAck(id,e['generation'],self._revision,e['effective_sequence'])
        del self._entries[id]
        return ack
    def list(self):
        return tuple({'id':id,'generation':e['generation'],'enabled':e['config'].enabled,'state':e['state'],'config':_copy(e['config']),'validated_schema':_copy(e.get('validated_schema')),'resolved_type':e.get('resolved_type'),'resolved_wire_type':e.get('resolved_wire_type'),'has_baseline':e['baseline'] is not None,'effective_sequence':e.get('effective_sequence',0)} for id,e in self._entries.items())
    def snapshot(self): return {'capture_revision':self._revision,'at':_copy(self._last),'events':_copy(tuple(e for e,size in self._ring)),'retained_bytes':self._bytes,'health':self.health}
    def current(self,id):
        _natural(id,'watch id')
        return _copy(self._entries.get(id,{}).get('current'))
    def clear_ring(self):
        self._guard(); self._ring.clear(); self._bytes=0
    def clear_failure_snapshot(self):
        self._guard(); self._failure=None
    @property
    def failure_snapshot(self): return _copy(self._failure)
    def observation_gap(self):
        self._guard(); self._gap()
    def _gap(self):
        self._gaps=min(U64_MAX,self._gaps+1)
        for id,e in self._entries.items():
            e['baseline']=None; e['reset']='observation-gap'; e['current']=None
            self._lifecycle(id,'observation-gap')
    def _lifecycle(self,id,kind):
        e=self._entries[id]
        config=e['config']; schema=e.get('validated_schema')
        if schema is not None:
            schema=replace(schema,name=schema.name.encode('utf8')[:1024].decode('utf8','ignore'))
        self._push(WatchEvent(id,e['generation'],self._revision,self._last or SnapshotId(),
            'diagnostic-control',kind,path=config.selector.path,schema=schema,
            model=config.selector.model,machine=config.selector.machine,phase='control',trigger=config.trigger))
    def _push(self,event):
        size=_event_bytes(event,self._limits.max_event_bytes)
        if size>self._limits.max_event_bytes or size>self._limits.max_ring_bytes or not self._limits.max_ring_records:
            self._dropped=min(U64_MAX,self._dropped+1); self._pending_dropped=min(U64_MAX,self._pending_dropped+1); return False
        retained=_copy(event)
        dropped=0
        while self._ring and (len(self._ring)>=self._limits.max_ring_records or self._bytes+size>self._limits.max_ring_bytes):
            old=self._ring.pop(0); self._bytes-=old[1]; dropped+=1
        self._dropped=min(U64_MAX,self._dropped+dropped)
        self._ring.append((replace(retained,dropped_before=min(U64_MAX,dropped+self._pending_dropped)),size)); self._bytes+=size; self._pending_dropped=0
        return True
    def _report_drops(self):
        if self._reported_drops==self._dropped: return
        marker=WatchEvent(0,0,self._revision,self._last or SnapshotId(),
            'diagnostic-control','events-dropped',phase='control',
            completeness='observation-gap',trigger='every',
            dropped_count=self._dropped-self._reported_drops)
        size=_event_bytes(marker,self._limits.max_event_bytes)
        if (len(self._ring)<self._limits.max_ring_records and size<=self._limits.max_event_bytes
                and self._bytes+size<=self._limits.max_ring_bytes):
            self._ring.append((marker,size)); self._bytes+=size
            self._reported_drops=self._dropped
    def _project(self,e,value,context):
        config=e['config']
        # Reserve the known event metadata before asking an inspector for data.
        metadata=WatchEvent(0,0,0,context.snapshot,context.origin,'snapshot',path=config.selector.path,
            failing_properties=_property_ids(context),model=context.model,machine=context.machine)
        budget=min(config.limits.max_bytes,max(0,self._limits.max_event_bytes-_event_bytes(metadata,self._limits.max_event_bytes)))
        result=inspect(value,InspectQuery(context.snapshot,config.selector.path,config.selector.schema_version,config.page,replace(config.limits,max_bytes=budget)))
        if result.node.kind=='redacted': raise ModelError('redacted watch path')
        return result
    def snapshot_now(self,id,value,context):
        self._guard(); _natural(id,'watch id'); e=self._entries.get(id)
        if e is None: raise ModelError('unknown watch')
        if type(context) is not ObservationContext: raise ValueError('expected ObservationContext')
        self._busy=True
        try:
            context=_copy(context)
            result=self._project(e,value,context)
            known=e.get('validated_schema')
            if known is not None and (known.name,known.version)!=(result.schema.name,result.schema.version): raise ModelError('watch schema changed')
            event=WatchEvent(id,e['generation'],self._revision,context.snapshot,context.origin,'snapshot',result.node,path=e['config'].selector.path,property_failure=context.property_failure,failing_properties=_property_ids(context),schema=_event_schema(result.schema),model=context.model,machine=context.machine,phase='control',completeness=_metadata_completeness(context,result.node.completeness,result.schema),trigger=e['config'].trigger)
            if _event_bytes(event,self._limits.max_event_bytes)>self._limits.max_event_bytes: raise ModelError('watch event capacity')
            self._push(event); return event
        finally: self._busy=False
    def observe(self,value,context):
        self._guard()
        if type(context) is not ObservationContext: raise ValueError('expected ObservationContext')
        self._busy=True
        try:
            context=_copy(context)
            gap=False
            if not self.interested:
                if self._last is not None and context.snapshot.session==self._last.session and context.snapshot.sequence<=self._last.sequence:
                    return ObservationSummary(stale=True)
                self._last=context.snapshot
                return ObservationSummary()
            if self._last is not None:
                if context.snapshot.session==self._last.session and context.snapshot.sequence<=self._last.sequence: return ObservationSummary(stale=True)
                if context.snapshot.session!=self._last.session or context.snapshot.sequence!=self._last.sequence+1:
                    self._gap(); gap=True
            self._last=context.snapshot
            selected=evaluated=emitted=inspection_errors=0; projections={}; metadata_error=None
            for id,e in self._entries.items():
                config=e['config']
                if e['state']!='active': continue
                if config.ttl_observations is not None and e['selected']>=config.ttl_observations:
                    e['state']='expired'; e['config']=replace(config,enabled=False); e['baseline']=None; e['current']=None; self._lifecycle(id,'watch-expired'); continue
                if not config.selector.matches(context):
                    self._filtered=min(U64_MAX,self._filtered+1); continue
                # A metadata callback is just as fallible as a projection callback.
                # Cache a bounded failure for this observation, without retaining or
                # formatting the host exception or retrying it for another watch.
                if metadata_error is None:
                    try:
                        actual_schema=_read_schema(value)
                    except Exception:
                        metadata_error=ModelError('inspector metadata callback failed')
                if metadata_error is None:
                    known=e.get('validated_schema')
                    if type(actual_schema) is not DisplaySchema or actual_schema.version!=config.selector.schema_version or (known is not None and (known.name,known.version)!=(actual_schema.name,actual_schema.version)):
                        e['state']='paused'; e['config']=replace(config,enabled=False); e['baseline']=None; e['current']=None; e['reset']='observation-gap'; self._lifecycle(id,'schema-changed'); continue
                    e['validated_schema']=actual_schema
                source=(context.model,context.machine,context.origin)
                # A source may change and change back between projections. Keep
                # the reset pending even when this boundary is sampled out.
                if e.get('current') is not None and e.get('source') != source:
                    e['reset']='source-changed'
                selected+=1; e['selected']=min(U64_MAX,e['selected']+1)
                if not context.property_failure and (e['selected']-1)%config.evaluation_interval:
                    self._evaluation_sampled=min(U64_MAX,self._evaluation_sampled+1)
                    e['sampled']=True
                    # Rust WatchRegistry::observe: sampled-out expiry keeps current/baseline.
                    if config.ttl_observations is not None and e['selected']>=config.ttl_observations:
                        e['state']='expired'; e['config']=replace(config,enabled=False); self._lifecycle(id,'watch-expired')
                    continue
                evaluated+=1
                self._evaluated=min(U64_MAX,self._evaluated+1)
                old=e['baseline']; previous=e.get('current'); reason=e['reset']
                e['source']=source
                if previous is not None and config.baseline_ttl_sequences is not None and context.snapshot.sequence-previous.snapshot.sequence>config.baseline_ttl_sequences:
                    reason='expired'
                if reason in ('source-changed','expired'): old=None
                try:
                    if metadata_error is not None: raise metadata_error
                    query=(config.selector.path,config.selector.schema_version,config.page,config.limits)
                    if query not in projections:
                        try: projections[query]=self._project(e,value,context)
                        except Exception as error: projections[query]=error
                    result=projections[query]
                    if isinstance(result,Exception): raise result
                    known=e.get('validated_schema')
                    if known is not None and (known.name,known.version)!=(result.schema.name,result.schema.version): raise ModelError('watch schema changed')
                    e['resolved_type']=result.node.kind
                    e['resolved_wire_type']=_scalar_wire_type(result.node) if result.node.kind=='scalar' else result.node.kind
                    if old is not None and (old.schema.name,old.schema.version)!=(result.schema.name,result.schema.version): old=None; reason='source-changed'
                    difference=compare_nodes(None if old is None else old.node,result.node,max_chars=config.limits.max_value_bytes)
                    sampled_changed=None
                    if not result.node.complete_with_budget(config.limits.max_value_bytes):
                        self._truncated=min(U64_MAX,self._truncated+1); kind='unknown'
                    elif old is None: kind='baseline:'+reason
                    elif e.get('sampled'):
                        kind='change-since-last-sample'; sampled_changed=difference=='changed'
                    else: kind='changed' if difference=='changed' else 'unchanged' if difference=='unchanged' else 'unknown'
                    e['sampled']=False
                    if kind.startswith('baseline:') or kind in ('unknown','change-since-last-sample'): difference='unknown'
                    e['baseline']=result if result.node.complete_with_budget(config.limits.max_value_bytes) else None; e['reset']='observation-gap' if not result.node.complete_with_budget(config.limits.max_value_bytes) else 'initial'
                    fire=context.property_failure or config.trigger=='every' or (config.trigger=='changed' and kind!='unchanged') or (config.trigger=='property_failure' and context.property_failure) or (config.trigger=='equals' and result.node.complete_with_budget(config.limits.max_value_bytes) and result.node.kind=='scalar' and result.node.value==config.equals)
                    if context.property_failure: kind='property-failure'
                    elif fire and config.trigger=='equals': kind='value-matched'
                    event=WatchEvent(id,e['generation'],self._revision,context.snapshot,context.origin,kind,result.node,
                        None if previous is None else previous.snapshot,difference,config.selector.path,context.property_failure,
                        _property_ids(context),schema=_event_schema(result.schema),sampled_changed=sampled_changed,model=context.model,machine=context.machine,
                        phase='initial' if context.snapshot.sequence==0 else 'post-turn',completeness=_metadata_completeness(context,'sampled' if sampled_changed is not None else result.node.completeness,result.schema),trigger=config.trigger)
                    if _event_bytes(event,self._limits.max_event_bytes)>self._limits.max_event_bytes: raise ModelError('watch event capacity')
                    e['current']=event
                    e['delivered']=min(U64_MAX,e['delivered']+int(fire))
                    if fire:
                        if context.property_failure or (e['delivered']-1)%config.delivery_interval==0: emitted+=int(self._push(event))
                        else: self._delivery_sampled=min(U64_MAX,self._delivery_sampled+1)
                except Exception as error:
                    inspection_errors+=1
                    self._inspection_errors=min(U64_MAX,self._inspection_errors+1)
                    e['baseline']=None; e['reset']='observation-gap'
                    # Do not invoke arbitrary host exception formatting here: even
                    # __str__ can throw. Only our exact schema diagnostics classify
                    # a schema change; callback messages are not control signals.
                    schema_error=(type(error) in (ModelError,InspectError) and len(error.args)==1
                        and type(error.args[0]) is str and error.args[0] in
                        ('watch schema changed','schema mismatch','invalid display schema'))
                    kind='schema-changed' if schema_error else 'inspector-error'
                    e['reset']='source-changed' if kind=='schema-changed' else 'observation-gap'
                    event=WatchEvent(id,e['generation'],self._revision,context.snapshot,context.origin,kind,path=config.selector.path,completeness='unavailable',property_failure=context.property_failure,
                        failing_properties=_property_ids(context),model=context.model,machine=context.machine,phase=context.phase,trigger=config.trigger)
                    code={'path not found':'PathNotFound','wrong path segment':'WrongPathSegment','invalid page':'InvalidPage','work limit':'WorkLimit','projection budget exceeded':'BudgetExceeded','path budget exceeded':'BudgetExceeded'}.get(error.args[0] if type(error) is InspectError and len(error.args)==1 and type(error.args[0]) is str else '', 'InvalidMetadata')
                    event=replace(event,inspection_error=code,previous_snapshot=None if previous is None else previous.snapshot)
                    e['current']=event if _event_bytes(event,self._limits.max_event_bytes)<=self._limits.max_event_bytes else None; e['sampled']=False
                    fire=context.property_failure or config.trigger in ('every','changed')
                    e['delivered']=min(U64_MAX,e['delivered']+int(fire))
                    if fire:
                        if context.property_failure or (e['delivered']-1)%config.delivery_interval==0: emitted+=int(self._push(event))
                        else: self._delivery_sampled=min(U64_MAX,self._delivery_sampled+1)
                if config.ttl_observations is not None and e['selected']>=config.ttl_observations:
                    # Rust keeps the just-evaluated current value; only pre-selection expiry clears it.
                    e['state']='expired'; e['config']=replace(config,enabled=False); self._lifecycle(id,'watch-expired')
                baseline_bytes=sum(_baseline_bytes(v,self._limits.max_event_bytes) for v in self._entries.values())
                if baseline_bytes>self._limits.max_baseline_bytes:
                    for candidate in sorted(self._entries.values(),key=lambda c: c['current'].snapshot.sequence if c.get('current') is not None else -1):
                        if candidate.get('current') is not None:
                            baseline_bytes-=_baseline_bytes(candidate,self._limits.max_event_bytes); candidate['baseline']=None; candidate['current']=None; candidate['reset']='evicted'
                            self._baseline_evictions=min(U64_MAX,self._baseline_evictions+1)
                            if baseline_bytes<=self._limits.max_baseline_bytes: break
            self._report_drops()
            if context.property_failure: self._failure=self.snapshot()
            return ObservationSummary(selected,evaluated,emitted,False,gap,inspection_errors)
        finally: self._busy=False
