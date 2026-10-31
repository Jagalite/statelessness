package statelessness_test

import (
	"errors"
	"reflect"
	"slices"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	s "github.com/Jagalite/statelessness/go"
)

func guidedCounter() s.GenerateModel[int, int, int] {
	return s.GenerateModel[int, int, int]{Model: s.Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil },
		Step:         func(state, input int) (s.Transition[int, int], error) { return s.Accept(state+input, state+input), nil },
		CheckState:   func(int) ([]s.Check, error) { return nil, nil },
		CloneState:   s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		EqualStates: func(a, b int) bool { return a == b }, HashState: func(int) uint64 { return 0 },
	}, Generate: func(int, *s.Rng) (int, bool, error) { return 1, true, nil }}
}
func guidedIdentity() s.FeedbackMetadata {
	return s.FeedbackMetadata{Name: "test", Version: 1, Build: "native"}
}
func intFeatures() s.FeatureKey[int] {
	return s.FeatureKey[int]{Clone: s.ValueCopy[int], Equal: func(a, b int) bool { return a == b }, Hash: func(int) uint64 { return 0 }}
}
func counterFeedback() s.Feedback[int, int, int, int] {
	return s.StateFeedback[int, int, int](guidedIdentity(), intFeatures(), func(state int) (int, error) { return state, nil })
}
func guidedConfig(cases, steps int) s.FuzzConfig {
	return s.FuzzConfig{Cases: cases, MaxSteps: steps, MaxTransitions: 1000}
}

func TestGuidedCollisionFIFOAndWitnessedEviction(t *testing.T) {
	g := guidedCounter()
	limits := s.GuidanceConfig{MaxCorpusEntries: 2, MaxCorpusInputs: 5, MaxFeatures: 5, MaxFeaturesPerObservation: 1}
	r, e := s.GuidedFuzz(g, guidedConfig(2, 4), limits, counterFeedback())
	if e != nil {
		t.Fatal(e)
	}
	f := r.Guidance
	if r.Cases != 2 || r.Transitions != 8 || r.Termination != "cases_completed" || f.Algorithm != "feature-corpus-v2" || f.UniqueFeatures != 5 || f.FeatureLimitReached || !f.CorpusLimitReached || f.CorpusInputs != 4 || f.EvictedPrefixes != 3 || f.DroppedPrefixes != 0 || f.MutationCases != 0 {
		t.Fatalf("report: %+v guidance: %+v", r, f)
	}
	want := []s.CorpusEntry[int]{{Inputs: []int{1, 1, 1, 1}, NewFeatures: 1, DiscoveredCase: 1, DiscoveredTransition: 4}}
	if !reflect.DeepEqual(f.Corpus, want) {
		t.Fatal(f.Corpus)
	}
	// Rediscovery in case two must not reinsert already witnessed, evicted keys.
	if f.Corpus[0].DiscoveredCase != 1 {
		t.Fatal("eviction forgot witnessed bits")
	}
}

func TestGuidedExactlyFilledLimitsAreNotSaturation(t *testing.T) {
	r, e := s.GuidedFuzz(guidedCounter(), guidedConfig(1, 2), s.GuidanceConfig{MaxCorpusEntries: 2, MaxCorpusInputs: 3, MaxFeatures: 3, MaxFeaturesPerObservation: 1}, counterFeedback())
	if e != nil || r.Guidance.FeatureLimitReached || r.Guidance.CorpusLimitReached || r.Guidance.CorpusInputs != 3 || len(r.Guidance.Corpus) != 2 {
		t.Fatal(r, e)
	}
	r, e = s.GuidedFuzz(guidedCounter(), guidedConfig(1, 3), s.GuidanceConfig{MaxCorpusEntries: 2, MaxCorpusInputs: 3, MaxFeatures: 3, MaxFeaturesPerObservation: 1}, counterFeedback())
	if e != nil || !r.Guidance.FeatureLimitReached || r.Guidance.CorpusLimitReached || r.Guidance.UniqueFeatures != 3 {
		t.Fatal(r, e)
	}
}

