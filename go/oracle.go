package statelessness

import (
	"fmt"
	"math"
	"unsafe"
)

// ExperimentalProfiles names candidates; these are not qualified Profiles.
func ExperimentalProfiles() []string {
	return []string{"paired-v1", "search-v1", "native-binary-v1", "bfs-controls-v1"}
}

type OracleState[S, H any] struct {
	Model  S
	Oracle H
}

// Oracle keeps explicit independent history. CloneHistory must deeply copy any
// mutable values. Advance must be pure; outputs are defensive snapshots.
type Oracle[S, H, I, O any] struct {
	Metadata            func() (Metadata, error)
	InitialState        func() (H, error)
	Advance             func(H, I, []O, Disposition) (H, error)
	CheckState          func(H, S) ([]Check, error)
	CheckTransition     func(OracleState[S, H], I, Transition[OracleState[S, H], O]) ([]Check, error)
	CloneHistory        func(H) H
	EstimatedStateBytes func(H) (*uint64, error)
	EqualHistory        func(H, H) bool
	EncodeHistory       func(H) ([]byte, error)
	DecodeHistory       func([]byte) (H, error)
}

type ObservationError[S, O any] struct {
	Err        error
	Transition Transition[S, O]
}

func (e *ObservationError[S, O]) Error() string { return e.Err.Error() }
func (e *ObservationError[S, O]) Unwrap() error { return e.Err }

type OracleModel[S, H, I, O any] struct {
	Model[OracleState[S, H], I, O]
	actual Model[S, I, O]
	oracle Oracle[S, H, I, O]
}

// WithOracle constructs a native adapter. Optional enumeration and persistence
// remain unavailable unless both components supply the corresponding hooks.
func WithOracle[S, H, I, O any](m Model[S, I, O], o Oracle[S, H, I, O]) (*OracleModel[S, H, I, O], error) {
	if err := m.validate(); err != nil {
		return nil, err
	}
	if o.InitialState == nil || o.Advance == nil || o.CheckState == nil || o.CloneHistory == nil {
		return nil, &ModelError{"oracle requires initial, advance, state-check, and clone callbacks"}
	}
	w := &OracleModel[S, H, I, O]{actual: m, oracle: o}
	w.CloneState = func(s OracleState[S, H]) OracleState[S, H] {
		return OracleState[S, H]{m.CloneState(s.Model), o.CloneHistory(s.Oracle)}
	}
	w.CloneInput = m.CloneInput
	w.CloneOutput = m.CloneOutput
	if m.InitialState != nil {
		w.InitialState = func() (OracleState[S, H], error) {
			s, e := call("initial state", m.InitialState)
			if e != nil {
				return OracleState[S, H]{}, e
			}
			h, e := call("initial history", o.InitialState)
			if e != nil {
				return OracleState[S, H]{}, e
			}
			return w.Attach(s, h)
		}
	}
	if m.Step != nil {
		w.Step = func(s OracleState[S, H], i I) (Transition[OracleState[S, H], O], error) {
			t, e := perform(m, s.Model, i)
			if e != nil {
				return Transition[OracleState[S, H], O]{}, e
			}
			return w.ObserveTransition(s, i, t)
		}
	}
	w.CheckState = func(s OracleState[S, H]) ([]Check, error) {
		a, e := partialStateChecks(m, s.Model)
		if e != nil {
			return a, e
		}
		b, e := partialChecks("oracle state check", func() ([]Check, error) { return o.CheckState(o.CloneHistory(s.Oracle), m.CloneState(s.Model)) })
		return append(a, b...), e
	}
	w.CheckTransition = func(s OracleState[S, H], i I, t Transition[OracleState[S, H], O]) ([]Check, error) {
		a, e := partialEdgeChecks(m, s.Model, i, Transition[S, O]{t.State.Model, t.Outputs, t.Disposition})
		if e != nil {
			return a, e
		}
		if o.CheckTransition == nil {
			return a, nil
		}
		b, e := partialChecks("oracle transition check", func() ([]Check, error) {
			return o.CheckTransition(w.CloneState(s), m.CloneInput(i), cloneOracleTransition(w, t))
		})
		return append(a, b...), e
	}
	w.EstimatedStateBytes = func(state OracleState[S, H]) (*uint64, error) {
		actual, e := estimateState(m, state.Model)
		if e != nil || actual == nil {
			return nil, e
		}
		if o.EstimatedStateBytes == nil {
			return nil, nil
		}
		history, e := call("oracle estimated_state_bytes", func() (*uint64, error) {
			value, e := o.EstimatedStateBytes(o.CloneHistory(state.Oracle))
			if e != nil || value == nil {
				return nil, e
			}
			owned := *value
			return &owned, nil
		})
		if e != nil || history == nil {
			return nil, e
		}
		if *history > math.MaxUint64-*actual {
			return nil, nil
		}
		total := *actual + *history
		overhead := OracleStateOverhead[S, H]()
		if overhead > math.MaxUint64-total {
			return nil, nil
		}
		total += overhead
		return &total, nil
	}
	if m.Inputs != nil {
		w.Inputs = func(s OracleState[S, H]) (Iterator[I], error) { return m.Inputs(m.CloneState(s.Model)) }
	}
	if m.EqualStates != nil && o.EqualHistory != nil {
		w.EqualStates = func(a, b OracleState[S, H]) bool {
			return m.EqualStates(a.Model, b.Model) && o.EqualHistory(a.Oracle, b.Oracle)
		}
	}
	if m.Codec != nil && o.Metadata != nil && o.EncodeHistory != nil && o.DecodeHistory != nil {
		c := m.Codec
		w.Codec = &Codec[OracleState[S, H], I, O]{Metadata: func() (Metadata, error) {
			if c.Metadata == nil {
				return Metadata{}, &ModelError{"missing actual metadata"}
			}
			a, e := c.Metadata()
			if e != nil {
				return Metadata{}, e
			}
			b, e := o.Metadata()
			if e != nil {
				return Metadata{}, e
			}
			if e = a.Validate(); e != nil {
				return Metadata{}, e
			}
			if e = b.Validate(); e != nil {
				return Metadata{}, e
			}
			return combinedMetadata(a, b), nil
		}, EncodeState: func(s OracleState[S, H]) ([]byte, error) {
			if c.EncodeState == nil {
				return nil, &ModelError{"missing actual state encoder"}
			}
			a, e := c.EncodeState(m.CloneState(s.Model))
			if e != nil {
				return nil, e
			}
			a = append([]byte{}, a...)
			b, e := o.EncodeHistory(o.CloneHistory(s.Oracle))
			if e != nil {
				return nil, e
			}
			return frameOracle(a, b), nil
		}, DecodeState: func(data []byte) (OracleState[S, H], error) {
			var z OracleState[S, H]
			a, b, e := unframeOracle(data)
			if e != nil {
				return z, e
			}
			if c.DecodeState == nil {
				return z, &ModelError{"missing actual state decoder"}
			}
			s, e := c.DecodeState(a)
			if e != nil {
				return z, e
			}
			h, e := o.DecodeHistory(b)
			if e != nil {
				return z, e
			}
			return w.Attach(s, h)
		}, EncodeInput: c.EncodeInput, DecodeInput: c.DecodeInput, EncodeOutput: c.EncodeOutput}
	}
	return w, nil
}
func cloneOracleTransition[S, H, I, O any](w *OracleModel[S, H, I, O], t Transition[OracleState[S, H], O]) Transition[OracleState[S, H], O] {
	out := make([]O, len(t.Outputs))
	for k, v := range t.Outputs {
		out[k] = w.CloneOutput(v)
	}
	return Transition[OracleState[S, H], O]{w.CloneState(t.State), out, t.Disposition}
}
func (w *OracleModel[S, H, I, O]) Attach(s S, h H) (OracleState[S, H], error) {
	return call("attach history", func() (OracleState[S, H], error) { return w.CloneState(OracleState[S, H]{s, h}), nil })
}

