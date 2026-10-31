package statelessness

import (
	"errors"
	"strings"
	"testing"
)

type edgeSnapshotBox struct{ value int }

func TestSearchInitialCapturesBeforeChecks(t *testing.T) {
	state := &edgeSnapshotBox{1}
	clone := func(v *edgeSnapshotBox) *edgeSnapshotBox { c := *v; return &c }
	var observer SearchObserver[*edgeSnapshotBox, int, int]
	called := false
	observer.Initial = func(v *edgeSnapshotBox, _ []Check) error {
		called = true
		if v.value != 1 {
			t.Fatalf("initial observer saw mutated state: %d", v.value)
		}
		return nil
	}
	m := Model[*edgeSnapshotBox, int, int]{CloneState: clone, CheckState: func(*edgeSnapshotBox) ([]Check, error) {
		state.value = 2
		observer.Initial = nil
		return nil, nil
	}}
	if _, err := searchInitialObserved(m, state, &observer); err != nil || !called {
		t.Fatalf("initial: %v, called=%v", err, called)
	}
}

func TestSearchInitialCaptureFailureDeferred(t *testing.T) {
	calls := 0
	m := Model[int, int, int]{CloneState: func(v int) int {
		calls++
		if calls == 1 {
			panic("capture failure")
		}
		return v
	}, CheckState: func(int) ([]Check, error) { return nil, errors.New("check failure") }}
	var phases []string
	o := SearchObserver[int, int, int]{Initial: func(int, []Check) error { t.Fatal("observer called"); return nil }, CheckError: func(p string, _ []Check, _ bool) error { phases = append(phases, p); return nil }}
	_, err := searchInitialObserved(m, 1, &o)
	if err == nil || !strings.Contains(err.Error(), "check failure") || len(phases) != 1 || phases[0] != "initial_state" {
		t.Fatalf("precedence: %v %v", err, phases)
	}
	// Without an initial observer no extra clone occurs before the state check.
	calls = 0
	m.CloneState = func(v int) int { calls++; return v }
	m.CheckState = func(int) ([]Check, error) { return nil, nil }
	if _, err := searchInitialObserved(m, 1, nil); err != nil || calls != 1 {
		t.Fatalf("unneeded clone: %v calls=%d", err, calls)
	}
}

func TestSearchEdgeCapturesBeforeStateChecks(t *testing.T) {
	before, input, after, output, history := &edgeSnapshotBox{1}, &edgeSnapshotBox{2}, &edgeSnapshotBox{3}, &edgeSnapshotBox{4}, &edgeSnapshotBox{5}
	clone := func(v *edgeSnapshotBox) *edgeSnapshotBox { c := *v; return &c }
	m := Model[*edgeSnapshotBox, *edgeSnapshotBox, *edgeSnapshotBox]{CloneState: clone, CloneInput: clone, CloneOutput: clone,
		CheckState: func(*edgeSnapshotBox) ([]Check, error) {
			before.value = 11
			input.value = 12
			after.value = 13
			output.value = 14
			history.value = 15
			return nil, nil
		},
		CheckTransition: func(b, i *edgeSnapshotBox, tr Transition[*edgeSnapshotBox, *edgeSnapshotBox]) ([]Check, error) {
			if b.value != 1 || i.value != 2 || tr.State.value != 3 || tr.Outputs[0].value != 4 {
				t.Fatal("transition check saw mutated edge")
			}
			return nil, nil
		},
	}
	observed := false
	observer := SearchObserver[*edgeSnapshotBox, *edgeSnapshotBox, *edgeSnapshotBox]{Transition: func(b, i *edgeSnapshotBox, tr Transition[*edgeSnapshotBox, *edgeSnapshotBox], _, _ []Check, h []*edgeSnapshotBox) error {
		observed = true
		if b.value != 1 || i.value != 2 || tr.State.value != 3 || tr.Outputs[0].value != 4 || h[0].value != 5 {
			t.Fatal("observer saw mutated edge")
		}
		return nil
	}}
	var skipped uint64
	_, err := searchEdgeObserved(GenerateModel[*edgeSnapshotBox, *edgeSnapshotBox, *edgeSnapshotBox]{Model: m}, before, input, Transition[*edgeSnapshotBox, *edgeSnapshotBox]{State: after, Outputs: []*edgeSnapshotBox{output}, Disposition: Accepted()}, []*edgeSnapshotBox{history}, &skipped, &observer)
	if err != nil || !observed {
		t.Fatalf("observation failed: %v", err)
	}
}

