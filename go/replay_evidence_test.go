package statelessness_test

import (
	"errors"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"strings"
	"testing"
)

func replayTrace(t *testing.T) *s.Trace {
	t.Helper()
	tr, e := s.Record(counter(), s.Values([]string{"increment", "increment"}), 10)
	if e != nil {
		t.Fatal(e)
	}
	return tr
}
func TestReplayEvidenceAllDifferencesAndStop(t *testing.T) {
	tr := replayTrace(t)
	m := counter()
	steps := 0
	m.Step = func(st counterState, _ string) (s.Transition[counterState, string], error) {
		steps++
		return s.Transition[counterState, string]{State: counterState{9}, Outputs: []string{"different"}, Disposition: s.Rejected("no")}, nil
	}
	initial, turn := 0, 0
	r, e := s.ReplayWithObservations(m, tr, false, s.ReplayObserver[counterState, string, string]{Initial: func(o s.ReplayInitialObservation[counterState]) error {
		initial++
		if o.Report.StepsVerified != 0 || !o.Actual.ChecksComplete {
			t.Fatal(o)
		}
		o.ActualState[0] = 'x'
		return nil
	}, Turn: func(o s.ReplayTurnObservation[counterState, string, string]) error {
		turn++
		if !o.Differences.Disposition || !o.Differences.Outputs || !o.Differences.State || !o.Differences.Checks || o.Report.Outcome != "diverged" || o.Actual.StateCheckCount == nil || *o.Actual.StateCheckCount != 1 {
			t.Fatal(o)
		}
		o.Expected.Input[0] = 'x'
		return nil
	}})
	if e != nil || r.Outcome != "diverged" || *r.Field != "disposition" || r.StepsVerified != 0 || steps != 1 || initial != 1 || turn != 1 || string(tr.Steps[0].Input) != "increment" {
		t.Fatalf("%+v %v %d %d %d", r, e, steps, initial, turn)
	}
}
func TestReplayEvidenceObserverErrorsAndUpdatedReport(t *testing.T) {
	tr := replayTrace(t)
	notifications := 0
	r, e := s.ReplayWithObservations(counter(), tr, false, s.ReplayObserver[counterState, string, string]{Turn: func(o s.ReplayTurnObservation[counterState, string, string]) error {
		if o.Report.StepsVerified != 1 {
			t.Fatal(o.Report)
		}
		return fmt.Errorf("stop")
	}, Error: func(s.ReplayErrorObservation[counterState, string, string]) error { notifications++; return nil }})
	var re *s.ReplayError[counterState, string, string]
	if !errors.As(e, &re) || r.StepsVerified != 1 || re.Actual.Turn == nil || re.Actual.Turn.StateCheckCount == nil || notifications != 0 || re.ObserverError != nil {
		t.Fatalf("%+v %#v %d", r, re, notifications)
	}
}
func TestReplayEvidenceErrorStages(t *testing.T) {
	for _, stage := range []string{"decode", "transition", "checks", "encode", "snapshot"} {
		t.Run(stage, func(t *testing.T) {
			tr := replayTrace(t)
			m := counter()
			steps := 0
			cloneCalls := 0
			original := m.Step
			m.Step = func(st counterState, i string) (s.Transition[counterState, string], error) {
				steps++
				if stage == "transition" {
					return s.Transition[counterState, string]{}, fmt.Errorf("reducer")
				}
				return original(st, i)
			}
			switch stage {
			case "decode":
				m.Codec.DecodeInput = func([]byte) (string, error) { return "", fmt.Errorf("decoder") }
			case "checks":
				m.CheckTransition = func(counterState, string, s.Transition[counterState, string]) ([]s.Check, error) {
					return nil, fmt.Errorf("checker")
				}
			case "encode":
				m.Codec.EncodeOutput = func(string) ([]byte, error) { return nil, fmt.Errorf("encoder") }
			case "snapshot":
				m.CloneOutput = func(string) string { cloneCalls++; panic("clone") }
			}
			observed := 0
			_, e := s.ReplayWithObservations(m, tr, false, s.ReplayObserver[counterState, string, string]{Error: func(o s.ReplayErrorObservation[counterState, string, string]) error {
				observed++
				if stage == "snapshot" && (o.Actual != nil || o.SnapshotError == nil) {
					t.Fatal("snapshot failure observer must mark unavailable actual", o)
				}
				return fmt.Errorf("notification")
			}})
			var re *s.ReplayError[counterState, string, string]
			if !errors.As(e, &re) || re.Sequence != 1 {
				t.Fatalf("%#v %v", re, e)
			}
			if stage == "decode" || stage == "transition" {
				if re.Actual != nil {
					t.Fatal("unexpected actual")
				}
			} else {
				if re.Actual == nil || re.Actual.Turn == nil {
					t.Fatal("lost actual")
				}
				complete := re.Actual.Turn.StateCheckCount != nil
				if complete != (stage == "encode") {
					t.Fatalf("check completeness %v", complete)
				}
			}
			if observed != 1 || re.ObserverError == nil {
				t.Fatalf("missing secondary %d %#v", observed, re)
			}
			if stage == "snapshot" && (cloneCalls != 1 || re.SnapshotError == nil || re.Actual.Turn.Transition.Outputs[0] != "changed") {
				t.Fatalf("lost raw actual or retried failed clone: calls=%d error=%#v", cloneCalls, re)
			}
			if steps > 1 {
				t.Fatal("repeated reducer")
			}
		})
	}
}
func TestReplayEvidenceCanonicalIdentityAndFailure(t *testing.T) {
	tr := replayTrace(t)
	m := counter()
	decode := 0
	m.Codec.DecodeState = func([]byte) (counterState, error) { decode++; return counterState{}, nil }
	tr.Metadata.Name = "foreign"
	r, e := s.ReplayWithObservations(m, tr, false, s.ReplayObserver[counterState, string, string]{})
	if e != nil || r.Outcome != "incompatible" || decode != 0 {
		t.Fatal(r, e, decode)
	}
	tr = replayTrace(t)
	tr.InitialState = []byte("00")
	_, e = s.ReplayWithObservations(counter(), tr, false, s.ReplayObserver[counterState, string, string]{})
	var re *s.ReplayError[counterState, string, string]
	if !errors.As(e, &re) || !strings.Contains(e.Error(), "canonical") || re.Actual.Initial.ChecksComplete {
		t.Fatal(e)
	}
	tr, e = s.Record(counter(), s.Values([]string{"increment", "increment", "increment"}), 10)
	if e != nil {
		t.Fatal(e)
	}
	m = counter()
	original := m.CheckState
	m.CheckState = func(st counterState) ([]s.Check, error) {
		checks, e := original(st)
		if st.value > 2 {
			checks[0].Details = "another detail"
		}
		return checks, e
	}
	r, e = s.ReplayWithObservations(m, tr, false, s.ReplayObserver[counterState, string, string]{})
	if e != nil || !r.FailureReproduced || r.Outcome != "diverged" || *r.Field != "checks" {
		t.Fatal(r, e)
	}
}
func TestReplayEvidenceBoundedPrevalidationAndActualBudget(t *testing.T) {
	tr := replayTrace(t)
	audit, e := s.AuditFromPortable(*tr, s.RunConfig{Strategy: "replay"})
	if e != nil {
		t.Fatal(e)
	}
	m := counter()
	decode := 0
	originalDecode := m.Codec.DecodeState
	m.Codec.DecodeState = func(b []byte) (counterState, error) { decode++; return originalDecode(b) }
	limits := s.DefaultBinaryLimits()
	limits.MaxSteps = 0
	_, e = s.ReplayWithObservationsBounded(m, audit, false, limits, s.ReplayObserver[counterState, string, string]{})
	if e == nil || decode != 0 {
		t.Fatal(e, decode)
	}
	limits = s.DefaultBinaryLimits()
	limits.MaxBlobBytes = 9
	m = counter()
	m.Step = func(st counterState, i string) (s.Transition[counterState, string], error) {
		return s.Accept[counterState, string](counterState{st.value + 1}, strings.Repeat("x", 10)), nil
	}
	_, e = s.ReplayWithObservationsBounded(m, audit, false, limits, s.ReplayObserver[counterState, string, string]{})
	var re *s.ReplayError[counterState, string, string]
	if !errors.As(e, &re) || re.Actual == nil || re.Actual.Turn == nil || re.Actual.Turn.StateCheckCount == nil {
		t.Fatalf("%v %#v", e, re)
	}
}