func TestGuidedDropRecoveryAndDuplicateEmission(t *testing.T) {
	g := guidedCounter()
	caseNumber := 0
	generated := 0
	g.InitialState = func() (int, error) { caseNumber++; generated = 0; return 0, nil }
	g.Generate = func(int, *s.Rng) (int, bool, error) {
		generated++
		if generated > 3 {
			return 0, false, nil
		}
		if caseNumber == 2 {
			if generated == 1 {
				return 2, true, nil
			}
			return 0, false, nil
		}
		if generated == 3 {
			return 0, true, nil
		}
		return 1, true, nil
	}
	f := counterFeedback()
	f.InitialFeatures = nil
	f.TransitionFeatures = func(_ int, _ int, after s.Transition[int, int], out *s.FeatureBuffer[int]) error {
		if after.State != 2 {
			return nil
		}
		if e := out.Push(2); e != nil {
			return e
		}
		return out.Push(2)
	}
	r, e := s.GuidedFuzz(g, guidedConfig(2, 3), s.GuidanceConfig{MaxCorpusEntries: 1, MaxCorpusInputs: 1, MaxFeatures: 1, MaxFeaturesPerObservation: 2}, f)
	if e != nil {
		t.Fatal(e)
	}
	want := []s.CorpusEntry[int]{{Inputs: []int{2}, NewFeatures: 0, DiscoveredCase: 2, DiscoveredTransition: 4}}
	if r.Transitions != 4 || r.Guidance.UniqueFeatures != 1 || r.Guidance.DroppedPrefixes != 1 || r.Guidance.EvictedPrefixes != 0 || !r.Guidance.CorpusLimitReached || r.Guidance.FeatureLimitReached || !reflect.DeepEqual(r.Guidance.Corpus, want) {
		t.Fatalf("%+v %+v", r, r.Guidance)
	}
}

func TestGuidedInitialWitnessRecoveryDoesNotRetainEmptyPrefix(t *testing.T) {
	g := guidedCounter()
	c := 0
	g.InitialState = func() (int, error) { c++; return 0, nil }
	f := counterFeedback()
	f.InitialFeatures = func(_ int, out *s.FeatureBuffer[int]) error {
		if c == 2 {
			return out.Push(7)
		}
		return nil
	}
	f.TransitionFeatures = func(_ int, _ int, after s.Transition[int, int], out *s.FeatureBuffer[int]) error {
		if after.State == 2 || c == 2 {
			return out.Push(7)
		}
		return nil
	}
	r, e := s.GuidedFuzz(g, guidedConfig(2, 2), s.GuidanceConfig{MaxCorpusEntries: 1, MaxCorpusInputs: 1, MaxFeatures: 1, MaxFeaturesPerObservation: 1}, f)
	if e != nil || r.Guidance.DroppedPrefixes != 1 || len(r.Guidance.Corpus) != 0 || r.Guidance.UniqueFeatures != 1 {
		t.Fatal(r, e)
	}
}

func TestGuidedBufferStickyFailureAndCallbackPrecedence(t *testing.T) {
	for _, initial := range []bool{false, true} {
		for _, own := range []bool{false, true} {
			for _, panics := range []bool{false, true} {
				f := counterFeedback()
				var escaped *s.FeatureBuffer[int]
				callback := func(out *s.FeatureBuffer[int]) error {
					escaped = out
					_ = out.Push(1)
					_ = out.Push(1)
					if panics {
						panic("own panic")
					}
					if own {
						return errors.New("own error")
					}
					return nil
				}
				f.InitialFeatures = nil
				f.TransitionFeatures = func(_ int, _ int, _ s.Transition[int, int], out *s.FeatureBuffer[int]) error { return callback(out) }
				if initial {
					f.InitialFeatures = func(_ int, out *s.FeatureBuffer[int]) error { return callback(out) }
				}
				limits := s.DefaultGuidanceConfig()
				limits.MaxFeaturesPerObservation = 1
				r, e := s.GuidedFuzz(guidedCounter(), guidedConfig(1, 1), limits, f)
				want := "feature limit"
				if own {
					want = "own error"
				}
				if panics {
					want = "own panic"
				}
				stage := "transition feedback"
				if initial {
					stage = "initial feedback"
				}
				if r != nil || e == nil || !strings.Contains(e.Error(), want) || !strings.Contains(e.Error(), stage) {
					t.Fatal(r, e, want)
				}
				if e := escaped.Push(2); e == nil || !strings.Contains(e.Error(), "expired") {
					t.Fatal("escaped buffer", e)
				}
			}
		}
	}
}

