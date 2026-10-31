"""Candidate native generation, seeded fuzzing and causal shrinking.

Limits are cooperative: callbacks and iterator pulls cannot be preempted.
EXHAUSTED denotes an exhausted domain; every other value, including None, is input.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import sys
import time
from typing import TYPE_CHECKING, Callable, Generic, Iterable, Protocol

from .core import (S, I, O, Check, Transition, Model, EnumerateModel, ModelError, Rng, Failure,
                   Violation, _Captured, _call, _copy, _error_detail, _natural, _step, _saturating_add_u64, _dispatch_checks)

if TYPE_CHECKING:
    from .guided import GuidanceReport


class _Exhausted:
    def __deepcopy__(self, memo):
        return self

    def __repr__(self):
        return "EXHAUSTED"


EXHAUSTED = _Exhausted()


class GenerateModel(Model[S, I, O], Protocol[S, I, O]):
    def generate(self, state: S, rng: Rng) -> I | _Exhausted: ...
    def is_enabled(self, state: S, input: I) -> bool: ...
    def simpler_inputs(self, input: I) -> Iterable[I]: ...


class Generated(Generic[S, I, O]):
    """Explicit generation adapter; all other model operations delegate unchanged."""
    def __init__(self, model: Model[S, I, O], generate: Callable[[S, Rng], I | _Exhausted],
                 *, is_enabled: Callable[[S, I], bool] | None = None,
                 simpler_inputs: Callable[[I], Iterable[I]] | None = None):
        self.model = model
        self._generate = generate
        self._enabled = is_enabled
        self._simpler = simpler_inputs

    def __getattr__(self, name):
        return getattr(self.model, name)

    def generate(self, state, rng):
        return _copy(self._generate(_copy(state), rng))

    def is_enabled(self, state, input):
        # Select the callback before application-defined copy hooks can mutate
        # this adapter. The absent callback remains a no-copy fast path.
        callback = self._enabled
        if callback is None: return True
        captured_input = _copy(input)
        result = callback(_copy(state), captured_input)
        if type(result) is not bool: raise ModelError("is_enabled must return bool")
        return result

    def simpler_inputs(self, input):
        return () if self._simpler is None else (_copy(value) for value in self._simpler(_copy(input)))


@dataclass(frozen=True)
class AutoOptions:
    max_candidates: int = 100_000

    def __post_init__(self):
        _natural(self.max_candidates, "max_candidates", 1)


class Auto(Generic[S, I, O]):
    """Reservoir sample a complete finite inputs() domain, retaining one input.

    Duplicate entries carry duplicate weight. A max+1 pull detects overflow;
    overflow/iterator errors leave the caller RNG unchanged. Membership stops at
    its first match, so a match within the cap need not inspect the suffix.
    Membership copies its query before obtaining the domain; the copy hook can
    have side effects even for an empty domain. Copy failures are deferred until
    a candidate is compared. Copy hooks must cooperate with external aliases.
    """
    def __init__(self, model: EnumerateModel[S, I, O], options: AutoOptions = AutoOptions()):
        self.model, self.options = model, options

    def __getattr__(self, name):
        return getattr(self.model, name)

    def _inputs(self, state):
        domain = _call("automatic inputs", self.model.inputs, _copy(state))
        return _call("automatic inputs", iter, domain)

    def generate(self, state, rng):
        return self._generate(state, rng, None)

    def _generate(self, state, rng, poll):
        maximum = _natural(self.options.max_candidates, "max_candidates", 1)
        working = Rng(rng.state)
        selected = EXHAUSTED
        sentinel = object()
        iterator = self._inputs(state)
        count = 0
        while True:
            if poll is not None: poll()
            candidate = _call("automatic inputs next", next, iterator, sentinel)
            if candidate is sentinel:
                if poll is not None: poll()
                rng.state = working.state
                return selected
            count += 1
            if count > maximum:
                raise ModelError("automatic input domain exceeds max_candidates")
            if count == 1 or working.index(count) == 0:
                selected = _copy(candidate)

    def is_enabled(self, state, input):
        return self._is_enabled(state, input, None)

    def _is_enabled(self, state, input, poll):
        maximum = _natural(self.options.max_candidates, "max_candidates", 1)
        # Preserve the query before state-copy and domain callbacks. Defer a
        # copy failure until a candidate needs comparison, so an empty or
        # failing domain retains its normal result/error precedence.
        query = _Captured(input)
        sentinel = object()
        iterator = self._inputs(state)
        count = 0
        while True:
            if poll is not None: poll()
            candidate = _call("automatic inputs next", next, iterator, sentinel)
            if candidate is sentinel:
                return False
            count += 1
            if count > maximum:
                raise ModelError("automatic input domain exceeds max_candidates")
            if _equal(candidate, query.get()):
                return True

    def simpler_inputs(self, input):
        return ()


@dataclass(frozen=True)
class RunLimits:
    max_duration: float | None = None
    cancellation: Callable[[], bool] | None = None

    def __post_init__(self):
        if self.max_duration is not None and (isinstance(self.max_duration, bool) or
                not isinstance(self.max_duration, (float, int)) or
                not math.isfinite(self.max_duration) or self.max_duration < 0):
            raise ValueError("max_duration must be finite nonnegative seconds")
        if self.cancellation is not None and not callable(self.cancellation):
            raise ValueError("cancellation must be callable")


@dataclass(frozen=True)
class SearchLimits:
    control: RunLimits = RunLimits()
    max_estimated_state_bytes: int | None = None

    def __post_init__(self):
        if not isinstance(self.control, RunLimits):
            raise ValueError("control must be RunLimits")
        if self.max_estimated_state_bytes is not None:
            _natural(self.max_estimated_state_bytes, "max_estimated_state_bytes")


def _estimate(model, state):
    callback = getattr(model, "estimated_state_bytes", None)
    value = None if callback is None else _call("estimated_state_bytes", callback, _copy(state))
    if value is not None and (type(value) is not int or not 0 <= value <= (1 << 64) - 1):
        raise ModelError("estimated_state_bytes must return an unsigned 64-bit integer or None")
    return value


class _Control:
    def __init__(self, limits):
        self.limits = limits
        self.deadline = (None if limits.max_duration is None else
                         time.monotonic() + limits.max_duration)

    def stop(self):
        if self.limits.cancellation is not None:
            cancelled = _call("cancellation", self.limits.cancellation)
            if type(cancelled) is not bool:
                raise ModelError("cancellation must return bool")
            if cancelled:
                return "cancelled"
        if self.deadline is not None and time.monotonic() >= self.deadline:
            return "deadline"
        return None


def _enabled(model, state, input):
    callback = getattr(model, "is_enabled", None)
    if callback is None:
        value = True
    else:
        captured_input = _copy(input)
        value = _call("is_enabled", callback, _copy(state), captured_input)
    if type(value) is not bool:
        raise ModelError("is_enabled must return bool")
    return value


def _equal(left, right):
    return _call("input equality", lambda: bool(_copy(left) == _copy(right)))


class SearchObserver(Protocol[S, I, O]):
    """Optional observation hooks; methods may be omitted by implementations.

    Snapshots and complete initial-state input prefixes are owned by the observer.
    Hooks describe actual search, including failing edges. check_error exposes
    partial check batches without certifying the edge that failed to check.
    """
    def needs_history(self) -> bool:
        return False
    def initial(self, state: S, checks: tuple[Check, ...]) -> None: ...
    def transition(self, before: S, input: I, transition: Transition[S, O],
                   state_checks: tuple[Check, ...], transition_checks: tuple[Check, ...],
                   history: tuple[I, ...]) -> None: ...
    def check_error(self, phase: str, checks: tuple[Check, ...], complete: bool) -> None: ...


def _notify(observer, method, *args):
    callback = getattr(observer, method, None) if observer is not None else None
    if callback is not None:
        _call("search observer " + method, callback, *(_copy(arg) for arg in args))


def _notify_check_error(observer, phase, checks, complete, primary):
    try:
        _notify(observer, "check_error", phase, checks, complete)
    except ModelError as secondary:
        # Checker errors remain primary even when error reporting itself fails.
        primary.observer_errors = getattr(primary, "observer_errors", ()) + (secondary,)


_NO_STATE_SNAPSHOT = object()


def _violations(model, state, input=None, transition=None, *, observer=None, history=(), storage=None, edge_capture_error=None, state_snapshot=_NO_STATE_SNAPSHOT, state_snapshot_error=None):
    values = [] if storage is None else storage
    values.clear()
    initial = transition is None
    phase = "initial_state" if initial else "state"
    # Capture the edge before state checks can mutate application-owned aliases.
    # Defer snapshot failures to the stage that needs them, so state-check errors
    # retain precedence and observer history failures do not mask transition checks.
    edge = history_snapshot = None
    initial_callback = initial_snapshot = initial_lookup_error = None
    initial_check_snapshot = None
    if initial and observer is not None:
        initial_check_snapshot = _Captured(state)
        initial_snapshot = _Captured(state)
        try:
            initial_callback = getattr(observer, "initial", None)
        except Exception as error:
            initial_lookup_error = error
    observer_transition = transition_lookup_error = None
    if not initial:
        edge = _Captured((state, input, transition))
        if observer is not None:
            # Resolve the hook once, deferring a lookup failure like the initial
            # hook. Copy history only for an observer that will consume it.
            try:
                observer_transition = _call("search observer transition", getattr, observer, "transition", None)
            except ModelError as error:
                transition_lookup_error = error
            if observer_transition is not None:
                history_snapshot = _Captured(tuple(history))
    try:
        if state_snapshot_error is not None:
            raise state_snapshot_error
        initial_check_state = (initial_check_snapshot.get() if initial and observer is not None else state)
        _dispatch_checks(model, "check_state_into", "check_state",
                         (_copy(initial_check_state if initial else (state_snapshot if state_snapshot is not _NO_STATE_SNAPSHOT else transition.state)),), values,
                         "initial check_state" if initial else "check_state")
    except ModelError as error:
        if observer is not None:
            _notify_check_error(observer, phase, tuple(values), False, error)
        raise
    boundary = len(values)
    if initial:
        if initial_lookup_error is not None:
            raise ModelError(f"search observer initial: {_error_detail(initial_lookup_error)}") from initial_lookup_error
        if initial_callback is not None:
            snapshot = initial_snapshot.get()
            _call("search observer initial", initial_callback, _copy(snapshot), _copy(tuple(values)))
    else:
        try:
            def resolve_transition_check():
                append = getattr(model, "check_transition_into", None)
                return append, None if append is not None else getattr(model, "check_transition", None)
            append, legacy = _call("transition check", resolve_transition_check)
            if append is not None or legacy is not None:
                if edge_capture_error is not None:
                    raise edge_capture_error
                before_snapshot, input_snapshot, transition_snapshot = edge.get()
                class PinnedCheck:
                    check_transition_into = staticmethod(append) if append is not None else None
                    check_transition = staticmethod(legacy) if legacy is not None else None
                _dispatch_checks(PinnedCheck(), "check_transition_into", "check_transition",
                                 (_copy(before_snapshot), _copy(input_snapshot), _copy(transition_snapshot)), values,
                                 "transition check", optional=True)
        except ModelError as error:
            if observer is not None:
                _notify_check_error(observer, "state", tuple(values[:boundary]), True, error)
                _notify_check_error(observer, "transition", tuple(values[boundary:]), False, error)
            raise
        if transition_lookup_error is not None:
            raise transition_lookup_error
        if observer_transition is not None:
            if edge_capture_error is not None:
                raise edge_capture_error
            before_snapshot, input_snapshot, transition_snapshot = edge.get()
            history_value = history_snapshot.get()
            _call("search observer transition", observer_transition,
                  *(_copy(arg) for arg in (before_snapshot, input_snapshot, transition_snapshot,
                    tuple(values[:boundary]), tuple(values[boundary:]), history_value)))
    failures = tuple(Violation("initial_state" if initial else "state" if index < boundary else "transition", check)
                     for index, check in enumerate(values) if check.status == "failed")
    skipped = _saturating_add_u64(0, sum(check.status == "skipped" for check in values))
    return failures, skipped


@dataclass(frozen=True)
class FuzzConfig:
    seed: int = 0
    cases: int = 1_000
    max_steps: int = 100
    max_transitions: int = 100_000
    mutation_percent: int = 50

    def __post_init__(self):
        for name in ("seed", "cases", "max_steps", "max_transitions"):
            _natural(getattr(self, name), name)
        _natural(self.mutation_percent, "mutation_percent", maximum=255)

    def validate(self):
        """Validate at admission, after enclosing campaign bounds."""
        if self.mutation_percent > 100:
            raise ModelError("mutation_percent must be in 0..=100")


@dataclass(frozen=True)
class FuzzReport(Generic[I]):
    termination: str
    seed: int
    cases: int
    transitions: int
    skipped_checks: int
    failure: Failure[I] | None = None
    guidance: GuidanceReport[I] | None = None


class _PreviousSequence:
    def candidate(self, previous, rng, mutation_percent):
        if not previous or rng.index(100) >= mutation_percent:
            return (), None
        mutated = list(_copy(previous))
        start = rng.index(len(mutated))
        length = 1 + rng.index(len(mutated) - start)
        del mutated[start:start + length]
        return mutated, rng.index(len(mutated) + 1)


class _AutoScanStopped(BaseException):
    def __init__(self, owner, reason):
        self.owner, self.reason = owner, reason


class _ControlledAuto(Auto):
    def __init__(self, model, options, control):
        super().__init__(model, options)
        self.control = control

    def _poll(self):
        reason = self.control.stop()
        if reason:
            raise _AutoScanStopped(self, reason)

    def generate(self, state, rng):
        return self._generate(state, rng, self._poll)

    def is_enabled(self, state, input):
        return self._is_enabled(state, input, self._poll)


def fuzz_auto(model: EnumerateModel[S, I, O], config: FuzzConfig = FuzzConfig(),
              *, options: AutoOptions = AutoOptions(), limits: RunLimits = RunLimits(),
              observer: SearchObserver[S, I, O] | None = None) -> FuzzReport[I]:
    """Fuzz a finite domain with cancellation between automatic iterator pulls.

    Application callbacks cannot be preempted; an interrupted sampling scan does
    not commit its tentative random state. Existing Auto/fuzz behavior is unchanged.
    """
    if not isinstance(config, FuzzConfig): raise ValueError("config must be FuzzConfig")
    if not isinstance(limits, RunLimits): raise ValueError("limits must be RunLimits")
    if not isinstance(options, AutoOptions): raise ValueError("options must be AutoOptions")
    config = FuzzConfig(config.seed, config.cases, config.max_steps, config.max_transitions, config.mutation_percent)
    config.validate()
    options = AutoOptions(options.max_candidates)
    control = _Control(RunLimits(limits.max_duration, limits.cancellation))
    return _fuzz_with_driver(_ControlledAuto(model, options, control), config, control, observer)


def fuzz(model: GenerateModel[S, I, O], config: FuzzConfig = FuzzConfig(),
         *, limits: RunLimits = RunLimits(),
         observer: SearchObserver[S, I, O] | None = None) -> FuzzReport[I]:
    """Seeded state-aware search; completion proves no graph exhaustion."""
    if not isinstance(config, FuzzConfig):
        raise ValueError("config must be FuzzConfig")
    if not isinstance(limits, RunLimits):
        raise ValueError("limits must be RunLimits")
    config = FuzzConfig(config.seed, config.cases, config.max_steps,
                        config.max_transitions, config.mutation_percent)
    limits = RunLimits(limits.max_duration, limits.cancellation)
    return _fuzz_with_driver(model, config, _Control(limits), observer)


def _fuzz_with_driver(model, config, control, observer, driver=None):
    """One execution/check kernel; ordinary search keeps its existing polls.

    Guided drivers add initial/transition observations and their post-feedback
    control boundary. Previous-sequence fuzz has no feedback callbacks and retains
    the same polling count and order it had before this extraction.
    """
    config.validate()
    rng = Rng(config.seed)
    candidate_driver = _PreviousSequence() if driver is None else driver
    previous = ()
    cases = transitions = skipped = 0
    check_storage = []

    def report(reason, failure=None):
        return FuzzReport(reason, config.seed, cases, transitions, skipped, failure)

    stop = control.stop()
    if stop:
        return report(stop)
    for _ in range(config.cases):
        stop = control.stop()
        if stop:
            return report(stop)
        state = _copy(_call("initial_state", model.initial_state))
        cases += 1
        # Checks and observers receive application callbacks. Keep the search's
        # continuation state independent of aliases they may mutate. A failed
        # extra copy is only needed if the run continues past this boundary.
        initial_state = initial_error = None
        try:
            initial_state = _copy(state)
        except ModelError as error:
            initial_error = error
        violations, count = _violations(model, state, observer=observer, storage=check_storage)
        skipped = _saturating_add_u64(skipped, count)
        if violations:
            return report("failure_found", Failure((), violations))
        stop = control.stop()
        if stop:
            return report(stop)
        if driver is not None:
            if initial_error is not None:
                raise initial_error
            state = initial_state
            continuation = continuation_error = None
            try:
                continuation = _copy(state)
            except ModelError as error:
                continuation_error = error
            driver.initial(state, cases)
            stop = control.stop()
            if stop:
                return report(stop)
            state = continuation
            initial_error = continuation_error
        else:
            state = initial_state
        mutated, replace_at = candidate_driver.candidate(previous, rng, config.mutation_percent)
        inputs = []
        for step in range(config.max_steps):
            if transitions == config.max_transitions:
                return report("transition_limit")
            stop = control.stop()
            if stop:
                return report(stop)
            if initial_error is not None:
                raise initial_error
            feedback_before = None
            if driver is not None:
                feedback_before = _Captured(state)
            candidate_copy = candidate_error = None
            eligible = step != replace_at and step < len(mutated)
            if eligible:
                try:
                    candidate_copy = _copy(mutated[step])
                except Exception as error:
                    candidate_error = error
            try:
                reused = eligible and _enabled(
                    model, state, candidate_copy if candidate_error is None else mutated[step])
            except _AutoScanStopped as stopped:
                if stopped.owner is not model: raise
                return report(stopped.reason)
            stop = control.stop()
            if stop:
                return report(stop)
            if reused:
                input = mutated[step]
                retained_input, retained_error = candidate_copy, candidate_error
            else:
                try:
                    input = _copy(_call("generate", model.generate, _copy(state), rng))
                except _AutoScanStopped as stopped:
                    if stopped.owner is not model: raise
                    return report(stopped.reason)
                retained_input = retained_error = None
                if input is not EXHAUSTED:
                    try:
                        retained_input = _copy(input)
                    except Exception as error:
                        retained_error = error
            stop = control.stop()
            if stop:
                return report(stop)
            if input is EXHAUSTED:
                break
            try:
                enabled = reused or _enabled(
                    model, state, retained_input if retained_error is None else input)
            except _AutoScanStopped as stopped:
                if stopped.owner is not model: raise
                return report(stopped.reason)
            stop = control.stop()
            if stop:
                return report(stop)
            if not enabled:
                raise ModelError("generate returned a causally disabled input")
            # Capture before admission callbacks, but preserve disabled/stop
            # precedence if the extra copy failed and was never needed.
            if retained_error is not None:
                raise retained_error
            feedback_input = None
            if driver is not None:
                feedback_input = _Captured(retained_input)
            transition = _step(model, state, retained_input, stage="step")
            transitions += 1
            successor = successor_error = None
            try:
                successor = _copy(transition.state)
            except ModelError as error:
                successor_error = error
            checks_transition = checks_transition_error = None
            try:
                checks_transition = _copy(transition)
            except ModelError as error:
                checks_transition_error = error
            feedback_transition = None
            if driver is not None:
                feedback_transition = _Captured(transition)
            inputs.append(_copy(retained_input))
            violations, count = _violations(model, state, retained_input,
                checks_transition if checks_transition_error is None else transition,
                observer=observer, history=inputs, storage=check_storage,
                edge_capture_error=checks_transition_error,
                state_snapshot=successor if checks_transition_error is not None else _NO_STATE_SNAPSHOT,
                state_snapshot_error=successor_error if checks_transition_error is not None else None)
            skipped = _saturating_add_u64(skipped, count)
            if violations:
                return report("failure_found", Failure(tuple(inputs), violations))
            stop = control.stop()
            if stop:
                return report(stop)
            if driver is not None:
                # Raise deferred capture failures in before/input/transition order.
                feedback_args = (feedback_before.get(), feedback_input.get(), feedback_transition.get())
                # The search-owned prefix is lent, not copied: the driver
                # snapshots it only when it retains a corpus entry, so a step
                # does not copy the whole history (quadratic per case).
                driver.transition(*feedback_args, inputs, cases, transitions)
                stop = control.stop()
                if stop:
                    return report(stop)
            # No later step consumes the successor after the final iteration.
            if step + 1 < config.max_steps:
                state, initial_error = successor, successor_error
        previous = tuple(inputs)
    return report("cases_completed")


@dataclass(frozen=True)
class ShrinkConfig:
    max_attempts: int = 10_000

    def __post_init__(self):
        _natural(self.max_attempts, "max_attempts", 1)


@dataclass(frozen=True)
class ShrinkLimits:
    control: RunLimits = RunLimits()
    max_replayed_transitions: int | None = None

    def __post_init__(self):
        if self.max_replayed_transitions is not None:
            _natural(self.max_replayed_transitions, "max_replayed_transitions")


@dataclass(frozen=True)
class ShrinkOperation:
    kind: str
    start: int | None = None
    end: int | None = None
    index: int | None = None
    alternative: int | None = None


@dataclass(frozen=True)
class ShrinkAttempt:
    attempt: int
    candidate_length: int
    operation: ShrinkOperation
    outcome: str


@dataclass(frozen=True)
class ShrinkReport(Generic[I]):
    original: Failure[I]
    minimized: Failure[I]
    attempts: int
    termination: str
    history: tuple[ShrinkAttempt, ...]
    replayed_transitions: int
    validated_original: bool


def shrink(model: GenerateModel[S, I, O], original: Failure[I],
           config: ShrinkConfig = ShrinkConfig(), *,
           limits: ShrinkLimits = ShrinkLimits()) -> ShrinkReport[I]:
    """Replay-preserving deletion/simplification; no global minimality claim.

    Candidate copies are captured ahead of replay (up to the transition budget),
    so copy hooks can run for inputs never reached after an earlier failure or
    cancellation. Copy hooks must cooperate; concurrent external alias mutation
    cannot be made atomic.
    """
    config = ShrinkConfig(config.max_attempts)
    limits = ShrinkLimits(RunLimits(limits.control.max_duration, limits.control.cancellation),
                          limits.max_replayed_transitions)
    original = _copy(original)
    if not original.violations or original.violations[0].check.status != "failed":
        raise ModelError("cannot shrink a sequence without a target violation")
    target = original.violations[0]
    control = _Control(limits.control)
    transitions = attempts = 0
    history = []
    check_storage = []
    termination = "search_complete"
    minimized = original
    validated = False
    seen = []

    def has_target(failure):
        return any(v.phase == target.phase and v.check.id == target.check.id and
                   v.check.status == "failed" for v in failure.violations)

    def known(inputs):
        for prior in seen:
            if len(prior) != len(inputs):
                continue
            equal = True
            for left, right in zip(prior, inputs):
                if control.stop():
                    return False
                same = _equal(left, right)
                if control.stop():
                    return False
                if not same:
                    equal = False
                    break
            if equal:
                return True
        return False

    def replay_candidate(inputs):
        nonlocal transitions
        stop = control.stop()
        if stop:
            return stop, None
        # Retain the candidate before callbacks can mutate later inputs. Defer
        # copy failures until replay reaches the corresponding position.
        input_count = len(inputs)
        capture_count = input_count
        if limits.max_replayed_transitions is not None:
            capture_count = min(input_count, max(0, limits.max_replayed_transitions - transitions))
        captured = [_Captured(value) for value in inputs[:capture_count]]
        state = _copy(_call("shrink initial_state", model.initial_state))
        violations, _ = _violations(model, state, storage=check_storage)
        if violations:
            return "failure", Failure((), violations)
        stop = control.stop()
        if stop:
            return stop, None
        retained_inputs = []
        for index in range(input_count):
            stop = control.stop()
            if stop:
                return stop, None
            if limits.max_replayed_transitions is not None and transitions >= limits.max_replayed_transitions:
                return "transition_limit", None
            input = captured[index].get()
            retained_inputs.append(input)
            enabled = _enabled(model, state, input)
            stop = control.stop()
            if stop:
                return stop, None
            if not enabled:
                return "invalid_causality", None
            transition = _step(model, state, input, stage="step")
            transitions += 1
            violations, _ = _violations(model, state, input, transition, storage=check_storage)
            if violations:
                return "failure", Failure(_copy(tuple(retained_inputs)), violations)
            stop = control.stop()
            if stop:
                return stop, None
            state = transition.state
        return "no_failure", None

    def result():
        return ShrinkReport(_copy(original), _copy(minimized), attempts, termination,
                            tuple(history), transitions, validated)

    outcome, failure = replay_candidate(original.inputs)
    attempts = 1
    if outcome in ("cancelled", "deadline", "transition_limit"):
        termination = outcome
        history.append(ShrinkAttempt(1, len(original.inputs), ShrinkOperation("validate_original"), "resource_limit"))
        return result()
    if outcome != "failure" or not has_target(failure):
        raise ModelError("original failure does not reproduce the target check and phase with valid causality")
    minimized, validated = failure, True
    seen.append(_copy(minimized.inputs))
    history.append(ShrinkAttempt(1, len(original.inputs), ShrinkOperation("validate_original"), "retained"))

    def attempt(candidate, operation):
        nonlocal attempts, termination, minimized
        if attempts == config.max_attempts:
            termination = "attempt_limit"
            return False, False
        attempts += 1
        outcome, failure = replay_candidate(candidate)
        retained = False
        if outcome in ("cancelled", "deadline", "transition_limit"):
            termination = outcome
            history.append(ShrinkAttempt(attempts, len(candidate), operation, "resource_limit"))
            return False, False
        if outcome == "failure":
            if not has_target(failure):
                outcome = "different_failure"
            else:
                was_seen = known(failure.inputs)
                stop = control.stop()
                if stop:
                    termination = stop
                    history.append(ShrinkAttempt(attempts, len(candidate), operation, "resource_limit"))
                    return False, False
                if was_seen:
                    outcome = "already_seen"
                else:
                    outcome, retained, minimized = "retained", True, failure
                    seen.append(_copy(failure.inputs))
        history.append(ShrinkAttempt(attempts, len(candidate), operation, outcome))
        return True, retained

    def close_simplifier(value, primary=False):
        try:
            closer = _call("simpler_inputs close", getattr, value, "close", None)
            if closer is not None:
                _call("simpler_inputs close", closer)
        except BaseException:
            if not primary:
                raise

    while True:
        groups = 2
        while minimized.inputs:
            length = len(minimized.inputs)
            chunk = (length + groups - 1) // groups
            accepted = False
            for start in range(0, length, chunk):
                stop = control.stop()
                if stop:
                    termination = stop
                    return result()
                end = min(start + chunk, length)
                candidate = minimized.inputs[:start] + minimized.inputs[end:]
                was_seen = known(candidate)
                stop = control.stop()
                if stop:
                    termination = stop
                    return result()
                if was_seen:
                    continue
                proceed, accepted = attempt(candidate, ShrinkOperation("remove", start=start, end=end))
                if not proceed:
                    return result()
                if accepted:
                    groups = max(groups - 1, 2)
                    break
            if accepted:
                continue
            if groups >= length:
                break
            groups = min(groups * 2, length)
        simplified = False
        for index in range(len(minimized.inputs)):
            stop = control.stop()
            if stop:
                termination = stop
                return result()
            callback = getattr(model, "simpler_inputs", lambda input: ())
            domain = _call("simpler_inputs", callback, _copy(minimized.inputs[index]))
            try:
                stop = control.stop()
            except BaseException:
                close_simplifier(domain, primary=True)
                raise
            if stop:
                termination = stop
                close_simplifier(domain)
                return result()
            try:
                alternatives = _call("simpler_inputs", iter, domain)
            except BaseException:
                close_simplifier(domain, primary=True)
                raise
            alternative = 0
            # Duplicate hints create no replay attempt or history entry, but a
            # lazy iterator can yield them forever. Bound consecutive skips per
            # iterator (reset after every replayed hint), mirroring Rust
            # shrink_ops, so finite runs are unaffected.
            duplicate_hints = 0
            sentinel = object()
            exhausted = False
            try:
                while True:
                    stop = control.stop()
                    if stop:
                        termination = stop
                        return result()
                    input = _call("simpler_inputs next", next, alternatives, sentinel)
                    stop = control.stop()
                    if stop:
                        termination = stop
                        return result()
                    if input is sentinel:
                        exhausted = True
                        break
                    candidate = list(_copy(minimized.inputs))
                    candidate[index] = _copy(input)
                    was_seen = known(candidate)
                    stop = control.stop()
                    if stop:
                        termination = stop
                        return result()
                    operation = ShrinkOperation("simplify", index=index, alternative=alternative)
                    alternative += 1
                    if was_seen:
                        duplicate_hints += 1
                        if duplicate_hints > config.max_attempts:
                            termination = "attempt_limit"
                            return result()
                        continue
                    duplicate_hints = 0
                    proceed, simplified = attempt(tuple(candidate), operation)
                    if not proceed:
                        return result()
                    if simplified:
                        break
            finally:
                if not exhausted:
                    # Release caller-retained generators on early exit, without
                    # replacing an exception already raised by the search.
                    close_simplifier(alternatives, primary=sys.exc_info()[0] is not None)
            if simplified:
                break
        if not simplified:
            return result()
