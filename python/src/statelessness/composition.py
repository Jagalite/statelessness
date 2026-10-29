"""Heterogeneous two-child composition with explicit message delivery."""
from dataclasses import dataclass
from .core import Metadata, ModelError, Transition, _copy, _natural
from .network import _namespaced

@dataclass(frozen=True)
class Address:
    side: str
    value: object
    def __post_init__(self):
        if self.side not in ('left','right'): raise ModelError('invalid address tag')
@dataclass(frozen=True)
class PairInput:
    kind: str
    value: object
@dataclass(frozen=True)
class PairState:
    left: object
    right: object
    pending: tuple = ()

class Pair:
    def __init__(self,left,right,wiring,max_pending=32):
        _natural(max_pending,'max_pending');self.left,self.right,self.wiring,self.max_pending=left,right,wiring,max_pending
    def metadata(self):
        ms=[m.metadata() for m in (self.left,self.right,self.wiring)]
        identities=[f'{len(m.name.encode())}:{m.name}:{m.model_version}/{m.properties_version}/{m.codec_version}' for m in ms]
        names=''.join(f'{len(s.encode())}:{s}' for s in identities)
        builds=''.join(f'{len(m.build.encode())}:{m.build}' for m in ms)
        return Metadata(f'pair-v1:{names};pending={self.max_pending}',build=builds+';native')
    def initial_state(self): return PairState(_copy(self.left.initial_state()),_copy(self.right.initial_state()))
    def addressed(self,s,i):
        if i.kind=='local' and isinstance(i.value,Address): return i.value
        if i.kind!='deliver': raise ModelError('invalid composition input tag')
        _natural(i.value,'pending index',maximum=0xffffffff)
        if i.value>=len(s.pending): raise ModelError('pending message index out of range')
        return s.pending[i.value]
    def step(self,s,i):
        if len(s.pending)>self.max_pending: raise ModelError('pending message bound exceeded; verification incomplete')
        a=self.addressed(s,i);child=getattr(self,a.side);t=child.step(_copy(getattr(s,a.side)),_copy(a.value))
        q=list(_copy(s.pending))
        if i.kind=='deliver':q.pop(i.value)
        outputs=tuple(Address(a.side,_copy(o)) for o in t.outputs)
        for o in outputs:
            for message in self.wiring.messages(_copy(o)):
                if len(q)>=self.max_pending: raise ModelError('pending message bound exceeded; verification incomplete')
                if not isinstance(message,Address): raise ModelError('invalid routed address')
                q.append(_copy(message))
        return Transition(PairState(_copy(t.state if a.side=='left' else s.left),_copy(t.state if a.side=='right' else s.right),tuple(q)),outputs,t.disposition)
    def inputs(self,s):
        for side in ('left','right'):
            for i in getattr(self,side).inputs(_copy(getattr(s,side))): yield PairInput('local',Address(side,_copy(i)))
        for n in range(len(s.pending)):yield PairInput('deliver',n)
    def check_state_into(self,s,sink):
        for side in ('left','right'): _namespaced(getattr(self,side),'state',(_copy(getattr(s,side)),),side,sink)
        _namespaced(self.wiring,'state',(_copy(s),),'global',sink)
    def check_transition_into(self,s,i,t,sink):
        a=self.addressed(s,i)
        if any(o.side!=a.side for o in t.outputs): raise ModelError('wrong child output address')
        ct=Transition(_copy(getattr(t.state,a.side)),tuple(_copy(o.value) for o in t.outputs),t.disposition)
        _namespaced(getattr(self,a.side),'transition',(_copy(getattr(s,a.side)),_copy(a.value),ct),a.side,sink)
        _namespaced(self.wiring,'transition',(_copy(s),_copy(i),_copy(t)),'global',sink)
    def equal_states(self,a,b):return a==b
