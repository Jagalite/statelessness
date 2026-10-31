"""Explicit scoped probes with acknowledged config and independent sink queues.

Probe capture performs no exporter I/O, clock reads, reducer or recorder calls.
Callbacks are cooperative; reflection is not a security boundary.
"""
from dataclasses import dataclass, replace
from .core import ModelError, U64_MAX, _natural, _copy
from .debugger_inspect import inspect, InspectQuery, InspectLimits, SnapshotId, PageRequest, compare_nodes, _payload_bytes
from .debugger_inspect import PathSegment, DisplaySchema, InspectNode, _node_shape, _float_parts, _read_schema, _inspect_with_schema
from .debugger_watches import _node_bytes, _schema_bytes


@dataclass(frozen=True)
class SinkPermissions:
    sites: tuple[str,...] = ()
    paths: tuple[tuple,...] = ()
    @classmethod
    def local_all(cls): return cls(('*',),((),))
    def allows(self,subscription):
        return ('*' in self.sites or subscription.site in self.sites) and any(subscription.path[:len(p)]==p for p in self.paths)


@dataclass(frozen=True)
class SinkLimits:
    records: int = 1024
    bytes: int = 4*1024*1024
    policy: str = 'newest'
    def __post_init__(self):
        _natural(self.records,'records',1); _natural(self.bytes,'bytes',1)
        if self.policy not in ('newest','oldest'): raise ValueError('invalid drop policy')


@dataclass(frozen=True)
class DiagnosticLimits:
    """Limits for capture and retention; projection budgets default to InspectLimits."""
    producers: int = 64
    sinks: int = 16
    subscriptions: int = 64
    config_bytes: int = 65536
    turn_records: int = 256
    turn_bytes: int = 256*1024
    event_bytes: int = 32768
    baseline_bytes: int = 8*1024*1024
    projection_value_bytes: int = 4096
    projection_work: int = 10_000
    def __post_init__(self):
        for name in self.__dataclass_fields__: _natural(getattr(self,name),name)


@dataclass(frozen=True)
class Subscription:
    sink: int
    site: str
    kind: str = 'probe'
    path: tuple = ()
    trigger: str = 'every'
    equals: object = None
    threshold: int = 0
    hysteresis: int = 0
    cooldown_turns: int = 0
    sample_every: int = 1
    minimum_severity: str = 'debug'
    selector: tuple[tuple[str,object],...] = ()
    def __post_init__(self):
        _natural(self.sink,'sink'); _natural(self.sample_every,'sample interval',1)
        for key in ('threshold','hysteresis'): _natural(getattr(self,key),key,maximum=(1<<128)-1)
        _natural(self.cooldown_turns,'cooldown')
        if self.kind not in ('probe','marker') or self.trigger not in ('every','changed','equals','above-u128'): raise ValueError('invalid subscription')
        if self.minimum_severity not in ('trace','debug','info','warn','error'): raise ValueError('invalid severity')
        if self.trigger=='equals': _node_shape(InspectNode('scalar',self.equals))
        elif self.equals is not None: raise ValueError('unexpected equals payload')
        if type(self.site) is not str or not self.site or len(self.site)>256 or len(self.path)>32: raise ValueError('invalid subscription site/path')
        path=tuple(self.path)
        if any(type(s) is not PathSegment for s in path): raise ValueError('invalid subscription path')
        object.__setattr__(self,'path',path); object.__setattr__(self,'selector',tuple(tuple(p) for p in self.selector))


@dataclass(frozen=True)
class ConfigurationAck:
    capture_revision: int
    pending_producers: tuple[int,...]


@dataclass(frozen=True)
class ProducerAck:
    producer: int
    epoch: int
    capture_revision: int
    effective_transition: int


@dataclass(frozen=True)
class DiagnosticEvent:
    run_id: int
    producer: int
    epoch: int
    producer_sequence: int
    capture_revision: int
    transition: int
    origin: str
    severity: str
    site: str
    kind: str
    path: tuple
    payload: object = None
    display_schema: object = None
    baseline: bool = False
    baseline_reason: str | None = None
    change_unknown: bool = False
    incomplete_turn: bool = False


