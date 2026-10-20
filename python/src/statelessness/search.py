"""Candidate native generation, seeded fuzzing and causal shrinking.

Limits are cooperative: callbacks and iterator pulls cannot be preempted.
EXHAUSTED denotes an exhausted domain; every other value, including None, is input.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Callable, Generic, Iterable, Protocol

from .core import (S, I, O, Check, Transition, Model, EnumerateModel, ModelError, Rng, Failure,
                   Violation, _call, _copy, _natural, _step, _saturating_add_u64, _dispatch_checks)


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
        return self._generate(state, rng)

    def is_enabled(self, state, input):
        return True if self._enabled is None else self._enabled(state, input)

    def simpler_inputs(self, input):
        return () if self._simpler is None else self._simpler(input)


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
    """
    def __init__(self, model: EnumerateModel[S, I, O], options: AutoOptions = AutoOptions()):
        self.model, self.options = model, options

    def __getattr__(self, name):
        return getattr(self.model, name)

    def _inputs(self, state):
        domain = _call("automatic inputs", self.model.inputs, _copy(state))
        return _call("automatic inputs", iter, domain)

    def generate(self, state, rng):
        working = Rng(rng.state)
        selected = EXHAUSTED
        sentinel = object()
        iterator = self._inputs(state)
        count = 0
        while True:
            candidate = _call("automatic inputs next", next, iterator, sentinel)
            if candidate is sentinel:
                rng.state = working.state
                return selected
            count += 1
            if count > self.options.max_candidates:
                raise ModelError("automatic input domain exceeds max_candidates")
            if count == 1 or working.index(count) == 0:
                selected = _copy(candidate)

    def is_enabled(self, state, input):
        sentinel = object()
        iterator = self._inputs(state)
        count = 0
        while True:
            candidate = _call("automatic inputs next", next, iterator, sentinel)
            if candidate is sentinel:
                return False
            count += 1
            if count > self.options.max_candidates:
                raise ModelError("automatic input domain exceeds max_candidates")
            if _equal(candidate, input):
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
    value = True if callback is None else _call("is_enabled", callback, _copy(state), _copy(input))
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


def _violations(model, state, input=None, transition=None, *, observer=None, history=(), storage=None):
    values = [] if storage is None else storage
    values.clear()
    initial = transition is None
    phase = "initial_state" if initial else "state"
    try:
        _dispatch_checks(model, "check_state_into", "check_state",
                         (_copy(state if initial else transition.state),), values,
                         "initial check" if initial else "state check")
    except ModelError as error:
        if observer is not None:
            _notify_check_error(observer, phase, tuple(values), False, error)
        raise
    boundary = len(values)
    if initial:
        if observer is not None:
            _notify(observer, "initial", state, tuple(values))
    else:
        try:
            _dispatch_checks(model, "check_transition_into", "check_transition",
                             (_copy(state), _copy(input), _copy(transition)), values,
                             "transition check", optional=True)
        except ModelError as error:
            if observer is not None:
                _notify_check_error(observer, "state", tuple(values[:boundary]), True, error)
                _notify_check_error(observer, "transition", tuple(values[boundary:]), False, error)
            raise
        if observer is not None:
            _notify(observer, "transition", state, input, transition,
                    tuple(values[:boundary]), tuple(values[boundary:]), tuple(history))
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
        _natural(self.mutation_percent, "mutation_percent", maximum=100)


@dataclass(frozen=True)
class FuzzReport(Generic[I]):
    termination: str
    seed: int
    cases: int
    transitions: int
    skipped_checks: int
    failure: Failure[I] | None = None


def fuzz(model: GenerateModel[S, I, O], config: FuzzConfig = FuzzConfig(),
         *, limits: RunLimits = RunLimits(),
         observer: SearchObserver[S, I, O] | None = None) -> FuzzReport[I]:
    """Seeded state-aware search; completion proves no graph exhaustion."""
    control, rng = _Control(limits), Rng(config.seed)
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
        violations, count = _violations(model, state, observer=observer, storage=check_storage)
        skipped = _saturating_add_u64(skipped, count)
        if violations:
            return report("failure_found", Failure((), violations))
        stop = control.stop()
        if stop:
            return report(stop)
        mutated, replace_at = (), None
        if previous and rng.index(100) < config.mutation_percent:
            mutated = list(_copy(previous))
            start = rng.index(len(mutated))
            length = 1 + rng.index(len(mutated) - start)
            del mutated[start:start + length]
            replace_at = rng.index(len(mutated) + 1)
        inputs = []
        for step in range(config.max_steps):
            if transitions == config.max_transitions:
                return report("transition_limit")
            stop = control.stop()
            if stop:
                return report(stop)
            reused = step != replace_at and step < len(mutated) and _enabled(model, state, mutated[step])
            stop = control.stop()
            if stop:
                return report(stop)
            input = (_copy(mutated[step]) if reused else
                     _copy(_call("generate", model.generate, _copy(state), rng)))
            stop = control.stop()
            if stop:
                return report(stop)
            if input is EXHAUSTED:
                break
            enabled = reused or _enabled(model, state, input)
            stop = control.stop()
            if stop:
                return report(stop)
            if not enabled:
                raise ModelError("generate returned a causally disabled input")
            transition = _step(model, state, input)
            transitions += 1
            inputs.append(_copy(input))
            violations, count = _violations(model, state, input, transition, observer=observer, history=inputs, storage=check_storage)
            skipped = _saturating_add_u64(skipped, count)
            if violations:
                return report("failure_found", Failure(tuple(inputs), violations))
            stop = control.stop()
            if stop:
                return report(stop)
            state = transition.state
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
    """Replay-preserving deletion/simplification; no global minimality claim."""
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
        state = _copy(_call("shrink initial_state", model.initial_state))
        violations, _ = _violations(model, state, storage=check_storage)
        if violations:
            return "failure", Failure((), violations)
        stop = control.stop()
        if stop:
            return stop, None
        for index, input in enumerate(inputs):
            stop = control.stop()
            if stop:
                return stop, None
            if limits.max_replayed_transitions is not None and transitions >= limits.max_replayed_transitions:
                return "transition_limit", None
            enabled = _enabled(model, state, input)
            stop = control.stop()
            if stop:
                return stop, None
            if not enabled:
                return "invalid_causality", None
            transition = _step(model, state, input)
            transitions += 1
            violations, _ = _violations(model, state, input, transition, storage=check_storage)
            if violations:
                return "failure", Failure(_copy(tuple(inputs[:index + 1])), violations)
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
            stop = control.stop()
            if stop:
                termination = stop
                return result()
            alternatives = _call("simpler_inputs", iter, domain)
            alternative = 0
            sentinel = object()
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
                    continue
                proceed, simplified = attempt(tuple(candidate), operation)
                if not proceed:
                    return result()
                if simplified:
                    break
            if simplified:
                break
        if not simplified:
            return result()
