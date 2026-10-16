"""Private structural interchange with ABI 2; no model execution or replay.

Portable JSON has no Rust run configuration. Import uses a manual configuration;
export intentionally discards binary run configuration, as the portable spec does.
"""
from __future__ import annotations
import struct
from .core import Check, Disposition, Metadata, Trace, TraceLimits, TraceStep
from .trace_json import FormatError, trace_from_data, trace_to_data

MAGIC = b"STV2P001"
MAX = 64 * 1024 * 1024
KINDS = ("accepted", "rejected", "ignored")
STATUSES = ("passed", "failed", "skipped")
TERMINATIONS = ("completed", "property_failed", "step_limit", "interrupted", "model_error")


def encode(trace: Trace, limits: TraceLimits = TraceLimits()) -> bytes:
    trace = trace_from_data(trace_to_data(trace), limits)
    data = bytearray(MAGIC)
    def raw(value: bytes) -> None:
        if len(value) > MAX - len(data):
            raise FormatError("native packet byte limit")
        data.extend(value)
    def number(value: int) -> None: raw(struct.pack("<I", value))
    def blob(value: bytes) -> None: number(len(value)); raw(value)
    def text(value: str) -> None: blob(value.encode("utf-8", "strict"))
    def checks(values: tuple[Check, ...]) -> None:
        number(len(values))
        for check in values:
            text(check.id); raw(bytes([STATUSES.index(check.status)])); text(check.details)
    m = trace.metadata
    text(m.name); text(m.build)
    for v in (m.model_version, m.properties_version, m.codec_version): number(v)
    text("manual"); raw(b"\0"); number(0)
    blob(trace.initial_state); checks(trace.initial_checks); number(len(trace.steps))
    for step in trace.steps:
        blob(step.input); raw(bytes([KINDS.index(step.disposition.kind)])); text(step.disposition.reason)
        number(len(step.outputs))
        for output in step.outputs: blob(output)
        blob(step.post_state); checks(step.checks)
    raw(bytes([TERMINATIONS.index(trace.termination)])); text(trace.error)
    return bytes(data)


def decode(packet: bytes, limits: TraceLimits = TraceLimits()) -> Trace:
    if type(packet) is not bytes or len(packet) > MAX:
        raise FormatError("native packet byte limit")
    if not isinstance(limits, TraceLimits): raise TypeError("limits must be TraceLimits")
    offset = 0
    items = payload = 0
    def raw(n: int) -> bytes:
        nonlocal offset
        if n > len(packet) - offset: raise FormatError("truncated native packet")
        value = packet[offset:offset+n]; offset += n; return value
    def number() -> int: return struct.unpack("<I", raw(4))[0]
    def count() -> int:
        nonlocal items
        n = number()
        if n > limits.max_items - items: raise FormatError("native packet item limit")
        items += n
        return n
    def blob(logical: bool = True) -> bytes:
        nonlocal payload
        n = number()
        if n > MAX or (logical and n > limits.max_blob_bytes): raise FormatError("native packet blob limit")
        if logical:
            if n > limits.max_payload_bytes - payload: raise FormatError("native packet payload limit")
            payload += n
        return raw(n)
    def text() -> str: return blob(False).decode("utf-8", "strict")
    def tag(values: tuple[str, ...]) -> str:
        n = raw(1)[0]
        if n >= len(values): raise FormatError("invalid native packet tag")
        return values[n]
    def checks() -> tuple[Check, ...]:
        return tuple(Check(text(), tag(STATUSES), text()) for _ in range(count()))
    if raw(8) != MAGIC: raise FormatError("unsupported native packet")
    name, build = text(), text()
    m = Metadata(name, number(), number(), number(), build)
    text()  # binary strategy is deliberately not a portable JSON field
    seed = raw(1)[0]
    if seed not in (0, 1): raise FormatError("invalid seed tag")
    if seed: raw(8)
    parameters = number()
    if parameters > (len(packet)-offset)//8: raise FormatError("native packet parameter limit")
    for _ in range(parameters): text(); text()
    initial, initial_checks = blob(), checks()
    steps = []
    step_count = count()
    if step_count > limits.max_steps: raise FormatError("native packet step limit")
    for _ in range(step_count):
        input = blob(); disposition = Disposition(tag(KINDS), text())
        outputs = tuple(blob() for _ in range(count()))
        steps.append(TraceStep(input, disposition, outputs, blob(), checks()))
    termination, error = tag(TERMINATIONS), text()
    if offset != len(packet): raise FormatError("trailing native packet bytes")
    trace = Trace(m, initial, initial_checks, tuple(steps), termination, error)
    return trace_from_data(trace_to_data(trace), limits)
