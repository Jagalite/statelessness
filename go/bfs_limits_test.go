package statelessness_test

import (
	"errors"
	s "github.com/Jagalite/statelessness/go"
	"math"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"sync/atomic"
	"testing"
	"time"
	"unsafe"
)

func u64(value uint64) *uint64 { return &value }
func bfsCounter() s.Model[int, int, int] {
	return s.Model[int, int, int]{InitialState: func() (int, error) { return 0, nil }, Step: func(state, input int) (s.Transition[int, int], error) { return s.Accept[int, int](state + input), nil }, CheckState: func(state int) ([]s.Check, error) {
		if state >= 3 {
			return []s.Check{s.Failed("threshold.bound", "three")}, nil
		}
		return []s.Check{s.Passed("threshold.bound")}, nil
	}, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], Inputs: func(int) (s.Iterator[int], error) { return s.Values([]int{0, 1}), nil }, EqualStates: func(a, b int) bool { return a == b }, HashState: func(state int) uint64 { return uint64(state) }, EstimatedStateBytes: func(int) (*uint64, error) { return u64(5), nil }}
}
func TestBFSByteAdmissionAndEstimateTotals(t *testing.T) {
	cases := []struct {
		name        string
		cap         *uint64
		estimate    func(int) (*uint64, error)
		states      int
		transitions uint64
		total       *uint64
		termination string
		error       bool
		maxStates   int
	}{
		{"base", nil, nil, 3, 6, u64(15), "failure_found", false, 100},
		{"exact", u64(15), nil, 3, 6, u64(15), "failure_found", false, 100},
		{"cap14", u64(14), nil, 2, 4, u64(10), "estimated_state_byte_limit", false, 100},
		{"initialover", u64(4), nil, 0, 0, u64(0), "estimated_state_byte_limit", false, 100},
		{"zero", u64(0), func(int) (*uint64, error) { return u64(0), nil }, 3, 6, u64(0), "failure_found", false, 100},
		{"missing", u64(10), func(int) (*uint64, error) { return nil, nil }, 0, 0, nil, "", true, 100},
		{"missingsuccessor", u64(10), func(state int) (*uint64, error) {
			if state > 0 {
				return nil, nil
			}
			return u64(5), nil
		}, 0, 0, nil, "", true, 100},
		{"unknown", nil, func(state int) (*uint64, error) {
			if state == 1 {
				return nil, nil
			}
			return u64(5), nil
		}, 3, 6, nil, "failure_found", false, 100},
		{"overflowuncapped", nil, func(state int) (*uint64, error) {
			if state == 0 {
				return u64(math.MaxUint64), nil
			}
			return u64(1), nil
		}, 3, 6, nil, "failure_found", false, 100},
		{"overflowcapped", u64(math.MaxUint64), func(state int) (*uint64, error) {
			if state == 0 {
				return u64(math.MaxUint64), nil
			}
			return u64(1), nil
		}, 1, 2, u64(math.MaxUint64), "estimated_state_byte_limit", false, 100},
		{"statecapfirst", u64(5), func(state int) (*uint64, error) {
			if state > 0 {
				panic("must not estimate rejected state")
			}
			return u64(5), nil
		}, 1, 2, u64(5), "state_limit", false, 1},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			m := bfsCounter()
			calls := []int{}
			estimate := m.EstimatedStateBytes
			if test.estimate != nil {
				estimate = test.estimate
			}
			m.EstimatedStateBytes = func(state int) (*uint64, error) { calls = append(calls, state); return estimate(state) }
			report, e := s.EnumerateWithLimits(m, s.SearchConfig{MaxStates: test.maxStates, MaxTransitions: 100, MaxDepth: 10}, s.SearchLimits{MaxEstimatedStateBytes: test.cap})
			if test.error {
				if e == nil {
					t.Fatal("missing estimate accepted")
				}
				return
			}
			if e != nil || report.Termination != test.termination || report.States != test.states || report.Transitions != test.transitions || !reflect.DeepEqual(report.EstimatedRetainedStateBytes, test.total) {
				t.Fatal(report, e, test)
			}
			if test.name == "base" && !slices.Equal(calls, []int{0, 1, 2}) {
				t.Fatal("duplicates or failing state estimated", calls)
			}
		})
	}
}
func TestBFSControlBoundariesAndFailurePrecedence(t *testing.T) {
	for _, phase := range []string{"entry", "initial", "initial-check", "initial-estimate", "initial-hash", "inputs", "next", "step", "state-check", "edge-check", "successor-hash", "equality", "successor-estimate"} {
		t.Run(phase, func(t *testing.T) {
			var cancelled atomic.Bool
			m := bfsCounter()
			callbacks := 0
			trigger := func(name string) {
				callbacks++
				if phase == name {
					cancelled.Store(true)
				}
			}
			m.InitialState = func() (int, error) { trigger("initial"); return 0, nil }
			m.CheckState = func(state int) ([]s.Check, error) {
				if state == 0 {
					trigger("initial-check")
				} else {
					trigger("state-check")
				}
				return nil, nil
			}
			m.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) { trigger("edge-check"); return nil, nil }
			m.Step = func(state, input int) (s.Transition[int, int], error) {
				trigger("step")
				return s.Accept[int, int](state + input), nil
			}
			m.EstimatedStateBytes = func(state int) (*uint64, error) {
				if state == 0 {
					trigger("initial-estimate")
				} else {
					trigger("successor-estimate")
				}
				return u64(5), nil
			}
			m.HashState = func(state int) uint64 {
				if state == 0 {
					trigger("initial-hash")
				} else {
					trigger("successor-hash")
				}
				return 0
			}
			m.EqualStates = func(a, b int) bool { trigger("equality"); return a == b }
			m.Inputs = func(int) (s.Iterator[int], error) {
				trigger("inputs")
				return func() (int, bool, error) { trigger("next"); return 1, true, nil }, nil
			}
			if phase == "entry" {
				cancelled.Store(true)
			}
			report, e := s.EnumerateWithLimits(m, s.SearchConfig{MaxStates: 100, MaxTransitions: 10, MaxDepth: 10}, s.SearchLimits{Control: s.RunLimits{Cancellation: &cancelled}})
			if e != nil || report.Termination != "cancelled" {
				t.Fatal(report, e)
			}
			if phase == "entry" && (callbacks != 0 || report.States != 0) {
				t.Fatal(report, callbacks)
			}
			if phase == "step" || phase == "state-check" {
				if callbacks < 3 {
					t.Fatal("checking boundary interrupted")
				}
			}
		})
	}
	m := bfsCounter()
	var cancelled atomic.Bool
	m.Step = func(state, input int) (s.Transition[int, int], error) {
		cancelled.Store(true)
		return s.Accept[int, int](3), nil
	}
	report, e := s.EnumerateWithLimits(m, s.DefaultSearchConfig(), s.SearchLimits{Control: s.RunLimits{Cancellation: &cancelled}})
	if e != nil || report.Termination != "failure_found" || report.Transitions != 1 {
		t.Fatal(report, e)
	}
	cancelled.Store(false)
	m = bfsCounter()
	m.CheckState = func(int) ([]s.Check, error) {
		cancelled.Store(true)
		return []s.Check{s.Failed("initial", "wins")}, nil
	}
	report, e = s.EnumerateWithLimits(m, s.DefaultSearchConfig(), s.SearchLimits{Control: s.RunLimits{Cancellation: &cancelled}})
	if e != nil || report.Termination != "failure_found" || report.Transitions != 0 {
		t.Fatal(report, e)
	}
	cancelled.Store(false)
	m = bfsCounter()
	m.EstimatedStateBytes = func(int) (*uint64, error) { cancelled.Store(true); return nil, errors.New("estimate failure wins") }
	if _, e = s.EnumerateWithLimits(m, s.DefaultSearchConfig(), s.SearchLimits{Control: s.RunLimits{Cancellation: &cancelled}}); e == nil || !strings.Contains(e.Error(), "estimate failure wins") {
		t.Fatal(e)
	}
	zero := time.Duration(0)
	cancelled.Store(true)
	report, e = s.EnumerateWithLimits(bfsCounter(), s.DefaultSearchConfig(), s.SearchLimits{Control: s.RunLimits{Cancellation: &cancelled, MaxDuration: &zero}})
	if e != nil || report.Termination != "cancelled" || report.States != 0 {
		t.Fatal(report, e)
	}
	report, e = s.EnumerateWithLimits(bfsCounter(), s.DefaultSearchConfig(), s.SearchLimits{Control: s.RunLimits{MaxDuration: &zero}})
	if e != nil || report.Termination != "deadline" || report.States != 0 {
		t.Fatal(report, e)
	}
}
func TestBFSNewestFirstCollisionAndProbeExhaustion(t *testing.T) {
	m := bfsCounter()
	m.CheckState = func(int) ([]s.Check, error) { return nil, nil }
	m.Inputs = func(state int) (s.Iterator[int], error) {
		if state == 0 {
			return s.Values([]int{1, 1}), nil
		}
		return s.Values([]int{}), nil
	}
	m.Step = func(int, int) (s.Transition[int, int], error) { return s.Accept[int, int](1), nil }
	m.HashState = func(int) uint64 { return 0 }
	comparisons := [][2]int{}
	m.EqualStates = func(a, b int) bool { comparisons = append(comparisons, [2]int{a, b}); return a == b }
	report, e := s.EnumerateWithLimits(m, s.SearchConfig{MaxStates: 100, MaxTransitions: 2, MaxDepth: 10}, s.SearchLimits{})
	if e != nil || report.Termination != "graph_exhausted" || report.Transitions != 2 || !reflect.DeepEqual(comparisons, [][2]int{{0, 1}, {1, 1}}) {
		t.Fatal(report, comparisons, e)
	}
	for _, depth := range []int{0, 1} {
		polls := 0
		m.Inputs = func(int) (s.Iterator[int], error) {
			return func() (int, bool, error) { polls++; return 1, true, nil }, nil
		}
		report, e = s.EnumerateWithLimits(m, s.SearchConfig{MaxStates: 100, MaxTransitions: 0, MaxDepth: depth}, s.SearchLimits{})
		if e != nil || polls != 1 || report.Transitions != 0 {
			t.Fatal(report, e, polls)
		}
	}
}
func TestOracleStateEstimateForwardingAndPadding(t *testing.T) {
	type actualState struct{ X uint64 }
	type oracleState byte
	m := s.Model[actualState, int, int]{InitialState: func() (actualState, error) { return actualState{}, nil }, Step: func(state actualState, _ int) (s.Transition[actualState, int], error) {
		return s.Accept[actualState, int](state), nil
	}, CheckState: func(actualState) ([]s.Check, error) { return nil, nil }, CloneState: s.ValueCopy[actualState], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], EstimatedStateBytes: func(actualState) (*uint64, error) { return u64(11), nil }}
	oracle := s.Oracle[actualState, oracleState, int, int]{InitialState: func() (oracleState, error) { return 0, nil }, Advance: func(h oracleState, _ int, _ []int, _ s.Disposition) (oracleState, error) { return h, nil }, CheckState: func(oracleState, actualState) ([]s.Check, error) { return nil, nil }, CloneHistory: s.ValueCopy[oracleState], EstimatedStateBytes: func(oracleState) (*uint64, error) { return u64(3), nil }}
	overhead := s.OracleStateOverhead[actualState, oracleState]()
	native := uint64(unsafe.Sizeof(s.OracleState[actualState, oracleState]{})) - uint64(unsafe.Sizeof(actualState{})) - uint64(unsafe.Sizeof(oracleState(0)))
	if overhead != native {
		t.Fatal(overhead, native)
	}
	w, e := s.WithOracle(m, oracle)
	if e != nil {
		t.Fatal(e)
	}
	initial, _ := w.InitialState()
	estimate, e := w.EstimatedStateBytes(initial)
	if e != nil || estimate == nil || *estimate != 14+overhead {
		t.Fatal(estimate, e)
	}
	for _, unknown := range []string{"actual", "oracle", "overflow"} {
		switch unknown {
		case "actual":
			m.EstimatedStateBytes = nil
		case "oracle":
			m.EstimatedStateBytes = func(actualState) (*uint64, error) { return u64(11), nil }
			oracle.EstimatedStateBytes = nil
		case "overflow":
			m.EstimatedStateBytes = func(actualState) (*uint64, error) { return u64(math.MaxUint64), nil }
			oracle.EstimatedStateBytes = func(oracleState) (*uint64, error) { return u64(1), nil }
		}
		w, e = s.WithOracle(m, oracle)
		if e != nil {
			t.Fatal(e)
		}
		if value, e := w.EstimatedStateBytes(initial); e != nil || value != nil {
			t.Fatal(unknown, value, e)
		}
	}
	reference := bfsCounter()
	projection := s.Projection[int, int, int, int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("projection", "go"), nil }, ActualState: s.ValueCopy[int], ReferenceState: s.ValueCopy[int], ActualEffect: s.ValueCopy[int], ReferenceEffect: s.ValueCopy[int], EqualStates: func(a, b int) bool { return a == b }, EqualEffects: func(a, b int) bool { return a == b }}
	paired, e := s.Paired(reference, projection)
	if e != nil {
		t.Fatal(e)
	}
	value, e := paired.EstimatedStateBytes(0)
	if e != nil || value == nil || *value != 5 {
		t.Fatal(value, e)
	}
}

