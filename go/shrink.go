package statelessness

type ShrinkConfig64 struct{ MaxAttempts uint64 }

func DefaultShrinkConfig64() ShrinkConfig64 { return ShrinkConfig64{10000} }

type ShrinkLimits struct {
	Control                RunLimits
	MaxReplayedTransitions *uint64
}
type ShrinkOperation64 struct {
	Kind                           string
	Start, End, Index, Alternative uint64
}
type ShrinkAttempt64 struct {
	Attempt, CandidateLength uint64
	Operation                ShrinkOperation64
	Outcome                  string
}
type ShrinkReport64[I any] struct {
	Original, Minimized Failure[I]
	Attempts            uint64
	Termination         string
	History             []ShrinkAttempt64
	ReplayedTransitions uint64
	ValidatedOriginal   bool
}
type shrinkWork[S, I, O any] struct {
	g           GenerateModel[S, I, O]
	control     runControl
	maximum     *uint64
	transitions uint64
	checks      []Check
}
type shrinkCandidate[I any] struct {
	kind    string
	failure *Failure[I]
	stop    string
}

func cloneFailure[S, I, O any](g GenerateModel[S, I, O], failure Failure[I]) (Failure[I], error) {
	inputs, e := cloneInputs(g, failure.Inputs)
	if e != nil {
		return Failure[I]{}, e
	}
	violations := append([]Violation{}, failure.Violations...)
	return Failure[I]{inputs, violations}, nil
}
func hasTarget[I any](failure Failure[I], target Violation) bool {
	for _, v := range failure.Violations {
		if v.Phase == target.Phase && v.Check.ID == target.Check.ID && v.Check.Status == "failed" {
			return true
		}
	}
	return false
}
func (w *shrinkWork[S, I, O]) replay(inputs []I) (shrinkCandidate[I], error) {
	if reason := w.control.stop(); reason != "" {
		return shrinkCandidate[I]{kind: "stopped", stop: reason}, nil
	}
	g := w.g
	// Preserve the candidate before initial/check callbacks can mutate aliased
	// inputs. Capture only transitions that this replay can reach and defer
	// individual copy failures until that position is actually needed.
	count := len(inputs)
	if w.maximum != nil {
		remaining := uint64(0)
		if w.transitions < *w.maximum {
			remaining = *w.maximum - w.transitions
		}
		if remaining < uint64(count) {
			count = int(remaining)
		}
	}
	slots := append([]I(nil), inputs[:count]...)
	captured := make([]I, count)
	captureErrors := make([]error, count)
	for i, value := range slots {
		captured[i], captureErrors[i] = copied(g.CloneInput, value)
	}
	state, e := call("shrink initial state", g.InitialState)
	if e != nil {
		return shrinkCandidate[I]{}, e
	}
	state, e = copied(g.CloneState, state)
	if e != nil {
		return shrinkCandidate[I]{}, e
	}
	checks, e := searchInitialObservedInto(g.Model, state, nil, &w.checks)
	if e != nil {
		return shrinkCandidate[I]{}, e
	}
	var skipped uint64
	violations := findings("initial_state", checks, &skipped)
	if len(violations) > 0 {
		return shrinkCandidate[I]{kind: "failure", failure: &Failure[I]{[]I{}, violations}}, nil
	}
	if reason := w.control.stop(); reason != "" {
		return shrinkCandidate[I]{kind: "stopped", stop: reason}, nil
	}
	for index := range inputs {
		if reason := w.control.stop(); reason != "" {
			return shrinkCandidate[I]{kind: "stopped", stop: reason}, nil
		}
		if w.maximum != nil && w.transitions >= *w.maximum {
			return shrinkCandidate[I]{kind: "stopped", stop: "transition_limit"}, nil
		}
		if captureErrors[index] != nil {
			return shrinkCandidate[I]{}, captureErrors[index]
		}
		input := captured[index]
		enabled, e := g.enabled(state, input)
		if e != nil {
			return shrinkCandidate[I]{}, e
		}
		if reason := w.control.stop(); reason != "" {
			return shrinkCandidate[I]{kind: "stopped", stop: reason}, nil
		}
		if !enabled {
			return shrinkCandidate[I]{kind: "invalid"}, nil
		}
		transition, e := perform(g.Model, state, input)
		if e != nil {
			return shrinkCandidate[I]{}, e
		}
		if w.transitions == ^uint64(0) {
			return shrinkCandidate[I]{}, &ModelError{"replayed transition count overflow"}
		}
		w.transitions++
		violations, e = searchEdgeObservedInto(g, state, input, transition, nil, &skipped, nil, &w.checks)
		if e != nil {
			return shrinkCandidate[I]{}, e
		}
		if len(violations) > 0 {
			owned, e := cloneInputs(g, captured[:index+1])
			if e != nil {
				return shrinkCandidate[I]{}, e
			}
			return shrinkCandidate[I]{kind: "failure", failure: &Failure[I]{owned, violations}}, nil
		}
		if reason := w.control.stop(); reason != "" {
			return shrinkCandidate[I]{kind: "stopped", stop: reason}, nil
		}
		state = transition.State
	}
	return shrinkCandidate[I]{kind: "pass"}, nil
}
func Shrink64[S, I, O any](g GenerateModel[S, I, O], original Failure[I], config ShrinkConfig64) (*ShrinkReport64[I], error) {
	return ShrinkWithLimits64(g, original, config, ShrinkLimits{})
}
func ShrinkWithLimits64[S, I, O any](g GenerateModel[S, I, O], original Failure[I], config ShrinkConfig64, limits ShrinkLimits) (*ShrinkReport64[I], error) {
	// Replay work is a fixed entry budget; callbacks cannot alter it through the
	// caller's optional pointer. Cancellation remains the live control channel.
	if limits.MaxReplayedTransitions != nil {
		maximum := *limits.MaxReplayedTransitions
		limits.MaxReplayedTransitions = &maximum
	}

	if e := g.validate(); e != nil {
		return nil, e
	}
	if g.InitialState == nil || g.Step == nil || g.InputEqual == nil {
		return nil, &ModelError{"shrink requires initial, step and input equality callbacks"}
	}
	if config.MaxAttempts <= 0 {
		return nil, &ConfigError{"shrink max attempts must be positive"}
	}
	if len(original.Violations) == 0 || original.Violations[0].Check.Status != "failed" {
		return nil, &ModelError{"cannot shrink without a target violation"}
	}
	control, e := newRunControl(limits.Control)
	if e != nil {
		return nil, e
	}
	saved, e := cloneFailure(g, original)
	if e != nil {
		return nil, e
	}
	minimum, e := cloneFailure(g, original)
	if e != nil {
		return nil, e
	}
	report := &ShrinkReport64[I]{Original: saved, Minimized: minimum, Attempts: 1, Termination: "search_complete"}
	target := saved.Violations[0]
	work := shrinkWork[S, I, O]{g: g, control: control, maximum: limits.MaxReplayedTransitions}
	validation, e := work.replay(saved.Inputs)
	if e != nil {
		return nil, e
	}
	report.ReplayedTransitions = work.transitions
	outcome := "retained"
	if validation.kind == "stopped" {
		report.Termination = validation.stop
		outcome = "resource_limit"
	} else if validation.kind != "failure" || !hasTarget(*validation.failure, target) {
		return nil, &ModelError{"original failure does not reproduce target check and phase with valid causality"}
	} else {
		report.Minimized = *validation.failure
		report.ValidatedOriginal = true
	}
	report.History = []ShrinkAttempt64{{1, uint64(len(saved.Inputs)), ShrinkOperation64{Kind: "validate_original"}, outcome}}
	if !report.ValidatedOriginal {
		return report, nil
	}
	initial, e := cloneInputs(g, report.Minimized.Inputs)
	if e != nil {
		return nil, e
	}
	seen := [][]I{initial}
	stopped := func() bool {
		if reason := control.stop(); reason != "" {
			report.Termination = reason
			return true
		}
		return false
	}
	known := func(inputs []I) (bool, error) {
		for _, prior := range seen {
			if len(prior) != len(inputs) {
				continue
			}
			same := true
			for k, left := range prior {
				if stopped() {
					return false, nil
				}
				equal, e := call("input_equal", func() (bool, error) { return g.InputEqual(g.CloneInput(left), g.CloneInput(inputs[k])) })
				if e != nil {
					return false, e
				}
				if stopped() {
					return false, nil
				}
				if !equal {
					same = false
					break
				}
			}
			if same {
				return true, nil
			}
		}
		return false, nil
	}
	attempt := func(candidate []I, operation ShrinkOperation64) (bool, error) {
		if report.Attempts == config.MaxAttempts {
			report.Termination = "attempt_limit"
			return false, nil
		}
		report.Attempts++
		length := uint64(len(candidate))
		replayed, e := work.replay(candidate)
		if e != nil {
			return false, e
		}
		report.ReplayedTransitions = work.transitions
		outcome := "no_failure"
		cont := true
		switch replayed.kind {
		case "stopped":
			report.Termination = replayed.stop
			outcome = "resource_limit"
			cont = false
		case "invalid":
			outcome = "invalid_causality"
		case "failure":
			if !hasTarget(*replayed.failure, target) {
				outcome = "different_failure"
			} else {
				already, e := known(replayed.failure.Inputs)
				if e != nil {
					return false, e
				}
				if stopped() {
					outcome = "resource_limit"
					cont = false
				} else if already {
					outcome = "already_seen"
				} else {
					copy, e := cloneInputs(g, replayed.failure.Inputs)
					if e != nil {
						return false, e
					}
					if e := countCanAppend[[]I](len(seen)); e != nil {
						return false, e
					}
					seen = append(seen, copy)
					report.Minimized = *replayed.failure
					outcome = "retained"
				}
			}
		}
		if e := countCanAppend[ShrinkAttempt64](len(report.History)); e != nil {
			return false, e
		}
		report.History = append(report.History, ShrinkAttempt64{report.Attempts, length, operation, outcome})
		return cont, nil
	}
	for {
		groups := 2
		for len(report.Minimized.Inputs) > 0 {
			length := len(report.Minimized.Inputs)
			chunk := length / groups
			if length%groups != 0 {
				chunk++
			}
			accepted := false
			for start := 0; start < length; start += min(chunk, length-start) {
				if stopped() {
					return report, nil
				}
				end := start + min(chunk, length-start)
				candidate, e := cloneInputs(g, report.Minimized.Inputs)
				if e != nil {
					return nil, e
				}
				candidate = append(candidate[:start], candidate[end:]...)
				already, e := known(candidate)
				if e != nil {
					return nil, e
				}
				if stopped() {
					return report, nil
				}
				if already {
					continue
				}
				cont, e := attempt(candidate, ShrinkOperation64{Kind: "remove", Start: uint64(start), End: uint64(end)})
				if e != nil {
					return nil, e
				}
				if !cont {
					return report, nil
				}
				if report.History[len(report.History)-1].Outcome == "retained" {
					accepted = true
					groups = max(2, groups-1)
					break
				}
			}
			if accepted {
				continue
			}
			if groups >= length {
				break
			}
			if groups > length/2 {
				groups = length
			} else {
				groups *= 2
			}
		}
		simplified := false
		for index := 0; index < len(report.Minimized.Inputs); index++ {
			if stopped() {
				return report, nil
			}
			if g.SimplerInputs == nil {
				continue
			}
			alternatives, e := call("simpler_inputs", func() (Iterator[I], error) { return g.SimplerInputs(g.CloneInput(report.Minimized.Inputs[index])) })
			if e != nil {
				return nil, e
			}
			if stopped() {
				return report, nil
			}
			// Duplicate hints do not create replay attempts or history
			// entries, but a lazy host iterator can produce them forever.
			// Mirrors Rust shrink_ops: bound consecutive skips per iterator
			// and reset after every replayed hint, so finite runs whose
			// total skips exceed the cap are unaffected.
			var duplicateHints uint64
			for alternative := uint64(0); ; {
				if stopped() {
					return report, nil
				}
				input, more, e := next(alternatives)
				if e != nil {
					return nil, e
				}
				if stopped() {
					return report, nil
				}
				if !more {
					break
				}
				alternativeIndex := alternative
				alternative, e = nextSimplificationIndex(alternative)
				if e != nil {
					return nil, e
				}
				candidate, e := cloneInputs(g, report.Minimized.Inputs)
				if e != nil {
					return nil, e
				}
				candidate[index], e = copied(g.CloneInput, input)
				if e != nil {
					return nil, e
				}
				already, e := known(candidate)
				if e != nil {
					return nil, e
				}
				if stopped() {
					return report, nil
				}
				if already {
					if duplicateHints == config.MaxAttempts {
						report.Termination = "attempt_limit"
						return report, nil
					}
					duplicateHints++
					continue
				}
				duplicateHints = 0
				cont, e := attempt(candidate, ShrinkOperation64{Kind: "simplify", Index: uint64(index), Alternative: alternativeIndex})
				if e != nil {
					return nil, e
				}
				if !cont {
					return report, nil
				}
				if report.History[len(report.History)-1].Outcome == "retained" {
					simplified = true
					break
				}
			}
			if simplified {
				break
			}
		}
		if !simplified {
			return report, nil
		}
	}
}

// Mirrors Rust checked_add before cloning or testing a yielded alternative.
func nextSimplificationIndex(current uint64) (uint64, error) {
	if current == ^uint64(0) {
		return 0, &ModelError{"simplification index overflow"}
	}
	return current + 1, nil
}
