"""Concrete optional protocol attachments; producer acknowledgement stays host-owned."""
from dataclasses import dataclass, replace
from .debugger_protocol import ProtocolError, encode_response, _U64
from .debugger_inspect import PathSegment, SnapshotId, InspectLimits, PageRequest
from .debugger_watches import WatchRegistry, WatchConfig, WatchSelector, ObservationContext
from .debugger_diagnostic import DiagnosticHub, Subscription


def n(value): return f'u64:{value}'
def b(value): return 'bool:'+str(bool(value)).lower()
def variant(value): return ''.join(part.capitalize() for part in value.replace('-','_').split('_'))
def segments(path):
    return tuple(PathSegment(kind,value) if kind in ('field','variant','index') else PathSegment('map-key',value if kind in ('key.string','key.bool') else (kind[4:],str(value))) for kind,value in path)
def segment(value):
    if value.kind!='map-key': return f'{value.kind}:{value.value}'
    item=value.value
    if isinstance(item,tuple): return f'key.{item[0]}:{item[1]}'
    return 'key.bool:'+str(item).lower() if type(item) is bool else 'key.string:'+item

def node_fields(prefix,node,depth=0):
    if depth>32: raise ProtocolError('limit')
    kind=node.kind;value=node.value
    fields=[(prefix+'.completeness','enum:'+('Complete' if node.completeness=='complete' else 'Partial('+variant(node.completeness)+')')),(prefix+'.child_count','none' if node.child_count is None else n(node.child_count))]
    if node.page is not None:
        fields.extend((prefix+'.page.'+k,v) for k,v in [('offset',n(node.page.offset)),('returned',n(node.page.returned)),('total',n(node.page.total)),('next','none' if node.page.next_offset is None else n(node.page.next_offset))])
    wire='none'
    if kind=='scalar':
        tag,payload,*extra=value
        if tag=='float' and len(extra)==2: kind='float';wire=payload+':'+extra[0];fields.append((prefix+'.bits',n(extra[1])))
        elif tag in ('f32','f64'): kind='float';wire=tag+':'+payload;fields.append((prefix+'.bits',n(extra[0])))
        elif tag=='python-int' or tag.startswith(('u','i')) and tag!='unit': kind='integer';wire=tag+':'+str(payload)
        else: kind=tag;wire='unit' if tag=='unit' else b(payload) if tag=='bool' else 'char:'+payload
    elif kind=='enum':
        name,id,label=value;wire='string:'+name;fields.extend(((prefix+'.variant_id','string:'+id),(prefix+'.variant_label','string:'+label)))
    elif kind=='bytes': wire='bytes:'+value.hex();fields.append((prefix+'.total_bytes',n(node.total_bytes)))
    elif kind=='string': wire='string:'+value;fields.append((prefix+'.total_bytes',n(node.total_bytes)))
    elif kind in ('object','sequence','opaque'): wire='string:'+value
    elif kind=='map': wire='enum:'+variant(node.ordering)
    elif kind=='truncated': wire='enum:'+variant(node.completeness)
    fields.extend(((prefix+'.kind','enum:'+kind),(prefix+'.value',wire),(prefix+'.children.returned',n(len(node.children)))))
    for i,child in enumerate(node.children):
        key=f'{prefix}.children.{i}';fields.extend(((key+'.segment',segment(child.segment)),(key+'.label','string:'+child.label)));fields.extend(node_fields(key+'.node',child.node,depth+1))
    return fields

def service_error(error):
    # Host exceptions may have arbitrary or throwing __str__ implementations.
    # Only exact built-in diagnostic types with an exact string argument may
    # contribute to protocol classification.
    from .core import ModelError
    from .debugger_inspect import InspectError
    if type(error) not in (ModelError, InspectError, ValueError) or len(error.args)!=1 or type(error.args[0]) is not str:
        return ProtocolError('callback')
    text=error.args[0]
    for words,code in [(('unauthorized','redacted'),'unauthorized'),(('capacity','budget'),'limit'),(('unknown watch',),'stale_handle'),(('exhausted',),'exhausted'),(('owned',),'busy')]:
        if any(word in text for word in words): return ProtocolError(code)
    return ProtocolError('invalid_input')

