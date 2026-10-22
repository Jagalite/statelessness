"""Optional local ordered watch capture. No reducer, recorder, or clock calls.

Capture acknowledgements concern one ordered producer. Hosts must acknowledge
remote/multi-producer changes explicitly; a local ack is not remote activation.
"""
from dataclasses import dataclass, replace
from .core import ModelError, U64_MAX, _natural, _copy
from .debugger_inspect import (SnapshotId, InspectLimits, PageRequest, InspectQuery,
    inspect, compare_nodes, _payload_bytes, PathSegment, _node_shape, InspectNode, DisplaySchema)


def _node_bytes(node):
    if node is None: return 0
    return 128+_payload_bytes(node.value)+sum(96+_payload_bytes(c.label)+_payload_bytes(c.segment.value)+_payload_bytes(c.source)+_node_bytes(c.node) for c in node.children)


def _schema_bytes(schema):
    return 0 if schema is None else 64+_payload_bytes(schema.name)+_payload_bytes(schema.source)


def _result_bytes(result):
    return _node_bytes(result.node)+_schema_bytes(result.schema)+sum(32+_payload_bytes(s.value) for s in result.path)


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
    def __post_init__(self):
        if type(self.snapshot) is not SnapshotId or type(self.property_failure) is not bool: raise ValueError('invalid observation context')
        if type(self.failing_properties) not in (tuple,list) or len(self.failing_properties)>1024: raise ValueError('failing property list limit')
        if type(self.output_variants) not in (tuple,list) or len(self.output_variants)>1024: raise ValueError('output variant list limit')
        object.__setattr__(self,'failing_properties',tuple(self.failing_properties)); object.__setattr__(self,'output_variants',tuple(self.output_variants))
        for value in (self.model,self.machine,self.input_variant,self.output_variant):
            if value is not None and (type(value) is not str or _payload_bytes(value)>256): raise ValueError('invalid observation label')
        if any(type(p) is not str for p in self.failing_properties): raise ValueError('invalid failing property identity')
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


@dataclass(frozen=True)
class ObservationSummary:
    selected: int = 0
    evaluated: int = 0
    emitted: int = 0
    stale: bool = False


def _event_bytes(event):
    return 256+_node_bytes(event.value)+_schema_bytes(event.schema)+_payload_bytes(event.model)+_payload_bytes(event.machine)+sum(_payload_bytes(s.value)+32 for s in event.path)+sum(len(p.encode('utf8')) for p in event.failing_properties)


