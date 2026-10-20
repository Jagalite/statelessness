"""Native deterministic checking and finite exploration; no FFI or runtime deps.

Applications own states, transitions, effects, checks, and codecs. Callbacks must
be deterministic and side-effect free. The engine describes, never executes, I/O.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Iterable, Protocol, TypeVar

S = TypeVar("S")
I = TypeVar("I")
O = TypeVar("O")
T = TypeVar("T")
U64_MAX = (1 << 64) - 1


def _saturating_add_u64(left: int, right: int) -> int:
    """Counters match Rust u64::saturating_add rather than growing unbounded."""
    return min(U64_MAX, left + right)


class ModelError(Exception):
    """A callback/codec failure, not an application property violation."""


def _natural(value: int, name: str, minimum: int = 0, maximum: int = U64_MAX) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _call(stage: str, callback: Callable[..., T], *args: Any) -> T:
    try:
        return callback(*args)
    except Exception as error:
        raise ModelError(f"{stage}: {error}") from error


def _copy(value: T) -> T:
    return _call("snapshot", deepcopy, value)


@dataclass(frozen=True)
class Check:
    id: str
    status: str = "passed"
    details: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not isinstance(self.details, str):
            raise ValueError("check ID and details must be strings")
        if self.status not in ("passed", "failed", "skipped"):
            raise ValueError("unknown check status")
        if self.status == "passed" and self.details:
            raise ValueError("passing checks have no details")


@dataclass(frozen=True)
class Disposition:
    kind: str = "accepted"
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("accepted", "rejected", "ignored"):
            raise ValueError("unknown disposition")
        if not isinstance(self.reason, str) or (self.kind == "accepted" and self.reason):
            raise ValueError("invalid disposition reason")


@dataclass(frozen=True)
class Transition(Generic[S, O]):
    state: S
    outputs: tuple[O, ...] = ()
    disposition: Disposition = field(default_factory=Disposition)

    def __post_init__(self) -> None:
        object.__setattr__(self, "outputs", tuple(self.outputs))
        if not isinstance(self.disposition, Disposition):
            raise ValueError("transition needs a Disposition")


@dataclass(frozen=True)
class Metadata:
    name: str
    model_version: int = 1
    properties_version: int = 1
    codec_version: int = 1
    build: str = "unqualified"

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not isinstance(self.build, str):
            raise ValueError("model name and build must be strings")
        for name in ("model_version", "properties_version", "codec_version"):
            _natural(getattr(self, name), name, maximum=(1 << 32) - 1)


class Model(Protocol[S, I, O]):
    """Minimal model. Optional check_transition adds edge properties.

    Optional estimated_state_bytes(state) returns an unsigned 64-bit logical
    retained-state estimate or None. It does not measure process RSS.
    """
    def initial_state(self) -> S: ...
    def step(self, state: S, input: I) -> Transition[S, O]: ...
    def check_state(self, state: S) -> Iterable[Check]: ...


class EnumerateModel(Model[S, I, O], Protocol[S, I, O]):
    """Finite input domain, in stable order; duplicates remain observable edges."""
    def inputs(self, state: S) -> Iterable[I]: ...


class ModelCodec(Model[S, I, O], Protocol[S, I, O]):
    def metadata(self) -> Metadata: ...
    def encode_state(self, state: S) -> bytes: ...
    def decode_state(self, data: bytes) -> S: ...
    def encode_input(self, input: I) -> bytes: ...
    def decode_input(self, data: bytes) -> I: ...
    def encode_output(self, output: O) -> bytes: ...


@dataclass(frozen=True)
class CheckPolicy:
    state_every: int = 1
    transition_checks: bool = True

    def __post_init__(self) -> None:
        _natural(self.state_every, "state_every", 1)
        if type(self.transition_checks) is not bool:
            raise ValueError("transition_checks must be boolean")


def _checks(stage: str, callback: Callable[..., Iterable[Check]], *args: Any) -> tuple[Check, ...]:
    def collect() -> tuple[Check, ...]:
        result = tuple(callback(*args))
        if any(not isinstance(check, Check) for check in result):
            raise TypeError("checker must return Check values")
        return result
    return _call(stage, collect)


def _state_checks(model: Model[S, I, O], state: S) -> tuple[Check, ...]:
    return _checks("state check", model.check_state, _copy(state))


def _transition_checks(model: Model[S, I, O], before: S, input: I,
                       transition: Transition[S, O]) -> tuple[Check, ...]:
    callback = getattr(model, "check_transition", None)
    if callback is None:
        return ()
    return _checks("transition check", callback, _copy(before), _copy(input), _copy(transition))


def _step(model: Model[S, I, O], before: S, input: I) -> Transition[S, O]:
    transition = _call("transition", model.step, _copy(before), _copy(input))
    if not isinstance(transition, Transition):
        raise ModelError("transition: model must return Transition")
    return _copy(transition)


def check_observed(model: Model[S, I, O], before: S, input: I,
                   transition: Transition[S, O], sequence: int,
                   policy: CheckPolicy = CheckPolicy()) -> tuple[Check, ...]:
    """Check a completed transition without calling step. Sequence is one-based.

    Initial checking is separate. Callback errors raise without returning partial
    check batches. Defensive copies keep checker mutation away from caller state.
    """
    _natural(sequence, "sequence", 1)
    if not isinstance(transition, Transition):
        raise ValueError("transition must be a Transition")
    state_checks = (_state_checks(model, transition.state)
                    if sequence % policy.state_every == 0 else
                    (Check("stateless.state_checks", "skipped", "periodic checking policy"),))
    transition_checks = (_transition_checks(model, before, input, transition)
                         if policy.transition_checks else
                         (Check("stateless.transition_checks", "skipped", "disabled by policy"),))
    return state_checks + transition_checks


def _failed(checks: Iterable[Check]) -> bool:
    return any(check.status == "failed" for check in checks)


def _encode(stage: str, callback: Callable[[Any], bytes], value: Any) -> bytes:
    data = _call(stage, callback, _copy(value))
    if type(data) is not bytes:
        raise ModelError(f"{stage}: codec must return bytes")
    return data


@dataclass(frozen=True)
class TraceStep:
    input: bytes
    disposition: Disposition
    outputs: tuple[bytes, ...]
    post_state: bytes
    checks: tuple[Check, ...]


@dataclass(frozen=True)
class Trace:
    metadata: Metadata
    initial_state: bytes
    initial_checks: tuple[Check, ...]
    steps: tuple[TraceStep, ...]
    termination: str
    error: str = ""


@dataclass(frozen=True)
class TraceLimits:
    """Python payload/JSON limits; not Rust binary framing limits or process RSS.

    Callbacks may allocate before returning. max_payload_bytes counts encoded
    state/input/output blobs retained by record, not Python object overhead.
    """
    max_steps: int = 100_000
    max_blob_bytes: int = 4 * 1024 * 1024
    max_items: int = 250_000
    max_payload_bytes: int = 32 * 1024 * 1024
    max_json_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            _natural(getattr(self, name), name)


def _blob_limit(data: bytes, limits: TraceLimits) -> bytes:
    if len(data) > limits.max_blob_bytes:
        raise ModelError("encoded blob exceeds byte limit")
    return data


def record(model: ModelCodec[S, I, O], inputs: Iterable[I], max_steps: int = 100_000,
           *, limits: TraceLimits = TraceLimits()) -> Trace:
    """Record a checked prefix, stopping at the first failing transition.

    Errors before the initial checkpoint raise. Later callback/codec/limit errors
    retain only the coherent prefix with model_error termination. Replaying such
    a prefix does not validate its unrecorded terminal error.
    """
    _natural(max_steps, "max_steps")
    state = _copy(_call("initial state", model.initial_state))
    initial_checks = _checks("initial check", model.check_state, _copy(state))
    initial_state = _blob_limit(_encode("encode initial state", model.encode_state, state), limits)
    metadata = _call("metadata", model.metadata)
    if not isinstance(metadata, Metadata):
        raise ModelError("metadata: model must return Metadata")
    items, payload_bytes = len(initial_checks), len(initial_state)
    if items > limits.max_items or payload_bytes > limits.max_payload_bytes:
        raise ModelError("recording limit: initial checkpoint")
    steps: list[TraceStep] = []
    termination, error = "completed", ""
    if _failed(initial_checks):
        return Trace(metadata, initial_state, initial_checks, (), "property_failed")
    iterator = _call("inputs", iter, inputs)
    while True:
        try:
            try:
                input = next(iterator)
            except StopIteration:
                break
            except Exception as cause:
                raise ModelError(f"inputs: {cause}") from cause
            if len(steps) >= min(max_steps, limits.max_steps):
                termination = "step_limit"
                break
            encoded_input = _blob_limit(_encode("encode input", model.encode_input, input), limits)
            transition = _step(model, state, input)
            checks = check_observed(model, state, input, transition, len(steps) + 1)
            next_items = items + 1 + len(checks) + len(transition.outputs)
            if next_items > limits.max_items:
                raise ModelError("recording limit: aggregate items")
            outputs = tuple(_blob_limit(_encode("encode output", model.encode_output, x), limits)
                            for x in transition.outputs)
            post_state = _blob_limit(_encode("encode state", model.encode_state, transition.state), limits)
            next_payload = payload_bytes + len(encoded_input) + len(post_state) + sum(map(len, outputs))
            if next_payload > limits.max_payload_bytes:
                raise ModelError("recording limit: aggregate payload bytes")
            step = TraceStep(encoded_input, transition.disposition, outputs, post_state, checks)
        except ModelError as cause:
            termination, error = "model_error", str(cause)
            break
        steps.append(step)
        state, items, payload_bytes = transition.state, next_items, next_payload
        if _failed(checks):
            termination = "property_failed"
            break
    return Trace(metadata, initial_state, initial_checks, tuple(steps), termination, error)


@dataclass(frozen=True)
class ReplayReport:
    outcome: str
    steps_verified: int = 0
    failure_reproduced: bool = False
    build_matches: bool = True
    step: int | None = None
    field: str | None = None


def _same_failure(expected: Iterable[Check], actual: Iterable[Check]) -> bool:
    return bool({c.id for c in expected if c.status == "failed"} &
                {c.id for c in actual if c.status == "failed"})


def validate_recording(trace: Trace) -> None:
    if trace.termination not in ("completed", "property_failed", "step_limit", "interrupted", "model_error"):
        raise ModelError("unknown trace termination")
    if trace.error and trace.termination != "model_error":
        raise ModelError("error text requires model_error termination")
    initial_failed = _failed(trace.initial_checks)
    if initial_failed and trace.steps:
        raise ModelError("trace continues after initial property failure")
    if any(_failed(step.checks) for step in trace.steps[:-1]):
        raise ModelError("trace continues after property failure")
    failed = initial_failed or bool(trace.steps and _failed(trace.steps[-1].checks))
    if failed != (trace.termination == "property_failed"):
        raise ModelError("trace termination disagrees with recorded checks")


def replay(model: ModelCodec[S, I, O], trace: Trace, *, allow_build_mismatch: bool = False) -> ReplayReport:
    """Restore the saved checkpoint; never call initial_state to reconstruct it.

    Exact verifies recorded observations, not graph exhaustion or an error after
    the recorded prefix. Divergent step numbers are one-based; None is initial.
    """
    if type(allow_build_mismatch) is not bool:
        raise ValueError("allow_build_mismatch must be boolean")
    metadata = _call("metadata", model.metadata)
    if not isinstance(metadata, Metadata):
        raise ModelError("metadata: model must return Metadata")
    build_matches = metadata.build == trace.metadata.build
    identities = ("name", "model_version", "properties_version", "codec_version")
    if any(getattr(metadata, key) != getattr(trace.metadata, key) for key in identities) or (
            not build_matches and not allow_build_mismatch):
        return ReplayReport("incompatible", build_matches=build_matches)
    validate_recording(trace)
    state = _call("decode initial state", model.decode_state, trace.initial_state)
    if _encode("encode initial state", model.encode_state, state) != trace.initial_state:
        raise ModelError("initial state encoding is not canonical")
    checks = _checks("initial check", model.check_state, _copy(state))
    reproduced = _same_failure(trace.initial_checks, checks)
    if checks != trace.initial_checks:
        return ReplayReport("diverged", 0, reproduced, build_matches, None, "initial checks")
    for index, expected in enumerate(trace.steps, 1):
        input = _call("decode input", model.decode_input, expected.input)
        if _encode("encode input", model.encode_input, input) != expected.input:
            raise ModelError(f"input {index} encoding is not canonical")
        actual = _step(model, state, input)
        checks = check_observed(model, state, input, actual, index)
        reproduced |= _same_failure(expected.checks, checks)
        outputs = tuple(_encode("encode output", model.encode_output, x) for x in actual.outputs)
        post_state = _encode("encode state", model.encode_state, actual.state)
        comparisons = (("disposition", actual.disposition, expected.disposition),
                       ("outputs", outputs, expected.outputs),
                       ("state", post_state, expected.post_state),
                       ("checks", checks, expected.checks))
        for name, left, right in comparisons:
            if left != right:
                return ReplayReport("diverged", index - 1, reproduced, build_matches, index, name)
        state = actual.state
    return ReplayReport("exact", len(trace.steps), reproduced, build_matches)


@dataclass(frozen=True)
class SearchConfig:
    max_states: int = 100_000
    max_transitions: int = 1_000_000
    max_depth: int = 100

    def __post_init__(self) -> None:
        _natural(self.max_states, "max_states")
        if self.max_states == 0:
            raise ValueError("enumeration max_states must be greater than zero")
        _natural(self.max_transitions, "max_transitions")
        _natural(self.max_depth, "max_depth")


@dataclass(frozen=True)
class Violation:
    phase: str
    check: Check


@dataclass(frozen=True)
class Failure(Generic[I]):
    inputs: tuple[I, ...]
    violations: tuple[Violation, ...]


@dataclass(frozen=True)
class SearchReport(Generic[I]):
    termination: str
    states: int
    transitions: int
    max_depth_reached: int
    skipped_checks: int
    failure: Failure[I] | None = None
    estimated_retained_state_bytes: int | None = None


def _hash(state: Any) -> int | None:
    try:
        return hash(state)
    except TypeError:
        # Native dict/list states use an equality bucket. Eq still decides identity.
        return None


def enumerate_states(model: EnumerateModel[S, I, O], config: SearchConfig = SearchConfig(),
                     *, limits=None, observer=None) -> SearchReport[I]:
    """FIFO search with exact equality, cooperative controls and admission caps.

    Optional SearchLimits bounds summed logical state estimates, excluding
    temporary successors, predecessor inputs, map/frontier storage and callback
    allocation. None reports an unknown/overflowed total. Observer histories are
    real root prefixes when needs_history() returns True. Snapshots are detached.
    """
    # Local import preserves the existing core/search public module split.
    from .search import SearchLimits, _Control, _estimate, _violations
    limits = SearchLimits() if limits is None else limits
    if not isinstance(limits, SearchLimits):
        raise ValueError("limits must be SearchLimits")
    control = _Control(limits.control)
    states = transitions = max_depth = skipped = 0
    estimated = None
    nodes = []
    cutoff = False

    def report(reason, failure=None):
        return SearchReport(reason, states, transitions, max_depth, skipped,
                            failure, estimated)

    def stopped():
        reason = control.stop()
        return None if reason is None else report(reason)

    def path(index, input):
        inputs = [_copy(input)]
        while nodes[index][1] is not None:
            inputs.append(_copy(nodes[index][2]))
            index = nodes[index][1]
        inputs.reverse()
        return tuple(inputs)

    stop = stopped()
    if stop is not None:
        return stop
    state = _copy(_call("initial_state", model.initial_state))
    states = 1
    violations, skipped = _violations(model, state, observer=observer)
    if violations:
        return report("failure_found", Failure((), violations))
    stop = stopped()
    if stop is not None:
        return stop
    estimated = _estimate(model, state)
    stop = stopped()
    if stop is not None:
        return stop
    if limits.max_estimated_state_bytes is not None:
        if estimated is None:
            raise ModelError("state byte budget requires estimated_state_bytes")
        if estimated > limits.max_estimated_state_bytes:
            states, estimated = 0, 0
            return report("estimated_state_byte_limit")
    hashed = _call("state hash", _hash, _copy(state))
    stop = stopped()
    if stop is not None:
        return stop
    nodes.append((state, None, None, 0))
    visited = {hashed: [0]}
    queue = deque([0])
    sentinel = object()
    while queue:
        index = queue.popleft()
        stop = stopped()
        if stop is not None:
            return stop
        state, _, _, depth = nodes[index]
        domain = _call("enumerate inputs", model.inputs, _copy(state))
        stop = stopped()
        if stop is not None:
            return stop
        iterator = _call("enumerate inputs", iter, domain)
        stop = stopped()
        if stop is not None:
            return stop
        if depth == config.max_depth:
            unexamined = _call("enumerate inputs", next, iterator, sentinel) is not sentinel
            stop = stopped()
            if stop is not None:
                return stop
            cutoff |= unexamined
            continue
        while True:
            stop = stopped()
            if stop is not None:
                return stop
            input = _call("enumerate inputs", next, iterator, sentinel)
            stop = stopped()
            if stop is not None:
                return stop
            if input is sentinel:
                break
            if transitions == config.max_transitions:
                return report("transition_limit")
            transition = _step(model, state, input)
            transitions += 1
            max_depth = max(max_depth, depth + 1)
            wants_history = getattr(observer, "needs_history", None) if observer is not None else None
            history_requested = False if wants_history is None else _call("search observer needs_history", wants_history)
            if type(history_requested) is not bool:
                raise ModelError("search observer needs_history must return bool")
            history = path(index, input) if history_requested else ()
            violations, count = _violations(model, state, input, transition,
                                            observer=observer, history=history)
            skipped = _saturating_add_u64(skipped, count)
            if violations:
                return report("failure_found", Failure(path(index, input), violations))
            stop = stopped()
            if stop is not None:
                return stop
            hashed = _call("state hash", _hash, _copy(transition.state))
            stop = stopped()
            if stop is not None:
                return stop
            # Latest collision first, matching the Rust collision-chain order.
            bucket = (range(len(nodes) - 1, -1, -1) if hashed is None else
                      sorted(visited.get(hashed, []) + visited.get(None, []), reverse=True))
            duplicate = False
            for other in bucket:
                stop = stopped()
                if stop is not None:
                    return stop
                duplicate = _call("state equality", lambda left, right: bool(left == right),
                                  _copy(nodes[other][0]), _copy(transition.state))
                stop = stopped()
                if stop is not None:
                    return stop
                if duplicate:
                    break
            if duplicate:
                continue
            if len(nodes) == config.max_states:
                return report("state_limit")
            estimate = _estimate(model, transition.state)
            stop = stopped()
            if stop is not None:
                return stop
            total = None if estimated is None or estimate is None else estimated + estimate
            if total is not None and total > U64_MAX:
                total = None
            if limits.max_estimated_state_bytes is not None:
                if estimate is None:
                    raise ModelError("state byte budget requires estimated_state_bytes")
                if total is None or total > limits.max_estimated_state_bytes:
                    return report("estimated_state_byte_limit")
            successor = len(nodes)
            visited.setdefault(hashed, []).append(successor)
            nodes.append((_copy(transition.state), index, _copy(input), depth + 1))
            queue.append(successor)
            states, estimated = len(nodes), total
    return report("depth_bound" if cutoff else "graph_exhausted")


class Rng:
    """SplitMix64 and rejection-sampled bounded choice; not cryptographic."""
    def __init__(self, seed: int):
        self.state = _natural(seed, "seed")

    def next_u64(self) -> int:
        self.state = (self.state + 0x9E3779B97F4A7C15) & U64_MAX
        z = self.state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & U64_MAX
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & U64_MAX
        return z ^ (z >> 31)

    def index(self, upper: int) -> int | None:
        _natural(upper, "upper")
        if upper == 0:
            return None
        threshold = ((-upper) & U64_MAX) % upper
        while True:
            value = self.next_u64()
            if value >= threshold:
                return value % upper
