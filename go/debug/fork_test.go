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

func TestForkRequiresUnambiguousEnvironmentProvenance(t *testing.T) {
	const env = "stateless.debug.environment"
	const origin = "stateless.debug.origin"
	policy := d.InputPolicy[int, int]{Label: "allowed", Validate: func(int, int) (bool, error) { return true, nil }}
	cases := []struct {
		name       string
		parameters [][2]string
		policy     d.InputPolicy[int, int]
		wantError  bool
	}{
		{"duplicate environment", [][2]string{{env, "allowed"}, {env, "allowed"}, {origin, "simulation"}}, d.Unrestricted[int, int](), true},
		{"conflicting environment", [][2]string{{env, "allowed"}, {env, "other"}, {origin, "simulation"}}, policy, true},
		{"duplicate origin", [][2]string{{env, "allowed"}, {origin, "simulation"}, {origin, "out-of-domain-injection"}}, d.Unrestricted[int, int](), true},
		{"missing origin", [][2]string{{env, "allowed"}}, d.Unrestricted[int, int](), true},
		{"missing environment", [][2]string{{origin, "simulation"}}, policy, true},
		{"unknown origin", [][2]string{{env, "allowed"}, {origin, "unknown"}}, d.Unrestricted[int, int](), true},
		{"unrestricted parent", [][2]string{{env, "allowed"}, {origin, "out-of-domain-injection"}}, policy, true},
		{"different label", [][2]string{{env, "other"}, {origin, "simulation"}}, policy, true},
		{"matching simulation", [][2]string{{env, "allowed"}, {origin, "simulation"}}, policy, false},
		{"explicit unrestricted", [][2]string{{env, "allowed"}, {origin, "simulation"}}, d.Unrestricted[int, int](), false},
		{"legacy", nil, policy, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			a := fixture()
			a.V2.Trace.Config.Parameters = tc.parameters
			v, err := d.NewEvidenceViewer(a, s.DefaultEvidenceLimits())
			if err != nil {
				t.Fatal(err)
			}
			count := 0
			g := model(&count)
			_, err = d.Fork(v, g.Model, 0, "fork", tc.policy, g.InputEqual, d.DefaultSessionLimits(), s.ShrinkLimits{})
			if (err != nil) != tc.wantError {
				t.Fatalf("error = %v, want error %v", err, tc.wantError)
			}
		})
	}
}
