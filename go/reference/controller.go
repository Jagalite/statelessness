// Package reference implements the finite request-controller example. It is an
// application reducer: returned effects are facts for a host, never executed here.
package reference

import (
	"math"
	"slices"

	s "github.com/Jagalite/statelessness/go"
)

type Bounds struct {
	Generations uint64
	Attempts    uint8
	Time        uint64
}

func DefaultBounds() Bounds { return Bounds{2, 2, 2} }
func (b Bounds) Validate() error {
	if b.Generations == 0 || b.Attempts == 0 || b.Time == math.MaxUint64 {
		return &s.ModelError{Message: "positive generations/attempts and finite retry-clock headroom required"}
	}
	return nil
}

type Input struct {
	Kind       string
	Generation uint64
	Attempt    uint8
	Outcome    string
	Now        uint64
}

// Validate rejects combinations that cannot be represented by the reference
// tagged union; domain-invalid generations/attempts remain valid stale inputs.
func (in Input) Validate() error {
	valid := false
	switch in.Kind {
	case "submit":
		valid = in.Generation == 0 && in.Attempt == 0 && in.Outcome == "" && in.Now == 0
	case "cancel":
		valid = in.Attempt == 0 && in.Outcome == "" && in.Now == 0
	case "complete":
		valid = in.Now == 0 && (in.Outcome == "success" || in.Outcome == "retryable_failure")
	case "cancelled":
		valid = in.Outcome == "" && in.Now == 0
	case "tick":
		valid = in.Generation == 0 && in.Attempt == 0 && in.Outcome == ""
	}
	if !valid {
		return &s.ModelError{Message: "invalid reference-controller input variant"}
	}
	return nil
}

type Effect struct {
	Kind       string
	Generation uint64
	Attempt    uint8
	Result     string
}

// Validate rejects payload combinations outside the reference effect enum.
func (e Effect) Validate() error {
	valid := false
	switch e.Kind {
	case "start", "cancel":
		valid = e.Result == ""
	case "publish", "release":
		valid = e.Attempt == 0 && e.Result == ""
	case "settle":
		valid = e.Attempt == 0 && (e.Result == "succeeded" || e.Result == "cancelled" || e.Result == "failed")
	}
	if !valid {
		return &s.ModelError{Message: "invalid reference-controller effect variant"}
	}
	return nil
}

type Job struct {
	Generation    uint64
	Attempt       uint8
	Phase         string
	ResourceOwned bool
	Deadline      uint64
	Result        string
}
type State struct {
	Now     uint64
	Current *uint64
	Jobs    []Job
}

// validateRepresentation checks enum shape without asserting safety properties.
func (state State) validateRepresentation() error {
	for _, job := range state.Jobs {
		if _, _, err := phaseWire(job); err != nil {
			return err
		}
	}
	return nil
}
func validateObservation(before State, in Input, after s.Transition[State, Effect]) error {
	if err := in.Validate(); err != nil {
		return err
	}
	if err := before.validateRepresentation(); err != nil {
		return err
	}
	if err := after.State.validateRepresentation(); err != nil {
		return err
	}
	if err := after.Disposition.Validate(); err != nil {
		return err
	}
	for _, effect := range after.Outputs {
		if err := effect.Validate(); err != nil {
			return err
		}
	}
	return nil
}

