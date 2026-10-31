"""Explicit network scheduling. One action, one child; effects remain data."""
from dataclasses import dataclass, replace
from types import SimpleNamespace
from .core import Check, Metadata, ModelError, Transition, _Captured, _call, _copy, _natural, _dispatch_checks

@dataclass(frozen=True)
class Address:
    machine: int
    value: object
    def __post_init__(self): _natural(self.machine,'machine id',maximum=0xffffffff)
@dataclass(frozen=True)
class Route:
    destination: int
    channel: int
    input: object
    def __post_init__(self):
        _natural(self.destination,'destination machine id',maximum=0xffffffff)
        _natural(self.channel,'channel',maximum=0xffffffff)
@dataclass(frozen=True)
class Message:
    source: int
    route: Route
    def __post_init__(self):
        _natural(self.source,'source machine id',maximum=0xffffffff)
        if not isinstance(self.route,Route): raise ModelError('invalid network route')
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
    def machine(self,id):
        """Return the registered state, or None when the ID is absent.

        As in the Rust state accessor, the machine entries must be in canonical
        increasing ID order. The returned state is not copied; None can also be
        an application state, so inspect ``machines`` to distinguish that case.
        """
        _natural(id,'machine id',maximum=0xffffffff)
        lo,hi=0,len(self.machines)
        while lo<hi:
            mid=(lo+hi)//2
            if self.machines[mid][0]<id: lo=mid+1
            else: hi=mid
        return self.machines[lo][1] if lo<len(self.machines) and self.machines[lo][0]==id else None
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
    def machine_ids(self):
        """Return an ordered snapshot of the registered machine IDs."""
        return tuple(self._machine_snapshot())
    def _machine_snapshot(self):
        # The registration mapping remains public for compatibility. Reestablish
        # canonical ID ordering at each operation boundary, before callbacks.
        entries=tuple(self.machines.items())
        if not entries: raise ModelError('empty or duplicate network machine id')
        for id,_ in entries: _natural(id,'machine id',maximum=0xffffffff)
        return dict(sorted(entries))
    def metadata(self):
        machines=tuple(self._machine_snapshot().items()); wiring=self.wiring
        p=self.policy
        policy=QueuePolicy(p.delivery,p.max_pending,p.max_per_channel,p.max_routes_per_output,p.allow_drop,p.allow_duplicate)
        names=''; builds=''
        for id,m in machines:
            meta=m.metadata(); identity=_identity(meta); names+=f'{id}={len(identity.encode())}:{identity};'; builds+=f'{len(meta.build.encode())}:{meta.build}'
        w=wiring.metadata(); identity=_identity(w); names+=f'w={len(identity.encode())}:{identity};'; builds+=f'{len(w.build.encode())}:{w.build}'
        p=policy
        return Metadata(f'network-v1:{names}policy={p.delivery},{p.max_pending},{p.max_per_channel},{p.max_routes_per_output},{str(p.allow_drop).lower()},{str(p.allow_duplicate).lower()}',build=builds+';native')
    def initial_state(self):
        machines=tuple(self._machine_snapshot().items())
        return NetworkState(tuple((id,_copy(m.initial_state())) for id,m in machines))
    def validate(self,s,*,machines=None,policy=None):
        machines=self._machine_snapshot() if machines is None else machines
        policy=self.policy if policy is None else policy
        for id,_ in s.machines: _natural(id,'machine id',maximum=0xffffffff)
        if [id for id,_ in s.machines]!=list(machines): raise ModelError('network state machine IDs differ from registration')
        if len(s.pending)>policy.max_pending: raise ModelError('network pending bound exceeded; verification incomplete')
        counts={}
        for msg in s.pending:
            if msg.source not in machines or msg.route.destination not in machines: raise ModelError('unknown network route machine')
            _natural(msg.route.channel,'channel',maximum=0xffffffff)
            counts[msg.channel]=counts.get(msg.channel,0)+1
            if counts[msg.channel]>policy.max_per_channel: raise ModelError('network channel bound exceeded; verification incomplete')
    def deliverable(self,s,n,policy=None):
        policy=self.policy if policy is None else policy
        return policy.delivery=='Unordered' or all(m.channel!=s.pending[n].channel for m in s.pending[:n])
    def addressed(self,s,i,*,machines=None,policy=None):
        machines=self.machines if machines is None else machines
        policy=self.policy if policy is None else policy
        # Public dataclasses can be forged, unlike Rust's closed input enum.
        # Do not let an object's custom equality select a scheduler action.
        if not isinstance(i,NetworkInput) or type(i.kind) is not str:
            raise ModelError('invalid network action tag')
        if i.kind=='local':
            if not isinstance(i.value,Address): raise ModelError('unknown network local machine')
            if type(i.value.machine) is not int or not 0 <= i.value.machine <= 0xffffffff:
                raise ModelError('invalid network local machine id')
            if i.value.machine not in machines: raise ModelError('unknown network local machine')
            return i.value
        if i.kind not in ('deliver','drop','duplicate'): raise ModelError('invalid network action tag')
        _natural(i.value,'pending index',maximum=0xffffffff)
        if i.value>=len(s.pending): raise ModelError('network pending index out of range')
        if i.kind=='deliver':
            if not self.deliverable(s,i.value,policy): raise ModelError('network FIFO channel order violation')
            m=s.pending[i.value]; return Address(m.route.destination,m.route.input)
        if not getattr(policy,'allow_'+i.kind): raise ModelError('network '+i.kind+' disabled')
        return None
    def _enqueue(self,q,m,policy,machines):
        if m.route.destination not in machines: raise ModelError('unknown network route machine')
        _natural(m.route.channel,'channel',maximum=0xffffffff)
        if len(q)>=policy.max_pending or sum(x.channel==m.channel for x in q)>=policy.max_per_channel: raise ModelError('network queue bound exceeded; verification incomplete')
        q.append(_copy(m))
    def step(self,s,i):
        p=self.policy
        policy=QueuePolicy(p.delivery,p.max_pending,p.max_per_channel,p.max_routes_per_output,p.allow_drop,p.allow_duplicate)
        machines=self._machine_snapshot(); wiring=self.wiring
        self.validate(s,machines=machines,policy=policy)
        self.addressed(s,i,machines=machines,policy=policy)
        # Capture queue payloads before copying machine states: application
        # deepcopy hooks for a machine can mutate externally aliased messages.
        # Copy together so shared references inside the observation retain their
        # usual deepcopy identity, with the queue visited first.
        i=_copy(i); pending,states=_copy((s.pending,s.machines)); s=NetworkState(states,pending)
        self.validate(s,machines=machines,policy=policy); a=self.addressed(s,i,machines=machines,policy=policy); q=list(_copy(s.pending)); states=dict(_copy(s.machines))
        if i.kind in ('deliver','drop'): q.pop(i.value)
        if i.kind=='duplicate': self._enqueue(q,s.pending[i.value],policy,machines)
        if a is None: return Transition(NetworkState(tuple(states.items()),tuple(q)))
        t=_copy(machines[a.machine].step(_copy(states[a.machine]),_copy(a.value))); states[a.machine]=_copy(t.state)
        outputs=tuple(Address(a.machine,_copy(o)) for o in t.outputs)
        for o in outputs:
            count=0
            for route in wiring.routes(_copy(o)):
                count+=1
                if count>policy.max_routes_per_output: raise ModelError('network route fanout bound exceeded; verification incomplete')
                self._enqueue(q,Message(a.machine,route),policy,machines)
        return Transition(NetworkState(tuple(states.items()),tuple(q)),outputs,t.disposition)
    def inputs(self,s):
        p=self.policy
        policy=QueuePolicy(p.delivery,p.max_pending,p.max_per_channel,p.max_routes_per_output,p.allow_drop,p.allow_duplicate)
        machines=self._machine_snapshot()
        self.validate(s,machines=machines,policy=policy)
        # Queue actions need only channel identities, not application payloads.
        channels = tuple(m.channel for m in s.pending)
        states = _copy(s.machines)
        if [id for id,_ in states] != list(machines):
            raise ModelError('network state machine IDs differ from registration')
        def enumerate_inputs():
            for id,state in states:
                for i in machines[id].inputs(_copy(state)): yield NetworkInput('local',Address(id,_copy(i)))
            for n,channel in enumerate(channels):
                if policy.delivery=='Unordered' or channel not in channels[:n]: yield NetworkInput('deliver',n)
                if policy.allow_drop: yield NetworkInput('drop',n)
                if policy.allow_duplicate and len(channels)<policy.max_pending and channels.count(channel)<policy.max_per_channel: yield NetworkInput('duplicate',n)
        return enumerate_inputs()
    def check_state_into(self,s,sink):
        p=self.policy; policy=QueuePolicy(p.delivery,p.max_pending,p.max_per_channel,p.max_routes_per_output,p.allow_drop,p.allow_duplicate)
        machines=self._machine_snapshot(); wiring=self.wiring
        self.validate(s,machines=machines,policy=policy); s=_copy(s)
        self.validate(s,machines=machines,policy=policy)
        for id,state in s.machines: _namespaced(machines[id],'state',(_copy(state),),f'machine/{id}',sink)
        _namespaced(wiring,'state',(_copy(s),),'global',sink)
    def check_transition_into(self,s,i,t,sink):
        p=self.policy; policy=QueuePolicy(p.delivery,p.max_pending,p.max_per_channel,p.max_routes_per_output,p.allow_drop,p.allow_duplicate)
        machines=self._machine_snapshot(); wiring=self.wiring
        self.validate(s,machines=machines,policy=policy)
        self.validate(t.state,machines=machines,policy=policy)
        addressed=self.addressed(s,i,machines=machines,policy=policy)
        if addressed is None:
            if t.outputs: raise ModelError('network queue action emitted child output')
            # Queue-only actions have no child checker.  Match the default
            # no-op wiring check without copying application values when no
            # transition callback is installed.
            append=_call('composed transition',getattr,wiring,'check_transition_into',None)
            legacy=None if append is not None else _call('composed transition',getattr,wiring,'check_transition',None)
            if append is None and legacy is None:
                return
            # Keep the originally resolved bound callback: descriptors can have
            # side effects, and dispatch must not resolve the same hook twice.
            wiring=SimpleNamespace(check_transition_into=append,check_transition=legacy)
        if addressed is not None:
            # Preserve the output address slots before application copy hooks.
            # Rust maps each output in order: an earlier copy failure must not
            # be hidden by a later wrong address.  Capture the other observations
            # eagerly for isolation, but defer their failure until mapping ends.
            target=addressed.machine
            slots=tuple((o.machine,o.value) for o in t.outputs)
            captured=_Captured((s,i,t.state,t.disposition))
            outputs=[]
            for machine,value in slots:
                if machine!=target: raise ModelError('wrong network child output address')
                outputs.append(Address(machine,_copy(value)))
            s,i,post,disposition=captured.get()
            t=Transition(post,tuple(outputs),disposition)
        else:
            s,i,t=_copy((s,i,t))
        self.validate(s,machines=machines,policy=policy); self.validate(t.state,machines=machines,policy=policy); a=self.addressed(s,i,machines=machines,policy=policy)
        if a is not None:
            model=machines[a.machine]
            append=_call('composed transition',getattr,model,'check_transition_into',None)
            legacy=None if append is not None else _call('composed transition',getattr,model,'check_transition',None)
            if append is not None or legacy is not None:
                child=Transition(_copy(t.state.machine(a.machine)),tuple(_copy(o.value) for o in t.outputs),_copy(t.disposition))
                _namespaced(SimpleNamespace(check_transition_into=append,check_transition=legacy),'transition',(_copy(s.machine(a.machine)),_copy(a.value),child),f'machine/{a.machine}',sink)
        elif t.outputs: raise ModelError('network queue action emitted child output')
        append=_call('composed transition',getattr,wiring,'check_transition_into',None)
        legacy=None if append is not None else _call('composed transition',getattr,wiring,'check_transition',None)
        if append is not None or legacy is not None:
            _namespaced(SimpleNamespace(check_transition_into=append,check_transition=legacy),'transition',(_copy(s),_copy(i),_copy(t)),'global',sink)
    def equal_states(self,a,b):
        if a is b: return True
        if len(a.machines)!=len(b.machines): return False
        if a.machines and a.machines[0][0]!=b.machines[0][0]: return False
        # Preserve queued observations before machine copy/equality callbacks can
        # mutate externally aliased message payloads.  Copy the two observations
        # together to retain normal deepcopy memo semantics across their values.
        queue_lengths=(len(a.pending),len(b.pending))
        memo={}
        queues=_Captured((a.pending,b.pending),memo)
        if queues.error is not None:
            # A failed deepcopy can leave partially initialized objects in its
            # memo. Discard them before copying the machine observations.
            memo={}
        am,bm=_copy((a.machines,b.machines),memo)
        if am!=bm: return False
        if queue_lengths[0]!=queue_lengths[1]: return False
        aq,bq=queues.get()
        return aq==bq
