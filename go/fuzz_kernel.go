package statelessness

// This private boundary also permits deterministic control-poll regression
// tests. Public entry points always supply the ordinary runControl clock/flag.
type fuzzControl interface{ stop() string }

// fuzzKernel64 is shared by ordinary and guided search. Keep checks, observer
// delivery, property failure, and control precedence together in this loop.
func fuzzKernel64[S, I, O any](g GenerateModel[S, I, O], config FuzzConfig64, control fuzzControl, observer *SearchObserver[S, I, O], driver fuzzDriver[S, I, O]) (*FuzzReport64[I], error) {
	report := &FuzzReport64[I]{Termination: "cases_completed", Seed: config.Seed}
	stop := func() bool {
		if reason := control.stop(); reason != "" {
			report.Termination = reason
			return true
		}
		return false
	}
	if stop() {
		return report, nil
	}
	// Only the built-in automatic scanner can set this private marker.
	scanStopped := func() bool {
		if c, ok := control.(interface{ scanInterrupted() string }); ok {
			if reason := c.scanInterrupted(); reason != "" {
				report.Termination = reason
				return true
			}
		}
		return false
	}
	rng := NewRng(config.Seed)
	var checkStorage []Check
	var previous []I
	for c := uint64(0); c < config.Cases; c++ {
		if stop() {
			return report, nil
		}
		state, e := call("initial_state", g.InitialState)
		if e != nil {
			return nil, e
		}
		state, e = copied(g.CloneState, state)
		if e != nil {
			return nil, e
		}
		// Checks may mutate externally aliased application objects. Preserve the
		// continuation before running them, but defer failure until it is needed.
		initialState, initialStateErr := copied(g.CloneState, state)
		var initialFeedback S
		var initialFeedbackErr error
		if driver != nil {
			initialFeedback, initialFeedbackErr = copied(g.CloneState, state)
		}
		report.Cases++
		checks, e := searchInitialObservedInto(g.Model, state, observer, &checkStorage)
		if e != nil {
			return nil, e
		}
		violations := findings("initial_state", checks, &report.SkippedChecks)
		if len(violations) > 0 {
			report.Failure = &Failure[I]{Inputs: []I{}, Violations: violations}
			report.Termination = "failure_found"
			return report, nil
		}
		if stop() {
			return report, nil
		}
		if driver != nil {
			if initialFeedbackErr != nil {
				return nil, initialFeedbackErr
			}
			if e := driver.initial(initialFeedback, report.Cases); e != nil {
				return nil, e
			}
			if stop() {
				return report, nil
			}
		}
		var mutated []I
		replacement := -1
		if driver == nil {
			mutated, replacement, e = previousCandidate(g, previous, rng, config.MutationPercent)
		} else {
			mutated, replacement, e = driver.candidate(previous, rng, config.MutationPercent)
		}
		if e != nil {
			return nil, e
		}
		inputs := []I{}
		pendingState, pendingStateErr, hasPendingState := initialState, initialStateErr, true
		for step := uint64(0); step < config.MaxSteps; step++ {
			if report.Transitions == config.MaxTransitions {
				report.Termination = "transition_limit"
				return report, nil
			}
			if stop() {
				return report, nil
			}
			if hasPendingState {
				if pendingStateErr != nil {
					return nil, pendingStateErr
				}
				state = pendingState
				hasPendingState = false
			}
			// Admission callbacks can hold aliases to the current state. Keep the
			// reducer's observation separate from the one used for admission.
			executionState, executionStateErr := copied(g.CloneState, state)
			admissionState, admissionStateErr := copied(g.CloneState, state)
			var feedbackState S
			var feedbackStateErr error
			if driver != nil {
				feedbackState, feedbackStateErr = copied(g.CloneState, state)
			}
			var input I
			var feedbackInput I
			var feedbackInputErr error
			var executionInput I
			var executionErr error
			reused := false
			if (replacement < 0 || uint64(replacement) != step) && step < uint64(len(mutated)) {
				candidate, candidateErr := copied(g.CloneInput, mutated[step])
				query := mutated[step]
				if candidateErr == nil {
					query = candidate
					executionInput, executionErr = copied(g.CloneInput, candidate)
					if driver != nil && executionErr == nil {
						feedbackInput, feedbackInputErr = copied(g.CloneInput, executionInput)
					}
				}
				if g.IsEnabled != nil && admissionStateErr != nil {
					return nil, admissionStateErr
				}
				enabled, e := g.enabled(admissionState, query)
				if e != nil {
					if scanStopped() {
						return report, nil
					}
					return nil, e
				}
				if enabled {
					if candidateErr != nil {
						return nil, candidateErr
					}
					if executionErr != nil {
						return nil, executionErr
					}
					input = executionInput
					reused = true
				}
			}
			if stop() {
				return report, nil
			}
			ok := true
			if !reused {
				if admissionStateErr != nil {
					return nil, admissionStateErr
				}
				type generated struct {
					value I
					ok    bool
				}
				v, e := call("generate", func() (generated, error) {
					value, ok, e := g.Generate(g.CloneState(admissionState), rng)
					return generated{value, ok}, e
				})
				if e != nil {
					if scanStopped() {
						return report, nil
					}
					return nil, e
				}
				ok = v.ok
				if ok {
					input, e = copied(g.CloneInput, v.value)
					if e != nil {
						return nil, e
					}
					executionInput, executionErr = copied(g.CloneInput, input)
					if driver != nil && executionErr == nil {
						feedbackInput, feedbackInputErr = copied(g.CloneInput, executionInput)
					}
				}
			}
			if stop() {
				return report, nil
			}
			if !ok {
				break
			}
			if !reused {
				enabled, e := g.enabled(admissionState, input)
				if e != nil {
					if scanStopped() {
						return report, nil
					}
					return nil, e
				}
				if stop() {
					return report, nil
				}
				if !enabled {
					return nil, &ModelError{"generate returned a causally disabled input"}
				}
				if executionErr != nil {
					return nil, executionErr
				}
				input = executionInput
			} else if driver != nil && stop() {
				// Rust's guided boundary polls after the short-circuited enabled
				// result even on reuse. Preserve ordinary Go's existing boundary.
				return report, nil
			}
			if executionStateErr != nil {
				return nil, executionStateErr
			}
			state = executionState
			var edgeObserver func(S, I, Transition[S, O], []Check, []Check, []I) error
			if observer != nil {
				edgeObserver = observer.Transition
			}
			transition, e := performStage(g.Model, state, input, "step")
			if e != nil {
				return nil, e
			}
			report.Transitions++
			// Preserve the successor before check and observer callbacks can mutate
			// external aliases. A failed copy is only relevant if another step
			// actually needs the successor.
			var successor S
			var successorErr error
			needSuccessor := step+1 < config.MaxSteps && report.Transitions < config.MaxTransitions
			if needSuccessor {
				successor, successorErr = copied(g.CloneState, transition.State)
			}
			// History copying invokes application hooks; retain both the checked
			// edge and guided feedback transition before those hooks run.
			edgeCapture := captureFuzzEdge(g, state, input, transition, edgeObserver)
			var feedbackTransition Transition[S, O]
			var feedbackTransitionErr error
			if driver != nil {
				feedbackTransition, feedbackTransitionErr = cloneTransition(g.Model, transition)
			}
			saved, e := copied(g.CloneInput, input)
			if e != nil {
				return nil, e
			}
			if e := countCanAppend[I](len(inputs)); e != nil {
				return nil, e
			}
			inputs = append(inputs, saved)
			var feedbackHistory []I
			var feedbackHistoryErr error
			if driver != nil {
				feedbackHistory, feedbackHistoryErr = cloneInputs(g, inputs)
			}
			violations, e := searchEdgeObservedPrepared(g, state, input, transition, inputs, &report.SkippedChecks, observer, &checkStorage, edgeCapture)
			if e != nil {
				return nil, e
			}
			if len(violations) > 0 {
				// Every slot is an engine-owned copy (saved above) that no callback
				// retains, so the failure takes ownership as Rust moves `inputs`.
				report.Failure = &Failure[I]{inputs, violations}
				report.Termination = "failure_found"
				return report, nil
			}
			if stop() {
				return report, nil
			}
			if driver != nil {
				for _, captureErr := range []error{feedbackStateErr, feedbackInputErr, feedbackTransitionErr, feedbackHistoryErr} {
					if captureErr != nil {
						return nil, captureErr
					}
				}
				if e := driver.transition(feedbackState, feedbackInput, feedbackTransition, feedbackHistory, report.Cases, report.Transitions); e != nil {
					return nil, e
				}
				if stop() {
					return report, nil
				}
			}
			if needSuccessor {
				pendingState, pendingStateErr, hasPendingState = successor, successorErr, true
			}
		}
		if driver == nil {
			// Rust moves the executed case into `previous`. previousCandidate
			// copies before handing any slot to a callback, so no clone is needed.
			previous = inputs
		}
	}
	return report, nil
}
