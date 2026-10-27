"""Optional synchronous, single-owner typed simulation.

No delivery effects are dispatched. Python guards reentrant mutations explicitly;
callbacks remain cooperative and are not sandboxed against reflection.
"""
from dataclasses import dataclass
from .core import ModelError, CheckPolicy, U64_MAX, _call, _copy, _natural, _step
from .checked import check_initial, check_turn, TurnObservation
from .debugger import InputPolicy


@dataclass(frozen=True)
class SessionLimits:
    max_turns: int = 100_000
    max_continue: int = 1000
    max_breakpoints: int = 64
    max_candidates: int = 1000

    def __post_init__(self):
        for name in ('max_turns', 'max_continue', 'max_candidates'):
            _natural(getattr(self, name), name, 1)
        _natural(self.max_breakpoints, 'max_breakpoints')


@dataclass(frozen=True)
class StopReason:
    kind: str
    detail: object = None


@dataclass(frozen=True)
class StepResult:
    sequence: int
    delivered: bool
    stop: StopReason | None


class DebugError(ModelError): pass


_UNSET = object()
_next_epoch = 1


class DebugSession:
    def __init__(self, id, model, policy, *, limits=SessionLimits(), state=_UNSET, check_policy=CheckPolicy(), recorder_options=None, config=None, _provenance=()):
        global _next_epoch
        initial = _copy(_call('initial state', model.initial_state) if state is _UNSET else state)
        if type(id) is not str or not id: raise ValueError('nonempty identity required')
        if type(policy) is not InputPolicy: raise ValueError('expected InputPolicy')
        if type(limits) is not SessionLimits: raise ValueError('expected SessionLimits')
        if type(check_policy) is not CheckPolicy: raise ValueError('expected CheckPolicy')
        if _next_epoch == U64_MAX: raise DebugError('session identity exhausted')
        self._id, self._epoch, self._model, self._policy, self._limits = id, _next_epoch, model, policy, limits
        _next_epoch += 1
        self._state = initial
        self._sequence = self._revision = 0
        self._phase, self._stop = 'ready', None
        self._check_policy, self._busy, self._pending = check_policy, False, None
        self._pre, self._post, self._next_breakpoint = {}, {}, 1
        self._last, self._issues, self._checks_complete = None, [], True
        self._recorder = None
        self._busy = True
        try:
            try:
                checked = check_initial(model, self._state)
                self._initial_checks = checked.checks
                if any(c.status == 'failed' for c in self._initial_checks): self._stop = StopReason('property_failure')
            except Exception as error:
                self._initial_checks, self._checks_complete = (), False
                self._stop = StopReason('initial_check_error', str(error))
            if recorder_options is not None and self._checks_complete:
                from .monitor import Recorder
                from .trace_binary import RunConfig
                try:
                    if check_policy.state_every != 1 or not check_policy.transition_checks:
                        raise ModelError('exact recording requires full checking')
                    base = RunConfig(strategy='debugger-simulation') if config is None else config
                    if any(k.startswith('stateless.debug.') for k,v in base.parameters):
                        raise ModelError('stateless.debug. parameters are reserved')
                    parameters = base.parameters + tuple(_provenance) + (('stateless.debug.environment', policy.label),
                        ('stateless.debug.origin', 'out-of-domain-injection' if policy.unrestricted else 'simulation'))
                    self._recorder = Recorder.with_checked_initial(checked, RunConfig(base.strategy, base.seed, parameters), recorder_options)
                except Exception as error:
                    self._issues.append('exact recording: ' + str(error))
                    if self._stop is None: self._stop = StopReason('recording_error', str(error))
            if self._stop: self._phase = 'ended'
        finally: self._busy = False

    @property
    def state(self): return _copy(self._state)
    @property
    def id(self): return self._id
    @property
    def epoch(self): return self._epoch
    @property
    def sequence(self): return self._sequence
    @property
    def revision(self): return self._revision
    @property
    def phase(self): return self._phase
    @property
    def stop(self): return self._stop
    @property
    def policy(self): return self._policy
    @property
    def limits(self): return self._limits
    @property
    def check_policy(self): return self._check_policy
    @property
    def initial_checks(self): return _copy(self._initial_checks)
    @property
    def observation(self): return _copy(self._last)
    @property
    def diagnostic_errors(self): return tuple(self._issues)
    @property
    def checks_complete(self): return self._checks_complete
    @property
    def capabilities(self):
        return {'mode': 'simulation', 'typed_input': True, 'exact_recording': self._recorder is not None,
                'enumerate_inputs': False, 'effects_dispatched': False, 'live_restore': False}

    def export_trace(self):
        self._guard()
        if self._recorder is None: raise DebugError('exact recording was not enabled')
        return self._recorder.snapshot()

    def write_trace(self, writer):
        self._guard()
        if self._recorder is None: raise DebugError('exact recording was not enabled')
        self._busy = True
        try: return self._recorder.write_to(writer)
        finally: self._busy = False

    def _guard(self):
        if self._busy: raise DebugError('session already owned by a callback')

    def check_revision(self, expected):
        _natural(expected, 'revision')
        if expected != self._revision: raise DebugError(f'stale revision: expected {expected}, actual {self._revision}')

    def add_breakpoint(self, predicate, *, post=False):
        self._guard()
        if not callable(predicate) or type(post) is not bool: raise ValueError('invalid breakpoint')
        if len(self._pre) + len(self._post) >= self._limits.max_breakpoints: raise DebugError('breakpoint budget exceeded')
        if self._next_breakpoint == U64_MAX: raise DebugError('breakpoint identity exhausted')
        id = self._next_breakpoint; self._next_breakpoint += 1
        (self._post if post else self._pre)[id] = predicate
        return id

    def remove_breakpoint(self, id):
        self._guard(); _natural(id, 'breakpoint')
        return self._pre.pop(id, None) is not None or self._post.pop(id, None) is not None

    def step(self, expected_revision, input, *, observer=None):
        self._guard(); self.check_revision(expected_revision)
        if self._phase in ('ended', 'disconnected'): raise DebugError('session ended')
        if self._sequence >= self._limits.max_turns or self._revision == U64_MAX:
            self._phase, self._stop = 'ended', StopReason('budget_exhausted')
            return StepResult(self._sequence, False, self._stop)
        self._busy = True
        try:
            input = _copy(input)
            if not self._policy.permits(self._model, self._state, input, self._limits.max_candidates): raise DebugError('input not permitted')
            bypass = self._pending is not None and self._pending[0] == self._revision and self._pending[1] == input
            self._pending = None
            if not bypass:
                old_phase, self._phase = self._phase, 'ended'
                for id, predicate in tuple(self._pre.items()):
                    value = _call('pre breakpoint', predicate, self.state, _copy(input))
                    if type(value) is not bool: raise ModelError('breakpoint must return bool')
                    if value:
                        self._pending = self._revision, _copy(input)
                        self._phase, self._stop = 'paused', StopReason('pre_breakpoint', id)
                        return StepResult(self._sequence, False, self._stop)
                self._phase = old_phase
            self._phase, self._stop = 'ended', None
            before = self.state
            try: transition = _step(self._model, before, input)
            except Exception as error:
                self._stop = StopReason('model_error', str(error))
                if self._recorder is not None: self._recorder.stop_with_error(error)
                return StepResult(self._sequence, False, self._stop)
            self._sequence += 1; self._revision += 1
            checks, boundary = (), 0
            try:
                checked = check_turn(self._model, before, input, transition, self._sequence, self._check_policy)
                checks, boundary, self._checks_complete = checked.checks, checked.state_check_count, True
                if any(c.status == 'failed' for c in checks): self._stop = StopReason('property_failure')
                if self._recorder is not None:
                    try: self._recorder.observe_checked(checked)
                    except Exception as error:
                        self._issues.append('exact recording: ' + str(error))
                        if self._stop is None: self._stop = StopReason('recording_error', str(error))
            except Exception as error:
                self._checks_complete, self._stop = False, StopReason('checker_error', str(error))
                if self._recorder is not None: self._recorder.stop_with_error(error)
            actual = TurnObservation(self._sequence, before, _copy(input), _copy(transition), checks, boundary)
            # Prepare publication before invoking fallible diagnostic callbacks.
            # Successful post predicates still see the precommit session state.
            committed_state, committed_last = _copy(transition.state), _copy(actual)
            try:
                if self._stop is None:
                    for id, predicate in tuple(self._post.items()):
                        value = _call('post breakpoint', predicate, _copy(actual))
                        if type(value) is not bool: raise ModelError('breakpoint must return bool')
                        if value:
                            self._stop = StopReason('post_breakpoint', id); break
            except Exception as error:
                self._issues.append(str(error))
                self._stop = StopReason('observer_error', str(error))
                raise
            finally:
                # A diagnostic fault cannot rewind a completed, recorded turn.
                self._state, self._last = committed_state, committed_last
            terminal = self._stop is not None and self._stop.kind != 'post_breakpoint'
            if observer is not None:
                try:
                    value = _call('debug observer', observer, self.observation)
                    if value is not None: raise ModelError('observer must return None synchronously')
                except Exception as error:
                    self._issues.append(str(error))
                    if self._stop is None: self._stop = StopReason('observer_error', str(error))
                    terminal = True
            self._phase = 'ended' if terminal else 'paused'
            return StepResult(self._sequence, True, self._stop)
        finally: self._busy = False

    def run_bounded(self, expected_revision, inputs, maximum):
        self._guard(); self.check_revision(expected_revision)
        _natural(maximum, 'maximum', 1, self._limits.max_continue)
        self._busy = True
        try: iterator = iter(inputs)
        finally: self._busy = False
        results = []
        for _ in range(maximum):
            self._busy = True
            try:
                try: input = next(iterator)
                except StopIteration: return tuple(results)
            finally: self._busy = False
            result = self.step(self._revision, input); results.append(result)
            if result.stop is not None: return tuple(results)
        self._phase, self._stop = 'paused', StopReason('budget_exhausted')
        return tuple(results)

    def cancel(self, expected_revision):
        self._guard(); self.check_revision(expected_revision)
        if self._phase in ('ended', 'disconnected'): raise DebugError('session ended')
        self._phase, self._stop = 'ended', StopReason('cancelled')
