"""Experimental exact causal evidence envelopes; host labels are assertions.

Structural quotas bound encoded bytes and logical decoder requests, not RSS.
"""
from dataclasses import dataclass, replace
import os
import zlib
from .core import Check, Disposition, Metadata, TraceStep, ModelError, U64_MAX, _natural
from .trace_binary import (AuditTrace, BinaryLimits, BinaryTraceError, RunConfig, U32_MAX, _text, _variant,
    _termination, audit_to_bytes, read_audit, _write_all)
from .replay_evidence import (ReplayError, replay_with_observations_bounded,
                              _audit_preflight)

MAGIC = b'STLESS\x1a\n'
OVERHEAD = 32


def _sequence(values, name):
    if type(values) is not tuple and type(values) is not list: raise ValueError(name + ' must be tuple or list')
    return tuple(values)


@dataclass(frozen=True)
class Provenance:
    run: str = ''
    epoch: int = 0
    manifest: str | None = None
    source: str | None = None
    environment: tuple[tuple[str, str], ...] = ()
    def __post_init__(self):
        _natural(self.epoch, 'epoch')
        pairs = _sequence(self.environment, 'environment')
        if any((type(p) is not tuple and type(p) is not list) or len(p) != 2 for p in pairs):
            raise ValueError('environment entries must be explicit pairs')
        object.__setattr__(self, 'environment', tuple(tuple(p) for p in pairs))


@dataclass(frozen=True)
class OutputOrigin:
    sequence: int
    output: int
    def __post_init__(self):
        _natural(self.sequence, 'sequence'); _natural(self.output, 'output', maximum=U32_MAX)


@dataclass(frozen=True)
class CausalContext:
    machine: str | None = None
    generation: int | None = None
    correlation: str | None = None
    logical_time: int | None = None
    causes: tuple[OutputOrigin, ...] = ()
    def __post_init__(self):
        for number in (self.generation, self.logical_time):
            if number is not None: _natural(number, 'optional causal integer')
        object.__setattr__(self, 'causes', _sequence(self.causes, 'causes'))


@dataclass(frozen=True)
class StepEvidence:
    sequence: int
    state_check_count: int
    causal: CausalContext = CausalContext()
    def __post_init__(self):
        _natural(self.sequence, 'sequence'); _natural(self.state_check_count, 'state_check_count', maximum=U32_MAX)


@dataclass(frozen=True)
class EvidenceLimits:
    trace: BinaryLimits = BinaryLimits()
    max_metadata_bytes: int = 4_194_304
    max_labels: int = 1024
    max_label_bytes: int = 4096
    max_causes_per_step: int = 32
    max_total_causes: int = 100_000
    def __post_init__(self):
        if type(self.trace) is not BinaryLimits: raise ValueError('trace limits must be BinaryLimits')
        object.__setattr__(self, 'trace', BinaryLimits(**{n: getattr(self.trace, n) for n in BinaryLimits.__dataclass_fields__}))
        for name in self.__dataclass_fields__:
            if name != 'trace': _natural(getattr(self, name), name)


def _limits(value):
    _need(type(value) is EvidenceLimits, 'expected EvidenceLimits')
    return EvidenceLimits(**{n: getattr(value, n) for n in EvidenceLimits.__dataclass_fields__})


def _need(condition, message):
    if not condition: raise ModelError(message)


def _utf8_size(text, maximum, message):
    _text(text, 'evidence string')
    _need(str.__len__(text) <= maximum, message)
    size = 0
    for ch in str.__iter__(text):
        n = ord(ch)
        _need(not 0xD800 <= n <= 0xDFFF, 'invalid UTF-8')
        size += 1 if n < 128 else 2 if n < 2048 else 3 if n < 65536 else 4
        _need(size <= maximum, message)
    return size


