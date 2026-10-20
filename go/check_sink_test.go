package statelessness_test

import (
	"errors"
	s "github.com/Jagalite/statelessness/go"
	"reflect"
	"strings"
	"testing"
)

func assertExpiredSink(t *testing.T, sink *s.CheckSink) {
	t.Helper()
	operations := []func() error{func() error { return sink.Push(s.Passed("late")) }, func() error { return sink.Extend([]s.Check{s.Passed("late")}) }, func() error { _, e := sink.Snapshot(); return e }, func() error { _, e := sink.Len(); return e }, func() error { _, e := sink.IsEmpty(); return e }, func() error { return sink.Reserve(1) }}
	for _, operation := range operations {
		if e := operation(); e == nil || !strings.Contains(e.Error(), "expired") {
			t.Fatal("escaped sink remained usable", e)
		}
	}
}
func TestSinkOwnershipCapacityAndLeaseRevocation(t *testing.T) {
	checks := make([]s.Check, 1, 16)
	checks[0] = s.Passed("prefix")
	base := &checks[:cap(checks)][0]
	var escaped, copyAlias *s.CheckSink
	original := s.Passed("owned")
	e := s.WithCheckSink(&checks, func(sink *s.CheckSink) error {
		escaped = sink
		copy := *sink
		copyAlias = &copy
		if n, e := sink.Len(); e != nil || n != 1 {
			t.Fatal(n, e)
		}
		if empty, e := sink.IsEmpty(); e != nil || empty {
			t.Fatal(empty, e)
		}
		if e := sink.Reserve(3); e != nil {
			return e
		}
		if e := sink.Push(original); e != nil {
			return e
		}
		snapshot, e := sink.Snapshot()
		if e != nil {
			return e
		}
		snapshot[0] = s.Passed("mutated")
		original.ID = "mutated"
		more := []s.Check{s.Skipped("duplicate", "reason"), s.Failed("duplicate", "")}
		if e = sink.Extend(more); e != nil {
			return e
		}
		more[0] = s.Passed("mutated")
		return nil
	})
	if e != nil || &checks[:cap(checks)][0] != base || !reflect.DeepEqual(checks, []s.Check{s.Passed("prefix"), s.Passed("owned"), s.Skipped("duplicate", "reason"), s.Failed("duplicate", "")}) {
		t.Fatal(checks, e)
	}
	assertExpiredSink(t, escaped)
	assertExpiredSink(t, copyAlias)
	for _, mode := range []string{"error", "panic", "ignored-invalid", "ignored-reserve"} {
		t.Run(mode, func(t *testing.T) {
			storage := []s.Check{}
			var escaped *s.CheckSink
			e := s.WithCheckSink(&storage, func(sink *s.CheckSink) error {
				escaped = sink
				if e := sink.Push(s.Passed("partial")); e != nil {
					return e
				}
				switch mode {
				case "error":
					return errors.New("checker failed")
				case "panic":
					panic("checker panicked")
				case "ignored-invalid":
					_ = sink.Push(s.Check{ID: "invalid", Status: "bogus"})
				case "ignored-reserve":
					_ = sink.Reserve(-1)
				}
				_ = sink.Push(s.Passed("after failure"))
				return nil
			})
			if e == nil || !reflect.DeepEqual(storage, []s.Check{s.Passed("partial")}) {
				t.Fatal(storage, e)
			}
			assertExpiredSink(t, escaped)
			// Expired errors cannot poison the next callback using the same backing buffer.
			if e = s.WithCheckSink(&storage, func(fresh *s.CheckSink) error { assertExpiredSink(t, escaped); return fresh.Push(s.Passed("fresh")) }); e != nil || storage[len(storage)-1].ID != "fresh" {
				t.Fatal(storage, e)
			}
		})
	}
}
func TestObservedIntoClearsAllErrorsAndPreservesCapacity(t *testing.T) {
	m := counter()
	m.Step = func(counterState, string) (s.Transition[counterState, string], error) {
		panic("observed must not step")
	}
	m.CheckState = func(counterState) ([]s.Check, error) { panic("legacy state must not run") }
	m.CheckTransition = func(counterState, string, s.Transition[counterState, string]) ([]s.Check, error) {
		panic("legacy edge must not run")
	}
	var escaped []*s.CheckSink
	m.CheckStateInto = func(_ counterState, sink *s.CheckSink) error {
		escaped = append(escaped, sink)
		return sink.Extend([]s.Check{s.Passed("same"), s.Skipped("skip", "reason")})
	}
	m.CheckTransitionInto = func(_ counterState, _ string, _ s.Transition[counterState, string], sink *s.CheckSink) error {
		escaped = append(escaped, sink)
		if n, e := sink.Len(); e != nil || n != 2 {
			t.Fatal(n, e)
		}
		return sink.Push(s.Passed("same"))
	}
	storage := make([]s.Check, 1, 16)
	storage[0] = s.Passed("sentinel")
	base := &storage[:cap(storage)][0]
	transition := s.Accept(counterState{1}, "changed")
	for sequence := uint64(1); sequence <= 3; sequence++ {
		if e := s.CheckObservedInto(m, counterState{}, "increment", transition, sequence, s.FullChecks(), &storage); e != nil || len(storage) != 3 || &storage[:cap(storage)][0] != base {
			t.Fatal(storage, e)
		}
		for _, sink := range escaped {
			assertExpiredSink(t, sink)
		}
	}
	for _, kind := range []string{"sequence", "policy", "validation", "checker", "panic", "snapshot"} {
		t.Run(kind, func(t *testing.T) {
			model := m
			sequence := uint64(1)
			policy := s.FullChecks()
			switch kind {
			case "sequence":
				sequence = 0
			case "policy":
				policy.StateEvery = 0
			case "validation":
				model.CloneOutput = nil
			case "checker":
				model.CheckTransitionInto = func(_ counterState, _ string, _ s.Transition[counterState, string], sink *s.CheckSink) error {
					_ = sink.Push(s.Passed("partial"))
					return errors.New("edge failed")
				}
			case "panic":
				model.CheckStateInto = func(_ counterState, sink *s.CheckSink) error {
					_ = sink.Push(s.Passed("partial"))
					panic("checker panic")
				}
			case "snapshot":
				model.CloneState = func(counterState) counterState { panic("clone panic") }
			}
			storage = storage[:1]
			storage[0] = s.Passed("old")
			if e := s.CheckObservedInto(model, counterState{}, "increment", transition, sequence, policy, &storage); e == nil || len(storage) != 0 || cap(storage) != 16 {
				t.Fatal(storage, e, cap(storage))
			}
		})
	}
	if e := s.CheckObservedInto(m, counterState{}, "increment", transition, 1, s.CheckPolicy{StateEvery: 2}, &storage); e != nil || !reflect.DeepEqual(storage, []s.Check{s.Skipped("stateless.state_checks", "periodic checking policy"), s.Skipped("stateless.transition_checks", "disabled by policy")}) {
		t.Fatal(storage, e)
	}
}
func TestAppendOnlyHooksReachEveryEngineAndOwnRetainedChecks(t *testing.T) {
	model := counter()
	model.Inputs = func(state counterState) (s.Iterator[string], error) {
		if state.value == 0 {
			return s.Values([]string{"increment"}), nil
		}
		return s.Values([]string{}), nil
	}
	model.CheckState = func(counterState) ([]s.Check, error) { panic("legacy state called") }
	model.CheckTransition = func(counterState, string, s.Transition[counterState, string]) ([]s.Check, error) {
		panic("legacy transition called")
	}
	states, edges := 0, 0
	model.CheckStateInto = func(state counterState, sink *s.CheckSink) error {
		states++
		if state.value > 0 {
			return sink.Push(s.Failed("bad", "broken"))
		}
		return sink.Push(s.Passed("bad"))
	}
	model.CheckTransitionInto = func(_ counterState, _ string, _ s.Transition[counterState, string], sink *s.CheckSink) error {
		edges++
		return sink.Push(s.Passed("edge"))
	}
	report, e := s.Enumerate(model, s.DefaultSearchConfig())
	if e != nil || report.Termination != "failure_found" {
		t.Fatal(report, e)
	}
	g := s.GenerateModel[counterState, string, string]{Model: model, Generate: func(counterState, *s.Rng) (string, bool, error) { return "increment", true, nil }, InputEqual: func(a, b string) (bool, error) { return a == b, nil }}
	fuzz, e := s.Fuzz(g, s.FuzzConfig{Cases: 1, MaxSteps: 1, MaxTransitions: 1})
	if e != nil || fuzz.Failure == nil {
		t.Fatal(fuzz, e)
	}
	shrunk, e := s.Shrink(g, *fuzz.Failure, s.DefaultShrinkConfig())
	if e != nil || !shrunk.ValidatedOriginal {
		t.Fatal(shrunk, e)
	}
	trace, e := s.Record(model, s.Values([]string{"increment"}), 10)
	if e != nil || trace.Termination != "property_failed" {
		t.Fatal(trace, e)
	}
	replay, e := s.Replay(model, trace, false)
	if e != nil || replay.Outcome != "exact" || !replay.FailureReproduced {
		t.Fatal(replay, e)
	}
	if states < 10 || edges < 5 {
		t.Fatal("append hooks were not integrated", states, edges)
	}
	// Append-only state models are explicitly supported; retained trace checks must
	// survive buffer clearing/reuse and mutation of a later caller-owned result.
	model.CheckState = nil
	trace, e = s.Record(model, s.Values([]string{"increment"}), 10)
	if e != nil {
		t.Fatal(e)
	}
	checks, e := s.CheckObserved(model, counterState{}, "increment", s.Accept(counterState{1}, "changed"), 1, s.FullChecks())
	if e != nil {
		t.Fatal(e)
	}
	checks[0] = s.Passed("mutated")
	if trace.InitialChecks[0].ID != "bad" || trace.Steps[0].Checks[0].Status != "failed" {
		t.Fatal(trace)
	}
}

