package statelessness

import (
	"bytes"
	"fmt"
	"slices"
)

type TraceStep struct {
	Input       []byte
	Disposition Disposition
	Outputs     [][]byte
	PostState   []byte
	Checks      []Check
}
type Trace struct {
	Metadata      Metadata
	InitialState  []byte
	InitialChecks []Check
	Steps         []TraceStep
	Termination   string
	Error         string
}

// TraceLimits count retained bytes/items, not arbitrary allocations by callbacks.
type TraceLimits struct {
	MaxSteps        int
	MaxBlobBytes    int
	MaxItems        int
	MaxPayloadBytes int
	MaxJSONBytes    int
}

func DefaultTraceLimits() TraceLimits {
	return TraceLimits{100_000, 4 * 1024 * 1024, 250_000, 32 * 1024 * 1024, 64 * 1024 * 1024}
}
func (l TraceLimits) validate() error {
	if l.MaxSteps < 0 || l.MaxBlobBytes < 0 || l.MaxItems < 0 || l.MaxPayloadBytes < 0 || l.MaxJSONBytes < 0 {
		return &ConfigError{"negative trace limit"}
	}
	// Hex expansion and aggregate accounting must not overflow platform Int.
	maximum := int(^uint(0)>>1) / 2
	if l.MaxSteps > maximum || l.MaxBlobBytes > maximum || l.MaxItems > maximum || l.MaxPayloadBytes > maximum || l.MaxJSONBytes > maximum {
		return &ConfigError{"trace limit exceeds safe platform range"}
	}
	return nil
}
func encode[T any](stage string, callback func(T) ([]byte, error), value T, maximum int) ([]byte, error) {
	return call(stage, func() ([]byte, error) {
		b, err := callback(value)
		if err != nil {
			return nil, err
		}
		if len(b) > maximum {
			return nil, fmt.Errorf("encoded blob exceeds byte limit")
		}
		return append([]byte{}, b...), nil
	})
}
func Record[S, I, O any](m Model[S, I, O], inputs Iterator[I], maxSteps int) (*Trace, error) {
	return RecordWithLimits(m, inputs, maxSteps, DefaultTraceLimits())
}

