package debug_test

import (
	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

// Expectations are authored manually, independently of Rust execution.
func fixture() s.Artifact {
	return s.Artifact{V2: &s.TraceV2{Trace: s.AuditTrace{Trace: s.Trace{Metadata: s.Identity("debug-fixture", "manual"), InitialState: []byte("10"), InitialChecks: []s.Check{s.Passed("safe")}, Steps: []s.TraceStep{{Input: []byte("2"), Disposition: s.Accepted(), PostState: []byte("12"), Outputs: [][]byte{[]byte("12")}, Checks: []s.Check{s.Failed("safe", "limit"), s.Passed("edge")}}}, Termination: "property_failed"}}, CheckpointAfter: 9007199254740993, Provenance: s.Provenance{Run: "manual", Epoch: 18446744073709551615}, Steps: []s.StepEvidence{{Sequence: 9007199254740994, StateCheckCount: 1}}}}
}
func model(counter *int) s.GenerateModel[int, int, int] {
	enc := func(x int) ([]byte, error) { return []byte(strconv.Itoa(x)), nil }
	dec := func(x []byte) (int, error) { return strconv.Atoi(string(x)) }
	return s.GenerateModel[int, int, int]{Model: s.Model[int, int, int]{InitialState: func() (int, error) { panic("must restore checkpoint") }, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[int], CloneOutput: s.ValueCopy[int], Step: func(x, i int) (s.Transition[int, int], error) { *counter++; return s.Accept(x+i, x+i), nil }, CheckState: func(x int) ([]s.Check, error) {
		if x >= 12 {
			return []s.Check{s.Failed("safe", "limit")}, nil
		}
		return []s.Check{s.Passed("safe")}, nil
	}, CheckTransition: func(int, int, s.Transition[int, int]) ([]s.Check, error) { return []s.Check{s.Passed("edge")}, nil }, Codec: &s.Codec[int, int, int]{Metadata: func() (s.Metadata, error) { return s.Identity("debug-fixture", "manual"), nil }, EncodeState: enc, DecodeState: dec, EncodeInput: enc, DecodeInput: dec, EncodeOutput: enc}}, InputEqual: func(a, b int) (bool, error) { return a == b, nil }}
}
func TestPassiveAndOwned(t *testing.T) {
	a := fixture()
	v, e := d.NewEvidenceViewer(a, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	a.V2.Trace.InitialState[0] = '9'
	b, ok := v.Boundary(1)
	if !ok || string(b.Before) != "10" || string(b.After) != "12" || b.HostSequence != 9007199254740994 {
		t.Fatalf("%+v", b)
	}
	b.Before[0] = '8'
	again, _ := v.Boundary(0)
	if string(again.After) != "10" {
		t.Fatal("alias")
	}
	f, e := v.Failure(1, 0)
	if e != nil || f.Phase != s.PhaseState {
		t.Fatal(f, e)
	}
	if _, e = v.Failure(1, 1); e == nil {
		t.Fatal("passed selected")
	}
	if _, e = v.Failure(2, 0); e == nil {
		t.Fatal("missing selected")
	}
	p := d.Preview(b.After, 1)
	if string(p.Bytes) != "1" || p.TotalBytes != 2 || p.Complete {
		t.Fatal(p)
	}
}
func TestLegacySemanticValidationAndUnknownPhase(t *testing.T) {
	a := fixture()
	legacy := s.Artifact{V1: &a.V2.Trace}
	v, e := d.NewEvidenceViewer(legacy, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = v.Failure(1, 0); e == nil || !strings.Contains(e.Error(), "unknown") {
		t.Fatal(e)
	}
	legacy.V1.Steps = append(legacy.V1.Steps, legacy.V1.Steps[0])
	if _, e = d.NewEvidenceViewer(legacy, s.DefaultEvidenceLimits()); e == nil {
		t.Fatal("continuation accepted")
	}
}
func TestVerificationAndMinimization(t *testing.T) {
	v, e := d.NewEvidenceViewer(fixture(), s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	count := 0
	g := model(&count)
	max := uint64(1)
	r, e := d.Verify(v, g.Model, s.ShrinkLimits{MaxReplayedTransitions: &max})
	if e != nil || r.Report.Outcome != "exact" || !r.Report.FailureReproduced || count != 1 {
		t.Fatal(r, e, count)
	}
	max = 0
	if _, e = d.Verify(v, g.Model, s.ShrinkLimits{MaxReplayedTransitions: &max}); e == nil || count != 1 {
		t.Fatal("preflight budget", e, count)
	}
	max = 20
	report, e := d.Minimize(v, g, 1, 0, s.DefaultShrinkConfig(), s.ShrinkLimits{MaxReplayedTransitions: &max})
	if e != nil || !report.ValidatedOriginal || !reflect.DeepEqual(report.Minimized.Inputs, []int{2}) {
		t.Fatal(report, e)
	}
	if report.ReplayedTransitions < 2 {
		t.Fatal("verification not charged")
	}
	a := fixture()
	a.V2.Steps[0].StateCheckCount = 0
	bad, e := d.NewEvidenceViewer(a, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = d.Minimize(bad, g, 1, 0, s.DefaultShrinkConfig(), s.ShrinkLimits{}); e == nil || !strings.Contains(e.Error(), "phase") {
		t.Fatal(e)
	}
}
func TestPolicyExplicitAndBounded(t *testing.T) {
	a := fixture()
	a.V2.Trace.Config.Parameters = [][2]string{{"stateless.debug.environment", "allowed"}, {"stateless.debug.origin", "simulation"}}
	v, e := d.NewEvidenceViewer(a, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	count := 0
	g := model(&count)
	if _, e = d.Minimize(v, g, 1, 0, s.DefaultShrinkConfig(), s.ShrinkLimits{}); e == nil || count != 0 {
		t.Fatal("policy omission", e, count)
	}
	p := d.InputPolicy[int, int]{Label: "allowed", Candidates: func(int) (s.Iterator[int], error) { return s.Values([]int{0, 0}), nil }}
	if _, e = d.MinimizeWithPolicy(v, g, 1, 0, p, 1, s.DefaultShrinkConfig(), s.ShrinkLimits{}); e == nil || !strings.Contains(e.Error(), "candidate limit") {
		t.Fatal(e)
	}
	p.Candidates = func(int) (s.Iterator[int], error) { return s.Values([]int{2}), nil }
	if _, e = d.MinimizeWithPolicy(v, g, 1, 0, p, 2, s.DefaultShrinkConfig(), s.ShrinkLimits{}); e != nil {
		t.Fatal(e)
	}
}
func TestExplicitLegacyPhaseAndVerificationBudgetOrder(t *testing.T) {
	count := 0
	g := model(&count)
	a := fixture()
	legacy, e := d.NewEvidenceViewer(s.Artifact{V1: &a.V2.Trace}, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	limit := uint64(1)
	if _, e = d.MinimizeWithPhase(legacy, g, 1, 0, s.PhaseState, s.DefaultShrinkConfig(), s.ShrinkLimits{MaxReplayedTransitions: &limit}); e == nil || count != 0 {
		t.Fatal("v1 insufficient budget executed", e, count)
	}
	limit = 20
	report, e := d.MinimizeWithPhase(legacy, g, 1, 0, s.PhaseState, s.DefaultShrinkConfig(), s.ShrinkLimits{MaxReplayedTransitions: &limit})
	if e != nil || !report.ValidatedOriginal {
		t.Fatal(report, e)
	}
	viewer, e := d.NewEvidenceViewer(fixture(), s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	for _, maximum := range []uint64{1, 2} {
		count = 0
		if _, e = d.Minimize(viewer, g, 1, 0, s.DefaultShrinkConfig(), s.ShrinkLimits{MaxReplayedTransitions: &maximum}); e == nil || count != 1 {
			t.Fatal("v2 phase-first budget", maximum, e, count)
		}
	}
	if _, e = d.MinimizeWithPhase(viewer, g, 1, 0, s.PhaseTransition, s.DefaultShrinkConfig(), s.ShrinkLimits{}); e == nil {
		t.Fatal("persisted phase contradiction")
	}
}
func TestStructuralReadVersusSemanticViewerAndCheckpoint(t *testing.T) {
	a := fixture()
	legacy := s.Artifact{V1: &a.V2.Trace}
	legacy.V1.Steps = append(legacy.V1.Steps, legacy.V1.Steps[0])
	wire, e := s.MarshalArtifact(legacy, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	read, e := s.ParseArtifact(wire, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal("structural read rejected semantic fixture", e)
	}
	if _, e = d.NewEvidenceViewer(read, s.DefaultEvidenceLimits()); e == nil {
		t.Fatal("viewer accepted failed continuation")
	}
	a = fixture()
	legacy = s.Artifact{V1: &a.V2.Trace}
	legacy.V1.Config.Parameters = [][2]string{{"stateless.monitor.checkpoint_after_sequence", "9007199254740993"}}
	v, e := d.NewEvidenceViewer(legacy, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	b, ok := v.Boundary(0)
	if !ok || b.HostSequence != 9007199254740993 || string(b.After) != "10" {
		t.Fatal(b)
	}
	b, ok = v.Boundary(1)
	if !ok || b.HostSequence != 9007199254740994 {
		t.Fatal(b)
	}
}
func TestVerifyPreservesAllDifferencesAndView(t *testing.T) {
	v, e := d.NewEvidenceViewer(fixture(), s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	count := 0
	g := model(&count)
	g.Step = func(int, int) (s.Transition[int, int], error) {
		return s.Transition[int, int]{State: 13, Outputs: []int{99}, Disposition: s.Rejected("different")}, nil
	}
	g.CheckState = func(x int) ([]s.Check, error) {
		if x == 10 {
			return []s.Check{s.Passed("safe")}, nil
		}
		return []s.Check{s.Failed("other", "different")}, nil
	}
	result, e := d.Verify(v, g.Model, s.ShrinkLimits{})
	if e != nil || result.Report.Outcome != "diverged" || len(result.Turns) != 1 {
		t.Fatal(result, e)
	}
	differences := result.Turns[0].Differences
	if !differences.Disposition || !differences.Outputs || !differences.State || !differences.Checks {
		t.Fatal(differences)
	}
	b, _ := v.Boundary(1)
	if string(b.After) != "12" {
		t.Fatal("verification mutated evidence")
	}
}
func TestLegacyCheckpointPlusAndLeadingZeroes(t *testing.T) {
	a := fixture()
	t1 := a.V2.Trace
	t1.Steps = nil
	t1.Termination = "completed"
	t1.Config.Parameters = [][2]string{{"stateless.monitor.checkpoint_after_sequence", "+018446744073709551615"}}
	viewer, e := d.NewEvidenceViewer(s.Artifact{V1: &t1}, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	boundary, ok := viewer.Boundary(0)
	if !ok || boundary.HostSequence != 18446744073709551615 {
		t.Fatal(boundary)
	}
}
func TestLegacyCheckpointFirstMarkerAndV2AuthoritativeCheckpoint(t *testing.T) {
	a := fixture()
	legacy := a.V2.Trace
	legacy.Config.Parameters = [][2]string{{"stateless.monitor.checkpoint_after_sequence", "+0005"}, {"stateless.monitor.checkpoint_after_sequence", "invalid second ignored"}}
	viewer, e := d.NewEvidenceViewer(s.Artifact{V1: &legacy}, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	boundary, _ := viewer.Boundary(1)
	if boundary.HostSequence != 6 {
		t.Fatal(boundary)
	}
	a.V2.Trace.Config.Parameters = [][2]string{{"stateless.monitor.checkpoint_after_sequence", "invalid non-authoritative legacy marker"}}
	viewer, e = d.NewEvidenceViewer(a, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	boundary, _ = viewer.Boundary(1)
	if boundary.HostSequence != 9007199254740994 {
		t.Fatal(boundary)
	}
}
func TestMinimizeZeroAttemptsMatchesPhasePassOrder(t *testing.T) {
	count := 0
	g := model(&count)
	a := fixture()
	viewer, e := d.NewEvidenceViewer(a, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = d.Minimize(viewer, g, 1, 0, s.ShrinkConfig{MaxAttempts: 0}, s.ShrinkLimits{}); e == nil || count != 1 {
		t.Fatal("v2 zero attempts phase pass", e, count)
	}
	count = 0
	legacy, e := d.NewEvidenceViewer(s.Artifact{V1: &a.V2.Trace}, s.DefaultEvidenceLimits())
	if e != nil {
		t.Fatal(e)
	}
	if _, e = d.MinimizeWithPhase(legacy, g, 1, 0, s.PhaseState, s.ShrinkConfig{MaxAttempts: 0}, s.ShrinkLimits{}); e == nil || count != 0 {
		t.Fatal("v1 zero attempts ran model", e, count)
	}
}
