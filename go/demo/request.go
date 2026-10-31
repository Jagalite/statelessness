// Package demo ports the finite request-lifecycle teaching model. It is an
// engine fixture, not a qualified model of a production application.
package demo

import (
	"fmt"
	"slices"
	"unsafe"

	s "github.com/Jagalite/statelessness/go"
)

type State struct {
	Generation    uint8
	Active, Ready bool
	Pending       []uint8
}
type Input struct {
	Kind       string
	Generation uint8
}
type Output struct {
	Kind       string
	Generation uint8
}
type RequestModel struct{ InjectBug bool }

func Fixed() RequestModel      { return RequestModel{} }
func Buggy() RequestModel      { return RequestModel{InjectBug: true} }
func cloneState(v State) State { v.Pending = slices.Clone(v.Pending); return v }
func validInput(v Input) bool {
	return (v.Kind == "start" || v.Kind == "cancel") && v.Generation == 0 || v.Kind == "complete"
}
func rule(id string, holds bool, detail string) s.Check {
	if holds {
		return s.Passed(id)
	}
	return s.Failed(id, detail)
}
func (m RequestModel) Model() s.Model[State, Input, Output] {
	codec := &s.Codec[State, Input, Output]{
		Metadata: func() (s.Metadata, error) {
			build := "go-request-demo-v1:fixed"
			if m.InjectBug {
				build = "go-request-demo-v1:injected"
			}
			return s.Identity("request-lifecycle", build), nil
		},
		EncodeState: func(v State) ([]byte, error) {
			if len(v.Pending) > 255 {
				return nil, fmt.Errorf("pending list too long")
			}
			b := []byte{v.Generation, 0, 0, byte(len(v.Pending))}
			if v.Active {
				b[1] = 1
			}
			if v.Ready {
				b[2] = 1
			}
			return append(b, v.Pending...), nil
		},
		DecodeState: func(b []byte) (State, error) {
			if len(b) < 4 || len(b) != 4+int(b[3]) || b[1] > 1 || b[2] > 1 {
				return State{}, fmt.Errorf("invalid request state encoding")
			}
			return State{Generation: b[0], Active: b[1] != 0, Ready: b[2] != 0, Pending: slices.Clone(b[4:])}, nil
		},
		EncodeInput: func(v Input) ([]byte, error) {
			if !validInput(v) {
				return nil, fmt.Errorf("invalid request input")
			}
			switch v.Kind {
			case "start":
				return []byte{0}, nil
			case "cancel":
				return []byte{1}, nil
			default:
				return []byte{2, v.Generation}, nil
			}
		},
		DecodeInput: func(b []byte) (Input, error) {
			if len(b) == 1 && b[0] == 0 {
				return Input{Kind: "start"}, nil
			}
			if len(b) == 1 && b[0] == 1 {
				return Input{Kind: "cancel"}, nil
			}
			if len(b) == 2 && b[0] == 2 {
				return Input{Kind: "complete", Generation: b[1]}, nil
			}
			return Input{}, fmt.Errorf("invalid request input encoding")
		},
		EncodeOutput: func(v Output) ([]byte, error) {
			var tag byte
			switch v.Kind {
			case "request":
				tag = 0
			case "publish":
				tag = 1
			case "release":
				tag = 2
			default:
				return nil, fmt.Errorf("invalid request output")
			}
			return []byte{tag, v.Generation}, nil
		},
	}
	return s.Model[State, Input, Output]{
		InitialState: func() (State, error) { return State{}, nil }, CloneState: cloneState, CloneInput: s.ValueCopy[Input], CloneOutput: s.ValueCopy[Output], Codec: codec,
		Step: func(before State, in Input) (s.Transition[State, Output], error) {
			if !validInput(in) {
				return s.Transition[State, Output]{}, fmt.Errorf("invalid request input")
			}
			state := cloneState(before)
			outputs := []Output{}
			switch {
			case in.Kind == "start" && state.Generation < 2:
				state.Generation++
				state.Active = true
				state.Ready = false
				state.Pending = append(state.Pending, state.Generation)
				outputs = append(outputs, Output{"request", state.Generation})
			case in.Kind == "cancel" && state.Active:
				state.Active = false
				state.Ready = false
			case in.Kind == "complete" && slices.Contains(state.Pending, in.Generation):
				state.Pending = slices.DeleteFunc(state.Pending, func(v uint8) bool { return v == in.Generation })
				if m.InjectBug || state.Active && in.Generation == state.Generation {
					state.Ready = true
					outputs = append(outputs, Output{"publish", in.Generation})
				} else {
					outputs = append(outputs, Output{"release", in.Generation})
				}
			default:
				return s.Transition[State, Output]{State: state, Outputs: outputs, Disposition: s.Rejected("input is not enabled")}, nil
			}
			return s.Accept[State, Output](state, outputs...), nil
		},
		CheckState: func(v State) ([]s.Check, error) {
			bounded := len(v.Pending) <= 2 && v.Generation <= 2
			for i, p := range v.Pending {
				if p == 0 || p > v.Generation || i > 0 && v.Pending[i-1] >= p {
					bounded = false
				}
			}
			return []s.Check{rule("ready_requires_active", !v.Ready || v.Active, "retired request became ready"), rule("bounded_pending", bounded, "invalid pending ledger")}, nil
		},
		CheckTransition: func(before State, in Input, tr s.Transition[State, Output]) ([]s.Check, error) {
			stale := in.Kind == "complete" && slices.Contains(before.Pending, in.Generation) && (!before.Active || in.Generation != before.Generation)
			holds := !stale || len(tr.Outputs) == 1 && tr.Outputs[0] == (Output{"release", in.Generation})
			return []s.Check{rule("stale_completion_is_cleanup_only", holds, "stale completion published instead of releasing")}, nil
		},
		Inputs: func(v State) (s.Iterator[Input], error) { return s.Values(inputs(v)), nil },
		EqualStates: func(a, b State) bool {
			return a.Generation == b.Generation && a.Active == b.Active && a.Ready == b.Ready && slices.Equal(a.Pending, b.Pending)
		},
		EstimatedStateBytes: func(v State) (*uint64, error) {
			base := uint64(unsafe.Sizeof(v))
			capacity := uint64(cap(v.Pending))
			if capacity > ^uint64(0)-base {
				return nil, nil
			}
			total := base + capacity
			return &total, nil
		},
	}
}
func inputs(v State) []Input {
	out := []Input{}
	if v.Generation < 2 {
		out = append(out, Input{Kind: "start"})
	}
	if v.Active {
		out = append(out, Input{Kind: "cancel"})
	}
	for _, g := range v.Pending {
		out = append(out, Input{Kind: "complete", Generation: g})
	}
	return out
}

// Generator uses the same ordered finite domain and one RNG index as Rust.
func (m RequestModel) Generator() s.GenerateModel[State, Input, Output] {
	return s.GenerateModel[State, Input, Output]{Model: m.Model(), InputEqual: func(a, b Input) (bool, error) { return a == b, nil }, Generate: func(state State, rng *s.Rng) (Input, bool, error) {
		if rng == nil {
			return Input{}, false, fmt.Errorf("nil RNG")
		}
		values := inputs(state)
		index, ok := rng.Index(uint64(len(values)))
		if !ok {
			return Input{}, false, nil
		}
		return values[index], true, nil
	}, IsEnabled: func(state State, in Input) (bool, error) {
		return validInput(in) && slices.Contains(inputs(state), in), nil
	}}
}