def _event_bytes(event, max_chars=256*1024): return 256+len(event.site.encode('utf8'))+sum(32+_payload_bytes(s.value) for s in event.path)+_node_bytes(event.payload,max_chars)+_schema_bytes(event.display_schema)

def _health_increment(health,key,amount=1):
    health[key]=min(U64_MAX,health[key]+amount)


class DiagnosticHub:
    def __init__(self,limits=DiagnosticLimits()):
        if type(limits) is not DiagnosticLimits: raise ValueError('expected DiagnosticLimits')
        self._limits=limits; self._revision=0; self._config=(); self._sinks={}; self._producers={}; self._owner=None
    @property
    def revision(self): return self._revision
    @property
    def limits(self): return self._limits
    def _guard(self):
        if self._owner is not None: raise ModelError('diagnostic hub already owned by turn/export')
    def add_sink(self,id,permissions=SinkPermissions(),limits=SinkLimits()):
        self._guard()
        admission=object();self._owner=admission
        try: return self._add_sink(id,permissions,limits)
        finally: self._owner=None
    def _add_sink(self,id,permissions,limits):
        _natural(id,'sink')
        if type(permissions) is not SinkPermissions or type(limits) is not SinkLimits: raise ValueError('invalid sink configuration types')
        if id in self._sinks or len(self._sinks)>=self._limits.sinks: raise ModelError('sink capacity')
        if len(permissions.sites)>self._limits.subscriptions or len(permissions.paths)>self._limits.subscriptions: raise ModelError('permission capacity')
        size=sum(_payload_bytes(s)+32 for s in permissions.sites)+sum(sum(_payload_bytes(s.value)+32 for s in p) for p in permissions.paths)
        if size>self._limits.config_bytes or any(len(p)>32 for p in permissions.paths): raise ModelError('permission capacity')
        self._sinks[id]={'permissions':_copy(permissions),'limits':limits,'queue':[],'bytes':0,
            'health':{k:0 for k in ('captured','filtered','sampled','truncated','dropped','exporter_failed','exported','abandoned','observation_gaps','inspection_failed','baseline_evicted')}}
    def register_producer(self,id,epoch):
        self._guard(); _natural(id,'producer'); _natural(epoch,'epoch',1)
        if id in self._producers or len(self._producers)>=self._limits.producers: raise ModelError('producer capacity')
        self._producers[id]={'epoch':epoch,'revision':0,'effective':0,'last':None,'source':None,'sequence':0,'config':[]}
    def configure(self,expected,subscriptions):
        self._guard()
        admission=object();self._owner=admission
        try: return self._configure(expected,subscriptions)
        finally: self._owner=None
    def _configure(self,expected,subscriptions):
        _natural(expected,'revision')
        if expected!=self._revision: raise ModelError('stale configuration revision')
        if type(subscriptions) not in (tuple,list) or len(subscriptions)>self._limits.subscriptions: raise ModelError('subscription capacity')
        size=0
        allowed={'producer','origin','model','machine','entity','correlation','input_variant','output_variant','property','effect_kind','source_site','property_failed'}
        for subscription in subscriptions:
            if type(subscription) is not Subscription: raise ValueError('expected Subscription')
            sink=self._sinks.get(subscription.sink)
            if sink is None: raise ModelError('unknown sink')
            if not sink['permissions'].allows(subscription): raise ModelError('unauthorized diagnostic site/path')
            if len(subscription.selector)>len(allowed) or len(dict(subscription.selector))!=len(subscription.selector): raise ModelError('invalid selector')
            for key,value in subscription.selector:
                if key not in allowed: raise ModelError('invalid selector')
                if key=='producer': _natural(value,'selector producer')
                elif key=='property_failed':
                    if type(value) is not bool: raise ModelError('invalid selector boolean')
                elif type(value) is not str or not value or _payload_bytes(value)>256: raise ModelError('invalid selector label')
            size+=256+_payload_bytes(subscription.site)+sum(32+_payload_bytes(s.value) for s in subscription.path)+_payload_bytes(subscription.equals)+_payload_bytes(subscription.selector)
        if size>self._limits.config_bytes: raise ModelError('configuration capacity')
        if self._revision==U64_MAX: raise ModelError('capture revision exhausted')
        staged=_copy(tuple(subscriptions))
        self._revision+=1; self._config=staged
        return ConfigurationAck(self._revision,tuple(sorted(self._producers)))
    def acknowledge(self,producer,revision,effective_transition):
        self._guard(); _natural(producer,'producer'); _natural(revision,'revision'); _natural(effective_transition,'effective transition')
        if revision!=self._revision: raise ModelError('stale configuration revision')
        p=self._producers.get(producer)
        if p is None: raise ModelError('unknown producer')
        if p['last'] is not None and effective_transition<=p['last']: raise ModelError('stale boundary')
        reason='initial' if p['revision']==0 else 'reconfigured'
        p.update(revision=revision,effective=effective_transition,source=None,
            config=[{'config':s,'baseline':None,'reason':reason,'eligible':0,'schema':None,'high':False,'last_alert':None} for s in self._config])
        return ProducerAck(producer,p['epoch'],revision,effective_transition)
    @property
    def pending_producers(self): return tuple(id for id in sorted(self._producers) if self._producers[id]['revision']!=self._revision)
    def begin_turn(self,producer,run_id,transition,origin='simulation',*,context=()):
        self._guard()
        admission=object();self._owner=admission
        try: return self._begin_turn(producer,run_id,transition,origin,context)
        finally:
            if self._owner is admission: self._owner=None
    def _begin_turn(self,producer,run_id,transition,origin,context):
        _natural(producer,'producer'); _natural(run_id,'run'); _natural(transition,'transition')
        p=self._producers.get(producer)
        if p is None: raise ModelError('unknown producer')
        if p['last'] is not None and transition<=p['last']: raise ModelError('stale boundary')
        if transition<p['effective']: raise ModelError('pending configuration boundary')
        if origin not in ('simulation','live','debug-controlled-live','test','replay','imported'): raise ValueError('invalid diagnostic origin')
        allowed={'model','machine','entity','correlation','input_variant','output_variant','property','effect_kind','source_site','property_failed'}
        # Context is caller-provided and may be an unbounded iterator. Consume
        # at most one past the closed set of supported label names.
        from collections.abc import Mapping
        from itertools import islice
        pairs=list(islice(iter(context.items() if isinstance(context,Mapping) else context),len(allowed)+1))
        if len(pairs)>len(allowed): raise ModelError('context label limit')
        labels=dict(pairs)
        for key,value in labels.items():
            if key not in allowed: raise ModelError('unknown capture context label')
            if key=='property_failed':
                if type(value) is not bool: raise ModelError('invalid property failure flag')
            elif type(value) is not str or _payload_bytes(value)>256: raise ModelError('context label limit')
        # Only modeled-instance identity belongs in a comparison baseline.
        # Event labels still select subscriptions, but may vary each turn.
        source=(run_id,origin,tuple(labels.get(key) for key in ('model','machine','entity','correlation')))
        matches=[all((producer if key=='producer' else origin if key=='origin' else labels.get(key))==value
            for key,value in e['config'].selector) for e in p['config']]
        selected=any(matches)
        changed=selected and p['source'] is not None and source!=p['source']
        gap=p['last'] is not None and transition!=p['last']+1
        if changed or gap:
            for e in p['config']: e.update(baseline=None,reason='source-changed' if changed else 'observation-gap',high=False,last_alert=None)
            if gap:
                for sink in {e['config'].sink for e in p['config']}: _health_increment(self._sinks[sink]['health'],'observation_gaps')
        for e,matched in zip(p['config'],matches):
            if not matched: e.update(baseline=None,reason='selector-gap',high=False,last_alert=None)
        p['last']=transition
        if selected: p['source']=source
        scope=ScopedDiagnostics(self,producer,run_id,transition,origin,labels)
        self._owner=scope
        return scope
    def health(self,id): return dict(self._sinks[id]['health'])
    def queued(self,id): return len(self._sinks[id]['queue']),self._sinks[id]['bytes']
    def peek(self,id): return _copy(self._sinks[id]['queue'][0][0]) if self._sinks[id]['queue'] else None
    def pop(self,id):
        self._guard(); sink=self._sinks[id]
        if not sink['queue']: return None
        event,size=sink['queue'].pop(0); sink['bytes']-=size
        return _copy(event)
    def _enqueue(self,id,event):
        sink=self._sinks[id]; size=_event_bytes(event,self.limits.event_bytes)
        if size>sink['limits'].bytes: _health_increment(sink['health'],'dropped'); return
        while len(sink['queue'])>=sink['limits'].records or sink['bytes']+size>sink['limits'].bytes:
            _health_increment(sink['health'],'dropped')
            if sink['limits'].policy=='newest': return
            _,old=sink['queue'].pop(0); sink['bytes']-=old
        sink['queue'].append((_copy(event),size)); sink['bytes']+=size; _health_increment(sink['health'],'captured')
    def drain_to(self,id,exporter,maximum):
        self._guard(); _natural(maximum,'maximum')
        written=0
        for _ in range(maximum):
            event=self.pop(id)
            if event is None: break
            self._owner=exporter
            try:
                try:
                    result=exporter(event)
                    if result is not None: raise ModelError('exporter must return None')
                except Exception:
                    _health_increment(self._sinks[id]['health'],'exporter_failed'); break
                written+=1; _health_increment(self._sinks[id]['health'],'exported')
            finally: self._owner=None
        return written
    def abandon(self,id):
        self._guard(); sink=self._sinks[id]; count=len(sink['queue'])
        _health_increment(sink['health'],'abandoned',count); sink['queue'].clear(); sink['bytes']=0
        return count


