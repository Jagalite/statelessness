package statelessness_test

import (
	"bytes"
	"encoding/binary"
	"encoding/csv"
	"encoding/hex"
	"errors"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"os"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"testing"
)

type history struct{ Code, Length int }

func queuePair(t *testing.T, mutation string) *s.OracleModel[[]int, history, int, string] {
	t.Helper()
	actual := s.Model[[]int, int, string]{
		InitialState: func() ([]int, error) { return []int{}, nil },
		Step: func(q []int, i int) (s.Transition[[]int, string], error) {
			if i < 2 {
				if len(q) == 2 {
					return s.Transition[[]int, string]{q, nil, s.Rejected("full")}, nil
				}
				q = append(q, i)
				if mutation == "state" {
					q[len(q)-1] = 1 - i
				}
				effects := []string{strconv.Itoa(10 + i), strconv.Itoa(20 + len(q))}
				if mutation == "order" {
					slices.Reverse(effects)
				}
				if mutation == "omit" {
					effects = effects[:1]
				}
				return s.Accept(q, effects...), nil
			}
			if len(q) == 0 {
				reason := "empty"
				if mutation == "reason" {
					reason = "different"
				}
				return s.Transition[[]int, string]{q, nil, s.Ignored(reason)}, nil
			}
			v := q[0]
			q = q[1:]
			return s.Accept(q, strconv.Itoa(30+v), strconv.Itoa(20+len(q))), nil
		}, CheckState: func([]int) ([]s.Check, error) { return nil, nil }, CloneState: slices.Clone[[]int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[string], Inputs: func([]int) (s.Iterator[int], error) { return s.Values([]int{0, 1, 2}), nil }, EqualStates: slices.Equal[[]int],
		Codec: &s.Codec[[]int, int, string]{Metadata: func() (s.Metadata, error) { return s.Identity("queue", "go"), nil }, EncodeState: func(q []int) ([]byte, error) {
			b := make([]byte, len(q))
			for k, v := range q {
				b[k] = byte(v)
			}
			return b, nil
		}, DecodeState: func(b []byte) ([]int, error) {
			q := make([]int, len(b))
			for k, v := range b {
				q[k] = int(v)
			}
			return q, nil
		}, EncodeInput: func(i int) ([]byte, error) { return []byte{byte(i)}, nil }, DecodeInput: func(b []byte) (int, error) {
			if len(b) != 1 {
				return 0, fmt.Errorf("invalid input")
			}
			return int(b[0]), nil
		}, EncodeOutput: func(o string) ([]byte, error) { return []byte(o), nil }},
	}
	ref := s.Model[history, int, int]{InitialState: func() (history, error) {
		if mutation == "initial" {
			return history{1, 1}, nil
		}
		return history{}, nil
	}, Step: func(h history, i int) (s.Transition[history, int], error) {
		if i < 2 {
			if h.Length == 2 {
				return s.Transition[history, int]{h, nil, s.Rejected("full")}, nil
			}
			factor := 1
			for k := 0; k < h.Length; k++ {
				factor *= 3
			}
			h.Code += (i + 1) * factor
			h.Length++
			return s.Accept(h, 10+i, 20+h.Length), nil
		}
		if h.Length == 0 {
			return s.Transition[history, int]{h, nil, s.Ignored("empty")}, nil
		}
		v := h.Code%3 - 1
		h.Code /= 3
		h.Length--
		return s.Accept(h, 30+v, 20+h.Length), nil
	}, CheckState: func(history) ([]s.Check, error) { return nil, nil }, CloneState: s.ValueCopy[history], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], EqualStates: func(a, b history) bool { return a == b }, Codec: &s.Codec[history, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("reference", "go"), nil }, EncodeState: func(h history) ([]byte, error) { return []byte{byte(h.Code), byte(h.Length)}, nil }, DecodeState: func(b []byte) (history, error) {
		if len(b) != 2 {
			return history{}, fmt.Errorf("invalid history")
		}
		return history{int(b[0]), int(b[1])}, nil
	}}}
	projection := s.Projection[[]int, history, string, int, []int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("projection", "go"), nil }, ActualState: slices.Clone[[]int], ReferenceState: func(h history) []int {
		q := make([]int, h.Length)
		for k := range q {
			q[k] = h.Code%3 - 1
			h.Code /= 3
		}
		return q
	}, ActualEffect: func(o string) int { v, _ := strconv.Atoi(o); return v }, ReferenceEffect: s.ValueCopy[int], EqualStates: slices.Equal[[]int], EqualEffects: func(a, b int) bool { return a == b }}
	oracle, e := s.Paired(ref, projection)
	if e != nil {
		t.Fatal(e)
	}
	w, e := s.WithOracle(actual, oracle)
	if e != nil {
		t.Fatal(e)
	}
	return w
}
func TestPairedGolden(t *testing.T) {
	path := os.Getenv("STATELESS_PAIRED_CORPUS")
	if path == "" {
		path = "../conformance/paired-v1.tsv"
	}
	f, e := os.Open(path)
	if e != nil {
		t.Fatal(e)
	}
	defer f.Close()
	reader := csv.NewReader(f)
	reader.Comma = '\t'
	rows, e := reader.ReadAll()
	if e != nil {
		t.Fatal(e)
	}
	if len(rows) != 12 {
		t.Fatal("golden rows", len(rows))
	}
	for _, row := range rows[1:] {
		t.Run(row[0], func(t *testing.T) {
			states, _ := strconv.Atoi(row[2])
			edges, _ := strconv.ParseUint(row[3], 10, 64)
			depth, _ := strconv.Atoi(row[4])
			w := queuePair(t, row[1])
			r, e := s.Enumerate(w.Model, s.SearchConfig{MaxStates: states, MaxTransitions: edges, MaxDepth: depth})
			if e != nil {
				t.Fatal(e)
			}
			if r.Termination != row[5] || strconv.Itoa(r.States) != row[6] || strconv.FormatUint(r.Transitions, 10) != row[7] || strconv.FormatUint(r.SkippedChecks, 10) != row[11] {
				t.Fatalf("%+v expected %v", r, row)
			}
			ids, phases, witness := "-", "-", "-"
			if r.Failure != nil {
				var a, b, c []string
				for _, v := range r.Failure.Violations {
					a = append(a, v.Check.ID)
					b = append(b, v.Phase)
				}
				for _, i := range r.Failure.Inputs {
					c = append(c, strconv.Itoa(i))
				}
				ids = strings.Join(a, ",")
				phases = strings.Join(b, ",")
				if len(c) > 0 {
					witness = strings.Join(c, ",")
				}
			}
			if ids != row[8] || witness != row[9] || phases != row[10] {
				t.Fatalf("%s %s %s expected %v", ids, witness, phases, row)
			}
		})
	}
}
func TestPairedCheckpointReplay(t *testing.T) {
	w := queuePair(t, "none")
	state, e := w.Attach([]int{1}, history{2, 1})
	if e != nil {
		t.Fatal(e)
	}
	data, e := w.Codec.EncodeState(state)
	if e != nil {
		t.Fatal(e)
	}
	expected, _ := hex.DecodeString("4f52433101000000000000000102000000000000000201")
	if !bytes.Equal(data, expected) {
		t.Fatalf("%x", data)
	}
	decoded, e := w.Codec.DecodeState(data)
	if e != nil || !w.EqualStates(state, decoded) {
		t.Fatal(decoded, e)
	}
	for _, bad := range [][]byte{nil, []byte("ORC2"), data[:12], append(slices.Clone(data), 0), append([]byte("ORC1"), bytes.Repeat([]byte{255}, 8)...)} {
		if _, e := w.Codec.DecodeState(bad); e == nil {
			t.Fatalf("accepted %x", bad)
		}
	}
	for _, mutation := range []string{"none", "order", "reason", "initial"} {
		w := queuePair(t, mutation)
		tr, e := s.Record(w.Model, s.Values([]int{0, 1, 2, 2, 2}), 100)
		if e != nil {
			t.Fatal(e)
		}
		r, e := s.Replay(w.Model, tr, false)
		if e != nil || r.Outcome != "exact" {
			t.Fatal(r, e)
		}
	}
	trace, e := s.Record(w.Model, s.Values([]int{0, 1, 2}), 100)
	if e != nil {
		t.Fatal(e)
	}
	trace.InitialState = data
	trace.InitialChecks = []s.Check{s.Passed("pair.state")}
	trace.Steps = nil
	trace.Termination = "completed"
	r, e := s.Replay(w.Model, trace, false)
	if e != nil || r.Outcome != "exact" {
		t.Fatal(r, e)
	}
	// History participates in equality even when actual state is identical.
	other := state
	other.Oracle = history{1, 1}
	if w.EqualStates(state, other) {
		t.Fatal("history merged")
	}
	clone := w.CloneState(state)
	clone.Model[0] = 0
	if state.Model[0] != 1 {
		t.Fatal("clone aliases")
	}
}
func TestOracleObservationRetainsActual(t *testing.T) {
	m := s.Model[[]int, []int, []int]{CloneState: slices.Clone[[]int], CloneInput: slices.Clone[[]int], CloneOutput: slices.Clone[[]int], CheckState: func([]int) ([]s.Check, error) { return nil, nil }, Step: func([]int, []int) (s.Transition[[]int, []int], error) { panic("actual must not run") }}
	o := s.Oracle[[]int, []int, []int, []int]{InitialState: func() ([]int, error) { return nil, nil }, CloneHistory: slices.Clone[[]int], CheckState: func([]int, []int) ([]s.Check, error) { return nil, nil }, Advance: func(h, i []int, outputs [][]int, _ s.Disposition) ([]int, error) {
		h[0] = 9
		i[0] = 9
		outputs[0][0] = 9
		return nil, errors.New("history failed")
	}}
	w, e := s.WithOracle(m, o)
	if e != nil {
		t.Fatal(e)
	}
	before, _ := w.Attach([]int{1}, []int{2})
	input := []int{3}
	tr := s.Accept([]int{4}, []int{5})
	_, e = w.ObserveTransition(before, input, tr)
	var observed *s.ObservationError[[]int, []int]
	if !errors.As(e, &observed) {
		t.Fatal(e)
	}
	if !reflect.DeepEqual(observed.Transition, tr) || input[0] != 3 || before.Oracle[0] != 2 {
		t.Fatal("callback mutation escaped")
	}
	tr.State[0] = 10
	tr.Outputs[0][0] = 10
	if observed.Transition.State[0] != 4 || observed.Transition.Outputs[0][0] != 5 {
		t.Fatal("retained aliases actual")
	}
}

