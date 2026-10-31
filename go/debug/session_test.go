package debug_test

import (
	"errors"
	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

func TestSessionBreakpointCommitAndStale(t *testing.T) {
	count := 0
	g := model(&count)
	session, e := d.SessionFromState("session", g.Model, 10, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	if e != nil {
		t.Fatal(e)
	}
	id, e := session.AddPreBreakpoint(func(int, int) bool { return true })
	if e != nil {
		t.Fatal(e)
	}
	r, e := session.Step(0, 1)
	if e != nil || r.Delivered || r.Stop.Kind != "pre_breakpoint" || session.Revision() != 0 {
		t.Fatal(r, e)
	}
	r, e = session.Step(0, 1)
	if e != nil || !r.Delivered || session.State() != 11 || count != 1 || session.Revision() != 1 {
		t.Fatal(r, e)
	}
	if _, e = session.Step(0, 1); e == nil || count != 1 {
		t.Fatal("stale")
	}
	session.RemoveBreakpoint(id)
	r, e = session.StepWithObserver(1, 1, func(o d.Observation[int, int, int]) error {
		if o.State != 12 || o.Sequence != 2 {
			t.Fatal(o)
		}
		return errors.New("export failed")
	})
	if e != nil || !r.Delivered || r.Stop.Kind != "property_failure" || session.State() != 12 || session.Phase() != d.Ended || len(session.Issues()) != 1 {
		t.Fatal(r, e)
	}
}
func TestSessionPolicyAndCheckerFailure(t *testing.T) {
	count := 0
	g := model(&count)
	g.CheckState = func(x int) ([]s.Check, error) {
		if x > 10 {
			return nil, errors.New("checker fault")
		}
		return []s.Check{s.Passed("safe")}, nil
	}
	policy := d.InputPolicy[int, int]{Label: "one", Candidates: func(int) (s.Iterator[int], error) { return s.Values([]int{1}), nil }}
	session, e := d.SessionFromState("policy", g.Model, 10, policy, g.InputEqual, d.DefaultSessionLimits())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = session.Step(0, 2); e == nil || count != 0 {
		t.Fatal("unpermitted")
	}
	r, e := session.Step(0, 1)
	if e != nil || !r.Delivered || r.Stop.Kind != "checker_error" || session.State() != 11 || session.Observation().ChecksComplete {
		t.Fatal(r, e)
	}
}
func TestCancelNoRevisionAndCandidateAuthority(t *testing.T) {
	count := 0
	g := model(&count)
	g.Inputs = func(int) (s.Iterator[int], error) { return s.Values([]int{1, 2}), nil }
	p := d.InputPolicy[int, int]{Label: "predicate", Validate: func(_ int, i int) (bool, error) { return i == 1, nil }}
	session, e := d.SessionFromState("tokens", g.Model, 10, p, g.InputEqual, d.DefaultSessionLimits())
	if e != nil {
		t.Fatal(e)
	}
	page, e := session.Inputs(0, 1)
	if e != nil || len(page.Candidates) != 1 || !page.Truncated {
		t.Fatal(page, e)
	}
	other, e := d.SessionFromState("foreign", g.Model, 10, p, g.InputEqual, d.DefaultSessionLimits())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = other.Select(page.Candidates[0]); e == nil {
		t.Fatal("foreign")
	}
	if _, e = session.Select(page.Candidates[0]); e != nil {
		t.Fatal(e)
	}
	if _, e = session.Select(page.Candidates[0]); e == nil {
		t.Fatal("stale")
	}
	revision := session.Revision()
	if e = session.Cancel(revision); e != nil || session.Revision() != revision {
		t.Fatal(e)
	}
	if e = session.Cancel(revision); e == nil {
		t.Fatal("ended cancel")
	}
}
func TestRunBoundedRequiresCount(t *testing.T) {
	count := 0
	g := model(&count)
	session, e := d.SessionFromState("run", g.Model, 0, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = session.RunBounded(0, s.Values([]int{1}), 0); e == nil {
		t.Fatal("zero count")
	}
	r, e := session.RunBounded(0, s.Values([]int{1}), 1)
	if e != nil || len(r) != 1 || session.Stop().Kind != "budget_exhausted" || session.Phase() != d.Paused {
		t.Fatal(r, e)
	}
}
func TestCandidateRevalidatesChangedEnumeratedDomainEvenUnrestricted(t *testing.T) {
	count := 0
	g := model(&count)
	domain := []int{1}
	g.Inputs = func(int) (s.Iterator[int], error) { return s.Values(domain), nil }
	session, e := d.SessionFromState("domain", g.Model, 10, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	if e != nil {
		t.Fatal(e)
	}
	page, e := session.InputsPage(0, 0, 1)
	if e != nil || !page.Complete || page.Candidates[0].Input() != 1 || page.Candidates[0].Revision() != 0 {
		t.Fatal(page, e)
	}
	domain = []int{2}
	if _, e = session.Select(page.Candidates[0]); e == nil || count != 0 {
		t.Fatal("old candidate delivered", e, count)
	}
	r, e := session.StepEncoded(0, []byte("1"))
	if e != nil || !r.Delivered || session.State() != 11 {
		t.Fatal(r, e)
	}
}

func TestReadCloneHooksCannotMutateSession(t *testing.T) {
	count := 0
	g := model(&count)
	g.Inputs = func(int) (s.Iterator[int], error) { return s.Values([]int{1}), nil }
	var session *d.Session[int, int, int]
	attempts := 0
	hook := func(x int) int {
		if session != nil {
			attempts++
			if err := session.Cancel(session.Revision()); err == nil {
				t.Fatal("clone callback cancelled session")
			}
			if _, err := session.AddPreBreakpoint(func(int, int) bool { return true }); err == nil {
				t.Fatal("clone callback changed breakpoints")
			}
		}
		return x
	}
	g.CloneState = hook
	g.CloneInput = hook
	g.CloneOutput = hook
	var err error
	session, err = d.SessionFromState("clone-owner", g.Model, 0, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	if session.State() != 0 || session.Observation().State != 0 {
		t.Fatal("state projection")
	}
	page, err := session.Inputs(0, 1)
	if err != nil || len(page.Candidates) != 1 {
		t.Fatal(page, err)
	}
	if page.Candidates[0].Input() != 1 {
		t.Fatal("input projection")
	}
	result, err := session.Select(page.Candidates[0])
	if err != nil || !result.Delivered || count != 1 || attempts == 0 {
		t.Fatal(result, err, count, attempts)
	}
	// Observation clones the retained turn, including state, input and outputs.
	_ = session.Observation()
	if session.Phase() == d.Ended {
		t.Fatal("projection ended session")
	}
}

func TestSessionRejectsInvalidUTF8Identity(t *testing.T) {
	count := 0
	g := model(&count)
	if _, err := d.SessionFromState(string([]byte{0xff}), g.Model, 0, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits()); err == nil {
		t.Fatal("invalid identity accepted")
	}
	policy := d.Unrestricted[int, int]()
	policy.Label = string([]byte{0xff})
	if _, err := d.SessionFromState("identity", g.Model, 0, policy, g.InputEqual, d.DefaultSessionLimits()); err == nil {
		t.Fatal("invalid policy identity accepted")
	}
}
