"""Explicit network scheduling. One action, one child; effects remain data."""
from dataclasses import dataclass, replace
from .core import Check, Metadata, ModelError, Transition, _copy, _natural, _dispatch_checks

@dataclass(frozen=True)
class Address:
    machine: int
    value: object
@dataclass(frozen=True)
class Route:
    destination: int
    channel: int
    input: object
@dataclass(frozen=True)
class Message:
    source: int
    route: Route
    @property
    def channel(self): return (self.source,self.route.destination,self.route.channel)
@dataclass(frozen=True)
class NetworkInput:
    kind: str
    value: object
@dataclass(frozen=True)
class NetworkState:
    machines: tuple
    pending: tuple = ()
    def machine(self,id): return dict(self.machines)[id]
@dataclass(frozen=True)
class QueuePolicy:
    delivery: str = 'FifoPerChannel'
    max_pending: int = 32
    max_per_channel: int = 32
    max_routes_per_output: int = 8
    allow_drop: bool = False
    allow_duplicate: bool = False
    def __post_init__(self):
        if type(self.allow_drop) is not bool or type(self.allow_duplicate) is not bool: raise ModelError('queue action flags must be boolean')
        if self.delivery not in ('Unordered','FifoPerChannel'): raise ModelError('invalid delivery order')
        for name in ('max_pending','max_per_channel','max_routes_per_output'): _natural(getattr(self,name),name)
        _natural(self.max_pending,'max_pending',maximum=0xffffffff)

def _identity(m): return f'{len(m.name.encode())}:{m.name}/{m.model_version}/{m.properties_version}/{m.codec_version}'
def _namespaced(model,phase,args,prefix,sink):
    checks=[]
    try: _dispatch_checks(model,'check_'+phase+'_into','check_'+phase,args,checks,'composed '+phase,optional=phase=='transition')
    finally:
        for c in checks:
            c=replace(c,id=prefix+'::'+c.id)
            if any(p.id==c.id for p in sink.snapshot()): raise ModelError('duplicate composed property id: '+c.id)
            sink.push(c)