func TestOracleMetadataAndCheckOrder(t *testing.T) {
	m := counter()
	m.Codec.Metadata = func() (s.Metadata, error) { return s.Identity("é", "界"), nil }
	m.CheckState = func(counterState) ([]s.Check, error) {
		return []s.Check{s.Passed("duplicate"), s.Skipped("a", "reason")}, nil
	}
	m.CheckTransition = func(counterState, string, s.Transition[counterState, string]) ([]s.Check, error) {
		return []s.Check{s.Passed("duplicate")}, nil
	}
	oracle := s.Oracle[counterState, int, string, string]{Metadata: func() (s.Metadata, error) { return s.Identity("参", "ö"), nil }, InitialState: func() (int, error) { return 0, nil }, Advance: func(h int, _ string, _ []string, _ s.Disposition) (int, error) { return h + 1, nil }, CloneHistory: s.ValueCopy[int], EqualHistory: func(a, b int) bool { return a == b }, CheckState: func(int, counterState) ([]s.Check, error) { return []s.Check{s.Passed("duplicate")}, nil }, CheckTransition: func(s.OracleState[counterState, int], string, s.Transition[s.OracleState[counterState, int], string]) ([]s.Check, error) {
		return []s.Check{s.Passed("o")}, nil
	}, EncodeHistory: func(h int) ([]byte, error) { return []byte{byte(h)}, nil }, DecodeHistory: func(b []byte) (int, error) {
		if len(b) != 1 {
			return 0, fmt.Errorf("bad history")
		}
		return int(b[0]), nil
	}}
	w, e := s.WithOracle(m, oracle)
	if e != nil {
		t.Fatal(e)
	}
	metadata, e := w.Codec.Metadata()
	if e != nil {
		t.Fatal(e)
	}
	if metadata.Name != "with-oracle-v1:2:é:1:1:1:3:参:1:1:1" || metadata.Build != "3:界:2:ö" {
		t.Fatal(metadata)
	}
	before, _ := w.InitialState()
	transition, e := w.Step(before, "increment")
	if e != nil {
		t.Fatal(e)
	}
	checks, e := s.CheckObserved(w.Model, before, "increment", transition, 1, s.FullChecks())
	if e != nil {
		t.Fatal(e)
	}
	want := []s.Check{s.Passed("duplicate"), s.Skipped("a", "reason"), s.Passed("duplicate"), s.Passed("duplicate"), s.Passed("o")}
	if !reflect.DeepEqual(checks, want) {
		t.Fatal(checks)
	}
	trace, e := s.Record(w.Model, s.Values([]string{"increment"}), 10)
	if e != nil {
		t.Fatal(e)
	}
	trace.Metadata.Name += "changed"
	r, e := s.Replay(w.Model, trace, true)
	if e != nil || r.Outcome != "incompatible" {
		t.Fatal(r, e)
	}
}

