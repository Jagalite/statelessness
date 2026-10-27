package statelessness

import "math"

// SearchConfig bounds deterministic work, not process memory or callback time.
type SearchConfig struct {
	MaxStates      int
	MaxTransitions uint64
	MaxDepth       int
}

func DefaultSearchConfig() SearchConfig { return SearchConfig{100_000, 1_000_000, 100} }

type Violation struct {
	Phase string `json:"phase"`
	Check Check  `json:"check"`
}
type Failure[I any] struct {
	Inputs     []I         `json:"inputs"`
	Violations []Violation `json:"violations"`
}
type SearchReport[I any] struct {
	Termination                 string      `json:"termination"`
	States                      int         `json:"states"`
	Transitions                 uint64      `json:"transitions"`
	MaxDepthReached             int         `json:"max_depth_reached"`
	SkippedChecks               uint64      `json:"skipped_checks"`
	EstimatedRetainedStateBytes *uint64     `json:"estimated_retained_state_bytes"`
	Failure                     *Failure[I] `json:"failure"`
}
type node[S, I any] struct {
	state  S
	parent int
	input  I
	depth  int
}

func findings(phase string, checks []Check, skipped *uint64) []Violation {
	result := []Violation{}
	for _, c := range checks {
		if c.Status == "skipped" && *skipped < math.MaxUint64 {
			*skipped++
		}
		if c.Status == "failed" {
			result = append(result, Violation{phase, c})
		}
	}
	return result
}
func next[I any](it Iterator[I]) (value I, ok bool, err error) {
	type result struct {
		value I
		ok    bool
	}
	r, err := call("enumerate inputs", func() (result, error) { v, ok, e := it(); return result{v, ok}, e })
	return r.value, r.ok, err
}

// SearchLimits optionally bounds admitted state estimates and cooperatively
// controls execution. Temporary successors, frontier/maps and input history are
// outside the estimate budget; this is never a process-memory guarantee.
type SearchLimits struct {
	Control                RunLimits
	MaxEstimatedStateBytes *uint64
}

func estimateState[S, I, O any](m Model[S, I, O], state S) (*uint64, error) {
	if m.EstimatedStateBytes == nil {
		return nil, nil
	}
	return call("estimated_state_bytes", func() (*uint64, error) {
		value, e := m.EstimatedStateBytes(m.CloneState(state))
		if e != nil || value == nil {
			return nil, e
		}
		owned := *value
		return &owned, nil
	})
}
func searchPath[S, I, O any](m Model[S, I, O], nodes []node[S, I], index int, input I) ([]I, error) {
	return call("search history", func() ([]I, error) {
		path := []I{m.CloneInput(input)}
		for current := index; nodes[current].parent >= 0; current = nodes[current].parent {
			path = append(path, m.CloneInput(nodes[current].input))
		}
		for l, r := 0, len(path)-1; l < r; l, r = l+1, r-1 {
			path[l], path[r] = path[r], path[l]
		}
		return path, nil
	})
}

