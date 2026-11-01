package statelessness_test

import (
	"encoding/binary"
	"errors"
	"fmt"
	"reflect"
	"slices"
	"strings"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

// continued-failure-evidence-v1. Counter: state += input, outputs
// [input, state]; state check high fails at state >= 3; transition check
// big_step fails for input >= 5.
func continuedBytes(v int) []byte { return binary.LittleEndian.AppendUint64(nil, uint64(int64(v))) }
func continuedInt(b []byte) (int, error) {
	if len(b) != 8 {
		return 0, fmt.Errorf("bad integer")
	}
	return int(int64(binary.LittleEndian.Uint64(b))), nil
}
func continuedCodec(name string, version uint32) *s.Codec[int, int, int] {
	encode := func(v int) ([]byte, error) { return continuedBytes(v), nil }
	return &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) {
		return s.Metadata{Name: name, ModelVersion: version, PropertiesVersion: 1, CodecVersion: 1}, nil
	},
		EncodeState: encode, DecodeState: continuedInt, EncodeInput: encode, DecodeInput: continuedInt, EncodeOutput: encode}
}
func counterModel(version uint32, throwAt int) s.Model[int, int, int] {
	return s.Model[int, int, int]{InitialState: func() (int, error) { return 0, nil },
		Step: func(state, input int) (s.Transition[int, int], error) {
			return s.Accept(state+input, input, state+input), nil
		},
		CheckState: func(state int) ([]s.Check, error) {
			if state == throwAt {
				return nil, fmt.Errorf("checker exploded")
			}
			if state >= 3 {
				return []s.Check{s.Failed("high", fmt.Sprint(state))}, nil
			}
			return []s.Check{s.Passed("high")}, nil
		},
		CheckTransition: func(_, input int, _ s.Transition[int, int]) ([]s.Check, error) {
			if input >= 5 {
				return []s.Check{s.Failed("big_step", fmt.Sprint(input))}, nil
			}
			return nil, nil
		},
		CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		Inputs:      func(int) (s.Iterator[int], error) { return s.Values([]int{1, 5}), nil },
		EqualStates: func(a, b int) bool { return a == b }, HashState: func(state int) uint64 { return uint64(state) },
		Codec: continuedCodec("counter", version)}
}
func counterGenerate() s.GenerateModel[int, int, int] {
	return s.GenerateModel[int, int, int]{Model: counterModel(1, -1), InputEqual: func(a, b int) (bool, error) { return a == b, nil }}
}

// trigger: 0 unarmed, 1 armed, 2 fired. Input 0 waits, 1 arms (warning), 2
// fires and is enabled only when armed.
func triggerGenerate() s.GenerateModel[int, int, int] {
	m := s.Model[int, int, int]{InitialState: func() (int, error) { return 0, nil },
		Step: func(state, input int) (s.Transition[int, int], error) {
			switch {
			case input == 1 && state == 0:
				return s.Accept[int, int](1), nil
			case input == 2 && state == 1:
				return s.Accept[int, int](2), nil
			}
			return s.Accept[int, int](state), nil
		},
		CheckState: func(state int) ([]s.Check, error) {
			if state == 2 {
				return []s.Check{s.Failed("fired", "boom")}, nil
			}
			return []s.Check{s.Passed("fired")}, nil
		},
		CheckTransition: func(state, input int, _ s.Transition[int, int]) ([]s.Check, error) {
			if input == 1 && state == 0 {
				return []s.Check{s.Failed("armed_warning", "armed")}, nil
			}
			return nil, nil
		},
		CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		Inputs: func(state int) (s.Iterator[int], error) {
			switch state {
			case 0:
				return s.Values([]int{0, 1}), nil
			case 1:
				return s.Values([]int{0, 2}), nil
			}
			return s.Values[int](nil), nil
		},
		EqualStates: func(a, b int) bool { return a == b }, HashState: func(state int) uint64 { return uint64(state) },
		Codec: continuedCodec("trigger", 1)}
	return s.GenerateModel[int, int, int]{Model: m, IsEnabled: func(state, input int) (bool, error) { return input != 2 || state == 1, nil },
		InputEqual: func(a, b int) (bool, error) { return a == b, nil }}
}