def _validate_provenance(value, limits):
    _need(type(value) is Provenance, 'expected Provenance')
    _natural(value.epoch, 'epoch')
    for label in (value.run, value.manifest, value.source):
        if label is not None: _utf8_size(label, limits.max_label_bytes, 'evidence label byte limit')
    _need(value.run is not None, 'evidence run must be a string')
    _need(len(value.environment) <= limits.max_labels, 'evidence environment label limit')
    names = set()
    for name, text in value.environment:
        _utf8_size(name, limits.max_label_bytes, 'evidence label byte limit')
        _utf8_size(text, limits.max_label_bytes, 'evidence label byte limit')
        key = str.__str__(name)
        _need(str.__len__(name) != 0 and key not in names, 'empty or duplicate evidence environment name')
        names.add(key)


def _validate_context(value, sequence, limits):
    _need(type(value) is CausalContext, 'expected CausalContext')
    for label in (value.machine, value.correlation):
        if label is not None: _utf8_size(label, limits.max_label_bytes, 'evidence label byte limit')
    for number in (value.generation, value.logical_time):
        if number is not None: _natural(number, 'optional causal integer')
    _need(len(value.causes) <= limits.max_causes_per_step, 'causal links per step limit')
    seen = set()
    for origin in value.causes:
        _need(type(origin) is OutputOrigin, 'expected OutputOrigin')
        _natural(origin.sequence, 'origin sequence')
        _natural(origin.output, 'origin output', maximum=U32_MAX)
        key = (origin.sequence, origin.output)
        _need(0 < origin.sequence < sequence and key not in seen, 'causal origin must be unique and precede its input')
        seen.add(key)


def _own_provenance(value):
    """Canonical owned closed-schema values; call only after quota admission."""
    _need(type(value) is Provenance, 'expected Provenance')
    plain = lambda text: None if text is None else str.__str__(text)
    return Provenance(plain(value.run), value.epoch, plain(value.manifest), plain(value.source),
                      tuple((plain(k), plain(v)) for k, v in value.environment))


def _own_context(value):
    _need(type(value) is CausalContext, 'expected CausalContext')
    plain = lambda text: None if text is None else str.__str__(text)
    return CausalContext(plain(value.machine), value.generation, plain(value.correlation), value.logical_time,
                        tuple(OutputOrigin(c.sequence, c.output) for c in value.causes))


def _own_step(value):
    _need(type(value) is StepEvidence, 'expected StepEvidence')
    return StepEvidence(value.sequence, value.state_check_count, _own_context(value.causal))


class _MetaEncoder:
    def __init__(self, maximum, materialize=False):
        self.maximum, self.length = maximum, 0
        self.data = bytearray() if materialize else None
    def reserve(self, size):
        _need(size <= self.maximum - self.length, 'encoded blob exceeds byte limit')
        self.length += size
    def integer(self, value, width):
        _natural(value, 'structural unsigned integer', maximum=(1 << (width * 8)) - 1)
        self.reserve(width)
        if self.data is not None: self.data.extend(value.to_bytes(width, 'little'))
    def text(self, value):
        size = _utf8_size(value, min(U32_MAX, max(0, self.maximum - self.length - 4)), 'encoded blob exceeds byte limit')
        self.integer(size, 4)
        self.reserve(size)
        if self.data is not None: self.data.extend(str.encode(value, 'utf8', 'strict'))
    def option(self, value, callback):
        self.integer(int(value is not None), 1)
        if value is not None: callback(value)
    def provenance(self, value):
        _need(type(value) is Provenance, 'expected Provenance')
        self.text(value.run); self.integer(value.epoch, 8)
        self.option(value.manifest, self.text); self.option(value.source, self.text)
        self.integer(len(value.environment), 4)
        for name, text in value.environment: self.text(name); self.text(text)
    def context(self, value):
        _need(type(value) is CausalContext, 'expected CausalContext')
        self.option(value.machine, self.text)
        self.option(value.generation, lambda n: self.integer(n, 8))
        self.option(value.correlation, self.text)
        self.option(value.logical_time, lambda n: self.integer(n, 8))
        self.integer(len(value.causes), 4)
        for origin in value.causes:
            _need(type(origin) is OutputOrigin, 'expected OutputOrigin')
            self.integer(origin.sequence, 8); self.integer(origin.output, 4)
    def step(self, value):
        _need(type(value) is StepEvidence, 'expected StepEvidence')
        self.integer(value.sequence, 8); self.integer(value.state_check_count, 4); self.context(value.causal)


