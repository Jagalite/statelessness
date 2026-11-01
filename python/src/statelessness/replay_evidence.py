"""Candidate rich replay and exact binary-budget accounting, independent of FFI."""
from __future__ import annotations
from dataclasses import dataclass, replace
import inspect
from typing import Any
from .core import (Check, ModelError, Metadata, ReplayReport, Trace, TraceStep,
    Transition, U64_MAX, _call, _copy, _same_failure, _step, _validated_check,
    _check_observed_count_into, _dispatch_checks, validate_recording, _SnapshotFailure)
from .codec import encode_value
from .trace_binary import (AuditTrace, BinaryLimits, BinaryTraceError, RunConfig,
    _Encoder, _encode_run, _encode_step, _records, _limit, _variant, _text, _termination, U32_MAX)


@dataclass(frozen=True)
class ReplayDifferences:
    disposition: bool = False
    outputs: bool = False
    state: bool = False
    checks: bool = False
    def primary(self):
        return next((name for name in ("disposition", "outputs", "state", "checks") if getattr(self, name)), None)


@dataclass(frozen=True)
class ReplayActual:
    kind: str
    sequence: int = 0
    state: Any = None
    before: Any = None
    input: Any = None
    transition: Any = None
    checks: tuple[Check, ...] | None = None
    state_check_count: int | None = None


@dataclass(frozen=True)
class ReplayInitialObservation:
    state: Any
    checks: tuple[Check, ...]
    expected_state: bytes
    actual_state: bytes
    expected_checks: tuple[Check, ...]
    differences: ReplayDifferences
    sequence: int = 0
    kind: str = "initial"


@dataclass(frozen=True)
class ReplayTurnObservation:
    actual: ReplayActual
    expected: TraceStep
    actual_input: bytes
    actual_outputs: tuple[bytes, ...]
    actual_state: bytes
    differences: ReplayDifferences
    kind: str = "turn"


@dataclass(frozen=True)
class ReplayErrorObservation:
    sequence: int
    actual: ReplayActual | None
    error: ModelError
    kind: str = "error"
    actual_unavailable_reason: str | None = None


class ReplayError(ModelError):
    def __init__(self, error, report, actual=None, observer_error=None, actual_unavailable_reason=None):
        self.error, self.report, self.actual, self.observer_error = error, report, actual, observer_error
        self.actual_unavailable_reason = actual_unavailable_reason
        super().__init__(str(error))


def _audit_preflight(trace, limits):
    if not isinstance(trace, AuditTrace): raise ModelError("bounded replay requires lossless AuditTrace")
    _limit(len(trace.steps), limits.max_steps, "steps")
    items, total = [len(trace.steps)], 12
    _limit(items[0], limits.max_items, "aggregate items")
    for _, encode, args in _records(trace):
        encoder = _Encoder(limits, items)
        encode(encoder, *args)
        total += 9 + encoder.length
        _limit(total, limits.max_total_bytes, "total bytes")
    return total, items[0]


def _run_account(metadata, config, initial_state, checks, limits):
    encoder = _Encoder(limits, [0])
    value = AuditTrace(metadata, config, initial_state, checks, (), "completed")
    _encode_run(encoder, value)
    return encoder.length + 9, encoder.items[0]


class _EmptyBlobs:
    def __init__(self, count): self.count = count
    def __len__(self): return self.count
    def __iter__(self):
        for _ in range(self.count): yield b""


def _step_fixed(input, disposition, outputs, checks, limits):
    encoder = _Encoder(limits, [1])
    _limit(1, limits.max_items, "aggregate items")
    _encode_step(encoder, 0, TraceStep(input, disposition, _EmptyBlobs(outputs), b"", checks))
    return encoder.length, encoder.items[0]


def _disposition(value):
    kind = _variant(value.kind, ("accepted", "rejected", "ignored"), "disposition tag")
    reason = _text(value.reason, "disposition reason")
    if kind == "accepted" and str.__len__(reason): raise ModelError("accepted disposition reason")
    return kind, reason


def _same_disposition(left, right):
    lk, lr = _disposition(left)
    rk, rr = _disposition(right)
    return lk == rk and str.__eq__(lr, rr) is True


