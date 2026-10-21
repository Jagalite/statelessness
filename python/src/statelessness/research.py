"""Opt-in finite equivalence experiments; never enables production pruning.

Snapshots and equality callbacks are cooperative, synchronous and deterministic.
They must faithfully preserve values; this module is not a Python sandbox.
"""
from dataclasses import dataclass, replace
from enum import Enum
from collections import deque
import inspect
from .core import (Check, CheckSink, Disposition, Metadata, ModelError, Transition, _copy,
                   _natural, _saturating_add_u64)


def _text(value, name):
    if type(value) is not str: raise ValueError(name+' must be a string')
    return value

# Rust char::is_whitespace / Unicode White_Space, rather than Python's broader
# str.strip control-character policy or BOM-stripping conventions.
_WHITE_SPACE=frozenset((*range(9,14),32,0x85,0xA0,0x1680,*range(0x2000,0x200B),
                        0x2028,0x2029,0x202F,0x205F,0x3000))
def _blank(value):
    return all(ord(character) in _WHITE_SPACE for character in str.__iter__(value))

@dataclass(frozen=True, order=True)
class RequiredProperty:
    id: str
    phase: str
    def __post_init__(self):
        _text(self.id,'property ID')
        if type(self.phase) is not str or self.phase not in ('state','transition'):
            raise ValueError('property phase must be state or transition')

@dataclass(frozen=True)
class Assumptions:
    established: bool
    finite_domain: str
    equivalence: str
    effects: str
    transition_properties: str
    queue_order: str
    oracle_history: str
    visible_timing: str
    def __post_init__(self):
        if type(self.established) is not bool: raise ValueError('established must be bool')
        for name in _ASSUMPTIONS: _text(getattr(self,name),name)

_ASSUMPTIONS=('finite_domain','equivalence','effects','transition_properties',
              'queue_order','oracle_history','visible_timing')

@dataclass(frozen=True)
class Proposal:
    name: str
    version: int
    build: str
    assumptions: Assumptions
    required_properties: tuple[RequiredProperty,...] = ()
    def __post_init__(self):
        _text(self.name,'proposal name'); _text(self.build,'proposal build')
        _natural(self.version,'proposal version',maximum=(1<<32)-1)
        if type(self.assumptions) is not Assumptions: raise ValueError('assumptions must be Assumptions')
        if type(self.required_properties) is not tuple and type(self.required_properties) is not list:
            raise ValueError('required_properties must be an ordered sequence')
        if any(type(p) is not RequiredProperty for p in self.required_properties):
            raise ValueError('required_properties must contain RequiredProperty')
        object.__setattr__(self,'required_properties',tuple(self.required_properties))

@dataclass(frozen=True)
class ExperimentConfig:
    enabled: bool = False
    max_states: int = 256
    max_edges: int = 4096
    max_equivalence_pairs: int = 65_536
    def __post_init__(self):
        if type(self.enabled) is not bool: raise ValueError('enabled must be bool')
        for name in ('max_states','max_edges','max_equivalence_pairs'): _natural(getattr(self,name),name)

class ExperimentStatus(str,Enum):
    DISABLED='Disabled'
    ASSUMPTIONS_NOT_ESTABLISHED='AssumptionsNotEstablished'
    INCOMPLETE='Incomplete'
    COUNTEREXAMPLE='Counterexample'
    AGREES_WITHIN_DECLARED_FIXTURE='AgreesWithinDeclaredFixture'

@dataclass(frozen=True)
class ResearchEquality:
    """Optional exact equality hooks; each receives detached snapshots."""
    state: object = None
    input: object = None
    output: object = None
    projection: object = None
    def __post_init__(self):
        for name in ('state','input','output','projection'):
            value=getattr(self,name)
            if value is not None and not callable(value): raise ValueError(name+' equality must be callable')

@dataclass(frozen=True)
class ViolationWitness:
    property: RequiredProperty
    details: str
    inputs: tuple

@dataclass(frozen=True)
class Reachability:
    states: tuple[int,...]
    edges: int
    violations: tuple[ViolationWitness,...]
    observed_properties: tuple[RequiredProperty,...]
    skipped_checks: int

@dataclass(frozen=True)
class EquivalenceCounterexample:
    left_state: int
    right_state: int
    left_prefix: tuple
    right_prefix: tuple
    reason: str