def _encode_metadata(maximum, callback):
    dry = _MetaEncoder(maximum)
    callback(dry)
    actual = _MetaEncoder(maximum, True)
    callback(actual)
    return bytes(actual.data)


def _provenance_bytes(value, maximum): return _encode_metadata(maximum, lambda e: e.provenance(value))
def _step_bytes(value, maximum): return _encode_metadata(maximum, lambda e: e.step(value))
def _metadata_bytes(checkpoint, provenance, steps, maximum):
    def emit(e):
        e.integer(checkpoint, 8); e.provenance(provenance); e.integer(len(steps), 4)
        for step in steps: e.step(step)
    return _encode_metadata(maximum, emit)


class _MetadataDecoder:
    """Rust64 logical request accounting, independent of v1 quotas and RSS."""
    def __init__(self, data, maximum):
        _need(len(data) <= maximum, 'decode byte limit')
        self.data, self.at = data, 0
        self.allocation = self.work = min(U64_MAX, maximum * 16)
        self.elements, self.depth = maximum, 0
    def value(self, read):
        _need(self.work > 0, 'decode work limit'); self.work -= 1
        _need(self.depth < 16, 'decode nesting limit'); self.depth += 1
        try: return read()
        finally: self.depth -= 1
    def take(self, size):
        _need(size <= len(self.data) - self.at, 'truncated value')
        result = self.data[self.at:self.at + size]; self.at += size
        return result
    def integer(self, width): return self.value(lambda: int.from_bytes(self.take(width), 'little'))
    def collection(self, count, unit):
        _need(count <= self.elements, 'decode element limit'); self.elements -= count
        _need(count * unit <= self.allocation, 'decode allocation limit'); self.allocation -= count * unit
        _need(count <= self.work, 'decode work limit')
    def text(self):
        def read():
            size = self.integer(4); self.collection(size, 1); self.work -= size
            try: return self.take(size).tobytes().decode('utf8', 'strict')
            except UnicodeError as error: raise ModelError('invalid UTF-8') from error
        return self.value(read)
    def option(self, callback):
        def read():
            tag = self.integer(1)
            _need(tag in (0, 1), 'invalid option tag')
            return None if tag == 0 else callback()
        return self.value(read)
    def vector(self, unit, callback):
        def read():
            count = self.integer(4); self.collection(count, unit)
            return tuple(callback() for _ in range(count))
        return self.value(read)
    def provenance(self):
        def pair(): return self.value(lambda: (self.text(), self.text()))
        return self.value(lambda: Provenance(self.text(), self.integer(8), self.option(self.text), self.option(self.text), self.vector(48, pair)))
    def origin(self): return self.value(lambda: OutputOrigin(self.integer(8), self.integer(4)))
    def context(self):
        return self.value(lambda: CausalContext(self.option(self.text), self.option(lambda: self.integer(8)),
            self.option(self.text), self.option(lambda: self.integer(8)), self.vector(16, self.origin)))
    def step(self): return self.value(lambda: StepEvidence(self.integer(8), self.integer(4), self.context()))
    def finish(self): _need(self.at == len(self.data), 'trailing value bytes')


