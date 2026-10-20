package statelessness

import (
	"bytes"
	"fmt"
	"io"
	"math"
	"strconv"
	"strings"
)

// RecorderOptions bounds retained serialized evidence, not callback allocations.
type RecorderOptions struct {
	MaxSteps         int
	MaxRetainedBytes uint64
	Limits           BinaryLimits
}

func DefaultRecorderOptions() RecorderOptions {
	l := DefaultBinaryLimits()
	return RecorderOptions{1024, l.MaxTotalBytes, l}
}

type recorderObservation struct {
	step        TraceStep
	stateChecks int
}

// Recorder captures transitions already executed by an application. It never runs Step.
// A recorder freezes on the first failed property or recording error.
type Recorder struct {
	trace                     AuditTrace
	options                   RecorderOptions
	observations              []recorderObservation
	observed, evicted, origin uint64
	transferred               bool
	busy                      bool
	exporting                 bool
	firstError                error
}

func NewRecorder[S, I, O any](m Model[S, I, O], state S, config RunConfig, maxSteps int) (*Recorder, error) {
	o := DefaultRecorderOptions()
	o.MaxSteps = maxSteps
	return NewRecorderWithOptions(m, state, config, o)
}
func NewRecorderWithOptions[S, I, O any](m Model[S, I, O], state S, config RunConfig, options RecorderOptions) (*Recorder, error) {
	return newRecorder(m, state, config, options, nil, 0)
}
func NewRecorderWithCheckedInitial[S, I, O any](initial *CheckedInitial[S, I, O], config RunConfig, options RecorderOptions) (*Recorder, error) {
	return NewRecorderWithCheckedInitialAt(initial, 0, config, options)
}
func NewRecorderWithCheckedInitialAt[S, I, O any](initial *CheckedInitial[S, I, O], origin uint64, config RunConfig, options RecorderOptions) (*Recorder, error) {
	if initial == nil || !initial.valid() {
		return nil, &ModelError{"invalid checked initial"}
	}
	return newRecorder(initial.model, initial.state, config, options, &initial.checks, origin)
}
func newRecorder[S, I, O any](m Model[S, I, O], state S, config RunConfig, o RecorderOptions, checked *[]Check, origin uint64) (*Recorder, error) {
	if o.MaxSteps <= 0 || uint64(o.MaxSteps) > o.Limits.MaxSteps {
		return nil, &ConfigError{"runtime recorder max_steps must be positive and within trace limits"}
	}
	if o.MaxRetainedBytes == 0 {
		return nil, &ConfigError{"runtime recorder byte budget must be positive"}
	}
	for _, p := range config.Parameters {
		if strings.HasPrefix(p[0], "stateless.monitor.") {
			return nil, &ConfigError{"stateless.monitor. parameters are reserved"}
		}
	}
	config, e := stampAuditConfig(config)
	if e != nil {
		return nil, e
	}
	if origin != 0 {
		config.Parameters = append(config.Parameters, [2]string{"stateless.monitor.sequence_origin", strconv.FormatUint(origin, 10)})
	}
	for _, p := range [][2]string{{"max_steps", strconv.Itoa(o.MaxSteps)}, {"max_retained_bytes", strconv.FormatUint(o.MaxRetainedBytes, 10)}, {"checking", "full"}, {"observed_steps", "0"}, {"evicted_steps", "0"}, {"checkpoint_after_sequence", "0"}} {
		config.Parameters = append(config.Parameters, [2]string{"stateless.monitor." + p[0], p[1]})
	}
	if e = m.validateCodec(); e != nil {
		return nil, e
	}
	metadata, e := call("runtime metadata", m.Codec.Metadata)
	if e != nil {
		return nil, e
	}
	if e = metadata.Validate(); e != nil {
		return nil, e
	}
	if o.Limits.MaxFrameBytes < 13 {
		return nil, fmt.Errorf("runtime recording limit: frame bytes")
	}
	r := &Recorder{options: o, origin: origin}
	r.trace = AuditTrace{Trace: Trace{Metadata: metadata, Steps: []TraceStep{}, Termination: "interrupted"}, Config: config}
	if e = recorderEncodeState(r, m, state, &r.trace.InitialState, r.blobLimit(), "checkpoint"); e != nil {
		return nil, e
	}
	if checked != nil {
		items := uint64(0)
		batch := auditEncoder{limits: o.Limits, items: &items}
		batch.checks(*checked)
		if batch.err != nil {
			return nil, fmt.Errorf("runtime recording limit: %w", batch.err)
		}
		if batch.size > o.MaxRetainedBytes {
			return nil, fmt.Errorf("runtime recording limit: retained bytes")
		}
		r.trace.InitialChecks = copyChecks(*checked)
	} else if e = stateChecksInto(m, state, &r.trace.InitialChecks); e != nil {
		return nil, e
	}
	if hasFailure(r.trace.InitialChecks) {
		r.trace.Termination = "property_failed"
	}
	if _, e = r.measure(&r.trace, !r.IsFrozen()); e != nil {
		if strings.HasPrefix(e.Error(), "runtime recording exceeds") {
			return nil, e
		}
		if a, ok := e.(*AuditError); ok && a.Kind == "limit_exceeded" {
			return nil, fmt.Errorf("runtime recording limit: trace exceeds %s limit", a.Detail)
		}
		return nil, fmt.Errorf("runtime recording limit: %w", e)
	}
	return r, nil
}
func (r *Recorder) blobLimit() uint64 {
	return min(r.options.Limits.MaxBlobBytes, r.options.Limits.MaxFrameBytes, r.options.Limits.MaxTotalBytes, r.options.MaxRetainedBytes, uint64(math.MaxUint32))
}
func recorderEncodeState[S, I, O any](r *Recorder, m Model[S, I, O], v S, target *[]byte, maximum uint64, stage string) error {
	_, e := call("runtime encode "+stage, func() (struct{}, error) { return struct{}{}, encodeStateInto(m, v, target, maximum) })
	return e
}
func (r *Recorder) counts(t *AuditTrace, observed, evicted uint64) {
	n := len(t.Config.Parameters) - 3
	for i, v := range []uint64{observed, evicted, evicted} {
		t.Config.Parameters[n+i][1] = strconv.FormatUint(v, 10)
	}
}