@dataclass(frozen=True)
class ExperimentReport:
    model: Metadata
    proposal: Proposal
    config: ExperimentConfig
    status: ExperimentStatus
    declared_states: int
    materialized_edges: int = 0
    compared_pairs: int = 0
    exact: Reachability | None = None
    reduced: Reachability | None = None
    counterexamples: tuple[EquivalenceCounterexample,...] = ()
    missing_violations: tuple[ViolationWitness,...] = ()
    missing_properties: tuple[RequiredProperty,...] = ()
    limitation: str = ('No production reduction is enabled. Evidence applies only to '
                       'the explicit finite fixture and its assumptions.')

@dataclass(frozen=True)
class _Edge:
    input: object
    target: int
    outputs: tuple
    disposition: tuple[str,str]
    checks: tuple[Check,...]

@dataclass(frozen=True)
class _Graph:
    initial: int
    checks: tuple
    edges: tuple


def _invoke(stage,callback,*args):
    # Install a contextual fallback without trusting exception stringification.
    try:
        result=callback(*args)
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result): result.close()
            raise ModelError('research callbacks must be synchronous')
        return result
    except ModelError: raise
    except Exception as error:
        try: message=str(error)
        except BaseException: message='callback failed (message unavailable)'
        raise ModelError(stage+': '+message) from error

def _equal(hooks,kind,left,right):
    a,b=_copy(left),_copy(right)
    callback=getattr(hooks,kind)
    value=_invoke(kind+' equality',callback,a,b) if callback is not None else _invoke(kind+' equality',lambda: a==b)
    if type(value) is not bool: raise ModelError(kind+' equality must return bool')
    return value

def _position(states,candidate,hooks):
    for index,state in enumerate(states):
        if _equal(hooks,'state',state,candidate): return index
    return None

def _disposition(value):
    if type(value) is not Disposition: raise ModelError('transition needs a Disposition')
    if not issubclass(type(value.kind),str) or not issubclass(type(value.reason),str):
        raise ModelError('invalid transition disposition')
    kind=next((s for s in ('accepted','rejected','ignored') if str.__eq__(value.kind,s)),None)
    reason=str.__str__(value.reason)
    if kind is None or (kind=='accepted' and str.__len__(reason)):
        raise ModelError('invalid transition disposition')
    return kind,reason


def _research_checks(model,phase,args):
    """Raw preferred/legacy dispatch: preserve typed ModelError text exactly."""
    values=[]; sink=CheckSink(values)
    try:
        def run():
            try:
                callback=getattr(model,'check_'+phase+'_into',None)
                if callback is not None:
                    result=callback(*(tuple(_copy(value) for value in args)+(sink,)))
                    if result is not None:
                        failure=sink._poison(ModelError('append checker must return None synchronously'))
                        if inspect.iscoroutine(result) or inspect.isgenerator(result): result.close()
                        raise failure
                else:
                    legacy=getattr(model,'check_'+phase,None)
                    if legacy is None:
                        if phase=='transition': return
                        raise ModelError('model needs check_state or check_state_into')
                    sink.extend(legacy(*(tuple(_copy(value) for value in args))))
            except BaseException:
                if sink._failure() is not None: raise sink._failure()
                raise
            if sink._failure() is not None: raise sink._failure()
        _invoke(phase+' check',run)
        return tuple(values)
    finally: sink._revoke()


def _materialize(model,states,maximum,count,hooks):
    initial=_copy(_invoke('initial state',model.initial_state))
    initial_index=_position(states,initial,hooks)
    if initial_index is None: raise ModelError('initial state is outside the declared finite domain')
    checks=[]; edges=[]; sentinel=object()
    for state in states:
        checks.append(_research_checks(model,'state',(state,)))
        callback=getattr(model,'input_iter',None)
        if callback is None: callback=model.inputs
        domain=_invoke('inputs',callback,_copy(state))
        iterator=_invoke('input iterator',iter,domain)
        outgoing=[]
        while True:
            input=_invoke('input next',next,iterator,sentinel)
            if input is sentinel: break
            if count[0]==maximum:
                raise ModelError('edge materialization budget reached; finite graph not completed')
            owned_input=_copy(input)
            raw=_invoke('transition',model.step,_copy(state),_copy(owned_input))
            count[0]+=1
            if not isinstance(raw,Transition): raise ModelError('transition: model must return Transition')
            transition=_copy(raw)
            target=_position(states,transition.state,hooks)
            if target is None: raise ModelError('successor is outside the declared finite domain')
            observations=_research_checks(model,'transition',(state,owned_input,transition))
            outgoing.append(_Edge(owned_input,target,_copy(transition.outputs),
                                  _disposition(transition.disposition),observations))
        edges.append(tuple(outgoing))
    return _Graph(initial_index,tuple(checks),tuple(edges))