class Network:
    def __init__(self,machines,wiring,policy=QueuePolicy()):
        entries=list(machines.items() if isinstance(machines,dict) else machines)
        for id,_ in entries: _natural(id,'machine id',maximum=0xffffffff)
        self.machines=dict(sorted(entries)); self.wiring=wiring; self.policy=policy
        if not entries or len(entries)!=len(self.machines): raise ModelError('empty or duplicate network machine id')
    def metadata(self):
        names=''; builds=''
        for id,m in self.machines.items():
            meta=m.metadata(); identity=_identity(meta); names+=f'{id}={len(identity.encode())}:{identity};'; builds+=f'{len(meta.build.encode())}:{meta.build}'
        w=self.wiring.metadata(); identity=_identity(w); names+=f'w={len(identity.encode())}:{identity};'; builds+=f'{len(w.build.encode())}:{w.build}'
        p=self.policy
        return Metadata(f'network-v1:{names}policy={p.delivery},{p.max_pending},{p.max_per_channel},{p.max_routes_per_output},{str(p.allow_drop).lower()},{str(p.allow_duplicate).lower()}',build=builds+';native')
    def initial_state(self): return NetworkState(tuple((id,_copy(m.initial_state())) for id,m in self.machines.items()))
    def validate(self,s):
        if [id for id,_ in s.machines]!=list(self.machines): raise ModelError('network state machine IDs differ from registration')
        if len(s.pending)>self.policy.max_pending: raise ModelError('network pending bound exceeded; verification incomplete')
        counts={}
        for msg in s.pending:
            if msg.source not in self.machines or msg.route.destination not in self.machines: raise ModelError('unknown network route machine')
            _natural(msg.route.channel,'channel',maximum=0xffffffff)
            counts[msg.channel]=counts.get(msg.channel,0)+1
            if counts[msg.channel]>self.policy.max_per_channel: raise ModelError('network channel bound exceeded; verification incomplete')
    def deliverable(self,s,n): return self.policy.delivery=='Unordered' or all(m.channel!=s.pending[n].channel for m in s.pending[:n])
    def addressed(self,s,i):
        if i.kind=='local':
            if not isinstance(i.value,Address) or i.value.machine not in self.machines: raise ModelError('unknown network local machine')
            return i.value
        if i.kind not in ('deliver','drop','duplicate'): raise ModelError('invalid network action tag')
        _natural(i.value,'pending index',maximum=0xffffffff)
        if i.value>=len(s.pending): raise ModelError('network pending index out of range')
        if i.kind=='deliver':
            if not self.deliverable(s,i.value): raise ModelError('network FIFO channel order violation')
            m=s.pending[i.value]; return Address(m.route.destination,m.route.input)
        if not getattr(self.policy,'allow_'+i.kind): raise ModelError('network '+i.kind+' disabled')
        return None
    def _enqueue(self,q,m):
        if m.route.destination not in self.machines: raise ModelError('unknown network route machine')
        _natural(m.route.channel,'channel',maximum=0xffffffff)
        if len(q)>=self.policy.max_pending or sum(x.channel==m.channel for x in q)>=self.policy.max_per_channel: raise ModelError('network queue bound exceeded; verification incomplete')
        q.append(_copy(m))
    def step(self,s,i):
        self.validate(s); a=self.addressed(s,i); q=list(_copy(s.pending)); states=dict(_copy(s.machines))
        if i.kind in ('deliver','drop'): q.pop(i.value)
        if i.kind=='duplicate': self._enqueue(q,s.pending[i.value])
        if a is None: return Transition(NetworkState(tuple(states.items()),tuple(q)))
        t=self.machines[a.machine].step(_copy(states[a.machine]),_copy(a.value)); states[a.machine]=_copy(t.state)
        outputs=tuple(Address(a.machine,_copy(o)) for o in t.outputs)
        for o in outputs:
            count=0
            for route in self.wiring.routes(_copy(o)):
                count+=1
                if count>self.policy.max_routes_per_output: raise ModelError('network route fanout bound exceeded; verification incomplete')
                self._enqueue(q,Message(a.machine,route))
        return Transition(NetworkState(tuple(states.items()),tuple(q)),outputs,t.disposition)
    def inputs(self,s):
        self.validate(s)
        for id,state in s.machines:
            for i in self.machines[id].inputs(_copy(state)): yield NetworkInput('local',Address(id,_copy(i)))
        for n,m in enumerate(s.pending):
            if self.deliverable(s,n): yield NetworkInput('deliver',n)
            if self.policy.allow_drop: yield NetworkInput('drop',n)
            if self.policy.allow_duplicate and len(s.pending)<self.policy.max_pending and sum(x.channel==m.channel for x in s.pending)<self.policy.max_per_channel: yield NetworkInput('duplicate',n)
    def check_state_into(self,s,sink):
        self.validate(s)
        for id,state in s.machines: _namespaced(self.machines[id],'state',(_copy(state),),f'machine/{id}',sink)
        _namespaced(self.wiring,'state',(_copy(s),),'global',sink)
    def check_transition_into(self,s,i,t,sink):
        self.validate(s); self.validate(t.state); a=self.addressed(s,i)
        if a is not None:
            if any(o.machine!=a.machine for o in t.outputs): raise ModelError('wrong network child output address')
            child=Transition(_copy(t.state.machine(a.machine)),tuple(_copy(o.value) for o in t.outputs),t.disposition)
            _namespaced(self.machines[a.machine],'transition',(_copy(s.machine(a.machine)),_copy(a.value),child),f'machine/{a.machine}',sink)
        elif t.outputs: raise ModelError('network queue action emitted child output')
        _namespaced(self.wiring,'transition',(_copy(s),_copy(i),_copy(t)),'global',sink)
    def equal_states(self,a,b):
        return a==b
