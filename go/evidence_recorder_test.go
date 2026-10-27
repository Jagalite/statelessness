package statelessness_test

import (
	"errors"
	s "github.com/Jagalite/statelessness/go"
	"math"
	"reflect"
	"strings"
	"testing"
)

func evidenceRecorderSetup(t *testing.T, maxSteps int, metadata uint64) (s.Model[int, int, string], *s.EvidenceRecorder, s.EvidenceLimits) {
	t.Helper()
	m := auditRecordingModel()
	initial, e := s.CheckInitial(m, 0)
	if e != nil {
		t.Fatal(e)
	}
	limits := s.DefaultEvidenceLimits()
	limits.MaxMetadataBytes = metadata
	o := s.DefaultRecorderOptions()
	o.MaxSteps = maxSteps
	r, e := s.NewEvidenceRecorder(initial, 0, s.RunConfig{Strategy: "manual"}, o, s.Provenance{}, limits)
	if e != nil {
		t.Fatal(e)
	}
	return m, r, limits
}
func evidenceTurn(t *testing.T, m s.Model[int, int, string], sequence uint64) *s.CheckedTurn[int, int, string] {
	t.Helper()
	turn, e := s.CheckTurn(m, int(sequence-1), 1, s.Accept(int(sequence), "o"), sequence, s.FullChecks())
	if e != nil {
		t.Fatal(e)
	}
	return turn
}
func TestEvidenceRecorderAtomicMetadataAndTraceRetention(t *testing.T) {
	for _, retention := range []string{"metadata", "trace", "causes"} {
		t.Run(retention, func(t *testing.T) {
			m, r, limits := evidenceRecorderSetup(t, 10, 1024)
			if retention == "metadata" {
				m, r, limits = evidenceRecorderSetup(t, 10, 70)
			}
			if retention == "trace" {
				m, r, limits = evidenceRecorderSetup(t, 1, 1024)
			}
			if retention == "causes" {
				initial, e := s.CheckInitial(m, 0)
				if e != nil {
					t.Fatal(e)
				}
				limits.MaxTotalCauses = 1
				r, e = s.NewEvidenceRecorder(initial, 0, s.RunConfig{Strategy: "manual"}, s.DefaultRecorderOptions(), s.Provenance{}, limits)
				if e != nil {
					t.Fatal(e)
				}
			}
			for sequence := uint64(1); sequence <= 3; sequence++ {
				context := s.CausalContext{}
				if retention == "causes" && sequence > 1 {
					context.Causes = []s.OutputOrigin{{Sequence: sequence - 1, Output: 0}}
				}
				turn := evidenceTurn(t, m, sequence)
				m.CheckState = func(int) ([]s.Check, error) { t.Fatal("recorder rechecked"); return nil, nil }
				if e := s.ObserveEvidenceRecorder(r, turn, context); e != nil {
					t.Fatal(e)
				}
				m.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Passed("c")}, nil }
				snapshot := r.Snapshot()
				if e := snapshot.Validate(limits); e != nil {
					t.Fatal(e)
				}
				if len(snapshot.Steps) != len(snapshot.Trace.Steps) {
					t.Fatal("journal mismatch")
				}
			}
			snap := r.Snapshot()
			if snap.CheckpointAfter == 0 {
				t.Fatal("did not evict")
			}
			for i, step := range snap.Steps {
				if step.Sequence != snap.CheckpointAfter+uint64(i)+1 || step.StateCheckCount != 1 {
					t.Fatal(step)
				}
			}
			if !reflect.DeepEqual(snap.Trace.InitialChecks, []s.Check{s.Passed("c")}) {
				t.Fatal("checkpoint phase wrong")
			}
		})
	}
}
func TestEvidenceRecorderResolvesBeforePlannedEviction(t *testing.T) {
	m, r, limits := evidenceRecorderSetup(t, 10, 70)
	for seq := uint64(1); seq <= 2; seq++ {
		if e := s.ObserveEvidenceRecorder(r, evidenceTurn(t, m, seq), s.CausalContext{}); e != nil {
			t.Fatal(e)
		}
	}
	e := s.ObserveEvidenceRecorder(r, evidenceTurn(t, m, 3), s.CausalContext{Causes: []s.OutputOrigin{{Sequence: 1, Output: 1}}})
	if e == nil || !strings.Contains(e.Error(), "retained output range") {
		t.Fatalf("%v", e)
	}
	snap := r.Snapshot()
	if snap.CheckpointAfter != 0 || len(snap.Steps) != 2 || snap.Trace.Termination != "model_error" {
		t.Fatal("invalid cause evicted prefix")
	}
	if e = snap.Validate(limits); e != nil {
		t.Fatal(e)
	}
}
func TestEvidenceRecorderOwnedContextAndProvenance(t *testing.T) {
	m := auditRecordingModel()
	initial, e := s.CheckInitial(m, 0)
	if e != nil {
		t.Fatal(e)
	}
	limits := s.DefaultEvidenceLimits()
	limits.MaxMetadataBytes = 1024
	manifest := "manifest"
	provenance := s.Provenance{Run: "run", Manifest: &manifest, Environment: [][2]string{{"mode", "original"}}}
	r, e := s.NewEvidenceRecorder(initial, 41, s.RunConfig{Strategy: "manual"}, s.DefaultRecorderOptions(), provenance, limits)
	if e != nil {
		t.Fatal(e)
	}
	manifest = "mutated"
	provenance.Environment[0][1] = "mutated"
	machine := "machine"
	context := s.CausalContext{Machine: &machine, Causes: []s.OutputOrigin{{Sequence: 1, Output: math.MaxUint32}}}
	turn, e := s.CheckTurn(m, 0, 1, s.Accept(1, "o"), 42, s.FullChecks())
	if e != nil {
		t.Fatal(e)
	}
	if e = s.ObserveEvidenceRecorder(r, turn, context); e != nil {
		t.Fatal(e)
	}
	machine = "changed"
	context.Causes[0].Output = 0
	snap := r.Snapshot()
	if *snap.Provenance.Manifest != "manifest" || snap.Provenance.Environment[0][1] != "original" || *snap.Steps[0].Causal.Machine != "machine" || snap.Steps[0].Causal.Causes[0].Output != math.MaxUint32 {
		t.Fatal("retained caller aliases")
	}
	snap.Steps[0].Causal.Causes[0].Output = 1
	snap.Trace.InitialState[0] = 99
	if r.Snapshot().Steps[0].Causal.Causes[0].Output != math.MaxUint32 || r.Snapshot().Trace.InitialState[0] != 0 {
		t.Fatal("snapshot aliases")
	}
	if e = r.Snapshot().Validate(limits); e != nil {
		t.Fatal(e)
	}
}
func TestEvidenceRecorderFailureAtomicityAndReentry(t *testing.T) {
	m, r, limits := evidenceRecorderSetup(t, 10, 50)
	if e := s.ObserveEvidenceRecorder(r, evidenceTurn(t, m, 1), s.CausalContext{}); e != nil {
		t.Fatal(e)
	}
	if e := s.ObserveEvidenceRecorder(r, evidenceTurn(t, m, 2), s.CausalContext{Causes: []s.OutputOrigin{{Sequence: 1, Output: 0}}}); e == nil {
		t.Fatal("oversized single metadata accepted")
	}
	if len(r.Snapshot().Steps) != 1 {
		t.Fatal("failure mutated journal")
	}
	r.StopWithError(errors.New("later"))
	if e := r.Snapshot().Validate(limits); e != nil {
		t.Fatal(e)
	}
	m, r, limits = evidenceRecorderSetup(t, 10, 1024)
	turn := evidenceTurn(t, m, 1)
	first := errors.New("stop during codec")
	m.Codec.EncodeOutput = func(string) ([]byte, error) {
		if r.IntoTrace() != nil {
			t.Fatal("nested transfer")
		}
		if e := s.ObserveEvidenceRecorder(r, turn, s.CausalContext{}); e == nil {
			t.Fatal("nested observe")
		}
		r.StopWithError(first)
		return []byte("o"), nil
	}
	turn = evidenceTurn(t, m, 1)
	if e := s.ObserveEvidenceRecorder(r, turn, s.CausalContext{}); e != first {
		t.Fatalf("%v", e)
	}
	if len(r.Snapshot().Steps) != 0 || len(r.Snapshot().Trace.Steps) != 0 {
		t.Fatal("outer committed after stop")
	}
	if e := r.Snapshot().Validate(limits); e != nil {
		t.Fatal(e)
	}
}
func TestEvidenceRecorderReservationAndPropertyFreeze(t *testing.T) {
	m := auditRecordingModel()
	initial, e := s.CheckInitial(m, 0)
	if e != nil {
		t.Fatal(e)
	}
	limits := s.DefaultEvidenceLimits()
	limits.MaxMetadataBytes = math.MaxUint64
	if _, e = s.NewEvidenceRecorder(initial, 0, s.RunConfig{}, s.DefaultRecorderOptions(), s.Provenance{}, limits); e == nil {
		t.Fatal("reservation overflow accepted")
	}
	limits = s.DefaultEvidenceLimits()
	o := s.DefaultRecorderOptions()
	o.MaxRetainedBytes = 100
	if _, e = s.NewEvidenceRecorder(initial, 0, s.RunConfig{}, o, s.Provenance{}, limits); e == nil {
		t.Fatal("reservation beyond retention")
	}
	limits.MaxMetadataBytes = 1024
	m.CheckState = func(int) ([]s.Check, error) { return []s.Check{s.Failed("failed", "bad")}, nil }
	initial, e = s.CheckInitial(m, 0)
	if e != nil {
		t.Fatal(e)
	}
	r, e := s.NewEvidenceRecorder(initial, 0, s.RunConfig{}, s.DefaultRecorderOptions(), s.Provenance{}, limits)
	if e != nil || !r.IsFrozen() {
		t.Fatalf("%v", e)
	}
	r.StopWithError(errors.New("later"))
	if r.Snapshot().Trace.Termination != "property_failed" {
		t.Fatal("property overwritten")
	}
}

func TestEvidenceRecorderSnapshotsProvenanceBeforeCallbacks(t *testing.T) {
	m := auditRecordingModel()
	manifest := "original"
	provenance := s.Provenance{Manifest: &manifest, Environment: [][2]string{{"key", "original"}}}
	m.Codec.Metadata = func() (s.Metadata, error) {
		manifest = "mutated"
		provenance.Environment[0][1] = "mutated"
		return s.Identity("m", "b"), nil
	}
	initial, e := s.CheckInitial(m, 0)
	if e != nil {
		t.Fatal(e)
	}
	limits := s.DefaultEvidenceLimits()
	limits.MaxMetadataBytes = 1024
	r, e := s.NewEvidenceRecorder(initial, 0, s.RunConfig{}, s.DefaultRecorderOptions(), provenance, limits)
	if e != nil {
		t.Fatal(e)
	}
	snapshot := r.Snapshot()
	if *snapshot.Provenance.Manifest != "original" || snapshot.Provenance.Environment[0][1] != "original" {
		t.Fatal("callback rewrote already validated provenance")
	}
	if e = snapshot.Validate(limits); e != nil {
		t.Fatal(e)
	}
}