func TestNestedCheckLeasesPreserveOuterAndRejectSwallowedChildFailures(t *testing.T) {
	actual := counter()
	var actualLease, oracleLease *s.CheckSink
	actual.CheckStateInto = func(_ counterState, sink *s.CheckSink) error {
		actualLease = sink
		return sink.Push(s.Passed("actual"))
	}
	oracle := s.Oracle[counterState, int, string, string]{InitialState: func() (int, error) { return 0, nil }, CloneHistory: s.ValueCopy[int], Advance: func(h int, _ string, _ []string, _ s.Disposition) (int, error) { return h, nil }, CheckStateInto: func(_ int, _ counterState, sink *s.CheckSink) error {
		oracleLease = sink
		assertExpiredSink(t, actualLease)
		snapshot, e := sink.Snapshot()
		if e != nil || !reflect.DeepEqual(snapshot, []s.Check{s.Passed("outer-before"), s.Passed("actual")}) {
			t.Fatal(snapshot, e)
		}
		return sink.Push(s.Passed("oracle"))
	}}
	wrapped, e := s.WithOracle(actual, oracle)
	if e != nil {
		t.Fatal(e)
	}
	outer := wrapped.Model
	outer.CheckStateInto = func(state s.OracleState[counterState, int], sink *s.CheckSink) error {
		if e := sink.Push(s.Passed("outer-before")); e != nil {
			return e
		}
		if e := wrapped.CheckStateInto(state, sink); e != nil {
			return e
		}
		assertExpiredSink(t, actualLease)
		assertExpiredSink(t, oracleLease)
		return sink.Push(s.Passed("outer-after"))
	}
	checks, e := s.CheckObserved(outer, s.OracleState[counterState, int]{}, "increment", s.Accept[s.OracleState[counterState, int], string](s.OracleState[counterState, int]{}), 1, s.CheckPolicy{StateEvery: 1})
	if e != nil || !reflect.DeepEqual(checks, []s.Check{s.Passed("outer-before"), s.Passed("actual"), s.Passed("oracle"), s.Passed("outer-after"), s.Skipped("stateless.transition_checks", "disabled by policy")}) {
		t.Fatal(checks, e)
	}
	actual.CheckStateInto = func(_ counterState, sink *s.CheckSink) error {
		_ = sink.Push(s.Passed("actual-before-invalid"))
		_ = sink.Push(s.Check{Status: "invalid"})
		return nil
	}
	wrapped, e = s.WithOracle(actual, oracle)
	if e != nil {
		t.Fatal(e)
	}
	outer = wrapped.Model
	outer.CheckStateInto = func(state s.OracleState[counterState, int], sink *s.CheckSink) error {
		_ = wrapped.CheckStateInto(state, sink)
		_ = sink.Push(s.Passed("must not append"))
		return nil
	}
	storage := []s.Check{s.Passed("old")}
	if e = s.CheckObservedInto(outer, s.OracleState[counterState, int]{}, "increment", s.Accept[s.OracleState[counterState, int], string](s.OracleState[counterState, int]{}), 1, s.FullChecks(), &storage); e == nil || len(storage) != 0 {
		t.Fatal("swallowed nested error became success", storage, e)
	}
}
func TestLegacyPartialErrorKeepsPrimaryAndAllSearchDiagnostics(t *testing.T) {
	model := bfsCounter()
	model.CheckState = func(state int) ([]s.Check, error) {
		if state == 0 {
			return []s.Check{s.Passed("initial")}, nil
		}
		return []s.Check{s.Passed("partial"), {Status: "invalid"}, s.Passed("after-invalid")}, errors.New("primary checker error")
	}
	model.Inputs = func(int) (s.Iterator[int], error) { return s.Values([]int{1}), nil }
	var partial []s.Check
	observer := s.SearchObserver[int, int, int]{CheckError: func(phase string, checks []s.Check, complete bool) error {
		if phase != "state" || complete {
			t.Fatal(phase, complete)
		}
		partial = checks
		return errors.New("secondary observer error")
	}}
	_, e := s.EnumerateObserved(model, s.DefaultSearchConfig(), s.SearchLimits{}, observer)
	if e == nil || !strings.Contains(e.Error(), "primary checker error") || !strings.Contains(e.Error(), "secondary observer error") || !reflect.DeepEqual(partial, []s.Check{s.Passed("partial")}) {
		t.Fatal(partial, e)
	}
}

