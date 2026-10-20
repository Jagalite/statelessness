"""Exact bounded capture of application-owned transitions; never runs a reducer.

Budgets count serialized evidence, not callback allocations or process RSS.
"""
from dataclasses import dataclass, replace
from .core import (Metadata, ModelError, TraceStep, U64_MAX, _call, _copy,
                   _dispatch_checks, _check_observed_count_into, _failed, _natural)
from .codec import encode_value
from .trace_binary import (AuditTrace, BinaryLimits, BinaryTraceError, RunConfig, U32_MAX, _Encoder,
                           _encode_step, _limit, write_audit, _variant, _text)
from .replay_evidence import _run_account, _step_fixed
from .audit_record import _footer_size, _error_reason


def _runtime_error(error):
    if isinstance(error, BinaryTraceError):
        if error.kind == 'limit_exceeded':
            return ModelError('runtime recording limit: trace exceeds ' + error.detail + ' limit')
        return ModelError('runtime recording limit: ' + str(error))
    return error


@dataclass(frozen=True)
class RecorderOptions:
    max_steps: int = 1024
    max_retained_bytes: int = 67_108_864
    limits: BinaryLimits = BinaryLimits()

    def __post_init__(self):
        limits = BinaryLimits(**{n: getattr(self.limits, n) for n in BinaryLimits.__dataclass_fields__})
        object.__setattr__(self, 'limits', limits)
        if type(self.max_steps) is not int or not 1 <= self.max_steps <= min(U64_MAX, limits.max_steps):
            raise ValueError('runtime recorder max_steps must be positive and within trace limits')
        if type(self.max_retained_bytes) is not int or not 1 <= self.max_retained_bytes <= U64_MAX:
            raise ValueError('runtime recorder byte budget must be positive')