func TestSearchEdgeUnusedCaptureAndHistoryErrorStages(t *testing.T) {
	clone := func(v int) int {
		if v == 5 {
			panic("history clone")
		}
		return v
	}
	stateErr := errors.New("state check failure")
	m := Model[int, int, int]{CloneState: func(v int) int { return v }, CloneInput: clone, CloneOutput: func(v int) int { return v }, CheckState: func(int) ([]Check, error) { return nil, stateErr }}
	var skipped uint64
	var phases []string
	o := SearchObserver[int, int, int]{Transition: func(int, int, Transition[int, int], []Check, []Check, []int) error { return nil }, CheckError: func(p string, _ []Check, _ bool) error { phases = append(phases, p); return nil }}
	_, err := searchEdgeObserved(GenerateModel[int, int, int]{Model: m}, 1, 2, Accept[int, int](3), []int{5}, &skipped, &o)
	if err == nil || !strings.Contains(err.Error(), "state check failure") || len(phases) != 1 || phases[0] != "state" {
		t.Fatalf("state error precedence: %v %v", err, phases)
	}
	m.CheckState = func(int) ([]Check, error) { return nil, nil }
	phases = nil
	_, err = searchEdgeObserved(GenerateModel[int, int, int]{Model: m}, 1, 2, Accept[int, int](3), []int{5}, &skipped, &o)
	if err == nil || len(phases) != 0 {
		t.Fatalf("observer-only failure incorrectly reported as check error: %v %v", err, phases)
	}
	// Without a transition consumer, no input copy is required.
	m.CloneInput = func(int) int { panic("unused clone") }
	_, err = searchEdgeObserved(GenerateModel[int, int, int]{Model: m}, 1, 2, Accept[int, int](3), nil, &skipped, nil)
	if err != nil {
		t.Fatalf("unused snapshot: %v", err)
	}
}

func TestSearchEdgePinsObserverAndHistorySlots(t *testing.T) {
	history := []*edgeSnapshotBox{{5}}
	clone := func(v *edgeSnapshotBox) *edgeSnapshotBox { c := *v; return &c }
	var o SearchObserver[*edgeSnapshotBox, *edgeSnapshotBox, *edgeSnapshotBox]
	observed := false
	o.Transition = func(_, _ *edgeSnapshotBox, _ Transition[*edgeSnapshotBox, *edgeSnapshotBox], _, _ []Check, h []*edgeSnapshotBox) error {
		observed = true
		if h[0].value != 5 {
			t.Fatalf("history slot mutated: %d", h[0].value)
		}
		return nil
	}
	inputCalls := 0
	m := Model[*edgeSnapshotBox, *edgeSnapshotBox, *edgeSnapshotBox]{CloneState: clone, CloneOutput: clone, CloneInput: func(v *edgeSnapshotBox) *edgeSnapshotBox {
		inputCalls++
		if inputCalls == 1 {
			history[0] = &edgeSnapshotBox{99}
		}
		return clone(v)
	}, CheckState: func(*edgeSnapshotBox) ([]Check, error) { o.Transition = nil; return nil, nil }}
	var skipped uint64
	_, err := searchEdgeObserved(GenerateModel[*edgeSnapshotBox, *edgeSnapshotBox, *edgeSnapshotBox]{Model: m}, &edgeSnapshotBox{1}, &edgeSnapshotBox{2}, Accept[*edgeSnapshotBox, *edgeSnapshotBox](&edgeSnapshotBox{3}), history, &skipped, &o)
	if err != nil || !observed {
		t.Fatalf("observer replacement affected operation: %v", err)
	}
}