def _trace_shape(trace, limits=None, selected=None):
    """Closed evidence schema never invokes subclass-defined field getters."""
    _need(type(trace) is AuditTrace, 'evidence requires lossless AuditTrace')
    _need(type(trace.metadata) is Metadata and type(trace.config) is RunConfig, 'invalid evidence trace metadata type')
    if limits is not None:
        _need(len(trace.steps) <= limits.max_steps, 'trace exceeds steps limit')
        _need(len(trace.initial_checks) <= limits.max_checks_per_step, 'trace exceeds checks per step limit')
    for check in trace.initial_checks: _need(type(check) is Check, 'invalid evidence Check type')
    count = len(trace.steps) if selected is None else min(selected, len(trace.steps))
    for index in range(count):
        step = trace.steps[index]
        _need(type(step) is TraceStep, 'invalid evidence TraceStep type')
        _need(type(step.disposition) is Disposition, 'invalid evidence Disposition type')
        if limits is not None:
            _need(len(step.checks) <= limits.max_checks_per_step, 'trace exceeds checks per step limit')
            _need(len(step.outputs) <= limits.max_outputs_per_step, 'trace exceeds outputs per step limit')
        for check in step.checks: _need(type(check) is Check, 'invalid evidence Check type')


def _binary_error(error):
    if error.kind == 'limit_exceeded':
        return ModelError('trace exceeds ' + error.detail + ' limit')
    return ModelError(str(error))


class _ViewReader:
    def __init__(self, data): self.data, self.at = data, 0
    def read(self, size):
        size = min(size, len(self.data) - self.at)
        value = self.data[self.at:self.at + size].tobytes()
        self.at += size
        return value


def _read_embedded(data, limits):
    # Each frame header is admitted before requesting/copying its payload.
    try: return read_audit(_ViewReader(data), limits=limits)
    except BinaryTraceError as error: raise _binary_error(error) from error


