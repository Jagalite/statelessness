package statelessness

import "testing"

func TestFuzzPreparedEdgePinsObserver(t *testing.T) {
	calls := 0
	model := Model[int, int, int]{CloneState: func(v int) int { return v }, CloneInput: func(v int) int { return v }, CloneOutput: func(v int) int { return v }, CheckState: func(int) ([]Check, error) { return nil, nil }}
	g := GenerateModel[int, int, int]{Model: model}
	observer := &SearchObserver[int, int, int]{Transition: func(int, int, Transition[int, int], []Check, []Check, []int) error { calls++; return nil }}
	edge := Accept[int, int](1)
	p := captureFuzzEdge(g, 0, 1, edge, observer.Transition)
	observer.Transition = nil
	checks := []Check{}
	var skipped uint64
	_, err := searchEdgeObservedPrepared(g, 0, 1, edge, []int{1}, &skipped, observer, &checks, p)
	if err != nil || calls != 1 {
		t.Fatalf("calls=%d err=%v", calls, err)
	}
}

type fuzzAlwaysContinue struct{}

func (fuzzAlwaysContinue) stop() string { return "" }

type reuseFuzzDriver struct{}

func (reuseFuzzDriver) candidate([]int, *Rng, int) ([]int, int, error) { return []int{7}, -1, nil }
func (reuseFuzzDriver) initial(*int, uint64) error                     { return nil }
func (reuseFuzzDriver) transition(*int, int, Transition[*int, int], []int, uint64, uint64) error {
	return nil
}

func TestFuzzUnusedAdmissionCaptureFailureWithNilEnabled(t *testing.T) {
	var pending *int
	copies := 0
	fromPending := 0
	steps := 0
	g := GenerateModel[*int, int, int]{Model: Model[*int, int, int]{
		InitialState: func() (*int, error) { v := 1; return &v, nil },
		CloneState: func(p *int) *int {
			copies++
			if p == pending && pending != nil {
				fromPending++
				if fromPending == 2 {
					panic("unused admission")
				}
			}
			v := *p
			q := &v
			if copies == 2 {
				pending = q
			}
			return q
		},
		CloneInput: func(v int) int { return v }, CloneOutput: func(v int) int { return v },
		Step:       func(p *int, _ int) (Transition[*int, int], error) { steps++; return Accept[*int, int](p), nil },
		CheckState: func(*int) ([]Check, error) { return nil, nil },
	}, Generate: func(*int, *Rng) (int, bool, error) { t.Fatal("unexpected generation"); return 0, false, nil }}
	report, err := fuzzKernel64(g, FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 1}, fuzzAlwaysContinue{}, nil, reuseFuzzDriver{})
	if err != nil || steps != 1 || report.Transitions != 1 || fromPending < 2 {
		t.Fatalf("report=%+v steps=%d pendingClones=%d err=%v", report, steps, fromPending, err)
	}
}

func TestFuzzPinsObserverBeforeSuccessorCopy(t *testing.T) {
	calls := 0
	var next *int
	var observer SearchObserver[*int, int, int]
	observer.Transition = func(*int, int, Transition[*int, int], []Check, []Check, []int) error { calls++; return nil }
	g := GenerateModel[*int, int, int]{Model: Model[*int, int, int]{
		InitialState: func() (*int, error) { v := 0; return &v, nil },
		CloneState: func(p *int) *int {
			if p == next && next != nil {
				observer.Transition = nil
			}
			v := *p
			return &v
		},
		CloneInput: func(v int) int { return v }, CloneOutput: func(v int) int { return v },
		Step: func(_ *int, _ int) (Transition[*int, int], error) {
			v := 1
			next = &v
			return Accept[*int, int](next), nil
		},
		CheckState: func(*int) ([]Check, error) { return nil, nil },
	}, Generate: func(*int, *Rng) (int, bool, error) { return 1, true, nil }}
	_, err := fuzzKernel64(g, FuzzConfig64{Cases: 1, MaxSteps: 2, MaxTransitions: 2}, fuzzAlwaysContinue{}, &observer, nil)
	if err != nil || calls != 1 {
		t.Fatalf("calls=%d err=%v", calls, err)
	}
}
