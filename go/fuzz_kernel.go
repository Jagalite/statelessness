package statelessness

// This private boundary also permits deterministic control-poll regression
// tests. Public entry points always supply the ordinary runControl clock/flag.
type fuzzControl interface{ stop() string }

// fuzzKernel is shared by ordinary and guided search. Keep checks, observer
// delivery, property failure, and control precedence together in this loop.
func fuzzKernel[S, I, O any](g GenerateModel[S, I, O], config FuzzConfig, control fuzzControl, observer *SearchObserver[S, I, O], driver fuzzDriver[S, I, O]) (*FuzzReport[I], error) {
	report := &FuzzReport[I]{Termination: "cases_completed", Seed: config.Seed}
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
	rng := NewRng(config.Seed)
	var checkStorage []Check
	var previous []I
	for c := 0; c < config.Cases; c++ {
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
			if e := driver.initial(state, report.Cases); e != nil {
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
		for step := 0; step < config.MaxSteps; step++ {
			if report.Transitions == config.MaxTransitions {
				report.Termination = "transition_limit"
				return report, nil
			}
			if stop() {
				return report, nil
			}
			var input I
			reused := false
			if replacement != step && step < len(mutated) {
				enabled, e := g.enabled(state, mutated[step])
				if e != nil {
					return nil, e
				}
				if enabled {
					input, e = copied(g.CloneInput, mutated[step])
					if e != nil {
						return nil, e
					}
					reused = true
				}
			}
			if stop() {
				return report, nil
			}
			ok := true
			if !reused {
				type generated struct {
					value I
					ok    bool
				}
				v, e := call("generate", func() (generated, error) {
					value, ok, e := g.Generate(g.CloneState(state), rng)
					return generated{value, ok}, e
				})
				if e != nil {
					return nil, e
				}
				ok = v.ok
				if ok {
					input, e = copied(g.CloneInput, v.value)
					if e != nil {
						return nil, e
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
				enabled, e := g.enabled(state, input)
				if e != nil {
					return nil, e
				}
				if stop() {
					return report, nil
				}
				if !enabled {
					return nil, &ModelError{"generate returned a causally disabled input"}
				}
			} else if driver != nil && stop() {
				// Rust's guided boundary polls after the short-circuited enabled
				// result even on reuse. Preserve ordinary Go's existing boundary.
				return report, nil
			}
			transition, e := performStage(g.Model, state, input, "step")
			if e != nil {
				return nil, e
			}
			report.Transitions++
			saved, e := copied(g.CloneInput, input)
			if e != nil {
				return nil, e
			}
			inputs = append(inputs, saved)
			violations, e := searchEdgeObservedInto(g, state, input, transition, inputs, &report.SkippedChecks, observer, &checkStorage)
			if e != nil {
				return nil, e
			}
			if len(violations) > 0 {
				owned, e := cloneInputs(g, inputs)
				if e != nil {
					return nil, e
				}
				report.Failure = &Failure[I]{owned, violations}
				report.Termination = "failure_found"
				return report, nil
			}
			if stop() {
				return report, nil
			}
			if driver != nil {
				if e := driver.transition(state, input, transition, inputs, report.Cases, report.Transitions); e != nil {
					return nil, e
				}
				if stop() {
					return report, nil
				}
			}
			state = transition.State
		}
		if driver == nil {
			previous, e = cloneInputs(g, inputs)
			if e != nil {
				return nil, e
			}
		}
	}
	return report, nil
}
