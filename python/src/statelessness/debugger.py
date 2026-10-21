"""Optional offline diagnostics. Import explicitly; no clocks or transport start.

Callbacks are cooperative. Exact byte previews can contain secrets. Verification
and minimization execute application code only when explicitly requested.
"""
from dataclasses import dataclass, replace
from .core import ModelError, Failure, Violation, validate_recording, _copy, _natural, _call
from .evidence import Artifact, EvidenceLimits, _limits
from .replay_evidence import replay_with_observations_bounded, _audit_preflight
from .search import ShrinkConfig, ShrinkLimits, RunLimits, shrink


@dataclass(frozen=True)
class Boundary:
    before: bytes
    after: bytes
    input: bytes | None
    outputs: tuple[bytes, ...]
    checks: tuple
    causal: object | None


@dataclass(frozen=True)
class EncodedPreview:
    bytes: bytes
    total_bytes: int
    complete: bool
    provenance: str = 'stored-exact-encoding'


@dataclass(frozen=True)
class VerificationLimits:
    max_transitions: int = 100_000
    control: RunLimits = RunLimits()

    def __post_init__(self):
        _natural(self.max_transitions, 'max_transitions')
        if type(self.control) is not RunLimits: raise ValueError('expected RunLimits')


@dataclass(frozen=True)
class InputPolicy:
    label: str
    admission: object = None
    unrestricted: bool = False
    enumerated_domain: bool = False

    def __post_init__(self):
        if type(self.label) is not str or not self.label: raise ValueError('policy label required')
        if type(self.unrestricted) is not bool: raise ValueError('unrestricted must be boolean')
        if self.unrestricted:
            if self.label != 'out-of-domain-injection' or self.admission is not None:
                raise ValueError('unrestricted policy requires explicit out-of-domain-injection label')
        elif self.enumerated_domain:
            if self.label != 'enumerated-domain' or self.admission is not None: raise ValueError('invalid enumerated policy')
        elif not callable(self.admission): raise ValueError('declared policy requires admission callback')

    @classmethod
    def enumerated(cls): return cls('enumerated-domain', enumerated_domain=True)

    def permits(self, model, state, input, maximum=1000):
        if self.unrestricted: return True
        if self.enumerated_domain:
            _natural(maximum, 'admission maximum', 1)
            iterator = iter(_call('enumerated input domain', model.inputs, _copy(state)))
            sentinel = object()
            for _ in range(maximum):
                value = _call('enumerated input next', next, iterator, sentinel)
                if value is sentinel: return False
                if value == input: return True
            if _call('enumerated input lookahead', next, iterator, sentinel) is not sentinel:
                raise ModelError('environmental admission work budget exhausted')
            return False
        value = _call('debugger input admission', self.admission, model, _copy(state), _copy(input))
        if type(value) is not bool: raise ModelError('admission must return bool')
        return value