def _reach(graph,equivalent):
    states=[graph.initial]; edges=0; violations=[]; observed=set(); seen=set(); skipped=0
    paths=[None]*len(graph.edges); paths[graph.initial]=()
    queue=deque((graph.initial,))
    def observe(checks,phase,path):
        nonlocal skipped
        for check in checks:
            property=RequiredProperty(check.id,phase); observed.add(property)
            if check.status=='failed' and property not in seen:
                seen.add(property)
                violations.append(ViolationWitness(property,check.details,_copy(path)))
            elif check.status=='skipped': skipped=_saturating_add_u64(skipped,1)
    observe(graph.checks[graph.initial],'state',())
    while queue:
        source=queue.popleft()
        for edge in graph.edges[source]:
            edges+=1; path=paths[source]+(_copy(edge.input),)
            observe(graph.checks[edge.target],'state',path)
            observe(edge.checks,'transition',path)
            # A malformed/non-reflexive cooperative relation must never enqueue
            # an exact node repeatedly. Observations above still count each edge.
            if paths[edge.target] is not None: continue
            if any(equivalent(old,edge.target) for old in states): continue
            paths[edge.target]=path; states.append(edge.target); queue.append(edge.target)
    return Reachability(tuple(states),edges,tuple(violations),tuple(sorted(observed)),skipped),paths


def _compare(graph,keys,left,right,hooks):
    if graph.checks[left]!=graph.checks[right]: return 'state checks differ'
    a,b=graph.edges[left],graph.edges[right]
    if len(a)!=len(b): return 'enabled input counts differ'
    for first,second in zip(a,b):
        if not _equal(hooks,'input',first.input,second.input): return 'enabled input labels or order differ'
        if len(first.outputs)!=len(second.outputs) or any(not _equal(hooks,'output',x,y) for x,y in zip(first.outputs,second.outputs)):
            return 'ordered effects differ'
        if first.disposition!=second.disposition: return 'transition disposition differs'
        if first.checks!=second.checks: return 'transition checks differ'
        if not _equal(hooks,'projection',keys[first.target],keys[second.target]): return 'successor equivalence classes differ'
    return None


