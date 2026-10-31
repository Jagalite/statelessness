package statelessness_test

import (
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

// The observer owns the history copied before checks; it is not copied twice.
func TestFuzzObserverHistoryIsCopiedOnce(t *testing.T) {
	clones, mark := 0, 0
	observed := 0
	g := s.GenerateModel[int, int, int]{Model: s.Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil },
		Step:         func(v, i int) (s.Transition[int, int], error) { return s.Accept[int, int](v + i), nil },
		CheckState:   func(int) ([]s.Check, error) { mark = clones; return nil, nil },
		CloneState:   s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		CloneInput: func(v int) int { clones++; return v },
	}, Generate: func(int, *s.Rng) (int, bool, error) { return 1, true, nil }}
	o := s.SearchObserver[int, int, int]{Transition: func(_ int, _ int, _ s.Transition[int, int], _, _ []s.Check, history []int) error {
		observed++
		// Only the observer's own edge-input copy runs after the state checks.
		if clones-mark != 1 || len(history) != observed {
			t.Errorf("step %d: %d input copies after checks, history=%v", observed, clones-mark, history)
		}
		return nil
	}}
	r, err := s.FuzzObserved64(g, s.FuzzConfig64{Cases: 1, MaxSteps: 3, MaxTransitions: 3}, s.RunLimits{}, o)
	if err != nil || observed != 3 || r.Transitions != 3 {
		t.Fatalf("report=%+v observed=%d err=%v", r, observed, err)
	}
}

// Mirrors Rust, which moves the executed inputs into the failure.
func TestFuzzFailureTakesOwnedInputsWithoutCopy(t *testing.T) {
	failing := false
	g := s.GenerateModel[int, int, int]{Model: s.Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil },
		Step:         func(v, i int) (s.Transition[int, int], error) { return s.Accept[int, int](v + i), nil },
		CheckState: func(v int) ([]s.Check, error) {
			if v == 2 {
				failing = true
				return []s.Check{s.Failed("bad", "two")}, nil
			}
			return nil, nil
		},
		CloneState: s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		CloneInput: func(v int) int {
			if failing {
				panic("failure inputs copied")
			}
			return v
		},
	}, Generate: func(int, *s.Rng) (int, bool, error) { return 1, true, nil }}
	r, err := s.Fuzz64(g, s.FuzzConfig64{Cases: 1, MaxSteps: 5, MaxTransitions: 5})
	if err != nil || r.Failure == nil || len(r.Failure.Inputs) != 2 || r.Termination != "failure_found" {
		t.Fatalf("report=%+v err=%v", r, err)
	}
}

// Guided feedback borrows the engine history and copies a prefix only when it
// retains a corpus entry, so a case whose feedback emits nothing stays linear.
func TestGuidedFeedbackDoesNotCopyHistoryPerTransition(t *testing.T) {
	const steps = 200
	clones := 0
	g := guidedCounter()
	g.CloneInput = func(v int) int { clones++; return v }
	f := counterFeedback()
	f.InitialFeatures = nil
	f.TransitionFeatures = func(int, int, s.Transition[int, int], *s.FeatureBuffer[int]) error { return nil }
	r, e := s.GuidedFuzz(g, s.FuzzConfig{Cases: 1, MaxSteps: steps, MaxTransitions: steps}, s.DefaultGuidanceConfig(), f)
	if e != nil || r.Transitions != steps || len(r.Guidance.Corpus) != 0 {
		t.Fatal(r, e)
	}
	// A per-transition prefix copy would need about steps*(steps+1)/2 clones.
	if clones > 8*steps {
		t.Fatalf("%d input clones for %d transitions", clones, steps)
	}
}
