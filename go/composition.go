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
		q := make([]PairAddress[LI, RI], len(s.Pending))
		for i, a := range s.Pending {
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
	p.InitialState = func() (PairState[L, R, LI, RI], error) {
		l, e := left.InitialState()
		if e != nil {
			return PairState[L, R, LI, RI]{}, e
		}
		r, e := right.InitialState()
		if e != nil {
			return PairState[L, R, LI, RI]{}, e
		}
		return p.CloneState(PairState[L, R, LI, RI]{Left: l, Right: r}), nil
	}
	p.Step = func(s PairState[L, R, LI, RI], i PairInput[LI, RI]) (Transition[PairState[L, R, LI, RI], PairAddress[LO, RO]], error) {
		var empty Transition[PairState[L, R, LI, RI], PairAddress[LO, RO]]
		if len(s.Pending) > maxPending {
			return empty, &ModelError{"pending bound exceeded; verification incomplete"}
		}
		a, e := addressed(s, i)
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
			t, e := perform(left, s.Left, a.Left)
			if e != nil {
				return empty, e
			}
			next.Left = t.State
			disposition = t.Disposition
			for _, o := range t.Outputs {
				outputs = append(outputs, PairLeft[LO, RO](o))
			}
		} else {
			t, e := perform(right, s.Right, a.Right)
			if e != nil {
				return empty, e
			}
			next.Right = t.State
			disposition = t.Disposition
			for _, o := range t.Outputs {
				outputs = append(outputs, PairRight[LO, RO](o))
			}
		}
		for _, o := range outputs {
			messages, e := w.Messages(p.CloneOutput(o))
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
	p.CheckStateInto = func(s PairState[L, R, LI, RI], sink *CheckSink) error {
		if e := networkChecks("left", sink, func(c *CheckSink) error { return appendStateChecks(left, s.Left, c) }); e != nil {
			return e
		}
		if e := networkChecks("right", sink, func(c *CheckSink) error { return appendStateChecks(right, s.Right, c) }); e != nil {
			return e
		}
		return networkChecks("global", sink, func(c *CheckSink) error { return w.CheckState(p.CloneState(s), c) })
	}
	p.CheckTransitionInto = func(s PairState[L, R, LI, RI], i PairInput[LI, RI], t Transition[PairState[L, R, LI, RI], PairAddress[LO, RO]], sink *CheckSink) error {
		a, e := addressed(s, i)
		if e != nil {
			return e
		}
		if a.Side == "left" {
			outputs := []LO{}
			for _, o := range t.Outputs {
				if o.Side != "left" {
					return &ModelError{"wrong child output address"}
				}
				outputs = append(outputs, left.CloneOutput(o.Left))
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
				outputs = append(outputs, right.CloneOutput(o.Right))
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
	p.Inputs = func(s PairState[L, R, LI, RI]) (Iterator[PairInput[LI, RI]], error) {
		if left.Inputs == nil || right.Inputs == nil {
			return nil, &ModelError{"pair child lacks enumeration"}
		}
		values := []PairInput[LI, RI]{}
		li, e := left.Inputs(left.CloneState(s.Left))
		if e != nil {
			return nil, e
		}
		for {
			v, ok, e := li()
			if e != nil {
				return nil, e
			}
			if !ok {
				break
			}
			values = append(values, PairInput[LI, RI]{Kind: "local", Address: PairLeft[LI, RI](left.CloneInput(v))})
		}
		ri, e := right.Inputs(right.CloneState(s.Right))
		if e != nil {
			return nil, e
		}
		for {
			v, ok, e := ri()
			if e != nil {
				return nil, e
			}
			if !ok {
				break
			}
			values = append(values, PairInput[LI, RI]{Kind: "local", Address: PairRight[LI, RI](right.CloneInput(v))})
		}
		for j := range s.Pending {
			values = append(values, PairInput[LI, RI]{Kind: "deliver", Index: uint32(j)})
		}
		return Values(values), nil
	}
	if len(equality) > 0 {
		p.EqualStates = func(a, b PairState[L, R, LI, RI]) bool {
			if left.EqualStates == nil || right.EqualStates == nil {
				panic("child equality required")
			}
			if !left.EqualStates(a.Left, b.Left) || !right.EqualStates(a.Right, b.Right) || len(a.Pending) != len(b.Pending) {
				return false
			}
			for j, v := range a.Pending {
				if v.Side != b.Pending[j].Side || !equality[0](v, b.Pending[j]) {
					return false
				}
			}
			return true
		}
	}
	return p, nil
}
