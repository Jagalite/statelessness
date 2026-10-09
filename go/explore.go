package statelessness

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
	Termination     string      `json:"termination"`
	States          int         `json:"states"`
	Transitions     uint64      `json:"transitions"`
	MaxDepthReached int         `json:"max_depth_reached"`
	SkippedChecks   uint64      `json:"skipped_checks"`
	Failure         *Failure[I] `json:"failure"`
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
		if c.Status == "skipped" {
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

// Enumerate performs FIFO exploration, preserving first predecessors. Every
// executed edge is checked before deduplication or state-admission limits.
func Enumerate[S, I, O any](m Model[S, I, O], config SearchConfig) (*SearchReport[I], error) {
	if config.MaxStates <= 0 || config.MaxDepth < 0 {
		return nil, &ConfigError{"invalid enumeration configuration"}
	}
	if err := m.validate(); err != nil {
		return nil, err
	}
	if m.InitialState == nil || m.Step == nil || m.Inputs == nil || m.EqualStates == nil {
		return nil, &ModelError{"enumeration requires initial, step, inputs, and equality callbacks"}
	}
	initial, err := call("initial_state", m.InitialState)
	if err != nil {
		return nil, err
	}
	initial, err = copied(m.CloneState, initial)
	if err != nil {
		return nil, err
	}
	checks, err := stateChecks(m, initial)
	if err != nil {
		return nil, err
	}
	report := &SearchReport[I]{Termination: "graph_exhausted", States: 1}
	bad := findings("initial_state", checks, &report.SkippedChecks)
	if len(bad) > 0 {
		report.Termination = "failure_found"
		report.Failure = &Failure[I]{[]I{}, bad}
		return report, nil
	}
	nodes := []node[S, I]{{state: initial, parent: -1}}
	hash := func(s S) (uint64, error) {
		return call("state hash", func() (uint64, error) {
			if m.HashState == nil {
				return 0, nil
			}
			return m.HashState(m.CloneState(s)), nil
		})
	}
	h, err := hash(initial)
	if err != nil {
		return nil, err
	}
	visited := map[uint64][]int{h: {0}}
	cutoff := false
	for cursor := 0; cursor < len(nodes); cursor++ {
		current := nodes[cursor]
		state, err := copied(m.CloneState, current.state)
		if err != nil {
			return nil, err
		}
		iterator, err := call("enumerate inputs", func() (Iterator[I], error) { return m.Inputs(state) })
		if err != nil {
			return nil, err
		}
		if current.depth == config.MaxDepth {
			_, ok, err := next(iterator)
			if err != nil {
				return nil, err
			}
			cutoff = cutoff || ok
			continue
		}
		for {
			input, ok, err := next(iterator)
			if err != nil {
				return nil, err
			}
			if !ok {
				break
			}
			if report.Transitions == config.MaxTransitions {
				report.Termination = "transition_limit"
				return report, nil
			}
			transition, err := perform(m, current.state, input)
			if err != nil {
				return nil, err
			}
			report.Transitions++
			if current.depth+1 > report.MaxDepthReached {
				report.MaxDepthReached = current.depth + 1
			}
			sc, err := stateChecks(m, transition.State)
			if err != nil {
				return nil, err
			}
			ec, err := edgeChecks(m, current.state, input, transition)
			if err != nil {
				return nil, err
			}
			bad := append(findings("state", sc, &report.SkippedChecks), findings("transition", ec, &report.SkippedChecks)...)
			if len(bad) > 0 {
				item, err := copied(m.CloneInput, input)
				if err != nil {
					return nil, err
				}
				path := []I{item}
				for k := cursor; nodes[k].parent >= 0; k = nodes[k].parent {
					item, err = copied(m.CloneInput, nodes[k].input)
					if err != nil {
						return nil, err
					}
					path = append(path, item)
				}
				for l, r := 0, len(path)-1; l < r; l, r = l+1, r-1 {
					path[l], path[r] = path[r], path[l]
				}
				report.Failure = &Failure[I]{path, bad}
				report.Termination = "failure_found"
				return report, nil
			}
			h, err := hash(transition.State)
			if err != nil {
				return nil, err
			}
			duplicate := false
			for _, index := range visited[h] {
				equal, err := call("state equality", func() (bool, error) {
					return m.EqualStates(m.CloneState(nodes[index].state), m.CloneState(transition.State)), nil
				})
				if err != nil {
					return nil, err
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
			item, err := copied(m.CloneInput, input)
			if err != nil {
				return nil, err
			}
			visited[h] = append(visited[h], len(nodes))
			nodes = append(nodes, node[S, I]{transition.State, cursor, item, current.depth + 1})
			report.States = len(nodes)
		}
	}
	if cutoff {
		report.Termination = "depth_bound"
	}
	return report, nil
}
