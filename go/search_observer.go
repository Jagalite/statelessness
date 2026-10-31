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
	// Retain the observer's initial state before application checks can mutate
	// an external alias. A failed capture is only relevant after checks succeed.
	var initial func(S, []Check) error
	if observer != nil {
		initial = observer.Initial
	}
	var retained S
	var captureErr error
	if initial != nil {
		retained, captureErr = call("snapshot", func() (S, error) { return m.CloneState(state), nil })
	}
	e := stateChecksStageBatch("initial check_state", m, state, &checkBatch{checks: checks})
	if e != nil {
		if observerError := observerCheckError(observer, "initial_state", *checks, false); observerError != nil {
			return nil, fmt.Errorf("%w; %v", e, observerError)
		}
		return nil, e
	}
	if initial != nil {
		_, e = call("observer initial", func() (struct{}, error) {
			if captureErr != nil {
				return struct{}{}, captureErr
			}
			return struct{}{}, initial(m.CloneState(retained), copyChecks(*checks))
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
	return searchEdgeObservedPrepared(g, state, input, t, history, skipped, observer, checks, nil)
}

// fuzzEdgeCapture is taken before history-copy hooks can mutate the completed edge.
type fuzzEdgeCapture[S, I, O any] struct {
	state      S
	stateErr   error
	before     S
	input      I
	transition Transition[S, O]
	edgeErr    error
	observer   func(S, I, Transition[S, O], []Check, []Check, []I) error
}

func captureFuzzEdge[S, I, O any](g GenerateModel[S, I, O], state S, input I, t Transition[S, O], transitionObserver func(S, I, Transition[S, O], []Check, []Check, []I) error) *fuzzEdgeCapture[S, I, O] {
	p := &fuzzEdgeCapture[S, I, O]{}
	p.observer = transitionObserver
	needEdge := g.CheckTransitionInto != nil || g.CheckTransition != nil || p.observer != nil
	if needEdge {
		p.input, p.edgeErr = copied(g.CloneInput, input)
	}
	p.state, p.stateErr = copied(g.CloneState, t.State)
	if needEdge {
		var err error
		p.before, err = copied(g.CloneState, state)
		if p.edgeErr == nil {
			p.edgeErr = err
		}
		p.transition, err = cloneTransition(g.Model, t)
		if p.edgeErr == nil {
			p.edgeErr = err
		}
	}
	return p
}

func searchEdgeObservedPrepared[S, I, O any](g GenerateModel[S, I, O], state S, input I, t Transition[S, O], history []I, skipped *uint64, observer *SearchObserver[S, I, O], checks *[]Check, prepared *fuzzEdgeCapture[S, I, O]) ([]Violation, error) {
	resetChecks(checks)
	// Capture the edge before state checks can mutate externally aliased inputs.
	// A failed capture is consumed only by the later stage that needs it, so a
	// state-check failure and an absent transition callback retain precedence.
	type edgeSnapshot struct {
		before     S
		input      I
		transition Transition[S, O]
	}
	needCheck := g.CheckTransitionInto != nil || g.CheckTransition != nil
	var transitionObserver func(S, I, Transition[S, O], []Check, []Check, []I) error
	if observer != nil {
		transitionObserver = observer.Transition
	}
	if prepared != nil {
		transitionObserver = prepared.observer
	}
	needObserver := transitionObserver != nil
	var edge edgeSnapshot
	var edgeErr error
	var retainedHistory []I
	var historyErr error
	var historySlots []I
	if needObserver {
		historySlots = append([]I(nil), history...)
	}
	if prepared != nil && (needCheck || needObserver) {
		edge = edgeSnapshot{prepared.before, prepared.input, prepared.transition}
		edgeErr = prepared.edgeErr
	} else if needCheck || needObserver {
		edge, edgeErr = call("snapshot", func() (edgeSnapshot, error) {
			capturedInput := g.CloneInput(input)
			capturedBefore := g.CloneState(state)
			capturedTransition, err := cloneTransition(g.Model, t)
			return edgeSnapshot{capturedBefore, capturedInput, capturedTransition}, err
		})
	}
	if needObserver {
		retainedHistory, historyErr = cloneInputs(g, historySlots)
	}
	checkState := t.State
	var e error
	if prepared != nil {
		checkState, e = prepared.state, prepared.stateErr
	}
	if e == nil {
		e = stateChecksStageBatch("check_state", g.Model, checkState, &checkBatch{checks: checks})
	}
	if e != nil {
		if observerError := observerCheckError(observer, "state", *checks, false); observerError != nil {
			return nil, fmt.Errorf("%w; %v", e, observerError)
		}
		return nil, e
	}
	boundary := len(*checks)
	if needCheck {
		e = edgeErr
		if e == nil {
			e = edgeChecksInto(g.Model, edge.before, edge.input, edge.transition, checks)
		}
	}
	if e != nil {
		first := observerCheckError(observer, "state", (*checks)[:boundary], true)
		second := observerCheckError(observer, "transition", (*checks)[boundary:], false)
		if first != nil || second != nil {
			return nil, fmt.Errorf("%w; observer errors: %v; %v", e, first, second)
		}
		return nil, e
	}
	if needObserver {
		_, e = call("observer transition", func() (struct{}, error) {
			if edgeErr != nil {
				return struct{}{}, edgeErr
			}
			if historyErr != nil {
				return struct{}{}, historyErr
			}
			snapshot, e := cloneTransition(g.Model, edge.transition)
			if e != nil {
				return struct{}{}, e
			}
			// retainedHistory was copied before any check ran and is never handed
			// to another callback, so the observer can own it without a second copy.
			return struct{}{}, transitionObserver(g.CloneState(edge.before), g.CloneInput(edge.input), snapshot, copyChecks((*checks)[:boundary]), copyChecks((*checks)[boundary:]), retainedHistory)
		})
		if e != nil {
			return nil, e
		}
	}
	return append(findings("state", (*checks)[:boundary], skipped), findings("transition", (*checks)[boundary:], skipped)...), nil
}