// measure validates every independent format quota and the active error reserve.
func (r *Recorder) measure(t *AuditTrace, reserve bool) (uint64, error) {
	l := r.options.Limits
	frameLimits := l

	items := uint64(0)
	total := uint64(12)
	frame := func(start uint64, f func(*auditEncoder)) error {
		localItems := start
		if e := auditLimit(localItems, l.MaxItems, "aggregate items"); e != nil {
			return e
		}
		a := auditEncoder{limits: frameLimits, items: &localItems}
		f(&a)
		if a.err != nil {
			return a.err
		}
		if e := auditAdd(&items, localItems, math.MaxUint64, "aggregate items"); e != nil {
			return e
		}
		return auditAdd(&total, a.size+9, math.MaxUint64, "total bytes")
	}
	if e := frame(0, func(a *auditEncoder) { auditEncodeRun(a, t) }); e != nil {
		return 0, e
	}
	for i, s := range t.Steps {
		if e := frame(1, func(a *auditEncoder) { auditEncodeStep(a, i, s) }); e != nil {
			return 0, e
		}
	}
	end := uint64(18)
	if reserve {
		if l.MaxFrameBytes < 13 {
			return 0, auditError("limit_exceeded", "frame bytes")
		}
		end += 4 + auditRecordReasonLimit(l)
	} else {
		a := auditEncoder{limits: frameLimits, items: &items}
		auditEncodeEnd(&a, t)
		if a.err != nil {
			return 0, a.err
		}
		end = a.size + 9
	}
	if e := auditAdd(&total, end, math.MaxUint64, "total bytes"); e != nil {
		return 0, e
	}
	for _, v := range []struct {
		actual, maximum uint64
		label           string
	}{{total, l.MaxTotalBytes, "total bytes"}, {items, l.MaxItems, "aggregate items"}, {uint64(len(t.Steps)), l.MaxSteps, "steps"}} {
		if v.actual > v.maximum {
			return 0, fmt.Errorf("trace exceeds %s limit", v.label)
		}
	}
	if total > r.options.MaxRetainedBytes {
		return 0, &ModelError{"runtime recording exceeds retained byte budget"}
	}
	if len(t.Steps) > r.options.MaxSteps {
		return 0, &ModelError{"runtime recording exceeds retained step budget"}
	}
	return total, nil
}

