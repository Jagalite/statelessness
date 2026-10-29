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
	// Guidance is nil for ordinary fuzz and owned by a successful guided report.
	Guidance *GuidanceReport[I]
}

func Fuzz[S, I, O any](g GenerateModel[S, I, O], config FuzzConfig) (*FuzzReport[I], error) {
	return FuzzWithLimits(g, config, RunLimits{})
}
func FuzzWithLimits[S, I, O any](g GenerateModel[S, I, O], config FuzzConfig, limits RunLimits) (*FuzzReport[I], error) {
	return fuzzObserved(g, config, limits, nil)
}
func fuzzObserved[S, I, O any](g GenerateModel[S, I, O], config FuzzConfig, limits RunLimits, observer *SearchObserver[S, I, O]) (*FuzzReport[I], error) {
	if e := validateFuzzModel(g); e != nil {
		return nil, e
	}
	if e := validateFuzzConfig(config); e != nil {
		return nil, e
	}
	control, e := newRunControl(limits)
	if e != nil {
		return nil, e
	}
	return fuzzKernel(g, config, control, observer, nil)
}
func searchEdge[S, I, O any](m Model[S, I, O], state S, input I, t Transition[S, O], skipped *uint64) ([]Violation, error) {
	checks := []Check{}
	return searchEdgeObservedInto(GenerateModel[S, I, O]{Model: m}, state, input, t, nil, skipped, nil, &checks)
}