func TestOracleLegacyFallbackDoesNotRecurseWhenAppendFieldsDisabled(t *testing.T) {
	model := counter()
	model.CheckTransition = func(counterState, string, s.Transition[counterState, string]) ([]s.Check, error) {
		return []s.Check{s.Passed("actual-edge")}, nil
	}
	oracle := s.Oracle[counterState, int, string, string]{InitialState: func() (int, error) { return 0, nil }, CloneHistory: s.ValueCopy[int], Advance: func(h int, _ string, _ []string, _ s.Disposition) (int, error) { return h + 1, nil }, CheckState: func(int, counterState) ([]s.Check, error) { return []s.Check{s.Passed("oracle-state")}, nil }, CheckTransition: func(s.OracleState[counterState, int], string, s.Transition[s.OracleState[counterState, int], string]) ([]s.Check, error) {
		return []s.Check{s.Passed("oracle-edge")}, nil
	}}
	wrapped, e := s.WithOracle(model, oracle)
	if e != nil {
		t.Fatal(e)
	}
	wrapped.CheckStateInto = nil
	wrapped.CheckTransitionInto = nil
	before, e := wrapped.InitialState()
	if e != nil {
		t.Fatal(e)
	}
	transition, e := wrapped.Step(before, "increment")
	if e != nil {
		t.Fatal(e)
	}
	checks, e := s.CheckObserved(wrapped.Model, before, "increment", transition, 1, s.FullChecks())
	if e != nil || !reflect.DeepEqual(checks, []s.Check{s.Passed("bound"), s.Passed("oracle-state"), s.Passed("actual-edge"), s.Passed("oracle-edge")}) {
		t.Fatal(checks, e)
	}
}