func TestGuidedRevokedCopiedLeaseCannotContaminateNextObservation(t *testing.T) {
	f := counterFeedback()
	f.InitialFeatures = nil
	var old *s.FeatureBuffer[int]
	var copied s.FeatureBuffer[int]
	f.TransitionFeatures = func(_ int, _ int, after s.Transition[int, int], out *s.FeatureBuffer[int]) error {
		if old != nil {
			if old.Push(99) == nil || copied.Push(98) == nil {
				t.Fatal("old lease revived")
			}
		}
		old = out
		copied = *out
		return out.Push(after.State)
	}
	r, e := s.GuidedFuzz(guidedCounter(), guidedConfig(1, 3), s.DefaultGuidanceConfig(), f)
	if e != nil || r.Guidance.UniqueFeatures != 3 {
		t.Fatal(r, e)
	}
	if old.Push(97) == nil || copied.Push(96) == nil {
		t.Fatal("lease alive after return")
	}
	var empty s.FeatureBuffer[int]
	var absent *s.FeatureBuffer[int]
	if empty.Push(0) == nil || absent.Push(0) == nil {
		t.Fatal("zero buffers writable")
	}
}

func TestGuidedMutableKeyAndCallbackArgumentOwnership(t *testing.T) {
	copyInts := func(v []int) []int { return slices.Clone(v) }
	sharedInput := []int{1}
	sharedFeature := []int{0}
	g := s.GenerateModel[[]int, []int, []int]{Model: s.Model[[]int, []int, []int]{
		InitialState: func() ([]int, error) { return []int{0}, nil }, CloneState: copyInts, CloneInput: copyInts, CloneOutput: copyInts,
		CheckState: func(state []int) ([]s.Check, error) {
			if state[0] > 3 {
				t.Fatal("feedback changed state", state)
			}
			state[0] = 90
			return nil, nil
		},
		Step: func(state, input []int) (s.Transition[[]int, []int], error) {
			next := state[0] + input[0]
			state[0] = 91
			input[0] = 92
			return s.Accept([]int{next}, []int{next}), nil
		},
	}, Generate: func(state []int, _ *s.Rng) ([]int, bool, error) { state[0] = 93; return sharedInput, true, nil }, IsEnabled: func(state, input []int) (bool, error) { state[0] = 94; input[0] = 95; return true, nil }}
	keys := s.FeatureKey[[]int]{Clone: copyInts, Hash: func(key []int) uint64 { key[0] = 96; return 0 }, Equal: func(a, b []int) bool { same := slices.Equal(a, b); a[0] = 97; b[0] = 98; return same }}
	f := s.Feedback[[]int, []int, []int, []int]{Key: keys, Metadata: func() (s.FeedbackMetadata, error) { return guidedIdentity(), nil }, TransitionFeatures: func(before, input []int, after s.Transition[[]int, []int], out *s.FeatureBuffer[[]int]) error {
		sharedFeature[0] = after.State[0]
		e := out.Push(sharedFeature)
		sharedFeature[0] = 99
		before[0] = 80
		input[0] = 81
		after.State[0] = 82
		after.Outputs[0][0] = 83
		return e
	}}
	r, e := s.GuidedFuzz(g, guidedConfig(1, 3), s.DefaultGuidanceConfig(), f)
	if e != nil || r.Guidance.UniqueFeatures != 3 || sharedInput[0] != 1 {
		t.Fatal(r, e, sharedInput)
	}
	sharedInput[0] = 9
	for _, entry := range r.Guidance.Corpus {
		for _, input := range entry.Inputs {
			if input[0] != 1 {
				t.Fatal("retained input aliases callback", entry)
			}
		}
	}
	r.Guidance.Corpus[0].Inputs[0][0] = 7
	if r.Guidance.Corpus[1].Inputs[0][0] != 1 {
		t.Fatal("corpus prefixes alias")
	}
}