@dataclass(frozen=True)
class TraceV2:
    trace: AuditTrace
    checkpoint_after: int = 0
    provenance: Provenance = Provenance()
    steps: tuple[StepEvidence, ...] = ()
    def __post_init__(self):
        _natural(self.checkpoint_after, 'checkpoint_after')
        object.__setattr__(self, 'steps', _sequence(self.steps, 'steps'))
    def validate(self, limits=EvidenceLimits()):
        _need(type(self) is TraceV2, 'expected TraceV2')
        limits = _limits(limits)
        _validate_provenance(self.provenance, limits)
        _trace_shape(self.trace)
        kind, _ = _termination(self.trace)
        def failed(checks):
            return any(_variant(c.status, ('passed', 'failed', 'skipped'), 'check status') == 'failed' for c in checks)
        initial_failed = failed(self.trace.initial_checks)
        _need(not initial_failed or not self.trace.steps, 'trace continues after initial property failure')
        _need(not any(failed(s.checks) for s in self.trace.steps[:-1]), 'trace continues after property failure')
        _need((initial_failed or bool(self.trace.steps and failed(self.trace.steps[-1].checks))) == (kind == 'property_failed'), 'trace termination disagrees with recorded checks')
        try: measured, _ = _audit_preflight(self.trace, limits.trace)
        except BinaryTraceError as error: raise _binary_error(error) from error
        metadata = _metadata_bytes(self.checkpoint_after, self.provenance, self.steps, limits.max_metadata_bytes)
        _need(measured + len(metadata) + OVERHEAD <= limits.trace.max_total_bytes, 'evidence total byte limit')
        _need(len(self.steps) == len(self.trace.steps), 'phase evidence does not cover every recorded step')
        _natural(self.checkpoint_after, 'checkpoint_after')
        _need(self.checkpoint_after + len(self.steps) <= U64_MAX, 'evidence sequence overflow')
        causes = 0
        for index, (evidence, step) in enumerate(zip(self.steps, self.trace.steps)):
            _need(evidence.sequence == self.checkpoint_after + index + 1 and evidence.state_check_count <= len(step.checks), 'invalid phase evidence sequence or check boundary')
            _validate_context(evidence.causal, evidence.sequence, limits)
            causes += len(evidence.causal.causes)
            _need(causes <= limits.max_total_causes, 'total causal links limit')
            for origin in evidence.causal.causes:
                if origin.sequence > self.checkpoint_after:
                    source = self.trace.steps[origin.sequence - self.checkpoint_after - 1]
                    _need(origin.output < len(source.outputs), 'causal origin output index outside retained evidence')
    def check_phase(self, sequence, check):
        _need(type(self) is TraceV2, 'expected TraceV2')
        if type(sequence) is not int or type(check) is not int or sequence < 0 or check < 0: return None
        if sequence == 0: return 'initial_state' if check < len(self.trace.initial_checks) else None
        if sequence > len(self.trace.steps) or sequence > len(self.steps) or check >= len(self.trace.steps[sequence - 1].checks): return None
        return 'state' if check < self.steps[sequence - 1].state_check_count else 'transition'
    def to_bytes(self, *, limits=EvidenceLimits()):
        _need(type(self) is TraceV2, 'expected TraceV2')
        limits = _limits(limits); self.validate(limits)
        metadata = _metadata_bytes(self.checkpoint_after, self.provenance, self.steps, limits.max_metadata_bytes)
        try: exact = audit_to_bytes(self.trace, limits=limits.trace)
        except BinaryTraceError as error: raise _binary_error(error) from error
        head = MAGIC + b'\x02\x00\x00\x00' + len(exact).to_bytes(8, 'little') + len(metadata).to_bytes(8, 'little')
        body = head + exact + metadata
        return body + zlib.crc32(body).to_bytes(4, 'little')
    def write(self, writer, *, limits=EvidenceLimits()):
        _need(type(self) is TraceV2, 'expected TraceV2')
        _write_all(writer, self.to_bytes(limits=limits))
    def write_raw_new(self, path, *, limits=EvidenceLimits()):
        _need(type(self) is TraceV2, 'expected TraceV2')
        _private_new(path, self.to_bytes(limits=limits))
    def replay(self, model, *, limits=EvidenceLimits(), allow_build_mismatch=False):
        _need(type(self) is TraceV2, 'expected TraceV2')
        limits = _limits(limits); self.validate(limits)
        mismatch = None
        def observe(value, report):
            nonlocal mismatch
            if value.kind == 'turn' and report.outcome == 'exact':
                sequence = value.actual.sequence
                if self.steps[sequence - 1].state_check_count != value.actual.state_check_count:
                    mismatch = sequence
                    raise ModelError('persisted check phase differs from replay')
        try: return replay_with_observations_bounded(model, self.trace, observe, limits=limits.trace, allow_build_mismatch=allow_build_mismatch)
        except ReplayError as error:
            if mismatch is not None: return replace(error.report, outcome='diverged', step=mismatch, field='check_phase', steps_verified=mismatch - 1, failure_reproduced=False)
            raise error.error from error
    @classmethod
    def verify_upgrade(cls, model, trace, provenance=Provenance(), checkpoint_after=0, *, limits=EvidenceLimits(), allow_build_mismatch=False):
        _need(cls is TraceV2, 'expected TraceV2')
        limits = _limits(limits); rows = []
        def observe(value, report):
            if value.kind == 'turn':
                sequence = checkpoint_after + value.actual.sequence
                _need(sequence <= U64_MAX, 'evidence sequence overflow')
                _need(value.actual.state_check_count <= U32_MAX, 'phase check count overflow')
                rows.append(StepEvidence(sequence, value.actual.state_check_count))
        try: report = replay_with_observations_bounded(model, trace, observe, limits=limits.trace, allow_build_mismatch=allow_build_mismatch)
        except ReplayError as error: raise error.error from error
        _need(report.outcome == 'exact', 'v1 upgrade requires exact compatible replay')
        result = cls(trace, checkpoint_after, provenance, tuple(rows)); result.validate(limits)
        return result


