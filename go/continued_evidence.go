package statelessness

import (
	"encoding/json"

	"github.com/Jagalite/statelessness/go/internal/portablejson"
)

// Extension continued-failure-evidence-v1 (S5); see
// spec/continued-failure-evidence-v1.md. A continued trace may record steps
// after a failed check. It is a distinct versioned format: ValidateRecording,
// Replay and ParseTrace (trace-json v1) reject it rather than silently
// reinterpreting a later failure as the end of a stop-at-failure trace.

const (
	ContinuedTraceFormat         = "stateless.continued-trace-json"
	ContinuedTraceVersion uint64 = 1
)

// ContinuedTrace termination is completed, step_limit, interrupted or
// model_error; property_failed is never used because failures do not end
// recording.
type ContinuedTrace struct{ Trace Trace }

// FailingSequences lists 0 for a failing initial state, then the 1-based steps
// whose checks failed.
func (t *ContinuedTrace) FailingSequences() []uint64 {
	result := []uint64{}
	if hasFailure(t.Trace.InitialChecks) {
		result = append(result, 0)
	}
	for k, step := range t.Trace.Steps {
		if hasFailure(step.Checks) {
			result = append(result, uint64(k+1))
		}
	}
	return result
}

func ValidateContinuedRecording(t *ContinuedTrace) error {
	if t == nil {
		return &ModelError{"nil trace"}
	}
	return validateContinuedTrace(&t.Trace)
}
func validateContinuedTrace(t *Trace) error {
	if !validTermination(t.Termination) || t.Error != "" && t.Termination != "model_error" {
		return &ModelError{"invalid trace termination"}
	}
	if t.Termination == "property_failed" {
		return &ModelError{"continued traces do not terminate on property failure"}
	}
	return nil
}

// RecordContinued records every input until the iterator ends, the step limit,
// or a model error, continuing past failed checks. Checker errors after earlier
// failures yield model_error with every completed step retained.
func RecordContinued[S, I, O any](m Model[S, I, O], inputs Iterator[I], maxSteps int) (*ContinuedTrace, error) {
	return RecordContinuedWithLimits(m, inputs, maxSteps, DefaultTraceLimits())
}
func RecordContinuedWithLimits[S, I, O any](m Model[S, I, O], inputs Iterator[I], maxSteps int, limits TraceLimits) (*ContinuedTrace, error) {
	trace, e := recordKernel(m, inputs, maxSteps, limits, true)
	if e != nil {
		return nil, e
	}
	return &ContinuedTrace{*trace}, nil
}

type ContinuedReplayReport struct {
	Replay           ReplayReport
	RecordedFailures []uint64
	// Recorded failing sequences whose replayed checks failed every recorded
	// failed check ID.
	ReproducedFailures []uint64
	// Exact outcome, every recorded step verified (states, dispositions,
	// outputs and checks), and every recorded failure reproduced. A verified
	// prefix is never complete reproduction.
	CompleteReproduction bool
}

// ReplayContinued uses the standard replay kernel with continued validation.
func ReplayContinued[S, I, O any](m Model[S, I, O], trace *ContinuedTrace, allowBuildMismatch bool) (*ContinuedReplayReport, error) {
	if trace == nil {
		return nil, &ModelError{"nil trace"}
	}
	reproduced := []uint64{}
	same := func(expected, actual []Check) bool {
		failed := false
		for _, e := range expected {
			if e.Status != "failed" {
				continue
			}
			failed = true
			found := false
			for _, a := range actual {
				if a.Status == "failed" && a.ID == e.ID {
					found = true
					break
				}
			}
			if !found {
				return false
			}
		}
		return failed
	}
	observer := ReplayObserver[S, I, O]{
		Initial: func(o ReplayInitialObservation[S]) error {
			if same(o.ExpectedChecks, o.Actual.Checks) {
				reproduced = append(reproduced, 0)
			}
			return nil
		},
		Turn: func(o ReplayTurnObservation[S, I, O]) error {
			if same(o.Expected.Checks, o.Actual.Checks) {
				reproduced = append(reproduced, o.Actual.Sequence)
			}
			return nil
		},
	}
	report, e := replayEvidenceValidated(m, &trace.Trace, nil, allowBuildMismatch, nil, observer, nil, validateContinuedTrace)
	if e != nil {
		if replayError, ok := e.(*ReplayError[S, I, O]); ok {
			return nil, replayError.Err
		}
		return nil, e
	}
	recorded := trace.FailingSequences()
	complete := report.Outcome == "exact" && report.StepsVerified == len(trace.Trace.Steps) && equalSequences(reproduced, recorded)
	return &ContinuedReplayReport{*report, recorded, reproduced, complete}, nil
}
func equalSequences(a, b []uint64) bool {
	if len(a) != len(b) {
		return false
	}
	for k := range a {
		if a[k] != b[k] {
			return false
		}
	}
	return true
}