func TestPairedMidstreamAndLimits(t *testing.T) {
	w := queuePair(t, "none")
	checkpoint, e := w.Attach([]int{1}, history{2, 1})
	if e != nil {
		t.Fatal(e)
	}
	w.InitialState = func() (s.OracleState[[]int, history], error) { return checkpoint, nil }
	trace, e := s.Record(w.Model, s.Values([]int{0, 2, 2}), 100)
	if e != nil || trace.Termination != "completed" {
		t.Fatal(trace, e)
	}
	r, e := s.Replay(w.Model, trace, false)
	if e != nil || r.Outcome != "exact" || r.StepsVerified != 3 {
		t.Fatal(r, e)
	}
	limits := s.DefaultTraceLimits()
	limits.MaxBlobBytes = 1
	if _, e = s.RecordWithLimits(w.Model, s.Values([]int{0}), 100, limits); e == nil {
		t.Fatal("checkpoint bypassed blob limit")
	}
	// An alternate component decoder may accept these bytes, but exact replay
	// requires the original checkpoint's canonical re-encoding.
	trace.InitialState = append(trace.InitialState, 0)
	if _, e = s.Replay(w.Model, trace, false); e == nil {
		t.Fatal("trailing checkpoint accepted")
	}
}

func TestPairedRejectsMalformedReferenceAndSemanticChanges(t *testing.T) {
	actual, reference := counter(), counter()
	projection := s.Projection[counterState, counterState, string, string, int, string]{Metadata: func() (s.Metadata, error) { return s.Identity("projection", "go"), nil }, ActualState: func(s counterState) int { return s.value }, ReferenceState: func(s counterState) int { return s.value }, ActualEffect: s.ValueCopy[string], ReferenceEffect: s.ValueCopy[string], EqualStates: func(a, b int) bool { return a == b }, EqualEffects: func(a, b string) bool { return a == b }}
	makePair := func() *s.OracleModel[counterState, counterState, string, string] {
		o, e := s.Paired(reference, projection)
		if e != nil {
			t.Fatal(e)
		}
		w, e := s.WithOracle(actual, o)
		if e != nil {
			t.Fatal(e)
		}
		return w
	}
	w := makePair()
	trace, e := s.Record(w.Model, s.Values([]string{"increment"}), 10)
	if e != nil {
		t.Fatal(e)
	}
	originalMetadata := reference.Codec.Metadata
	for _, field := range []string{"model", "properties", "codec"} {
		reference.Codec.Metadata = func() (s.Metadata, error) {
			m, _ := originalMetadata()
			switch field {
			case "model":
				m.ModelVersion++
			case "properties":
				m.PropertiesVersion++
			case "codec":
				m.CodecVersion++
			}
			return m, nil
		}
		r, e := s.Replay(makePair().Model, trace, true)
		if e != nil || r.Outcome != "incompatible" {
			t.Fatal(field, r, e)
		}
	}
	reference.Codec.Metadata = originalMetadata
	projection.Metadata = func() (s.Metadata, error) { m := s.Identity("projection", "go"); m.ModelVersion++; return m, nil }
	r, e := s.Replay(makePair().Model, trace, true)
	if e != nil || r.Outcome != "incompatible" {
		t.Fatal(r, e)
	}
	projection.Metadata = func() (s.Metadata, error) { return s.Identity("projection", "go"), nil }
	reference.Step = func(counterState, string) (s.Transition[counterState, string], error) {
		return s.Transition[counterState, string]{Disposition: s.Disposition{Kind: "invalid"}}, nil
	}
	w = makePair()
	before, _ := w.InitialState()
	_, e = w.Step(before, "increment")
	var observation *s.ObservationError[counterState, string]
	if !errors.As(e, &observation) || observation.Transition.State.value != 1 {
		t.Fatal("invalid reference lost actual transition", e)
	}
}

