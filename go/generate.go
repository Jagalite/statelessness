package statelessness

// GenerateModel adds state-aware generation to an ordinary native Model.
// A nil IsEnabled accepts all inputs; nil SimplerInputs yields no alternatives.
// InputEqual is required for shrinking/automatic membership, and must compare
// semantic input identity independently of object addresses.
type GenerateModel[S, I, O any] struct {
	Model[S, I, O]
	Generate      func(S, *Rng) (I, bool, error)
	IsEnabled     func(S, I) (bool, error)
	SimplerInputs func(I) (Iterator[I], error)
	InputEqual    func(I, I) (bool, error)
}
type AutoOptions64 struct{ MaxCandidates uint64 }

func DefaultAutoOptions64() AutoOptions64 { return AutoOptions64{100000} }

// Auto samples finite enumeration using reservoir sampling. The cap permits one
// extra iterator call to distinguish exact exhaustion from overflow. RNG state
// commits only after a successful complete scan, including successful snapshots.
// Membership snapshots the query before domain callbacks; that copy hook runs
// even for an empty or failing domain, though its error is deferred until needed.
// Copy hooks must cooperate and cannot make external concurrent mutation atomic.
func Auto64[S, I, O any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64) (GenerateModel[S, I, O], error) {
	return auto64(m, equal, options, nil)
}

// auto64 optionally receives the owning run control, never an independent clock.
func auto64[S, I, O any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64, poll func() error) (GenerateModel[S, I, O], error) {
	var z GenerateModel[S, I, O]
	if err := m.validate(); err != nil {
		return z, err
	}
	if m.Inputs == nil || equal == nil || options.MaxCandidates <= 0 {
		return z, &ConfigError{"auto requires inputs, equality, and positive candidate cap"}
	}
	g := GenerateModel[S, I, O]{Model: m, InputEqual: equal}
	cursor := func(state S) (Iterator[I], error) {
		return call("enumerate inputs", func() (Iterator[I], error) { return m.Inputs(m.CloneState(state)) })
	}
	g.Generate = func(state S, rng *Rng) (value I, ok bool, err error) {
		var zero I
		if rng == nil {
			return zero, false, &ConfigError{"nil RNG"}
		}
		candidate := *rng
		it, err := cursor(state)
		if err != nil {
			return zero, false, err
		}
		for count := uint64(0); ; count++ {
			if poll != nil {
				if e := poll(); e != nil {
					return zero, false, e
				}
			}
			v, more, e := next(it)
			if e != nil {
				return zero, false, e
			}
			if poll != nil {
				if e := poll(); e != nil {
					return zero, false, e
				}
			}
			if !more {
				*rng = candidate
				return value, ok, nil
			}
			if count == options.MaxCandidates {
				return zero, false, &ModelError{"automatic generation candidate limit"}
			}
			selectIt := count == 0
			if count > 0 {
				index, _ := candidate.Index(uint64(count + 1))
				selectIt = index == 0
			}
			if selectIt {
				value, e = copied(m.CloneInput, v)
				if e != nil {
					return zero, false, e
				}
				ok = true
			}
		}
	}
	g.IsEnabled = func(state S, input I) (bool, error) {
		// Retain the query before the domain callback can mutate an external
		// alias. Defer a copy failure until a candidate actually needs it.
		query, queryErr := copied(m.CloneInput, input)
		it, e := cursor(state)
		if e != nil {
			return false, e
		}
		for count := uint64(0); ; count++ {
			if poll != nil {
				if e := poll(); e != nil {
					return false, e
				}
			}
			v, more, e := next(it)
			if e != nil {
				return false, e
			}
			if poll != nil {
				if e := poll(); e != nil {
					return false, e
				}
			}
			if !more {
				return false, nil
			}
			if count == options.MaxCandidates {
				return false, &ModelError{"automatic generation candidate limit"}
			}
			if queryErr != nil {
				return false, queryErr
			}
			same, e := call("input equality", func() (bool, error) { return equal(m.CloneInput(v), m.CloneInput(query)) })
			if e != nil {
				return false, e
			}
			if same {
				return true, nil
			}
		}
	}
	return g, nil
}
func (g GenerateModel[S, I, O]) enabled(state S, input I) (bool, error) {
	if g.IsEnabled == nil {
		return true, nil
	}
	return call("is_enabled", func() (bool, error) {
		ownedInput := g.CloneInput(input)
		return g.IsEnabled(g.CloneState(state), ownedInput)
	})
}
func cloneInputs[S, I, O any](g GenerateModel[S, I, O], inputs []I) ([]I, error) {
	// Freeze slice slots before invoking application clone hooks: a hook may
	// hold an alias to the caller's slice and replace a later input.
	slots := append([]I(nil), inputs...)
	return call("snapshot inputs", func() ([]I, error) {
		out := make([]I, len(slots))
		for k, v := range slots {
			out[k] = g.CloneInput(v)
		}
		return out, nil
	})
}
