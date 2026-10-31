package reference

import (
	s "github.com/Jagalite/statelessness/go"
	"slices"
)

var mutationTargets = map[string]string{"obsolete-publication": "controller.publication_current", "missing-cleanup": "controller.cleanup_required", "double-settlement": "controller.settlement_once", "dropped-cancellation-ack": "controller.cancellation_ack", "duplicate-completion": "controller.duplicate_inert", "early-release": "controller.release_after_settlement", "skipped-retry-deadline": "controller.retry_deadline", "ignored-mutation": "controller.ignored_inert", "reordered-effects": "controller.effect_order"}

func equalState(a, b State) bool {
	return a.Now == b.Now && slices.Equal(a.Jobs, b.Jobs) && ((a.Current == nil && b.Current == nil) || (a.Current != nil && b.Current != nil && *a.Current == *b.Current))
}
func current(st State, g uint64) bool { return st.Current != nil && *st.Current == g }
func (c Controller) Step(before State, in Input) (s.Transition[State, Effect], error) {
	after, err := Reduce(before, in, c.Bounds)
	if err != nil {
		return after, err
	}
	switch c.Mutation {
	case "":
	case "obsolete-publication":
		j, ok := before.Job(in.Generation)
		if in.Kind == "complete" && in.Outcome == "success" && ok && j.Phase == "cancelling" && !current(before, in.Generation) {
			after.Outputs = append([]Effect{{Kind: "publish", Generation: in.Generation}}, after.Outputs...)
		}
	case "missing-cleanup":
		after.Outputs = slices.DeleteFunc(after.Outputs, func(e Effect) bool { return e.Kind == "release" })
	case "double-settlement":
		for _, e := range after.Outputs {
			if e.Kind == "settle" {
				after.Outputs = append(after.Outputs, e)
				break
			}
		}
	case "dropped-cancellation-ack":
		if in.Kind == "cancelled" && after.Disposition.Kind == "accepted" {
			after.State = before.Clone()
			after.Outputs = nil
		}
	case "duplicate-completion":
		j, ok := before.Job(in.Generation)
		if in.Kind == "complete" && ok && j.Phase == "settled" {
			after.Outputs = append(after.Outputs, Effect{Kind: "settle", Generation: in.Generation, Result: "succeeded"})
			after.Disposition = s.Accepted()
		}
	case "early-release":
		for _, e := range after.Outputs {
			if e.Kind == "cancel" {
				after.Outputs = append(after.Outputs, Effect{Kind: "release", Generation: e.Generation})
				break
			}
		}
	case "skipped-retry-deadline":
		if in.Kind == "tick" {
			for _, e := range after.Outputs {
				if e.Kind == "start" {
					after.State.Jobs = slices.Clone(before.Jobs)
					after.Outputs = nil
					break
				}
			}
		}
	case "ignored-mutation":
		if after.Disposition.Kind == "ignored" && after.State.Now < c.Bounds.Time {
			after.State.Now++
		}
	case "reordered-effects":
		cancel, start := false, false
		for _, e := range after.Outputs {
			cancel = cancel || e.Kind == "cancel"
			start = start || e.Kind == "start"
		}
		if cancel && start {
			slices.Reverse(after.Outputs)
		}
	default:
		return s.Transition[State, Effect]{}, &s.ModelError{Message: "unknown reference-controller mutation"}
	}
	return after, nil
}
func (c Controller) CheckTransition(before State, in Input, after s.Transition[State, Effect]) ([]s.Check, error) {
	if err := validateObservation(before, in, after); err != nil {
		return nil, err
	}
	count := func(w Effect) int {
		n := 0
		for _, e := range after.Outputs {
			if e == w {
				n++
			}
		}
		return n
	}
	publication, success, cleanup, settlement, ack, duplicate, release, retry, ignored, ordering, completionProgress, cancelProgress, submitProgress := true, true, true, true, true, true, true, true, true, true, true, true, true
	old, exists := before.Job(in.Generation)
	for i, e := range after.Outputs {
		switch e.Kind {
		case "publish":
			j, ok := before.Job(e.Generation)
			publication = publication && current(before, e.Generation) && in.Kind == "complete" && in.Generation == e.Generation && in.Outcome == "success" && ok && j.Phase == "running" && j.Attempt == in.Attempt
		case "settle":
			j, ok := before.Job(e.Generation)
			n, has := after.State.Job(e.Generation)
			nsettles := 0
			for _, o := range after.Outputs {
				if o.Kind == "settle" && o.Generation == e.Generation {
					nsettles++
				}
			}
			settlement = settlement && ok && j.Phase != "settled" && has && n.Phase == "settled" && n.Result == e.Result && nsettles == 1
		case "release":
			found := false
			for _, o := range after.Outputs[:i] {
				if o.Kind == "settle" && o.Generation == e.Generation {
					found = true
				}
			}
			j, ok := after.State.Job(e.Generation)
			release = release && found && ok && j.Phase == "settled" && !j.ResourceOwned
		}
	}
	if in.Kind == "complete" && in.Outcome == "success" && exists && old.Phase == "running" && old.Attempt == in.Attempt && current(before, in.Generation) {
		success = count(Effect{Kind: "publish", Generation: in.Generation}) == 1
	}
	for _, j := range after.State.Jobs {
		old, ok := before.Job(j.Generation)
		if j.Phase == "settled" && ok && old.Phase != "settled" {
			cleanup = cleanup && count(Effect{Kind: "release", Generation: j.Generation}) == 1
			settlement = settlement && count(Effect{Kind: "settle", Generation: j.Generation, Result: j.Result}) == 1
		}
	}
	terminal := func(g uint64, result string) bool {
		j, ok := after.State.Job(g)
		return ok && j.Phase == "settled" && j.Result == result && !j.ResourceOwned && !current(after.State, g) && count(Effect{Kind: "settle", Generation: g, Result: result}) == 1 && count(Effect{Kind: "release", Generation: g}) == 1
	}
	cancellation := func(g uint64) bool {
		j, ok := before.Job(g)
		if !ok {
			return true
		}
		switch j.Phase {
		case "running":
			n, ok := after.State.Job(g)
			return ok && n.Phase == "cancelling" && n.Attempt == j.Attempt && n.ResourceOwned && !current(after.State, g) && count(Effect{Kind: "cancel", Generation: g, Attempt: j.Attempt}) == 1
		case "waiting":
			return terminal(g, "cancelled")
		}
		return true
	}
	if in.Kind == "cancelled" && exists && old.Phase == "cancelling" && old.Attempt == in.Attempt {
		j, ok := after.State.Job(in.Generation)
		ack = ok && j.Phase == "settled" && j.Result == "cancelled" && count(Effect{Kind: "settle", Generation: in.Generation, Result: "cancelled"}) == 1
	}
	if in.Kind == "complete" && (!exists || old.Attempt != in.Attempt || (old.Phase != "running" && old.Phase != "cancelling")) {
		duplicate = equalState(before, after.State) && len(after.Outputs) == 0 && after.Disposition.Kind == "ignored"
	}
	if in.Kind == "tick" && in.Now > before.Now && in.Now <= c.Bounds.Time {
		for _, j := range before.Jobs {
			if j.Phase == "waiting" && j.Deadline <= in.Now {
				n, ok := after.State.Job(j.Generation)
				retry = retry && j.Attempt < 255 && ok && n.Attempt == j.Attempt+1 && n.Phase == "running" && count(Effect{Kind: "start", Generation: j.Generation, Attempt: j.Attempt + 1}) == 1
			}
		}
	}
	if after.Disposition.Kind == "ignored" {
		ignored = equalState(before, after.State) && len(after.Outputs) == 0
	}
	if in.Kind == "submit" && before.Current != nil {
		old, ok := before.Job(*before.Current)
		if ok && old.Phase == "running" && len(after.State.Jobs) != len(before.Jobs) {
			ci, si := -1, -1
			for i, e := range after.Outputs {
				if e.Kind == "cancel" && e.Generation == old.Generation {
					ci = i
				}
				if e.Kind == "start" && current(after.State, e.Generation) {
					si = i
				}
			}
			ordering = ci >= 0 && si >= 0 && ci < si
		}
	}
	if in.Kind == "complete" && exists && old.Attempt == in.Attempt {
		switch old.Phase {
		case "cancelling":
			completionProgress = after.Disposition.Kind == "accepted" && terminal(in.Generation, "cancelled")
		case "running":
			progress := false
			if in.Outcome == "success" {
				progress = terminal(in.Generation, "succeeded")
			} else if in.Attempt < c.Bounds.Attempts && before.Now < c.Bounds.Time {
				n, ok := after.State.Job(in.Generation)
				progress = ok && n.Phase == "waiting" && n.Deadline == before.Now+1 && n.Attempt == in.Attempt && n.ResourceOwned && ((before.Current == nil && after.State.Current == nil) || (before.Current != nil && after.State.Current != nil && *before.Current == *after.State.Current))
			} else {
				progress = terminal(in.Generation, "failed")
			}
			completionProgress = after.Disposition.Kind == "accepted" && progress
		}
	}
	if in.Kind == "cancel" && exists && (old.Phase == "running" || old.Phase == "waiting") {
		cancelProgress = after.Disposition.Kind == "accepted" && cancellation(in.Generation)
	}
	if in.Kind == "submit" && uint64(len(before.Jobs)) < c.Bounds.Generations {
		g := uint64(len(before.Jobs)) + 1
		j, ok := after.State.Job(g)
		submitProgress = after.Disposition.Kind == "accepted" && len(after.State.Jobs) == len(before.Jobs)+1 && current(after.State, g) && ok && j.Attempt == 1 && j.Phase == "running" && j.ResourceOwned && count(Effect{Kind: "start", Generation: g, Attempt: 1}) == 1
		if before.Current != nil {
			submitProgress = submitProgress && cancellation(*before.Current)
		}
	}
	ids := []string{"controller.publication_current", "controller.success_published", "controller.cleanup_required", "controller.settlement_once", "controller.cancellation_ack", "controller.duplicate_inert", "controller.release_after_settlement", "controller.retry_deadline", "controller.ignored_inert", "controller.effect_order", "controller.completion_progress", "controller.cancellation_progress", "controller.submission_progress"}
	checks := []bool{publication, success, cleanup, settlement, ack, duplicate, release, retry, ignored, ordering, completionProgress, cancelProgress, submitProgress}
	result := make([]s.Check, len(ids))
	for i, ok := range checks {
		if ok {
			result[i] = s.Passed(ids[i])
		} else {
			result[i] = s.Failed(ids[i], "independent controller obligation was violated")
		}
	}
	return result, nil
}

// Mutations returns the independent, ordered fault catalog as an owned copy.
func Mutations() []string { return slices.Clone(mutationOrder) }

// MutationTarget identifies the independent property a named control must fail.
func MutationTarget(name string) (string, bool) {
	target, ok := mutationTargets[name]
	return target, ok
}
