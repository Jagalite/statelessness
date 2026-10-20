package statelessness

import (
	"bytes"
	"fmt"
	"io"
	"math"
	"slices"
)

// ReplayDifferences includes every difference; Primary preserves comparison order.
type ReplayDifferences struct{ Disposition, Outputs, State, Checks bool }

func (d ReplayDifferences) Primary() string {
	switch {
	case d.Disposition:
		return "disposition"
	case d.Outputs:
		return "outputs"
	case d.State:
		return "state"
	case d.Checks:
		return "checks"
	}
	return ""
}

type ReplayInitialActual[S any] struct {
	State          S
	Checks         []Check
	ChecksComplete bool
}
type ReplayTurnActual[S, I, O any] struct {
	Sequence        uint64
	Before          S
	Input           I
	Transition      Transition[S, O]
	Checks          []Check
	StateCheckCount *int
}

// Nil StateCheckCount means the entire check batch is unknown, not passing.
type ReplayActual[S, I, O any] struct {
	Initial *ReplayInitialActual[S]
	Turn    *ReplayTurnActual[S, I, O]
}
type ReplayInitialObservation[S any] struct {
	Actual                     ReplayInitialActual[S]
	ExpectedState, ActualState []byte
	ExpectedChecks             []Check
	Differences                ReplayDifferences
	Report                     ReplayReport
}
type ReplayTurnObservation[S, I, O any] struct {
	Actual        ReplayTurnActual[S, I, O]
	Expected      TraceStep
	ActualInput   []byte
	ActualOutputs [][]byte
	ActualState   []byte
	Differences   ReplayDifferences
	Report        ReplayReport
}
type ReplayErrorObservation[S, I, O any] struct {
	SnapshotError error
	Sequence      uint64
	Actual        *ReplayActual[S, I, O]
	Err           error
	Report        ReplayReport
}
type ReplayObserver[S, I, O any] struct {
	Initial func(ReplayInitialObservation[S]) error
	Turn    func(ReplayTurnObservation[S, I, O]) error
	Error   func(ReplayErrorObservation[S, I, O]) error
}

// ReplayError retains produced values without executing callbacks again. If a
// clone callback itself fails, recovery values may share model-owned references;
// replay has stopped and callers must treat this fallback evidence as read-only.
type ReplayError[S, I, O any] struct {
	SnapshotError      error
	Err, ObserverError error
	Report             ReplayReport
	Actual             *ReplayActual[S, I, O]
	Sequence           uint64
}

func (e *ReplayError[S, I, O]) Error() string { return e.Err.Error() }
func (e *ReplayError[S, I, O]) Unwrap() error { return e.Err }
func replayReportCopy(r ReplayReport) ReplayReport {
	if r.Step != nil {
		v := *r.Step
		r.Step = &v
	}
	if r.Field != nil {
		v := *r.Field
		r.Field = &v
	}
	return r
}
func replaySnapshot[S, I, O any](m Model[S, I, O], a *ReplayActual[S, I, O]) (*ReplayActual[S, I, O], error) {
	if a == nil {
		return nil, nil
	}
	out := &ReplayActual[S, I, O]{}
	if a.Initial != nil {
		s, e := copied(m.CloneState, a.Initial.State)
		if e != nil {
			return nil, e
		}
		out.Initial = &ReplayInitialActual[S]{s, copyChecks(a.Initial.Checks), a.Initial.ChecksComplete}
	}
	if a.Turn != nil {
		v := *a.Turn
		var e error
		v.Before, e = copied(m.CloneState, v.Before)
		if e != nil {
			return nil, e
		}
		v.Input, e = copied(m.CloneInput, v.Input)
		if e != nil {
			return nil, e
		}
		v.Transition, e = cloneTransition(m, v.Transition)
		if e != nil {
			return nil, e
		}
		v.Checks = copyChecks(v.Checks)
		if v.StateCheckCount != nil {
			n := *v.StateCheckCount
			v.StateCheckCount = &n
		}
		out.Turn = &v
	}
	return out, nil
}