func (state State) Clone() State {
	state.Jobs = slices.Clone(state.Jobs)
	if state.Current != nil {
		x := *state.Current
		state.Current = &x
	}
	return state
}
func (state State) Job(g uint64) (Job, bool) {
	for _, j := range state.Jobs {
		if j.Generation == g {
			return j, true
		}
	}
	return Job{}, false
}
func (state State) Pending() int {
	n := 0
	for _, j := range state.Jobs {
		if j.Phase != "settled" {
			n++
		}
	}
	return n
}
func Reduce(before State, input Input, bounds Bounds) (s.Transition[State, Effect], error) {
	if err := bounds.Validate(); err != nil {
		return s.Transition[State, Effect]{}, err
	}
	if err := input.Validate(); err != nil {
		return s.Transition[State, Effect]{}, err
	}
	if err := before.validateRepresentation(); err != nil {
		return s.Transition[State, Effect]{}, err
	}
	state := before.Clone()
	effects := []Effect{}
	index := func(g uint64) int {
		for i, j := range state.Jobs {
			if j.Generation == g {
				return i
			}
		}
		return -1
	}
	finish := func(i int, result string) {
		j := &state.Jobs[i]
		j.Phase = "settled"
		j.Result = result
		j.Deadline = 0
		j.ResourceOwned = false
		effects = append(effects, Effect{Kind: "settle", Generation: j.Generation, Result: result}, Effect{Kind: "release", Generation: j.Generation})
		if state.Current != nil && *state.Current == j.Generation {
			state.Current = nil
		}
	}
	cancel := func(g uint64) bool {
		i := index(g)
		if i < 0 {
			return false
		}
		j := &state.Jobs[i]
		switch j.Phase {
		case "running":
			j.Phase = "cancelling"
			effects = append(effects, Effect{Kind: "cancel", Generation: g, Attempt: j.Attempt})
			if state.Current != nil && *state.Current == g {
				state.Current = nil
			}
			return true
		case "waiting":
			finish(i, "cancelled")
			return true
		}
		return false
	}
	accepted := false
	switch input.Kind {
	case "submit":
		if uint64(len(state.Jobs)) < bounds.Generations {
			if state.Current != nil {
				cancel(*state.Current)
			}
			g := uint64(len(state.Jobs)) + 1
			state.Jobs = append(state.Jobs, Job{Generation: g, Attempt: 1, Phase: "running", ResourceOwned: true})
			state.Current = &g
			effects = append(effects, Effect{Kind: "start", Generation: g, Attempt: 1})
			accepted = true
		}
	case "cancel":
		accepted = cancel(input.Generation)
	case "complete":
		if input.Outcome == "success" || input.Outcome == "retryable_failure" {
			i := index(input.Generation)
			if i >= 0 && state.Jobs[i].Attempt == input.Attempt {
				j := state.Jobs[i]
				if j.Phase == "cancelling" {
					finish(i, "cancelled")
					accepted = true
				} else if j.Phase == "running" {
					if input.Outcome == "success" {
						if state.Current != nil && *state.Current == input.Generation {
							effects = append(effects, Effect{Kind: "publish", Generation: input.Generation})
						}
						finish(i, "succeeded")
					} else if input.Attempt < bounds.Attempts && state.Now < bounds.Time {
						state.Jobs[i].Phase = "waiting"
						state.Jobs[i].Deadline = state.Now + 1
					} else {
						finish(i, "failed")
					}
					accepted = true
				}
			}
		}
	case "cancelled":
		i := index(input.Generation)
		if i >= 0 && state.Jobs[i].Attempt == input.Attempt && state.Jobs[i].Phase == "cancelling" {
			finish(i, "cancelled")
			accepted = true
		}
	case "tick":
		if input.Now > state.Now && input.Now <= bounds.Time {
			state.Now = input.Now
			for i := range state.Jobs {
				j := &state.Jobs[i]
				if j.Phase == "waiting" && j.Deadline <= state.Now {
					if j.Attempt == math.MaxUint8 {
						return s.Transition[State, Effect]{}, &s.ModelError{Message: "retry attempt counter overflow"}
					}
					j.Attempt++
					j.Phase = "running"
					j.Deadline = 0
					effects = append(effects, Effect{Kind: "start", Generation: j.Generation, Attempt: j.Attempt})
				}
			}
			accepted = true
		}
	}
	if accepted {
		return s.Transition[State, Effect]{State: state, Outputs: effects, Disposition: s.Accepted()}, nil
	}
	return s.Transition[State, Effect]{State: state, Outputs: effects, Disposition: s.Ignored("outside active request/attempt/time domain")}, nil
}

// Controller exposes the bounded reducer, checks, input domain, and codec.
// Coverage and named mutation controls are provided by this package as well.
type Controller struct {
	Bounds   Bounds
	Mutation string
}