func TestGuidedExactStatePreservesHistory(t *testing.T) {
	g := s.GenerateModel[[]int, int, int]{Model: s.Model[[]int, int, int]{
		InitialState: func() ([]int, error) { return []int{0}, nil },
		Step: func(state []int, input int) (s.Transition[[]int, int], error) {
			return s.Accept[[]int, int](append(state, input)), nil
		},
		CheckState: func([]int) ([]s.Check, error) { return nil, nil }, CloneState: func(v []int) []int { return slices.Clone(v) }, CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int],
		EqualStates: func(a, b []int) bool { return slices.Equal(a, b) },
	}, Generate: func([]int, *s.Rng) (int, bool, error) { return 0, true, nil }}
	r, e := s.GuidedFuzz(g, guidedConfig(1, 3), s.DefaultGuidanceConfig(), s.ExactStateFeedback(g.Model))
	if e != nil || r.Guidance.UniqueFeatures != 4 || r.Guidance.Feedback != (s.FeedbackMetadata{Name: "exact-state", Version: 1, Build: "clone-eq-hash-v1"}) {
		t.Fatal(r, e)
	}
	projected := s.StateFeedback[[]int, int, int](guidedIdentity(), intFeatures(), func(state []int) (int, error) { return state[0], nil })
	r, e = s.GuidedFuzz(g, guidedConfig(1, 3), s.DefaultGuidanceConfig(), projected)
	if e != nil || r.Guidance.UniqueFeatures != 1 || len(r.Guidance.Corpus) != 0 {
		t.Fatal(r, e)
	}
}

func TestGuidedValidationMetadataAndPreControlOrdering(t *testing.T) {
	f := counterFeedback()
	metadata := 0
	f.Metadata = func() (s.FeedbackMetadata, error) { metadata++; return s.FeedbackMetadata{}, nil }
	var cancelled atomic.Bool
	cancelled.Store(true)
	g := guidedCounter()
	g.InitialState = func() (int, error) { t.Fatal("pre-cancelled initial"); return 0, nil }
	bad := s.DefaultGuidanceConfig()
	bad.MaxFeatures = 0
	config := guidedConfig(1, 1)
	config.MutationPercent = 101
	r, e := s.GuidedFuzzWithLimits(g, config, bad, f, s.RunLimits{Cancellation: &cancelled})
	if r != nil || e == nil || !strings.Contains(e.Error(), "guidance") || metadata != 0 {
		t.Fatal(r, e, metadata)
	}
	r, e = s.GuidedFuzzWithLimits(g, config, s.DefaultGuidanceConfig(), f, s.RunLimits{Cancellation: &cancelled})
	if r != nil || e == nil || metadata != 0 {
		t.Fatal(r, e, metadata)
	}
	config.MutationPercent = 0
	r, e = s.GuidedFuzzWithLimits(g, config, s.DefaultGuidanceConfig(), f, s.RunLimits{Cancellation: &cancelled})
	if e != nil || r.Termination != "cancelled" || r.Cases != 0 || metadata != 1 || r.Guidance.Feedback != (s.FeedbackMetadata{}) {
		t.Fatal(r, e, metadata)
	}
	zero := time.Duration(0)
	cancelled.Store(false)
	r, e = s.GuidedFuzzWithLimits(g, config, s.DefaultGuidanceConfig(), f, s.RunLimits{MaxDuration: &zero})
	if e != nil || r.Termination != "deadline" || metadata != 2 {
		t.Fatal(r, e, metadata)
	}
}

func TestGuidedMetadataDurationCounts(t *testing.T) {
	f := counterFeedback()
	f.Metadata = func() (s.FeedbackMetadata, error) { time.Sleep(15 * time.Millisecond); return guidedIdentity(), nil }
	duration := time.Millisecond
	g := guidedCounter()
	g.InitialState = func() (int, error) { t.Fatal("expired metadata executed model"); return 0, nil }
	r, e := s.GuidedFuzzWithLimits(g, guidedConfig(1, 1), s.DefaultGuidanceConfig(), f, s.RunLimits{MaxDuration: &duration})
	if e != nil || r.Termination != "deadline" || r.Cases != 0 {
		t.Fatal(r, e)
	}
}