var continuedWitness = []int{1, 1, 1, 1, 5, 0, 7}

func recordCounter(t *testing.T, m s.Model[int, int, int], inputs []int) *s.ContinuedTrace {
	t.Helper()
	trace, e := s.RecordContinued(m, s.Values(inputs), 100_000)
	if e != nil {
		t.Fatal(e)
	}
	return trace
}
func rebuilt(trace *s.ContinuedTrace, edit func(steps []s.TraceStep)) *s.ContinuedTrace {
	edited := *trace
	edited.Trace.Steps = slices.Clone(trace.Trace.Steps)
	edit(edited.Trace.Steps)
	return &edited
}
func replayCounter(t *testing.T, m s.Model[int, int, int], trace *s.ContinuedTrace) *s.ContinuedReplayReport {
	t.Helper()
	r, e := s.ReplayContinued(m, trace, false)
	if e != nil {
		t.Fatal(e)
	}
	return r
}
func violation(phase, id string) s.Violation {
	return s.Violation{Phase: phase, Check: s.Failed(id, "")}
}
func findingSequences(findings []s.ContinuedFinding) []uint64 {
	result := []uint64{}
	for _, f := range findings {
		result = append(result, f.Sequence)
	}
	return result
}

func TestContinuedRecordContinuesPastFailuresAndOldReadersReject(t *testing.T) {
	trace := recordCounter(t, counterModel(1, -1), continuedWitness)
	if len(trace.Trace.Steps) != 7 || trace.Trace.Termination != "completed" {
		t.Fatalf("%+v", trace.Trace)
	}
	expectEqual(t, "failing", trace.FailingSequences(), []uint64{3, 4, 5, 6, 7})
	if e := s.ValidateContinuedRecording(trace); e != nil {
		t.Fatal(e)
	}
	if e := s.ValidateRecording(&trace.Trace); e == nil || !strings.Contains(e.Error(), "continues after property failure") {
		t.Fatal(e)
	}
	if _, e := s.Replay(counterModel(1, -1), &trace.Trace, false); e == nil {
		t.Fatal("stop-at-failure replay accepted a continued trace")
	}
	// The stop-at-failure recorder ends at the first failure, unchanged.
	legacy, e := s.Record(counterModel(1, -1), s.Values(continuedWitness), 100_000)
	if e != nil || len(legacy.Steps) != 3 || legacy.Termination != "property_failed" {
		t.Fatal(e, legacy)
	}
	encoded, e := s.MarshalContinuedTrace(trace)
	if e != nil {
		t.Fatal(e)
	}
	parsed, e := s.ParseContinuedTrace(encoded)
	if e != nil || !reflect.DeepEqual(parsed, trace) {
		t.Fatalf("%v\n%+v\n%+v", e, parsed, trace)
	}
	// trace-json v1 readers reject the artifact (unknown field or format), never reinterpret it.
	var format *s.FormatError
	if _, e := s.ParseTrace(encoded); !errors.As(e, &format) {
		t.Fatal(e)
	}
	legacyEncoded, e := s.MarshalTrace(legacy)
	if e != nil {
		t.Fatal(e)
	}
	if _, e := s.ParseContinuedTrace(legacyEncoded); e == nil {
		t.Fatal("continued reader accepted trace-json v1")
	}
}