class Recorder:
    def __init__(self, model, initial_state, config=RunConfig(), max_steps=1024):
        self._require_uninitialized()
        self._initialize(model, initial_state, config, RecorderOptions(max_steps), None, 0)

    @classmethod
    def with_options(cls, model, initial_state, config, options):
        result = cls.__new__(cls)
        result._initialize(model, initial_state, config, options, None, 0)
        return result

    @classmethod
    def with_checked_initial(cls, initial, config=RunConfig(), options=RecorderOptions()):
        return cls.with_checked_initial_at(initial, 0, config, options)

    @classmethod
    def with_checked_initial_at(cls, initial, host_sequence, config=RunConfig(), options=RecorderOptions()):
        from .checked import _initial_context
        model, state, checks = _initial_context(initial)
        result = cls.__new__(cls)
        result._initialize(model, state, config, options, checks, host_sequence)
        return result

    def _require_uninitialized(self):
        if '_initialization_started' in self.__dict__:
            raise ModelError('runtime recorder cannot be reinitialized')

    def _initialize(self, model, state, config, options, checked, origin):
        self._require_uninitialized()
        self._initialization_started = True
        try: self._initialize_impl(model, state, config, options, checked, origin)
        except Exception as error: raise ModelError('runtime recorder: ' + str(_runtime_error(error))) from error

    def _initialize_impl(self, model, state, config, options, checked, origin):
        _natural(origin, 'host_sequence')
        if not isinstance(options, RecorderOptions): raise ValueError('options must be RecorderOptions')
        self._options = RecorderOptions(options.max_steps, options.max_retained_bytes, options.limits)
        if not isinstance(config, RunConfig): raise ValueError('config must be RunConfig')
        from . import __version__
        for key, _ in config.parameters:
            if str.startswith(key, 'stateless.monitor.') or str.startswith(key, 'stateless.engine.'):
                raise ModelError('stateless.monitor. and stateless.engine. parameters are reserved')
        pairs = config.parameters + (('stateless.engine.version', __version__), ('stateless.engine.build', 'native-python-v1'))
        if origin: pairs += (('stateless.monitor.sequence_origin', str(origin)),)
        pairs += tuple(('stateless.monitor.' + k, str(v)) for k, v in (
            ('max_steps', options.max_steps), ('max_retained_bytes', options.max_retained_bytes),
            ('checking', 'full'), ('observed_steps', 0), ('evicted_steps', 0), ('checkpoint_after_sequence', 0)))
        self._config = RunConfig(config.strategy, config.seed, pairs)
        metadata = _call('runtime metadata', model.metadata)
        if not isinstance(metadata, Metadata): raise ModelError('runtime metadata must return Metadata')
        self._metadata = Metadata(**{n: getattr(metadata, n) for n in Metadata.__dataclass_fields__})
        self._origin, self._observed, self._evicted = origin, 0, 0
        self._steps = []
        self._busy, self._transferred = False, False
        self._state_storage, self._input_storage, self._before_storage = bytearray(), bytearray(), bytearray()
        self._output_storage = []
        limits = self._options.limits
        maximum_reason = min(256, limits.max_string_bytes, max(0, limits.max_frame_bytes - 13))
        self._reserve = _footer_size(0, 'model_error', 'x' * maximum_reason, limits)
        self._initial = self._encode(model, 'state', state, self._blob_limit(), 'checkpoint')
        if checked is not None:
            # Admit borrowed trusted evidence before making the recorder's copy.
            batch = _Encoder(limits, [0])
            batch.checks(checked)
            _limit(batch.length, self._options.max_retained_bytes, 'retained bytes')
            checks = tuple(checked)
        else:
            checks = self._initial_checks(model, state)
        self._checks = _copy(checks)
        self._termination, self._error = ('property_failed' if _failed(checks) else 'interrupted'), ''
        self._measure(self._initial, self._checks, [], self._config, _failed(checks))

    def _initial_checks(self, model, state):
        checks = []
        _dispatch_checks(model, 'check_state_into', 'check_state', (_copy(state),), checks, 'runtime check checkpoint')
        return tuple(checks)

    def _blob_limit(self):
        o, l = self._options, self._options.limits
        return min(l.max_blob_bytes, U32_MAX, l.max_frame_bytes, o.max_retained_bytes, l.max_total_bytes)

    def _encode(self, model, kind, value, maximum, stage):
        storage = self._input_storage if kind == 'input' else self._before_storage if stage == 'before state' else self._state_storage
        return encode_value(model, kind, value, maximum=maximum, storage=storage, stage='runtime encode ' + stage)

    def _counts_config(self, observed, evicted):
        pairs = self._config.parameters[:-3] + tuple((key, str(value)) for (key, _), value in zip(
            self._config.parameters[-3:], (observed, evicted, evicted)))
        return replace(self._config, parameters=pairs)

    def _measure(self, state, checks, observations, config, failed):
        l = self._options.limits
        run_bytes, items = _run_account(self._metadata, config, state, checks, l)
        total = 12 + run_bytes
        for step, _, size, count in observations:
            total += size
            items += count
        total += _footer_size(0, 'property_failed', '', l) if failed else self._reserve
        _limit(total, l.max_total_bytes, 'total bytes')
        _limit(items, l.max_items, 'aggregate items')
        _limit(len(observations), l.max_steps, 'steps')
        if total > self._options.max_retained_bytes:
            raise ModelError('runtime recording exceeds retained byte budget')
        if len(observations) > self._options.max_steps:
            raise ModelError('runtime recording exceeds retained step budget')

    def observe(self, model, before, input, transition):
        return self._observe(model, before, input, transition, None)

    def observe_checked(self, checked):
        return self._observe_checked_retaining(checked, 0)

    def _observe_checked_retaining(self, checked, minimum_evictions):
        self._require_active()
        try:
            from .checked import _turn_context
            model, before, input, transition, sequence, checks, boundary, full = _turn_context(checked)
            if not full: raise ModelError('runtime recorder requires full checking')
            expected = self._origin + self._observed + 1
            if expected > U64_MAX or sequence != expected:
                raise ModelError('runtime checked observation sequence is missing or reordered')
        except Exception as error:
            self.stop_with_error(error)
            raise
        return self._observe(model, before, input, transition, (checks, boundary), minimum_evictions)

    def _require_usable(self):
        if self._transferred: raise ModelError('runtime recorder has transferred its trace')

    def _require_active(self):
        self._require_usable()
        if self._busy: raise ModelError('runtime recorder observation is already in progress')
        if self.is_frozen(): raise ModelError('runtime recorder is frozen; export its retained trace')

    def _observe(self, model, before, input, transition, checked, minimum_evictions=0):
        self._require_active()
        self._busy = True
        try:
            metadata = _call('runtime metadata', model.metadata)
            if not isinstance(metadata, Metadata) or not all(
                    str.__eq__(getattr(metadata, n), getattr(self._metadata, n)) is True
                    if n in ('name', 'build') else type(getattr(metadata, n)) is int and getattr(metadata, n) == getattr(self._metadata, n)
                    for n in Metadata.__dataclass_fields__): raise ModelError('runtime recorder model identity changed')
            sequence = self._observed + 1
            if sequence > U64_MAX: raise ModelError('runtime observation sequence overflow')
            l, o, maximum = self._options.limits, self._options, self._blob_limit()
            encoded_before = self._encode(model, 'state', before, maximum, 'before state')
            expected = self._steps[-1][0].post_state if self._steps else self._initial
            if encoded_before != expected:
                raise ModelError('runtime before state is discontinuous; observations are missing or reordered')
            count = len(transition.outputs)
            _limit(count, min(l.max_outputs_per_step, U32_MAX), 'outputs per step')
            _limit(count, l.max_items, 'aggregate items')
            payload = min(l.max_frame_bytes, U32_MAX, o.max_retained_bytes, l.max_total_bytes)
            _limit(count * 4, payload, 'frame bytes')
            # Validate borrowed disposition strings before making an owned candidate.
            kind = _variant(transition.disposition.kind, ('accepted', 'ignored', 'rejected'), 'disposition tag')
            reason = _text(transition.disposition.reason, 'disposition reason')
            reason_encoder = _Encoder(l, [0]); reason_encoder.string(reason)
            if kind == 'accepted' and str.__len__(reason): raise ModelError('accepted disposition reason')
            _limit(reason_encoder.length - 4, payload, 'frame bytes')
            encoded_input = self._encode(model, 'input', input, maximum, 'input')
            if checked is None:
                checks = []
                boundary = _check_observed_count_into(model, before, input, transition, sequence, checks)
                checks = tuple(checks)
            else: checks, boundary = checked
            base, _ = _step_fixed(encoded_input, transition.disposition, count, checks, l)
            budget = min(min(l.max_frame_bytes, U32_MAX) + 9, o.max_retained_bytes, l.max_total_bytes)
            remaining = budget - base - 9
            if remaining < 0: raise ModelError('runtime recording limit: frame bytes')
            outputs = []
            for index, output in enumerate(transition.outputs):
                if index == len(self._output_storage): self._output_storage.append(bytearray())
                data = encode_value(model, 'output', output, maximum=min(maximum, remaining), storage=self._output_storage[index], stage='runtime encode output')
                outputs.append(data); remaining -= len(data)
            post = self._encode(model, 'state', transition.state, min(maximum, remaining), 'post state')
            step = TraceStep(encoded_input, _copy(transition.disposition), tuple(outputs), post, _copy(tuple(checks)))
            encoder = _Encoder(l, [1]); _encode_step(encoder, 0, step)
            observation = (step, boundary, encoder.length + 9, encoder.items[0])
            returned_checks = _copy(tuple(checks))
            if self.is_frozen(): raise ModelError('runtime recorder was stopped during observation')
            candidate = self._steps + [observation]
            if type(minimum_evictions) is not int or not 0 <= minimum_evictions <= len(self._steps):
                raise ModelError('invalid envelope retention request')
            first = max(minimum_evictions, int(len(self._steps) == o.max_steps))
            last_error = None
            for evictions in range(first, len(self._steps) + 1):
                state = self._initial if not evictions else self._steps[evictions-1][0].post_state
                checkpoint = self._checks if not evictions else self._steps[evictions-1][0].checks[:self._steps[evictions-1][1]]
                config = self._counts_config(sequence, self._evicted + evictions)
                try: self._measure(state, checkpoint, candidate[evictions:], config, _failed(checks))
                except Exception as error: last_error = error; continue
                self._initial, self._checks = state, checkpoint
                self._steps, self._config = candidate[evictions:], config
                self._observed, self._evicted = sequence, self._evicted + evictions
                if _failed(checks): self._termination = 'property_failed'
                return returned_checks
            raise last_error
        except Exception as error:
            contextual = _runtime_error(error)
            self.stop_with_error(contextual)
            if contextual is not error: raise contextual from error
            raise
        finally:
            self._busy = False

    def stop_with_error(self, error):
        self._require_usable()
        if not self.is_frozen():
            previous_busy, self._busy = self._busy, True
            self._termination, self._error = 'model_error', ''
            try:
                try: self._error = _error_reason(error, self._options.limits)
                except BaseException: pass
            finally:
                self._busy = previous_busy
        return error

    def _trace(self):
        self._require_usable()
        return AuditTrace(self._metadata, self._config, self._initial, self._checks,
                               tuple(item[0] for item in self._steps), self._termination, self._error)

    def snapshot(self): return _copy(self._trace())

    def into_trace(self):
        self._require_usable()
        if self._busy: raise ModelError('runtime recorder observation is already in progress')
        result = self._trace()
        self._transferred = True
        self._steps, self._initial, self._checks = [], b'', ()
        return result

    def write_to(self, writer): self.write_with_limits(writer, self._options.limits)
    def write_with_limits(self, writer, limits): write_audit(self._trace(), writer, limits=limits)
    def retained_bytes(self):
        trace = self.snapshot()
        run, _ = _run_account(trace.metadata, trace.config, trace.initial_state, trace.initial_checks, self._options.limits)
        return 12 + run + sum(s[2] for s in self._steps) + _footer_size(0, trace.termination, trace.error, self._options.limits)
    def retained_steps(self): self._require_usable(); return len(self._steps)
    def _retained_output_count(self, sequence):
        self._require_usable()
        if type(sequence) is not int: return None
        checkpoint = self._origin + self._evicted
        if checkpoint > U64_MAX: return None
        index = sequence - checkpoint - 1
        return len(self._steps[index][0].outputs) if 0 <= index < len(self._steps) else None

    def observed_steps(self): self._require_usable(); return self._observed
    def evicted_steps(self): self._require_usable(); return self._evicted
    def is_frozen(self): self._require_usable(); return self._termination != 'interrupted'
