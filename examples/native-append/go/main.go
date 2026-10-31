package main

import (
	"encoding/json"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"os"
	"slices"
	"strconv"
	"strings"
)

func must(err error) {
	if err != nil {
		panic(err)
	}
}

var stateIDs = []string{"threshold.bound", "state.appended"}
var allIDs = []string{"threshold.bound", "state.appended", "edge.checked", "edge.appended"}

func inspect(sink *s.CheckSink, expected []string) {
	checks, err := sink.Snapshot()
	must(err)
	n, err := sink.Len()
	must(err)
	empty, err := sink.IsEmpty()
	must(err)
	ids := make([]string, len(checks))
	for i, c := range checks {
		ids[i] = c.ID
	}
	if n != len(expected) || empty != (n == 0) || !slices.Equal(ids, expected) {
		panic("sink inspection/order mismatch")
	}
}
func expired(sink *s.CheckSink) {
	operations := []func() error{func() error { return sink.Push(s.Passed("expired")) }, func() error { return sink.Extend([]s.Check{s.Passed("expired")}) },
		func() error { _, e := sink.Snapshot(); return e }, func() error { _, e := sink.Len(); return e }, func() error { return sink.Reserve(1) }, func() error { _, e := sink.IsEmpty(); return e }}
	for _, operation := range operations {
		err := operation()
		if err == nil || !strings.Contains(strings.ToLower(err.Error()), "expired") {
			panic("escaped sink operation succeeded")
		}
	}
}

type stats struct {
	stateCalls, transitionCalls, stepCalls, legacyCalls int
	leases                                              []*s.CheckSink
	nestedExpired, outerActive                          bool
}
type app struct {
	model     s.Model[int, int, string]
	generated s.GenerateModel[int, int, string]
	stats     *stats
}

