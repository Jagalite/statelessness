"""Lossless candidate Rust audit binary-v1 codec, independent of Rust/FFI.

CRC32 detects corruption, not authenticity. Bounded streams are required: EOF
probing may block. This codec neither flushes nor closes caller-owned streams.
It validates structure, not semantic replay or legacy check-phase boundaries.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import struct
import zlib

from .core import Check, Disposition, Metadata, Trace, TraceStep, _natural

HEADER = b"STLESS\x1a\n\x01\x00\x00\x00"
U32_MAX = (1 << 32) - 1
TERMINATIONS = ("completed", "property_failed", "step_limit", "interrupted", "model_error")


class BinaryTraceError(Exception):
    def __init__(self, kind: str, detail: str | None = None):
        self.kind, self.detail = kind, detail
        super().__init__(kind + (": " + detail if detail is not None else ""))


def _invalid(detail):
    raise BinaryTraceError("invalid_data", detail)


def _limit(value, maximum, name):
    if value > maximum:
        raise BinaryTraceError("limit_exceeded", name)


@dataclass(frozen=True)
class BinaryLimits:
    max_total_bytes: int = 67_108_864
    max_frame_bytes: int = 8_388_608
    max_blob_bytes: int = 4_194_304
    max_string_bytes: int = 65_536
    max_steps: int = 100_000
    max_checks_per_step: int = 4_096
    max_outputs_per_step: int = 4_096
    max_parameters: int = 1_024
    max_items: int = 250_000

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            _natural(getattr(self, name), name)


@dataclass(frozen=True)
class RunConfig:
    strategy: str = "manual"
    seed: int | None = None
    parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        if not isinstance(self.strategy, str):
            raise ValueError("strategy must be a string")
        if self.seed is not None:
            _natural(self.seed, "seed")
        parameter_values = tuple(self.parameters)
        if any(isinstance(pair, (str, bytes)) for pair in parameter_values):
            raise ValueError("parameter pair must contain two strings")
        pairs = tuple(tuple(pair) for pair in parameter_values)
        if any(len(pair) != 2 or any(not isinstance(value, str) for value in pair) for pair in pairs):
            raise ValueError("parameters must be ordered string pairs")
        object.__setattr__(self, "parameters", pairs)


@dataclass(frozen=True)
class AuditTrace:
    metadata: Metadata
    config: RunConfig
    initial_state: bytes
    initial_checks: tuple[Check, ...]
    steps: tuple[TraceStep, ...]
    termination: str
    error: str = ""

    def __post_init__(self):
        object.__setattr__(self, "initial_checks", tuple(self.initial_checks))
        object.__setattr__(self, "steps", tuple(
            TraceStep(step.input, step.disposition, tuple(step.outputs), step.post_state,
                      tuple(step.checks)) if isinstance(step, TraceStep) else step
            for step in self.steps))

    @classmethod
    def from_portable(cls, trace: Trace, config: RunConfig) -> AuditTrace:
        """Require explicit configuration; never invent seed/strategy provenance."""
        if not isinstance(trace, Trace) or not isinstance(config, RunConfig):
            raise ValueError("conversion requires Trace and explicit RunConfig")
        if trace.error and trace.termination != "model_error":
            _invalid("error text requires model_error termination")
        return cls(trace.metadata, config, trace.initial_state, trace.initial_checks,
                   trace.steps, trace.termination, trace.error)

    def to_portable(self) -> tuple[Trace, RunConfig]:
        """Return omitted configuration alongside the portable trace, losslessly."""
        if self.error and self.termination != "model_error":
            _invalid("error text requires model_error termination")
        return (Trace(self.metadata, self.initial_state, self.initial_checks,
                      self.steps, self.termination, self.error), self.config)


def _utf8_payload(value):
    return str.encode(value, "utf-8", "strict")


class _Encoder:
    def __init__(self, limits, items, materialize=False):
        self.limits, self.items, self.length = limits, items, 0
        self.data = bytearray() if materialize else None

    def reserve_length(self, size):
        self.length += size
        _limit(self.length, min(self.limits.max_frame_bytes, U32_MAX), "frame bytes")

    def raw(self, value):
        self.reserve_length(len(value))
        if self.data is not None:
            self.data.extend(value)

    def integer(self, value, width):
        if type(value) is not int or not 0 <= value < 1 << (8 * width):
            _invalid("unsigned integer outside wire range")
        self.raw(value.to_bytes(width, "little"))

    def sized(self, value, maximum, name):
        if type(value) is not bytes:
            _invalid(name + " must be bytes")
        _limit(len(value), min(maximum, U32_MAX), name)
        self.integer(len(value), 4)
        self.raw(value)

    def string(self, value):
        if not isinstance(value, str):
            _invalid("string value")
        maximum = min(self.limits.max_string_bytes, U32_MAX)
        _limit(str.__len__(value), maximum, "string bytes")
        size = 0
        for character in str.__iter__(value):
            code = ord(character)
            if 0xD800 <= code <= 0xDFFF:
                _invalid("invalid UTF-8")
            size += 1 if code < 0x80 else 2 if code < 0x800 else 3 if code < 0x10000 else 4
            _limit(size, maximum, "string bytes")
        self.integer(size, 4)
        self.reserve_length(size)
        if self.data is not None:
            # Quotas and strict scalar validation precede encoding allocation.
            self.data.extend(_utf8_payload(value))

    def blob(self, value):
        self.sized(value, self.limits.max_blob_bytes, "blob bytes")

    def count(self, count, maximum, name):
        _limit(count, min(maximum, U32_MAX), name)
        self.items[0] += count
        _limit(self.items[0], self.limits.max_items, "aggregate items")
        self.integer(count, 4)

    def checks(self, values):
        self.count(len(values), self.limits.max_checks_per_step, "checks per step")
        for check in values:
            if not isinstance(check, Check):
                _invalid("check value")
            self.string(check.id)
            if check.status not in ("passed", "failed", "skipped"):
                _invalid("check status tag")
            if check.status == "passed" and check.details:
                _invalid("passing check reason")
            self.integer(("passed", "failed", "skipped").index(check.status), 1)
            if check.status != "passed":
                self.string(check.details)


def _encode_run(e, trace):
    metadata, config = trace.metadata, trace.config
    if not isinstance(metadata, Metadata) or not isinstance(config, RunConfig):
        _invalid("metadata/config value")
    e.string(metadata.name)
    for value in (metadata.model_version, metadata.properties_version, metadata.codec_version):
        e.integer(value, 4)
    e.string(metadata.build)
    e.string(config.strategy)
    e.integer(int(config.seed is not None), 1)
    if config.seed is not None:
        e.integer(config.seed, 8)
    e.count(len(config.parameters), e.limits.max_parameters, "parameters")
    for key, value in config.parameters:
        e.string(key)
        e.string(value)
    e.blob(trace.initial_state)
    e.checks(trace.initial_checks)


def _encode_step(e, index, step):
    if not isinstance(step, TraceStep):
        _invalid("step value")
    e.integer(index, 8)
    e.blob(step.input)
    disposition = step.disposition
    if not isinstance(disposition, Disposition) or disposition.kind not in ("accepted", "rejected", "ignored"):
        _invalid("disposition tag")
    if disposition.kind == "accepted" and disposition.reason:
        _invalid("accepted disposition reason")
    e.integer(("accepted", "rejected", "ignored").index(disposition.kind), 1)
    if disposition.kind != "accepted":
        e.string(disposition.reason)
    e.count(len(step.outputs), e.limits.max_outputs_per_step, "outputs per step")
    for output in step.outputs:
        e.blob(output)
    e.blob(step.post_state)
    e.checks(step.checks)


def _encode_end(e, trace):
    e.integer(len(trace.steps), 8)
    if trace.termination not in TERMINATIONS:
        _invalid("termination tag")
    if trace.error and trace.termination != "model_error":
        _invalid("error text requires model_error termination")
    if not isinstance(trace.error, str):
        _invalid("error string")
    e.integer(TERMINATIONS.index(trace.termination), 1)
    if trace.termination == "model_error":
        e.string(trace.error)


def _records(trace):
    yield 1, _encode_run, (trace,)
    for index, step in enumerate(trace.steps):
        yield 2, _encode_step, (index, step)
    yield 3, _encode_end, (trace,)


def _write_all(writer, data):
    view = memoryview(data)
    while view:
        try:
            count = writer.write(view)
        except InterruptedError:
            continue
        except MemoryError:
            raise
        except Exception as error:
            raise BinaryTraceError("io", str(error)) from error
        if type(count) is not int or count <= 0 or count > len(view):
            raise BinaryTraceError("io", "writer made no progress or returned invalid count")
        view = view[count:]


def write_audit(trace: AuditTrace, writer, *, limits: BinaryLimits = BinaryLimits()) -> None:
    """Preflight every field/quota before first write; I/O may still be partial."""
    try:
        if not isinstance(trace, AuditTrace):
            _invalid("writer requires AuditTrace")
        _limit(len(trace.steps), limits.max_steps, "steps")
        total, items = len(HEADER), [len(trace.steps)]
        _limit(items[0], limits.max_items, "aggregate items")
        for _, encode, args in _records(trace):
            encoder = _Encoder(limits, items)
            encode(encoder, *args)
            total += encoder.length + 9
            _limit(total, limits.max_total_bytes, "total bytes")
        _write_all(writer, HEADER)
        items = [len(trace.steps)]
        for tag, encode, args in _records(trace):
            encoder = _Encoder(limits, items, True)
            encode(encoder, *args)
            head = bytes([tag]) + struct.pack("<I", encoder.length)
            _write_all(writer, head)
            _write_all(writer, encoder.data)
            crc = zlib.crc32(encoder.data, zlib.crc32(head))
            _write_all(writer, struct.pack("<I", crc))
    except MemoryError as error:
        raise BinaryTraceError("allocation_failed") from error


def audit_to_bytes(trace: AuditTrace, *, limits: BinaryLimits = BinaryLimits()) -> bytes:
    stream = io.BytesIO()
    write_audit(trace, stream, limits=limits)
    return stream.getvalue()


class _Reader:
    def __init__(self, reader, limits):
        self.reader, self.limits, self.consumed = reader, limits, 0

    def read(self, size):
        while True:
            try:
                value = self.reader.read(size)
                break
            except InterruptedError:
                continue
            except MemoryError:
                raise
            except Exception as error:
                raise BinaryTraceError("io", str(error)) from error
        if type(value) is not bytes or len(value) > size:
            raise BinaryTraceError("io", "reader returned invalid bytes")
        return value

    def exact(self, size):
        self.consumed += size
        _limit(self.consumed, self.limits.max_total_bytes, "total bytes")
        data = bytearray()
        while len(data) < size:
            part = self.read(size - len(data))
            if not part:
                raise BinaryTraceError("truncated")
            data.extend(part)
        return bytes(data)

    def frame(self):
        head = self.exact(5)
        size = int.from_bytes(head[1:], "little")
        _limit(size, self.limits.max_frame_bytes, "frame bytes")
        _limit(self.consumed + size + 4, self.limits.max_total_bytes, "total bytes")
        payload = self.exact(size)
        crc = int.from_bytes(self.exact(4), "little")
        if crc != zlib.crc32(payload, zlib.crc32(head)):
            raise BinaryTraceError("checksum_mismatch")
        return head[0], payload


class _Decoder:
    def __init__(self, data, limits, items):
        self.data, self.limits, self.items, self.pos = data, limits, items, 0

    def raw(self, size):
        end = self.pos + size
        if end > len(self.data):
            _invalid("field exceeds frame")
        value = self.data[self.pos:end]
        self.pos = end
        return value

    def integer(self, size):
        return int.from_bytes(self.raw(size), "little")

    def sized(self, maximum, name):
        size = self.integer(4)
        _limit(size, maximum, name)
        return self.raw(size)

    def string(self):
        value = self.sized(self.limits.max_string_bytes, "string bytes")
        try:
            return value.decode("utf-8", "strict")
        except UnicodeError as error:
            raise BinaryTraceError("invalid_data", "invalid UTF-8") from error

    def blob(self):
        return self.sized(self.limits.max_blob_bytes, "blob bytes")

    def count(self, maximum, name, minimum):
        count = self.integer(4)
        _limit(count, maximum, name)
        self.items[0] += count
        _limit(self.items[0], self.limits.max_items, "aggregate items")
        if count > (len(self.data) - self.pos) // minimum:
            _invalid("collection count exceeds frame")
        return count

    def checks(self):
        count = self.count(self.limits.max_checks_per_step, "checks per step", 5)
        values = []
        for _ in range(count):
            id = self.string()
            tag = self.integer(1)
            if tag > 2:
                _invalid("check status tag")
            values.append(Check(id, ("passed", "failed", "skipped")[tag], "" if tag == 0 else self.string()))
        return tuple(values)

    def finish(self):
        if self.pos != len(self.data):
            _invalid("trailing record fields")


def read_audit(reader, *, limits: BinaryLimits = BinaryLimits()) -> AuditTrace:
    """Read one bounded artifact, requiring EOF after its mandatory footer."""
    try:
        stream = _Reader(reader, limits)
        header = stream.exact(12)
        if header[:8] != HEADER[:8]:
            _invalid("file magic")
        version = int.from_bytes(header[8:10], "little")
        if version != 1:
            raise BinaryTraceError("unsupported_version", str(version))
        if header[10:] != b"\0\0":
            _invalid("unsupported header flags")
        tag, payload = stream.frame()
        if tag != 1:
            _invalid("first record must be run metadata")
        items = [0]
        d = _Decoder(payload, limits, items)
        metadata = Metadata(d.string(), d.integer(4), d.integer(4), d.integer(4), d.string())
        strategy = d.string()
        seed_tag = d.integer(1)
        if seed_tag > 1:
            _invalid("seed tag")
        seed = d.integer(8) if seed_tag else None
        count = d.count(limits.max_parameters, "parameters", 8)
        parameters = tuple((d.string(), d.string()) for _ in range(count))
        initial_state, initial_checks = d.blob(), d.checks()
        d.finish()
        steps = []
        while True:
            tag, payload = stream.frame()
            d = _Decoder(payload, limits, items)
            if tag == 2:
                _limit(len(steps) + 1, limits.max_steps, "steps")
                items[0] += 1
                _limit(items[0], limits.max_items, "aggregate items")
                if d.integer(8) != len(steps):
                    _invalid("transition index")
                input = d.blob()
                disposition_tag = d.integer(1)
                if disposition_tag > 2:
                    _invalid("disposition tag")
                disposition = Disposition(("accepted", "rejected", "ignored")[disposition_tag],
                                          "" if disposition_tag == 0 else d.string())
                count = d.count(limits.max_outputs_per_step, "outputs per step", 4)
                outputs = tuple(d.blob() for _ in range(count))
                post_state, checks = d.blob(), d.checks()
                d.finish()
                steps.append(TraceStep(input, disposition, outputs, post_state, checks))
            elif tag == 3:
                if d.integer(8) != len(steps):
                    _invalid("footer transition count")
                termination = d.integer(1)
                if termination >= len(TERMINATIONS):
                    _invalid("termination tag")
                error = d.string() if termination == 4 else ""
                d.finish()
                if stream.read(1):
                    _invalid("trailing data after footer")
                return AuditTrace(metadata, RunConfig(strategy, seed, parameters), initial_state,
                                  initial_checks, tuple(steps), TERMINATIONS[termination], error)
            else:
                _invalid("unknown or misplaced record")
    except MemoryError as error:
        raise BinaryTraceError("allocation_failed") from error


def audit_from_bytes(data: bytes, *, limits: BinaryLimits = BinaryLimits()) -> AuditTrace:
    if type(data) is not bytes:
        raise ValueError("binary input must be bytes")
    return read_audit(io.BytesIO(data), limits=limits)