@dataclass(frozen=True)
class Artifact:
    value: AuditTrace | TraceV2
    def __post_init__(self): _need(type(self.value) is AuditTrace or type(self.value) is TraceV2, 'expected v1 or v2 artifact')
    @property
    def trace(self): return self.value.trace if type(self.value) is TraceV2 else self.value
    @property
    def version(self): return 2 if type(self.value) is TraceV2 else 1
    def check_phase(self, sequence, check):
        if self.version == 2: return self.value.check_phase(sequence, check)
        if type(sequence) is int and type(check) is int and sequence == 0 and 0 <= check < len(self.trace.initial_checks): return 'initial_state'
        return None
    @classmethod
    def read(cls, reader, *, limits=EvidenceLimits()):
        limits = _limits(limits)
        data = bytearray()
        maximum = limits.trace.max_total_bytes
        while True:
            try: chunk = reader.read(min(8192, maximum - len(data) + 1))
            except Exception as error: raise ModelError('evidence read: ' + str(error)) from error
            _need(type(chunk) is bytes, 'evidence reader must return bytes')
            if not chunk: break
            _need(len(chunk) <= maximum - len(data), 'evidence byte limit')
            data.extend(chunk)
        return _parse_artifact(memoryview(data), limits)
    @classmethod
    def from_bytes(cls, data, *, limits=EvidenceLimits()):
        limits = _limits(limits)
        _need(type(data) is bytes or type(data) is bytearray or type(data) is memoryview, 'expected bytes')
        view = memoryview(data)
        _need(view.c_contiguous, 'evidence bytes must be contiguous'); view = view.cast('B')
        _need(view.nbytes <= limits.trace.max_total_bytes, 'evidence byte limit')
        _header_admission(view, limits)
        # Mutable inputs are detached only after their declared quotas pass.
        # Concurrent mutation during this synchronous operation is unsupported.
        frozen = data if type(data) is bytes else bytes(view)
        return _parse_artifact(memoryview(frozen), limits)


def _header_admission(data, limits):
    _need(len(data) >= 12 and data[:8] == MAGIC, 'truncated or invalid evidence header')
    version = int.from_bytes(data[8:10], 'little')
    if version == 1: return
    _need(version == 2, 'unsupported evidence version')
    _need(len(data) >= OVERHEAD and data[10:12] == b'\0\0', 'invalid or truncated v2 header')
    trace_length, metadata_length = int.from_bytes(data[12:20], 'little'), int.from_bytes(data[20:28], 'little')
    _need(trace_length + metadata_length + OVERHEAD == len(data) and metadata_length <= limits.max_metadata_bytes, 'invalid evidence lengths or metadata limit')


def _parse_artifact(data, limits):
    _need(len(data) >= 12 and data[:8] == MAGIC, 'truncated or invalid evidence header')
    version = int.from_bytes(data[8:10], 'little')
    if version == 1: return Artifact(_read_embedded(data, limits.trace))
    _need(version == 2, 'unsupported evidence version')
    _need(len(data) >= OVERHEAD and data[10:12] == b'\0\0', 'invalid or truncated v2 header')
    trace_length, metadata_length = int.from_bytes(data[12:20], 'little'), int.from_bytes(data[20:28], 'little')
    _need(trace_length + metadata_length + OVERHEAD == len(data) and metadata_length <= limits.max_metadata_bytes, 'invalid evidence lengths or metadata limit')
    end = len(data) - 4
    _need(zlib.crc32(data[:end]) == int.from_bytes(data[end:], 'little'), 'evidence checksum mismatch')
    split = 28 + trace_length
    trace = _read_embedded(data[28:split], limits.trace)
    decoder = _MetadataDecoder(data[split:end], limits.max_metadata_bytes)
    checkpoint = decoder.integer(8); provenance = decoder.provenance(); steps = decoder.vector(120, decoder.step); decoder.finish()
    result = TraceV2(trace, checkpoint, provenance, steps); result.validate(limits)
    return Artifact(result)


