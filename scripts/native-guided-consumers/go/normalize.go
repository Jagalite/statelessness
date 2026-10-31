package main

import (
	"fmt"
	"strconv"
	"strings"

	sl "github.com/Jagalite/statelessness/go"
)

type wireDisposition struct {
	Kind   string  `json:"kind"`
	Reason *string `json:"reason"`
}

func (d *wireDisposition) native() (sl.Disposition, error) {
	if d == nil {
		return sl.Accepted(), nil
	}
	switch d.Kind {
	case "Accepted":
		if d.Reason == nil {
			return sl.Accepted(), nil
		}
	case "Rejected":
		if d.Reason != nil {
			return sl.Rejected(*d.Reason), nil
		}
	case "Ignored":
		if d.Reason != nil {
			return sl.Ignored(*d.Reason), nil
		}
	}
	return sl.Disposition{}, fmt.Errorf("invalid disposition kind/reason for %q", d.Kind)
}

func normalizeDisposition(d sl.Disposition) wireDisposition {
	var reason *string
	if d.Kind != "accepted" {
		value := d.Reason
		reason = &value
	}
	return wireDisposition{Kind: pascal(d.Kind), Reason: reason}
}

type wireCheck struct {
	ID      string  `json:"id"`
	Status  string  `json:"status"`
	Message *string `json:"message"`
}

func (c wireCheck) native() (sl.Check, error) {
	switch c.Status {
	case "Passed":
		if c.Message == nil {
			return sl.Passed(c.ID), nil
		}
	case "Failed":
		if c.Message != nil {
			return sl.Failed(c.ID, *c.Message), nil
		}
	case "Skipped":
		if c.Message != nil {
			return sl.Skipped(c.ID, *c.Message), nil
		}
	}
	return sl.Check{}, fmt.Errorf("invalid check status/message for %q", c.ID)
}

func normalizeCheck(c sl.Check) wireCheck {
	var message *string
	if c.Status != "passed" {
		value := c.Details
		message = &value
	}
	return wireCheck{c.ID, pascal(c.Status), message}
}

func normalizeChecks(checks []sl.Check) []wireCheck {
	result := make([]wireCheck, len(checks))
	for i, check := range checks {
		result[i] = normalizeCheck(check)
	}
	return result
}

func nonnil[T any](values []T) []T { return append([]T{}, values...) }

// Public Go enums use snake_case; the shared wire contract uses Rust's names.
// Unknown values remain visible rather than being replaced by a passing value.
func pascal(value string) string {
	parts := strings.Split(value, "_")
	for i, part := range parts {
		if len(part) > 0 {
			parts[i] = strings.ToUpper(part[:1]) + part[1:]
		}
	}
	return strings.Join(parts, "")
}

type wireViolation struct {
	Phase string    `json:"phase"`
	Check wireCheck `json:"check"`
}

type wireFailure struct {
	Inputs     []int64         `json:"inputs"`
	Violations []wireViolation `json:"violations"`
}

func normalizeFailure(failure *sl.Failure[int64]) *wireFailure {
	if failure == nil {
		return nil
	}
	result := &wireFailure{Inputs: nonnil(failure.Inputs), Violations: make([]wireViolation, len(failure.Violations))}
	for i, violation := range failure.Violations {
		result.Violations[i] = wireViolation{pascal(violation.Phase), normalizeCheck(violation.Check)}
	}
	return result
}

type wireReport struct {
	Termination   string                    `json:"termination"`
	Seed          string                    `json:"seed"`
	Cases         int                       `json:"cases"`
	Transitions   uint64                    `json:"transitions"`
	SkippedChecks uint64                    `json:"skipped_checks"`
	Failure       *wireFailure              `json:"failure"`
	Guidance      *sl.GuidanceReport[int64] `json:"guidance"`
}

func normalizeReport(report *sl.FuzzReport[int64]) *wireReport {
	var guidance *sl.GuidanceReport[int64]
	if report.Guidance != nil {
		copy := *report.Guidance
		copy.Corpus = make([]sl.CorpusEntry[int64], len(report.Guidance.Corpus))
		for i, entry := range report.Guidance.Corpus {
			copy.Corpus[i] = entry
			copy.Corpus[i].Inputs = nonnil(entry.Inputs)
		}
		guidance = &copy
	}
	return &wireReport{pascal(report.Termination), strconv.FormatUint(report.Seed, 10), report.Cases,
		report.Transitions, report.SkippedChecks, normalizeFailure(report.Failure), guidance}
}

// Only documented language-level context spelling is normalized. Error text
// from callbacks is never rewritten, and no expected field is consulted.
func (f *fixture) normalizeError(err error) string {
	message := err.Error()
	for _, context := range [][2]string{
		{"initial state: ", "initial_state: "},
		{"transition: ", "step: "},
		{"transition check: ", "check_transition: "},
	} {
		if strings.HasPrefix(message, context[0]) {
			return context[1] + strings.TrimPrefix(message, context[0])
		}
	}
	if strings.HasPrefix(message, "state check: ") {
		context := "check_state: "
		if f.lastCheckPhase == "InitialState" {
			context = "initial check_state: "
		}
		return context + strings.TrimPrefix(message, "state check: ")
	}
	if message == "invalid fuzz configuration" && f.spec.Config.Cases >= 0 && f.spec.Config.MaxSteps >= 0 &&
		(f.spec.Config.MutationPercent < 0 || f.spec.Config.MutationPercent > 100) {
		return "mutation_percent must be in 0..=100"
	}
	return message
}

type wireAttempt struct {
	Attempt         int            `json:"attempt"`
	CandidateLength int            `json:"candidate_length"`
	Operation       map[string]any `json:"operation"`
	Outcome         string         `json:"outcome"`
}

type wireShrink struct {
	Original            *wireFailure  `json:"original"`
	Minimized           *wireFailure  `json:"minimized"`
	Attempts            int           `json:"attempts"`
	Termination         string        `json:"termination"`
	History             []wireAttempt `json:"history"`
	ReplayedTransitions uint64        `json:"replayed_transitions"`
	ValidatedOriginal   bool          `json:"validated_original"`
}

func normalizeShrink(report *sl.ShrinkReport[int64]) (*wireShrink, error) {
	result := &wireShrink{
		Original: normalizeFailure(&report.Original), Minimized: normalizeFailure(&report.Minimized),
		Attempts: report.Attempts, Termination: pascal(report.Termination), History: make([]wireAttempt, len(report.History)),
		ReplayedTransitions: report.ReplayedTransitions, ValidatedOriginal: report.ValidatedOriginal,
	}
	for i, attempt := range report.History {
		op := attempt.Operation
		operation := map[string]any{"kind": pascal(op.Kind)}
		switch op.Kind {
		case "validate_original":
		case "remove":
			operation["start"], operation["end"] = op.Start, op.End
		case "simplify":
			operation["index"], operation["alternative"] = op.Index, op.Alternative
		default:
			return nil, fmt.Errorf("unknown public shrink operation %q", op.Kind)
		}
		result.History[i] = wireAttempt{attempt.Attempt, attempt.CandidateLength, operation, pascal(attempt.Outcome)}
	}
	return result, nil
}