// RecordWithLimits preserves only a coherent prefix after a later callback error.
// A failure in initial checkpoint creation returns an error without a trace.
func RecordWithLimits[S, I, O any](m Model[S, I, O], inputs Iterator[I], maxSteps int, limits TraceLimits) (*Trace, error) {
	if maxSteps < 0 {
		return nil, &ConfigError{"negative step limit"}
	}
	if err := limits.validate(); err != nil {
		return nil, err
	}
	if err := m.validateCodec(); err != nil {
		return nil, err
	}
	if m.InitialState == nil || m.Step == nil {
		return nil, &ModelError{"record requires initial and step callbacks"}
	}
	state, err := call("initial state", m.InitialState)
	if err != nil {
		return nil, err
	}
	state, err = copied(m.CloneState, state)
	if err != nil {
		return nil, err
	}
	sc, err := copied(m.CloneState, state)
	if err != nil {
		return nil, err
	}
	initialChecks, err := collect("initial check", func() ([]Check, error) { return m.CheckState(sc) })
	if err != nil {
		return nil, err
	}
	sc, err = copied(m.CloneState, state)
	if err != nil {
		return nil, err
	}
	initial, err := encode("encode initial state", m.Codec.EncodeState, sc, limits.MaxBlobBytes)
	if err != nil {
		return nil, err
	}
	metadata, err := call("metadata", func() (Metadata, error) {
		v, e := m.Codec.Metadata()
		if e == nil {
			e = v.Validate()
		}
		return v, e
	})
	if err != nil {
		return nil, err
	}
	items, payload := len(initialChecks), len(initial)
	if items > limits.MaxItems || payload > limits.MaxPayloadBytes {
		return nil, &ModelError{"recording limit: initial checkpoint"}
	}
	trace := &Trace{Metadata: metadata, InitialState: initial, InitialChecks: initialChecks, Steps: []TraceStep{}, Termination: "completed"}
	if hasFailure(initialChecks) {
		trace.Termination = "property_failed"
		return trace, nil
	}
	terminalError := func(err error) (*Trace, error) {
		trace.Termination = "model_error"
		trace.Error = err.Error()
		return trace, nil
	}
	for {
		// Iterator errors belong to the coherent-prefix report, not a fabricated step.
		type item struct {
			value I
			ok    bool
		}
		x, err := call("inputs", func() (item, error) { v, ok, e := inputs(); return item{v, ok}, e })
		if err != nil {
			return terminalError(err)
		}
		if !x.ok {
			return trace, nil
		}
		if len(trace.Steps) >= min(maxSteps, limits.MaxSteps) {
			trace.Termination = "step_limit"
			return trace, nil
		}
		i, err := copied(m.CloneInput, x.value)
		if err != nil {
			return terminalError(err)
		}
		encoded, err := encode("encode input", m.Codec.EncodeInput, i, limits.MaxBlobBytes)
		if err != nil {
			return terminalError(err)
		}
		t, err := perform(m, state, x.value)
		if err != nil {
			return terminalError(err)
		}
		checks, err := CheckObserved(m, state, x.value, t, uint64(len(trace.Steps)+1), FullChecks())
		if err != nil {
			return terminalError(err)
		}
		nextItems := items + 1 + len(checks) + len(t.Outputs)
		if nextItems > limits.MaxItems {
			return terminalError(&ModelError{"recording limit: aggregate items"})
		}
		outputs := make([][]byte, len(t.Outputs))
		for k, o := range t.Outputs {
			oc, e := copied(m.CloneOutput, o)
			if e != nil {
				return terminalError(e)
			}
			outputs[k], err = encode("encode output", m.Codec.EncodeOutput, oc, limits.MaxBlobBytes)
			if err != nil {
				return terminalError(err)
			}
		}
		sc, err = copied(m.CloneState, t.State)
		if err != nil {
			return terminalError(err)
		}
		post, err := encode("encode state", m.Codec.EncodeState, sc, limits.MaxBlobBytes)
		if err != nil {
			return terminalError(err)
		}
		nextPayload := payload + len(encoded) + len(post)
		for _, o := range outputs {
			nextPayload += len(o)
		}
		if nextPayload > limits.MaxPayloadBytes {
			return terminalError(&ModelError{"recording limit: aggregate payload bytes"})
		}
		trace.Steps = append(trace.Steps, TraceStep{encoded, t.Disposition, outputs, post, checks})
		state = t.State
		items = nextItems
		payload = nextPayload
		if hasFailure(checks) {
			trace.Termination = "property_failed"
			return trace, nil
		}
	}
}

type ReplayReport struct {
	Outcome           string  `json:"outcome"`
	StepsVerified     int     `json:"steps_verified"`
	FailureReproduced bool    `json:"failure_reproduced"`
	BuildMatches      bool    `json:"build_matches"`
	Step              *int    `json:"step"`
	Field             *string `json:"field"`
}

func sameFailure(a, b []Check) bool {
	for _, x := range a {
		if x.Status == "failed" {
			for _, y := range b {
				if y.Status == "failed" && x.ID == y.ID {
					return true
				}
			}
		}
	}
	return false
}
func validTermination(t string) bool {
	return t == "completed" || t == "property_failed" || t == "step_limit" || t == "interrupted" || t == "model_error"
}
func ValidateRecording(t *Trace) error {
	if t == nil {
		return &ModelError{"nil trace"}
	}
	if !validTermination(t.Termination) || t.Error != "" && t.Termination != "model_error" {
		return &ModelError{"invalid trace termination"}
	}
	initialFailed := hasFailure(t.InitialChecks)
	if initialFailed && len(t.Steps) > 0 {
		return &ModelError{"trace continues after initial property failure"}
	}
	for k, s := range t.Steps {
		if k < len(t.Steps)-1 && hasFailure(s.Checks) {
			return &ModelError{"trace continues after property failure"}
		}
	}
	failed := initialFailed || len(t.Steps) > 0 && hasFailure(t.Steps[len(t.Steps)-1].Checks)
	if failed != (t.Termination == "property_failed") {
		return &ModelError{"trace termination disagrees with recorded checks"}
	}
	return nil
}