func TestBFSObserverHistoriesIsolationAndPartialErrors(t *testing.T) {
	m := s.Model[[]int, int, []int]{InitialState: func() ([]int, error) { return []int{0}, nil }, Step: func(state []int, input int) (s.Transition[[]int, []int], error) {
		state[0] += input
		return s.Accept(state, []int{state[0]}), nil
	}, CheckState: func(state []int) ([]s.Check, error) {
		if state[0] >= 3 {
			return []s.Check{s.Failed("bad", "three")}, nil
		}
		return []s.Check{s.Passed("bad")}, nil
	}, CheckTransition: func([]int, int, s.Transition[[]int, []int]) ([]s.Check, error) {
		return []s.Check{s.Passed("edge")}, nil
	}, CloneState: slices.Clone[[]int], CloneInput: s.ValueCopy[int], CloneOutput: slices.Clone[[]int], Inputs: func([]int) (s.Iterator[int], error) { return s.Values([]int{0, 1}), nil }, EqualStates: func(a, b []int) bool { return slices.Equal(a, b) }, EstimatedStateBytes: func([]int) (*uint64, error) { return u64(5), nil }}
	histories := [][]int{}
	initials := 0
	observer := s.SearchObserver[[]int, int, []int]{NeedsHistory: func() bool { return true }, Initial: func(state []int, checks []s.Check) error {
		initials++
		state[0] = 99
		checks[0] = s.Passed("changed")
		return nil
	}, Transition: func(before []int, input int, tr s.Transition[[]int, []int], state, edge []s.Check, history []int) error {
		histories = append(histories, slices.Clone(history))
		if len(history) == 0 || history[len(history)-1] != input {
			t.Fatal(history, input)
		}
		before[0] = 99
		tr.State[0] = 99
		tr.Outputs[0][0] = 99
		state[0] = s.Passed("changed")
		edge[0] = s.Passed("changed")
		history[0] = 99
		return nil
	}}
	report, e := s.EnumerateObserved(m, s.DefaultSearchConfig(), s.SearchLimits{}, observer)
	if e != nil || report.Termination != "failure_found" || report.Transitions != 6 || initials != 1 || !slices.Equal(report.Failure.Inputs, []int{1, 1, 1}) || report.Failure.Violations[0].Check.ID != "bad" {
		t.Fatal(report, e)
	}
	if !reflect.DeepEqual(histories, [][]int{{0}, {1}, {1, 0}, {1, 1}, {1, 1, 0}, {1, 1, 1}}) {
		t.Fatal(histories)
	}
	observer.NeedsHistory = nil
	observer.Transition = func(_ []int, _ int, _ s.Transition[[]int, []int], _, _ []s.Check, history []int) error {
		if len(history) != 0 {
			t.Fatal("history not requested", history)
		}
		return nil
	}
	if _, e = s.EnumerateObserved(m, s.DefaultSearchConfig(), s.SearchLimits{}, observer); e != nil {
		t.Fatal(e)
	}
	observer.NeedsHistory = func() bool { panic("history hook panic") }
	if _, e = s.EnumerateObserved(m, s.DefaultSearchConfig(), s.SearchLimits{}, observer); e == nil || !strings.Contains(e.Error(), "observer needs history") {
		t.Fatal(e)
	}
	for _, phase := range []string{"initial_state", "state", "transition"} {
		t.Run(phase, func(t *testing.T) {
			model := bfsCounter()
			model.Inputs = func(int) (s.Iterator[int], error) { return s.Values([]int{1}), nil }
			model.CheckState = func(state int) ([]s.Check, error) {
				if phase == "initial_state" && state == 0 || phase == "state" && state > 0 {
					return []s.Check{s.Passed("partial-state")}, errors.New("primary checker error")
				}
				return []s.Check{s.Passed("complete-state")}, nil
			}
			model.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) {
				return []s.Check{s.Passed("partial-edge")}, errors.New("primary checker error")
			}
			events := []string{}
			obs := s.SearchObserver[int, int, int]{CheckError: func(phase string, checks []s.Check, complete bool) error {
				events = append(events, phase+":"+strconv.FormatBool(complete)+":"+checks[0].ID)
				return errors.New("secondary observer error")
			}}
			_, e := s.EnumerateObserved(model, s.DefaultSearchConfig(), s.SearchLimits{}, obs)
			if e == nil || !strings.Contains(e.Error(), "primary checker error") || !strings.Contains(e.Error(), "secondary observer error") {
				t.Fatal(e)
			}
			expected := []string{phase + ":false:partial-state"}
			if phase == "transition" {
				expected = []string{"state:true:complete-state", "transition:false:partial-edge"}
			}
			if !reflect.DeepEqual(events, expected) {
				t.Fatal(events, expected)
			}
		})
	}
}
func TestBFSCallbackTriggeredDeadline(t *testing.T) {
	duration := 100 * time.Millisecond
	for _, failure := range []bool{false, true} {
		m := bfsCounter()
		m.InitialState = func() (int, error) { time.Sleep(150 * time.Millisecond); return 0, nil }
		m.CheckState = func(int) ([]s.Check, error) {
			if failure {
				return []s.Check{s.Failed("initial", "failure wins")}, nil
			}
			return nil, nil
		}
		report, e := s.EnumerateWithLimits(m, s.DefaultSearchConfig(), s.SearchLimits{Control: s.RunLimits{MaxDuration: &duration}})
		expected := "deadline"
		if failure {
			expected = "failure_found"
		}
		if e != nil || report.Termination != expected || report.States != 1 || report.Transitions != 0 {
			t.Fatal(report, e)
		}
	}
}

