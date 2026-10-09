// Package statelessness implements independent native deterministic model checking.
// No Rust, cgo, Wasm, or third-party dependencies are required. Models describe
// effects as data. Callbacks must not execute I/O or hide mutable model history.
package statelessness

import (
	"fmt"
	"unicode/utf8"
)

const Version = "0.1.0"
const SpecVersion = "1.0.0"

func Profiles() []string { return []string{"core-v1", "bfs-v1", "rng-splitmix64-v1", "trace-json-v1"} }

type ModelError struct{ Message string }

func (e *ModelError) Error() string { return e.Message }

type ConfigError struct{ Message string }

func (e *ConfigError) Error() string { return e.Message }

// Check IDs and details are exact UTF-8 strings, not normalized text.
type Check struct {
	ID      string `json:"id"`
	Status  string `json:"status"`
	Details string `json:"details"`
}

func Passed(id string) Check          { return Check{ID: id, Status: "passed"} }
func Failed(id, details string) Check { return Check{ID: id, Status: "failed", Details: details} }
func Skipped(id, reason string) Check { return Check{ID: id, Status: "skipped", Details: reason} }
func (c Check) Validate() error {
	if !utf8.ValidString(c.ID) || !utf8.ValidString(c.Details) || (c.Status != "passed" && c.Status != "failed" && c.Status != "skipped") || (c.Status == "passed" && c.Details != "") {
		return fmt.Errorf("invalid check")
	}
	return nil
}

type Disposition struct {
	Kind   string `json:"kind"`
	Reason string `json:"reason"`
}

func Accepted() Disposition              { return Disposition{Kind: "accepted"} }
func Rejected(reason string) Disposition { return Disposition{Kind: "rejected", Reason: reason} }
func Ignored(reason string) Disposition  { return Disposition{Kind: "ignored", Reason: reason} }
func (d Disposition) Validate() error {
	if !utf8.ValidString(d.Reason) || (d.Kind != "accepted" && d.Kind != "rejected" && d.Kind != "ignored") || (d.Kind == "accepted" && d.Reason != "") {
		return fmt.Errorf("invalid disposition")
	}
	return nil
}

type Transition[S, O any] struct {
	State       S
	Outputs     []O
	Disposition Disposition
}

func Accept[S, O any](state S, outputs ...O) Transition[S, O] {
	return Transition[S, O]{State: state, Outputs: outputs, Disposition: Accepted()}
}

type Metadata struct {
	Name              string `json:"name"`
	ModelVersion      uint32 `json:"model_version"`
	PropertiesVersion uint32 `json:"properties_version"`
	CodecVersion      uint32 `json:"codec_version"`
	Build             string `json:"build"`
}

func Identity(name, build string) Metadata { return Metadata{name, 1, 1, 1, build} }
func (m Metadata) Validate() error {
	if !utf8.ValidString(m.Name) || !utf8.ValidString(m.Build) {
		return fmt.Errorf("invalid metadata UTF-8")
	}
	return nil
}

// Iterator returns (value, true, nil), then (zero, false, nil) at exhaustion.
// It permits errors and lazy finite domains without collecting every candidate.
type Iterator[T any] func() (T, bool, error)

func Values[T any](values []T) Iterator[T] {
	index := 0
	return func() (value T, ok bool, err error) {
		if index == len(values) {
			return
		}
		value = values[index]
		index++
		return value, true, nil
	}
}

// ValueCopy is suitable ONLY for immutable/value-only types. For slices, maps,
// pointers, or structs containing references, provide a real deep copy instead.
func ValueCopy[T any](value T) T { return value }

type Codec[S, I, O any] struct {
	Metadata     func() (Metadata, error)
	EncodeState  func(S) ([]byte, error)
	DecodeState  func([]byte) (S, error)
	EncodeInput  func(I) ([]byte, error)
	DecodeInput  func([]byte) (I, error)
	EncodeOutput func(O) ([]byte, error)
}

// Model uses ordinary typed Go callbacks. Enumeration and persistence hooks are
// optional until those operations are requested. Copies are explicit because a
// generic Go assignment does not deeply copy application-owned slices or maps.
type Model[S, I, O any] struct {
	InitialState    func() (S, error)
	Step            func(S, I) (Transition[S, O], error)
	CheckState      func(S) ([]Check, error)
	CheckTransition func(S, I, Transition[S, O]) ([]Check, error)
	CloneState      func(S) S
	CloneInput      func(I) I
	CloneOutput     func(O) O
	Inputs          func(S) (Iterator[I], error)
	EqualStates     func(S, S) bool
	// Equal states must hash equally. Nil uses a single equality bucket.
	HashState func(S) uint64
	Codec     *Codec[S, I, O]
}

