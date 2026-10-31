"""Heterogeneous two-child composition with explicit message delivery."""
from dataclasses import dataclass
from types import SimpleNamespace
from .core import Metadata, ModelError, Transition, _Captured, _call, _copy, _natural
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
        models=(self.left,self.right,self.wiring); maximum=_natural(self.max_pending,'max_pending')
        ms=[m.metadata() for m in models]
        identities=[f'{len(m.name.encode())}:{m.name}:{m.model_version}/{m.properties_version}/{m.codec_version}' for m in ms]
        names=''.join(f'{len(s.encode())}:{s}' for s in identities)
        builds=''.join(f'{len(m.build.encode())}:{m.build}' for m in ms)
        return Metadata(f'pair-v1:{names};pending={maximum}',build=builds+';native')
    def initial_state(self):
        left,right=self.left,self.right
        return PairState(_copy(left.initial_state()),_copy(right.initial_state()))
    @staticmethod
    def _address(value):
        # Address is a public Python value; frozen dataclasses can still be
        # forged or mutated through object.__setattr__. Rust's enum cannot
        # represent these tags, so reject them before dispatching callbacks.
        if not isinstance(value,Address) or type(value.side) is not str or value.side not in ('left','right'):
            raise ModelError('invalid address tag')
        return value
    def _pending(self,state):
        for address in state.pending: self._address(address)
    def addressed(self,s,i):
        if not isinstance(i,PairInput): raise ModelError('invalid composition input tag')
        kind=i.kind
        if type(kind) is not str: raise ModelError('invalid composition input tag')
        if kind=='local': return self._address(i.value)
        if kind!='deliver': raise ModelError('invalid composition input tag')
        _natural(i.value,'pending index',maximum=0xffffffff)
        if i.value>=len(s.pending): raise ModelError('pending message index out of range')
        return self._address(s.pending[i.value])
    def step(self,s,i):
        maximum=_natural(self.max_pending,'max_pending'); left,right,wiring=self.left,self.right,self.wiring
        if len(s.pending)>maximum: raise ModelError('pending message bound exceeded; verification incomplete')
        self._pending(s); self.addressed(s,i)
        # Freeze structural addresses before invoking application copy hooks,
        # then copy payloads before child state hooks. One memo preserves
        # sharing between payloads and child states.
        pending_structure=tuple(Address(a.side,a.value) for a in s.pending)
        left_state,right_state=s.left,s.right
        i=_copy(i); pending,left_state,right_state=_copy((pending_structure,left_state,right_state))
        s=PairState(left_state,right_state,pending)
        if len(s.pending)>maximum: raise ModelError('pending message bound exceeded; verification incomplete')
        self._pending(s)
        a=self.addressed(s,i);child=left if a.side=='left' else right;t=_copy(child.step(_copy(getattr(s,a.side)),_copy(a.value)))
        q=list(_copy(s.pending))
        if i.kind=='deliver':q.pop(i.value)
        outputs=tuple(Address(a.side,_copy(o)) for o in t.outputs)
        for o in outputs:
            for message in wiring.messages(_copy(o)):
                if len(q)>=maximum: raise ModelError('pending message bound exceeded; verification incomplete')
                self._address(message)
                copied=_copy(message); self._address(copied); q.append(copied)
        return Transition(PairState(_copy(t.state if a.side=='left' else s.left),_copy(t.state if a.side=='right' else s.right),tuple(q)),outputs,t.disposition)
    def inputs(self,s):
        models={'left':self.left,'right':self.right}
        self._pending(s)
        # Delivery enumeration needs only the queue length. Capture it and the
        # child references before copy hooks run; queued payloads are never
        # inspected or copied by the Rust scheduler at this boundary.
        pending_count=len(s.pending)
        state_references=(s.left,s.right)
        enumerate_child={side:model.inputs for side,model in models.items()}
        left,right=_copy(state_references)
        states={'left':left,'right':right}
        def enumerate_inputs():
            for side in ('left','right'):
                for i in enumerate_child[side](_copy(states[side])): yield PairInput('local',Address(side,_copy(i)))
            for n in range(pending_count): yield PairInput('deliver',n)
        return enumerate_inputs()
    def check_state_into(self,s,sink):
        models={'left':self.left,'right':self.right}; wiring=self.wiring; self._pending(s); s=_copy(s); self._pending(s)
        for side in ('left','right'): _namespaced(models[side],'state',(_copy(getattr(s,side)),),side,sink)
        _namespaced(wiring,'state',(_copy(s),),'global',sink)
    def check_transition_into(self,s,i,t,sink):
        models={'left':self.left,'right':self.right}; wiring=self.wiring
        self._pending(s); self._pending(t.state)
        target=self.addressed(s,i).side
        def resolve(model):
            try:
                append=_call('composed transition',getattr,model,'check_transition_into',None)
                legacy=None if append is not None else _call('composed transition',getattr,model,'check_transition',None)
                return SimpleNamespace(check_transition_into=append,check_transition=legacy),None
            except Exception as error:
                return None,error
        # Freeze address slots before application copy hooks, but validate and
        # copy each output in order, as the Rust mapping does. Capture the
        # remaining observation early and defer its failure until mapping ends.
        slots=[]
        for output in t.outputs:
            if not isinstance(output,Address):
                slots.append((False,None,None,None)); continue
            try:
                side=output.side
                value=output.value if type(side) is str and side==target else None
                slots.append((True,side,value,None))
            except Exception as error:
                slots.append((True,None,None,error))
        child_hooks,child_error=resolve(models[target])
        wiring_hooks,wiring_error=resolve(wiring)
        captured=_Captured((s,i,t.state,t.disposition))
        outputs=[]
        for is_address,side,value,slot_error in slots:
            if slot_error is not None: raise slot_error
            if not is_address or type(side) is not str or side not in ('left','right'):
                raise ModelError('invalid address tag')
            if side!=target: raise ModelError('wrong child output address')
            outputs.append(Address(side,_copy(value)))
        s,i,post,disposition=captured.get()
        t=Transition(post,tuple(outputs),disposition)
        self._pending(s); self._pending(t.state)
        a=self.addressed(s,i)
        if child_error is not None: raise child_error
        if child_hooks.check_transition_into is not None or child_hooks.check_transition is not None:
            ct=Transition(_copy(getattr(t.state,a.side)),tuple(_copy(o.value) for o in t.outputs),_copy(t.disposition))
            _namespaced(child_hooks,'transition',(_copy(getattr(s,a.side)),_copy(a.value),ct),a.side,sink)
        if wiring_error is not None: raise wiring_error
        if wiring_hooks.check_transition_into is not None or wiring_hooks.check_transition is not None:
            _namespaced(wiring_hooks,'transition',(_copy(s),_copy(i),_copy(t)),'global',sink)
    def equal_states(self,a,b):
        if a is b: return True
        if type(a) is not PairState or type(b) is not PairState:
            return a==b
        # Capture pending values before child copying or comparison can mutate
        # externally aliased messages. Preserve the dataclass comparison order;
        # an unused pending-copy failure must not mask unequal children.
        # Freeze structural references before any application copy hook runs.
        # PairState.pending is canonically a tuple of addresses.
        ap=tuple(Address(item.side,item.value) for item in a.pending)
        bp=tuple(Address(item.side,item.value) for item in b.pending)
        left_a,right_a,left_b,right_b=a.left,a.right,b.left,b.right
        pending_lengths=(len(ap),len(bp))
        memo={}
        pending=_Captured((ap,bp),memo)
        if pending.error is not None: memo={}  # discard a partial memo
        al,bl=_copy((left_a,left_b),memo)
        rights=_Captured((right_a,right_b),memo)
        if al is not bl and not (al == bl): return False
        ar,br=rights.get()
        if ar is not br and not (ar == br): return False
        if pending_lengths[0] != pending_lengths[1]: return False
        aq,bq=pending.get()
        return aq==bq
