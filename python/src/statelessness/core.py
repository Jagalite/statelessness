"""Native deterministic checking and finite exploration; no FFI or runtime deps.

Applications own states, transitions, effects, checks, and codecs. Callbacks must
be deterministic and side-effect free. The engine describes, never executes, I/O.
"""
from __future__ import annotations

from collections import deque
import inspect
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


def _error_detail(error: Exception) -> str:
    try:
        return str(error)
    except Exception:
        return 'exception detail unavailable'


def _call(stage: str, callback: Callable[..., T], *args: Any) -> T:
    try:
        return callback(*args)
    except Exception as error:
        raise ModelError(f"{stage}: {_error_detail(error)}") from error


class _SnapshotFailure(ModelError):
    """Internal typed ownership failure; never inferred from application text."""


def _copy(value: T, memo=None) -> T:
    try:
        return deepcopy(value, memo)
    except Exception as error:
        raise _SnapshotFailure(f"snapshot: {_error_detail(error)}") from error


class _Captured:
    """An owned snapshot taken now, or the copy failure deferred until use.

    Single rule for deferred captures: `_copy` converts every `Exception` from a
    copy hook into a `ModelError`, so only `ModelError` is deferred here;
    `BaseException` (KeyboardInterrupt, SystemExit, ...) propagates immediately.
    """
    __slots__ = ("_value", "error")

    def __init__(self, value: Any, memo=None) -> None:
        try:
            self._value, self.error = _copy(value, memo), None
        except ModelError as error:
            self._value, self.error = None, error

    def get(self) -> Any:
        """Return the snapshot, or raise the deferred copy failure."""
        if self.error is not None:
            raise self.error
        return self._value


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


def _validated_check(value: Check) -> Check:
    if not isinstance(value, Check):
        raise ModelError("checker must emit Check values")
    fields = (value.id, value.status, value.details)
    if any(not isinstance(field, str) for field in fields):
        raise ModelError("check fields must be strings")
    # Intrinsic conversion strips str-subclass overrides while preserving the
    # actual immutable text; arbitrary equality objects cannot spoof status.
    return Check(*(str.__str__(field) for field in fields))


class _SinkBuffer:
    __slots__ = ("checks", "failure")
    def __init__(self, checks):
        self.checks, self.failure = checks, None


class _SinkLease:
    __slots__ = ("buffer", "parent", "active")
    def __init__(self, buffer, parent=None):
        self.buffer, self.parent, self.active = buffer, parent, True

    def require(self):
        if not self.active:
            raise ModelError("check sink lease has expired")
        if self.parent is not None:
            self.parent.require()
        if self.buffer.failure is not None:
            raise self.buffer.failure
        return self.buffer.checks

    def poison(self, error):
        if self.buffer.failure is None:
            # Poison before invoking application-controlled exception formatting:
            # __str__ may raise or re-enter an escaped sink.
            self.buffer.failure = error if isinstance(error, ModelError) else ModelError('exception detail unavailable')
            self.active = False
            if not isinstance(error, ModelError):
                self.buffer.failure = ModelError(_error_detail(error))
        self.active = False
        return self.buffer.failure


