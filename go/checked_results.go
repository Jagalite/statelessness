package statelessness

// checkedSeal is engine-private authority, distinct from public diagnostic data.
type checkedSeal struct{ marker byte }

var checkedAuthority = &checkedSeal{1}

// CheckedInitial certifies a complete state-check invocation, including failed
// properties. Its context is detached using the model's clone contract.
type CheckedInitial[S, I, O any] struct {
	model  Model[S, I, O]
	state  S
	checks []Check
	seal   *checkedSeal
}

// CheckedTurn certifies one complete policy-governed check of an existing turn.
// It never certifies that the properties passed and is not globally single-use.
type CheckedTurn[S, I, O any] struct {
	model           Model[S, I, O]
	before          S
	input           I
	transition      Transition[S, O]
	sequence        uint64
	checks          []Check
	stateCheckCount int
	fullChecking    bool
	seal            *checkedSeal
}

// TurnObservation is detached diagnostic data, not recorder handoff authority.
type TurnObservation[S, I, O any] struct {
	Sequence        uint64
	Before          S
	Input           I
	Transition      Transition[S, O]
	Checks          []Check
	StateCheckCount int
}

func (c *CheckedInitial[S, I, O]) valid() bool { return c != nil && c.seal == checkedAuthority }
func (c *CheckedTurn[S, I, O]) valid() bool    { return c != nil && c.seal == checkedAuthority }
func snapshotModel[S, I, O any](m Model[S, I, O]) Model[S, I, O] {
	if m.Codec != nil {
		c := *m.Codec
		m.Codec = &c
	}
	return m
}

// CheckInitial checks the supplied checkpoint once, without calling InitialState
// or Step and without requiring a codec. Snapshot/check failures produce no token.
func CheckInitial[S, I, O any](m Model[S, I, O], state S) (*CheckedInitial[S, I, O], error) {
	if e := m.validate(); e != nil {
		return nil, e
	}
	m = snapshotModel(m)
	snapshot, e := copied(m.CloneState, state)
	if e != nil {
		return nil, e
	}
	checks := []Check{}
	if e = stateChecksStageBatch("initial check", m, snapshot, &checkBatch{checks: &checks}); e != nil {
		return nil, e
	}
	return &CheckedInitial[S, I, O]{model: m, state: snapshot, checks: copyChecks(checks), seal: checkedAuthority}, nil
}

// CheckTurn snapshots and checks an application-owned completed turn once.
// Sequence is exact positive u64. The application's transition remains intact.
func CheckTurn[S, I, O any](m Model[S, I, O], before S, input I, transition Transition[S, O], sequence uint64, policy CheckPolicy) (*CheckedTurn[S, I, O], error) {
	if e := m.validate(); e != nil {
		return nil, e
	}
	if sequence == 0 {
		return nil, &ConfigError{"transition sequence must be positive"}
	}
	if policy.StateEvery == 0 {
		return nil, &ConfigError{"state period must be positive"}
	}
	m = snapshotModel(m)
	b, e := copied(m.CloneState, before)
	if e != nil {
		return nil, e
	}
	i, e := copied(m.CloneInput, input)
	if e != nil {
		return nil, e
	}
	t, e := cloneTransition(m, transition)
	if e != nil {
		return nil, e
	}
	checks := []Check{}
	count, e := checkObservedCountInto(m, b, i, t, sequence, policy, &checks)
	if e != nil {
		return nil, e
	}
	return &CheckedTurn[S, I, O]{model: m, before: b, input: i, transition: t, sequence: sequence, checks: copyChecks(checks), stateCheckCount: count, fullChecking: policy.StateEvery == 1 && policy.TransitionChecks, seal: checkedAuthority}, nil
}
func (c *CheckedInitial[S, I, O]) Model() (Model[S, I, O], error) {
	if !c.valid() {
		return Model[S, I, O]{}, &ConfigError{"invalid checked initial"}
	}
	return snapshotModel(c.model), nil
}
func (c *CheckedInitial[S, I, O]) State() (S, error) {
	if !c.valid() {
		var z S
		return z, &ConfigError{"invalid checked initial"}
	}
	return copied(c.model.CloneState, c.state)
}
func (c *CheckedInitial[S, I, O]) Checks() []Check {
	if !c.valid() {
		return nil
	}
	return copyChecks(c.checks)
}
func (c *CheckedInitial[S, I, O]) StateCheckCount() int {
	if !c.valid() {
		return 0
	}
	return len(c.checks)
}

// IntoChecks returns detached checks; Go tokens remain usable by other consumers.
func (c *CheckedInitial[S, I, O]) IntoChecks() []Check { return c.Checks() }
func (c *CheckedTurn[S, I, O]) Model() (Model[S, I, O], error) {
	if !c.valid() {
		return Model[S, I, O]{}, &ConfigError{"invalid checked turn"}
	}
	return snapshotModel(c.model), nil
}
func (c *CheckedTurn[S, I, O]) Before() (S, error) {
	if !c.valid() {
		var z S
		return z, &ConfigError{"invalid checked turn"}
	}
	return copied(c.model.CloneState, c.before)
}
func (c *CheckedTurn[S, I, O]) Input() (I, error) {
	if !c.valid() {
		var z I
		return z, &ConfigError{"invalid checked turn"}
	}
	return copied(c.model.CloneInput, c.input)
}
func (c *CheckedTurn[S, I, O]) Transition() (Transition[S, O], error) {
	if !c.valid() {
		return Transition[S, O]{}, &ConfigError{"invalid checked turn"}
	}
	return cloneTransition(c.model, c.transition)
}
func (c *CheckedTurn[S, I, O]) Sequence() uint64 {
	if !c.valid() {
		return 0
	}
	return c.sequence
}
func (c *CheckedTurn[S, I, O]) Checks() []Check {
	if !c.valid() {
		return nil
	}
	return copyChecks(c.checks)
}
func (c *CheckedTurn[S, I, O]) IntoChecks() []Check { return c.Checks() }
func (c *CheckedTurn[S, I, O]) StateCheckCount() int {
	if !c.valid() {
		return 0
	}
	return c.stateCheckCount
}
func (c *CheckedTurn[S, I, O]) FullChecking() bool { return c.valid() && c.fullChecking }
func (c *CheckedTurn[S, I, O]) Observation() (TurnObservation[S, I, O], error) {
	var z TurnObservation[S, I, O]
	b, e := c.Before()
	if e != nil {
		return z, e
	}
	i, e := c.Input()
	if e != nil {
		return z, e
	}
	t, e := c.Transition()
	if e != nil {
		return z, e
	}
	return TurnObservation[S, I, O]{Sequence: c.sequence, Before: b, Input: i, Transition: t, Checks: c.Checks(), StateCheckCount: c.stateCheckCount}, nil
}