func TestReplayBoundedReservesMinimalFooter(t *testing.T) {
	tr := replayTrace(t)
	tr.Steps = nil
	tr.InitialChecks = []s.Check{s.Skipped("bound", "")}
	tr.Termination = "model_error"
	tr.Error = strings.Repeat("e", 100)
	audit, e := s.AuditFromPortable(*tr, s.RunConfig{Strategy: "replay"})
	if e != nil {
		t.Fatal(e)
	}
	wire, e := s.MarshalAuditTrace(audit, s.DefaultBinaryLimits())
	if e != nil {
		t.Fatal(e)
	}
	m := counter()
	m.CheckState = func(counterState) ([]s.Check, error) {
		return []s.Check{s.Skipped("bound", strings.Repeat("x", 124))}, nil
	}
	limits := s.DefaultBinaryLimits()
	limits.MaxTotalBytes = uint64(len(wire) + 20)
	r, e := s.ReplayWithObservationsBounded(m, audit, false, limits, s.ReplayObserver[counterState, string, string]{})
	if e != nil || r.Outcome != "diverged" {
		t.Fatal(r, e)
	}
	limits.MaxTotalBytes--
	_, e = s.ReplayWithObservationsBounded(m, audit, false, limits, s.ReplayObserver[counterState, string, string]{})
	if e == nil {
		t.Fatal("actual initial quota not enforced")
	}
}