func ObserveRecorder[S, I, O any](r *Recorder, m Model[S, I, O], before S, input I, transition Transition[S, O]) ([]Check, error) {
	return recorderObserve(r, m, before, input, transition, nil, 0)
}
func ObserveCheckedRecorder[S, I, O any](r *Recorder, t *CheckedTurn[S, I, O]) ([]Check, error) {
	if r.busy {
		return nil, &ModelError{"runtime recorder mutation already in progress"}
	}
	if r.IsFrozen() {
		return nil, &ModelError{"runtime recorder is frozen; export its retained trace"}
	}
	if t == nil || !t.valid() {
		return nil, r.freeze(&ModelError{"invalid checked turn"})
	}
	if !t.fullChecking {
		return nil, r.freeze(&ModelError{"runtime recorder requires full checking"})
	}
	if r.observed == math.MaxUint64 || r.origin > math.MaxUint64-r.observed-1 || t.sequence != r.origin+r.observed+1 {
		return nil, r.freeze(&ModelError{"runtime checked observation sequence is missing or reordered"})
	}
	return recorderObserve(r, t.model, t.before, t.input, t.transition, &t.checks, t.stateCheckCount)
}
func recorderObserve[S, I, O any](r *Recorder, m Model[S, I, O], before S, input I, t Transition[S, O], checked *[]Check, phase int) ([]Check, error) {
	if r.busy {
		return nil, &ModelError{"runtime recorder mutation already in progress"}
	}
	r.busy = true
	defer func() { r.busy = false }()
	if r.IsFrozen() {
		return nil, &ModelError{"runtime recorder is frozen; export its retained trace"}
	}
	checks, e := recorderPrepare(r, m, before, input, t, checked, phase)
	if e != nil {
		return nil, r.freeze(e)
	}
	return checks, nil
}
func recorderPrepare[S, I, O any](r *Recorder, m Model[S, I, O], before S, input I, t Transition[S, O], checked *[]Check, phase int) ([]Check, error) {
	if e := m.validateCodec(); e != nil {
		return nil, e
	}
	metadata, e := call("runtime metadata", m.Codec.Metadata)
	if e != nil {
		return nil, e
	}
	if metadata != r.trace.Metadata {
		return nil, &ModelError{"runtime recorder model identity changed"}
	}
	if r.observed == math.MaxUint64 {
		return nil, &ModelError{"runtime observation sequence overflow"}
	}
	var encoded []byte
	if e = recorderEncodeState(r, m, before, &encoded, r.blobLimit(), "before state"); e != nil {
		return nil, e
	}
	expected := r.trace.InitialState
	if len(r.trace.Steps) > 0 {
		expected = r.trace.Steps[len(r.trace.Steps)-1].PostState
	}
	if !bytes.Equal(encoded, expected) {
		return nil, &ModelError{"runtime before state is discontinuous; observations are missing or reordered"}
	}
	if e = t.Disposition.Validate(); e != nil {
		return nil, e
	}
	l := r.options.Limits
	budget := min(l.MaxFrameBytes, uint64(math.MaxUint32), l.MaxTotalBytes, r.options.MaxRetainedBytes)
	for _, limit := range []struct {
		maximum uint64
		label   string
	}{{l.MaxOutputsPerStep, "outputs per step"}, {l.MaxItems, "aggregate items"}, {budget / 4, "frame bytes"}} {
		if uint64(len(t.Outputs)) > limit.maximum {
			return nil, auditError("limit_exceeded", limit.label)
		}
	}
	if uint64(len(t.Disposition.Reason)) > min(l.MaxStringBytes, uint64(math.MaxUint32)) {
		return nil, auditError("limit_exceeded", "string bytes")
	}
	if uint64(len(t.Disposition.Reason)) > budget {
		return nil, auditError("limit_exceeded", "frame bytes")
	}
	s := TraceStep{Disposition: t.Disposition}
	_, e = call("runtime encode input", func() (struct{}, error) { return struct{}{}, encodeInputInto(m, input, &s.Input, r.blobLimit()) })
	if e != nil {
		return nil, e
	}
	if checked != nil {
		items := uint64(0)
		batch := auditEncoder{limits: l, items: &items}
		batch.checks(*checked)
		if batch.err != nil {
			return nil, batch.err
		}
		if batch.size > budget {
			return nil, auditError("limit_exceeded", "frame bytes")
		}
		s.Checks = copyChecks(*checked)
	} else {
		phase, e = checkObservedCountInto(m, before, input, t, r.observed+1, FullChecks(), &s.Checks)
		if e != nil {
			return nil, e
		}
	}
	if phase < 0 || phase > len(s.Checks) {
		return nil, &ModelError{"invalid state check boundary"}
	}
	s.Outputs = make([][]byte, len(t.Outputs))
	items := uint64(1)
	a := auditEncoder{limits: l, items: &items}
	auditEncodeStep(&a, 0, s)
	if a.err != nil {
		return nil, a.err
	}
	frameBudget := min(min(l.MaxFrameBytes, uint64(math.MaxUint32))+9, l.MaxTotalBytes, r.options.MaxRetainedBytes)
	if a.size+9 > frameBudget {
		return nil, auditError("limit_exceeded", "frame bytes")
	}
	remaining := frameBudget - a.size - 9
	for i, v := range t.Outputs {
		_, e = call("runtime encode output", func() (struct{}, error) {
			return struct{}{}, encodeOutputInto(m, v, &s.Outputs[i], min(r.blobLimit(), remaining))
		})
		if e != nil {
			return nil, e
		}
		remaining -= uint64(len(s.Outputs[i]))
	}
	if e = recorderEncodeState(r, m, t.State, &s.PostState, min(r.blobLimit(), remaining), "post state"); e != nil {
		return nil, e
	}
	if r.IsFrozen() {
		return nil, r.firstError
	}
	// Plan all evictions before changing retained evidence or counters.
	candidate := r.trace
	candidate.Config = cloneRunConfig(r.trace.Config)
	candidate.Steps = append(append([]TraceStep{}, r.trace.Steps...), s)
	if hasFailure(s.Checks) {
		candidate.Termination = "property_failed"
	}
	minimum := 0
	if len(r.trace.Steps) == r.options.MaxSteps {
		minimum = 1
	}
	var fitErr error
	for evict := minimum; evict <= len(r.observations); evict++ {
		candidate.InitialState = r.trace.InitialState
		candidate.InitialChecks = r.trace.InitialChecks
		if evict > 0 {
			v := r.observations[evict-1]
			candidate.InitialState = v.step.PostState
			candidate.InitialChecks = v.step.Checks[:v.stateChecks]
		}
		candidate.Steps = append(append([]TraceStep{}, r.trace.Steps[evict:]...), s)
		r.counts(&candidate, r.observed+1, r.evicted+uint64(evict))
		_, fitErr = r.measure(&candidate, candidate.Termination == "interrupted")
		if fitErr == nil {
			r.trace = candidate
			r.observations = append(r.observations[evict:], recorderObservation{s, phase})
			r.observed++
			r.evicted += uint64(evict)
			return copyChecks(s.Checks), nil
		}
	}
	return nil, fitErr
}
func (r *Recorder) freeze(e error) error {
	if r.IsFrozen() {
		if r.firstError != nil {
			return r.firstError
		}
		return e
	}
	previous := r.busy
	r.busy = true
	defer func() { r.busy = previous }()
	r.firstError = e
	r.trace.Termination = "model_error"
	r.trace.Error = ""
	reason := func() (message string) {
		defer func() {
			if recover() != nil {
				message = "runtime error formatting panicked"
			}
		}()
		return e.Error()
	}()
	r.trace.Error = auditRecordReason(reason, r.options.Limits)
	return e
}
func (r *Recorder) StopWithError(e error) error {
	if r.exporting {
		return &ModelError{"runtime recorder export already in progress"}
	}
	if e == nil {
		e = fmt.Errorf("runtime recording stopped")
	}
	if !r.IsFrozen() {
		r.freeze(e)
	}
	return e
}
func (r *Recorder) Snapshot() *AuditTrace {
	return &AuditTrace{Trace: clonePortableTrace(r.trace.Trace), Config: cloneRunConfig(r.trace.Config)}
}

