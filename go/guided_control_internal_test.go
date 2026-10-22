package statelessness

import "testing"

type guidedPollControl struct {
	armed bool
	polls int
}

func (c *guidedPollControl) stop() string {
	if c.armed {
		c.polls++
		if c.polls == 3 {
			return "cancelled"
		}
	}
	return ""
}

type guidedReuseDriver struct{}

func (guidedReuseDriver) candidate([]int, *Rng, int) ([]int, int, error) { return []int{1}, -1, nil }
func (guidedReuseDriver) initial(int, int) error                         { return nil }
func (guidedReuseDriver) transition(int, int, Transition[int, int], []int, int, uint64) error {
	return nil
}

func TestGuidedReusePollsAfterShortCircuitedEnabledResult(t *testing.T) {
	control := &guidedPollControl{}
	steps := 0
	g := GenerateModel[int, int, int]{Model: Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil },
		CloneState:   ValueCopy[int], CloneInput: ValueCopy[int], CloneOutput: ValueCopy[int],
		CheckState: func(int) ([]Check, error) { return nil, nil },
		Step: func(state, input int) (Transition[int, int], error) {
			steps++
			return Accept[int, int](state + input), nil
		},
	}, Generate: func(int, *Rng) (int, bool, error) { t.Fatal("enabled reuse generated"); return 0, false, nil },
		IsEnabled: func(int, int) (bool, error) { control.armed = true; return true, nil }}
	r, e := fuzzKernel(g, FuzzConfig{Cases: 1, MaxSteps: 1, MaxTransitions: 1}, control, nil, guidedReuseDriver{})
	if e != nil || r.Termination != "cancelled" || r.Transitions != 0 || steps != 0 || control.polls != 3 {
		t.Fatal(r, e, steps, control.polls)
	}
}

func TestOrdinaryReuseRetainsExistingControlBoundary(t *testing.T) {
	control := &guidedPollControl{}
	generated := false
	steps := 0
	g := GenerateModel[int, int, int]{Model: Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil },
		CloneState:   ValueCopy[int], CloneInput: ValueCopy[int], CloneOutput: ValueCopy[int],
		CheckState: func(int) ([]Check, error) { return nil, nil },
		Step: func(state, input int) (Transition[int, int], error) {
			steps++
			return Accept[int, int](state + input), nil
		},
	}, Generate: func(_ int, rng *Rng) (int, bool, error) { generated = true; return int(rng.NextU64() % 10), true, nil },
		IsEnabled: func(int, int) (bool, error) {
			if !generated {
				control.armed = true
			}
			generated = false
			return true, nil
		}}
	// The fixed seed-0 ordinary chooser reuses the first input in case two.
	r, e := fuzzKernel(g, FuzzConfig{Seed: 0, Cases: 2, MaxSteps: 3, MaxTransitions: 6, MutationPercent: 100}, control, nil, nil)
	if e != nil || r.Termination != "cancelled" || r.Transitions != 4 || steps != 4 || control.polls != 3 {
		t.Fatal(r, e, steps, control.polls)
	}
}