func ReplayWithObservations[S, I, O any](m Model[S, I, O], trace *Trace, allowBuildMismatch bool, observer ReplayObserver[S, I, O]) (*ReplayReport, error) {
	return replayEvidence(m, trace, nil, allowBuildMismatch, nil, observer)
}
func ReplayWithObservationsBounded[S, I, O any](m Model[S, I, O], trace *AuditTrace, allowBuildMismatch bool, limits BinaryLimits, observer ReplayObserver[S, I, O]) (*ReplayReport, error) {
	if trace == nil {
		return replayEvidence(m, nil, nil, allowBuildMismatch, &limits, observer)
	}
	return replayEvidence(m, &trace.Trace, trace, allowBuildMismatch, &limits, observer)
}
func replayEvidence[S, I, O any](m Model[S, I, O], trace *Trace, audit *AuditTrace, allow bool, limits *BinaryLimits, observer ReplayObserver[S, I, O]) (*ReplayReport, error) {
	return replayEvidenceCaptured(m, trace, audit, allow, limits, observer, nil)
}

// capture is engine-private admission of an entry-owned artifact after identity.
func replayEvidenceCaptured[S, I, O any](m Model[S, I, O], trace *Trace, audit *AuditTrace, allow bool, limits *BinaryLimits, observer ReplayObserver[S, I, O], capture func() (*AuditTrace, error)) (*ReplayReport, error) {
	r := ReplayReport{Outcome: "exact"}
	var actual *ReplayActual[S, I, O]
	sequence := uint64(0)
	var unavailableSnapshot error
	fail := func(e error, notify bool) (*ReplayReport, error) {
		var view *ReplayActual[S, I, O]
		snapshotErr := unavailableSnapshot
		if notify && observer.Error != nil && snapshotErr == nil {
			view, snapshotErr = replaySnapshot(m, actual)
		}
		re := &ReplayError[S, I, O]{Err: e, Report: replayReportCopy(r), Actual: actual, Sequence: sequence, SnapshotError: snapshotErr}
		if notify && observer.Error != nil {
			_, re.ObserverError = call("replay error observer", func() (struct{}, error) {
				return struct{}{}, observer.Error(ReplayErrorObservation[S, I, O]{Sequence: sequence, Actual: view, Err: e, Report: replayReportCopy(r), SnapshotError: snapshotErr})
			})
		}
		return &r, re
	}
	if m.Codec == nil || m.Codec.Metadata == nil {
		return fail(&ModelError{"model requires metadata callback"}, true)
	}
	if trace == nil {
		return fail(&ModelError{"nil trace"}, true)
	}
	identity, e := call("metadata", func() (Metadata, error) {
		v, e := m.Codec.Metadata()
		if e == nil {
			e = v.Validate()
		}
		return v, e
	})
	if e != nil {
		return fail(e, true)
	}
	r.BuildMatches = identity.Build == trace.Metadata.Build
	if identity.Name != trace.Metadata.Name || identity.ModelVersion != trace.Metadata.ModelVersion || identity.PropertiesVersion != trace.Metadata.PropertiesVersion || identity.CodecVersion != trace.Metadata.CodecVersion || !r.BuildMatches && !allow {
		r.Outcome = "incompatible"
		return &r, nil
	}
	if e := m.validateCodec(); e != nil {
		return fail(e, true)
	}
	if m.Step == nil {
		return fail(&ModelError{"replay requires step callback"}, true)
	}

	if capture != nil {
		captured, captureError := capture()
		if captureError != nil {
			return fail(fmt.Errorf("bounded replay artifact: %w", captureError), true)
		}
		audit = captured
		trace = &captured.Trace
	}

	if limits != nil {
		if e = audit.WriteBinaryWithLimits(io.Discard, *limits); e != nil {
			return fail(fmt.Errorf("bounded replay artifact: %w", e), true)
		}
	}
	if e = ValidateRecording(trace); e != nil {
		return fail(e, true)
	}
	blob := uint64(math.MaxInt)
	if limits != nil {
		blob = min(blob, limits.MaxBlobBytes, limits.MaxFrameBytes, limits.MaxTotalBytes, uint64(math.MaxUint32))
	}
	state, e := call("decode initial state", func() (S, error) { return m.Codec.DecodeState(slices.Clone(trace.InitialState)) })
	if e != nil {
		return fail(e, true)
	}
	actual = &ReplayActual[S, I, O]{Initial: &ReplayInitialActual[S]{State: state}}
	var stateBytes, inputBytes []byte
	var checks []Check
	if e = encodeStateInto(m, state, &stateBytes, blob); e != nil {
		return fail(fmt.Errorf("encode initial state: %w", e), true)
	}
	if !bytes.Equal(stateBytes, trace.InitialState) {
		return fail(&ModelError{"initial state encoding is not canonical"}, true)
	}
	if e = stateChecksInto(m, state, &checks); e != nil {
		return fail(e, true)
	}
	actual.Initial.Checks = copyChecks(checks)
	actual.Initial.ChecksComplete = true
	budget := replayBudget{}
	if limits != nil {
		if e = budget.initial(audit, identity, stateBytes, checks, *limits); e != nil {
			return fail(fmt.Errorf("bounded replay initial: %w", e), true)
		}
	}
	r.FailureReproduced = sameFailure(trace.InitialChecks, checks)
	diff := ReplayDifferences{Checks: !slices.Equal(checks, trace.InitialChecks)}
	if diff.Checks {
		r.Outcome = "diverged"
		f := "initial checks"
		r.Field = &f
	}
	if observer.Initial != nil {
		snap, se := replaySnapshot(m, actual)
		if se != nil {
			unavailableSnapshot = se
			return fail(se, true)
		}
		_, e = call("replay observer", func() (struct{}, error) {
			return struct{}{}, observer.Initial(ReplayInitialObservation[S]{*snap.Initial, slices.Clone(trace.InitialState), slices.Clone(stateBytes), copyChecks(trace.InitialChecks), diff, replayReportCopy(r)})
		})
		if e != nil {
			return fail(e, false)
		}
	}
	if diff.Checks {
		return &r, nil
	}
	for index, expected := range trace.Steps {
		sequence = uint64(index + 1)
		actual = nil
		input, e := call("decode input", func() (I, error) { return m.Codec.DecodeInput(slices.Clone(expected.Input)) })
		if e != nil {
			return fail(e, true)
		}
		if e = encodeInputInto(m, input, &inputBytes, blob); e != nil {
			return fail(fmt.Errorf("encode input: %w", e), true)
		}
		if !bytes.Equal(inputBytes, expected.Input) {
			return fail(&ModelError{fmt.Sprintf("input %d encoding is not canonical", index+1)}, true)
		}
		before, e := copied(m.CloneState, state)
		if e != nil {
			return fail(e, true)
		}
		stepInput, e := copied(m.CloneInput, input)
		if e != nil {
			return fail(e, true)
		}
		transition, e := call("transition", func() (Transition[S, O], error) { return m.Step(before, stepInput) })
		if e != nil {
			return fail(e, true)
		}
		actual = &ReplayActual[S, I, O]{Turn: &ReplayTurnActual[S, I, O]{Sequence: sequence, Before: state, Input: input, Transition: transition}}
		owned, e := cloneTransition(m, transition)
		if e != nil {
			unavailableSnapshot = e
			return fail(e, true)
		}
		actual.Turn.Transition = owned
		transition = owned
		resetChecks(&checks)
		if e = stateChecksInto(m, transition.State, &checks); e != nil {
			return fail(e, true)
		}
		n := len(checks)
		if e = edgeChecksInto(m, state, input, transition, &checks); e != nil {
			return fail(e, true)
		}
		actual.Turn.Checks = copyChecks(checks)
		actual.Turn.StateCheckCount = &n
		remaining := uint64(math.MaxInt)
		if limits != nil {
			remaining, e = budget.step(len(inputBytes), transition.Disposition, len(transition.Outputs), checks, *limits)
			if e != nil {
				return fail(e, true)
			}
		}
		r.FailureReproduced = r.FailureReproduced || sameFailure(expected.Checks, checks)
		outputs := make([][]byte, len(transition.Outputs))
		for k, o := range transition.Outputs {
			if e = encodeOutputInto(m, o, &outputs[k], min(blob, remaining)); e != nil {
				return fail(fmt.Errorf("encode output: %w", e), true)
			}
			remaining -= uint64(len(outputs[k]))
			if limits != nil {
				budget.total += uint64(len(outputs[k]))
			}
		}
		if e = encodeStateInto(m, transition.State, &stateBytes, min(blob, remaining)); e != nil {
			return fail(fmt.Errorf("encode state: %w", e), true)
		}
		if limits != nil {
			budget.total += uint64(len(stateBytes))
		}
		diff = ReplayDifferences{transition.Disposition != expected.Disposition, !slices.EqualFunc(outputs, expected.Outputs, bytes.Equal), !bytes.Equal(stateBytes, expected.PostState), !slices.Equal(checks, expected.Checks)}
		field := diff.Primary()
		if field != "" {
			r.Outcome = "diverged"
			n := index + 1
			r.Step = &n
			r.Field = &field
		} else {
			r.StepsVerified++
		}
		if observer.Turn != nil {
			snap, se := replaySnapshot(m, actual)
			if se != nil {
				unavailableSnapshot = se
				return fail(se, true)
			}
			expectedCopy := clonePortableTrace(Trace{Steps: []TraceStep{expected}}).Steps[0]
			_, e = call("replay observer", func() (struct{}, error) {
				return struct{}{}, observer.Turn(ReplayTurnObservation[S, I, O]{*snap.Turn, expectedCopy, slices.Clone(inputBytes), outputs, slices.Clone(stateBytes), diff, replayReportCopy(r)})
			})
			if e != nil {
				return fail(e, false)
			}
		}
		if field != "" {
			return &r, nil
		}
		state = transition.State
	}
	return &r, nil
}

