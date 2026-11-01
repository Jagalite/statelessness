"""Candidate continued-failure-evidence-v1 (S5); see spec/continued-failure-evidence-v1.md.

A continued trace records steps after a failed check. It is a distinct versioned
JSON format: `validate_recording`, `replay` and `trace_json.loads` (trace-json
v1) reject it rather than reinterpreting a later failure as the end of a
stop-at-failure trace. `record`, `replay`, `validate_recording` and `shrink`
are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Iterable

from .core import (Check, ModelError, ReplayReport, Trace, TraceLimits, _failed,
                   _record_kernel)
from .replay_evidence import ReplayError, _replay
from .trace_json import FormatError, array, dumps, integer, strict_loads, trace_from_data

CONTINUED_TRACE_FORMAT = "stateless.continued-trace-json"
CONTINUED_TRACE_VERSION = 1
_TERMINATIONS = ("completed", "property_failed", "step_limit", "interrupted", "model_error")


@dataclass(frozen=True)
class ContinuedTrace:
    """Termination is completed, step_limit, interrupted or model_error;
    property_failed is never used because failures do not end recording."""
    trace: Trace

    @property
    def failing_sequences(self) -> tuple[int, ...]:
        """0 for the initial state; otherwise 1-based steps whose checks failed."""
        initial = (0,) if _failed(self.trace.initial_checks) else ()
        return initial + tuple(index for index, step in enumerate(self.trace.steps, 1) if _failed(step.checks))


def validate_continued_recording(trace: ContinuedTrace) -> None:
    if not isinstance(trace, ContinuedTrace):
        raise ValueError("expected ContinuedTrace")
    t = trace.trace
    if t.termination not in _TERMINATIONS:
        raise ModelError("unknown trace termination")
    if t.error and t.termination != "model_error":
        raise ModelError("error text requires model_error termination")
    if t.termination == "property_failed":
        raise ModelError("continued traces do not terminate on property failure")


def record_continued(model, inputs: Iterable[Any], max_steps: int = 100_000,
                     *, limits: TraceLimits = TraceLimits()) -> ContinuedTrace:
    """Record every input until the inputs end, the step limit, or a model,
    checker or codec error, continuing past failed checks. A later error yields
    model_error with every completed step retained."""
    return ContinuedTrace(_record_kernel(model, inputs, max_steps, limits, continued=True))


@dataclass(frozen=True)
class ContinuedReplayReport:
    replay: ReplayReport
    recorded_failures: tuple[int, ...]
    # Recorded failing sequences whose replayed checks failed every recorded failed ID.
    reproduced_failures: tuple[int, ...]
    # Exact outcome, every recorded step verified, and every recorded failure
    # reproduced. A verified prefix is never complete reproduction.
    complete_reproduction: bool


def _reproduced(expected: Iterable[Check], actual: Iterable[Check]) -> bool:
    failed = [check for check in expected if check.status == "failed"]
    actual = [check for check in actual if check.status == "failed"]
    return bool(failed) and all(any(str.__eq__(e.id, a.id) is True for a in actual) for e in failed)


def replay_continued(model, trace: ContinuedTrace, *, allow_build_mismatch: bool = False) -> ContinuedReplayReport:
    """Standard replay kernel with continued validation: every step's
    disposition, ordered outputs, post-state and complete check batch are
    compared, stopping at the first divergence."""
    if not isinstance(trace, ContinuedTrace):
        raise ValueError("expected ContinuedTrace")
    reproduced: list[int] = []

    def observe(observation, _report):
        if observation.kind == "initial":
            if _reproduced(observation.expected_checks, observation.checks):
                reproduced.append(0)
        elif observation.kind == "turn":
            if _reproduced(observation.expected.checks, observation.actual.checks):
                reproduced.append(observation.actual.sequence)

    try:
        report = _replay(model, trace.trace, observe, allow_build_mismatch, None,
                         validate=lambda portable: validate_continued_recording(ContinuedTrace(portable)))
    except ReplayError as error:
        raise error.error from error
    recorded = trace.failing_sequences
    complete = (report.outcome == "exact" and report.steps_verified == len(trace.trace.steps)
                and tuple(reproduced) == recorded)
    return ContinuedReplayReport(report, recorded, tuple(reproduced), complete)


def encode_continued_trace(trace: ContinuedTrace, *, limits: TraceLimits = TraceLimits()) -> str:
    """trace-json v1 object with `format` = stateless.continued-trace-json,
    `version` = 1 and `failing_sequences` equal to the recorded checks."""
    validate_continued_recording(trace)
    data = json.loads(dumps(trace.trace, limits=limits))
    data["format"] = CONTINUED_TRACE_FORMAT
    data["version"] = CONTINUED_TRACE_VERSION
    data["failing_sequences"] = list(trace.failing_sequences)
    encoded = json.dumps(data, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > limits.max_json_bytes:
        raise FormatError("JSON byte limit exceeded")
    return encoded


def parse_continued_trace(data: str | bytes, *, limits: TraceLimits = TraceLimits()) -> ContinuedTrace:
    """Rejects trace-json v1, other versions, truncated bytes and
    `failing_sequences` that disagree with the recorded checks."""
    value = strict_loads(data, limits.max_json_bytes)
    if type(value) is not dict:
        raise FormatError("expected object")
    fields = dict(value)
    version = fields.get("version")
    if fields.get("format") != CONTINUED_TRACE_FORMAT or type(version) is not int or version != CONTINUED_TRACE_VERSION:
        raise FormatError("unsupported continued trace format")
    if "failing_sequences" not in fields:
        raise FormatError("continued trace requires failing_sequences")
    declared = tuple(integer(item) for item in array(fields.pop("failing_sequences"), limits.max_steps + 1))
    fields["format"], fields["version"] = "stateless.trace-json", 1
    trace = ContinuedTrace(trace_from_data(fields, limits))
    try:
        validate_continued_recording(trace)
    except ModelError as error:
        raise FormatError(str(error)) from error
    if declared != trace.failing_sequences:
        raise FormatError("failing_sequences disagree with recorded checks")
    return trace