// Replay starts from the saved snapshot. It never calls InitialState. Exact
// replay verifies a prefix, not graph exhaustion or its unrecorded terminal error.
func Replay[S, I, O any](m Model[S, I, O], trace *Trace, allowBuildMismatch bool) (*ReplayReport, error) {
	if err := m.validateCodec(); err != nil {
		return nil, err
	}
	if trace == nil {
		return nil, &ModelError{"nil trace"}
	}
	identity, err := call("metadata", func() (Metadata, error) {
		v, e := m.Codec.Metadata()
		if e == nil {
			e = v.Validate()
		}
		return v, e
	})
	if err != nil {
		return nil, err
	}
	r := &ReplayReport{Outcome: "exact", BuildMatches: identity.Build == trace.Metadata.Build}
	if identity.Name != trace.Metadata.Name || identity.ModelVersion != trace.Metadata.ModelVersion || identity.PropertiesVersion != trace.Metadata.PropertiesVersion || identity.CodecVersion != trace.Metadata.CodecVersion || !r.BuildMatches && !allowBuildMismatch {
		r.Outcome = "incompatible"
		return r, nil
	}
	if err = ValidateRecording(trace); err != nil {
		return nil, err
	}
	state, err := call("decode initial state", func() (S, error) { return m.Codec.DecodeState(slices.Clone(trace.InitialState)) })
	if err != nil {
		return nil, err
	}
	sc, err := copied(m.CloneState, state)
	if err != nil {
		return nil, err
	}
	encoded, err := encode("encode initial state", m.Codec.EncodeState, sc, int(^uint(0)>>1))
	if err != nil {
		return nil, err
	}
	if !bytes.Equal(encoded, trace.InitialState) {
		return nil, &ModelError{"initial state encoding is not canonical"}
	}
	sc, err = copied(m.CloneState, state)
	if err != nil {
		return nil, err
	}
	checks, err := collect("initial check", func() ([]Check, error) { return m.CheckState(sc) })
	if err != nil {
		return nil, err
	}
	r.FailureReproduced = sameFailure(trace.InitialChecks, checks)
	if !slices.Equal(checks, trace.InitialChecks) {
		r.Outcome = "diverged"
		field := "initial checks"
		r.Field = &field
		return r, nil
	}
	for index, expected := range trace.Steps {
		input, err := call("decode input", func() (I, error) { return m.Codec.DecodeInput(slices.Clone(expected.Input)) })
		if err != nil {
			return nil, err
		}
		ic, err := copied(m.CloneInput, input)
		if err != nil {
			return nil, err
		}
		encoded, err := encode("encode input", m.Codec.EncodeInput, ic, int(^uint(0)>>1))
		if err != nil {
			return nil, err
		}
		if !bytes.Equal(encoded, expected.Input) {
			return nil, &ModelError{fmt.Sprintf("input %d encoding is not canonical", index+1)}
		}
		actual, err := perform(m, state, input)
		if err != nil {
			return nil, err
		}
		checks, err := CheckObserved(m, state, input, actual, uint64(index+1), FullChecks())
		if err != nil {
			return nil, err
		}
		r.FailureReproduced = r.FailureReproduced || sameFailure(expected.Checks, checks)
		outputs := make([][]byte, len(actual.Outputs))
		for k, o := range actual.Outputs {
			oc, e := copied(m.CloneOutput, o)
			if e != nil {
				return nil, e
			}
			outputs[k], err = encode("encode output", m.Codec.EncodeOutput, oc, int(^uint(0)>>1))
			if err != nil {
				return nil, err
			}
		}
		sc, err = copied(m.CloneState, actual.State)
		if err != nil {
			return nil, err
		}
		post, err := encode("encode state", m.Codec.EncodeState, sc, int(^uint(0)>>1))
		if err != nil {
			return nil, err
		}
		mismatch := ""
		switch {
		case actual.Disposition != expected.Disposition:
			mismatch = "disposition"
		case !slices.EqualFunc(outputs, expected.Outputs, bytes.Equal):
			mismatch = "outputs"
		case !bytes.Equal(post, expected.PostState):
			mismatch = "state"
		case !slices.Equal(checks, expected.Checks):
			mismatch = "checks"
		}
		if mismatch != "" {
			r.Outcome = "diverged"
			n := index + 1
			r.Step = &n
			r.Field = &mismatch
			return r, nil
		}
		r.StepsVerified++
		state = actual.State
	}
	return r, nil
}