func TestContinuedReplayComparesEveryStep(t *testing.T) {
	m := counterModel(1, -1)
	trace := recordCounter(t, m, continuedWitness)
	exact := replayCounter(t, m, trace)
	if !exact.CompleteReproduction || exact.Replay.StepsVerified != 7 {
		t.Fatalf("%+v", exact)
	}
	expectEqual(t, "reproduced", exact.ReproducedFailures, []uint64{3, 4, 5, 6, 7})
	// Corrupted outputs at a later step: the verified prefix is not complete reproduction.
	corrupt := replayCounter(t, m, rebuilt(trace, func(steps []s.TraceStep) {
		steps[4].Outputs = [][]byte{continuedBytes(5), continuedBytes(0)}
	}))
	if corrupt.CompleteReproduction || corrupt.Replay.Outcome != "diverged" || *corrupt.Replay.Step != 5 || *corrupt.Replay.Field != "outputs" {
		t.Fatalf("%+v", corrupt)
	}
	// Reordered effects diverge.
	reordered := replayCounter(t, m, rebuilt(trace, func(steps []s.TraceStep) {
		steps[1].Outputs = [][]byte{steps[1].Outputs[1], steps[1].Outputs[0]}
	}))
	if *reordered.Replay.Field != "outputs" || reordered.CompleteReproduction {
		t.Fatalf("%+v", reordered)
	}
	// A suppressed later failure diverges on checks.
	suppressed := replayCounter(t, m, rebuilt(trace, func(steps []s.TraceStep) {
		steps[6].Checks = []s.Check{s.Passed("high")}
	}))
	if suppressed.CompleteReproduction || *suppressed.Replay.Field != "checks" {
		t.Fatalf("%+v", suppressed)
	}
	expectEqual(t, "recorded", suppressed.RecordedFailures, []uint64{3, 4, 5, 6})
	// Incompatible model version.
	incompatible := replayCounter(t, counterModel(2, -1), trace)
	if incompatible.Replay.Outcome != "incompatible" || incompatible.CompleteReproduction {
		t.Fatalf("%+v", incompatible)
	}
}

func TestContinuedCorruptedAndTruncatedArtifactsAreRejected(t *testing.T) {
	encoded, e := s.MarshalContinuedTrace(recordCounter(t, counterModel(1, -1), continuedWitness))
	if e != nil {
		t.Fatal(e)
	}
	if _, e := s.ParseContinuedTrace(encoded[:len(encoded)/2]); e == nil {
		t.Fatal("truncated artifact accepted")
	}
	text := string(encoded)
	if !strings.Contains(text, `"failing_sequences":[3,4,5,6,7]`) {
		t.Fatal(text)
	}
	if _, e := s.ParseContinuedTrace([]byte(strings.Replace(text, `"failing_sequences":[3,4,5,6,7]`, `"failing_sequences":[3,4]`, 1))); e == nil || !strings.Contains(e.Error(), "failing_sequences disagree") {
		t.Fatal(e)
	}
	if _, e := s.ParseContinuedTrace([]byte(strings.Replace(text, `"version":1`, `"version":2`, 1))); e == nil {
		t.Fatal("other version accepted")
	}
	failed := &s.ContinuedTrace{Trace: s.Trace{Metadata: s.Identity("counter", ""), InitialState: continuedBytes(0), InitialChecks: []s.Check{}, Steps: []s.TraceStep{}, Termination: "property_failed"}}
	if _, e := s.MarshalContinuedTrace(failed); e == nil {
		t.Fatal("property_failed continued trace encoded")
	}
}

func TestContinuedIncompleteChecksAfterFailure(t *testing.T) {
	trace := recordCounter(t, counterModel(1, 4), []int{1, 1, 1, 1, 1})
	if trace.Trace.Termination != "model_error" || len(trace.Trace.Steps) != 3 || !strings.Contains(trace.Trace.Error, "checker exploded") {
		t.Fatalf("%+v", trace.Trace)
	}
	expectEqual(t, "failing", trace.FailingSequences(), []uint64{3})
	encoded, e := s.MarshalContinuedTrace(trace)
	if e != nil {
		t.Fatal(e)
	}
	parsed, e := s.ParseContinuedTrace(encoded)
	if e != nil || !reflect.DeepEqual(parsed, trace) {
		t.Fatal(e, parsed)
	}
}