// MarshalContinuedTrace encodes the trace-json v1 object with the continued
// format, version 1 and the failing_sequences array.
func MarshalContinuedTrace(t *ContinuedTrace) ([]byte, error) {
	if e := ValidateContinuedRecording(t); e != nil {
		return nil, e
	}
	// MarshalTrace applies every trace-json v1 limit and validity check.
	if _, e := MarshalTrace(&t.Trace); e != nil {
		return nil, e
	}
	data := TraceData(&t.Trace)
	data["format"] = ContinuedTraceFormat
	data["version"] = ContinuedTraceVersion
	data["failing_sequences"] = t.FailingSequences()
	encoded, e := json.Marshal(data)
	if e != nil {
		return nil, e
	}
	if _, e = ParseContinuedTrace(encoded); e != nil {
		return nil, e
	}
	return encoded, nil
}

// ParseContinuedTrace rejects trace-json v1, other versions, truncated bytes,
// and failing_sequences that disagree with the recorded checks.
func ParseContinuedTrace(data []byte) (*ContinuedTrace, error) {
	limits := DefaultTraceLimits()
	value, e := portablejson.Strict(data, limits.MaxJSONBytes)
	if e != nil {
		return nil, e
	}
	object, ok := value.(map[string]any)
	if !ok {
		return nil, &FormatError{Message: "expected object"}
	}
	r := &portablejson.Reader{}
	if r.Text(object["format"]) != ContinuedTraceFormat || r.Integer(object["version"], (1<<53)-1) != ContinuedTraceVersion || r.Err != nil {
		return nil, &FormatError{Message: "unsupported continued trace format"}
	}
	declaredValues, present := object["failing_sequences"]
	if !present {
		return nil, &FormatError{Message: "missing field: failing_sequences"}
	}
	declared := []uint64{}
	for _, v := range r.Array(declaredValues, limits.MaxSteps+1) {
		declared = append(declared, r.Integer(v, (1<<53)-1))
	}
	if r.Err != nil {
		return nil, r.Err
	}
	fields := make(map[string]any, len(object))
	for k, v := range object {
		if k != "failing_sequences" {
			fields[k] = v
		}
	}
	fields["format"] = "stateless.trace-json"
	fields["version"] = json.Number("1")
	trace, e := TraceFromData(fields, limits)
	if e != nil {
		return nil, e
	}
	t := &ContinuedTrace{*trace}
	if e := ValidateContinuedRecording(t); e != nil {
		return nil, e
	}
	if !equalSequences(declared, t.FailingSequences()) {
		return nil, &FormatError{Message: "failing_sequences disagree with recorded checks"}
	}
	return t, nil
}

type ContinuedFinding struct {
	// 0 for the initial state; otherwise the 1-based step.
	Sequence uint64
	// Every failed check of that batch, state checks before transition checks.
	Violations []Violation
}
type ContinuedShrinkReport[I any] struct {
	Target Violation
	// Base.Original.Violations is [Target]; Base.Minimized.Violations holds the
	// final sequence's complete failing batch.
	Base *ShrinkReport64[I]
	// Every failure of the validated original, in sequence order; empty when
	// validation was interrupted.
	OriginalFindings []ContinuedFinding
	// Every failure of the minimized witness. Earlier failures are retained and
	// never suppressed; the last entry contains the target.
	MinimizedFindings []ContinuedFinding
}

// ShrinkContinued shrinks a witness whose selected failure occurs after earlier
// failed checks. Replay continues past failures; a candidate reproduces only
// when its final sequence fails with the target's exact phase and ID. Causal
// validity is the same rule as Shrink64 (each step must be enabled when it is
// replayed).
func ShrinkContinued[S, I, O any](g GenerateModel[S, I, O], inputs []I, target Violation, config ShrinkConfig64) (*ContinuedShrinkReport[I], error) {
	return ShrinkContinuedWithLimits(g, inputs, target, config, ShrinkLimits{})
}
func ShrinkContinuedWithLimits[S, I, O any](g GenerateModel[S, I, O], inputs []I, target Violation, config ShrinkConfig64, limits ShrinkLimits) (*ContinuedShrinkReport[I], error) {
	if limits.MaxReplayedTransitions != nil {
		maximum := *limits.MaxReplayedTransitions
		limits.MaxReplayedTransitions = &maximum
	}
	if e := g.validate(); e != nil {
		return nil, e
	}
	if g.InitialState == nil || g.Step == nil || g.InputEqual == nil {
		return nil, &ModelError{"shrink requires initial, step and input equality callbacks"}
	}
	if config.MaxAttempts <= 0 {
		return nil, &ConfigError{"shrink max attempts must be positive"}
	}
	if target.Check.Status != "failed" {
		return nil, &ModelError{"cannot shrink without a target violation"}
	}
	control, e := newRunControl(limits.Control)
	if e != nil {
		return nil, e
	}
	work := &shrinkWork[S, I, O]{g: g, control: control, maximum: limits.MaxReplayedTransitions, continued: true}
	base, e := shrinkDriver(g, Failure[I]{inputs, []Violation{target}}, target, config, work)
	if e != nil {
		return nil, e
	}
	report := &ContinuedShrinkReport[I]{Target: target, Base: base, OriginalFindings: []ContinuedFinding{}, MinimizedFindings: []ContinuedFinding{}}
	if base.ValidatedOriginal {
		report.OriginalFindings, report.MinimizedFindings = work.originalFindings, work.minimizedFindings
	}
	return report, nil
}