func application(failTransition, nested bool) app {
	stats := &stats{}
	m := s.Model[int, int, string]{
		InitialState: func() (int, error) { return 0, nil }, Inputs: func(int) (s.Iterator[int], error) { return s.Values([]int{0, 1}), nil }, EqualStates: func(a, b int) bool { return a == b },
		Step: func(state, input int) (s.Transition[int, string], error) {
			stats.stepCalls++
			if input != 0 && input != 1 {
				return s.Transition[int, string]{}, fmt.Errorf("expected zero or one")
			}
			if input == 0 {
				return s.Accept[int, string](state), nil
			}
			return s.Accept[int, string](state+input, "changed"), nil
		},
		CheckState: func(int) ([]s.Check, error) {
			stats.legacyCalls++
			return nil, fmt.Errorf("legacy state checker invoked")
		},
		CheckTransition: func(int, int, s.Transition[int, string]) ([]s.Check, error) {
			stats.legacyCalls++
			return nil, fmt.Errorf("legacy transition checker invoked")
		},
		CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[string],
		Codec: &s.Codec[int, int, string]{Metadata: func() (s.Metadata, error) { return s.Identity("native-append-threshold", "application-v1"), nil },
			EncodeState: func(v int) ([]byte, error) { return []byte(strconv.Itoa(v)), nil }, DecodeState: func(v []byte) (int, error) { return strconv.Atoi(string(v)) },
			EncodeInput: func(v int) ([]byte, error) { return []byte(strconv.Itoa(v)), nil }, DecodeInput: func(v []byte) (int, error) {
				if string(v) != "0" && string(v) != "1" {
					return 0, fmt.Errorf("expected zero or one")
				}
				return strconv.Atoi(string(v))
			}, EncodeOutput: func(v string) ([]byte, error) { return []byte(v), nil }},
	}
	m.CheckStateInto = func(state int, sink *s.CheckSink) error {
		stats.stateCalls++
		stats.leases = append(stats.leases, sink)
		inspect(sink, []string{})
		must(sink.Reserve(2))
		first := s.Passed("threshold.bound")
		if state >= 3 {
			first = s.Failed("threshold.bound", "at least three")
		}
		must(sink.Push(first))
		inspect(sink, []string{"threshold.bound"})
		if nested {
			child := application(false, false)
			storage := []s.Check{}
			must(s.CheckObservedInto(child.model, 0, 1, s.Accept[int, string](1, "changed"), 1, s.FullChecks(), &storage))
			for _, lease := range child.stats.leases {
				expired(lease)
			}
			stats.nestedExpired = true
			inspect(sink, []string{"threshold.bound"})
			stats.outerActive = true
		}
		must(sink.Extend([]s.Check{s.Passed("state.appended")}))
		inspect(sink, stateIDs)
		return nil
	}
	m.CheckTransitionInto = func(_ int, _ int, _ s.Transition[int, string], sink *s.CheckSink) error {
		stats.transitionCalls++
		stats.leases = append(stats.leases, sink)
		inspect(sink, stateIDs)
		must(sink.Reserve(2))
		must(sink.Push(s.Passed("edge.checked")))
		inspect(sink, allIDs[:3])
		if failTransition {
			return fmt.Errorf("application checker failure")
		}
		must(sink.Extend([]s.Check{s.Passed("edge.appended")}))
		inspect(sink, allIDs)
		return nil
	}
	generated := s.GenerateModel[int, int, string]{Model: m, Generate: func(_ int, rng *s.Rng) (int, bool, error) { i, _ := rng.Index(2); return 1 - int(i), true, nil }, IsEnabled: func(_ int, i int) (bool, error) { return i == 0 || i == 1, nil }, InputEqual: func(a, b int) (bool, error) { return a == b, nil }}
	return app{m, generated, stats}
}
func controls() map[string]any {
	a := application(false, false)
	storage := []s.Check{s.Passed("stale")}
	must(s.CheckObservedInto(a.model, 0, 1, s.Accept[int, string](1, "changed"), 1, s.FullChecks(), &storage))
	ids := []string{}
	for _, c := range storage {
		ids = append(ids, c.ID)
	}
	for _, lease := range a.stats.leases {
		expired(lease)
	}
	failed := application(true, false)
	failedStorage := []s.Check{s.Passed("stale")}
	err := s.CheckObservedInto(failed.model, 0, 1, s.Accept[int, string](1, "changed"), 1, s.FullChecks(), &failedStorage)
	if err == nil || !strings.Contains(err.Error(), "application checker failure") {
		panic("expected application checker error missing")
	}
	for _, lease := range failed.stats.leases {
		expired(lease)
	}
	nested := application(false, true)
	nestedStorage := []s.Check{}
	must(s.CheckObservedInto(nested.model, 0, 1, s.Accept[int, string](1, "changed"), 1, s.FullChecks(), &nestedStorage))
	return map[string]any{"ids": ids, "step_calls": a.stats.stepCalls, "state_calls": a.stats.stateCalls, "transition_calls": a.stats.transitionCalls,
		"error_cleared": len(failedStorage) == 0, "error_step_calls": failed.stats.stepCalls, "error_state_calls": failed.stats.stateCalls, "error_transition_calls": failed.stats.transitionCalls,
		"escaped_sink_expired": true, "escaped_operations": []string{"push", "extend", "snapshot", "length", "reserve", "is_empty"}, "nested_child_expired": nested.stats.nestedExpired, "outer_active_after_nested": nested.stats.outerActive,
		"legacy_calls": a.stats.legacyCalls + failed.stats.legacyCalls + nested.stats.legacyCalls}
}
func main() {
	if len(os.Args) != 3 {
		panic("expected record|replay PATH")
	}
	mode, path := os.Args[1], os.Args[2]
	var receipt map[string]any
	if mode == "record" {
		control := controls()
		a := application(false, false)
		bfs, err := s.Enumerate(a.model, s.DefaultSearchConfig())
		must(err)
		found, err := s.Fuzz(a.generated, s.FuzzConfig{Seed: 0, Cases: 4, MaxSteps: 32, MaxTransitions: 128, MutationPercent: 0})
		must(err)
		if bfs.Failure == nil || found.Failure == nil {
			panic("search witness missing")
		}
		reduced, err := s.Shrink(a.generated, *found.Failure, s.DefaultShrinkConfig())
		must(err)
		if !slices.Equal(reduced.Minimized.Inputs, []int{1, 1, 1}) || !reduced.ValidatedOriginal {
			panic("shrink mismatch")
		}
		trace, err := s.Record(a.model, s.Values(reduced.Minimized.Inputs), 10)
		must(err)
		data, err := s.MarshalTrace(trace)
		must(err)
		restored, err := s.ParseTrace(data)
		must(err)
		replayed, err := s.Replay(a.model, restored, false)
		must(err)
		if replayed.Outcome != "exact" || !replayed.FailureReproduced {
			panic("local witness replay mismatch")
		}
		must(os.WriteFile(path, data, 0600))
		receipt = map[string]any{"controls": control, "bfs": map[string]any{"states": bfs.States, "transitions": bfs.Transitions, "witness": bfs.Failure.Inputs}, "fuzz": map[string]any{"original_steps": len(found.Failure.Inputs), "minimized_steps": len(reduced.Minimized.Inputs), "shrink_attempts": reduced.Attempts, "validated_original": reduced.ValidatedOriginal}, "legacy_calls": a.stats.legacyCalls, "append_state_calls": a.stats.stateCalls, "append_transition_calls": a.stats.transitionCalls}
	} else if mode == "replay" {
		a := application(false, false)
		data, err := os.ReadFile(path)
		must(err)
		trace, err := s.ParseTrace(data)
		must(err)
		result, err := s.Replay(a.model, trace, false)
		must(err)
		receipt = map[string]any{"outcome": result.Outcome, "failure_reproduced": result.FailureReproduced, "steps": len(trace.Steps), "legacy_calls": a.stats.legacyCalls, "append_state_calls": a.stats.stateCalls, "append_transition_calls": a.stats.transitionCalls}
	} else {
		panic("expected record or replay")
	}
	must(json.NewEncoder(os.Stdout).Encode(receipt))
}
