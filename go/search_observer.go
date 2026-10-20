package statelessness

import "fmt"

// SearchObserver receives defensive snapshots of checked work, including the
// failing transition and its entire executed history. Callback errors/panics are
// explicit engine errors. CheckError marks incomplete batches; these are never
// presented as successful transition observations or property reproductions.
type SearchObserver[S, I, O any] struct {
	Initial    func(S, []Check) error
	Transition func(S, I, Transition[S, O], []Check, []Check, []I) error
	CheckError func(phase string, checks []Check, complete bool) error
}

func FuzzObserved[S, I, O any](g GenerateModel[S, I, O], config FuzzConfig, limits RunLimits, observer SearchObserver[S, I, O]) (*FuzzReport[I], error) {
	return fuzzObserved(g, config, limits, &observer)
}
func copyChecks(checks []Check) []Check { return append([]Check{}, checks...) }

// A Go checker can return valid partial checks alongside an error. Preserve them
// for diagnostics; invalid entries and their suffix are not observations.
func partialChecks(stage string, callback func() ([]Check, error)) ([]Check, error) {
	var callbackError error
	checks, e := call(stage, func() ([]Check, error) {
		values, e := callback()
		callbackError = e
		valid := make([]Check, 0, len(values))
		for _, check := range values {
			if e := check.Validate(); e != nil {
				if callbackError == nil {
					callbackError = e
				}
				break
			}
			valid = append(valid, check)
		}
		return valid, nil
	})
	if e != nil {
		return checks, e
	}
	if callbackError != nil {
		return checks, &ModelError{fmt.Sprintf("%s: %v", stage, callbackError)}
	}
	return checks, nil
}
func observerCheckError[S, I, O any](observer *SearchObserver[S, I, O], phase string, checks []Check, complete bool) error {
	if observer == nil || observer.CheckError == nil {
		return nil
	}
	_, e := call("observer check error", func() (struct{}, error) { return struct{}{}, observer.CheckError(phase, copyChecks(checks), complete) })
	return e
}
func searchInitialObserved[S, I, O any](m Model[S, I, O], state S, observer *SearchObserver[S, I, O]) ([]Check, error) {
	if observer == nil {
		return stateChecks(m, state)
	}
	checks, e := partialChecks("initial state check", func() ([]Check, error) { return m.CheckState(m.CloneState(state)) })
	if e != nil {
		if observerError := observerCheckError(observer, "initial_state", checks, false); observerError != nil {
			return nil, fmt.Errorf("%w; %v", e, observerError)
		}
		return nil, e
	}
	if observer.Initial != nil {
		_, e = call("observer initial", func() (struct{}, error) { return struct{}{}, observer.Initial(m.CloneState(state), copyChecks(checks)) })
		if e != nil {
			return nil, e
		}
	}
	return checks, nil
}
func searchEdgeObserved[S, I, O any](g GenerateModel[S, I, O], state S, input I, t Transition[S, O], history []I, skipped *uint64, observer *SearchObserver[S, I, O]) ([]Violation, error) {
	if observer == nil {
		return searchEdge(g.Model, state, input, t, skipped)
	}
	checks, e := partialChecks("state check", func() ([]Check, error) { return g.CheckState(g.CloneState(t.State)) })
	if e != nil {
		if observerError := observerCheckError(observer, "state", checks, false); observerError != nil {
			return nil, fmt.Errorf("%w; %v", e, observerError)
		}
		return nil, e
	}
	edges := []Check{}
	if g.CheckTransition != nil {
		edges, e = partialChecks("transition check", func() ([]Check, error) {
			snapshot, e := cloneTransition(g.Model, t)
			if e != nil {
				return nil, e
			}
			return g.CheckTransition(g.CloneState(state), g.CloneInput(input), snapshot)
		})
	}
	if e != nil {
		first := observerCheckError(observer, "state", checks, true)
		second := observerCheckError(observer, "transition", edges, false)
		if first != nil || second != nil {
			return nil, fmt.Errorf("%w; observer errors: %v; %v", e, first, second)
		}
		return nil, e
	}
	if observer.Transition != nil {
		_, e = call("observer transition", func() (struct{}, error) {
			snapshot, e := cloneTransition(g.Model, t)
			if e != nil {
				return struct{}{}, e
			}
			inputs, e := cloneInputs(g, history)
			if e != nil {
				return struct{}{}, e
			}
			return struct{}{}, observer.Transition(g.CloneState(state), g.CloneInput(input), snapshot, copyChecks(checks), copyChecks(edges), inputs)
		})
		if e != nil {
			return nil, e
		}
	}
	return append(findings("state", checks, skipped), findings("transition", edges, skipped)...), nil
}

func partialStateChecks[S, I, O any](m Model[S, I, O], state S) ([]Check, error) {
	return partialChecks("state check", func() ([]Check, error) { return m.CheckState(m.CloneState(state)) })
}
func partialEdgeChecks[S, I, O any](m Model[S, I, O], state S, input I, t Transition[S, O]) ([]Check, error) {
	if m.CheckTransition == nil {
		return []Check{}, nil
	}
	return partialChecks("transition check", func() ([]Check, error) {
		snapshot, e := cloneTransition(m, t)
		if e != nil {
			return nil, e
		}
		return m.CheckTransition(m.CloneState(state), m.CloneInput(input), snapshot)
	})
}