func TestOracleCanonicalReplayAndPanickingInputClone(t *testing.T) {
	m := counter()
	m.Codec.DecodeInput = func(b []byte) (string, error) { return strings.ToLower(string(b)), nil }
	oracle := s.Oracle[counterState, int, string, string]{Metadata: func() (s.Metadata, error) { return s.Identity("oracle", "go"), nil }, InitialState: func() (int, error) { return 0, nil }, Advance: func(h int, _ string, _ []string, _ s.Disposition) (int, error) { return h + 1, nil }, CloneHistory: s.ValueCopy[int], CheckState: func(int, counterState) ([]s.Check, error) { return nil, nil }, EncodeHistory: func(h int) ([]byte, error) { return []byte(strconv.Itoa(h)), nil }, DecodeHistory: func(b []byte) (int, error) { return strconv.Atoi(string(b)) }}
	w, e := s.WithOracle(m, oracle)
	if e != nil {
		t.Fatal(e)
	}
	trace, e := s.Record(w.Model, s.Values([]string{"increment"}), 10)
	if e != nil {
		t.Fatal(e)
	}
	trace.Steps[0].Input = []byte("INCREMENT")
	if _, e = s.Replay(w.Model, trace, false); e == nil {
		t.Fatal("input alias accepted")
	}
	trace, e = s.Record(w.Model, s.Values([]string{"increment"}), 10)
	if e != nil {
		t.Fatal(e)
	}
	frame := func(a, b []byte) []byte {
		out := []byte("ORC1")
		out = binary.LittleEndian.AppendUint64(out, uint64(len(a)))
		out = append(out, a...)
		out = binary.LittleEndian.AppendUint64(out, uint64(len(b)))
		return append(out, b...)
	}
	trace.InitialState = frame([]byte("00"), []byte("0"))
	if _, e = s.Replay(w.Model, trace, false); e == nil {
		t.Fatal("state alias accepted")
	}
	sentinel := errors.New("underlying history error")
	oracle.Advance = func(int, string, []string, s.Disposition) (int, error) { return 0, sentinel }
	w, e = s.WithOracle(m, oracle)
	if e != nil {
		t.Fatal(e)
	}
	before, _ := w.InitialState()
	_, e = w.ObserveTransition(before, "increment", s.Accept(counterState{1}, "changed"))
	if !errors.Is(e, sentinel) {
		t.Fatal("underlying cause lost", e)
	}
	m.CloneInput = func(string) string { panic("input snapshot failure") }
	w, e = s.WithOracle(m, oracle)
	if e != nil {
		t.Fatal(e)
	}
	_, e = w.ObserveTransition(before, "increment", s.Accept(counterState{1}, "changed"))
	var observed *s.ObservationError[counterState, string]
	if !errors.As(e, &observed) || observed.Transition.State.value != 1 || len(observed.Transition.Outputs) != 1 {
		t.Fatal("snapshot panic lost actual transition", e)
	}
}