class ScopedDiagnostics:
    def __init__(self,hub,producer,run,transition,origin,context):
        self._hub,self._producer,self._run_id,self._transition,self._origin,self._context=hub,producer,run,transition,origin,context
        self._staged=[]; self._bytes=0; self._finished=False; self._capturing=False
    @property
    def producer(self): return self._producer
    @property
    def run_id(self): return self._run_id
    @property
    def transition(self): return self._transition
    @property
    def origin(self): return self._origin
    @property
    def context(self): return dict(self._context)
    def _ensure(self):
        if self._finished or self._hub._owner is not self: raise ModelError('diagnostic scope finished or foreign')
    def __enter__(self): return self
    def __exit__(self,kind,error,traceback): self.finish(kind is None)
    def _matches(self,s):
        return all((self._producer if k=='producer' else self._origin if k=='origin' else self._context.get(k))==v for k,v in s.selector)
    def interested(self,site,kind='probe',severity='debug'):
        self._ensure()
        levels=('trace','debug','info','warn','error')
        return any(e['config'].site==site and e['config'].kind==kind and levels.index(severity)>=levels.index(e['config'].minimum_severity) and self._matches(e['config']) for e in self._hub._producers[self._producer]['config'])
    def probe(self,site,value,*,severity='debug'):
        if not self.interested(site,'probe',severity): return
        if self._capturing: raise ModelError('probe capture already owned')
        self._capturing=True
        try: self._capture(site,'probe',severity,value())
        finally: self._capturing=False
    def marker(self,site,*,severity='debug'):
        if self._capturing: raise ModelError('probe capture already owned')
        if self.interested(site,'marker',severity): self._capture(site,'marker',severity,None)
    def _capture(self,site,kind,severity,value):
        hub=self._hub; p=hub._producers[self._producer]
        if p['sequence']==U64_MAX: return
        p['sequence']+=1; sequence=p['sequence']; projections={}
        display=None
        if kind=='probe':
            try: display=_read_schema(value)
            except Exception: pass
        levels=('trace','debug','info','warn','error')
        for e in p['config']:
            s=e['config']; sink=hub._sinks[s.sink]
            if s.site!=site or s.kind!=kind or not self._matches(s) or levels.index(severity)<levels.index(s.minimum_severity):
                if not self._matches(s): e.update(baseline=None,reason='selector-gap',high=False,last_alert=None)
                continue
            node=schema=None
            if kind=='probe':
                if s.path not in projections:
                    try:
                        if display is None: raise ModelError('invalid display schema')
                        result=_inspect_with_schema(value,InspectQuery(SnapshotId(self._run_id,self._transition,self._transition),s.path,schema_version=display.version,page=PageRequest(),limits=InspectLimits(max_bytes=max(0,hub.limits.event_bytes-512),max_value_bytes=hub.limits.projection_value_bytes,max_work=hub.limits.projection_work)),display)
                        projections[s.path]=(result.node,result.schema)
                    except Exception: projections[s.path]=(None,display)
                node,schema=projections[s.path]
                if node is None: _health_increment(sink['health'],'inspection_failed')
            if e['schema'] is not None and schema!=e['schema']: e.update(baseline=None,reason='schema-changed',high=False,last_alert=None)
            e['schema']=schema
            complete=node is not None and node.complete_with_budget(hub.limits.event_bytes)
            baseline=e['baseline'] is None
            reason=(e['reason'] or 'initial') if baseline else None; e['reason']=None
            unknown=kind=='probe' and (not complete or (reason is not None and reason!='initial'))
            changed=compare_nodes(e['baseline'],node,max_chars=hub.limits.event_bytes)=='changed'
            fire=s.trigger=='every' or (s.trigger=='changed' and (baseline or unknown or changed)) or (s.trigger=='equals' and complete and node.kind=='scalar' and node.value==s.equals)
            threshold_unavailable=False
            if s.trigger=='above-u128':
                numeric=None
                if complete and node.kind=='scalar' and type(node.value) is tuple and node.value[0] in ('python-int','u8','u16','u32','u64','u128','usize','i8','i16','i32','i64','i128','isize'):
                    try: numeric=int(node.value[1])
                    except (ValueError,TypeError): pass
                if numeric is not None and 0<=numeric<1<<128:
                    if numeric<=max(0,s.threshold-s.hysteresis): e['high']=False
                    fire=numeric>s.threshold and not e['high'] and (e['last_alert'] is None or self._transition-e['last_alert']>=s.cooldown_turns)
                    if fire: e['high']=True; e['last_alert']=self._transition
                else: fire=False; unknown=True; threshold_unavailable=True; e.update(high=False,last_alert=None,reason='type-unavailable')
            e['baseline']=node if complete and not threshold_unavailable else None
            if not complete and kind=='probe': e.update(reason='inspection-incomplete',high=False,last_alert=None)
            if not fire: _health_increment(sink['health'],'filtered'); continue
            e['eligible']=min(U64_MAX,e['eligible']+1)
            if (e['eligible']-1)%s.sample_every: _health_increment(sink['health'],'sampled'); continue
            if unknown: _health_increment(sink['health'],'truncated')
            event=DiagnosticEvent(self._run_id,self._producer,p['epoch'],sequence,p['revision'],self._transition,self._origin,severity,site,kind,s.path,node,schema,baseline,reason,unknown)
            size=_event_bytes(event,hub.limits.event_bytes)
            if size>hub.limits.event_bytes or len(self._staged)>=hub.limits.turn_records or self._bytes+size>hub.limits.turn_bytes:
                _health_increment(sink['health'],'dropped'); continue
            self._staged.append((s.sink,event)); self._bytes+=size
        total=sum(_node_bytes(e['baseline'],hub.limits.event_bytes) for producer in hub._producers.values() for e in producer['config'])
        if total>hub.limits.baseline_bytes:
            for producer_id in sorted(hub._producers):
                producer=hub._producers[producer_id]
                for e in producer['config']:
                    if e['baseline'] is not None:
                        total-=_node_bytes(e['baseline'],hub.limits.event_bytes); _health_increment(hub._sinks[e['config'].sink]['health'],'baseline_evicted'); e.update(baseline=None,reason='evicted',high=False,last_alert=None)
                        if total<=hub.limits.baseline_bytes: return
    def finish(self,complete_turn=True):
        self._ensure()
        if self._capturing: raise ModelError('cannot finish while capturing')
        if type(complete_turn) is not bool: raise ValueError('complete_turn must be bool')
        for sink,event in self._staged: self._hub._enqueue(sink,replace(event,incomplete_turn=not complete_turn))
        self._staged.clear(); self._finished=True; self._hub._owner=None