func TestGuidedPropertyObserverAndFeedbackOrdering(t *testing.T) {
	var events []string
	var cancelled atomic.Bool
	g := guidedCounter()
	g.CheckState = func(state int) ([]s.Check, error) {
		if state == 0 {
			events = append(events, "initial checks")
			return nil, nil
		}
		events = append(events, "state checks")
		cancelled.Store(true)
		return []s.Check{s.Failed("bad", "state")}, nil
	}
	g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) {
		events = append(events, "transition checks")
		return []s.Check{s.Skipped("skip", "reason")}, nil
	}
	f := counterFeedback()
	f.InitialFeatures = func(int, *s.FeatureBuffer[int]) error { events = append(events, "initial feedback"); return nil }
	f.TransitionFeatures = func(int, int, s.Transition[int, int], *s.FeatureBuffer[int]) error {
		t.Fatal("failing step fed back")
		return nil
	}
	observer := s.SearchObserver[int, int, int]{Initial: func(int, []s.Check) error { events = append(events, "initial observer"); return nil }, Transition: func(_ int, _ int, _ s.Transition[int, int], cs, ct []s.Check, h []int) error {
		events = append(events, "transition observer")
		if len(cs) != 1 || len(ct) != 1 || !slices.Equal(h, []int{1}) {
			t.Fatal(cs, ct, h)
		}
		return nil
	}}
	r, e := s.GuidedFuzzObserved(g, guidedConfig(1, 2), s.DefaultGuidanceConfig(), f, s.RunLimits{Cancellation: &cancelled}, observer)
	want := []string{"initial checks", "initial observer", "initial feedback", "state checks", "transition checks", "transition observer"}
	if e != nil || r.Termination != "failure_found" || r.SkippedChecks != 1 || r.Guidance.UniqueFeatures != 0 || !slices.Equal(events, want) {
		t.Fatal(r, e, events)
	}
}

func TestGuidedCheckerErrorPreservesPartialObserverBatches(t *testing.T) {
	g := guidedCounter()
	g.CheckState = func(state int) ([]s.Check, error) {
		if state == 0 {
			return nil, nil
		}
		return []s.Check{s.Failed("earlier", "failure")}, nil
	}
	g.CheckTransitionInto = func(_ int, _ int, _ s.Transition[int, int], out *s.CheckSink) error {
		_ = out.Push(s.Passed("partial"))
		return errors.New("transition checker failed")
	}
	var phases []string
	var complete []bool
	var batches [][]s.Check
	o := s.SearchObserver[int, int, int]{Transition: func(int, int, s.Transition[int, int], []s.Check, []s.Check, []int) error {
		t.Fatal("normal observation after error")
		return nil
	}, CheckError: func(phase string, checks []s.Check, done bool) error {
		phases = append(phases, phase)
		complete = append(complete, done)
		batches = append(batches, checks)
		return nil
	}}
	f := counterFeedback()
	f.TransitionFeatures = func(int, int, s.Transition[int, int], *s.FeatureBuffer[int]) error {
		t.Fatal("feedback after error")
		return nil
	}
	r, e := s.GuidedFuzzObserved(g, guidedConfig(1, 1), s.DefaultGuidanceConfig(), f, s.RunLimits{}, o)
	if r != nil || e == nil || !strings.Contains(e.Error(), "transition checker failed") || !slices.Equal(phases, []string{"state", "transition"}) || !slices.Equal(complete, []bool{true, false}) || len(batches[0]) != 1 || len(batches[1]) != 1 {
		t.Fatal(r, e, phases, complete, batches)
	}
}

func TestGuidedFeedbackCancellationLearnsBeforeStop(t *testing.T) {
	for _, initial := range []bool{true, false} {
		var cancelled atomic.Bool
		f := counterFeedback()
		emit := func(state int, out *s.FeatureBuffer[int]) error { cancelled.Store(true); return out.Push(state) }
		if initial {
			f.InitialFeatures = emit
		} else {
			f.InitialFeatures = nil
			f.TransitionFeatures = func(_ int, _ int, after s.Transition[int, int], out *s.FeatureBuffer[int]) error {
				return emit(after.State, out)
			}
		}
		r, e := s.GuidedFuzzWithLimits(guidedCounter(), guidedConfig(1, 3), s.DefaultGuidanceConfig(), f, s.RunLimits{Cancellation: &cancelled})
		want := uint64(1)
		corpus := 1
		if initial {
			want = 0
			corpus = 0
		}
		if e != nil || r.Termination != "cancelled" || r.Transitions != want || r.Guidance.UniqueFeatures != 1 || len(r.Guidance.Corpus) != corpus {
			t.Fatal(r, e)
		}
	}
}

