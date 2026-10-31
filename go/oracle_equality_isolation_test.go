package statelessness_test

import (
	"errors"
	"slices"
	"strings"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

func TestOracleEqualityIsolatesMutableComponents(t *testing.T) {
	clone := func(v []int) []int { return slices.Clone(v) }
	model := s.Model[[]int, int, int]{CloneState: clone, CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], CheckState: func([]int) ([]s.Check, error) { return nil, nil }, EqualStates: func(a, b []int) bool {
		equal := slices.Equal(a, b)
		a[0], b[0] = 90, 91
		return equal
	}}
	oracle := s.Oracle[[]int, []int, int, int]{InitialState: func() ([]int, error) { return []int{1}, nil }, Advance: func(h []int, _ int, _ []int, _ s.Disposition) ([]int, error) { return h, nil }, CheckState: func([]int, []int) ([]s.Check, error) { return nil, nil }, CloneHistory: clone, EqualHistory: func(a, b []int) bool {
		equal := slices.Equal(a, b)
		a[0], b[0] = 92, 93
		return equal
	}}
	w, err := s.WithOracle(model, oracle)
	if err != nil {
		t.Fatal(err)
	}
	a, b := []int{1}, []int{1}
	if !w.EqualStates(s.OracleState[[]int, []int]{Model: a, Oracle: a}, s.OracleState[[]int, []int]{Model: b, Oracle: b}) {
		t.Fatal("shared history changed during comparison")
	}
	if a[0] != 1 || b[0] != 1 {
		t.Fatalf("comparison mutated caller state: %v %v", a, b)
	}
}

func TestOracleEqualityCapturesHistoryBeforeActualComparator(t *testing.T) {
	leftHistory, rightHistory := []int{1}, []int{1}
	model := s.Model[int, int, int]{CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], CheckState: func(int) ([]s.Check, error) { return nil, nil }, EqualStates: func(a, b int) bool {
		leftHistory[0], rightHistory[0] = 2, 3
		return a == b
	}}
	oracle := s.Oracle[int, []int, int, int]{InitialState: func() ([]int, error) { return nil, nil }, Advance: func(h []int, _ int, _ []int, _ s.Disposition) ([]int, error) { return h, nil }, CheckState: func([]int, int) ([]s.Check, error) { return nil, nil }, CloneHistory: slices.Clone[[]int], EqualHistory: slices.Equal[[]int]}
	w, err := s.WithOracle(model, oracle)
	if err != nil {
		t.Fatal(err)
	}
	if !w.EqualStates(s.OracleState[int, []int]{Model: 1, Oracle: leftHistory}, s.OracleState[int, []int]{Model: 1, Oracle: rightHistory}) {
		t.Fatal("actual comparator changed the history observation")
	}
}

func TestOracleSnapshotsActualBeforeHistoryCallbacks(t *testing.T) {
	clone := func(v []int) []int { return slices.Clone(v) }
	actual := []int{1}
	codec := &s.Codec[[]int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("actual", "test"), nil },
		EncodeState: func(v []int) ([]byte, error) { return []byte{byte(v[0])}, nil },
		DecodeState: func(data []byte) ([]int, error) { actual[0] = int(data[0]); return actual, nil },
		EncodeInput: func(v int) ([]byte, error) { return []byte{byte(v)}, nil }, DecodeInput: func(v []byte) (int, error) { return int(v[0]), nil },
		EncodeOutput: func(v int) ([]byte, error) { return []byte{byte(v)}, nil }}
	model := s.Model[[]int, int, int]{InitialState: func() ([]int, error) { return actual, nil }, CloneState: clone, CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], CheckState: func([]int) ([]s.Check, error) { return nil, nil }, Codec: codec}
	oracle := s.Oracle[[]int, int, int, int]{InitialState: func() (int, error) { actual[0] = 9; return 2, nil }, Advance: func(h int, _ int, _ []int, _ s.Disposition) (int, error) { return h, nil }, CheckState: func(int, []int) ([]s.Check, error) { return nil, nil }, CloneHistory: s.ValueCopy[int],
		Metadata: func() (s.Metadata, error) { return s.Identity("history", "test"), nil }, EncodeHistory: func(h int) ([]byte, error) { return []byte{byte(h)}, nil }, DecodeHistory: func(data []byte) (int, error) { actual[0] = 8; return int(data[0]), nil }}
	w, err := s.WithOracle(model, oracle)
	if err != nil {
		t.Fatal(err)
	}
	state, err := w.InitialState()
	if err != nil || state.Model[0] != 1 || actual[0] != 9 {
		t.Fatalf("initial: %+v, %v", state, err)
	}
	bytes, err := w.Codec.EncodeState(state)
	if err != nil {
		t.Fatal(err)
	}
	restored, err := w.Codec.DecodeState(bytes)
	if err != nil || restored.Model[0] != 1 || actual[0] != 8 {
		t.Fatalf("decoded: %+v, %v", restored, err)
	}
}

