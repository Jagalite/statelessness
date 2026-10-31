package statelessness

import "math"

type evidenceObservation struct {
	step  StepEvidence
	bytes uint64
}

// EvidenceRecorder atomically retains exact trace bytes and sealed phase/causal
// metadata. Host provenance is an assertion, not authentication. Callbacks may
// inspect snapshots but must not concurrently mutate the recorder.
type EvidenceRecorder struct {
	recorder      *Recorder
	provenance    Provenance
	origin        uint64
	steps         []evidenceObservation
	metadataBytes uint64
	limits        EvidenceLimits
	busy          bool
}

func NewEvidenceRecorder[S, I, O any](initial *CheckedInitial[S, I, O], origin uint64, config RunConfig, options RecorderOptions, provenance Provenance, limits EvidenceLimits) (*EvidenceRecorder, error) {
	if limits.MaxMetadataBytes > math.MaxUint64-32 {
		return nil, &ModelError{"evidence reservation overflow"}
	}
	overhead := limits.MaxMetadataBytes + 32
	available := min(options.MaxRetainedBytes, limits.Trace.MaxTotalBytes)
	if available < overhead {
		return nil, &ModelError{"evidence metadata reservation exceeds total limit"}
	}
	options.MaxRetainedBytes = available - overhead
	options.Limits = limits.Trace
	base, e := encodeEvidenceProvenance(provenance, limits.MaxMetadataBytes)
	if e != nil {
		return nil, e
	}
	if uint64(len(base)) > math.MaxUint64-12 {
		return nil, &ModelError{"metadata size overflow"}
	}
	baseSize := uint64(len(base)) + 12
	if baseSize > limits.MaxMetadataBytes {
		return nil, &ModelError{"metadata budget exceeded"}
	}
	if e = validateEvidenceProvenance(provenance, limits); e != nil {
		return nil, e
	}
	provenance = cloneProvenance(provenance)
	recorder, e := NewRecorderWithCheckedInitialAt(initial, origin, config, options)
	if e != nil {
		return nil, e
	}
	return &EvidenceRecorder{recorder: recorder, provenance: provenance, origin: origin, steps: []evidenceObservation{}, metadataBytes: baseSize, limits: limits}, nil
}
func ObserveEvidenceRecorder[S, I, O any](r *EvidenceRecorder, turn *CheckedTurn[S, I, O], causal CausalContext) error {
	if r.busy {
		return &ModelError{"runtime evidence recorder mutation already in progress"}
	}
	if r.IsFrozen() {
		return &ModelError{"runtime evidence recorder is frozen; export its retained trace"}
	}
	r.busy = true
	defer func() { r.busy = false }()
	fail := func(e error) error {
		r.recorder.StopWithError(e)
		if r.recorder.firstError != nil {
			return r.recorder.firstError
		}
		return e
	}
	if turn == nil || !turn.valid() {
		return fail(&ModelError{"invalid checked turn"})
	}
	if e := validateEvidenceContext(causal, turn.sequence, r.limits); e != nil {
		return fail(e)
	}
	if uint64(turn.stateCheckCount) > math.MaxUint32 {
		return fail(&ModelError{"phase boundary overflow"})
	}
	step := StepEvidence{Sequence: turn.sequence, StateCheckCount: uint32(turn.stateCheckCount), Causal: causal}
	// Metadata encoding is structural and runs no application callbacks.
	encoded, e := encodeEvidenceStep(step, r.limits.MaxMetadataBytes)
	if e != nil {
		return fail(e)
	}
	size := uint64(len(encoded))
	bytes := r.metadataBytes
	if e = auditAdd(&bytes, size, math.MaxUint64, "phase metadata size overflow"); e != nil {
		return fail(e)
	}
	links := uint64(len(causal.Causes))
	for _, old := range r.steps {
		if e = auditAdd(&links, uint64(len(old.step.Causal.Causes)), math.MaxUint64, "phase causal count overflow"); e != nil {
			return fail(e)
		}
	}
	evictions := 0
	for _, old := range r.steps {
		if bytes <= r.limits.MaxMetadataBytes && links <= r.limits.MaxTotalCauses {
			break
		}
		bytes -= old.bytes
		links -= uint64(len(old.step.Causal.Causes))
		evictions++
	}
	if bytes > r.limits.MaxMetadataBytes || links > r.limits.MaxTotalCauses {
		return fail(&ModelError{"candidate phase metadata exceeds retention limits"})
	}
	if r.origin > math.MaxUint64-r.recorder.evicted {
		return fail(&ModelError{"checkpoint overflow"})
	}
	checkpoint := r.origin + r.recorder.evicted
	// Validate against the current window, before planned evictions. Evicting an
	// invalid source must not turn it into an apparently valid omitted-prefix link.
	for _, source := range causal.Causes {
		if source.Sequence > checkpoint {
			count, ok := r.recorder.retainedOutputCount(source.Sequence)
			if !ok || uint64(source.Output) >= uint64(count) {
				return fail(&ModelError{"causal origin outside retained output range"})
			}
		}
	}
	// Allocate the replacement journal before the trace can commit.
	journal := make([]evidenceObservation, len(r.steps)+1)
	copy(journal, r.steps)
	journal[len(r.steps)] = evidenceObservation{cloneStepEvidence(step), size}
	previous := r.recorder.evicted
	if _, e = observeCheckedRecorderRetaining(r.recorder, turn, evictions); e != nil {
		return e
	}
	removed := int(r.recorder.evicted - previous)
	for _, old := range r.steps[:removed] {
		r.metadataBytes -= old.bytes
	}
	r.metadataBytes += size
	r.steps = journal[removed:]
	return nil
}
func (r *EvidenceRecorder) IsFrozen() bool              { return r.recorder.IsFrozen() }
func (r *EvidenceRecorder) StopWithError(e error) error { return r.recorder.StopWithError(e) }
func (r *EvidenceRecorder) Snapshot() *TraceV2 {
	steps := make([]StepEvidence, len(r.steps))
	for i, v := range r.steps {
		steps[i] = cloneStepEvidence(v.step)
	}
	return &TraceV2{Trace: *r.recorder.Snapshot(), CheckpointAfter: r.origin + r.recorder.evicted, Provenance: cloneProvenance(r.provenance), Steps: steps}
}

// IntoTrace detaches evidence and ends capture. Reentrant mutation returns nil.
func (r *EvidenceRecorder) IntoTrace() *TraceV2 {
	if r.busy || r.recorder.busy {
		return nil
	}
	trace := r.Snapshot()
	r.recorder.IntoTrace()
	return trace
}