func TestGuidedBudgetEdgesAndDefaultEmptyFeedback(t *testing.T) {
	for _, test := range []struct {
		cases, steps int
		cap          uint64
		wantCases    int
		trans        uint64
		term         string
	}{{0, 2, 0, 0, 0, "cases_completed"}, {2, 0, 0, 2, 0, "cases_completed"}, {2, 1, 0, 1, 0, "transition_limit"}, {1, 1, 1, 1, 1, "cases_completed"}, {2, 1, 1, 2, 1, "transition_limit"}} {
		f := counterFeedback()
		f.InitialFeatures = nil
		initials := 0
		g := guidedCounter()
		g.InitialState = func() (int, error) { initials++; return 0, nil }
		r, e := s.GuidedFuzz(g, s.FuzzConfig{Cases: test.cases, MaxSteps: test.steps, MaxTransitions: test.cap}, s.DefaultGuidanceConfig(), f)
		if e != nil || r.Cases != test.wantCases || initials != test.wantCases || r.Transitions != test.trans || r.Termination != test.term || r.Guidance.UniqueFeatures != int(test.trans) {
			t.Fatal(test, r, e)
		}
	}
	ordinary, e := s.Fuzz(guidedCounter(), guidedConfig(1, 1))
	if e != nil || ordinary.Guidance != nil {
		t.Fatal("ordinary gained guidance", ordinary, e)
	}
}

func TestGuidedFeatureHookPanicsAndReentrantPush(t *testing.T) {
	for _, hook := range []string{"clone", "hash", "equal", "metadata", "projection"} {
		t.Run(hook, func(t *testing.T) {
			f := counterFeedback()
			switch hook {
			case "clone":
				f.Key.Clone = func(int) int { panic("clone failed") }
			case "hash":
				f.Key.Hash = func(int) uint64 { panic("hash failed") }
			case "equal":
				f.Key.Equal = func(int, int) bool { panic("equal failed") }
			case "metadata":
				f.Metadata = func() (s.FeedbackMetadata, error) { panic("metadata failed") }
			case "projection":
				f = s.StateFeedback[int, int, int](guidedIdentity(), intFeatures(), func(int) (int, error) { panic("projection failed") })
			}
			r, e := s.GuidedFuzz(guidedCounter(), guidedConfig(1, 2), s.DefaultGuidanceConfig(), f)
			var modelError *s.ModelError
			if r != nil || !errors.As(e, &modelError) || !strings.Contains(e.Error(), hook+" failed") {
				t.Fatal(r, e)
			}
		})
	}
	f := counterFeedback()
	f.InitialFeatures = nil
	var active *s.FeatureBuffer[int]
	f.Key.Clone = func(v int) int {
		if active != nil {
			_ = active.Push(9)
		}
		return v
	}
	f.TransitionFeatures = func(_ int, _ int, _ s.Transition[int, int], out *s.FeatureBuffer[int]) error {
		active = out
		_ = out.Push(1)
		active = nil
		return nil
	}
	r, e := s.GuidedFuzz(guidedCounter(), guidedConfig(1, 1), s.DefaultGuidanceConfig(), f)
	if r != nil || e == nil || !strings.Contains(e.Error(), "reentrant") {
		t.Fatal(r, e)
	}
}

func TestGuidedRejectedTransitionsTeachAfterChecks(t *testing.T) {
	g := guidedCounter()
	g.Step = func(state, input int) (s.Transition[int, int], error) {
		return s.Transition[int, int]{State: state + input, Outputs: []int{}, Disposition: s.Rejected("fixture")}, nil
	}
	checked := 0
	g.CheckTransition = func(_ int, _ int, after s.Transition[int, int]) ([]s.Check, error) {
		checked++
		if after.Disposition.Kind != "rejected" {
			t.Fatal(after)
		}
		return nil, nil
	}
	r, e := s.GuidedFuzz(g, guidedConfig(1, 2), s.DefaultGuidanceConfig(), counterFeedback())
	if e != nil || r.Transitions != 2 || checked != 2 || r.Guidance.UniqueFeatures != 3 || len(r.Guidance.Corpus) != 2 {
		t.Fatal(r, e)
	}
}

