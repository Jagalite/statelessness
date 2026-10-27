package statelessness

import (
	"sync/atomic"
	"time"
)

// RunLimits interrupt between callbacks, never preempt arbitrary callback work.
// Cancellation takes priority over deadline. Nil duration means no wall clock.
type RunLimits struct {
	MaxDuration  *time.Duration
	Cancellation *atomic.Bool
}
type runControl struct {
	limits  RunLimits
	started time.Time
}

func newRunControl(limits RunLimits) (runControl, error) {
	if limits.MaxDuration != nil && *limits.MaxDuration < 0 {
		return runControl{}, &ConfigError{"negative duration"}
	}
	c := runControl{limits: limits}
	if limits.MaxDuration != nil {
		// Duration is immutable run configuration; only cancellation is live.
		duration := *limits.MaxDuration
		c.limits.MaxDuration = &duration
		c.started = time.Now()
	}
	return c, nil
}
func (c runControl) stop() string {
	if c.limits.Cancellation != nil && c.limits.Cancellation.Load() {
		return "cancelled"
	}
	if c.limits.MaxDuration != nil && time.Since(c.started) >= *c.limits.MaxDuration {
		return "deadline"
	}
	return ""
}

type FuzzConfig struct {
	Seed            uint64
	Cases, MaxSteps int
	MaxTransitions  uint64
	MutationPercent int
}

func DefaultFuzzConfig() FuzzConfig { return FuzzConfig{0, 1000, 100, 100000, 50} }

type FuzzReport[I any] struct {
	Termination                string
	Seed                       uint64
	Cases                      int
	Transitions, SkippedChecks uint64
	Failure                    *Failure[I]
}

func Fuzz[S, I, O any](g GenerateModel[S, I, O], config FuzzConfig) (*FuzzReport[I], error) {
	return FuzzWithLimits(g, config, RunLimits{})
}
func FuzzWithLimits[S, I, O any](g GenerateModel[S, I, O], config FuzzConfig, limits RunLimits) (*FuzzReport[I], error) {
	return fuzzObserved(g, config, limits, nil)
}
func fuzzObserved[S, I, O any](g GenerateModel[S, I, O], config FuzzConfig, limits RunLimits, observer *SearchObserver[S, I, O]) (*FuzzReport[I], error) {
	if e := g.validate(); e != nil {
		return nil, e
	}
	if g.InitialState == nil || g.Step == nil || g.Generate == nil {
		return nil, &ModelError{"fuzz requires initial, step and generate callbacks"}
	}
	if config.Cases < 0 || config.MaxSteps < 0 || config.MutationPercent < 0 || config.MutationPercent > 100 {
		return nil, &ConfigError{"invalid fuzz configuration"}
	}
	control, e := newRunControl(limits)
	if e != nil {
		return nil, e
	}
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
		state, e := call("initial state", g.InitialState)
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
		var mutated []I
		replacement := -1
		if len(previous) > 0 {
			draw, _ := rng.Index(100)
			if draw < uint64(config.MutationPercent) {
				mutated, e = cloneInputs(g, previous)
				if e != nil {
					return nil, e
				}
				start, _ := rng.Index(uint64(len(mutated)))
				span, _ := rng.Index(uint64(len(mutated)) - start)
				end := int(start + span + 1)
				mutated = append(mutated[:int(start)], mutated[end:]...)
				r, _ := rng.Index(uint64(len(mutated) + 1))
				replacement = int(r)
			}
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
			}
			transition, e := perform(g.Model, state, input)
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
			state = transition.State
		}
		previous, e = cloneInputs(g, inputs)
		if e != nil {
			return nil, e
		}
	}
	return report, nil
}
func searchEdge[S, I, O any](m Model[S, I, O], state S, input I, t Transition[S, O], skipped *uint64) ([]Violation, error) {
	checks := []Check{}
	return searchEdgeObservedInto(GenerateModel[S, I, O]{Model: m}, state, input, t, nil, skipped, nil, &checks)
}
