package debug_test

import (
	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

func TestRecordingChecksOnceAndFailureRetained(t *testing.T) {
	count := 0
	g := model(&count)
	checks := 0
	original := g.CheckState
	g.CheckState = func(x int) ([]s.Check, error) { checks++; return original(x) }
	session, e := d.RecordingSessionFromState("recorded", g.Model, 10, d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits(), s.RunConfig{}, s.DefaultRecorderOptions())
	if e != nil {
		t.Fatal(e)
	}
	if checks != 1 {
		t.Fatal("initial rechecked", checks)
	}
	r, e := session.Step(0, 2)
	if e != nil || !r.Delivered {
		t.Fatal(r, e)
	}
	trace, e := session.ExportTrace()
	if e != nil || checks != 2 || count != 1 || len(trace.Steps) != 1 || trace.Termination != "property_failed" {
		t.Fatal(trace, e, checks, count)
	}
}
func TestVerifiedForkRejectsDivergenceAndSeparatesState(t *testing.T) {
	a := fixture()
	v, e := d.NewEvidenceViewer(a, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	count := 0
	g := model(&count)
	child, e := d.Fork(v, g.Model, 0, "fork", d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits(), s.ShrinkLimits{})
	if e != nil {
		t.Fatal(e)
	}
	r, e := child.Step(0, 1)
	if e != nil || !r.Delivered || child.State() != 11 {
		t.Fatal(r, e)
	}
	b, _ := v.Boundary(0)
	if string(b.After) != "10" {
		t.Fatal("parent changed")
	}
	g.Step = func(x, i int) (s.Transition[int, int], error) { return s.Accept(x+i+1, x+i), nil }
	if _, e = d.Fork(v, g.Model, 1, "bad", d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits(), s.ShrinkLimits{}); e == nil {
		t.Fatal("divergent fork")
	}
}
func TestBranchProvenanceAndFailedBoundary(t *testing.T) {
	count := 0
	g := model(&count)
	a := fixture()
	v, e := d.NewEvidenceViewer(a, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = d.Fork(v, g.Model, 1, "failed", d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits(), s.ShrinkLimits{}); e == nil || count != 0 {
		t.Fatal("failed boundary executed")
	}
	branch, e := d.Fork(v, g.Model, 0, "bound", d.Unrestricted[int, int](), g.InputEqual, d.DefaultSessionLimits(), s.ShrinkLimits{})
	if e != nil {
		t.Fatal(e)
	}
	if e = branch.Provenance.ValidateParent(a.ExactTrace(), s.DefaultBinaryLimits()); e != nil {
		t.Fatal(e)
	}
	a.V2.Trace.InitialState[0] = '9'
	if e = branch.Provenance.ValidateParent(a.ExactTrace(), s.DefaultBinaryLimits()); e == nil {
		t.Fatal("mismatched parent")
	}
	trace, e := branch.ExportTrace()
	if e != nil || trace.Config.Strategy != "debugger-branch" {
		t.Fatal(trace, e)
	}
}