@dataclass(frozen=True)
class ExportPolicy:
    max_steps: int = 1000
    max_bytes: int = 1_048_576
    property_ids: tuple[str, ...] = ()
    def __post_init__(self):
        _natural(self.max_steps, 'max_steps'); _natural(self.max_bytes, 'max_bytes')
        object.__setattr__(self, 'property_ids', _sequence(self.property_ids, 'property_ids'))


def diagnostic_json(artifact, policy=ExportPolicy()):
    _need(type(artifact) is Artifact, 'expected Artifact')
    _need(type(policy) is ExportPolicy, 'expected ExportPolicy')
    policy = ExportPolicy(policy.max_steps, policy.max_bytes, policy.property_ids)
    _need(all(issubclass(type(name), str) for name in policy.property_ids), 'property IDs must be strings')
    output = bytearray()
    def raw(value):
        _need(len(value) <= policy.max_bytes - len(output), 'evidence byte limit'); output.extend(value)
    def text(value):
        raw(b'"')
        for ch in str.__iter__(value):
            n = ord(ch)
            if ch == '"': raw(b'\\"')
            elif ch == '\\': raw(b'\\\\')
            elif n <= 31: raw(('\\u%04x' % n).encode('ascii'))
            else:
                _need(not 0xD800 <= n <= 0xDFFF, 'invalid UTF-8'); raw(str.encode(ch, 'utf8'))
        raw(b'"')
    trace = artifact.trace
    _trace_shape(trace, selected=policy.max_steps)
    kind, _ = _termination(trace)
    termination = dict(completed='completed_sequence', property_failed='property_failure', step_limit='step_limit', interrupted='interrupted', model_error='model_error')[kind]
    checkpoint = artifact.value.checkpoint_after if artifact.version == 2 else None
    start = '{"schema":"statelessness.diagnostic-export/1","replayable":false,"redaction_policy":"omit-payloads-v1","raw_payloads_included":false,"checkpoint_after":' + ('null' if checkpoint is None else str(checkpoint)) + ',"omitted_prefix":' + ('null' if checkpoint is None else 'false' if checkpoint == 0 else 'true') + ',"termination":"' + termination + '","truncated":' + ('true' if len(trace.steps) > policy.max_steps else 'false') + ',"observations":['
    raw(start.encode('ascii'))
    for sequence in range(min(len(trace.steps), policy.max_steps) + 1):
        if sequence: raw(b',')
        raw(('{"sequence":%d,"checks":[' % sequence).encode('ascii'))
        values = trace.initial_checks if sequence == 0 else trace.steps[sequence - 1].checks
        for index, check in enumerate(values):
            if index: raw(b',')
            status = _variant(check.status, ('passed', 'failed', 'skipped'), 'check status')
            phase = artifact.check_phase(sequence, index) or 'unknown_legacy'
            raw(('{"status":"' + status + '","phase":"' + phase + '","id":').encode('ascii'))
            _text(check.id, 'check ID')
            if any(issubclass(type(name), str) and str.__eq__(check.id, name) is True for name in policy.property_ids): text(check.id)
            else: raw(b'null')
            raw(b'}')
        raw(b']}')
    raw(b']}')
    return bytes(output)


def _private_new(path, data):
    descriptor = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, 'wb', buffering=0) as stream:
                descriptor = None
                _write_all(stream, data); os.fsync(stream.fileno())
        except Exception as error:
            raise ModelError('incomplete newly created evidence file: ' + str(error)) from error
    except ModelError: raise
    except Exception as error: raise ModelError(str(error)) from error
    finally:
        if descriptor is not None:
            try: os.close(descriptor)
            except OSError: pass


def export_diagnostic_new(artifact, path, policy=ExportPolicy()): _private_new(path, diagnostic_json(artifact, policy))
