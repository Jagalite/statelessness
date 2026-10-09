package statelessness_test

import (
	"bytes"
	"errors"
	"fmt"
	"reflect"
	"strconv"
	"strings"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

type counterState struct{ value int }

func counter() s.Model[counterState, string, string] {
	return s.Model[counterState, string, string]{
		InitialState: func() (counterState, error) { return counterState{}, nil },
		Step: func(state counterState, input string) (s.Transition[counterState, string], error) {
			if input != "increment" {
				return s.Transition[counterState, string]{}, fmt.Errorf("unknown input")
			}
			return s.Accept(counterState{state.value + 1}, "changed"), nil
		},
		CheckState: func(state counterState) ([]s.Check, error) {
			if state.value > 2 {
				return []s.Check{s.Failed("bound", "above two")}, nil
			}
			return []s.Check{s.Passed("bound")}, nil
		},
		CloneState: s.ValueCopy[counterState], CloneInput: s.ValueCopy[string], CloneOutput: s.ValueCopy[string],
		Inputs:      func(counterState) (s.Iterator[string], error) { return s.Values([]string{"increment"}), nil },
		EqualStates: func(a, b counterState) bool { return a == b }, HashState: func(counterState) uint64 { return 0 },
		Codec: &s.Codec[counterState, string, string]{
			Metadata:    func() (s.Metadata, error) { return s.Identity("counter", "native-test-v1"), nil },
			EncodeState: func(state counterState) ([]byte, error) { return []byte(strconv.Itoa(state.value)), nil },
			DecodeState: func(b []byte) (counterState, error) { v, e := strconv.Atoi(string(b)); return counterState{v}, e },
			EncodeInput: func(i string) ([]byte, error) { return []byte(i), nil }, DecodeInput: func(b []byte) (string, error) { return string(b), nil },
			EncodeOutput: func(o string) ([]byte, error) { return []byte(o), nil },
		},
	}
}
func must[T any](t *testing.T, value T, err error) T {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
	return value
}
func modelError(t *testing.T, err error) {
	t.Helper()
	var e *s.ModelError
	if !errors.As(err, &e) {
		t.Fatalf("expected ModelError, got %v", err)
	}
}
func TestNativeStructAndCollisionWitness(t *testing.T) {
	r, err := s.Enumerate(counter(), s.DefaultSearchConfig())
	r = must(t, r, err)
	if r.Termination != "failure_found" || r.States != 3 || r.Transitions != 3 || r.Failure.Violations[0].Phase != "state" {
		t.Fatalf("%+v", r)
	}
	if !reflect.DeepEqual(r.Failure.Inputs, []string{"increment", "increment", "increment"}) {
		t.Fatal(r.Failure)
	}
}
func TestRecordReplayJSONRoundtrip(t *testing.T) {
	trace, err := s.Record(counter(), s.Values([]string{"increment", "increment", "increment", "increment"}), 100)
	trace = must(t, trace, err)
	data, err := s.MarshalTrace(trace)
	data = must(t, data, err)
	decoded, err := s.ParseTrace(data)
	decoded = must(t, decoded, err)
	if !reflect.DeepEqual(trace, decoded) {
		t.Fatal("roundtrip differs")
	}
	r, err := s.Replay(counter(), decoded, false)
	r = must(t, r, err)
	if r.Outcome != "exact" || !r.FailureReproduced || r.StepsVerified != 3 {
		t.Fatalf("%+v", r)
	}
}
func TestObservationNeverSteps(t *testing.T) {
	m := counter()
	m.Step = func(counterState, string) (s.Transition[counterState, string], error) { panic("must not run") }
	checks, err := s.CheckObserved(m, counterState{}, "increment", s.Accept[counterState, string](counterState{1}), 1, s.FullChecks())
	checks = must(t, checks, err)
	if !reflect.DeepEqual(checks, []s.Check{s.Passed("bound")}) {
		t.Fatal(checks)
	}
}
func TestPeriodicPolicy(t *testing.T) {
	c, err := s.CheckObserved(counter(), counterState{}, "increment", s.Accept[counterState, string](counterState{3}), 1, s.CheckPolicy{StateEvery: 2})
	c = must(t, c, err)
	if len(c) != 2 || c[0].Status != "skipped" || c[1].Status != "skipped" {
		t.Fatal(c)
	}
}
func TestCheckErrorClearsPartialFindings(t *testing.T) {
	m := counter()
	m.CheckTransition = func(counterState, string, s.Transition[counterState, string]) ([]s.Check, error) {
		return nil, fmt.Errorf("broken checker")
	}
	c, err := s.CheckObserved(m, counterState{}, "increment", s.Accept[counterState, string](counterState{3}), 1, s.FullChecks())
	modelError(t, err)
	if c != nil {
		t.Fatal("partial checks escaped")
	}
}
func TestIteratorError(t *testing.T) {
	m := counter()
	m.Inputs = func(counterState) (s.Iterator[string], error) {
		n := 0
		return func() (string, bool, error) {
			n++
			if n == 1 {
				return "increment", true, nil
			}
			return "", false, fmt.Errorf("domain failed")
		}, nil
	}
	report, err := s.Enumerate(m, s.DefaultSearchConfig())
	modelError(t, err)
	if report != nil || !strings.Contains(err.Error(), "enumerate inputs: domain failed") {
		t.Fatal(report, err)
	}
}
func TestDepthProbesOnlyOne(t *testing.T) {
	m := counter()
	calls := 0
	m.Inputs = func(counterState) (s.Iterator[string], error) {
		return func() (string, bool, error) {
			calls++
			if calls > 1 {
				return "", false, fmt.Errorf("extra probe")
			}
			return "increment", true, nil
		}, nil
	}
	r, err := s.Enumerate(m, s.SearchConfig{MaxStates: 1, MaxDepth: 0})
	r = must(t, r, err)
	if r.Termination != "depth_bound" || calls != 1 {
		t.Fatal(r, calls)
	}
}
func TestPanicIsAnErrorNotAFinding(t *testing.T) {
	m := counter()
	m.Step = func(counterState, string) (s.Transition[counterState, string], error) { panic("bad callback") }
	r, err := s.Enumerate(m, s.DefaultSearchConfig())
	modelError(t, err)
	if r != nil || !strings.Contains(err.Error(), "bad callback") {
		t.Fatal(r, err)
	}
	trace, err := s.Record(m, s.Values([]string{"increment"}), 100)
	trace = must(t, trace, err)
	if trace.Termination != "model_error" || len(trace.Steps) != 0 {
		t.Fatal(trace)
	}
}
func TestInitialCallbackError(t *testing.T) {
	m := counter()
	m.InitialState = func() (counterState, error) { return counterState{}, fmt.Errorf("initial failed") }
	trace, err := s.Record(m, s.Values([]string{}), 100)
	modelError(t, err)
	if trace != nil {
		t.Fatal(trace)
	}
}
func TestLaterErrorCoherentPrefix(t *testing.T) {
	trace, err := s.Record(counter(), s.Values([]string{"increment", "unknown"}), 100)
	trace = must(t, trace, err)
	if trace.Termination != "model_error" || len(trace.Steps) != 1 {
		t.Fatal(trace)
	}
	r, err := s.Replay(counter(), trace, false)
	r = must(t, r, err)
	if r.Outcome != "exact" {
		t.Fatal(r)
	}
}
func TestCodecError(t *testing.T) {
	m := counter()
	m.Codec.EncodeState = func(counterState) ([]byte, error) { return nil, fmt.Errorf("broken codec") }
	_, err := s.Record(m, s.Values([]string{}), 100)
	modelError(t, err)
}
func TestNoncanonicalInitial(t *testing.T) {
	trace, err := s.Record(counter(), s.Values([]string{}), 100)
	trace = must(t, trace, err)
	trace.InitialState = []byte("00")
	_, err = s.Replay(counter(), trace, false)
	modelError(t, err)
	if !strings.Contains(err.Error(), "not canonical") {
		t.Fatal(err)
	}
}
func TestNoncanonicalInput(t *testing.T) {
	m := counter()
	m.Codec.DecodeInput = func(b []byte) (string, error) { return strings.TrimSpace(string(b)), nil }
	trace, err := s.Record(m, s.Values([]string{"increment"}), 100)
	trace = must(t, trace, err)
	trace.Steps[0].Input = []byte("increment ")
	_, err = s.Replay(m, trace, false)
	modelError(t, err)
}
func TestSavedCheckpointNotInitial(t *testing.T) {
	trace, err := s.Record(counter(), s.Values([]string{"increment"}), 100)
	trace = must(t, trace, err)
	m := counter()
	m.InitialState = func() (counterState, error) { panic("must not call") }
	r, err := s.Replay(m, trace, false)
	r = must(t, r, err)
	if r.Outcome != "exact" {
		t.Fatal(r)
	}
}
func TestBuildOverrideDoesNotWaiveVersions(t *testing.T) {
	trace, err := s.Record(counter(), s.Values([]string{}), 100)
	trace = must(t, trace, err)
	m := counter()
	m.Codec.Metadata = func() (s.Metadata, error) { return s.Identity("counter", "different"), nil }
	r, err := s.Replay(m, trace, false)
	r = must(t, r, err)
	if r.Outcome != "incompatible" {
		t.Fatal(r)
	}
	r, err = s.Replay(m, trace, true)
	r = must(t, r, err)
	if r.Outcome != "exact" || r.BuildMatches {
		t.Fatal(r)
	}
	m.Codec.Metadata = func() (s.Metadata, error) { v := s.Identity("counter", "different"); v.CodecVersion = 2; return v, nil }
	r, err = s.Replay(m, trace, true)
	r = must(t, r, err)
	if r.Outcome != "incompatible" {
		t.Fatal(r)
	}
}
func TestDuplicateFailureIDs(t *testing.T) {
	m := counter()
	m.CheckState = func(counterState) ([]s.Check, error) {
		return []s.Check{s.Failed("same", "one"), s.Failed("same", "two")}, nil
	}
	r, err := s.Enumerate(m, s.DefaultSearchConfig())
	r = must(t, r, err)
	if r.Failure.Violations[0].Check.Details != "one" || r.Failure.Violations[1].Check.Details != "two" {
		t.Fatal(r)
	}
}
func TestMutableStateBranches(t *testing.T) {
	type state struct{ Items []string }
	start := &state{}
	m := s.Model[*state, string, string]{InitialState: func() (*state, error) { return start, nil }, CloneState: func(s *state) *state { return &state{append([]string{}, s.Items...)} }, CloneInput: s.ValueCopy[string], CloneOutput: s.ValueCopy[string],
		Inputs: func(v *state) (s.Iterator[string], error) {
			if len(v.Items) > 0 {
				return s.Values([]string{}), nil
			}
			return s.Values([]string{"a", "b"}), nil
		},
		EqualStates: func(a, b *state) bool { return reflect.DeepEqual(a, b) },
		Step: func(v *state, i string) (s.Transition[*state, string], error) {
			v.Items = append(v.Items, i)
			return s.Accept[*state, string](v), nil
		},
		CheckState: func(v *state) ([]s.Check, error) {
			if len(v.Items) > 1 {
				return []s.Check{s.Failed("one", "aliased")}, nil
			}
			return []s.Check{s.Passed("one")}, nil
		},
	}
	r, err := s.Enumerate(m, s.DefaultSearchConfig())
	r = must(t, r, err)
	if r.Termination != "graph_exhausted" || r.States != 3 || len(start.Items) != 0 {
		t.Fatal(r, start)
	}
}
func TestReusedCodecBuffersAreCopied(t *testing.T) {
	m := counter()
	shared := []byte{0}
	m.Codec.EncodeState = func(v counterState) ([]byte, error) { shared[0] = byte(48 + v.value); return shared, nil }
	m.Codec.EncodeOutput = func(string) ([]byte, error) { shared[0] = 120; return shared, nil }
	trace, err := s.Record(m, s.Values([]string{"increment", "increment"}), 100)
	trace = must(t, trace, err)
	shared[0] = 255
	if !bytes.Equal(trace.InitialState, []byte("0")) || !bytes.Equal(trace.Steps[0].PostState, []byte("1")) || !bytes.Equal(trace.Steps[0].Outputs[0], []byte("x")) {
		t.Fatal(trace)
	}
	r, err := s.Replay(m, trace, false)
	r = must(t, r, err)
	if r.Outcome != "exact" {
		t.Fatal(r)
	}
}
func TestMissingCloneCallbacksRejected(t *testing.T) {
	m := counter()
	m.CloneState = nil
	_, err := s.Enumerate(m, s.DefaultSearchConfig())
	modelError(t, err)
}
func TestConfigurationValidation(t *testing.T) {
	for _, cfg := range []s.SearchConfig{{MaxStates: 0}, {MaxStates: -1}, {MaxStates: 1, MaxDepth: -1}} {
		if _, err := s.Enumerate(counter(), cfg); err == nil {
			t.Fatal("accepted invalid config")
		}
	}
}
func TestRngVectorsAndEmptyDomain(t *testing.T) {
	a, b := s.NewRng(123), s.NewRng(123)
	if _, ok := a.Index(0); ok {
		t.Fatal("empty domain")
	}
	if a.NextU64() != b.NextU64() {
		t.Fatal("empty domain consumed RNG")
	}
	if s.NewRng(0).NextU64() != 16294208416658607535 {
		t.Fatal("vector mismatch")
	}
	for _, upper := range []uint64{1, 2, 3, 1<<63 + 1, ^uint64(0)} {
		n, ok := a.Index(upper)
		if !ok || n >= upper {
			t.Fatal(n, upper)
		}
	}
}
func TestRetentionLimits(t *testing.T) {
	l := s.DefaultTraceLimits()
	l.MaxPayloadBytes = 1
	trace, err := s.RecordWithLimits(counter(), s.Values([]string{"increment"}), 100, l)
	trace = must(t, trace, err)
	if trace.Termination != "model_error" || len(trace.Steps) != 0 {
		t.Fatal(trace)
	}
	l = s.DefaultTraceLimits()
	l.MaxBlobBytes = 0
	_, err = s.RecordWithLimits(counter(), s.Values([]string{}), 100, l)
	modelError(t, err)
	l = s.DefaultTraceLimits()
	l.MaxItems = 0
	_, err = s.RecordWithLimits(counter(), s.Values([]string{}), 100, l)
	modelError(t, err)
}
func TestMalformedTraceInput(t *testing.T) {
	for _, raw := range []string{`{"x":1,"x":2}`, `{"x":"\ud800"}`, `{"x":NaN}`, `{"x":1.0}`, `{} trailing`, "\xff"} {
		if _, err := s.ParseTrace([]byte(raw)); err == nil {
			t.Fatal(raw)
		}
	}
}
func TestContinuationAndFalseTermination(t *testing.T) {
	trace, err := s.Record(counter(), s.Values([]string{"increment", "increment", "increment"}), 100)
	trace = must(t, trace, err)
	trace.Termination = "completed"
	_, err = s.Replay(counter(), trace, false)
	modelError(t, err)
	trace.Termination = "property_failed"
	trace.Steps = append(trace.Steps, trace.Steps[2])
	_, err = s.Replay(counter(), trace, false)
	modelError(t, err)
}
func TestZeroAndExactStepLimits(t *testing.T) {
	for _, tc := range []struct {
		in   []string
		max  int
		want string
	}{{nil, 0, "completed"}, {[]string{"increment"}, 0, "step_limit"}, {[]string{"increment"}, 1, "completed"}, {[]string{"increment", "increment"}, 1, "step_limit"}} {
		r, e := s.Record(counter(), s.Values(tc.in), tc.max)
		r = must(t, r, e)
		if r.Termination != tc.want {
			t.Fatal(r)
		}
	}
}
func TestInvalidChecksAreErrors(t *testing.T) {
	m := counter()
	m.CheckState = func(counterState) ([]s.Check, error) {
		return []s.Check{{ID: "x", Status: "passed", Details: "bad"}}, nil
	}
	_, err := s.Enumerate(m, s.DefaultSearchConfig())
	modelError(t, err)
}
func TestUnicodeDetailsNotNormalized(t *testing.T) {
	m := counter()
	m.CheckState = func(counterState) ([]s.Check, error) { return []s.Check{s.Failed("p", "é")}, nil }
	trace, err := s.Record(m, s.Values([]string{}), 100)
	trace = must(t, trace, err)
	m.CheckState = func(counterState) ([]s.Check, error) { return []s.Check{s.Failed("p", "e\u0301")}, nil }
	r, err := s.Replay(m, trace, false)
	r = must(t, r, err)
	if r.Outcome != "diverged" || !r.FailureReproduced {
		t.Fatal(r)
	}
}

func TestMarshalRejectsInvalidUTF8BeforeJSONReplacement(t *testing.T) {
	m := counter()
	trace, err := s.Record(m, s.Values([]string{}), 10)
	if err != nil {
		t.Fatal(err)
	}
	trace.Metadata.Name = string([]byte{255})
	if _, err := s.MarshalTrace(trace); err == nil {
		t.Fatal("invalid UTF-8 silently normalized")
	}
}

func TestObservedDispositionValidatedWithoutOptionalChecks(t *testing.T) {
	for _, d := range []s.Disposition{{Kind: "invalid"}, {Kind: "accepted", Reason: "unexpected"}, {Kind: "rejected", Reason: string([]byte{255})}} {
		for _, enabled := range []bool{false, true} {
			for _, present := range []bool{false, true} {
				m := counter()
				m.CheckState = func(counterState) ([]s.Check, error) { t.Fatal("state checker ran before validation"); return nil, nil }
				if present {
					m.CheckTransition = func(counterState, string, s.Transition[counterState, string]) ([]s.Check, error) {
						t.Fatal("transition checker ran before validation")
						return nil, nil
					}
				}
				checks, err := s.CheckObserved(m, counterState{}, "increment", s.Transition[counterState, string]{State: counterState{1}, Disposition: d}, 1, s.CheckPolicy{StateEvery: 1, TransitionChecks: enabled})
				var modelErr *s.ModelError
				if checks != nil || !errors.As(err, &modelErr) {
					t.Fatalf("malformed disposition accepted: checks=%v error=%v", checks, err)
				}
			}
		}
	}
}