def replay_with_observations(model, trace: Trace, observer=None, *, allow_build_mismatch=False):
    return _replay(model, trace, observer, allow_build_mismatch, None)


def replay_with_observations_bounded(model, trace: AuditTrace, observer=None, *,
                                     limits=BinaryLimits(), allow_build_mismatch=False):
    # Capture caller-owned primitive fields before any model callback. Validation
    # remains after identity admission, so incompatible artifacts need no budget.
    try:
        captured = {name: getattr(limits, name) for name in BinaryLimits.__dataclass_fields__}
        capture_error = None
    except Exception as error:
        captured, capture_error = None, error
    return _replay(model, trace, observer, allow_build_mismatch, (captured, capture_error))


def _replay(model, trace, observer, allow_build_mismatch, limits, validate=validate_recording):
    if type(allow_build_mismatch) is not bool: raise ValueError("allow_build_mismatch must be boolean")
    report = ReplayReport("exact", build_matches=False)
    sequence, actual = 0, None
    check_storage, input_storage, state_storage = [], bytearray(), bytearray()
    output_storage = []

    def snapshot_failure(error):
        seen = set()
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            if isinstance(error, _SnapshotFailure):
                return True
            error = error.__cause__
        return False

    def observer_result(result):
        if result is not None:
            if inspect.iscoroutine(result) or inspect.isgenerator(result): result.close()
            raise ModelError("replay observer must return None synchronously")

    def notify_error(error, *, unavailable=False):
        if not isinstance(error, ModelError): error = ModelError(str(error))
        secondary, detached, reason = None, None, None
        if observer is not None:
            if unavailable or snapshot_failure(error):
                reason = "actual snapshot unavailable after snapshot failure"
            else:
                try: detached = _copy(actual)
                except ModelError as failure: reason = str(failure)
            try:
                result = _call("replay error observer", observer,
                    ReplayErrorObservation(sequence, detached, ModelError(str(error)), actual_unavailable_reason=reason), report)
                observer_result(result)
            except Exception as failure: secondary = failure
        return ReplayError(error, report, actual, secondary, reason)

    def deliver(observation):
        if observer is None: return
        try: detached = _copy(observation)
        except ModelError as error: raise notify_error(error, unavailable=True) from error
        try:
            result = _call("replay observer", observer, detached, report)
            observer_result(result)
        except Exception as error:
            raise ReplayError(error if isinstance(error, ModelError) else ModelError(str(error)), report, actual) from error

    try:
        metadata = _call("metadata", model.metadata)
        if not isinstance(metadata, Metadata): raise ModelError("metadata: model must return Metadata")
        build_matches = str.__eq__(_text(metadata.build, "metadata build"), _text(trace.metadata.build, "stored metadata build")) is True
        report = replace(report, build_matches=build_matches)
        identities = ("name", "model_version", "properties_version", "codec_version")
        if (str.__eq__(_text(metadata.name, "metadata name"), _text(trace.metadata.name, "stored metadata name")) is not True or
                any(getattr(metadata, key) != getattr(trace.metadata, key) for key in identities[1:]) or
                (not build_matches and not allow_build_mismatch)):
            return replace(report, outcome="incompatible")
        if limits is not None:
            try:
                captured, capture_error = limits
                if capture_error is not None: raise capture_error
                limits = BinaryLimits(**captured)
                _audit_preflight(trace, limits)
            except Exception as error: raise ModelError("bounded replay artifact: " + str(error)) from error
        if isinstance(trace, AuditTrace):
            portable, config = trace.to_portable()
        else:
            portable, config = trace, None
        # Validate runtime primitives before semantic comparisons.
        kind, reason = _termination(portable)
        portable = replace(portable, termination=kind, error=reason,
            initial_checks=tuple(_validated_check(c) for c in portable.initial_checks),
            steps=tuple(replace(s, checks=tuple(_validated_check(c) for c in s.checks)) for s in portable.steps))
        validate(portable)
        blob_limit = U64_MAX if limits is None else min(limits.max_blob_bytes, limits.max_frame_bytes, U32_MAX, limits.max_total_bytes)
        bounded_bytes = bounded_items = 0
        state = _call("decode initial state", model.decode_state, portable.initial_state)
        actual = ReplayActual("initial", state=state)
        state = _copy(state)
        actual = replace(actual, state=state)
        state_bytes = encode_value(model, "state", state, maximum=blob_limit, storage=state_storage, stage="encode initial state")
        if state_bytes != portable.initial_state: raise ModelError("initial state encoding is not canonical")
        _dispatch_checks(model, "check_state_into", "check_state", (_copy(state),), check_storage, "initial check")
        checks = tuple(check_storage)
        actual = replace(actual, checks=checks)
        if limits is not None:
            try:
                run_bytes, bounded_items = _run_account(metadata, config, state_bytes, checks, limits)
                bounded_bytes = 12 + run_bytes
                _limit(bounded_bytes + 18, limits.max_total_bytes, "total bytes")
            except Exception as error:
                raise ModelError("bounded replay initial: " + str(error)) from error
        report = replace(report, failure_reproduced=_same_failure(portable.initial_checks, checks))
        differences = ReplayDifferences(checks=checks != portable.initial_checks)
        if differences.checks: report = replace(report, outcome="diverged", field="initial checks", step=None)
        deliver(ReplayInitialObservation(state, checks, portable.initial_state, state_bytes, portable.initial_checks, differences))
        if differences.checks: return report
        actual = None
        for index, expected in enumerate(portable.steps, 1):
            sequence, actual = index, None
            input = _copy(_call("decode input", model.decode_input, expected.input))
            input_bytes = encode_value(model, "input", input, maximum=blob_limit, storage=input_storage, stage="encode input")
            if input_bytes != expected.input: raise ModelError(f"input {index} encoding is not canonical")
            transition = _call("transition", model.step, _copy(state), _copy(input))
            if not isinstance(transition, Transition): raise ModelError("transition: model must return Transition")
            actual = ReplayActual("turn", sequence, before=state, input=input, transition=transition)
            transition = _copy(transition)
            actual = replace(actual, transition=transition)
            boundary = _check_observed_count_into(model, state, input, transition, sequence, check_storage)
            checks = tuple(check_storage)
            actual = replace(actual, checks=checks, state_check_count=boundary)
            remaining = None
            if limits is not None:
                fixed, items = _step_fixed(input_bytes, transition.disposition, len(transition.outputs), checks, limits)
                _limit(bounded_items + items, limits.max_items, "aggregate items")
                total_remaining = limits.max_total_bytes - bounded_bytes - 18 - 9
                frame = min(limits.max_frame_bytes, U32_MAX, total_remaining)
                if frame < fixed: raise ModelError("bounded replay limit: frame bytes")
                remaining = frame - fixed
                bounded_bytes += fixed + 9
                bounded_items += items
            else:
                _disposition(transition.disposition)
            report = replace(report, failure_reproduced=report.failure_reproduced or _same_failure(expected.checks, checks))
            outputs = []
            for out_index, output in enumerate(transition.outputs):
                if out_index == len(output_storage): output_storage.append(bytearray())
                encoded = encode_value(model, "output", output, maximum=blob_limit if remaining is None else min(blob_limit, remaining),
                                       storage=output_storage[out_index], stage="encode output")
                outputs.append(encoded)
                if remaining is not None:
                    remaining -= len(encoded)
                    bounded_bytes += len(encoded)
            state_bytes = encode_value(model, "state", transition.state, maximum=blob_limit if remaining is None else min(blob_limit, remaining),
                                       storage=state_storage, stage="encode state")
            if remaining is not None: bounded_bytes += len(state_bytes)
            differences = ReplayDifferences(not _same_disposition(transition.disposition, expected.disposition),
                tuple(outputs) != expected.outputs, state_bytes != expected.post_state, checks != expected.checks)
            primary = differences.primary()
            report = replace(report, outcome="diverged", field=primary, step=index) if primary else replace(report, steps_verified=index)
            deliver(ReplayTurnObservation(actual, expected, input_bytes, tuple(outputs), state_bytes, differences))
            if primary: return report
            state, actual = transition.state, None
        return report
    except ReplayError:
        raise
    except Exception as error:
        raise notify_error(error) from error
