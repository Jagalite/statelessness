"""Coordinated exact trace and causal phase retention from sealed checked results.

Labels assert host claims; they authenticate neither claims nor causal delivery.
Budgets count encoded evidence, excluding model allocations and process RSS.
"""
from collections import deque
from .core import ModelError, U64_MAX, _natural
from .checked import _initial_context, _turn_context
from .monitor import Recorder, RecorderOptions
from .trace_binary import U32_MAX


class EvidenceRecorder:
    def __init__(self, initial, origin, config, options, provenance, limits):
        if '_initialization_started' in self.__dict__:
            raise ModelError('runtime evidence recorder cannot be reinitialized')
        self._initialization_started = True
        from .evidence import (_limits, _validate_provenance, _provenance_bytes,
                               Provenance, _own_provenance)
        _initial_context(initial)
        _natural(origin, 'origin')
        if not isinstance(options, RecorderOptions): raise ValueError('options must be RecorderOptions')
        self._limits = _limits(limits)
        overhead = self._limits.max_metadata_bytes + 32
        if overhead > U64_MAX: raise ModelError('evidence reservation overflow')
        available = min(options.max_retained_bytes, self._limits.trace.max_total_bytes) - overhead
        if available < 0: raise ModelError('evidence metadata reservation exceeds total limit')
        # The closed immutable provenance stays borrowed through admission;
        # canonical ownership is taken only after structural and label quotas.
        if type(provenance) is not Provenance: raise ModelError('expected Provenance')
        base = len(_provenance_bytes(provenance, self._limits.max_metadata_bytes)) + 12
        if base > self._limits.max_metadata_bytes: raise ModelError('metadata budget exceeded')
        _validate_provenance(provenance, self._limits)
        self._provenance = _own_provenance(provenance)
        self._origin = origin
        self._steps = deque()
        self._metadata_bytes = base
        self._busy, self._transferred = False, False
        self._recorder = Recorder.with_checked_initial_at(initial, origin, config,
            RecorderOptions(options.max_steps, available, self._limits.trace))

    def _require_usable(self):
        if self._transferred: raise ModelError('runtime evidence recorder has transferred its trace')

    def observe(self, turn, causal):
        self._require_usable()
        if self._busy: raise ModelError('runtime evidence recorder observation is already in progress')
        if self._recorder.is_frozen():
            raise ModelError('runtime evidence recorder is frozen; export its retained trace')
        self._busy = True
        try:
            from .evidence import (_validate_context, _step_bytes, CausalContext,
                                   StepEvidence, _own_step)
            _, _, _, _, sequence, _, boundary, _ = _turn_context(turn)
            if type(causal) is not CausalContext: raise ModelError('expected CausalContext')
            _validate_context(causal, sequence, self._limits)
            if not 0 <= boundary <= U32_MAX: raise ModelError('phase boundary overflow')
            step = StepEvidence(sequence, boundary, causal)
            size = len(_step_bytes(step, self._limits.max_metadata_bytes))
            byte_count = self._metadata_bytes + size
            links = len(step.causal.causes) + sum(len(old.causal.causes) for old, _ in self._steps)
            evictions = 0
            for old, old_size in self._steps:
                if byte_count <= self._limits.max_metadata_bytes and links <= self._limits.max_total_causes: break
                byte_count -= old_size
                links -= len(old.causal.causes)
                evictions += 1
            if byte_count > self._limits.max_metadata_bytes or links > self._limits.max_total_causes:
                raise ModelError('candidate phase metadata exceeds retention limits')
            checkpoint = self._origin + self._recorder.evicted_steps()
            if checkpoint > U64_MAX: raise ModelError('checkpoint overflow')
            for cause in step.causal.causes:
                if cause.sequence > checkpoint:
                    count = self._recorder._retained_output_count(cause.sequence)
                    if count is None or cause.output >= count:
                        raise ModelError('causal origin outside retained output range')
            # Detach and allocate journal storage before exact trace commit. Popping
            # an admitted prefix afterwards does not allocate journal entries.
            step = _own_step(step)
            journal = deque(self._steps)
            journal.append((step, size))
            previous = self._recorder.evicted_steps()
            self._recorder._observe_checked_retaining(turn, evictions)
            removed = self._recorder.evicted_steps() - previous
            metadata_bytes = self._metadata_bytes + size
            for _ in range(removed):
                _, old_size = journal.popleft()
                metadata_bytes -= old_size
            self._steps, self._metadata_bytes = journal, metadata_bytes
        except Exception as error:
            self._recorder.stop_with_error(error)
            raise
        finally:
            self._busy = False

    def is_frozen(self):
        self._require_usable()
        return self._recorder.is_frozen()

    def snapshot(self):
        self._require_usable()
        from .evidence import TraceV2, _own_provenance, _own_step
        previous_busy, self._busy = self._busy, True
        try:
            return TraceV2(self._recorder.snapshot(), self._origin + self._recorder.evicted_steps(),
                           _own_provenance(self._provenance), tuple(_own_step(step) for step, _ in self._steps))
        finally:
            self._busy = previous_busy

    def into_trace(self):
        self._require_usable()
        if self._busy: raise ModelError('runtime evidence recorder observation is already in progress')
        from .evidence import TraceV2
        self._busy = True
        try:
            checkpoint = self._origin + self._recorder.evicted_steps()
            # Build the envelope and empty replacement journal before consuming
            # the inner handle, so allocation errors retain a usable prefix.
            result = TraceV2(self._recorder.snapshot(), checkpoint, self._provenance,
                             tuple(step for step, _ in self._steps))
            empty = deque()
            self._recorder.into_trace()
            self._transferred = True
            self._steps = empty
            return result
        finally:
            self._busy = False