func TestOracleEncodingCapturesHistoryBeforeActualEncoder(t *testing.T) {
	history := []int{7}
	failClone, failModel := false, false
	clone := func(v []int) []int {
		if failClone {
			panic("history clone failure")
		}
		return slices.Clone(v)
	}
	codec := &s.Codec[int, int, int]{
		Metadata: func() (s.Metadata, error) { return s.Identity("actual", "test"), nil },
		EncodeState: func(v int) ([]byte, error) {
			if failModel {
				return nil, errors.New("model encoder failure")
			}
			history[0] = 99
			return []byte{byte(v)}, nil
		},
		DecodeState: func(data []byte) (int, error) { return int(data[0]), nil },
	}
	model := s.Model[int, int, int]{CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], CheckState: func(int) ([]s.Check, error) { return nil, nil }, Codec: codec}
	oracle := s.Oracle[int, []int, int, int]{
		InitialState: func() ([]int, error) { return []int{0}, nil },
		Advance:      func(h []int, _ int, _ []int, _ s.Disposition) ([]int, error) { return h, nil },
		CheckState:   func([]int, int) ([]s.Check, error) { return nil, nil }, CloneHistory: clone,
		Metadata:      func() (s.Metadata, error) { return s.Identity("history", "test"), nil },
		EncodeHistory: func(h []int) ([]byte, error) { return []byte{byte(h[0])}, nil },
		DecodeHistory: func(data []byte) ([]int, error) { return []int{int(data[0])}, nil },
	}
	w, err := s.WithOracle(model, oracle)
	if err != nil {
		t.Fatal(err)
	}
	data, err := w.Codec.EncodeState(s.OracleState[int, []int]{Model: 1, Oracle: history})
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := w.Codec.DecodeState(data)
	if err != nil || decoded.Oracle[0] != 7 || history[0] != 99 {
		t.Fatalf("decoded history %v, original %v, error %v", decoded.Oracle, history, err)
	}
	failClone, failModel = true, true
	_, err = w.Codec.EncodeState(s.OracleState[int, []int]{Model: 1, Oracle: history})
	if err == nil || !strings.Contains(err.Error(), "model encoder failure") {
		t.Fatalf("model error did not take precedence: %v", err)
	}
}

func TestOracleSnapshotsOutputElementsBeforeStateClone(t *testing.T) {
	outputs := []int{7}
	model := s.Model[int, int, int]{CloneState: func(v int) int { outputs[0] = 99; return v }, CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], CheckState: func(int) ([]s.Check, error) { return nil, nil }}
	oracle := s.Oracle[int, int, int, int]{InitialState: func() (int, error) { return 0, nil }, CloneHistory: s.ValueCopy[int], CheckState: func(int, int) ([]s.Check, error) { return nil, nil }, Advance: func(h int, _ int, effects []int, _ s.Disposition) (int, error) {
		if len(effects) != 1 || effects[0] != 7 {
			t.Fatalf("oracle observed changed outputs: %v", effects)
		}
		return h, nil
	}}
	w, err := s.WithOracle(model, oracle)
	if err != nil {
		t.Fatal(err)
	}
	result, err := w.ObserveTransition(s.OracleState[int, int]{}, 0, s.Transition[int, int]{State: 1, Outputs: outputs, Disposition: s.Accepted()})
	if err != nil || len(result.Outputs) != 1 || result.Outputs[0] != 7 {
		t.Fatalf("retained outputs: %+v, %v", result, err)
	}
}

