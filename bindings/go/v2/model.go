// Package v2 keeps typed application models in Go and executes the shared Rust
// engine through ABI 2. Sessions are synchronous and confined to one OS thread.
package v2

import (
	"fmt"
	"sync"
	"unicode/utf8"
)

type Metadata struct {
	Name              string `json:"name"`
	Build             string `json:"build"`
	ModelVersion      uint32 `json:"model_version"`
	PropertiesVersion uint32 `json:"properties_version"`
	CodecVersion      uint32 `json:"codec_version"`
}
type Check struct {
	ID      string `json:"id"`
	Status  string `json:"status"`
	Details string `json:"details"`
}

func Passed(id string) Check           { return Check{ID: id, Status: "passed"} }
func Failed(id, details string) Check  { return Check{ID: id, Status: "failed", Details: details} }
func Skipped(id, details string) Check { return Check{ID: id, Status: "skipped", Details: details} }

type Disposition struct {
	Kind   string `json:"kind"`
	Reason string `json:"reason"`
}

func Accepted() Disposition              { return Disposition{Kind: "accepted"} }
func Rejected(reason string) Disposition { return Disposition{Kind: "rejected", Reason: reason} }
func Ignored(reason string) Disposition  { return Disposition{Kind: "ignored", Reason: reason} }

type Transition[S, O any] struct {
	State       S
	Outputs     []O
	Disposition Disposition
}

func Accept[S, O any](state S, outputs ...O) Transition[S, O] {
	return Transition[S, O]{state, outputs, Accepted()}
}

// ValueCopy is suitable only for immutable values; slices, maps, and pointers
// require application-provided deep snapshots.
func ValueCopy[T any](value T) T { return value }

// Iterator has deterministic, optional cleanup. Next must return false only for
// exhausted input; errors are distinct. The engine closes every opened iterator.
type Iterator[T any] struct {
	Next  func() (T, bool, error)
	Close func() error
}

func Values[T any](values []T) Iterator[T] {
	i := 0
	return Iterator[T]{Next: func() (value T, ok bool, err error) {
		if i == len(values) {
			return
		}
		value = values[i]
		i++
		return value, true, nil
	}}
}

type Codec[S, I, O any] struct {
	EncodeState  func(S) ([]byte, error)
	DecodeState  func([]byte) (S, error)
	EncodeInput  func(I) ([]byte, error)
	DecodeInput  func([]byte) (I, error)
	EncodeOutput func(O) ([]byte, error)
}
type Model[S, I, O any] struct {
	Metadata            Metadata
	InitialState        func() (S, error)
	Step                func(S, I) (Transition[S, O], error)
	CheckState          func(S) ([]Check, error)
	CheckTransition     func(S, I, Transition[S, O]) ([]Check, error)
	CloneState          func(S) S
	CloneInput          func(I) I
	CloneOutput         func(O) O
	Inputs              func(S) (Iterator[I], error)
	EqualStates         func(S, S) (bool, error)
	HashState           func(S) (uint64, error)
	EstimatedStateBytes func(S) (uint64, bool, error)
	Codec               *Codec[S, I, O]
	// Automatic asks the shared Rust reservoir sampler to use Inputs.
	Automatic     bool
	Generate      func(S, *CallbackRNG) (I, bool, error)
	IsEnabled     func(S, I) (bool, error)
	EqualInputs   func(I, I) (bool, error)
	SimplerInputs func(I) (Iterator[I], error)
}

// CallbackRNG is valid only during the Generate callback that received it.
// Retaining it beyond that call produces an error, never a second random stream.
type CallbackRNG struct {
	mu    sync.Mutex
	next  func() (uint64, error)
	index func(uint64) (uint64, bool, error)
}

