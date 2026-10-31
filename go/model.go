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
	Metadata         func() (Metadata, error)
	EncodeState      func(S) ([]byte, error)
	DecodeState      func([]byte) (S, error)
	EncodeInput      func(I) ([]byte, error)
	DecodeInput      func([]byte) (I, error)
	EncodeOutput     func(O) ([]byte, error)
	EncodeStateInto  func(S, *EncodeBuffer) error
	EncodeInputInto  func(I, *EncodeBuffer) error
	EncodeOutputInto func(O, *EncodeBuffer) error
}

// Model uses ordinary typed Go callbacks. Enumeration and persistence hooks are
// optional until those operations are requested. Copies are explicit because a
// generic Go assignment does not deeply copy application-owned slices or maps.
type Model[S, I, O any] struct {
	InitialState        func() (S, error)
	Step                func(S, I) (Transition[S, O], error)
	CheckState          func(S) ([]Check, error)
	CheckStateInto      func(S, *CheckSink) error
	CheckTransitionInto func(S, I, Transition[S, O], *CheckSink) error
	CheckTransition     func(S, I, Transition[S, O]) ([]Check, error)
	CloneState          func(S) S
	CloneInput          func(I) I
	CloneOutput         func(O) O
	Inputs              func(S) (Iterator[I], error)
	EqualStates         func(S, S) bool
	// Equal states must hash equally. Nil uses a single equality bucket.
	HashState func(S) uint64
	// EstimatedStateBytes estimates retained state plus its owned data. Nil hook or
	// nil result means unknown; it never represents a hard process-memory bound.
	EstimatedStateBytes func(S) (*uint64, error)
	Codec               *Codec[S, I, O]
}

func (m Model[S, I, O]) validate() error {
	if m.CloneState == nil || m.CloneInput == nil || m.CloneOutput == nil || (m.CheckState == nil && m.CheckStateInto == nil) {
		return &ModelError{"model requires clone and state-check callbacks"}
	}
	return nil
}
func (m Model[S, I, O]) validateCodec() error {
	if err := m.validate(); err != nil {
		return err
	}
	c := m.Codec
	if c == nil || c.Metadata == nil || (c.EncodeState == nil && c.EncodeStateInto == nil) || c.DecodeState == nil || (c.EncodeInput == nil && c.EncodeInputInto == nil) || c.DecodeInput == nil || (c.EncodeOutput == nil && c.EncodeOutputInto == nil) {
		return &ModelError{"model requires canonical codec callbacks"}
	}
	return nil
}

// call contains callback panics at the public engine boundary; a panic is never
// converted into a passing check or a property violation.
// callbackPanic retains unwind provenance across composed native adapters.
// Its public ModelError view remains available through errors.As.
type callbackPanic struct {
	diagnostic *ModelError
	message    string
}

func (e *callbackPanic) Error() string { return e.diagnostic.Error() }
func (e *callbackPanic) Unwrap() error { return e.diagnostic }

func call[T any](stage string, f func() (T, error)) (value T, err error) {
	defer func() {
		if p := recover(); p != nil {
			var zero T
			value = zero
			message := campaignPanicMessage(p)
			err = &callbackPanic{&ModelError{fmt.Sprintf("%s: panic: %v", stage, p)}, message}
		}
	}()
	value, err = f()
	if err != nil {
		var zero T
		if provenance, ok := err.(*callbackPanic); ok {
			return zero, &callbackPanic{&ModelError{fmt.Sprintf("%s: %v", stage, err)}, provenance.message}
		}
		return zero, &ModelError{fmt.Sprintf("%s: %v", stage, err)}
	}
	return
}
func copied[T any](clone func(T) T, v T) (T, error) {
	return call("snapshot", func() (T, error) { return clone(v), nil })
}

func stateChecks[S, I, O any](m Model[S, I, O], state S) ([]Check, error) {
	checks := []Check{}
	if e := stateChecksInto(m, state, &checks); e != nil {
		return nil, e
	}
	return checks, nil
}

func cloneTransition[S, I, O any](m Model[S, I, O], t Transition[S, O]) (Transition[S, O], error) {
	return call("snapshot", func() (Transition[S, O], error) {
		if err := t.Disposition.Validate(); err != nil {
			return Transition[S, O]{}, err
		}
		// Capture the output elements before invoking the independent state
		// clone hook, which may hold an alias to the caller's output slice.
		outputs := append([]O(nil), t.Outputs...)
		result := Transition[S, O]{State: m.CloneState(t.State), Outputs: make([]O, len(outputs)), Disposition: t.Disposition}
		for i, o := range outputs {
			result.Outputs[i] = m.CloneOutput(o)
		}
		return result, nil
	})
}
func edgeChecks[S, I, O any](m Model[S, I, O], before S, input I, t Transition[S, O]) ([]Check, error) {
	checks := []Check{}
	if e := edgeChecksInto(m, before, input, t, &checks); e != nil {
		return nil, e
	}
	return checks, nil
}

func perform[S, I, O any](m Model[S, I, O], before S, input I) (Transition[S, O], error) {
	return performStage(m, before, input, "transition")
}
func performStage[S, I, O any](m Model[S, I, O], before S, input I, stage string) (Transition[S, O], error) {
	var zero Transition[S, O]
	b, err := copied(m.CloneState, before)
	if err != nil {
		return zero, err
	}
	i, err := copied(m.CloneInput, input)
	if err != nil {
		return zero, err
	}
	t, err := call(stage, func() (Transition[S, O], error) { return m.Step(b, i) })
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
	checks := []Check{}
	if e := CheckObservedInto(m, before, input, t, sequence, policy, &checks); e != nil {
		return nil, e
	}
	return checks, nil
}

// CheckObservedInto clears caller storage at entry and on every error while
// retaining capacity. It observes completed execution and never invokes Step.
func CheckObservedInto[S, I, O any](m Model[S, I, O], before S, input I, t Transition[S, O], sequence uint64, policy CheckPolicy, checks *[]Check) error {
	_, err := checkObservedCountInto(m, before, input, t, sequence, policy, checks)
	return err
}

func checkObservedCountInto[S, I, O any](m Model[S, I, O], before S, input I, t Transition[S, O], sequence uint64, policy CheckPolicy, checks *[]Check) (count int, err error) {
	if checks == nil {
		return 0, &ConfigError{"nil check storage"}
	}
	resetChecks(checks)
	defer func() {
		if err != nil {
			resetChecks(checks)
		}
	}()
	if e := m.validate(); e != nil {
		return 0, e
	}
	if sequence == 0 || policy.StateEvery == 0 {
		return 0, &ConfigError{"sequence and state period must be positive"}
	}
	if e := t.Disposition.Validate(); e != nil {
		return 0, &ModelError{fmt.Sprintf("observed transition: %v", e)}
	}
	if sequence%policy.StateEvery == 0 {
		if e := stateChecksInto(m, t.State, checks); e != nil {
			return 0, e
		}
	} else {
		*checks = append(*checks, Skipped("stateless.state_checks", "periodic checking policy"))
	}
	count = len(*checks)
	if policy.TransitionChecks {
		if e := edgeChecksInto(m, before, input, t, checks); e != nil {
			return 0, e
		}
	} else {
		*checks = append(*checks, Skipped("stateless.transition_checks", "disabled by policy"))
	}
	return count, nil
}

func hasFailure(checks []Check) bool {
	for _, c := range checks {
		if c.Status == "failed" {
			return true
		}
	}
	return false
}