func (m Model[S, I, O]) validate() error {
	if m.CloneState == nil || m.CloneInput == nil || m.CloneOutput == nil || m.CheckState == nil {
		return &ModelError{"model requires clone and state-check callbacks"}
	}
	return nil
}
func (m Model[S, I, O]) validateCodec() error {
	if err := m.validate(); err != nil {
		return err
	}
	c := m.Codec
	if c == nil || c.Metadata == nil || c.EncodeState == nil || c.DecodeState == nil || c.EncodeInput == nil || c.DecodeInput == nil || c.EncodeOutput == nil {
		return &ModelError{"model requires canonical codec callbacks"}
	}
	return nil
}

// call contains callback panics at the public engine boundary; a panic is never
// converted into a passing check or a property violation.
func call[T any](stage string, f func() (T, error)) (value T, err error) {
	defer func() {
		if p := recover(); p != nil {
			var zero T
			value = zero
			err = &ModelError{fmt.Sprintf("%s: panic: %v", stage, p)}
		}
	}()
	value, err = f()
	if err != nil {
		var zero T
		return zero, &ModelError{fmt.Sprintf("%s: %v", stage, err)}
	}
	return
}
func copied[T any](clone func(T) T, v T) (T, error) {
	return call("snapshot", func() (T, error) { return clone(v), nil })
}
func collect(stage string, f func() ([]Check, error)) ([]Check, error) {
	return call(stage, func() ([]Check, error) {
		values, err := f()
		if err != nil {
			return nil, err
		}
		result := make([]Check, len(values))
		copy(result, values)
		for _, c := range result {
			if err = c.Validate(); err != nil {
				return nil, err
			}
		}
		return result, nil
	})
}
func stateChecks[S, I, O any](m Model[S, I, O], s S) ([]Check, error) {
	copy, err := copied(m.CloneState, s)
	if err != nil {
		return nil, err
	}
	return collect("state check", func() ([]Check, error) { return m.CheckState(copy) })
}
func cloneTransition[S, I, O any](m Model[S, I, O], t Transition[S, O]) (Transition[S, O], error) {
	return call("snapshot", func() (Transition[S, O], error) {
		if err := t.Disposition.Validate(); err != nil {
			return Transition[S, O]{}, err
		}
		result := Transition[S, O]{State: m.CloneState(t.State), Outputs: make([]O, len(t.Outputs)), Disposition: t.Disposition}
		for i, o := range t.Outputs {
			result.Outputs[i] = m.CloneOutput(o)
		}
		return result, nil
	})
}
func edgeChecks[S, I, O any](m Model[S, I, O], before S, input I, t Transition[S, O]) ([]Check, error) {
	if m.CheckTransition == nil {
		return []Check{}, nil
	}
	b, err := copied(m.CloneState, before)
	if err != nil {
		return nil, err
	}
	i, err := copied(m.CloneInput, input)
	if err != nil {
		return nil, err
	}
	tr, err := cloneTransition(m, t)
	if err != nil {
		return nil, err
	}
	return collect("transition check", func() ([]Check, error) { return m.CheckTransition(b, i, tr) })
}
func perform[S, I, O any](m Model[S, I, O], before S, input I) (Transition[S, O], error) {
	var zero Transition[S, O]
	b, err := copied(m.CloneState, before)
	if err != nil {
		return zero, err
	}
	i, err := copied(m.CloneInput, input)
	if err != nil {
		return zero, err
	}
	t, err := call("transition", func() (Transition[S, O], error) { return m.Step(b, i) })
	if err != nil {
		return zero, err
	}
	return cloneTransition(m, t)
}

type CheckPolicy struct {
	StateEvery       uint64
	TransitionChecks bool
}

func FullChecks() CheckPolicy { return CheckPolicy{1, true} }

// CheckObserved checks an already completed transition without invoking Step.
// Callback errors invalidate the entire batch; partial checks are never returned.
func CheckObserved[S, I, O any](m Model[S, I, O], before S, input I, t Transition[S, O], sequence uint64, policy CheckPolicy) ([]Check, error) {
	if err := m.validate(); err != nil {
		return nil, err
	}
	if sequence == 0 || policy.StateEvery == 0 {
		return nil, &ConfigError{"sequence and state period must be positive"}
	}
	var sc, ec []Check
	var err error
	if sequence%policy.StateEvery == 0 {
		sc, err = stateChecks(m, t.State)
	} else {
		sc = []Check{Skipped("stateless.state_checks", "periodic checking policy")}
	}
	if err != nil {
		return nil, err
	}
	if policy.TransitionChecks {
		ec, err = edgeChecks(m, before, input, t)
	} else {
		ec = []Check{Skipped("stateless.transition_checks", "disabled by policy")}
	}
	if err != nil {
		return nil, err
	}
	return append(sc, ec...), nil
}
func hasFailure(checks []Check) bool {
	for _, c := range checks {
		if c.Status == "failed" {
			return true
		}
	}
	return false
}