type replayBudget struct{ total, items, footer uint64 }

func (b *replayBudget) initial(t *AuditTrace, metadata Metadata, state []byte, checks []Check, l BinaryLimits) error {
	b.footer = 18
	run := *t
	run.Metadata = metadata
	run.InitialState = state
	run.InitialChecks = checks
	b.items = 0
	e := auditEncoder{limits: l, items: &b.items}
	auditEncodeRun(&e, &run)
	if e.err != nil {
		return e.err
	}
	b.total = 12
	if err := auditAdd(&b.total, e.size+9, l.MaxTotalBytes, "total bytes"); err != nil {
		return err
	}
	total := b.total
	return auditAdd(&total, b.footer, l.MaxTotalBytes, "total bytes")
}
func (b *replayBudget) step(input int, d Disposition, outputs int, checks []Check, l BinaryLimits) (uint64, error) {
	// Measure fixed framing and all checks without allocating output storage.
	items := uint64(0)
	e := auditEncoder{limits: l, items: &items}
	e.u64(0)
	e.u32(uint64(input))
	if e.err == nil {
		e.err = auditAdd(&e.size, uint64(input), min(l.MaxFrameBytes, uint64(math.MaxUint32)), "frame bytes")
	}
	if e.err == nil {
		e.err = d.Validate()
	}
	e.u8(0)
	if d.Kind != "accepted" {
		e.str(d.Reason)
	}
	e.count(outputs, l.MaxOutputsPerStep, "outputs per step")
	if e.err == nil {
		e.err = auditAdd(&e.size, uint64(outputs)*4, min(l.MaxFrameBytes, uint64(math.MaxUint32)), "frame bytes")
	}
	e.u32(0)
	e.checks(checks)
	if e.err != nil {
		return 0, e.err
	}
	if err := auditAdd(&items, 1, l.MaxItems, "aggregate items"); err != nil {
		return 0, err
	}
	if err := auditAdd(&b.items, items, l.MaxItems, "aggregate items"); err != nil {
		return 0, err
	}
	total := b.total
	if err := auditAdd(&total, b.footer, l.MaxTotalBytes, "total bytes"); err != nil {
		return 0, err
	}
	if err := auditAdd(&total, 9, l.MaxTotalBytes, "total bytes"); err != nil {
		return 0, err
	}
	frame := min(l.MaxFrameBytes, uint64(math.MaxUint32), l.MaxTotalBytes-total)
	if e.size > frame {
		return 0, auditError("limit_exceeded", "frame bytes")
	}
	b.total += e.size + 9
	return frame - e.size, nil
}