func TestOracleEstimateOverflowAndGeneratedEnumerationForwarding(t *testing.T) {
	reference := bfsCounter()
	projection := s.Projection[int, int, int, int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("projection", "go"), nil }, ActualState: s.ValueCopy[int], ReferenceState: s.ValueCopy[int], ActualEffect: s.ValueCopy[int], ReferenceEffect: s.ValueCopy[int], EqualStates: func(a, b int) bool { return a == b }, EqualEffects: func(a, b int) bool { return a == b }}
	paired, e := s.Paired(reference, projection)
	if e != nil {
		t.Fatal(e)
	}
	actual := bfsCounter()
	generated, e := s.Auto(actual, func(a, b int) (bool, error) { return a == b, nil }, s.DefaultAutoOptions())
	if e != nil {
		t.Fatal(e)
	}
	combined, e := s.WithOracleGenerate(generated, paired)
	if e != nil {
		t.Fatal(e)
	}
	report, e := s.EnumerateWithLimits(combined.Model, s.DefaultSearchConfig(), s.SearchLimits{})
	perState := uint64(10) + s.OracleStateOverhead[int, int]()
	if e != nil || report.Termination != "failure_found" || report.EstimatedRetainedStateBytes == nil || *report.EstimatedRetainedStateBytes != 3*perState {
		t.Fatal(report, e)
	}
	// Pair estimates can sum without overflow yet overflow once inline padding is added.
	type tiny byte
	m := s.Model[uint64, int, int]{CloneState: s.ValueCopy[uint64], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], CheckState: func(uint64) ([]s.Check, error) { return nil, nil }, EstimatedStateBytes: func(uint64) (*uint64, error) { return u64(math.MaxUint64 - 1), nil }}
	oracle := s.Oracle[uint64, tiny, int, int]{CloneHistory: s.ValueCopy[tiny], InitialState: func() (tiny, error) { return 0, nil }, Advance: func(h tiny, _ int, _ []int, _ s.Disposition) (tiny, error) { return h, nil }, CheckState: func(tiny, uint64) ([]s.Check, error) { return nil, nil }, EstimatedStateBytes: func(tiny) (*uint64, error) { return u64(1), nil }}
	wrapped, e := s.WithOracle(m, oracle)
	if e != nil {
		t.Fatal(e)
	}
	value, e := wrapped.EstimatedStateBytes(s.OracleState[uint64, tiny]{})
	if e != nil || value != nil || s.OracleStateOverhead[uint64, tiny]() == 0 {
		t.Fatal(value, e)
	}
}