func TestOracleSnapshotsInputBeforeOtherCloneCallbacks(t *testing.T) {
	input := []int{7}
	model := s.Model[int, []int, int]{CloneState: func(v int) int { input[0] = 8; return v }, CloneInput: slices.Clone[[]int], CloneOutput: s.ValueCopy[int], CheckState: func(int) ([]s.Check, error) { return nil, nil }}
	oracle := s.Oracle[int, int, []int, int]{InitialState: func() (int, error) { return 0, nil }, CloneHistory: func(v int) int { input[0] = 9; return v }, CheckState: func(int, int) ([]s.Check, error) { return nil, nil }, Advance: func(h int, received []int, _ []int, _ s.Disposition) (int, error) {
		if len(received) != 1 || received[0] != 7 {
			t.Fatalf("oracle observed changed input: %v", received)
		}
		return h, nil
	}}
	w, err := s.WithOracle(model, oracle)
	if err != nil {
		t.Fatal(err)
	}
	_, err = w.ObserveTransition(s.OracleState[int, int]{}, input, s.Transition[int, int]{State: 1, Disposition: s.Accepted()})
	if err != nil {
		t.Fatal(err)
	}
}

func TestObserveTransitionCapturesHistoryBeforeTransitionClone(t *testing.T) {
	history := []int{7}
	model := s.Model[int, int, int]{CloneState: func(v int) int { history[0] = 99; return v }, CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], CheckState: func(int) ([]s.Check, error) { return nil, nil }}
	oracle := s.Oracle[int, []int, int, int]{InitialState: func() ([]int, error) { return nil, nil }, CloneHistory: slices.Clone[[]int], CheckState: func([]int, int) ([]s.Check, error) { return nil, nil }, Advance: func(h []int, _ int, _ []int, _ s.Disposition) ([]int, error) { return h, nil }}
	w, err := s.WithOracle(model, oracle)
	if err != nil {
		t.Fatal(err)
	}
	result, err := w.ObserveTransition(s.OracleState[int, []int]{Oracle: history}, 1, s.Transition[int, int]{State: 2, Disposition: s.Accepted()})
	if err != nil || len(result.State.Oracle) != 1 || result.State.Oracle[0] != 7 {
		t.Fatalf("history = %v, error = %v", result.State.Oracle, err)
	}
}

func TestObserveTransitionDefersHistoryFailureUntilTransitionRetained(t *testing.T) {
	model := s.Model[int, int, int]{CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], CheckState: func(int) ([]s.Check, error) { return nil, nil }}
	oracle := s.Oracle[int, int, int, int]{InitialState: func() (int, error) { return 0, nil }, CloneHistory: func(int) int { panic("history copy") }, CheckState: func(int, int) ([]s.Check, error) { return nil, nil }, Advance: func(h int, _ int, _ []int, _ s.Disposition) (int, error) { t.Fatal("advance called"); return h, nil }}
	w, err := s.WithOracle(model, oracle)
	if err != nil {
		t.Fatal(err)
	}
	_, err = w.ObserveTransition(s.OracleState[int, int]{}, 1, s.Transition[int, int]{State: 42, Outputs: []int{3}, Disposition: s.Accepted()})
	var observation *s.ObservationError[int, int]
	if !errors.As(err, &observation) || observation.Transition.State != 42 || len(observation.Transition.Outputs) != 1 || observation.Transition.Outputs[0] != 3 {
		t.Fatalf("completed transition not retained: %v", err)
	}
}

func TestObserveTransitionInputCopyFailureRetainsDetachedTransition(t *testing.T) {
	model := s.Model[[]int, int, []int]{CloneState: slices.Clone[[]int], CloneInput: func(int) int { panic("input copy") }, CloneOutput: slices.Clone[[]int], CheckState: func([]int) ([]s.Check, error) { return nil, nil }}
	oracle := s.Oracle[[]int, int, int, []int]{InitialState: func() (int, error) { return 0, nil }, CloneHistory: s.ValueCopy[int], CheckState: func(int, []int) ([]s.Check, error) { return nil, nil }, Advance: func(h int, _ int, _ [][]int, _ s.Disposition) (int, error) { t.Fatal("advance called"); return h, nil }}
	w, err := s.WithOracle(model, oracle)
	if err != nil {
		t.Fatal(err)
	}
	state, output := []int{42}, []int{3}
	_, err = w.ObserveTransition(s.OracleState[[]int, int]{}, 1, s.Transition[[]int, []int]{State: state, Outputs: [][]int{output}, Disposition: s.Accepted()})
	state[0], output[0] = 0, 0
	var observation *s.ObservationError[[]int, []int]
	if !errors.As(err, &observation) || observation.Transition.State[0] != 42 || observation.Transition.Outputs[0][0] != 3 {
		t.Fatalf("completed transition not detached: %v", err)
	}
}
