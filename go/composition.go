package statelessness

// PairAddress is a checked tagged sum. Only the selected side participates in
// execution; constructors and clone hooks clear the inactive generic field.
type PairAddress[L, R any] struct {
	Side  string
	Left  L
	Right R
}

func PairLeft[L, R any](v L) PairAddress[L, R]  { return PairAddress[L, R]{Side: "left", Left: v} }
func PairRight[L, R any](v R) PairAddress[L, R] { return PairAddress[L, R]{Side: "right", Right: v} }

type PairInput[L, R any] struct {
	Kind    string
	Address PairAddress[L, R]
	Index   uint32
}
type PairState[L, R, LI, RI any] struct {
	Left    L
	Right   R
	Pending []PairAddress[LI, RI]
}
type PairWiring[L, R, LI, RI, LO, RO any] struct {
	Messages        func(PairAddress[LO, RO]) ([]PairAddress[LI, RI], error)
	CheckState      func(PairState[L, R, LI, RI], *CheckSink) error
	CheckTransition func(PairState[L, R, LI, RI], PairInput[LI, RI], Transition[PairState[L, R, LI, RI], PairAddress[LO, RO]], *CheckSink) error
}

// NewPair returns a composed model. Operation callbacks reject malformed pending
// address tags. Direct calls to its CloneState, CloneInput, or CloneOutput hooks
// can panic on malformed tags because clone callbacks cannot return errors.
func NewPair[L, R, LI, RI, LO, RO any](left Model[L, LI, LO], right Model[R, RI, RO], w PairWiring[L, R, LI, RI, LO, RO], maxPending int, equality ...func(PairAddress[LI, RI], PairAddress[LI, RI]) bool) (Model[PairState[L, R, LI, RI], PairInput[LI, RI], PairAddress[LO, RO]], error) {
	var p Model[PairState[L, R, LI, RI], PairInput[LI, RI], PairAddress[LO, RO]]
	if maxPending < 0 || w.Messages == nil || w.CheckState == nil {
		return p, &ModelError{"invalid pair configuration"}
	}
	if e := left.validate(); e != nil {
		return p, e
	}
	if e := right.validate(); e != nil {
		return p, e
	}
	// PairAddress is a public tagged struct in Go. Reject impossible variants
	// before cloning or enumerating a caller-supplied state.
	validateState := func(s PairState[L, R, LI, RI]) error {
		for _, a := range s.Pending {
			if a.Side != "left" && a.Side != "right" {
				return &ModelError{"invalid pending pair address"}
			}
		}
		return nil
	}
	cloneAddress := func(a PairAddress[LI, RI]) PairAddress[LI, RI] {
		if a.Side == "left" {
			return PairLeft[LI, RI](left.CloneInput(a.Left))
		}
		if a.Side == "right" {
			return PairRight[LI, RI](right.CloneInput(a.Right))
		}
		panic("invalid pair address")
	}
	p.CloneState = func(s PairState[L, R, LI, RI]) PairState[L, R, LI, RI] {
		// Capture address tags and slots before input clone hooks can mutate the
		// caller's slice. Payload cloning remains cooperative across aliases.
		pending := append([]PairAddress[LI, RI](nil), s.Pending...)
		q := make([]PairAddress[LI, RI], len(pending))
		for i, a := range pending {
			q[i] = cloneAddress(a)
		}
		return PairState[L, R, LI, RI]{left.CloneState(s.Left), right.CloneState(s.Right), q}
	}
	p.CloneInput = func(i PairInput[LI, RI]) PairInput[LI, RI] {
		if i.Kind == "local" {
			return PairInput[LI, RI]{Kind: "local", Address: cloneAddress(i.Address)}
		}
		return PairInput[LI, RI]{Kind: i.Kind, Index: i.Index}
	}
	p.CloneOutput = func(a PairAddress[LO, RO]) PairAddress[LO, RO] {
		if a.Side == "left" {
			return PairLeft[LO, RO](left.CloneOutput(a.Left))
		}
		if a.Side == "right" {
			return PairRight[LO, RO](right.CloneOutput(a.Right))
		}
		panic("invalid pair output")
	}
	addressed := func(s PairState[L, R, LI, RI], i PairInput[LI, RI]) (PairAddress[LI, RI], error) {
		if i.Kind == "local" && (i.Address.Side == "left" || i.Address.Side == "right") {
			return i.Address, nil
		}
		if i.Kind == "deliver" && uint64(i.Index) < uint64(len(s.Pending)) {
			a := s.Pending[i.Index]
			if a.Side == "left" || a.Side == "right" {
				return a, nil
			}
		}
		return PairAddress[LI, RI]{}, &ModelError{"invalid pair address or pending index"}
	}
	if left.InitialState != nil && right.InitialState != nil {
		p.InitialState = func() (PairState[L, R, LI, RI], error) {
			l, e := left.InitialState()
			if e != nil {
				return PairState[L, R, LI, RI]{}, e
			}
			l, e = copied(left.CloneState, l)
			if e != nil {
				return PairState[L, R, LI, RI]{}, e
			}
			r, e := right.InitialState()
			if e != nil {
				return PairState[L, R, LI, RI]{}, e
			}
			return p.CloneState(PairState[L, R, LI, RI]{Left: l, Right: r}), nil
		}
	}
	if left.Step != nil && right.Step != nil {
		p.Step = func(s PairState[L, R, LI, RI], i PairInput[LI, RI]) (Transition[PairState[L, R, LI, RI], PairAddress[LO, RO]], error) {
			var empty Transition[PairState[L, R, LI, RI], PairAddress[LO, RO]]
			if e := validateState(s); e != nil {
				return empty, e
			}
			if len(s.Pending) > maxPending {
				return empty, &ModelError{"pending bound exceeded; verification incomplete"}
			}
			// Detach address tags before any user clone hook can mutate the
			// caller's pending slice through an external alias.
			s.Pending = append([]PairAddress[LI, RI](nil), s.Pending...)
			a, e := addressed(s, i)
			if e != nil {
				return empty, e
			}
			// Preserve the selected input before state clone hooks can mutate an
			// external alias. The reducer must also consume the detached state.
			a, e = copied(cloneAddress, a)
			if e != nil {
				return empty, e
			}
			next := p.CloneState(s)
			if i.Kind == "deliver" {
				next.Pending = append(next.Pending[:i.Index], next.Pending[i.Index+1:]...)
			}
			outputs := []PairAddress[LO, RO]{}
			var disposition Disposition
			if a.Side == "left" {
				t, e := perform(left, next.Left, a.Left)
				if e != nil {
					return empty, e
				}
				next.Left = t.State
				disposition = t.Disposition
				for _, o := range t.Outputs {
					outputs = append(outputs, PairLeft[LO, RO](o))
				}
			} else {
				t, e := perform(right, next.Right, a.Right)
				if e != nil {
					return empty, e
				}
				next.Right = t.State
				disposition = t.Disposition
				for _, o := range t.Outputs {
					outputs = append(outputs, PairRight[LO, RO](o))
				}
			}
			// Retain every routed observation before invoking the wiring callback:
			// an earlier callback may hold an alias to a later child output. Defer
			// copy failures to their position so an earlier wiring error wins.
			type routedOutput struct {
				value PairAddress[LO, RO]
				err   error
			}
			routed := make([]routedOutput, len(outputs))
			for index, output := range outputs {
				var retained PairAddress[LO, RO]
				retained, routed[index].err = copied(p.CloneOutput, output)
				if routed[index].err == nil {
					outputs[index] = retained
					routed[index].value, routed[index].err = copied(p.CloneOutput, retained)
				}
			}
			for _, output := range routed {
				if output.err != nil {
					return empty, output.err
				}
				messages, e := w.Messages(output.value)
				if e != nil {
					return empty, e
				}
				for _, m := range messages {
					if len(next.Pending) >= maxPending {
						return empty, &ModelError{"pending bound exceeded; verification incomplete"}
					}
					if m.Side != "left" && m.Side != "right" {
						return empty, &ModelError{"invalid routed address"}
					}
					next.Pending = append(next.Pending, cloneAddress(m))
				}
			}
			return Transition[PairState[L, R, LI, RI], PairAddress[LO, RO]]{next, outputs, disposition}, nil
		}
	}
	p.CheckStateInto = func(s PairState[L, R, LI, RI], sink *CheckSink) error {
		if e := validateState(s); e != nil {
			return e
		}
		// Snapshot the whole observation before either child callback runs.
		s = p.CloneState(s)
		if e := networkChecks("left", sink, func(c *CheckSink) error { return appendStateChecks(left, s.Left, c) }); e != nil {
			return e
		}
		if e := networkChecks("right", sink, func(c *CheckSink) error { return appendStateChecks(right, s.Right, c) }); e != nil {
			return e
		}
		return networkChecks("global", sink, func(c *CheckSink) error { return w.CheckState(p.CloneState(s), c) })
	}
	p.CheckTransitionInto = func(s PairState[L, R, LI, RI], i PairInput[LI, RI], t Transition[PairState[L, R, LI, RI], PairAddress[LO, RO]], sink *CheckSink) error {
		if e := validateState(s); e != nil {
			return e
		}
		if e := validateState(t.State); e != nil {
			return e
		}
		a, e := addressed(s, i)
		if e != nil {
			return e
		}
		if len(t.Outputs) != 0 && t.Outputs[0].Side != a.Side {
			return &ModelError{"wrong child output address"}
		}
		// Capture address slots before application copy hooks can rewrite them,
		// then retain the queried input before state clone hooks run.
		s.Pending = append([]PairAddress[LI, RI](nil), s.Pending...)
		t.State.Pending = append([]PairAddress[LI, RI](nil), t.State.Pending...)
		t.Outputs = append([]PairAddress[LO, RO](nil), t.Outputs...)
		input, inputErr := copied(p.CloneInput, i)
		before, beforeErr := copied(p.CloneState, s)
		after, afterErr := copied(p.CloneState, t.State)
		dispositionErr := t.Disposition.Validate()
		// Preserve sequential child output mapping: a failed earlier clone
		// takes precedence over a later wrong address.
		outputs := make([]PairAddress[LO, RO], 0, len(t.Outputs))
		for _, output := range t.Outputs {
			if output.Side != a.Side {
				return &ModelError{"wrong child output address"}
			}
			owned, err := copied(p.CloneOutput, output)
			if err != nil {
				return err
			}
			outputs = append(outputs, owned)
		}
		if inputErr != nil {
			return inputErr
		}
		if beforeErr != nil {
			return beforeErr
		}
		if afterErr != nil {
			return afterErr
		}
		if dispositionErr != nil {
			return dispositionErr
		}
		i, s = input, before
		t = Transition[PairState[L, R, LI, RI], PairAddress[LO, RO]]{after, outputs, t.Disposition}
		a, e = addressed(s, i)
		if e != nil {
			return e
		}
		if a.Side == "left" {
			outputs := []LO{}
			for _, o := range t.Outputs {
				if o.Side != "left" {
					return &ModelError{"wrong child output address"}
				}
				outputs = append(outputs, o.Left)
			}
			if e := networkChecks("left", sink, func(c *CheckSink) error {
				return appendEdgeChecks(left, s.Left, a.Left, Transition[L, LO]{t.State.Left, outputs, t.Disposition}, c)
			}); e != nil {
				return e
			}
		} else {
			outputs := []RO{}
			for _, o := range t.Outputs {
				if o.Side != "right" {
					return &ModelError{"wrong child output address"}
				}
				outputs = append(outputs, o.Right)
			}
			if e := networkChecks("right", sink, func(c *CheckSink) error {
				return appendEdgeChecks(right, s.Right, a.Right, Transition[R, RO]{t.State.Right, outputs, t.Disposition}, c)
			}); e != nil {
				return e
			}
		}
		if w.CheckTransition == nil {
			return nil
		}
		return networkChecks("global", sink, func(c *CheckSink) error {
			outputs := make([]PairAddress[LO, RO], len(t.Outputs))
			for j, o := range t.Outputs {
				outputs[j] = p.CloneOutput(o)
			}
			return w.CheckTransition(p.CloneState(s), p.CloneInput(i), Transition[PairState[L, R, LI, RI], PairAddress[LO, RO]]{p.CloneState(t.State), outputs, t.Disposition}, c)
		})
	}
	p.CheckState = func(s PairState[L, R, LI, RI]) ([]Check, error) {
		c := []Check{}
		e := invokeChecks("pair state", &c, func(sink *CheckSink) error { return p.CheckStateInto(s, sink) })
		return c, e
	}
	if left.Inputs != nil && right.Inputs != nil {
		p.Inputs = func(s PairState[L, R, LI, RI]) (Iterator[PairInput[LI, RI]], error) {
			if e := validateState(s); e != nil {
				return nil, e
			}
			if left.Inputs == nil || right.Inputs == nil {
				return nil, &ModelError{"pair child lacks enumeration"}
			}
			if uint64(len(s.Pending)) > uint64(1)<<32 {
				return nil, &ModelError{"pair pending index overflow"}
			}
			// Snapshot both sides before invoking either child; a later mutation by
			// the caller or the first child's callback cannot change the right side.
			leftState, rightState := left.CloneState(s.Left), right.CloneState(s.Right)
			pending := len(s.Pending)
			phase, index := 0, 0
			var li Iterator[LI]
			var ri Iterator[RI]
			return func() (PairInput[LI, RI], bool, error) {
				var zero PairInput[LI, RI]
				for {
					switch phase {
					case 0:
						if li == nil {
							var err error
							li, err = left.Inputs(leftState)
							if err != nil {
								phase = 3
								return zero, false, err
							}
							if li == nil {
								phase = 3
								return zero, false, &ModelError{"nil pair child iterator"}
							}
						}
						value, ok, err := li()
						if err != nil {
							phase = 3
							return zero, false, err
						}
						if ok {
							return PairInput[LI, RI]{Kind: "local", Address: PairLeft[LI, RI](left.CloneInput(value))}, true, nil
						}
						li = nil
						phase = 1
					case 1:
						if ri == nil {
							var err error
							ri, err = right.Inputs(rightState)
							if err != nil {
								phase = 3
								return zero, false, err
							}
							if ri == nil {
								phase = 3
								return zero, false, &ModelError{"nil pair child iterator"}
							}
						}
						value, ok, err := ri()
						if err != nil {
							phase = 3
							return zero, false, err
						}
						if ok {
							return PairInput[LI, RI]{Kind: "local", Address: PairRight[LI, RI](right.CloneInput(value))}, true, nil
						}
						ri = nil
						phase = 2
					case 2:
						if index < pending {
							item := PairInput[LI, RI]{Kind: "deliver", Index: uint32(index)}
							index++
							return item, true, nil
						}
						phase = 3
					default:
						return zero, false, nil
					}
				}
			}, nil
		}
	}
	if len(equality) > 0 && equality[0] != nil && left.EqualStates != nil && right.EqualStates != nil {
		equalInput := equality[0]
		p.EqualStates = func(a, b PairState[L, R, LI, RI]) bool {
			for _, pending := range [][]PairAddress[LI, RI]{a.Pending, b.Pending} {
				for _, v := range pending {
					if v.Side != "left" && v.Side != "right" {
						return false
					}
				}
			}
			// Freeze both queues before child state clone hooks can mutate the
			// caller's pending slices or their payloads through external aliases.
			a.Pending = append([]PairAddress[LI, RI](nil), a.Pending...)
			b.Pending = append([]PairAddress[LI, RI](nil), b.Pending...)
			for j := range a.Pending {
				a.Pending[j] = cloneAddress(a.Pending[j])
			}
			for j := range b.Pending {
				b.Pending[j] = cloneAddress(b.Pending[j])
			}
			a.Left, a.Right = left.CloneState(a.Left), right.CloneState(a.Right)
			b.Left, b.Right = left.CloneState(b.Left), right.CloneState(b.Right)
			if !left.EqualStates(left.CloneState(a.Left), left.CloneState(b.Left)) || !right.EqualStates(right.CloneState(a.Right), right.CloneState(b.Right)) {
				return false
			}
			if len(a.Pending) != len(b.Pending) {
				return false
			}
			for j, v := range a.Pending {
				if v.Side != b.Pending[j].Side || !equalInput(cloneAddress(v), cloneAddress(b.Pending[j])) {
					return false
				}
			}
			return true
		}
	}
	return p, nil
}
