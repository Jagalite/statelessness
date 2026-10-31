package reference

import (
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"math"
	"math/bits"
)

// Generator samples the same uniformly indexed ordered finite domain as Rust.
func (c Controller) Generator() s.GenerateModel[State, Input, Effect] {
	g := s.GenerateModel[State, Input, Effect]{Model: c.Model()}
	g.InputEqual = func(a, b Input) (bool, error) { return a == b, nil }
	g.IsEnabled = func(st State, in Input) (bool, error) {
		if err := c.Bounds.Validate(); err != nil {
			return false, err
		}
		n := uint64(len(st.Jobs))
		switch in.Kind {
		case "submit":
			return in == (Input{Kind: "submit"}), nil
		case "cancel":
			return in.Generation <= n && in == (Input{Kind: "cancel", Generation: in.Generation}), nil
		case "cancelled":
			return in.Generation <= n && in.Attempt > 0 && in.Attempt <= c.Bounds.Attempts && in == (Input{Kind: "cancelled", Generation: in.Generation, Attempt: in.Attempt}), nil
		case "complete":
			return in.Generation <= n && in.Attempt > 0 && in.Attempt <= c.Bounds.Attempts && (in.Outcome == "success" || in.Outcome == "retryable_failure") && in == (Input{Kind: "complete", Generation: in.Generation, Attempt: in.Attempt, Outcome: in.Outcome}), nil
		case "tick":
			return in.Now <= c.Bounds.Time && in == (Input{Kind: "tick", Now: in.Now}), nil
		}
		return false, nil
	}
	g.Generate = func(st State, rng *s.Rng) (Input, bool, error) {
		if rng == nil {
			return Input{}, false, &s.ConfigError{Message: "nil RNG"}
		}
		if err := c.Bounds.Validate(); err != nil {
			return Input{}, false, err
		}
		n := uint64(len(st.Jobs))
		per := 1 + 3*uint64(c.Bounds.Attempts)
		hi, domain := bits.Mul64(n+1, per)
		if hi != 0 || domain > math.MaxUint64-2 || c.Bounds.Time > math.MaxUint64-domain-2 {
			return Input{}, false, &s.ModelError{Message: "controller input domain size overflow"}
		}
		index, _ := rng.Index(domain + c.Bounds.Time + 2)
		if index == 0 {
			return Input{Kind: "submit"}, true, nil
		}
		index--
		if index >= domain {
			return Input{Kind: "tick", Now: index - domain}, true, nil
		}
		generation, slot := index/per, index%per
		if slot == 0 {
			return Input{Kind: "cancel", Generation: generation}, true, nil
		}
		slot--
		attempt := uint8(slot/3 + 1)
		switch slot % 3 {
		case 0:
			return Input{Kind: "cancelled", Generation: generation, Attempt: attempt}, true, nil
		case 1:
			return Input{Kind: "complete", Generation: generation, Attempt: attempt, Outcome: "success"}, true, nil
		default:
			return Input{Kind: "complete", Generation: generation, Attempt: attempt, Outcome: "retryable_failure"}, true, nil
		}
	}
	return g
}

type MutationResult struct {
	Mutation, Status, Reason, Search string
	Original, Minimized              *s.Failure[Input]
	ShrinkComplete                   bool
}

// EvaluateMutation reports a kill only when the named target survives causal replay.
func EvaluateMutation(name string, config s.SearchConfig) MutationResult {
	result := MutationResult{Mutation: name, Status: "not_evaluated"}
	target, ok := mutationTargets[name]
	if !ok {
		result.Reason = "unknown mutation"
		return result
	}
	c := Controller{Bounds: DefaultBounds(), Mutation: name}
	report, err := s.Enumerate(c.Model(), config)
	if err != nil {
		result.Reason = err.Error()
		return result
	}
	result.Search = report.Termination
	if report.Failure == nil {
		if report.Termination == "graph_exhausted" && report.SkippedChecks == 0 {
			result.Status = "survives"
		} else {
			result.Reason = fmt.Sprintf("incomplete search: %s; skipped=%d", report.Termination, report.SkippedChecks)
		}
		return result
	}
	f := *report.Failure
	f.Inputs = append([]Input(nil), f.Inputs...)
	f.Violations = append([]s.Violation(nil), f.Violations...)
	index := -1
	for i, v := range f.Violations {
		if v.Check.ID == target {
			index = i
			break
		}
	}
	if index < 0 {
		result.Reason = "search found unrelated failure before required target"
		return result
	}
	f.Violations[0], f.Violations[index] = f.Violations[index], f.Violations[0]
	result.Original = &f
	shrink, err := s.Shrink64(c.Generator(), f, s.DefaultShrinkConfig64())
	if err != nil {
		result.Reason = err.Error()
		return result
	}
	if !shrink.ValidatedOriginal {
		result.Reason = "original witness did not validate"
		return result
	}
	found := false
	for _, v := range shrink.Minimized.Violations {
		if v.Check.ID == target {
			found = true
		}
	}
	if !found {
		result.Reason = "target property did not survive causal replay"
		return result
	}
	result.Minimized = &shrink.Minimized
	result.ShrinkComplete = shrink.Termination == "search_complete"
	result.Status = "killed"
	return result
}
