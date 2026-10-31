"""Optional bounded candidate handles and independently verified simulation forks."""
from dataclasses import dataclass, replace
from weakref import WeakKeyDictionary, ref
from .core import ModelError, _copy, _natural, _call
from .debugger import EvidenceViewer, VerificationLimits, InputPolicy, _branch_parent_policy, _recorded_environment
from .debugger_session import DebugSession, DebugError, SessionLimits
from .evidence import Artifact
from .trace_binary import audit_to_bytes, RunConfig
from .monitor import RecorderOptions

_tokens = WeakKeyDictionary()


class CandidateToken:
    __slots__ = ('__weakref__',)
    def __new__(cls): raise TypeError('candidate tokens come from session.inputs')
    def __init_subclass__(cls, **kwargs): raise TypeError('candidate token cannot be subclassed')
    def __setattr__(self, name, value): raise AttributeError('candidate token is read-only')
    def __deepcopy__(self, memo): return self
    def __reduce_ex__(self, protocol): raise TypeError('candidate token cannot be serialized as authority')
    @property
    def input(self):
        record = _tokens[self]
        owner = record[5]()
        return owner._read_copy(record[4]) if owner is not None else _copy(record[4])
    @property
    def revision(self): return _tokens[self][2]


@dataclass(frozen=True)
class CandidatePage:
    candidates: tuple[CandidateToken, ...]
    next_offset: int | None
    complete: bool


class EnumeratedSession(DebugSession):
    @property
    def capabilities(self): return dict(super().capabilities, enumerate_inputs=True)

    def inputs(self, expected_revision, offset=0, limit=100):
        self._guard(); self.check_revision(expected_revision)
        _natural(offset, 'candidate offset'); _natural(limit, 'candidate limit', 1)
        if offset + limit > self.limits.max_candidates: raise DebugError('candidate work budget exceeded')
        self._busy = True
        try:
            iterator = iter(_call('candidate inputs', self._model.inputs, self.state))
            sentinel, tokens = object(), []
            for _ in range(offset):
                if _call('candidate skip', next, iterator, sentinel) is sentinel: return CandidatePage((), None, True)
            for _ in range(limit):
                input = _call('candidate input', next, iterator, sentinel)
                if input is sentinel: return CandidatePage(tuple(tokens), None, True)
                token = object.__new__(CandidateToken)
                _tokens[token] = self.id, self.epoch, self.revision, self.policy.label, _copy(input), ref(self)
                tokens.append(token)
            more = _call('candidate lookahead', next, iterator, sentinel) is not sentinel
            return CandidatePage(tuple(tokens), offset + limit if more else None, not more)
        finally: self._busy = False

    def candidate_permitted(self, input):
        self._guard(); self._busy = True
        try: return InputPolicy.enumerated().permits(self._model, self._state, input, self.limits.max_candidates)
        finally: self._busy = False

    def select(self, token):
        self._guard()
        if type(token) is not CandidateToken or token not in _tokens: raise DebugError('expected authentic candidate token')
        id, epoch, revision, domain, input, _owner = _tokens[token]
        if (id, epoch, domain) != (self.id, self.epoch, self.policy.label): raise DebugError('candidate belongs to another session/domain')
        self.check_revision(revision)
        if not self.candidate_permitted(input): raise DebugError('input not permitted')
        return self.step(revision, self._read_copy(input))


@dataclass(frozen=True)
class ArtifactIdentity:
    fnv1a64: int
    bytes: int


def _bytes_identity(data):
    hash = 0xcbf29ce484222325
    for byte in data: hash = ((hash ^ byte) * 0x100000001b3) & ((1 << 64)-1)
    return ArtifactIdentity(hash, len(data))


def artifact_identity(trace): return _bytes_identity(audit_to_bytes(trace))


@dataclass(frozen=True)
class BranchProvenance:
    version: int
    parent: ArtifactIdentity
    fork_sequence: int
    checkpoint: ArtifactIdentity
    parent_metadata: object
    model_metadata: object
    parent_prefix_verified: bool
    environment: str
    origin: str

    def validate_parent(self, trace):
        if self.version != 1 or artifact_identity(trace) != self.parent or trace.metadata != self.parent_metadata:
            raise ModelError('branch sidecar does not match parent artifact')
        if self.fork_sequence < 0 or self.fork_sequence > len(trace.steps): raise ModelError('fork outside parent history')
        state = trace.initial_state if self.fork_sequence == 0 else trace.steps[self.fork_sequence-1].post_state
        if _bytes_identity(state) != self.checkpoint: raise ModelError('fork checkpoint binding differs')

    def parameters(self):
        return (('stateless.debug.parent.digest', f'fnv1a64:{self.parent.fnv1a64:016x}'),
            ('stateless.debug.parent.bytes', str(self.parent.bytes)),
            ('stateless.debug.fork.sequence', str(self.fork_sequence)),
            ('stateless.debug.fork.checkpoint', f'fnv1a64:{self.checkpoint.fnv1a64:016x}'),
            ('stateless.debug.parent.build', self.parent_metadata.build),
            ('stateless.debug.parent_prefix_verified', str(self.parent_prefix_verified).lower()),
            ('stateless.debug.branch.origin', self.origin))


@dataclass(frozen=True)
class Branch:
    session: DebugSession
    provenance: BranchProvenance


def fork_verified(viewer, id, model, sequence, policy, *, verification=VerificationLimits(),
                  limits=SessionLimits(), recorder=RecorderOptions(), allow_build_mismatch=False,
                  enumerated=False):
    """Replay the selected prefix before restoring its checkpoint.

    FNV content bindings detect accidental sidecar mismatch, not authentication.
    This creates simulation state and never restores host resources.
    """
    if type(viewer) is not EvidenceViewer: raise ValueError('expected EvidenceViewer')
    if type(policy) is not InputPolicy: raise ValueError('expected InputPolicy')
    _natural(sequence, 'fork sequence')
    artifact, boundary = viewer.artifact, viewer.boundary(sequence)
    if boundary is None: raise ModelError('fork outside parent history')
    trace = artifact.trace
    recorded = _branch_parent_policy(trace)
    if recorded is not None and not policy.unrestricted and (recorded[0] != policy.label or recorded[1]):
        raise ModelError('branch environment differs; explicitly select unrestricted injection or retain parent policy')
    if any(c.status == 'failed' for c in trace.initial_checks) or any(c.status == 'failed' for c in boundary.checks):
        raise ModelError('normal branches must start before property failure')
    prefix = replace(trace, steps=trace.steps[:sequence], termination='completed', error='')
    selected = Artifact(prefix) if artifact.version == 1 else Artifact(replace(artifact.value, trace=prefix, steps=artifact.value.steps[:sequence]))
    report = EvidenceViewer(selected, limits=viewer._limits).verify(model, limits=verification, allow_build_mismatch=allow_build_mismatch)
    if report.outcome != 'exact': raise ModelError('fork prefix is not verified: ' + report.outcome)
    state = _call('decode fork checkpoint', model.decode_state, boundary.after)
    provenance = BranchProvenance(1, artifact_identity(trace), sequence, _bytes_identity(boundary.after),
        trace.metadata, _call('branch metadata', model.metadata), True, policy.label,
        'out-of-domain-injection' if policy.unrestricted else 'verified-simulation-branch')
    session_type = EnumeratedSession if enumerated else DebugSession
    session = session_type(id, model, policy, state=state, limits=limits, recorder_options=recorder,
        config=RunConfig('debugger-branch', trace.config.seed), _provenance=provenance.parameters())
    return Branch(session, provenance)