func TestContinuedTwoFailuresOnOneTurn(t *testing.T) {
	g := counterGenerate()
	target := violation("transition", "big_step")
	r, e := s.ShrinkContinued(g, continuedWitness, target, s.DefaultShrinkConfig64())
	if e != nil {
		t.Fatal(e)
	}
	if !r.Base.ValidatedOriginal || r.Base.Termination != "search_complete" || !slices.Equal(r.Base.Original.Inputs, continuedWitness) || !slices.Equal(r.Base.Minimized.Inputs, []int{7}) {
		t.Fatalf("%+v", r.Base)
	}
	expectEqual(t, "original", findingSequences(r.OriginalFindings), []uint64{3, 4, 5, 6, 7})
	expectEqual(t, "minimized", r.MinimizedFindings, []s.ContinuedFinding{{Sequence: 1, Violations: []s.Violation{{Phase: "state", Check: s.Failed("high", "7")}, {Phase: "transition", Check: s.Failed("big_step", "7")}}}})
	// Stop-at-failure shrinking cannot reach the later target.
	if _, e := s.Shrink64(g, s.Failure[int]{Inputs: continuedWitness, Violations: []s.Violation{target}}, s.DefaultShrinkConfig64()); e == nil {
		t.Fatal("stop-at-failure shrinking reproduced a later target")
	}
}

func TestContinuedSelectedLaterTargetOnSuccessiveTurns(t *testing.T) {
	r, e := s.ShrinkContinued(triggerGenerate(), []int{0, 1, 0, 2}, violation("state", "fired"), s.DefaultShrinkConfig64())
	if e != nil {
		t.Fatal(e)
	}
	expectEqual(t, "minimized", r.Base.Minimized.Inputs, []int{1, 2})
	expectEqual(t, "findings", r.MinimizedFindings, []s.ContinuedFinding{
		{Sequence: 1, Violations: []s.Violation{{Phase: "transition", Check: s.Failed("armed_warning", "armed")}}},
		{Sequence: 2, Violations: []s.Violation{{Phase: "state", Check: s.Failed("fired", "boom")}}}})
	outcomes := []string{}
	for _, h := range r.Base.History {
		outcomes = append(outcomes, h.Outcome)
	}
	if !slices.Contains(outcomes, "invalid_causality") || !slices.Contains(outcomes, "different_failure") {
		t.Fatal(outcomes)
	}
	// A target that does not terminate the witness is a wrong target.
	if _, e := s.ShrinkContinued(triggerGenerate(), []int{0, 1, 0, 2}, violation("transition", "armed_warning"), s.DefaultShrinkConfig64()); e == nil || !strings.Contains(e.Error(), "does not reproduce") {
		t.Fatal(e)
	}
}

func TestContinuedBudgetKeepsOriginalAndMinimizedWitnesses(t *testing.T) {
	r, e := s.ShrinkContinued(counterGenerate(), continuedWitness, violation("transition", "big_step"), s.ShrinkConfig64{MaxAttempts: 2})
	if e != nil {
		t.Fatal(e)
	}
	if r.Base.Termination != "attempt_limit" || !slices.Equal(r.Base.Original.Inputs, continuedWitness) || !slices.Equal(r.Base.Minimized.Inputs, []int{5, 0, 7}) {
		t.Fatalf("%+v", r.Base)
	}
	if last := r.MinimizedFindings[len(r.MinimizedFindings)-1]; last.Sequence != 3 {
		t.Fatal(r.MinimizedFindings)
	}
}

func TestContinuedGraphFindingsFlowIntoContinuedEvidence(t *testing.T) {
	g := triggerGenerate()
	report, e := s.ExploreGraph(g.Model, s.DefaultSearchConfig(), s.SearchLimits{}, s.GraphSearchOptions[int, int, int]{FailurePolicy: s.CollectFailures(10, true)})
	if e != nil {
		t.Fatal(e)
	}
	expectEqual(t, "witnesses", witnesses(report), [][]int{{1}, {1, 2}})
	if report.Findings[0].RequiresContinuedEvidence || !report.Findings[1].RequiresContinuedEvidence {
		t.Fatal(report.Findings)
	}
	later := report.Findings[1]
	shrunk, e := s.ShrinkContinued(g, later.Witness, later.Violations[0], s.DefaultShrinkConfig64())
	if e != nil || !slices.Equal(shrunk.Base.Minimized.Inputs, []int{1, 2}) {
		t.Fatal(e, shrunk)
	}
	trace := recordCounter(t, g.Model, later.Witness)
	expectEqual(t, "failing", trace.FailingSequences(), []uint64{1, 2})
	if !replayCounter(t, g.Model, trace).CompleteReproduction {
		t.Fatal("graph finding evidence did not reproduce")
	}
}