// ObserveTransition never executes the actual reducer. Failure retains the
// completed actual transition, independently of mutations in oracle callbacks.
func (w *OracleModel[S, H, I, O]) ObserveTransition(s OracleState[S, H], i I, t Transition[S, O]) (Transition[OracleState[S, H], O], error) {
	var z Transition[OracleState[S, H], O]
	retained, e := cloneTransition(w.actual, t)
	if e != nil {
		return z, &ObservationError[S, O]{e, t}
	}
	var cause error
	result, e := call("advance history", func() (Transition[OracleState[S, H], O], error) {
		input := w.actual.CloneInput(i)
		effects := make([]O, len(retained.Outputs))
		for k, v := range retained.Outputs {
			effects[k] = w.actual.CloneOutput(v)
		}
		h, e := w.oracle.Advance(w.oracle.CloneHistory(s.Oracle), input, effects, retained.Disposition)
		if e != nil {
			cause = e
			return z, e
		}
		attached, e := w.Attach(retained.State, h)
		if e != nil {
			return z, e
		}
		return Transition[OracleState[S, H], O]{attached, retained.Outputs, retained.Disposition}, nil
	})
	if e != nil {
		if cause != nil {
			e = fmt.Errorf("advance history: %w", cause)
		}
		return z, &ObservationError[S, O]{e, retained}
	}
	return result, nil
}

// WithOracleGenerate forwards generation against actual application state while
// retaining explicit history in the combined engine model, equality and codec.
// History does not influence generation, causal membership, or input identity.
func WithOracleGenerate[S, H, I, O any](g GenerateModel[S, I, O], o Oracle[S, H, I, O]) (GenerateModel[OracleState[S, H], I, O], error) {
	var z GenerateModel[OracleState[S, H], I, O]
	wrapped, e := WithOracle(g.Model, o)
	if e != nil {
		return z, e
	}
	result := GenerateModel[OracleState[S, H], I, O]{Model: wrapped.Model, InputEqual: g.InputEqual, SimplerInputs: g.SimplerInputs}
	if g.Generate != nil {
		result.Generate = func(state OracleState[S, H], rng *Rng) (input I, present bool, err error) {
			type generated struct {
				value   I
				present bool
			}
			v, e := call("oracle generate", func() (generated, error) {
				value, ok, e := g.Generate(g.CloneState(state.Model), rng)
				return generated{value, ok}, e
			})
			if e != nil {
				return input, false, e
			}
			if !v.present {
				return input, false, nil
			}
			owned, e := copied(g.CloneInput, v.value)
			if e != nil {
				return input, false, e
			}
			return owned, true, nil
		}
	}
	if g.IsEnabled != nil {
		result.IsEnabled = func(state OracleState[S, H], input I) (bool, error) { return g.enabled(state.Model, input) }
	}
	return result, nil
}

// OracleStateOverhead reports architecture-specific inline wrapper padding beyond
// its two fields, matching Rust's size_of rule. Application estimates must include
// their own inline/reachable state data; this excludes heap headers and RSS.
func OracleStateOverhead[S, H any]() uint64 {
	var pair OracleState[S, H]
	size := uint64(unsafe.Sizeof(pair))
	modelSize, oracleSize := uint64(unsafe.Sizeof(pair.Model)), uint64(unsafe.Sizeof(pair.Oracle))
	if oracleSize > math.MaxUint64-modelSize {
		return 0
	} // impossible for a representable native pair
	fields := modelSize + oracleSize
	if size <= fields {
		return 0
	}
	return size - fields
}