class EvidenceViewer:
    """Owned, semantically validated v1/v2 artifact; construction is passive."""
    def __init__(self, artifact, *, limits=EvidenceLimits()):
        self._limits = _limits(limits)
        if type(artifact) is not Artifact: raise ModelError('expected Artifact')
        if artifact.version == 2: artifact.value.validate(self._limits)
        else:
            portable, _ = artifact.trace.to_portable()
            validate_recording(portable)
            _audit_preflight(artifact.trace, self._limits.trace)
        self._artifact = _copy(artifact)
        self.retained_range  # Reject malformed checkpoint/overflow while passive.

    @classmethod
    def read(cls, reader, *, limits=EvidenceLimits()):
        return cls(Artifact.read(reader, limits=limits), limits=limits)

    @property
    def artifact(self): return _copy(self._artifact)

    @property
    def retained_range(self):
        start = self._artifact.value.checkpoint_after if self._artifact.version == 2 else 0
        if self._artifact.version == 1:
            values = [v for k, v in self._artifact.trace.config.parameters if k == 'stateless.monitor.checkpoint_after_sequence']
            if values:
                digits=values[0][1:] if values[0].startswith('+') else values[0]
                if not digits or not digits.isascii() or not digits.isdigit(): raise ModelError('invalid retained checkpoint sequence')
                start = int(digits)
        try:
            _natural(start, 'checkpoint sequence'); _natural(start + len(self._artifact.trace.steps), 'retained end')
        except ValueError as error: raise ModelError('retained sequence overflow') from error
        return start, start + len(self._artifact.trace.steps)

    def boundary(self, sequence):
        _natural(sequence, 'sequence')
        trace = self._artifact.trace
        if sequence > len(trace.steps): return None
        if sequence == 0:
            return Boundary(trace.initial_state, trace.initial_state, None, (), trace.initial_checks, None)
        step = trace.steps[sequence - 1]
        before = trace.initial_state if sequence == 1 else trace.steps[sequence - 2].post_state
        causal = self._artifact.value.steps[sequence - 1] if self._artifact.version == 2 else None
        return _copy(Boundary(before, step.post_state, step.input, step.outputs, step.checks, causal))

    def preview(self, sequence, maximum):
        _natural(maximum, 'maximum', maximum=4096)
        boundary = self.boundary(sequence)
        if boundary is None: raise ModelError('snapshot outside retained history')
        return EncodedPreview(boundary.after[:maximum], len(boundary.after), len(boundary.after) <= maximum)

    def failure(self, sequence, check):
        _natural(check, 'check')
        boundary = self.boundary(sequence)
        if boundary is None or check >= len(boundary.checks): raise ModelError('unknown evidence check')
        selected = boundary.checks[check]
        if selected.status != 'failed': raise ModelError('selected check is not a failure')
        phase = self._artifact.check_phase(sequence, check)
        if phase is None: raise ModelError('v1 phase unknown; explicitly verify and upgrade or supply a phase')
        return Violation(phase, selected)

    def verify(self, model, *, limits=VerificationLimits(), allow_build_mismatch=False):
        if type(limits) is not VerificationLimits: raise ValueError('expected VerificationLimits')
        if len(self._artifact.trace.steps) > limits.max_transitions: raise ModelError('verification transition budget')
        import time
        started = time.monotonic()
        def stop():
            if limits.control.cancellation is not None and limits.control.cancellation(): raise ModelError('verification cancelled')
            if limits.control.max_duration is not None and time.monotonic() - started >= limits.control.max_duration: raise ModelError('verification deadline')
        def observe(value, report):
            stop()
            if value.kind == 'turn' and self._artifact.version == 2 and report.outcome == 'exact':
                if self._artifact.value.steps[value.actual.sequence - 1].state_check_count != value.actual.state_check_count:
                    raise ModelError('persisted check phase differs from replay')
        stop()
        result = replay_with_observations_bounded(model, self._artifact.trace,
            observe, limits=self._limits.trace, allow_build_mismatch=allow_build_mismatch)
        stop()
        return result

    def minimize(self, model, sequence, check, config=ShrinkConfig(), *, limits=ShrinkLimits(), policy=None, phase=None, max_admission_candidates=1000):
        """Checkpoint-based bounded shrink, preserving selected phase and policy.

        Verification consumes the same overall deadline/transition allowance.
        Policy labels are provenance assertions, not authenticated capabilities.
        """
        target = self.failure(sequence, check) if phase is None else self._explicit_failure(sequence, check, phase)
        _natural(max_admission_candidates, 'max_admission_candidates', 1)
        trace = self._artifact.trace
        if sequence != len(trace.steps):
            if sequence != 0: raise ModelError('target must be terminal recorded failure')
        self._validate_policy(policy)
        # Clock is imported only for an explicit execution request.
        import time
        started = time.monotonic()
        def stopped():
            if limits.control.cancellation is not None and limits.control.cancellation():
                raise ModelError('minimization cancelled during verification')
            if limits.control.max_duration is not None and time.monotonic() - started >= limits.control.max_duration:
                raise ModelError('minimization deadline exhausted during verification')
        count = len(trace.steps) * (2 if self._artifact.version == 2 else 1)
        if limits.max_replayed_transitions is not None and (len(trace.steps) > limits.max_replayed_transitions or
                (self._artifact.version == 1 and len(trace.steps) > 0 and count >= limits.max_replayed_transitions)):
            raise ModelError('minimization budget cannot cover verification')
        stopped()
        def observe(value, report):
            stopped()
            if value.kind == 'turn' and self._artifact.version == 2 and report.outcome == 'exact':
                expected = self._artifact.value.steps[value.actual.sequence - 1].state_check_count
                if expected != value.actual.state_check_count: raise ModelError('persisted check phase differs from replay')
        report = replay_with_observations_bounded(model, trace, observe, limits=self._limits.trace)
        stopped()
        if report.outcome != 'exact' or not report.failure_reproduced:
            raise ModelError('minimization requires exact compatible failure replay')
        if self._artifact.version == 2:
            if limits.max_replayed_transitions is not None and len(trace.steps) > 0 and count >= limits.max_replayed_transitions:
                raise ModelError('minimization budget cannot cover verification and original validation')
            report = replay_with_observations_bounded(model, trace, lambda *_: stopped(), limits=self._limits.trace)
            stopped()
            if report.outcome != 'exact' or not report.failure_reproduced:
                raise ModelError('minimization requires exact compatible failure replay')
        checkpoint = trace.initial_state
        class CheckpointModel:
            def __getattr__(self, name): return getattr(model, name)
            def initial_state(self): return _call('decode checkpoint', model.decode_state, checkpoint)
            def is_enabled(self, state, input):
                if policy is not None and not policy.permits(model, state, input, max_admission_candidates): return False
                enabled = _call('causal input admission', model.is_enabled, _copy(state), _copy(input))
                if type(enabled) is not bool: raise ModelError('is_enabled must return bool')
                return enabled
        inputs = tuple(_copy(_call('decode input', model.decode_input, s.input)) for s in trace.steps[:sequence])
        remaining_time = None if limits.control.max_duration is None else max(0, limits.control.max_duration - (time.monotonic() - started))
        remaining_count = None if limits.max_replayed_transitions is None else limits.max_replayed_transitions - count
        result = shrink(CheckpointModel(), Failure(inputs, (target,)), config,
            limits=ShrinkLimits(RunLimits(remaining_time, limits.control.cancellation), remaining_count))
        return replace(result, replayed_transitions=result.replayed_transitions + count)

    def _explicit_failure(self, sequence, check, phase):
        if phase not in ('initial_state', 'state', 'transition'): raise ModelError('unknown failure phase')
        boundary = self.boundary(sequence)
        _natural(check, 'check')
        if boundary is None or check >= len(boundary.checks) or boundary.checks[check].status != 'failed':
            raise ModelError('unknown evidence failure')
        recorded = self._artifact.check_phase(sequence, check)
        if recorded is not None and recorded != phase: raise ModelError('selected phase differs from evidence')
        if (sequence == 0) != (phase == 'initial_state'): raise ModelError('phase differs from boundary')
        return Violation(phase, boundary.checks[check])

    def _validate_policy(self, policy):
        if policy is not None and type(policy) is not InputPolicy: raise ModelError('expected InputPolicy')
        params = self._artifact.trace.config.parameters
        def unique(name):
            values = [v for k, v in params if k == name]
            if len(values) > 1: raise ModelError('ambiguous recorded environmental policy')
            return values[0] if values else None
        environment, origin = unique('stateless.debug.environment'), unique('stateless.debug.origin')
        if environment is None and origin is None: return
        if environment is None or origin is None: raise ModelError('incomplete recorded environmental policy provenance')
        if origin not in ('simulation', 'out-of-domain-injection'): raise ModelError('unknown recorded environmental policy origin')
        unrestricted = origin == 'out-of-domain-injection'
        if policy is None:
            if unrestricted and environment == 'out-of-domain-injection': return
            raise ModelError('recorded environmental policy requires explicit policy')
        if policy.label != environment or policy.unrestricted != unrestricted:
            raise ModelError('minimization environment differs from recorded policy')