class CheckSink:
    """Synchronous append-only callback lease; snapshots are detached.

    No public mutable backing storage or capacity guarantee. Escaped callback
    views expire on return/error. Private implementation access is unsupported;
    this API is not a security sandbox and concurrent use is unsupported.
    """
    __slots__ = ("__lease",)

    def __init__(self, checks: list[Check]):
        if type(checks) is not list:
            raise ValueError("check sink storage must be a list")
        self.__lease = _SinkLease(_SinkBuffer(checks))

    @classmethod
    def _child(cls, parent):
        parent.__lease.require()
        child = object.__new__(cls)
        child.__lease = _SinkLease(parent.__lease.buffer, parent.__lease)
        return child

    def _revoke(self):
        self.__lease.active = False
        # An escaped expired view must not pin the reused buffer or later data.
        self.__lease.buffer = None
        self.__lease.parent = None

    def _failure(self):
        return self.__lease.buffer.failure

    def _poison(self, error):
        return self.__lease.poison(error)

    def append(self, check: Check) -> None:
        storage = self.__lease.require()
        try:
            value = _validated_check(check)
        except Exception as error:
            raise self.__lease.poison(error) from error
        storage.append(value)

    push = append

    def extend(self, checks: Iterable[Check]) -> None:
        self.__lease.require()
        try:
            for check in checks:
                self.append(check)
        except Exception as error:
            raise self.__lease.poison(error) from error

    def reserve(self, additional: int) -> None:
        self.__lease.require()
        try:
            _natural(additional, "additional")
        except Exception as error:
            raise self.__lease.poison(error) from error
        # Python lists expose no portable reserve/capacity guarantee.

    def snapshot(self) -> tuple[Check, ...]:
        return tuple(_validated_check(check) for check in self.__lease.require())

    def __len__(self) -> int:
        return len(self.__lease.require())

    def __iter__(self):
        return iter(self.snapshot())

    def __getitem__(self, index):
        return self.snapshot()[index]


def _dispatch_checks(model, append_name, legacy_name, args, destination, stage, *, optional=False):
    sink = CheckSink._child(destination) if isinstance(destination, CheckSink) else CheckSink(destination)
    try:
        def invoke():
            try:
                callback = getattr(model, append_name, None)
                if callback is not None:
                    result = callback(*args, sink)
                    if result is not None:
                        failure = sink._poison(ModelError("append checker must return None synchronously"))
                        if inspect.iscoroutine(result) or inspect.isgenerator(result):
                            result.close()
                        raise failure
                else:
                    legacy = getattr(model, legacy_name, None)
                    if legacy is None:
                        if optional:
                            return
                        raise ModelError("model needs " + legacy_name + " or " + append_name)
                    sink.extend(legacy(*args))
            except BaseException:
                if sink._failure() is not None:
                    raise sink._failure()
                raise
            if sink._failure() is not None:
                raise sink._failure()
        _call(stage, invoke)
    finally:
        sink._revoke()


def _checks(stage: str, callback: Callable[..., Iterable[Check]], *args: Any) -> tuple[Check, ...]:
    values = []
    sink = CheckSink(values)
    try:
        _call(stage, lambda: sink.extend(callback(*args)))
        return tuple(values)
    finally:
        sink._revoke()


def _state_checks(model: Model[S, I, O], state: S, *, stage="state check") -> tuple[Check, ...]:
    values = []
    _dispatch_checks(model, "check_state_into", "check_state", (_copy(state),), values, stage)
    return tuple(values)


def _transition_checks(model: Model[S, I, O], before: S, input: I,
                       transition: Transition[S, O]) -> tuple[Check, ...]:
    values = []
    _dispatch_checks(model, "check_transition_into", "check_transition",
                     (_copy(before), _copy(input), _copy(transition)), values,
                     "transition check", optional=True)
    return tuple(values)


def _step(model: Model[S, I, O], before: S, input: I, *, stage: str = "transition") -> Transition[S, O]:
    transition = _call(stage, model.step, _copy(before), _copy(input))
    if not isinstance(transition, Transition):
        raise ModelError("transition: model must return Transition")
    return _copy(transition)


def _check_observed_count_into(model: Model[S, I, O], before: S, input: I,
                        transition: Transition[S, O], sequence: int,
                        checks: list[Check], policy: CheckPolicy = CheckPolicy()) -> None:
    """Check an executed turn into reused caller storage, clearing on all errors.

    Checkers receive revocable append-only leases, never the backing list.
    Logical list identity is reused; Python capacity/allocation is not promised.
    """
    if type(checks) is not list:
        raise ValueError("checks must be a list")
    checks.clear()
    try:
        _natural(sequence, "sequence", 1)
        if not isinstance(transition, Transition):
            raise ValueError("transition must be a Transition")
        if not isinstance(policy, CheckPolicy):
            raise ValueError("policy must be CheckPolicy")
        # Validate and snapshot before invoking a checker, which may retain and
        # mutate the caller's policy despite its frozen dataclass declaration.
        state_every, transition_checks = policy.state_every, policy.transition_checks
        _natural(state_every, "state_every", 1)
        if type(transition_checks) is not bool:
            raise ValueError("transition_checks must be boolean")
        if sequence % state_every == 0:
            _dispatch_checks(model, "check_state_into", "check_state",
                             (_copy(transition.state),), checks, "state check")
        else:
            checks.append(Check("stateless.state_checks", "skipped", "periodic checking policy"))
        boundary = len(checks)
        if transition_checks:
            _dispatch_checks(model, "check_transition_into", "check_transition",
                             (_copy(before), _copy(input), _copy(transition)), checks,
                             "transition check", optional=True)
        else:
            checks.append(Check("stateless.transition_checks", "skipped", "disabled by policy"))
        return boundary
    except BaseException:
        checks.clear()
        raise