func TestGuidedFeedbackErrorWinsOverCancellation(t *testing.T) {
	var cancelled atomic.Bool
	f := counterFeedback()
	f.TransitionFeatures = func(_ int, _ int, _ s.Transition[int, int], out *s.FeatureBuffer[int]) error {
		_ = out.Push(99)
		cancelled.Store(true)
		return errors.New("cancel with error")
	}
	r, e := s.GuidedFuzzWithLimits(guidedCounter(), guidedConfig(1, 2), s.DefaultGuidanceConfig(), f, s.RunLimits{Cancellation: &cancelled})
	if r != nil || e == nil || !strings.Contains(e.Error(), "cancel with error") {
		t.Fatal(r, e)
	}
}

func TestGuidedObserverErrorsRemainNativeEngineErrors(t *testing.T) {
	for _, panicInstead := range []bool{false, true} {
		f := counterFeedback()
		f.TransitionFeatures = func(int, int, s.Transition[int, int], *s.FeatureBuffer[int]) error {
			t.Fatal("feedback after observer error")
			return nil
		}
		o := s.SearchObserver[int, int, int]{Transition: func(int, int, s.Transition[int, int], []s.Check, []s.Check, []int) error {
			if panicInstead {
				panic("observer failed")
			}
			return errors.New("observer failed")
		}}
		r, e := s.GuidedFuzzObserved(guidedCounter(), guidedConfig(1, 1), s.DefaultGuidanceConfig(), f, s.RunLimits{}, o)
		if r != nil || e == nil || !strings.Contains(e.Error(), "observer failed") {
			t.Fatal(r, e)
		}
	}
}

type guidedFormattingError struct{ onFormat func() }

func (e guidedFormattingError) Error() string { e.onFormat(); return "formatted feedback error" }

func TestGuidedLeaseExpiresBeforeErrorAndPanicFormatting(t *testing.T) {
	for _, panicInstead := range []bool{false, true} {
		formatted := false
		f := counterFeedback()
		f.InitialFeatures = nil
		f.TransitionFeatures = func(_ int, _ int, _ s.Transition[int, int], out *s.FeatureBuffer[int]) error {
			owned := *out
			e := guidedFormattingError{onFormat: func() {
				formatted = true
				if out.Push(99) == nil || owned.Push(98) == nil {
					t.Error("formatting retained a writable lease")
				}
			}}
			if panicInstead {
				panic(e)
			}
			return e
		}
		r, e := s.GuidedFuzz(guidedCounter(), guidedConfig(1, 1), s.DefaultGuidanceConfig(), f)
		if r != nil || e == nil || !formatted || !strings.Contains(e.Error(), "formatted feedback error") {
			t.Fatal(r, e, formatted)
		}
	}
	f := counterFeedback()
	var escaped *s.FeatureBuffer[int]
	f.InitialFeatures = func(_ int, out *s.FeatureBuffer[int]) error { escaped = out; return out.Push(0) }
	f.Key.Hash = func(int) uint64 {
		if escaped.Push(99) == nil {
			t.Fatal("hash callback retained writable lease")
		}
		return 0
	}
	r, e := s.GuidedFuzz(guidedCounter(), guidedConfig(1, 1), s.DefaultGuidanceConfig(), f)
	if e != nil || r.Guidance.UniqueFeatures != 2 {
		t.Fatal(r, e)
	}
}