func (c Controller) Model() s.Model[State, Input, Effect] {
	return s.Model[State, Input, Effect]{InitialState: func() (State, error) {
		if err := c.Bounds.Validate(); err != nil {
			return State{}, err
		}
		return State{}, nil
	}, Step: c.Step, CloneState: func(st State) State { return st.Clone() }, CloneInput: s.ValueCopy[Input], CloneOutput: s.ValueCopy[Effect], CheckTransition: c.CheckTransition, EqualStates: func(a, b State) bool {
		return a.Now == b.Now && slices.Equal(a.Jobs, b.Jobs) && ((a.Current == nil && b.Current == nil) || (a.Current != nil && b.Current != nil && *a.Current == *b.Current))
	}, CheckState: c.CheckState, Inputs: c.Inputs, Codec: &s.Codec[State, Input, Effect]{Metadata: func() (s.Metadata, error) {
		return s.Identity("reference-controller", controllerBuild(c)), nil
	}, EncodeState: c.EncodeState, DecodeState: c.DecodeState, EncodeInput: c.EncodeInput, DecodeInput: c.DecodeInput, EncodeOutput: c.EncodeOutput}}
}
func (c Controller) CheckState(st State) ([]s.Check, error) {
	valid := st.Now <= c.Bounds.Time && uint64(len(st.Jobs)) <= c.Bounds.Generations
	for i, j := range st.Jobs {
		valid = valid && (j.Phase == "running" || j.Phase == "cancelling" || j.Phase == "waiting" || j.Phase == "settled")
		switch j.Phase {
		case "running", "cancelling":
			valid = valid && j.Deadline == 0 && j.Result == ""
		case "waiting":
			valid = valid && j.Result == ""
		case "settled":
			valid = valid && j.Deadline == 0 && (j.Result == "succeeded" || j.Result == "cancelled" || j.Result == "failed")
		}
		valid = valid && j.Generation == uint64(i)+1 && j.Attempt > 0 && j.Attempt <= c.Bounds.Attempts && j.ResourceOwned != (j.Phase == "settled")
		if j.Phase == "waiting" {
			valid = valid && j.Deadline > st.Now && j.Deadline <= c.Bounds.Time && j.Attempt < c.Bounds.Attempts
		}
	}
	if st.Current != nil {
		j, ok := st.Job(*st.Current)
		valid = valid && ok && (j.Phase == "running" || j.Phase == "waiting")
	}
	if valid {
		return []s.Check{s.Passed("controller.state_valid")}, nil
	}
	return []s.Check{s.Failed("controller.state_valid", "independent controller obligation was violated")}, nil
}

// Inputs lazily preserves the reference's finite ordering, including invalid
// generations and stale completion/cancellation observations.
func (c Controller) Inputs(st State) (s.Iterator[Input], error) {
	if err := c.Bounds.Validate(); err != nil {
		return nil, err
	}
	n := uint64(len(st.Jobs))
	phase := 0
	generation := uint64(0)
	attempt := uint8(1)
	variant := 0
	tick := uint64(0)
	return func() (Input, bool, error) {
		if phase == 0 {
			phase = 1
			return Input{Kind: "submit"}, true, nil
		}
		if phase == 1 {
			if generation <= n {
				if variant == 0 {
					variant = 1
					return Input{Kind: "cancel", Generation: generation}, true, nil
				}
				v := Input{Generation: generation, Attempt: attempt}
				switch variant {
				case 1:
					v.Kind = "cancelled"
					variant = 2
				case 2:
					v.Kind = "complete"
					v.Outcome = "success"
					variant = 3
				case 3:
					v.Kind = "complete"
					v.Outcome = "retryable_failure"
					if attempt == c.Bounds.Attempts {
						generation++
						attempt = 1
						variant = 0
					} else {
						attempt++
						variant = 1
					}
				}
				return v, true, nil
			}
			phase = 2
		}
		if phase == 2 && tick <= c.Bounds.Time {
			v := Input{Kind: "tick", Now: tick}
			if tick == c.Bounds.Time {
				phase = 3
			} else {
				tick++
			}
			return v, true, nil
		}
		return Input{}, false, nil
	}, nil
}