def compare_equivalence(model,states,proposal,config,projection,*,equality=ResearchEquality()):
    """Compare an explicitly closed fixture once; no codecs or pruning required."""
    if type(proposal) is not Proposal or type(config) is not ExperimentConfig:
        raise ValueError('proposal/config must be Proposal/ExperimentConfig')
    if type(equality) is not ResearchEquality: raise ValueError('equality must be ResearchEquality')
    if type(states) is not tuple and type(states) is not list: raise ValueError('states must be an ordered list or tuple')
    if not callable(projection): raise ValueError('projection must be callable')
    if any(_blank(getattr(proposal.assumptions,name)) for name in _ASSUMPTIONS):
        raise ModelError('every reduction assumption needs an explicit statement')
    if _blank(proposal.name) or _blank(proposal.build) or not proposal.version:
        raise ModelError('reduction proposal needs a name, positive version, and build identity')
    if any(_blank(p.id) for p in proposal.required_properties) or len(set(proposal.required_properties))!=len(proposal.required_properties):
        raise ModelError('empty or duplicate expected research property')
    raw_metadata=_invoke('metadata',model.metadata)
    if type(raw_metadata) is not Metadata: raise ModelError('metadata must return Metadata')
    # Metadata is a closed primitive record, not an application clone hook.
    if not issubclass(type(raw_metadata.name),str) or not issubclass(type(raw_metadata.build),str):
        raise ModelError('metadata strings must be strings')
    metadata=Metadata(str.__str__(raw_metadata.name),raw_metadata.model_version,
                      raw_metadata.properties_version,raw_metadata.codec_version,
                      str.__str__(raw_metadata.build))
    report=ExperimentReport(metadata,proposal,config,ExperimentStatus.DISABLED,len(states))
    if not config.enabled: return report
    if not proposal.assumptions.established: return replace(report,status=ExperimentStatus.ASSUMPTIONS_NOT_ESTABLISHED)
    report=replace(report,status=ExperimentStatus.INCOMPLETE)
    if not states or len(states)>config.max_states:
        return replace(report,limitation='The explicit state domain is empty or exceeds the state budget.')
    try:
        owned=tuple(_copy(state) for state in states)
        duplicate=any(_position(owned[:index],state,equality) is not None for index,state in enumerate(owned))
    except Exception as error: return replace(report,limitation=_error_text(error))
    if duplicate: raise ModelError('declared research domain contains duplicate exact states')
    count=[0]
    try:
        graph=_materialize(model,owned,config.max_edges,count,equality)
    except Exception as error:
        return replace(report,materialized_edges=count[0],limitation=_error_text(error))
    report=replace(report,materialized_edges=count[0])
    try:
        keys=tuple(_copy(_invoke('projection',projection,_copy(state))) for state in owned)
        if any(not _equal(equality,'projection',key,key) for key in keys):
            raise ModelError('projection equality must be reflexive')
        exact,paths=_reach(graph,lambda a,b:a==b)
        equivalent=lambda a,b:_equal(equality,'projection',keys[a],keys[b])
        reduced,_=_reach(graph,equivalent)
        reduced_failures={v.property for v in reduced.violations}
        missing=tuple(v for v in exact.violations if v.property not in reduced_failures)
        absent=tuple(p for p in proposal.required_properties if p not in exact.observed_properties)
        pairs=0; limited=False; counterexamples=[]
        for position,left in enumerate(exact.states):
            for right in exact.states[position+1:]:
                if not equivalent(left,right): continue
                if pairs==config.max_equivalence_pairs: limited=True; break
                pairs+=1
                reason=_compare(graph,keys,left,right,equality)
                if reason is not None:
                    counterexamples.append(EquivalenceCounterexample(left,right,_copy(paths[left]),_copy(paths[right]),reason))
            if limited: break
        agrees=all(any(equivalent(state,other) for other in reduced.states) for state in exact.states)
        extra=any(v.property not in {w.property for w in exact.violations} for v in reduced.violations)
        report=replace(report,compared_pairs=pairs,exact=exact,reduced=reduced,
                       counterexamples=tuple(counterexamples),missing_violations=_copy(missing),missing_properties=absent)
        if counterexamples or missing or not agrees or extra:
            return replace(report,status=ExperimentStatus.COUNTEREXAMPLE,limitation='The proposed equivalence changes observable behavior or property reachability in this finite fixture; it must not be used for pruning.')
        if limited: return replace(report,limitation='Equivalence-pair budget reached; unchecked pairs cannot be assumed equivalent.')
        if absent or exact.skipped_checks or reduced.skipped_checks or not proposal.required_properties:
            return replace(report,limitation='Expected properties were absent or checks were skipped; equivalence qualification is incomplete.')
        return replace(report,status=ExperimentStatus.AGREES_WITHIN_DECLARED_FIXTURE)
    except Exception as error: return replace(report,limitation=_error_text(error))


def _error_text(error):
    try: return str(error)
    except BaseException: return 'research callback failed (message unavailable)'


def qualification_parameters(report):
    """Sorted provenance parameters; never an instruction to enable pruning."""
    values={'research.proposal':report.proposal.name,
            'research.proposal_version':str(report.proposal.version),
            'research.proposal_build':report.proposal.build,
            'research.model_build':report.model.build,
            'research.status':report.status.value,
            'research.declared_states':str(report.declared_states),
            'research.materialized_edges':str(report.materialized_edges),
            'research.compared_pairs':str(report.compared_pairs),
            'research.limitation':report.limitation,
            'research.production_pruning_enabled':'false'}
    values.update(('research.'+name,getattr(report.proposal.assumptions,name)) for name in _ASSUMPTIONS)
    return dict(sorted(values.items()))


__all__ = ["Assumptions", "Proposal", "ExperimentConfig", "ExperimentStatus",
           "RequiredProperty", "ResearchEquality", "ViolationWitness", "Reachability",
           "EquivalenceCounterexample", "ExperimentReport", "compare_equivalence",
           "qualification_parameters"]
