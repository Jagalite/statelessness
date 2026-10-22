"""Synchronous diagnostics for application-owned execution, without authority.

Events borrow payloads; callers keep them stable during observe(). No reducer,
checker, effect, continuity check or recorder handoff is performed here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum
import inspect
from typing import ClassVar, Protocol

from .codec import _dispatch_encode
from .core import Check, Metadata, Transition, _natural, _validated_check
from .trace_binary import RunConfig


class LogLevel(IntEnum):
    OFF = 0
    ERROR = 1
    WARN = 2
    INFO = 3
    DEBUG = 4
    TRACE = 5
    Off = OFF
    Error = ERROR
    Warn = WARN
    Info = INFO
    Debug = DEBUG
    Trace = TRACE


@dataclass(frozen=True)
class SnapshotPolicy:
    """Trace-only state selection. An explicit checkpoint ignores the interval."""
    kind: str = "none"
    interval: int | None = None
    NONE: ClassVar[SnapshotPolicy]
    EVERY_TRANSITION: ClassVar[SnapshotPolicy]

    def __post_init__(self):
        if self.kind == "every":
            _natural(self.interval, "snapshot interval", 1)
        elif self.kind not in ("none", "every_transition") or self.interval is not None:
            raise ValueError("invalid snapshot policy")

    @classmethod
    def every(cls, interval: int) -> SnapshotPolicy:
        return cls("every", interval)

    Every = every

    def includes(self, sequence: int) -> bool:
        return self.kind == "every_transition" or (
            self.kind == "every" and sequence % self.interval == 0)


SnapshotPolicy.NONE = SnapshotPolicy()
SnapshotPolicy.EVERY_TRANSITION = SnapshotPolicy("every_transition")


@dataclass(frozen=True)
class ObservationOptions:
    level: LogLevel = LogLevel.OFF
    snapshots: SnapshotPolicy = SnapshotPolicy.NONE
    max_record_bytes: int = 1024 * 1024

    def __post_init__(self):
        if not isinstance(self.level, LogLevel):
            raise ValueError("level must be LogLevel")
        if not isinstance(self.snapshots, SnapshotPolicy):
            raise ValueError("snapshots must be SnapshotPolicy")
        _natural(self.max_record_bytes, "max_record_bytes")


class Event:
    """Public diagnostic variants; constructing one never certifies checking."""
    Started: ClassVar[type[Started]]
    Checkpoint: ClassVar[type[Checkpoint]]
    Transition: ClassVar[type[TransitionEvent]]
    Message: ClassVar[type[Message]]
    Finished: ClassVar[type[Finished]]


@dataclass(frozen=True)
class Started(Event):
    metadata: Metadata
    config: RunConfig

    def __post_init__(self):
        if not isinstance(self.metadata, Metadata) or not isinstance(self.config, RunConfig):
            raise ValueError("started requires Metadata and RunConfig")


@dataclass(frozen=True)
class Checkpoint(Event):
    sequence: int
    state: object
    checks: tuple[Check, ...] = ()

    def __post_init__(self):
        _natural(self.sequence, "checkpoint sequence")
        object.__setattr__(self, "checks", _checks(self.checks))


@dataclass(frozen=True)
class TransitionEvent(Event):
    sequence: int
    before: object
    input: object
    transition: Transition
    checks: tuple[Check, ...] = ()

    def __post_init__(self):
        _natural(self.sequence, "transition sequence", 1)
        if not isinstance(self.transition, Transition):
            raise ValueError("transition event requires Transition")
        object.__setattr__(self, "checks", _checks(self.checks))


@dataclass(frozen=True)
class Message(Event):
    level: LogLevel
    sequence: int | None
    message: str

    def __post_init__(self):
        if not isinstance(self.level, LogLevel) or not isinstance(self.message, str):
            raise ValueError("message requires LogLevel and text")
        if self.sequence is not None:
            _natural(self.sequence, "message sequence")


@dataclass(frozen=True)
class Finished(Event):
    transitions: int
    reason: str

    def __post_init__(self):
        _natural(self.transitions, "transitions")
        if not isinstance(self.reason, str):
            raise ValueError("reason must be a string")


def _checks(values):
    return tuple(_validated_check(value) for value in values)


Event.Started = Started
Event.Checkpoint = Checkpoint
Event.Transition = TransitionEvent
Event.Message = Message
Event.Finished = Finished


class Observer(Protocol):
    def observe(self, model: object, event: Event) -> None: ...


class PayloadWriter(Protocol):
    """Synchronous binary append writer; escaped callback leases expire."""
    def write(self, data: bytes) -> int: ...
    def flush(self) -> None: ...


class PayloadFormat(Protocol):
    """Write bytes and return None; escaping custom payload text is your job."""
    def state(self, model: object, state: object, out: PayloadWriter) -> None: ...
    def input(self, model: object, input: object, out: PayloadWriter) -> None: ...
    def output(self, model: object, output: object, out: PayloadWriter) -> None: ...


class TextObserverError(OSError):
    """Diagnostic failure with a stable stage, separate from model checking."""
    def __init__(self, stage: str, detail: str):
        self.stage = {"write": "sink_write", "flush": "sink_flush"}.get(stage, stage.replace(" ", "_"))
        super().__init__(stage + ": " + detail)


def _error_detail(error):
    # User exceptions may themselves have a failing __str__.
    try:
        return str(error)
    except BaseException:
        return "callback failed (exception text unavailable)"


def _error(stage, error):
    return TextObserverError(stage, _error_detail(error))


class _EncodedPayloadError(TextObserverError):
    """Codec attribution belongs to one active payload lease, not an error type."""
    def __init__(self, operation, stage, detail):
        super().__init__(stage, detail)
        self._operation = operation
        self._encoding_stage, self._encoding_detail = stage, detail


def _encoding_error(stage, error, out):
    detail = _error_detail(error)
    if type(out) is _PayloadLease and out._operation is not None:
        return _EncodedPayloadError(out._operation, stage, detail)
    # Calling a built-in formatter directly still reports its codec stage.
    return TextObserverError(stage, detail)


def _sync(result):
    if result is not None:
        if inspect.iscoroutine(result) or inspect.isgenerator(result):
            result.close()
        raise TypeError("callback must return None synchronously")


def _append_all(writer, data):
    """Complete binary writes; a write interrupted before progress is retryable."""
    view = memoryview(data).toreadonly()
    offset = 0
    try:
        while offset < len(view):
            try:
                written = writer.write(view[offset:])
            except InterruptedError:
                continue
            if type(written) is not int or not 0 < written <= len(view) - offset:
                if inspect.iscoroutine(written) or inspect.isgenerator(written):
                    written.close()
                raise OSError("writer must report positive progress within the supplied bytes")
            offset += written
    finally:
        view.release()


class NoPayloads:
    """Payload placeholders; no codec, copying or repr requirement on values."""
    def state(self, model, state, out): _append_all(out, b"unavailable")
    def input(self, model, input, out): _append_all(out, b"unavailable")
    def output(self, model, output, out): _append_all(out, b"unavailable")


class EncodedPayloads:
    """Existing bounded codec dispatch, reusable storage, lowercase hex.

    Into hooks take precedence, including model_codec(...).bind(model). Existing
    dispatch snapshots values. Legacy encode_* can allocate before the bound.
    """
    def __init__(self, max_blob_bytes: int):
        _natural(max_blob_bytes, "max_blob_bytes")
        self._maximum = max_blob_bytes
        self._bytes = bytearray()
        self._busy = False

    def _encode(self, kind, model, value, out):
        stage = "encode " + kind
        if self._busy:
            raise _encoding_error(stage, "payload encoding already in progress", out)
        self._busy = True
        try:
            try:
                _dispatch_encode(model, kind, value, self._bytes, self._maximum, stage=stage)
            except Exception as error:
                raise _encoding_error(stage, error, out) from error
            _append_all(out, b"hex:")
            # No unbounded second full-size hex allocation.
            for byte in self._bytes:
                _append_all(out, f"{byte:02x}".encode("ascii"))
        finally:
            self._busy = False

    def state(self, model, state, out): self._encode("state", model, state, out)
    def input(self, model, input, out): self._encode("input", model, input, out)
    def output(self, model, output, out): self._encode("output", model, output, out)


class _Record:
    def __init__(self, data, maximum):
        data.clear()
        self.data, self.maximum, self.failure = data, maximum, None

    def require(self):
        if self.failure is not None:
            # Keep canonical attribution in the record, away from mutable public
            # exceptions that a formatter may catch, replace or relabel.
            raise TextObserverError(*self.failure)

    def write(self, data):
        self.require()
        try:
            if not isinstance(data, (bytes, bytearray, memoryview)):
                raise TypeError("payload writer requires bytes")
            view = memoryview(data).cast("B")
            if view.nbytes > self.maximum - len(self.data):
                self.failure = ("record limit", "observation record exceeds byte limit")
                self.require()
            self.data.extend(view)
            return view.nbytes
        except BaseException as error:
            if self.failure is None:
                self.failure = ("format", _error_detail(error))
            raise TextObserverError(*self.failure) from error


class _PayloadLease:
    """Revocation prevents retained formatters changing later records."""
    def __init__(self, record):
        self._record = record
        self._operation = object()
    def _require(self):
        if self._record is None:
            raise TextObserverError("format", "payload writer lease has expired")
        self._record.require()
        return self._record
    def write(self, data): return self._require().write(data)
    def flush(self): self._require()
    def _revoke(self):
        self._record = None
        self._operation = None


def _text(out, value):
    # Bound UTF-8 storage as it is produced, including long library strings.
    for offset in range(0, len(value), 256):
        out.write(value[offset:offset + 256].encode("utf-8"))


def _quoted(out, value):
    out.write(b'"')
    escapes = {'"': '\\"', "\\": "\\\\", "\0": "\\0", "\t": "\\t", "\n": "\\n", "\r": "\\r"}
    # Use intrinsic str operations; user __repr__/__str__ are never diagnostics.
    for char in str.__str__(value):
        if char in escapes:
            _text(out, escapes[char])
        elif not char.isprintable():
            _text(out, "\\u{" + format(ord(char), "x") + "}")
        else:
            _text(out, char)
    out.write(b'"')


def _optional(out, value):
    _text(out, "None" if value is None else f"Some({value})")


def _write_checks(out, checks, level):
    _text(out, f" checks={len(checks)}")
    for check in checks:
        if level == LogLevel.TRACE or check.status != "passed":
            out.write(b" check=")
            _quoted(out, check.id)
            _text(out, ":" + check.status.capitalize())
            if check.status != "passed":
                out.write(b"(")
                _quoted(out, check.details)
                out.write(b")")


def _level(event):
    if isinstance(event, (Started, Finished)):
        return LogLevel.INFO
    if isinstance(event, Message):
        return event.level
    if isinstance(event, (Checkpoint, TransitionEvent)):
        if any(check.status == "failed" for check in event.checks):
            return LogLevel.ERROR
        if any(check.status == "skipped" for check in event.checks):
            return LogLevel.WARN
        return LogLevel.TRACE if isinstance(event, Checkpoint) else LogLevel.DEBUG
    raise TypeError("expected an observation Event")


class TextObserver:
    """Bounded synchronous binary sink with record-atomic formatting.

    Writer/flush failure permanently poisons the sink, even at Off. Formatting
    errors publish nothing and recover on the next event. Not thread safe.
    """
    def __init__(self, writer, stream_id: str, options: ObservationOptions | None = None,
                 payloads: PayloadFormat | None = None):
        if hasattr(self, "_writer"):
            raise TextObserverError("sink", "observer cannot be reinitialized")
        if not isinstance(stream_id, str):
            raise ValueError("stream_id must be a string")
        if options is not None and not isinstance(options, ObservationOptions):
            raise ValueError("options must be ObservationOptions")
        self._writer, self._stream = writer, stream_id
        self._options = options if options is not None else ObservationOptions()
        self._payloads = payloads if payloads is not None else NoPayloads()
        self._record, self._failed, self._busy, self._taken = bytearray(), False, False, False

    @property
    def sink_poisoned(self) -> bool: return self._failed

    def set_level(self, level: LogLevel):
        self._options = replace(self._options, level=level)

    def set_snapshots(self, snapshots: SnapshotPolicy):
        self._options = replace(self._options, snapshots=snapshots)

    def get_ref(self):
        if self._taken: raise TextObserverError("sink", "writer ownership was transferred")
        return self._writer

    def into_inner(self):
        """Transfer the writer without flushing, even after poison."""
        if self._busy: raise TextObserverError("sink", "observation already in progress")
        writer = self.get_ref()
        self._taken, self._writer = True, None
        return writer

    def _healthy(self):
        if self._taken: raise TextObserverError("sink", "writer ownership was transferred")
        if self._failed:
            raise TextObserverError("sink poisoned", "observation sink failed; output may be incomplete")
        if self._busy: raise TextObserverError("sink", "observation already in progress")

    def flush(self):
        self._healthy()
        self._busy = True
        try:
            _sync(self._writer.flush())
        except BaseException as error:
            self._failed = True
            if not isinstance(error, Exception): raise
            raise _error("flush", error) from error
        finally:
            self._busy = False

    def _payload(self, kind, model, value, out):
        lease = _PayloadLease(out)
        operation, failure = lease._operation, None
        try:
            _sync(getattr(self._payloads, kind)(model, value, lease))
        except Exception as error:
            failure = error
        finally:
            # Exception text can execute user code too; revoke before rendering it.
            lease._revoke()
        # Rejected writes stay authoritative even if the callback later raises a
        # different error. Public staged errors carry no provenance; only a codec
        # failure from this invocation keeps its canonical stage.
        out.require()
        if failure is not None:
            if type(failure) is _EncodedPayloadError and failure._operation is operation:
                raise TextObserverError(failure._encoding_stage, failure._encoding_detail) from failure
            raise _error("format " + kind, failure) from failure

    def observe(self, model: object, event: Event) -> None:
        self._healthy()  # Poison takes precedence over filtering, including Off.
        options = self._options
        if options.level == LogLevel.OFF:
            return
        level = _level(event)
        if level == LogLevel.OFF or level > options.level:
            return
        self._busy = True
        try:
            out = _Record(self._record, options.max_record_bytes)
            try:
                _text(out, level.name.title() + " stream=")
                _quoted(out, self._stream)
                self._format(model, event, out, options)
                out.write(b"\n")
                out.require()
            except TextObserverError:
                raise
            except Exception as error:
                raise _error("format", error) from error
            self._write_all()
        finally:
            self._busy = False

    def _write_all(self):
        # A caller-owned Python writer can retain its argument. Publish immutable
        # bytes so that such retention neither pins nor aliases reusable storage.
        data = bytes(self._record)
        try:
            _append_all(self._writer, data)
        except BaseException as error:
            self._failed = True
            if not isinstance(error, Exception): raise
            raise _error("write", error) from error

    def _format(self, model, event, out, options):
        if isinstance(event, Started):
            metadata, config = event.metadata, event.config
            out.write(b" event=started metadata=ModelMetadata { name: ")
            _quoted(out, metadata.name)
            _text(out, f", model_version: {metadata.model_version}, properties_version: {metadata.properties_version}, codec_version: {metadata.codec_version}, build: ")
            _quoted(out, metadata.build)
            out.write(b" } config=RunConfig { strategy: ")
            _quoted(out, config.strategy)
            out.write(b", seed: ")
            _optional(out, config.seed)
            out.write(b", parameters: [")
            for index, (key, value) in enumerate(config.parameters):
                if index: out.write(b", ")
                out.write(b"(")
                _quoted(out, key)
                out.write(b", ")
                _quoted(out, value)
                out.write(b")")
            out.write(b"] }")
        elif isinstance(event, Finished):
            _text(out, f" event=finished transitions={event.transitions} reason=")
            _quoted(out, event.reason)
        elif isinstance(event, Message):
            out.write(b" event=message sequence=")
            _optional(out, event.sequence)
            out.write(b" message=")
            _quoted(out, event.message)
        elif isinstance(event, Checkpoint):
            _text(out, f" event=checkpoint sequence={event.sequence}")
            _write_checks(out, event.checks, options.level)
            if options.level == LogLevel.TRACE and options.snapshots.kind != "none":
                out.write(b" state=")
                self._payload("state", model, event.state, out)
        elif isinstance(event, TransitionEvent):
            transition = event.transition
            kind = str.__str__(transition.disposition.kind)
            label = {"accepted": "Accepted", "rejected": "Rejected", "ignored": "Ignored"}[kind]
            _text(out, f" event=transition sequence={event.sequence} disposition=" + label)
            if kind != "accepted":
                out.write(b"(")
                _quoted(out, transition.disposition.reason)
                out.write(b")")
            _text(out, f" outputs={len(transition.outputs)}")
            _write_checks(out, event.checks, options.level)
            if options.level == LogLevel.TRACE:
                out.write(b" input=")
                self._payload("input", model, event.input, out)
                for index, output in enumerate(transition.outputs):
                    _text(out, f" output[{index}]=")
                    self._payload("output", model, output, out)
                if options.snapshots.includes(event.sequence):
                    out.write(b" post_state=")
                    self._payload("state", model, transition.state, out)
