"""Feedback-guided sequence search with bounded feature and corpus counts.

Feature equality guides input selection only. It never changes model state,
checking or replay equality. Callbacks are synchronous; keys must have lawful,
stable hashing/equality and faithful deepcopy behavior. Counts are not RSS caps.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace
import inspect
from typing import Callable, Generic, Hashable, Protocol, TypeVar

from .core import I, O, S, ModelError, Transition, _natural
from .search import (FuzzConfig, FuzzReport, GenerateModel, RunLimits,
                     SearchObserver, _Control, _fuzz_with_driver)

K = TypeVar("K", bound=Hashable)
ALGORITHM = "feature-corpus-v2"


def _feedback_call(stage, callback, *args):
    try:
        return callback(*args)
    except Exception as error:
        # Formatting an application's exception can itself execute application
        # code. Preserve the original as cause even if that diagnostic throws.
        try:
            message = str(error)
        except BaseException as diagnostic:
            wrapped = ModelError(stage + ": callback message unavailable")
            wrapped.diagnostic_error = diagnostic
        else:
            wrapped = ModelError(stage + ": " + message)
        raise wrapped from error


def _snapshot(value):
    return _feedback_call("snapshot", deepcopy, value)


@dataclass(frozen=True)
class FeedbackMetadata:
    name: str
    version: int = 1
    build: str = "unqualified"

    def __post_init__(self):
        if not isinstance(self.name, str) or not isinstance(self.build, str):
            raise ValueError("feedback name and build must be strings")
        _natural(self.version, "version", maximum=(1 << 32) - 1)


def _invalid_return(value, message):
    """Reject lazy execution without running it; cleanup is always secondary."""
    error = ModelError(message)
    if inspect.iscoroutine(value) or inspect.isgenerator(value):
        try:
            value.close()
        except BaseException as cleanup:
            error.cleanup_error = cleanup
    raise error


def _sync_value(value, message):
    if inspect.isawaitable(value) or inspect.isgenerator(value) or inspect.isasyncgen(value):
        _invalid_return(value, message)
    return value


class FeatureBuffer(Generic[K]):
    """Bounded append-only feature sink with callback-scoped ownership.

    Each push counts, even duplicates, and captures a detached hashable key.
    Failed writes are sticky. Callback errors take precedence over sticky writes.
    An escaped callback sink expires on success, Exception or BaseException and
    releases its storage reference. Concurrent use and private access are outside
    this cooperative API. A standalone buffer owns its own private storage.
    """
    __slots__ = ("__values", "__maximum", "__failure", "__active", "__pushing")

    def __init__(self, maximum: int = 256):
        _natural(maximum, "maximum", 1)
        self.__values = []
        self.__maximum = maximum
        self.__failure = None
        self.__active = True
        self.__pushing = False

    @classmethod
    def _borrow(cls, values, maximum):
        out = cls(maximum)
        out.__values = values
        return out

    def _require(self):
        if not self.__active:
            raise ModelError("feature buffer lease has expired")
        if self.__failure is not None:
            raise self.__failure
        return self.__values

    def _finish(self):
        self._require()

    def _revoke(self):
        self.__active = False
        self.__values = None
        self.__failure = None

    def push(self, feature: K) -> None:
        values = self._require()
        if self.__pushing:
            self.__failure = ModelError("reentrant feature push is not supported")
            raise self.__failure
        if len(values) >= self.__maximum:
            self.__failure = ModelError("feedback exceeds per-observation feature limit")
            raise self.__failure
        self.__pushing = True
        try:
            value = _snapshot(feature)
            self._require()
            _feedback_call("feature hash", hash, value)
            # User copy/hash callbacks may catch a nested push failure. Check
            # sticky state and capacity again before committing the outer key.
            values = self._require()
            if len(values) >= self.__maximum:
                self.__failure = ModelError("feedback exceeds per-observation feature limit")
                raise self.__failure
            values.append(value)
        except Exception as error:
            if self.__failure is None:
                self.__failure = error
            raise
        finally:
            self.__pushing = False

    append = push


class Feedback(Protocol[S, I, O, K]):
    """Custom feature extraction. Omitting initial_features emits no initial keys.

    Hooks return None synchronously and receive independent owned snapshots.
    Metadata is called exactly once after admission, including pre-cancelled runs.
    """
    def metadata(self) -> FeedbackMetadata: ...

    def initial_features(self, state: S, out: FeatureBuffer[K]) -> None:
        pass

    def transition_features(self, before: S, input: I, after: Transition[S, O],
                            out: FeatureBuffer[K]) -> None:
        raise NotImplementedError("feedback needs transition_features")


class StateFeedback(Generic[S, K]):
    """Project initial and successor states without changing state equality."""
    def __init__(self, metadata: FeedbackMetadata, project: Callable[[S], K]):
        if not isinstance(metadata, FeedbackMetadata):
            raise ValueError("metadata must be FeedbackMetadata")
        if not callable(project):
            raise ValueError("project must be callable")
        self._metadata, self._project = _snapshot(metadata), project

    def metadata(self) -> FeedbackMetadata:
        return _snapshot(self._metadata)

    def initial_features(self, state: S, out: FeatureBuffer[K]) -> None:
        out.push(_sync_value(self._project(state), "state projection must return a key synchronously"))

    def transition_features(self, before: S, input: I, after: Transition[S, O],
                            out: FeatureBuffer[K]) -> None:
        self.initial_features(after.state, out)


def exact_state() -> StateFeedback:
    """Full cloned state novelty, including any represented oracle history.

    States must be hashable with lawful equality, like Rust's Hash requirement.
    Use StateFeedback for an application projection of an unhashable state.
    """
    return StateFeedback(FeedbackMetadata("exact-state", 1, "clone-eq-hash-v1"),
                         lambda state: state)


@dataclass(frozen=True)
class GuidanceConfig:
    max_corpus_entries: int = 256
    max_corpus_inputs: int = 25_600
    max_features: int = 100_000
    max_features_per_observation: int = 256

    def validate(self) -> None:
        for name in ("max_corpus_entries", "max_corpus_inputs", "max_features",
                     "max_features_per_observation"):
            _natural(getattr(self, name), name, 1)

    def __post_init__(self):
        self.validate()


@dataclass(frozen=True)
class CorpusEntry(Generic[I]):
    inputs: tuple[I, ...]
    new_features: int
    discovered_case: int
    discovered_transition: int


@dataclass(frozen=True)
class GuidanceReport(Generic[I]):
    algorithm: str
    config: GuidanceConfig
    feedback: FeedbackMetadata
    unique_features: int
    corpus_inputs: int
    corpus: tuple[CorpusEntry[I], ...]
    mutation_cases: int
    dropped_prefixes: int
    evicted_prefixes: int
    feature_limit_reached: bool
    corpus_limit_reached: bool


class _GuidedDriver:
    def __init__(self, feedback, guidance):
        self.feedback, self.config = feedback, guidance
        metadata = _sync_value(_feedback_call("feedback metadata", feedback.metadata),
                               "feedback metadata must return FeedbackMetadata synchronously")
        if not isinstance(metadata, FeedbackMetadata):
            raise ModelError("feedback metadata must return FeedbackMetadata synchronously")
        self.metadata = _snapshot(metadata)
        self.seen = {}
        self.scratch = []
        self.corpus = deque()
        self.corpus_inputs = self.mutation_cases = 0
        self.dropped_prefixes = self.evicted_prefixes = 0
        self.feature_limit_reached = self.corpus_limit_reached = False

    def report(self):
        return GuidanceReport(ALGORITHM, self.config, self.metadata, len(self.seen),
                              self.corpus_inputs, tuple(self.corpus), self.mutation_cases,
                              self.dropped_prefixes, self.evicted_prefixes,
                              self.feature_limit_reached, self.corpus_limit_reached)

    def _learn(self, inputs, case, transition):
        novel = 0
        recovered = False
        retainable = len(inputs) <= self.config.max_corpus_inputs
        for key in self.scratch:
            if key in self.seen:
                if retainable and not self.seen[key]:
                    self.seen[key] = True
                    recovered = True
            elif len(self.seen) == self.config.max_features:
                self.feature_limit_reached = True
            else:
                self.seen[key] = retainable
                novel += 1
        if (not novel and not recovered) or not inputs:
            return
        if not retainable:
            self.corpus_limit_reached = True
            self.dropped_prefixes += 1
            return
        allowance = self.config.max_corpus_inputs - len(inputs)
        while (len(self.corpus) >= self.config.max_corpus_entries or
               self.corpus_inputs > allowance):
            oldest = self.corpus.popleft()
            self.corpus_inputs -= len(oldest.inputs)
            self.evicted_prefixes += 1
            self.corpus_limit_reached = True
        self.corpus_inputs += len(inputs)
        self.corpus.append(CorpusEntry(_snapshot(tuple(inputs)), novel, case, transition))

    def _observe(self, stage, method, args, inputs, case, transition):
        self.scratch.clear()
        out = FeatureBuffer._borrow(self.scratch, self.config.max_features_per_observation)
        try:
            def invoke():
                try:
                    callback = getattr(self.feedback, method, None)
                    if callback is None:
                        if method != "initial_features":
                            raise ModelError("feedback needs transition_features")
                    else:
                        result = callback(*(_snapshot(arg) for arg in args), out)
                        if result is not None:
                            # Revoke before closing lazy results: their cleanup
                            # may try to write through a captured sink.
                            out._revoke()
                            _invalid_return(result, "feedback must return None synchronously")
                    # Callback exceptions bypass this sticky check, so they
                    # remain primary after a swallowed push failure.
                    out._finish()
                finally:
                    # Error formatting also executes application code. Expire
                    # the sink before the outer wrapper renders that error.
                    out._revoke()
                self._learn(inputs, case, transition)
            _feedback_call(stage, invoke)
        finally:
            out._revoke()
            self.scratch.clear()

    def initial(self, state, case):
        self._observe("initial feedback", "initial_features", (state,), (), case, 0)

    def transition(self, before, input, after, inputs, case, transition):
        self._observe("transition feedback", "transition_features", (before, input, after),
                      inputs, case, transition)

    def candidate(self, previous, rng, mutation_percent):
        if not self.corpus or rng.index(100) >= mutation_percent:
            return (), None
        self.mutation_cases += 1
        candidate = list(_snapshot(self.corpus[rng.index(len(self.corpus))].inputs))
        mode = rng.index(3)
        if mode == 0:
            return candidate, None
        if mode == 1:
            start = rng.index(len(candidate))
            length = 1 + rng.index(len(candidate) - start)
            del candidate[start:start + length]
            return candidate, rng.index(len(candidate) + 1)
        return candidate, rng.index(len(candidate))


def guided_fuzz(model: GenerateModel[S, I, O], feedback: Feedback[S, I, O, K],
                config: FuzzConfig = FuzzConfig(), guidance: GuidanceConfig = GuidanceConfig(),
                *, limits: RunLimits = RunLimits(),
                observer: SearchObserver[S, I, O] | None = None) -> FuzzReport[I]:
    """Explore with a fresh feature corpus; optional observation and controls.

    Property failures precede feedback. Callback errors produce no partial report.
    Metadata time counts against the cooperative deadline. Reports contain owned
    witness inputs, not resumable hidden feature state or exhaustive coverage.
    """
    if not isinstance(guidance, GuidanceConfig):
        raise ValueError("guidance must be GuidanceConfig")
    # Capture base, immutable scalar configurations before application callbacks.
    # Their ownership does not depend on caller aliases or deepcopy overrides.
    guidance = GuidanceConfig(guidance.max_corpus_entries, guidance.max_corpus_inputs,
                              guidance.max_features, guidance.max_features_per_observation)
    if not isinstance(config, FuzzConfig):
        raise ValueError("config must be FuzzConfig")
    config = FuzzConfig(config.seed, config.cases, config.max_steps,
                        config.max_transitions, config.mutation_percent)
    if not isinstance(limits, RunLimits):
        raise ValueError("limits must be RunLimits")
    limits = RunLimits(limits.max_duration, limits.cancellation)
    control = _Control(limits)
    driver = _GuidedDriver(feedback, guidance)
    result = _fuzz_with_driver(model, config, control, observer, driver)
    return replace(result, guidance=driver.report())