class WatchAttachment:
    def __init__(self,protocol,registry,project):
        if type(registry) is not WatchRegistry or project is not None and not callable(project): raise ValueError('typed watch registry and projection required')
        self.p,self.registry,self.project=protocol,registry,project
    def observe(self):
        s=self.p.session
        failed=s.stop is not None and s.stop.kind=='property_failure'
        # The session owns these already-validated checks. Reading only their
        # immutable fields avoids copying arbitrary model state for diagnostics.
        checks=s._initial_checks if s.sequence==0 else (() if s._last is None else s._last.checks)
        ids=[]; ids_complete=True
        if failed:
            # Check lists are host-controlled: cap traversal even when no IDs
            # can be retained (for example, unrepresentable Python strings).
            if len(checks)>1024: ids_complete=False
            for check in checks[:1024]:
                if check.status!='failed': continue
                if len(ids)==16:
                    ids_complete=False
                    break
                prefix=check.id[:256]
                if len(check.id)>256: ids_complete=False
                if any(0xD800 <= ord(c) <= 0xDFFF for c in prefix):
                    ids_complete=False
                    continue
                ids.append(prefix)
        context=ObservationContext(SnapshotId(s.epoch,s.revision,s.sequence),origin='out-of-domain-injection' if s.policy.unrestricted else 'simulation',property_failure=failed,failing_properties=tuple(ids),phase='initial' if s.sequence==0 else 'post-turn',failing_properties_complete=ids_complete)
        def capture():
            value=None
            if self.registry.interested:
                value=s.state
                if self.project is not None: value=self.project(value)
            self.registry.observe(value,context)
        self.p._encode_owned(capture)
    def execute(self,r,args):
        p=self.p;registry=self.registry;c=r.command
        def reserve(fields): encode_response(replace(p._response(r.request_id,fields=fields),revision=_U64,configuration=_U64),p.limits)
        try:
            if c=='watch':
                schema,baseline,path=args
                if r.expected_revision!=p.session.revision or baseline!=p.session.revision: raise ProtocolError('stale_revision')
                limits=InspectLimits(max_bytes=min(registry.limits.max_event_bytes,p.limits.max_frame_bytes//8),max_value_bytes=p.limits.max_field_bytes//4,max_nodes=128,max_depth=16,max_page_size=p.limits.max_page_size)
                config=WatchConfig(selector=WatchSelector(segments(path),schema),limits=limits,page=PageRequest(0,p.limits.max_page_size))
                fields=[('watch.id',n(_U64)),('watch.generation',n(_U64)),('watch.effective_sequence',n(_U64)),('watch.installed_at_revision',n(baseline)),('watch.baseline','enum:pending_at_effective_boundary'),('watch.metadata_complete','bool:false')];reserve(fields)
                def add():
                    value=p.session.state
                    if self.project is not None:
                        try: value=self.project(value)
                        except Exception: raise ProtocolError('callback') from None
                    return registry.add_validated(config,value)
                ack=p._encode_owned(add);fields[:3]=[('watch.id',n(ack.watch_id)),('watch.generation',n(ack.generation)),('watch.effective_sequence',n(ack.effective_sequence))]
                extra=[('watch.display_schema_version',n(ack.display_schema.version)),('watch.display_schema','string:'+ack.display_schema.name),('watch.resolved_type','enum:'+registry._entries[ack.watch_id]['resolved_wire_type'])]
                metadata_complete=True
                for item in extra:
                    try: reserve(fields+[item])
                    except ProtocolError: metadata_complete=False
                    else: fields.append(item)
                if metadata_complete: fields[5]=('watch.metadata_complete','bool:true')
                p._publish('watch_configured');return tuple(fields)
            if c=='unwatch':
                reserve([('watch.id',n(_U64)),('watch.generation',n(_U64)),('watch.effective_sequence',n(_U64))]);ack=registry.remove(args[0]);p._publish('watch_removed')
                return (('watch.id',n(ack.watch_id)),('watch.generation',n(ack.generation)),('watch.effective_sequence',n(ack.effective_sequence)))
            if c=='watch_status':
                statuses=registry.list();total=len(statuses)
                health=(('watches.dropped_events',n(registry.health['dropped'])),('watches.observation_gaps',n(registry.health['observation_gaps'])),('watches.inspection_errors',n(registry.health.get('inspection_errors',0))))
                maximum=min(total,p.limits.max_page_size,max(0,(p.limits.max_response_fields-5)//5))
                rows=[]
                for i,status in enumerate(statuses[:maximum]):
                    rows.append(tuple((f'watches.{i}.{k}',v) for k,v in [('id',n(status['id'])),('generation',n(status['generation'])),('enabled',b(status['config'].enabled)),('has_baseline',b(status['has_baseline'])),('effective_sequence',n(status['effective_sequence']))]))
                def page(count):
                    # Rust protocol.rs Command::WatchStatus: total, truncated, health counters, then rows.
                    return (('watches.total',n(total)),('watches.truncated',b(count<total)))+health+tuple(field for row in rows[:count] for field in row)
                def fits(count):
                    try: encode_response(p._response(r.request_id,fields=page(count)),p.limits);return True
                    except ProtocolError as error:
                        if error.code!='limit': raise
                        return False
                if not fits(0): raise ProtocolError('limit')
                low,high=0,maximum
                while low<high:
                    mid=(low+high+1)//2
                    if fits(mid): low=mid
                    else: high=mid-1
                return page(low)
            event=registry.current(args[0])
            if event is None: return (('watch.value','enum:unavailable'),)
            fields=[('watch.'+k,v) for k,v in [('id',n(event.watch_id)),('generation',n(event.generation)),('capture_revision',n(event.capture_revision)),('snapshot_revision',n(event.snapshot.revision)),('snapshot_sequence',n(event.snapshot.sequence)),('kind','enum:'+('Baseline('+variant(event.kind.split(':',1)[1])+')' if event.kind.startswith('baseline:') else 'InspectorError('+event.inspection_error+')' if event.kind=='inspector-error' else 'ChangeSinceLastSample { changed: '+str(bool(event.sampled_changed)).lower()+' }' if event.kind=='change-since-last-sample' else variant(event.kind))),('completeness','enum:'+('Complete' if event.completeness=='complete' else 'Partial('+variant(event.completeness)+')')),('origin','enum:'+variant(event.origin))]]
            if event.value is not None: fields+=node_fields('watch.node',event.value)
            return tuple(fields)
        except ProtocolError: raise
        except Exception as error: raise p._encode_owned(lambda:service_error(error)) from None

@dataclass(frozen=True)
class ProbeProfile:
    id: int
    label: str
    subscription: Subscription

class ProbeAttachment:
    def __init__(self,p,hub,profiles,sinks):
        if type(hub) is not DiagnosticHub: raise ValueError('typed diagnostic hub required')
        if hub._owner is not None: raise ProtocolError('busy')
        from itertools import islice
        profiles=tuple(islice(profiles,p.limits.max_probe_profiles+1));sinks=tuple(islice(sinks,p.limits.max_probe_profiles+1))
        if len(profiles)>p.limits.max_probe_profiles or len(sinks)>p.limits.max_probe_profiles or hub.limits.producers>p.limits.max_probe_profiles or hub.limits.subscriptions>p.limits.max_probe_profiles: raise ProtocolError('limit')
        if any(type(v) is not ProbeProfile or type(v.id) is not int or not 0<v.id<=_U64 or type(v.label) is not str or len(v.label.encode())>min(128,p.limits.max_field_bytes-7) or type(v.subscription) is not Subscription for v in profiles) or len({v.id for v in profiles})!=len(profiles): raise ProtocolError('invalid_input')
        if len(set(sinks))!=len(sinks) or any(type(s) is not int or not 0<=s<=_U64 or s not in hub._sinks for s in sinks): raise ProtocolError('invalid_input')
        from .debugger_inspect import _payload_bytes
        cost=sum(256+len(v.label.encode())+_payload_bytes(v.subscription.site)+_payload_bytes(v.subscription.selector)+_payload_bytes(v.subscription.equals)+sum(64+_payload_bytes(s.value) for s in v.subscription.path) for v in profiles)+len(sinks)*8
        if cost>p.limits.max_probe_profile_bytes: raise ProtocolError('limit')
        from copy import deepcopy
        self.p,self.hub,self.profiles,self.sinks=p,hub,deepcopy(profiles),sinks;self.selected=None
    def execute(self,r,args):
        p,h=self.p,self.hub;fields=[]
        if h._owner is not None: raise ProtocolError('busy')
        def reserve(): encode_response(p._response(r.request_id,fields=fields),p.limits)
        if r.command=='probe_configure':
            expected,*ids=args
            # The decoder returns the profile vector as one value.
            if len(ids)==1 and isinstance(ids[0],tuple): ids=list(ids[0])
            if len(ids)!=len(set(ids)): raise ProtocolError('invalid_input')
            approved={v.id:v.subscription for v in self.profiles}
            if any(id not in approved for id in ids): raise ProtocolError('unauthorized')
            if expected!=h.revision: raise ProtocolError('stale_configuration')
            fields=[('probes.capture_revision',n(_U64)),('probes.selected_count',n(len(ids))),('probes.pending_producers.total',n(len(h._producers))),('probes.pending_producers.truncated',b(len(h._producers)>p.limits.max_page_size))]
            reserve()
            for i,id in enumerate(sorted(h._producers)[:p.limits.max_page_size]):
                fields.append((f'probes.pending_producers.{i}',n(id)))
                try: reserve()
                except ProtocolError as error:
                    if error.code!='limit': raise
                    fields.pop();fields[3]=('probes.pending_producers.truncated','bool:true');break
            try: ack=h.configure(expected,[approved[id] for id in ids])
            except Exception as error: raise p._encode_owned(lambda:service_error(error)) from None
            fields[0]=('probes.capture_revision',n(ack.capture_revision));self.selected=(h.revision,tuple(ids));return tuple(fields)
        if r.command=='probe_status':
            offset,limit=args;producers=sorted(h._producers.items());pending=h.pending_producers
            total=max(len(producers),len(self.profiles),len(self.sinks));fields=[('probes.capture_revision',n(h.revision)),('probes.producers.total',n(len(producers))),('probes.producers.truncated',b(offset+limit<len(producers))),('probes.pending_producers.total',n(len(pending)))]
            for i,(id,producer) in enumerate(producers[offset:offset+limit]):
                fields.extend((f'probes.producers.{i}.{k}',v) for k,v in [('id',n(id)),('epoch',n(producer['epoch'])),('capture_revision',n(producer['revision'])),('effective_transition',n(producer['effective'])),('pending',b(id in pending))])
            fields.extend((('probes.status_live','bool:true'),('probes.offset',n(offset)),('probes.next_offset',n(offset+limit) if offset+limit<total else 'none'),('probes.profiles.total',n(len(self.profiles))),('probes.profiles.truncated',b(offset+limit<len(self.profiles)))))
            for i,v in enumerate(self.profiles[offset:offset+limit]): fields.extend(((f'probes.profiles.{i}.id',n(v.id)),(f'probes.profiles.{i}.label','string:'+v.label)))
            known=self.selected is not None and self.selected[0]==h.revision;fields.append(('probes.selected_profiles_known',b(known)))
            if known: fields.extend((f'probes.selected.{i}',n(id)) for i,id in enumerate(self.selected[1][offset:offset+limit]))
            fields.append(('probes.sinks.total',n(len(self.sinks))))
            for i,id in enumerate(self.sinks[offset:offset+limit]):
                records,size=h.queued(id);prefix=f'probes.sinks.{i}';fields.extend(((prefix+'.id',n(id)),(prefix+'.queued_records',n(records)),(prefix+'.queued_bytes',n(size))))
                health=h.health(id)
                fields.extend((prefix+'.health.'+key,n(health.get(key,0))) for key in ('captured','filtered','sampled','dropped','truncated','exporter_failed','abandoned','inspection_failed','baseline_evicted','observation_gaps'))
            return tuple(fields)
        sink,limit,payload=args
        if sink not in self.sinks: raise ProtocolError('unauthorized')
        health=h.health(sink);fields=[('probes.sink',n(sink)),('probes.capture_revision',n(h.revision)),('probes.sink.health.dropped',n(health['dropped'])),('probes.sink.health.exporter_failed',n(health['exporter_failed']))]
        for i,(event,_) in enumerate(h._sinks[sink]['queue'][:limit]):
            key=f'probes.events.{i}'
            fields.extend((key+'.'+k,n(v)) for k,v in [('schema_version',1),('run',event.run_id),('producer',event.producer),('epoch',event.epoch),('producer_sequence',event.producer_sequence),('capture_revision',event.capture_revision),('transition',event.transition)])
            fields.extend((key+'.'+k,'enum:'+variant(getattr(event,k))) for k in ('origin','severity','kind'))
            fields.append((key+'.baseline',b(event.baseline)))
            fields.append((key+'.baseline_reason','none' if event.baseline_reason is None else 'enum:'+variant(event.baseline_reason)))
            fields.extend((key+'.'+k,b(getattr(event,k))) for k in ('change_unknown','incomplete_turn'))
            if payload:
                fields.append((key+'.site','string:'+event.site));fields.extend((key+f'.path.{j}',segment(v)) for j,v in enumerate(event.path))
                if event.display_schema is not None: fields.extend(((key+'.display_schema','string:'+event.display_schema.name),(key+'.display_schema_version',n(event.display_schema.version))))
                fields.extend(node_fields(key+'.payload',event.payload) if event.payload is not None else [(key+'.payload','none')])
            else: fields.extend(((key+'.metadata_omitted','bool:true'),(key+'.payload','omitted:by_request')))
        count=min(limit,h.queued(sink)[0]);fields.append(('probes.events.returned',n(count)));reserve()
        for _ in range(count): h.pop(sink)
        return tuple(fields)