func TestStickyValidationPrecedesLaterPanicWithCauseIdentity(t *testing.T) {
	storage := []s.Check{}
	var first error
	var escaped *s.CheckSink
	e := s.WithCheckSink(&storage, func(sink *s.CheckSink) error {
		escaped = sink
		if e := sink.Push(s.Passed("valid-prefix")); e != nil {
			return e
		}
		first = sink.Push(s.Check{ID: "invalid", Status: "bogus"})
		panic("later panic must not replace validation")
	})
	var modelError *s.ModelError
	if e == nil || first == nil || !errors.Is(e, first) || !errors.As(e, &modelError) || !strings.Contains(e.Error(), "check sink callback") || strings.Contains(e.Error(), "later panic") || !reflect.DeepEqual(storage, []s.Check{s.Passed("valid-prefix")}) {
		t.Fatal(storage, first, e)
	}
	assertExpiredSink(t, escaped)
	model := bfsCounter()
	model.CheckStateInto = func(state int, sink *s.CheckSink) error {
		if state == 0 {
			return sink.Push(s.Passed("initial"))
		}
		_ = sink.Push(s.Passed("valid-prefix"))
		first = sink.Reserve(-1)
		panic("later checker panic")
	}
	var prefix []s.Check
	observer := s.SearchObserver[int, int, int]{CheckError: func(_ string, checks []s.Check, _ bool) error { prefix = checks; return nil }}
	_, e = s.EnumerateObserved(model, s.DefaultSearchConfig(), s.SearchLimits{}, observer)
	if e == nil || !errors.Is(e, first) || strings.Contains(e.Error(), "later checker panic") || !reflect.DeepEqual(prefix, []s.Check{s.Passed("valid-prefix")}) {
		t.Fatal(prefix, first, e)
	}
	storage = []s.Check{s.Passed("old")}
	e = s.CheckObservedInto(model, 0, 1, s.Accept[int, int](1), 1, s.FullChecks(), &storage)
	if e == nil || !errors.Is(e, first) || len(storage) != 0 {
		t.Fatal(storage, first, e)
	}
}
