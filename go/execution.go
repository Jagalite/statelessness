package statelessness

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
	var checkStorage []Check
	err = stateChecksInto(m, state, &checkStorage)
	initialChecks := copyChecks(checkStorage)
	if err != nil {
		return nil, err
	}
	sc, err = copied(m.CloneState, state)
	if err != nil {
		return nil, err
	}
	initial, err := encodeState(m, sc, limits.MaxBlobBytes)
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
		encoded, err := encodeInput(m, i, limits.MaxBlobBytes)
		if err != nil {
			return terminalError(err)
		}
		t, err := perform(m, state, x.value)
		if err != nil {
			return terminalError(err)
		}
		err = CheckObservedInto(m, state, x.value, t, uint64(len(trace.Steps)+1), FullChecks(), &checkStorage)
		checks := copyChecks(checkStorage)
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
			outputs[k], err = encodeOutput(m, oc, limits.MaxBlobBytes)
			if err != nil {
				return terminalError(err)
			}
		}
		sc, err = copied(m.CloneState, t.State)
		if err != nil {
			return terminalError(err)
		}
		post, err := encodeState(m, sc, limits.MaxBlobBytes)
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
	report, err := ReplayWithObservations(m, trace, allowBuildMismatch, ReplayObserver[S, I, O]{})
	if err != nil {
		if replayError, ok := err.(*ReplayError[S, I, O]); ok {
			return nil, replayError.Err
		}
		return nil, err
	}
	return report, nil
}