def event_json(event):
    """Lossless display JSON; integer fields are decimal strings, never floats.

    Keep the Python event envelope, with explicit byte/float payloads matching
    the Rust diagnostic value representation. This is not a replay codec.
    """
    from dataclasses import fields, is_dataclass
    import json
    if type(event) is not DiagnosticEvent: raise ValueError('expected DiagnosticEvent')
    def display(value):
        if type(value) is int: return str(value)
        if is_dataclass(value):
            result={field.name:display(getattr(value,field.name)) for field in fields(value)
                    if not (type(value) is InspectNode and field.name=='value')}
            if type(value) is InspectNode:
                if value.kind=='bytes':
                    result['value']={'hex':value.value.hex(),'total_bytes':display(value.total_bytes)}
                elif value.kind=='scalar' and _float_parts(value.value) is not None:
                    kind,decimal,bits=_float_parts(value.value)
                    result['kind']='float'
                    result['value']={'type':kind,'decimal':decimal,'bits':str(bits)}
                else: result['value']=display(value.value)
            return result
        if type(value) in (tuple,list): return [display(item) for item in value]
        return value
    return json.dumps(display(event),ensure_ascii=True,separators=(',',':'),allow_nan=False)


class JsonlSink:
    def __init__(self,writer,*,max_event_bytes=32768):
        _natural(max_event_bytes,'event bytes',1); self.writer,self.max_event_bytes=writer,max_event_bytes
    def __call__(self,event):
        from .trace_binary import _write_all
        data=event_json(event).encode('utf8')+b'\n'
        if len(data)>self.max_event_bytes: raise ModelError('diagnostic export event byte limit')
        _write_all(self.writer,data)