func TestOracleGenerationFuzzShrinkRecordReplay(t *testing.T) {
	actual := counter()
	g := s.GenerateModel[counterState, string, string]{Model: actual, Generate: func(state counterState, _ *s.Rng) (string, bool, error) {
		if state.value >= 3 {
			return "", false, nil
		}
		return "increment", true, nil
	}, IsEnabled: func(state counterState, input string) (bool, error) {
		return state.value < 3 && input == "increment", nil
	}, SimplerInputs: func(string) (s.Iterator[string], error) { return s.Values([]string{}), nil }, InputEqual: func(a, b string) (bool, error) { return a == b, nil }}
	reference := counter()
	projection := s.Projection[counterState, counterState, string, string, int, string]{Metadata: func() (s.Metadata, error) { return s.Identity("counter-projection", "go"), nil }, ActualState: func(state counterState) int { return state.value }, ReferenceState: func(state counterState) int { return state.value }, ActualEffect: s.ValueCopy[string], ReferenceEffect: s.ValueCopy[string], EqualStates: func(a, b int) bool { return a == b }, EqualEffects: func(a, b string) bool { return a == b }}
	oracle, e := s.Paired(reference, projection)
	if e != nil {
		t.Fatal(e)
	}
	combined, e := s.WithOracleGenerate(g, oracle)
	if e != nil {
		t.Fatal(e)
	}
	report, e := s.Fuzz(combined, s.FuzzConfig{Seed: 42, Cases: 1, MaxSteps: 10, MaxTransitions: 10})
	if e != nil || report.Termination != "failure_found" || len(report.Failure.Inputs) != 3 {
		t.Fatal(report, e)
	}
	shrunk, e := s.Shrink(combined, *report.Failure, s.DefaultShrinkConfig())
	if e != nil || !shrunk.ValidatedOriginal || len(shrunk.Minimized.Inputs) != 3 {
		t.Fatal(shrunk, e)
	}
	trace, e := s.Record(combined.Model, s.Values(shrunk.Minimized.Inputs), 10)
	if e != nil || trace.Termination != "property_failed" {
		t.Fatal(trace, e)
	}
	replay, e := s.Replay(combined.Model, trace, false)
	if e != nil || replay.Outcome != "exact" || !replay.FailureReproduced || replay.StepsVerified != 3 {
		t.Fatal(replay, e)
	}
	// Defaults retain the underlying true-enabled/no-simplification semantics.
	g.IsEnabled = nil
	g.SimplerInputs = nil
	combined, e = s.WithOracleGenerate(g, oracle)
	if e != nil || combined.IsEnabled != nil || combined.SimplerInputs != nil {
		t.Fatal(e)
	}
}