func TestGuidedObserverMutationCannotReachFeedback(t *testing.T) {
	copyInts := func(v []int) []int { return slices.Clone(v) }
	g := s.GenerateModel[[]int, []int, []int]{Model: s.Model[[]int, []int, []int]{
		InitialState: func() ([]int, error) { return []int{0}, nil }, CloneState: copyInts, CloneInput: copyInts, CloneOutput: copyInts,
		CheckState: func([]int) ([]s.Check, error) { return []s.Check{s.Passed("intact")}, nil },
		Step: func(state, input []int) (s.Transition[[]int, []int], error) {
			return s.Accept([]int{state[0] + input[0]}, []int{state[0] + input[0]}), nil
		},
	}, Generate: func([]int, *s.Rng) ([]int, bool, error) { return []int{1}, true, nil }}
	f := s.StateFeedback[[]int, []int, []int](guidedIdentity(), intFeatures(), func(state []int) (int, error) { return state[0], nil })
	inner := f.TransitionFeatures
	f.TransitionFeatures = func(before, input []int, after s.Transition[[]int, []int], out *s.FeatureBuffer[int]) error {
		if input[0] != 1 || after.State[0] != before[0]+1 || after.Outputs[0][0] != after.State[0] {
			t.Fatal("observer changed feedback", before, input, after)
		}
		return inner(before, input, after, out)
	}
	o := s.SearchObserver[[]int, []int, []int]{Initial: func(state []int, checks []s.Check) error {
		state[0] = 91
		checks[0] = s.Failed("mutated", "observer")
		return nil
	}, Transition: func(before, input []int, after s.Transition[[]int, []int], checks, _ []s.Check, history [][]int) error {
		before[0] = 92
		input[0] = 93
		after.State[0] = 94
		after.Outputs[0][0] = 95
		checks[0] = s.Failed("mutated", "observer")
		history[0][0] = 96
		return nil
	}}
	r, e := s.GuidedFuzzObserved(g, guidedConfig(1, 2), s.DefaultGuidanceConfig(), f, s.RunLimits{}, o)
	if e != nil || r.Failure != nil || r.Guidance.UniqueFeatures != 3 || r.Guidance.Corpus[1].Inputs[0][0] != 1 {
		t.Fatal(r, e)
	}
}

func TestGuidedDoesNotCloneUnusedPreviousSequence(t *testing.T) {
	afterFeedback := false
	g := guidedCounter()
	g.CloneInput = func(value int) int {
		if afterFeedback {
			panic("unexpected case-end clone")
		}
		return value
	}
	f := counterFeedback()
	f.InitialFeatures = nil
	f.TransitionFeatures = func(int, int, s.Transition[int, int], *s.FeatureBuffer[int]) error { afterFeedback = true; return nil }
	r, e := s.GuidedFuzz(g, guidedConfig(1, 1), s.DefaultGuidanceConfig(), f)
	if e != nil || r.Termination != "cases_completed" || r.Transitions != 1 {
		t.Fatal(r, e)
	}
	// Ordinary fuzz also moves the executed case into its previous sequence,
	// as Rust does, rather than copying it at case end.
	afterFeedback = false
	g.CheckTransition = func(int, int, s.Transition[int, int]) ([]s.Check, error) { afterFeedback = true; return nil, nil }
	r, e = s.Fuzz(g, guidedConfig(1, 1))
	if e != nil || r.Termination != "cases_completed" || r.Transitions != 1 {
		t.Fatal(r, e)
	}
}

func TestGuidedFeedbackRetainsInputBeforeEnabled(t *testing.T) {
	var generatedCopy []int
	copyInput := func(v []int) []int {
		result := slices.Clone(v)
		if generatedCopy == nil {
			generatedCopy = result
		}
		return result
	}
	clone := func(v []int) []int { return slices.Clone(v) }
	g := s.GenerateModel[[]int, []int, []int]{Model: s.Model[[]int, []int, []int]{
		InitialState: func() ([]int, error) { return []int{0}, nil }, CloneState: clone, CloneInput: copyInput, CloneOutput: clone,
		CheckState: func([]int) ([]s.Check, error) { return nil, nil },
		CheckTransition: func(_ []int, input []int, _ s.Transition[[]int, []int]) ([]s.Check, error) {
			if input[0] != 1 {
				t.Fatalf("check input: %v", input)
			}
			return nil, nil
		},
		Step: func(state, input []int) (s.Transition[[]int, []int], error) {
			if input[0] != 1 {
				t.Fatalf("step input: %v", input)
			}
			return s.Accept[[]int, []int](state), nil
		},
	}, Generate: func([]int, *s.Rng) ([]int, bool, error) { return []int{1}, true, nil },
		IsEnabled: func([]int, []int) (bool, error) { generatedCopy[0] = 99; return true, nil },
	}
	f := s.StateFeedback[[]int, []int, []int](guidedIdentity(), intFeatures(), func(state []int) (int, error) { return state[0], nil })
	f.TransitionFeatures = func(_ []int, input []int, _ s.Transition[[]int, []int], _ *s.FeatureBuffer[int]) error {
		if input[0] != 1 {
			t.Fatalf("feedback input: %v", input)
		}
		return nil
	}
	_, err := s.GuidedFuzz(g, guidedConfig(1, 1), s.DefaultGuidanceConfig(), f)
	if err != nil {
		t.Fatal(err)
	}
}
