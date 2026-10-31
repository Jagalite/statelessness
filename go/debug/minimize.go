package debug

import (
	"fmt"
	s "github.com/Jagalite/statelessness/go"
)

// InputPolicy declares environmental admission separately from Generate causality.
// Labels preserve provenance; they do not authenticate callback implementation.
type InputPolicy[S, I any] struct {
	Label        string
	Unrestricted bool
	Candidates   func(S) (s.Iterator[I], error)
	Validate     func(S, I) (bool, error)
}

func Unrestricted[S, I any]() InputPolicy[S, I] {
	return InputPolicy[S, I]{Label: "out-of-domain-injection", Unrestricted: true}
}

// branchParentPolicy treats a live capture, which records its origin without
// a simulation input policy, as constraining no branch. Minimization still
// requires full policy provenance.
func branchParentPolicy(t *s.AuditTrace) (string, bool, bool, error) {
	origins, environment := 0, false
	live := false
	for _, p := range t.Config.Parameters {
		switch p[0] {
		case "stateless.debug.environment":
			environment = true
		case "stateless.debug.origin":
			origins++
			live = p[1] == "recorded-live-observation"
		}
	}
	if !environment && origins == 1 && live {
		return "", false, false, nil
	}
	return recordedEnvironment(t)
}

func recordedEnvironment(t *s.AuditTrace) (string, bool, bool, error) {
	var env, origin *string
	for _, entry := range t.Config.Parameters {
		switch entry[0] {
		case "stateless.debug.environment":
			if env != nil {
				return "", false, false, fmt.Errorf("ambiguous recorded environmental policy")
			}
			x := entry[1]
			env = &x
		case "stateless.debug.origin":
			if origin != nil {
				return "", false, false, fmt.Errorf("ambiguous recorded environmental policy")
			}
			x := entry[1]
			origin = &x
		}
	}
	if env == nil && origin == nil {
		return "", false, false, nil
	}
	if env == nil || origin == nil {
		return "", false, false, fmt.Errorf("incomplete recorded environmental policy provenance")
	}
	unrestricted := *origin == "out-of-domain-injection"
	if !unrestricted && *origin != "simulation" {
		return "", false, false, fmt.Errorf("unknown recorded environmental policy origin")
	}
	return *env, unrestricted, true, nil
}
func policyProvenance[S, I any](t *s.AuditTrace, p *InputPolicy[S, I]) error {
	env, unrestricted, present, err := recordedEnvironment(t)
	if err != nil || !present {
		return err
	}
	if p == nil {
		if unrestricted && env == "out-of-domain-injection" {
			return nil
		}
		return fmt.Errorf("recorded environmental policy requires MinimizeWithPolicy")
	}
	if p.Label != env || p.Unrestricted != unrestricted {
		return fmt.Errorf("minimization environment differs from recorded policy")
	}
	return nil
}
func Minimize[S, I, O any](v *EvidenceViewer, g s.GenerateModel[S, I, O], sequence, check uint64, config s.ShrinkConfig, limits s.ShrinkLimits) (*s.ShrinkReport[I], error) {
	return minimize(v, g, sequence, check, nil, 0, config, limits, nil)
}
func MinimizeWithPolicy[S, I, O any](v *EvidenceViewer, g s.GenerateModel[S, I, O], sequence, check uint64, p InputPolicy[S, I], maximum uint64, config s.ShrinkConfig, limits s.ShrinkLimits) (*s.ShrinkReport[I], error) {
	if maximum == 0 || (!p.Unrestricted && p.Candidates == nil && p.Validate == nil) {
		return nil, fmt.Errorf("policy requires a positive bounded admission scan and callback")
	}
	return minimize(v, g, sequence, check, &p, maximum, config, limits, nil)
}
func minimize[S, I, O any](v *EvidenceViewer, g s.GenerateModel[S, I, O], sequence, check uint64, p *InputPolicy[S, I], maximum uint64, config s.ShrinkConfig, limits s.ShrinkLimits, explicitPhase *s.CheckPhase) (*s.ShrinkReport[I], error) {
	limits = snapshotLimits(limits)
	deadline, err := s.NewRunDeadline(limits.Control)
	if err != nil {
		return nil, err
	}
	if g.Codec != nil {
		codec := *g.Codec
		g.Codec = &codec
	}
	target, e := v.Failure(sequence, check)
	if explicitPhase != nil {
		b, ok := v.Boundary(sequence)
		if !ok || check >= uint64(len(b.Checks)) || b.Checks[check].Status != "failed" {
			return nil, fmt.Errorf("unknown or passing selected check")
		}
		phase := *explicitPhase
		if sequence == 0 && phase != s.PhaseInitialState || sequence > 0 && phase != s.PhaseState && phase != s.PhaseTransition {
			return nil, fmt.Errorf("invalid selected failure phase")
		}
		if persisted, known := v.artifact.CheckPhase(sequence, check); known && persisted != phase {
			return nil, fmt.Errorf("selected phase contradicts persisted evidence")
		}
		target = PropertyFailure{phase, b.Checks[check]}
		e = nil
	}
	if e != nil {
		return nil, e
	}
	t := v.artifact.ExactTrace()
	if e = policyProvenance(t, p); e != nil {
		return nil, e
	}
	if g.Codec == nil || g.Codec.DecodeState == nil || g.Codec.DecodeInput == nil || g.InputEqual == nil || g.CloneState == nil || g.CloneInput == nil {
		return nil, fmt.Errorf("minimization requires codec and generation equality/copy hooks")
	}
	// Verification and candidate shrinking share a single overall wall-time and
	// transition budget, including persisted v2 phase validation on every turn.
	stop := func() error {
		switch deadline.Stop() {
		case "cancelled":
			return fmt.Errorf("minimization cancelled")
		case "deadline":
			return fmt.Errorf("minimization deadline")
		}
		return nil
	}
	if v.artifact.V2 == nil && config.MaxAttempts <= 0 {
		return nil, fmt.Errorf("minimization max attempts must be positive")
	}
	if v.artifact.V2 == nil && limits.MaxReplayedTransitions != nil && len(t.Steps) > 0 && uint64(len(t.Steps)) >= *limits.MaxReplayedTransitions {
		return nil, fmt.Errorf("budget cannot cover verification and original validation")
	}
	limits.Control = deadline.Remaining()
	verified, e := Verify(v, g.Model, limits)
	if e != nil {
		return nil, e
	}
	if verified.Report.Outcome != "exact" || !verified.Report.FailureReproduced {
		return nil, fmt.Errorf("exact failure reproduction required")
	}
	if limits.MaxReplayedTransitions != nil {
		remaining := *limits.MaxReplayedTransitions - verified.Transitions
		limits.MaxReplayedTransitions = &remaining
	}
	if err := stop(); err != nil {
		return nil, err
	}
	limits.Control = deadline.Remaining()
	// The Rust v2 viewer performs a separate phase pass before the workbench's
	// exact verification. Preserve its extra work and charge both passes.
	if v.artifact.V2 != nil {
		if config.MaxAttempts <= 0 {
			return nil, fmt.Errorf("minimization max attempts must be positive")
		}
		if limits.MaxReplayedTransitions != nil && len(t.Steps) > 0 && uint64(len(t.Steps)) >= *limits.MaxReplayedTransitions {
			return nil, fmt.Errorf("budget cannot cover verification and original validation")
		}
		second, err := Verify(v, g.Model, limits)
		if err != nil {
			return nil, err
		}
		if second.Report.Outcome != "exact" || !second.Report.FailureReproduced {
			return nil, fmt.Errorf("exact failure reproduction required")
		}
		verified.Transitions += second.Transitions
		if limits.MaxReplayedTransitions != nil {
			remaining := *limits.MaxReplayedTransitions - second.Transitions
			limits.MaxReplayedTransitions = &remaining
		}
		if err := stop(); err != nil {
			return nil, err
		}
		limits.Control = deadline.Remaining()
	}
	if limits.MaxReplayedTransitions != nil && sequence > 0 && *limits.MaxReplayedTransitions == 0 {
		return nil, fmt.Errorf("budget cannot cover verification and original validation")
	}
	// Decode a fresh checkpoint for every candidate. Never call the application's
	// original initializer and never alias one candidate's mutable state to another.
	original := g.Model
	g.InitialState = func() (S, error) {
		var zero S
		if e := stop(); e != nil {
			return zero, e
		}
		state, e := original.Codec.DecodeState(append([]byte{}, t.InitialState...))
		if e != nil {
			return zero, e
		}
		if e = stop(); e != nil {
			return zero, e
		}
		return state, nil
	}
	enabled := g.IsEnabled
	policyAllowed := func(state S, query inputSnapshot[I]) (bool, error) {
		if p == nil || p.Unrestricted {
			return true, nil
		}
		if p.Validate != nil {
			return p.Validate(original.CloneState(state), original.CloneInput(query.get()))
		}
		it, e := p.Candidates(original.CloneState(state))
		if e != nil {
			return false, e
		}
		if it == nil {
			return false, fmt.Errorf("nil policy iterator")
		}
		for n := uint64(0); ; n++ {
			if e := stop(); e != nil {
				return false, e
			}
			candidate, ok, e := it()
			if e != nil {
				return false, e
			}
			if !ok {
				return false, nil
			}
			if n == maximum {
				return false, fmt.Errorf("policy admission candidate limit")
			}
			same, e := g.InputEqual(original.CloneInput(candidate), original.CloneInput(query.get()))
			if e != nil {
				return false, e
			}
			if same {
				return true, nil
			}
		}
	}
	g.IsEnabled = func(state S, input I) (bool, error) {
		if e := stop(); e != nil {
			return false, e
		}
		if (p == nil || p.Unrestricted) && enabled == nil {
			return true, nil
		}
		// Both policy callbacks and the enabled callback observe the same input,
		// even if one changes an externally aliased caller value.
		query := captureInput(original.CloneInput, input)
		ok, e := policyAllowed(state, query)
		if e != nil || !ok {
			return ok, e
		}
		if enabled != nil {
			enabledInput := original.CloneInput(query.get())
			return enabled(original.CloneState(state), enabledInput)
		}
		return true, nil
	}
	inputs := make([]I, 0, int(sequence))
	for _, step := range t.Steps[:sequence] {
		if e = stop(); e != nil {
			return nil, e
		}
		input, e := g.Codec.DecodeInput(append([]byte{}, step.Input...))
		if e != nil {
			return nil, e
		}
		inputs = append(inputs, g.CloneInput(input))
	}

	if err := stop(); err != nil {
		return nil, err
	}
	limits.Control = deadline.Remaining()
	report, e := s.ShrinkWithLimits(g, s.Failure[I]{Inputs: inputs, Violations: []s.Violation{{Phase: string(target.Phase), Check: target.Check}}}, config, limits)
	if report != nil {
		report.ReplayedTransitions += verified.Transitions
	}
	return report, e
}

// MinimizeWithPhase is the explicit legacy-v1 workbench entry point. Passive
// Failure still reports unknown noninitial v1 phases; this supplied phase is an
// application assertion whose actual reproduction must survive candidate checks.
func MinimizeWithPhase[S, I, O any](v *EvidenceViewer, g s.GenerateModel[S, I, O], sequence, check uint64, phase s.CheckPhase, config s.ShrinkConfig, limits s.ShrinkLimits) (*s.ShrinkReport[I], error) {
	return minimize(v, g, sequence, check, nil, 0, config, limits, &phase)
}
func MinimizeWithPhaseAndPolicy[S, I, O any](v *EvidenceViewer, g s.GenerateModel[S, I, O], sequence, check uint64, phase s.CheckPhase, p InputPolicy[S, I], maximum uint64, config s.ShrinkConfig, limits s.ShrinkLimits) (*s.ShrinkReport[I], error) {
	if maximum == 0 || !p.Unrestricted && p.Candidates == nil && p.Validate == nil {
		return nil, fmt.Errorf("positive policy admission budget and callback required")
	}
	return minimize(v, g, sequence, check, &p, maximum, config, limits, &phase)
}