// Enumerate performs FIFO exploration with detached shortest predecessor paths.
// Every executed edge is fully checked before deduplication or admission limits.
func Enumerate[S, I, O any](m Model[S, I, O], config SearchConfig) (*SearchReport[I], error) {
	return EnumerateWithLimits(m, config, SearchLimits{})
}
func EnumerateWithLimits[S, I, O any](m Model[S, I, O], config SearchConfig, limits SearchLimits) (*SearchReport[I], error) {
	return enumerateObserved(m, config, limits, nil)
}
func EnumerateObserved[S, I, O any](m Model[S, I, O], config SearchConfig, limits SearchLimits, observer SearchObserver[S, I, O]) (*SearchReport[I], error) {
	return enumerateObserved(m, config, limits, &observer)
}
func enumerateObserved[S, I, O any](m Model[S, I, O], config SearchConfig, limits SearchLimits, observer *SearchObserver[S, I, O]) (*SearchReport[I], error) {
	// The optional budget is configuration, not a live control channel. Copy its
	// pointed value before any callback; cancellation remains intentionally live.
	if limits.MaxEstimatedStateBytes != nil {
		maximum := *limits.MaxEstimatedStateBytes
		limits.MaxEstimatedStateBytes = &maximum
	}

	if config.MaxStates <= 0 {
		return nil, &ConfigError{"enumeration max_states must be greater than zero"}
	}
	if config.MaxDepth < 0 {
		return nil, &ConfigError{"invalid enumeration configuration"}
	}
	if e := m.validate(); e != nil {
		return nil, e
	}
	if m.InitialState == nil || m.Step == nil || m.Inputs == nil || m.EqualStates == nil {
		return nil, &ModelError{"enumeration requires initial, step, inputs, and equality callbacks"}
	}
	control, e := newRunControl(limits.Control)
	if e != nil {
		return nil, e
	}
	report := &SearchReport[I]{Termination: "graph_exhausted"}
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
	var checkStorage []Check
	initial, e := call("initial_state", m.InitialState)
	if e != nil {
		return nil, e
	}
	initial, e = copied(m.CloneState, initial)
	if e != nil {
		return nil, e
	}
	report.States = 1
	checks, e := searchInitialObservedInto(m, initial, observer, &checkStorage)
	if e != nil {
		return nil, e
	}
	bad := findings("initial_state", checks, &report.SkippedChecks)
	if len(bad) > 0 {
		report.Termination = "failure_found"
		report.Failure = &Failure[I]{[]I{}, bad}
		return report, nil
	}
	if stop() {
		return report, nil
	}
	initialBytes, e := estimateState(m, initial)
	if e != nil {
		return nil, e
	}
	report.EstimatedRetainedStateBytes = initialBytes
	if stop() {
		return report, nil
	}
	if limits.MaxEstimatedStateBytes != nil {
		if initialBytes == nil {
			return nil, &ModelError{"state byte budget requires estimated_state_bytes"}
		}
		if *initialBytes > *limits.MaxEstimatedStateBytes {
			zero := uint64(0)
			report.States = 0
			report.EstimatedRetainedStateBytes = &zero
			report.Termination = "estimated_state_byte_limit"
			return report, nil
		}
	}
	hash := func(state S) (uint64, error) {
		return call("state hash", func() (uint64, error) {
			if m.HashState == nil {
				return 0, nil
			}
			return m.HashState(m.CloneState(state)), nil
		})
	}
	h, e := hash(initial)
	if e != nil {
		return nil, e
	}
	if stop() {
		return report, nil
	}
	nodes := []node[S, I]{{state: initial, parent: -1}}
	visited := map[uint64][]int{h: {0}}
	cutoff := false
	for cursor := 0; cursor < len(nodes); cursor++ {
		if stop() {
			return report, nil
		}
		current := nodes[cursor]
		iterator, e := call("enumerate inputs", func() (Iterator[I], error) { return m.Inputs(m.CloneState(current.state)) })
		if e != nil {
			return nil, e
		}
		if stop() {
			return report, nil
		}
		if current.depth == config.MaxDepth {
			_, ok, e := next(iterator)
			if e != nil {
				return nil, e
			}
			if stop() {
				return report, nil
			}
			cutoff = cutoff || ok
			continue
		}
		for {
			if stop() {
				return report, nil
			}
			input, ok, e := next(iterator)
			if e != nil {
				return nil, e
			}
			if stop() {
				return report, nil
			}
			if !ok {
				break
			}
			if report.Transitions == config.MaxTransitions {
				report.Termination = "transition_limit"
				return report, nil
			}
			transition, e := perform(m, current.state, input)
			if e != nil {
				return nil, e
			}
			report.Transitions++
			report.MaxDepthReached = max(report.MaxDepthReached, current.depth+1)
			var history []I
			needsHistory := false
			if observer != nil && observer.NeedsHistory != nil {
				needsHistory, e = call("observer needs history", func() (bool, error) { return observer.NeedsHistory(), nil })
				if e != nil {
					return nil, e
				}
			}
			if needsHistory {
				history, e = searchPath(m, nodes, cursor, input)
				if e != nil {
					return nil, e
				}
			}
			bad, e := searchEdgeObservedInto(GenerateModel[S, I, O]{Model: m}, current.state, input, transition, history, &report.SkippedChecks, observer, &checkStorage)
			if e != nil {
				return nil, e
			}
			if len(bad) > 0 {
				path, e := searchPath(m, nodes, cursor, input)
				if e != nil {
					return nil, e
				}
				report.Failure = &Failure[I]{path, bad}
				report.Termination = "failure_found"
				return report, nil
			}
			if stop() {
				return report, nil
			}
			h, e := hash(transition.State)
			if e != nil {
				return nil, e
			}
			if stop() {
				return report, nil
			}
			duplicate := false
			// Rust inserts each newly admitted state at the collision chain head.
			for position := len(visited[h]) - 1; position >= 0; position-- {
				if stop() {
					return report, nil
				}
				index := visited[h][position]
				equal, e := call("state equality", func() (bool, error) {
					return m.EqualStates(m.CloneState(nodes[index].state), m.CloneState(transition.State)), nil
				})
				if e != nil {
					return nil, e
				}
				if stop() {
					return report, nil
				}
				if equal {
					duplicate = true
					break
				}
			}
			if duplicate {
				continue
			}
			if len(nodes) == config.MaxStates {
				report.Termination = "state_limit"
				return report, nil
			}
			estimate, e := estimateState(m, transition.State)
			if e != nil {
				return nil, e
			}
			if stop() {
				return report, nil
			}
			var total *uint64
			if report.EstimatedRetainedStateBytes != nil && estimate != nil && *estimate <= math.MaxUint64-*report.EstimatedRetainedStateBytes {
				sum := *report.EstimatedRetainedStateBytes + *estimate
				total = &sum
			}
			if limits.MaxEstimatedStateBytes != nil {
				if estimate == nil {
					return nil, &ModelError{"state byte budget requires estimated_state_bytes"}
				}
				if total == nil || *total > *limits.MaxEstimatedStateBytes {
					report.Termination = "estimated_state_byte_limit"
					return report, nil
				}
			}
			saved, e := copied(m.CloneInput, input)
			if e != nil {
				return nil, e
			}
			visited[h] = append(visited[h], len(nodes))
			nodes = append(nodes, node[S, I]{transition.State, cursor, saved, current.depth + 1})
			report.States = len(nodes)
			report.EstimatedRetainedStateBytes = total
		}
	}
	if cutoff {
		report.Termination = "depth_bound"
	}
	return report, nil
}