// IntoTrace returns detached evidence and freezes further capture.
func (r *Recorder) IntoTrace() *AuditTrace {
	if r.busy {
		return nil
	}
	t := r.Snapshot()
	if !r.IsFrozen() {
		r.transferred = true
	}
	return t
}

type recorderCountingWriter struct {
	writer io.Writer
	bytes  int64
}

func (w *recorderCountingWriter) Write(data []byte) (int, error) {
	n, e := w.writer.Write(data)
	w.bytes += int64(n)
	return n, e
}

// WriteTo implements io.WriterTo and reports bytes accepted by the writer.
func (r *Recorder) WriteTo(w io.Writer) (int64, error) {
	if w == nil {
		return 0, auditError("invalid_data", "nil writer")
	}
	counter := &recorderCountingWriter{writer: w}
	e := r.WriteWithLimits(counter, r.options.Limits)
	return counter.bytes, e
}
func (r *Recorder) WriteWithLimits(w io.Writer, l BinaryLimits) error {
	if r.busy {
		return &ModelError{"runtime recorder mutation already in progress"}
	}
	r.busy = true
	r.exporting = true
	defer func() { r.busy = false; r.exporting = false }()
	// A read/export borrow rejects mutation through writer callbacks.
	return r.trace.WriteBinaryWithLimits(w, l)
}
func (r *Recorder) RetainedBytes() uint64 { n, _ := r.measure(&r.trace, false); return n }
func (r *Recorder) RetainedSteps() int    { return len(r.trace.Steps) }
func (r *Recorder) ObservedSteps() uint64 { return r.observed }
func (r *Recorder) EvictedSteps() uint64  { return r.evicted }
func (r *Recorder) IsFrozen() bool {
	return r.transferred || r.trace.Termination != "interrupted"
}