@dataclass(frozen=True)
class DiagnosticSidecar:
    version: int
    parent_digest: int
    parent_bytes: int
    model_metadata: object
    @classmethod
    def for_trace(cls,trace):
        from .debugger_workbench import artifact_identity
        identity=artifact_identity(trace)
        return cls(1,identity.fnv1a64,identity.bytes,_copy(trace.metadata))
    def validate(self,trace):
        if self!=type(self).for_trace(trace): raise ModelError('diagnostic sidecar does not match parent artifact')
    def write_jsonl(self,writer,events,*,max_records=1000,max_bytes=4*1024*1024,max_event_bytes=32768):
        from dataclasses import asdict
        from .trace_binary import _write_all
        import json
        _natural(max_records,'records'); _natural(max_bytes,'bytes'); _natural(max_event_bytes,'event bytes',1)
        head=json.dumps({'schema':'statelessness.debugger-diagnostic-sidecar/1','binding':asdict(self)},separators=(',',':'),ensure_ascii=True).encode()+b'\n'
        if len(head)>max_bytes or len(head)>max_event_bytes: raise ModelError('sidecar header byte limit')
        _write_all(writer,head); total=len(head); count=0; iterator=iter(events)
        while count<max_records:
            try: event=next(iterator)
            except StopIteration: return count
            encoded=event_json(event).encode()+b'\n'
            if len(encoded)>max_event_bytes or len(encoded)>max_bytes-total: raise ModelError('sidecar export byte limit')
            _write_all(writer,encoded); total+=len(encoded); count+=1
        sentinel=object()
        if next(iterator,sentinel) is not sentinel: raise ModelError('sidecar export record limit')
        return count
    @classmethod
    def read_header(cls,reader,*,max_bytes=32768):
        import json
        from .core import Metadata
        _natural(max_bytes,'header bytes',1)
        data=reader.readline(max_bytes+1)
        if type(data) is not bytes or len(data)>max_bytes or not data.endswith(b'\n'): raise ModelError('invalid or oversized sidecar header')
        def pairs(entries):
            result={}
            for key,value in entries:
                if key in result: raise ModelError('duplicate sidecar header key')
                result[key]=value
            return result
        try:
            value=json.loads(data,object_pairs_hook=pairs)
            if set(value)!={'schema','binding'} or value['schema']!='statelessness.debugger-diagnostic-sidecar/1': raise ValueError('invalid schema')
            binding=value['binding']
            if set(binding)!={'version','parent_digest','parent_bytes','model_metadata'}: raise ValueError('invalid binding fields')
            if binding['version']!=1 or type(binding['version']) is not int: raise ValueError('invalid binding version')
            _natural(binding['parent_digest'],'parent digest'); _natural(binding['parent_bytes'],'parent bytes')
            metadata=Metadata(**binding['model_metadata'])
            return cls(1,binding['parent_digest'],binding['parent_bytes'],metadata)
        except (ValueError,TypeError,KeyError) as error: raise ModelError('invalid diagnostic sidecar header') from error