func (r *CallbackRNG) Next() (uint64, error) {
	if r == nil {
		return 0, fmt.Errorf("RNG callback has ended")
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.next == nil {
		return 0, fmt.Errorf("RNG callback has ended")
	}
	return r.next()
}
func (r *CallbackRNG) Index(upper uint64) (uint64, bool, error) {
	if r == nil {
		return 0, false, fmt.Errorf("RNG callback has ended")
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.index == nil {
		return 0, false, fmt.Errorf("RNG callback has ended")
	}
	return r.index(upper)
}

type Options struct{ MaxValues, MaxItems, MaxBytes uint64 }

func DefaultOptions() Options { return Options{1_000_000, 1_000_000, 64 * 1024 * 1024} }

type Bounds struct {
	States, Transitions, Depth uint64
	StateBytes                 *uint64
	DurationMillis             uint64
}

func DefaultBounds() Bounds { return Bounds{States: 100_000, Transitions: 1_000_000, Depth: 100} }

type FuzzConfig struct {
	Seed, Cases, Steps, Transitions, DurationMillis uint64
	MutationPercent                                 uint32
}

func DefaultFuzzConfig() FuzzConfig {
	return FuzzConfig{Cases: 1000, Steps: 100, Transitions: 100_000, MutationPercent: 50}
}

type ShrinkConfig struct{ Attempts, Transitions, DurationMillis uint64 }

func DefaultShrinkConfig() ShrinkConfig {
	return ShrinkConfig{Attempts: 10_000, Transitions: ^uint64(0)}
}

type CheckPolicy struct {
	StateEvery       uint64
	TransitionChecks bool
}

func FullChecks() CheckPolicy { return CheckPolicy{1, true} }

type Status int32

const (
	OK             Status = 0
	PropertyFailed Status = 1
	Diverged       Status = 2
	Incompatible   Status = 3
)

type Error struct {
	Status           Status
	Operation, Phase uint32
	Sequence         uint64
	Message          string
}

func (e *Error) Error() string {
	return fmt.Sprintf("stateless ABI2 status %d operation %d: %s", e.Status, e.Operation, e.Message)
}

// Results own copies of Go values and bytes. Their private engine handle stays
// alive for Shrink until Close or session teardown. Copy fields remain usable.
type Result[I any] struct {
	Status                                                 Status
	Kind, Termination, States, Transitions, Depth, Skipped uint64
	EstimatedBytes                                         uint64
	HasEstimate                                            bool
	Checks                                                 []Check
	CheckPhases                                            []uint32
	StateCheckCount                                        uint64
	Inputs                                                 []I
	OriginalInputs                                         []I
	Artifact                                               []byte
	Diagnostic                                             string
	StepsVerified, Cases, Attempts                         uint64
	BuildMatches, FailureReproduced, ValidatedOriginal     bool
	HasDivergenceStep                                      bool
	DivergenceStep                                         uint64
	DivergenceField                                        string
	handle                                                 *resultHandle
}
type resultHandle struct {
	mu             sync.Mutex
	value, session uint64
	release        func(uint64) error
}

func (r *Result[I]) Close() error {
	if r == nil || r.handle == nil {
		return nil
	}
	h := r.handle
	if !h.mu.TryLock() {
		return fmt.Errorf("result operation already active")
	}
	defer h.mu.Unlock()
	if h.value == 0 {
		return nil
	}
	err := h.release(h.value)
	if err == nil {
		h.value = 0
	}
	return err
}
func guard[T any](fn func() (T, error)) (value T, err error) {
	defer func() {
		if p := recover(); p != nil {
			err = fmt.Errorf("Go callback panic: %v", p)
		}
	}()
	return fn()
}

// Formatting an application error is another application callback: Error may
// panic, return invalid UTF-8, or return an arbitrarily large string.
func callbackDiagnostic(err error, limit uint64) (message []byte) {
	defer func() {
		if recover() != nil {
			message = boundedDiagnostic("Go callback error (diagnostic formatting panicked)", limit)
		}
	}()
	return boundedDiagnostic(err.Error(), limit)
}

func boundedDiagnostic(text string, limit uint64) []byte {
	maxBytes := int(min(limit, uint64(65536)))
	message := make([]byte, 0, min(len(text), maxBytes))
	for _, r := range text {
		if len(message)+utf8.RuneLen(r) > maxBytes {
			break
		}
		message = utf8.AppendRune(message, r)
	}
	return message
}

func validText(s string) error {
	if !utf8.ValidString(s) {
		return fmt.Errorf("invalid UTF-8")
	}
	return nil
}

func (c Check) Validate() error {
	if !utf8.ValidString(c.ID) || !utf8.ValidString(c.Details) || (c.Status != "passed" && c.Status != "failed" && c.Status != "skipped") || (c.Status == "passed" && c.Details != "") {
		return fmt.Errorf("invalid check")
	}
	return nil
}
func (d Disposition) Validate() error {
	if !utf8.ValidString(d.Reason) || (d.Kind != "accepted" && d.Kind != "rejected" && d.Kind != "ignored") || (d.Kind == "accepted" && d.Reason != "") {
		return fmt.Errorf("invalid disposition")
	}
	return nil
}
func (m Metadata) Validate() error {
	if !utf8.ValidString(m.Name) || !utf8.ValidString(m.Build) {
		return fmt.Errorf("invalid metadata UTF-8")
	}
	return nil
}