def check_observed_into(model, before, input, transition, sequence, checks, policy=CheckPolicy()):
    _check_observed_count_into(model, before, input, transition, sequence, checks, policy)


def check_observed(model: Model[S, I, O], before: S, input: I,
                   transition: Transition[S, O], sequence: int,
                   policy: CheckPolicy = CheckPolicy()) -> tuple[Check, ...]:
    """Check a completed transition without calling step. Sequence is one-based."""
    values = []
    check_observed_into(model, before, input, transition, sequence, values, policy)
    return tuple(values)


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
    return _record_kernel(model, inputs, max_steps, limits, continued=False)


def _record_kernel(model, inputs, max_steps, limits, *, continued):
    """`continued` records past property failures (continued-failure-evidence-v1)."""
    _natural(max_steps, "max_steps")
    if not isinstance(limits, TraceLimits): raise ValueError('limits must be TraceLimits')
    limits = TraceLimits(**{name: getattr(limits, name) for name in TraceLimits.__dataclass_fields__})
    from .codec import encode_value
    state = _copy(_call("initial state", model.initial_state))
    check_storage: list[Check] = []
    _dispatch_checks(model, "check_state_into", "check_state", (_copy(state),), check_storage, "initial check")
    initial_checks = tuple(check_storage)
    state_storage, input_storage, output_storage = bytearray(), bytearray(), []
    initial_state = _blob_limit(encode_value(model, "state", state, maximum=limits.max_blob_bytes, storage=state_storage, stage="encode initial state"), limits)
    metadata = _call("metadata", model.metadata)
    if not isinstance(metadata, Metadata):
        raise ModelError("metadata: model must return Metadata")
    items, payload_bytes = len(initial_checks), len(initial_state)
    if items > limits.max_items or payload_bytes > limits.max_payload_bytes:
        raise ModelError("recording limit: initial checkpoint")
    steps: list[TraceStep] = []
    termination, error = "completed", ""
    if _failed(initial_checks) and not continued:
        return Trace(metadata, initial_state, initial_checks, (), "property_failed")
    try:
        iterator = _call("inputs", iter, inputs)
    except ModelError as cause:
        return Trace(metadata, initial_state, initial_checks, (), "model_error", _error_detail(cause))
    while True:
        try:
            try:
                input = next(iterator)
            except StopIteration:
                break
            except Exception as cause:
                raise ModelError(f"inputs: {_error_detail(cause)}") from cause
            if len(steps) >= min(max_steps, limits.max_steps):
                termination = "step_limit"
                break
            encoded_input = _blob_limit(encode_value(model, "input", input, maximum=limits.max_blob_bytes, storage=input_storage, stage="encode input"), limits)
            transition = _step(model, state, input)
            check_observed_into(model, state, input, transition, len(steps) + 1, check_storage)
            checks = tuple(check_storage)
            next_items = items + 1 + len(checks) + len(transition.outputs)
            if next_items > limits.max_items:
                raise ModelError("recording limit: aggregate items")
            encoded_outputs = []
            for index, output in enumerate(transition.outputs):
                if index == len(output_storage): output_storage.append(bytearray())
                encoded_outputs.append(_blob_limit(encode_value(model, "output", output, maximum=limits.max_blob_bytes,
                    storage=output_storage[index], stage="encode output"), limits))
            outputs = tuple(encoded_outputs)
            post_state = _blob_limit(encode_value(model, "state", transition.state, maximum=limits.max_blob_bytes, storage=state_storage, stage="encode state"), limits)
            next_payload = payload_bytes + len(encoded_input) + len(post_state) + sum(map(len, outputs))
            if next_payload > limits.max_payload_bytes:
                raise ModelError("recording limit: aggregate payload bytes")
            step = TraceStep(encoded_input, transition.disposition, outputs, post_state, checks)
        except ModelError as cause:
            termination, error = "model_error", _error_detail(cause)
            break
        steps.append(step)
        state, items, payload_bytes = transition.state, next_items, next_payload
        if _failed(checks) and not continued:
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
    from .replay_evidence import replay_with_observations, ReplayError
    try:
        return replay_with_observations(model, trace, allow_build_mismatch=allow_build_mismatch)
    except ReplayError as error:
        raise error.error from error


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
    from .search import SearchLimits, RunLimits, _Control, _estimate, _violations
    limits = SearchLimits() if limits is None else limits
    if not isinstance(limits, SearchLimits):
        raise ValueError("limits must be SearchLimits")
    if not isinstance(config, SearchConfig):
        raise ValueError("config must be SearchConfig")
    config = SearchConfig(config.max_states, config.max_transitions, config.max_depth)
    limits = SearchLimits(RunLimits(limits.control.max_duration, limits.control.cancellation),
                          limits.max_estimated_state_bytes)
    control = _Control(limits.control)
    states = transitions = max_depth = skipped = 0
    check_storage = []
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
    # Keep the search node separate from application check/observer aliases.
    # Copy failures are deferred while an earlier violation or stop decides the run.
    try:
        initial_node, initial_error = _copy(state), None
    except _SnapshotFailure as error:
        initial_node, initial_error = None, error
    states = 1
    violations, skipped = _violations(model, state, observer=observer, storage=check_storage)
    if violations:
        return report("failure_found", Failure((), violations))
    stop = stopped()
    if stop is not None:
        return stop
    if initial_error is not None:
        raise initial_error
    state = initial_node
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
            # Preserve the enumerated value for checks and witnesses before the
            # reducer or its copy hooks can mutate an externally held alias.
            input = _copy(input)
            transition = _step(model, state, input)
            transitions += 1
            max_depth = max(max_depth, depth + 1)
            try:
                successor_state, successor_error = _copy(transition.state), None
            except _SnapshotFailure as error:
                successor_state, successor_error = None, error
            try:
                edge_observation, edge_error = _copy((state, input, transition)), None
            except _SnapshotFailure as error:
                edge_observation, edge_error = None, error
            wants_history = getattr(observer, "needs_history", None) if observer is not None else None
            history_requested = False if wants_history is None else _call("search observer needs_history", wants_history)
            if type(history_requested) is not bool:
                raise ModelError("search observer needs_history must return bool")
            history = path(index, input) if history_requested else ()
            edge_before, edge_input, edge_transition = (edge_observation if edge_error is None
                                                        else (state, input, transition))
            violations, count = _violations(model, edge_before, edge_input, edge_transition,
                                            observer=observer, history=history, storage=check_storage,
                                            edge_capture_error=edge_error,
                                            state_snapshot=(successor_state if successor_error is None
                                                            else edge_transition.state),
                                            state_snapshot_error=(successor_error if successor_error is not None
                                                                  and edge_error is not None else None))
            skipped = _saturating_add_u64(skipped, count)
            if violations:
                return report("failure_found", Failure(path(index, input), violations))
            stop = stopped()
            if stop is not None:
                return stop
            if successor_error is not None:
                raise successor_error
            hashed = _call("state hash", _hash, _copy(successor_state))
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
                                  _copy(nodes[other][0]), _copy(successor_state))
                stop = stopped()
                if stop is not None:
                    return stop
                if duplicate:
                    break
            if duplicate:
                continue
            if len(nodes) == config.max_states:
                return report("state_limit")
            estimate = _estimate(model, successor_state)
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
            nodes.append((_copy(successor_state), index, _copy(input), depth + 1))
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