func TestReplayEvidenceDetachedMutableObservations(t *testing.T) {
	clone := func(b []byte) []byte { return append([]byte{}, b...) }
	m := s.Model[[]byte, []byte, []byte]{InitialState: func() ([]byte, error) { return []byte{0}, nil }, Step: func(st, in []byte) (s.Transition[[]byte, []byte], error) {
		st[0] += in[0]
		return s.Accept[[]byte, []byte](st, []byte{st[0]}), nil
	}, CheckState: func([]byte) ([]s.Check, error) { return []s.Check{s.Passed("ok")}, nil }, CloneState: clone, CloneInput: clone, CloneOutput: clone, Codec: &s.Codec[[]byte, []byte, []byte]{Metadata: func() (s.Metadata, error) { return s.Identity("mutable", "v1"), nil }, EncodeState: func(b []byte) ([]byte, error) { return clone(b), nil }, DecodeState: func(b []byte) ([]byte, error) { return clone(b), nil }, EncodeInput: func(b []byte) ([]byte, error) { return clone(b), nil }, DecodeInput: func(b []byte) ([]byte, error) { return clone(b), nil }, EncodeOutput: func(b []byte) ([]byte, error) { return clone(b), nil }}}
	tr, e := s.Record(m, s.Values([][]byte{{1}, {1}}), 10)
	if e != nil {
		t.Fatal(e)
	}
	var retained []s.ReplayTurnObservation[[]byte, []byte, []byte]
	r, e := s.ReplayWithObservations(m, tr, false, s.ReplayObserver[[]byte, []byte, []byte]{Initial: func(o s.ReplayInitialObservation[[]byte]) error { o.Actual.State[0] = 99; return nil }, Turn: func(o s.ReplayTurnObservation[[]byte, []byte, []byte]) error {
		retained = append(retained, o)
		o.Actual.Before[0] = 88
		o.Actual.Input[0] = 88
		o.Actual.Transition.State[0] = 88
		o.Actual.Transition.Outputs[0][0] = 88
		o.Expected.PostState[0] = 88
		return nil
	}})
	if e != nil || r.Outcome != "exact" || r.StepsVerified != 2 || len(retained) != 2 {
		t.Fatal(r, e)
	}
	if retained[0].ActualState[0] != 1 || retained[1].ActualState[0] != 2 || tr.Steps[0].PostState[0] != 1 {
		t.Fatal("observations alias replay or stored evidence")
	}
}

func TestReplayIncompatibleDoesNotRequireExecutionHooks(t *testing.T) {
	tr := replayTrace(t)
	m := s.Model[counterState, string, string]{Codec: &s.Codec[counterState, string, string]{Metadata: func() (s.Metadata, error) { return s.Identity("foreign", "v1"), nil }}}
	r, e := s.ReplayWithObservations(m, tr, false, s.ReplayObserver[counterState, string, string]{})
	if e != nil || r.Outcome != "incompatible" {
		t.Fatal(r, e)
	}
}