func TestBFSStateByteCapSnapshotsCallerValue(t *testing.T) {
	cases := []struct {
		name, phase      string
		initial, changed uint64
		states           int
		transitions      uint64
		retained         uint64
		termination      string
	}{
		{"raise-before-initial-admission", "initial", 4, 100, 0, 0, 0, "estimated_state_byte_limit"},
		{"lower-before-initial-admission", "initial", 15, 0, 3, 6, 15, "failure_found"},
		{"raise-before-successor-admission", "step", 14, 100, 2, 4, 10, "estimated_state_byte_limit"},
		{"lower-before-successor-admission", "step", 15, 0, 3, 6, 15, "failure_found"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			maximum := test.initial
			m := bfsCounter()
			if test.phase == "initial" {
				m.InitialState = func() (int, error) { maximum = test.changed; return 0, nil }
			} else {
				m.Step = func(state, input int) (s.Transition[int, int], error) {
					maximum = test.changed
					return s.Accept[int, int](state + input), nil
				}
			}
			report, e := s.EnumerateWithLimits(m, s.DefaultSearchConfig(), s.SearchLimits{MaxEstimatedStateBytes: &maximum})
			if e != nil || report.Termination != test.termination || report.States != test.states || report.Transitions != test.transitions || report.EstimatedRetainedStateBytes == nil || *report.EstimatedRetainedStateBytes != test.retained || maximum != test.changed {
				t.Fatal(report, e, maximum)
			}
		})
	}
}
