package statelessness

import (
	"math/bits"
	"sync"
	"sync/atomic"
	"time"
)

// RunLimits interrupt between callbacks, never preempt arbitrary callback work.
// Cancellation takes priority over deadline. Nil duration means no wall clock.
type RunLimits struct {
	MaxDuration *time.Duration
	// MaxDurationNS supports an exact unsigned 128-bit nanosecond budget. It is
	// mutually exclusive with MaxDuration. The monotonic clock must be polled
	// at least once per maximum time.Duration interval (about 292 years).
	MaxDurationNS *Uint128
	Cancellation  *atomic.Bool
}
type exactDeadlineClock struct {
	mu      sync.Mutex
	last    time.Time
	elapsed Uint128
}
type runControl struct {
	limits  RunLimits
	started time.Time
	exact   *exactDeadlineClock
}

func snapshotRunLimits(limits RunLimits) RunLimits {
	if limits.MaxDuration != nil {
		v := *limits.MaxDuration
		limits.MaxDuration = &v
	}
	if limits.MaxDurationNS != nil {
		v := *limits.MaxDurationNS
		limits.MaxDurationNS = &v
	}
	return limits
}

func newRunControl(limits RunLimits) (runControl, error) {
	limits = snapshotRunLimits(limits)
	if limits.MaxDuration != nil && limits.MaxDurationNS != nil {
		return runControl{}, &ConfigError{"duration forms are mutually exclusive"}
	}
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
	if limits.MaxDurationNS != nil {
		c.exact = &exactDeadlineClock{last: time.Now()}
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
	if c.exact != nil {
		remaining := c.remainingExact()
		if remaining.Hi == 0 && remaining.Lo == 0 {
			return "deadline"
		}
	}
	return ""
}

// remainingExact advances the shared monotonic checkpoint and saturates at
// zero. Copies of a runControl share this clock for concurrent campaign polls.
func (c runControl) remainingExact() Uint128 {
	c.exact.mu.Lock()
	now := time.Now()
	elapsed := now.Sub(c.exact.last)
	c.exact.last = now
	if elapsed > 0 {
		lo, carry := bits.Add64(c.exact.elapsed.Lo, uint64(elapsed), 0)
		hi, overflow := bits.Add64(c.exact.elapsed.Hi, 0, carry)
		if overflow != 0 {
			lo, hi = ^uint64(0), ^uint64(0)
		}
		c.exact.elapsed = Uint128{Lo: lo, Hi: hi}
	}
	used := c.exact.elapsed
	c.exact.mu.Unlock()
	max := *c.limits.MaxDurationNS
	if used.Hi > max.Hi || used.Hi == max.Hi && used.Lo >= max.Lo {
		return Uint128{}
	}
	lo, borrow := bits.Sub64(max.Lo, used.Lo, 0)
	hi, _ := bits.Sub64(max.Hi, used.Hi, borrow)
	return Uint128{Hi: hi, Lo: lo}
}

// RunDeadline preserves one cooperative deadline across independently invoked
// engine phases. Cancellation remains live; Remaining returns owned budgets.
type RunDeadline struct{ control runControl }

func NewRunDeadline(limits RunLimits) (*RunDeadline, error) {
	control, err := newRunControl(limits)
	if err != nil {
		return nil, err
	}
	return &RunDeadline{control: control}, nil
}
func (d *RunDeadline) Stop() string         { return d.control.stop() }
func (d *RunDeadline) Remaining() RunLimits { return d.control.remaining() }

func (c runControl) remaining() RunLimits {
	limits := c.limits
	if limits.MaxDuration != nil {
		duration := *limits.MaxDuration - time.Since(c.started)
		if duration < 0 {
			duration = 0
		}
		limits.MaxDuration = &duration
	}
	if limits.MaxDurationNS != nil {
		remaining := c.remainingExact()
		limits.MaxDurationNS = &remaining
	}
	return limits
}

type FuzzConfig64 struct {
	Seed            uint64
	Cases, MaxSteps uint64
	MaxTransitions  uint64
	MutationPercent int
}

func DefaultFuzzConfig64() FuzzConfig64 { return FuzzConfig64{0, 1000, 100, 100000, 50} }

type FuzzReport64[I any] struct {
	Termination                string
	Seed                       uint64
	Cases                      uint64
	Transitions, SkippedChecks uint64
	Failure                    *Failure[I]
	// Guidance is nil for ordinary fuzz and owned by a successful guided report.
	Guidance *GuidanceReport64[I]
}

func Fuzz64[S, I, O any](g GenerateModel[S, I, O], config FuzzConfig64) (*FuzzReport64[I], error) {
	return FuzzWithLimits64(g, config, RunLimits{})
}
func FuzzWithLimits64[S, I, O any](g GenerateModel[S, I, O], config FuzzConfig64, limits RunLimits) (*FuzzReport64[I], error) {
	return fuzzObserved64(g, config, limits, nil)
}
func fuzzObserved64[S, I, O any](g GenerateModel[S, I, O], config FuzzConfig64, limits RunLimits, observer *SearchObserver[S, I, O]) (*FuzzReport64[I], error) {
	if e := validateFuzzModel(g); e != nil {
		return nil, e
	}
	if e := validateFuzzConfig64(config); e != nil {
		return nil, e
	}
	control, e := newRunControl(limits)
	if e != nil {
		return nil, e
	}
	return fuzzKernel64(g, config, control, observer, nil)
}
func searchEdge[S, I, O any](m Model[S, I, O], state S, input I, t Transition[S, O], skipped *uint64) ([]Violation, error) {
	checks := []Check{}
	return searchEdgeObservedInto(GenerateModel[S, I, O]{Model: m}, state, input, t, nil, skipped, nil, &checks)
}
