package reference

import (
	"reflect"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

func TestControllerCancellationRetryAndDuplicateFences(t *testing.T) {
	b := DefaultBounds()
	st := State{}
	apply := func(in Input) []Effect {
		t.Helper()
		r, err := Reduce(st, in, b)
		if err != nil {
			t.Fatal(err)
		}
		st = r.State
		return r.Outputs
	}
	if got := apply(Input{Kind: "submit"}); !reflect.DeepEqual(got, []Effect{{Kind: "start", Generation: 1, Attempt: 1}}) {
		t.Fatal(got)
	}
	if got := apply(Input{Kind: "complete", Generation: 1, Attempt: 1, Outcome: "retryable_failure"}); len(got) != 0 || st.Jobs[0].Phase != "waiting" || st.Jobs[0].Deadline != 1 {
		t.Fatal(st, got)
	}
	if got := apply(Input{Kind: "tick", Now: 1}); !reflect.DeepEqual(got, []Effect{{Kind: "start", Generation: 1, Attempt: 2}}) {
		t.Fatal(got)
	}
	if got := apply(Input{Kind: "submit"}); !reflect.DeepEqual(got, []Effect{{Kind: "cancel", Generation: 1, Attempt: 2}, {Kind: "start", Generation: 2, Attempt: 1}}) {
		t.Fatal(got)
	}
	got := apply(Input{Kind: "complete", Generation: 1, Attempt: 2, Outcome: "success"})
	if !reflect.DeepEqual(got, []Effect{{Kind: "settle", Generation: 1, Result: "cancelled"}, {Kind: "release", Generation: 1}}) {
		t.Fatal(got)
	}
	before := st.Clone()
	r, err := Reduce(st, Input{Kind: "complete", Generation: 1, Attempt: 2, Outcome: "success"}, b)
	if err != nil || r.Disposition.Kind != "ignored" || !reflect.DeepEqual(before, r.State) || len(r.Outputs) != 0 {
		t.Fatal(r, err)
	}
	got = apply(Input{Kind: "complete", Generation: 2, Attempt: 1, Outcome: "success"})
	if !reflect.DeepEqual(got, []Effect{{Kind: "publish", Generation: 2}, {Kind: "settle", Generation: 2, Result: "succeeded"}, {Kind: "release", Generation: 2}}) || st.Pending() != 0 {
		t.Fatal(got, st)
	}
}
func TestInputOrderingAndStateOwnership(t *testing.T) {
	c := Controller{Bounds: DefaultBounds()}
	if !c.Model().EqualStates(State{}, State{Jobs: []Job{}}) {
		t.Fatal("empty job representations differ")
	}
	checks, err := c.CheckState(State{Jobs: []Job{{Generation: 1, Attempt: 1, Phase: "invalid", ResourceOwned: true}}})
	if err != nil || checks[0].Status != "failed" {
		t.Fatal("unknown phase accepted")
	}
	it, err := c.Inputs(State{})
	if err != nil {
		t.Fatal(err)
	}
	var got []Input
	for {
		v, ok, err := it()
		if err != nil {
			t.Fatal(err)
		}
		if !ok {
			break
		}
		got = append(got, v)
	}
	want := []Input{{Kind: "submit"}, {Kind: "cancel", Generation: 0}, {Kind: "cancelled", Generation: 0, Attempt: 1}, {Kind: "complete", Generation: 0, Attempt: 1, Outcome: "success"}, {Kind: "complete", Generation: 0, Attempt: 1, Outcome: "retryable_failure"}, {Kind: "cancelled", Generation: 0, Attempt: 2}, {Kind: "complete", Generation: 0, Attempt: 2, Outcome: "success"}, {Kind: "complete", Generation: 0, Attempt: 2, Outcome: "retryable_failure"}, {Kind: "tick", Now: 0}, {Kind: "tick", Now: 1}, {Kind: "tick", Now: 2}}
	if !reflect.DeepEqual(got, want) {
		t.Fatal(got)
	}
	original := State{Jobs: []Job{{Generation: 1, Attempt: 1, Phase: "running", ResourceOwned: true}}}
	g := uint64(1)
	original.Current = &g
	next, err := Reduce(original, Input{Kind: "cancel", Generation: 1}, c.Bounds)
	if err != nil {
		t.Fatal(err)
	}
	next.State.Jobs[0].Attempt = 9
	if original.Jobs[0].Attempt != 1 || *original.Current != 1 {
		t.Fatal("mutated source state")
	}
}

func TestReferenceControllerExhaustsAndFaultsHitNamedObligations(t *testing.T) {
	base := Controller{Bounds: DefaultBounds()}
	config := s.DefaultSearchConfig()
	report, err := s.Enumerate(base.Model(), config)
	if err != nil || report.Termination != "graph_exhausted" {
		t.Fatalf("base controller: %+v %v", report, err)
	}
	for name, target := range mutationTargets {
		t.Run(name, func(t *testing.T) {
			c := Controller{Bounds: DefaultBounds(), Mutation: name}
			report, err := s.Enumerate(c.Model(), config)
			if err != nil || report.Failure == nil {
				t.Fatalf("fault not killed: %+v %v", report, err)
			}
			found := false
			for _, violation := range report.Failure.Violations {
				if violation.Check.ID == target {
					found = true
				}
			}
			if !found {
				t.Fatalf("target %s missing: %+v", target, report.Failure.Violations)
			}
		})
	}
}

func TestRestoredStateRejectsNoncanonicalPhasePayloads(t *testing.T) {
	c := Controller{Bounds: DefaultBounds()}
	for _, job := range []Job{{Generation: 1, Attempt: 1, Phase: "running", ResourceOwned: true, Deadline: 1}, {Generation: 1, Attempt: 1, Phase: "cancelling", ResourceOwned: true, Result: "failed"}, {Generation: 1, Attempt: 1, Phase: "waiting", ResourceOwned: true, Deadline: 1, Result: "failed"}, {Generation: 1, Attempt: 1, Phase: "settled", Result: ""}, {Generation: 1, Attempt: 1, Phase: "settled", Result: "invalid"}, {Generation: 1, Attempt: 1, Phase: "settled", Result: "failed", Deadline: 1}} {
		checks, err := c.CheckState(State{Jobs: []Job{job}})
		if err != nil || checks[0].Status != "failed" {
			t.Fatalf("invalid phase payload accepted: %+v", job)
		}
	}
	base, _ := c.Model().Codec.Metadata()
	c.Mutation = "missing-cleanup"
	mutant, _ := c.Model().Codec.Metadata()
	if base == mutant {
		t.Fatal("mutant shares executable identity")
	}
}

func TestMalformedCompletionAndBoundIdentity(t *testing.T) {
	c := Controller{Bounds: DefaultBounds()}
	in := Input{Kind: "complete", Outcome: "unknown"}
	if _, err := c.Step(State{}, in); err == nil {
		t.Fatal("invalid completion outcome accepted")
	}
	if _, err := c.CheckTransition(State{}, in, s.Transition[State, Effect]{}); err == nil {
		t.Fatal("invalid completion checked as legitimate")
	}
	base, _ := c.Model().Codec.Metadata()
	c.Bounds.Time++
	changed, _ := c.Model().Codec.Metadata()
	if base == changed {
		t.Fatal("different bounds share identity")
	}
}

func TestInputTaggedUnionRejectsMalformedPayloadsButKeepsStaleInputs(t *testing.T) {
	c := Controller{Bounds: DefaultBounds()}
	invalid := []Input{{Kind: "submit", Generation: 1}, {Kind: "cancel", Attempt: 1}, {Kind: "complete", Outcome: "success", Now: 1}, {Kind: "complete", Outcome: "bogus"}, {Kind: "cancelled", Outcome: "success"}, {Kind: "tick", Generation: 1}, {Kind: "unknown"}}
	for _, in := range invalid {
		if _, err := c.Step(State{}, in); err == nil {
			t.Fatalf("step accepted malformed input %+v", in)
		}
		if _, err := c.CheckTransition(State{}, in, s.Transition[State, Effect]{}); err == nil {
			t.Fatalf("checks accepted malformed input %+v", in)
		}
		if _, err := Coverage(State{}, in, s.Transition[State, Effect]{}, nil); err == nil {
			t.Fatalf("coverage accepted malformed input %+v", in)
		}
		if _, err := c.EncodeInput(in); err == nil {
			t.Fatalf("codec accepted malformed input %+v", in)
		}
	}
	stale := Input{Kind: "complete", Generation: 999, Attempt: 0, Outcome: "success"}
	result, err := c.Step(State{}, stale)
	if err != nil || result.Disposition.Kind != "ignored" {
		t.Fatalf("stale input not retained: %+v %v", result, err)
	}
}

func TestObservedTransitionRejectsMalformedUnionWithoutErasingPropertyFailures(t *testing.T) {
	c := Controller{Bounds: DefaultBounds()}
	in := Input{Kind: "tick", Now: 1}
	base := State{}
	invalid := []s.Transition[State, Effect]{
		{State: base, Disposition: s.Disposition{Kind: "unknown"}},
		{State: base, Disposition: s.Accepted(), Outputs: []Effect{{Kind: "start", Generation: 1, Attempt: 1, Result: "failed"}}},
		{State: base, Disposition: s.Accepted(), Outputs: []Effect{{Kind: "release", Generation: 1, Attempt: 1}}},
		{State: State{Jobs: []Job{{Generation: 1, Attempt: 1, Phase: "settled", Result: "invalid"}}}, Disposition: s.Accepted()},
	}
	for _, after := range invalid {
		if _, err := c.CheckTransition(base, in, after); err == nil {
			t.Fatalf("malformed observation checked: %+v", after)
		}
		if _, err := Coverage(base, in, after, nil); err == nil {
			t.Fatalf("malformed observation credited: %+v", after)
		}
	}
	// A structurally valid but unsafe resource state remains observable evidence.
	unsafe := State{Jobs: []Job{{Generation: 1, Attempt: 1, Phase: "running", ResourceOwned: false}}}
	checks, err := c.CheckState(unsafe)
	if err != nil || checks[0].Status != "failed" {
		t.Fatal("unsafe state erased")
	}
	if _, err = c.CheckTransition(unsafe, in, s.Transition[State, Effect]{State: unsafe, Disposition: s.Accepted()}); err != nil {
		t.Fatal("structurally valid bug rejected", err)
	}
}
