package statelessness

import "fmt"

// SearchObserver receives defensive snapshots of checked work, including the
// failing transition and its entire executed history. Callback errors/panics are
// explicit engine errors. CheckError marks incomplete batches; these are never
// presented as successful transition observations or property reproductions.
type SearchObserver[S, I, O any] struct {
	// NeedsHistory opts BFS into initial-rooted input histories (nil means false).
	// Fuzz always supplies its executed case prefix, independent of this hook.
	NeedsHistory func() bool
	Initial      func(S, []Check) error
	Transition   func(S, I, Transition[S, O], []Check, []Check, []I) error
	CheckError   func(phase string, checks []Check, complete bool) error
}

func FuzzObserved[S, I, O any](g GenerateModel[S, I, O], config FuzzConfig, limits RunLimits, observer SearchObserver[S, I, O]) (*FuzzReport[I], error) {
	return fuzzObserved(g, config, limits, &observer)
}
func copyChecks(checks []Check) []Check { return append([]Check{}, checks...) }

func observerCheckError[S, I, O any](observer *SearchObserver[S, I, O], phase string, checks []Check, complete bool) error {
	if observer == nil || observer.CheckError == nil {
		return nil
	}
	_, e := call("observer check error", func() (struct{}, error) { return struct{}{}, observer.CheckError(phase, copyChecks(checks), complete) })
	return e
}
func searchInitialObserved[S, I, O any](m Model[S, I, O], state S, observer *SearchObserver[S, I, O]) ([]Check, error) {
	checks := []Check{}
	return searchInitialObservedInto(m, state, observer, &checks)
}
func searchInitialObservedInto[S, I, O any](m Model[S, I, O], state S, observer *SearchObserver[S, I, O], checks *[]Check) ([]Check, error) {
	resetChecks(checks)
	e := stateChecksStageBatch("initial check_state", m, state, &checkBatch{checks: checks})
	if e != nil {
		if observerError := observerCheckError(observer, "initial_state", *checks, false); observerError != nil {
			return nil, fmt.Errorf("%w; %v", e, observerError)
		}
		return nil, e
	}
	if observer != nil && observer.Initial != nil {
		_, e = call("observer initial", func() (struct{}, error) {
			return struct{}{}, observer.Initial(m.CloneState(state), copyChecks(*checks))
		})
		if e != nil {
			return nil, e
		}
	}
	return *checks, nil
}
func searchEdgeObserved[S, I, O any](g GenerateModel[S, I, O], state S, input I, t Transition[S, O], history []I, skipped *uint64, observer *SearchObserver[S, I, O]) ([]Violation, error) {
	checks := []Check{}
	return searchEdgeObservedInto(g, state, input, t, history, skipped, observer, &checks)
}
func searchEdgeObservedInto[S, I, O any](g GenerateModel[S, I, O], state S, input I, t Transition[S, O], history []I, skipped *uint64, observer *SearchObserver[S, I, O], checks *[]Check) ([]Violation, error) {
	resetChecks(checks)
	e := stateChecksStageBatch("check_state", g.Model, t.State, &checkBatch{checks: checks})
	if e != nil {
		if observerError := observerCheckError(observer, "state", *checks, false); observerError != nil {
			return nil, fmt.Errorf("%w; %v", e, observerError)
		}
		return nil, e
	}
	boundary := len(*checks)
	e = edgeChecksInto(g.Model, state, input, t, checks)
	if e != nil {
		first := observerCheckError(observer, "state", (*checks)[:boundary], true)
		second := observerCheckError(observer, "transition", (*checks)[boundary:], false)
		if first != nil || second != nil {
			return nil, fmt.Errorf("%w; observer errors: %v; %v", e, first, second)
		}
		return nil, e
	}
	if observer != nil && observer.Transition != nil {
		_, e = call("observer transition", func() (struct{}, error) {
			snapshot, e := cloneTransition(g.Model, t)
			if e != nil {
				return struct{}{}, e
			}
			inputs, e := cloneInputs(g, history)
			if e != nil {
				return struct{}{}, e
			}
			return struct{}{}, observer.Transition(g.CloneState(state), g.CloneInput(input), snapshot, copyChecks((*checks)[:boundary]), copyChecks((*checks)[boundary:]), inputs)
		})
		if e != nil {
			return nil, e
		}
	}
	return append(findings("state", (*checks)[:boundary], skipped), findings("transition", (*checks)[boundary:], skipped)...), nil
}