class WatchRegistry:
    def __init__(self, *, limits=WatchLimits(), authorization=WatchAuthorization()):
        if type(limits) is not WatchLimits or type(authorization) is not WatchAuthorization: raise ValueError('invalid watch registry types')
        self._limits,self._authorization=limits,authorization
        self._revision,self._next=0,1
        self._entries,self._ring={},[]
        self._bytes,self._dropped,self._gaps,self._pending_dropped=0,0,0,0
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
    def health(self): return {'dropped':self._dropped,'observation_gaps':self._gaps,'retained_bytes':self._bytes}
    def _guard(self):
        if self._busy: raise ModelError('watch registry already owned')
    def _advance(self):
        if self._revision==U64_MAX or (self._last is not None and self._last.sequence==U64_MAX): raise ModelError('watch revision exhausted')
        self._revision+=1
    def _config(self,config):
        if type(config) is not WatchConfig: raise ValueError('expected WatchConfig')
        if not self._authorization.allows(config.selector.path): raise ModelError('unauthorized watch path')
        size=256+sum(_payload_bytes(s.value)+32 for s in config.selector.path)+_payload_bytes(config.equals)
        size+=sum(_payload_bytes(getattr(config.selector,k)) for k in ('model','machine','input_variant','output_variant','origin'))
        if size>self._limits.max_config_bytes: raise ModelError('watch config capacity')
        if config.page.limit>config.limits.max_page_size: raise ModelError('invalid watch page')
    def _ack(self,id):
        e=self._entries[id]
        if self._last is not None and self._last.sequence==U64_MAX: raise ModelError('watch effective sequence exhausted')
        return WatchAck(id,e['generation'],self._revision,0 if self._last is None else self._last.sequence+1,e.get('validated_schema'),e.get('resolved_type'))
    def add(self,config):
        self._guard(); self._config(config)
        if len(self._entries)>=self._limits.max_watches or self._next==U64_MAX: raise ModelError('watch capacity')
        self._advance(); id=self._next; self._next+=1
        self._entries[id]={'config':_copy(config),'generation':1,'state':'active' if config.enabled else 'disabled',
            'selected':0,'delivered':0,'baseline':None,'reset':'initial'}
        self._lifecycle(id,'watch-enabled' if config.enabled else 'watch-disabled')
        return self._ack(id)
    def update(self,id,config):
        self._guard(); _natural(id,'watch id'); self._config(config)
        e=self._entries.get(id)
        if e is None: raise ModelError('unknown watch')
        if e['generation']==U64_MAX: raise ModelError('watch generation exhausted')
        self._advance(); e.update(config=_copy(config),generation=e['generation']+1,state='active' if config.enabled else 'disabled',baseline=None,current=None,reset='reenabled',selected=0,delivered=0,validated_schema=None,resolved_type=None)
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
        ack=self.add(config); self._entries[ack.watch_id].update(validated_schema=result.schema,resolved_type=result.node.kind)
        return self._ack(ack.watch_id)
    def update_validated(self,id,config,value):
        self._guard(); _natural(id,'watch id'); self._config(config)
        if id not in self._entries: raise ModelError('unknown watch')
        self._busy=True
        try:
            result=inspect(value,InspectQuery(path=config.selector.path,schema_version=config.selector.schema_version,page=config.page,limits=config.limits))
            if result.node.kind=='redacted': raise ModelError('redacted watch path')
        finally: self._busy=False
        self.update(id,config); self._entries[id].update(validated_schema=result.schema,resolved_type=result.node.kind)
        return self._ack(id)
    def set_state(self,id,state):
        self._guard(); _natural(id,'watch id')
        if state not in ('active','paused','disabled'): raise ValueError('invalid watch state')
        e=self._entries.get(id)
        if e is None: raise ModelError('unknown watch')
        if e['generation']==U64_MAX: raise ModelError('watch generation exhausted')
        self._advance(); e['generation']+=1; e['state']=state; e['baseline']=None; e['current']=None; e['reset']='reenabled'
        e['config']=replace(e['config'],enabled=state!='disabled')
        self._lifecycle(id,'watch-'+('enabled' if state=='active' else state))
        return self._ack(id)
    def remove(self,id):
        self._guard(); _natural(id,'watch id')
        if id not in self._entries: raise ModelError('unknown watch')
        ack=self.set_state(id,'disabled'); del self._entries[id]
        return ack
    def list(self):
        return tuple({'id':id,'generation':e['generation'],'state':e['state'],'config':_copy(e['config']),'has_baseline':e['baseline'] is not None} for id,e in self._entries.items())
    def snapshot(self): return {'capture_revision':self._revision,'at':self._last,'events':_copy(tuple(e for e,size in self._ring)),'health':self.health}
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
        self._gaps+=1
        for id,e in self._entries.items():
            e['baseline']=None; e['reset']='observation-gap'; e['current']=None
            self._lifecycle(id,'observation-gap')
    def _lifecycle(self,id,kind):
        e=self._entries[id]
        self._push(WatchEvent(id,e['generation'],self._revision,self._last or SnapshotId(),'diagnostic-control',kind,path=e['config'].selector.path))
    def _push(self,event):
        size=_event_bytes(event)
        if size>self._limits.max_event_bytes or size>self._limits.max_ring_bytes or not self._limits.max_ring_records:
            self._dropped=min(U64_MAX,self._dropped+1); self._pending_dropped=min(U64_MAX,self._pending_dropped+1); return False
        dropped=0
        while self._ring and (len(self._ring)>=self._limits.max_ring_records or self._bytes+size>self._limits.max_ring_bytes):
            old=self._ring.pop(0); self._bytes-=old[1]; dropped+=1
        self._dropped=min(U64_MAX,self._dropped+dropped)
        self._ring.append((replace(event,dropped_before=min(U64_MAX,dropped+self._pending_dropped)),size)); self._bytes+=size; self._pending_dropped=0
        return True
    def _project(self,e,value,context):
        config=e['config']
        budget=min(config.limits.max_bytes,max(0,self._limits.max_event_bytes-256))
        result=inspect(value,InspectQuery(context.snapshot,config.selector.path,config.selector.schema_version,config.page,replace(config.limits,max_bytes=budget)))
        if result.node.kind=='redacted': raise ModelError('redacted watch path')
        return result
    def snapshot_now(self,id,value,context):
        self._guard(); _natural(id,'watch id'); e=self._entries.get(id)
        if e is None: raise ModelError('unknown watch')
        self._busy=True
        try:
            result=self._project(e,value,context)
            known=e.get('validated_schema')
            if known is not None and (known.name,known.version)!=(result.schema.name,result.schema.version): raise ModelError('watch schema changed')
            event=WatchEvent(id,e['generation'],self._revision,context.snapshot,context.origin,'snapshot',result.node,path=e['config'].selector.path,schema=result.schema,model=context.model,machine=context.machine,phase='control',completeness=result.node.completeness,trigger=e['config'].trigger)
            self._push(event); return event
        finally: self._busy=False
    def observe(self,value,context):
        self._guard()
        if type(context) is not ObservationContext: raise ValueError('expected ObservationContext')
        self._busy=True
        try:
            if self._last is not None:
                if context.snapshot.session==self._last.session and context.snapshot.sequence<=self._last.sequence: return ObservationSummary(stale=True)
                if context.snapshot.session!=self._last.session or context.snapshot.sequence!=self._last.sequence+1: self._gap()
            self._last=context.snapshot
            selected=evaluated=emitted=0; projections={}
            for id,e in self._entries.items():
                config=e['config']
                if e['state']!='active': continue
                if config.ttl_observations is not None and e['selected']>=config.ttl_observations:
                    e['state']='expired'; e['config']=replace(config,enabled=False); e['baseline']=None; e['current']=None; self._lifecycle(id,'watch-expired'); continue
                if not config.selector.matches(context): continue
                actual_schema=value.schema() if hasattr(value,'schema') else DisplaySchema(type(value).__name__)
                known=e.get('validated_schema')
                if type(actual_schema) is not DisplaySchema or actual_schema.version!=config.selector.schema_version or (known is not None and (known.name,known.version)!=(actual_schema.name,actual_schema.version)):
                    e['state']='paused'; e['config']=replace(config,enabled=False); e['baseline']=None; e['current']=None; e['reset']='observation-gap'; self._lifecycle(id,'schema-changed'); continue
                e['validated_schema']=actual_schema
                selected+=1; e['selected']+=1
                if not context.property_failure and (e['selected']-1)%config.evaluation_interval:
                    e['sampled']=True
                    if config.ttl_observations is not None and e['selected']>=config.ttl_observations:
                        e['state']='expired'; e['config']=replace(config,enabled=False); e['baseline']=None; e['current']=None; self._lifecycle(id,'watch-expired')
                    continue
                evaluated+=1
                old=e['baseline']; reason=e['reset']
                source=(context.model,context.machine,context.origin)
                if e.get('source') is not None and e['source']!=source: old=None; reason='source-changed'
                e['source']=source
                if old is not None and config.baseline_ttl_sequences is not None and context.snapshot.sequence-old.snapshot.sequence>config.baseline_ttl_sequences:
                    old=None; reason='expired'
                try:
                    query=(config.selector.path,config.selector.schema_version,config.page,config.limits)
                    if query not in projections:
                        try: projections[query]=self._project(e,value,context)
                        except Exception as error: projections[query]=error
                    result=projections[query]
                    if isinstance(result,Exception): raise result
                    known=e.get('validated_schema')
                    if known is not None and (known.name,known.version)!=(result.schema.name,result.schema.version): raise ModelError('watch schema changed')
                    if old is not None and old.schema!=result.schema: old=None; reason='source-changed'
                    difference=compare_nodes(None if old is None else old.node,result.node)
                    sampled_changed=None
                    if not result.node.is_complete: kind='unknown'
                    elif old is None: kind='baseline:'+reason
                    elif e.get('sampled'):
                        kind='change-since-last-sample'; sampled_changed=difference=='changed'
                    else: kind='changed' if difference=='changed' else 'unchanged' if difference=='unchanged' else 'unknown'
                    e['sampled']=False
                    if kind.startswith('baseline:') or kind in ('unknown','change-since-last-sample'): difference='unknown'
                    e['baseline']=result if result.node.is_complete else None; e['reset']='observation-gap' if not result.node.is_complete else 'initial'
                    fire=context.property_failure or config.trigger=='every' or (config.trigger=='changed' and kind!='unchanged') or (config.trigger=='property_failure' and context.property_failure) or (config.trigger=='equals' and result.node.is_complete and result.node.kind=='scalar' and result.node.value==config.equals)
                    if context.property_failure: kind='property-failure'
                    elif fire and config.trigger=='equals': kind='value-matched'
                    event=WatchEvent(id,e['generation'],self._revision,context.snapshot,context.origin,kind,result.node,
                        None if old is None else old.snapshot,difference,config.selector.path,context.property_failure,
                        tuple(p[:256] for p in context.failing_properties[:32]),schema=result.schema,sampled_changed=sampled_changed,model=context.model,machine=context.machine,
                        phase='initial' if context.snapshot.sequence==0 else 'post-turn',completeness='sampled' if sampled_changed is not None else result.node.completeness,trigger=config.trigger)
                    e['current']=event
                    e['delivered']+=int(fire)
                    if fire and (context.property_failure or (e['delivered']-1)%config.delivery_interval==0): emitted+=int(self._push(event))
                except Exception as error:
                    e['baseline']=None; e['reset']='initial'
                    kind='schema-changed' if 'schema' in str(error) else 'inspector-error'
                    e['reset']='source-changed' if kind=='schema-changed' else 'initial'
                    event=WatchEvent(id,e['generation'],self._revision,context.snapshot,context.origin,kind,path=config.selector.path,completeness='unavailable')
                    e['current']=event; emitted+=int(self._push(event))
                if config.ttl_observations is not None and e['selected']>=config.ttl_observations:
                    e['state']='expired'; e['config']=replace(config,enabled=False); e['baseline']=None; e['current']=None; self._lifecycle(id,'watch-expired')
                baseline_bytes=sum(_event_bytes(v['current'])+128 for v in self._entries.values() if v.get('current') is not None)
                if baseline_bytes>self._limits.max_baseline_bytes:
                    for candidate in sorted(self._entries.values(),key=lambda c: c['current'].snapshot.sequence if c.get('current') is not None else -1):
                        if candidate.get('current') is not None:
                            baseline_bytes-=_event_bytes(candidate['current'])+128; candidate['baseline']=None; candidate['current']=None; candidate['reset']='evicted'
                            if baseline_bytes<=self._limits.max_baseline_bytes: break
            if context.property_failure: self._failure=self.snapshot()
            return ObservationSummary(selected,evaluated,emitted)
        finally: self._busy=False
