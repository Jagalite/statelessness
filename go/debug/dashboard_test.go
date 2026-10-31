package debug_test

import (
	"encoding/json"
	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
	"math"
	"strings"
	"testing"
)

func TestDashboardPassiveUnknownAndBounded(t *testing.T) {
	m := metricStore(t)
	l := d.Labels{Origin: d.OriginLive}
	s := m.Current()
	s.Epoch = math.MaxUint64
	s.Revision = 9007199254740993
	view := d.DashboardFromSnapshot(s, d.TelemetryHealth{}, []d.Labels{l}, 100)
	if !view.ObservationComplete || view.Rows[0].Value != nil {
		t.Fatal(view)
	}
	b, e := view.JSON(1 << 20)
	if e != nil || !json.Valid(b) || !strings.Contains(string(b), "18446744073709551615") || !strings.Contains(string(b), "9007199254740993") || !strings.Contains(string(b), `"value":null`) {
		t.Fatal(string(b), e)
	}
	if _, e = view.JSON(8); e == nil {
		t.Fatal("byte bound")
	}
	view = d.DashboardFromSnapshot(s, d.TelemetryHealth{}, []d.Labels{l}, 1)
	if !view.Truncated || view.ObservationComplete {
		t.Fatal(view)
	}
}
func TestCausalJoinFullWidthAndUnknowns(t *testing.T) {
	machine := "18446744073709551615"
	e := s.TraceV2{CheckpointAfter: 9007199254740993, Provenance: s.Provenance{Run: "18446744073709551615", Epoch: math.MaxUint64}, Steps: []s.StepEvidence{{Sequence: 9007199254740994, Causal: s.CausalContext{Machine: &machine}}}, Trace: s.AuditTrace{Trace: s.Trace{Steps: []s.TraceStep{{Outputs: [][]byte{[]byte("sensitive")}}}}}}
	timeline := d.EffectTimeline{Effect: d.EffectID{Run: math.MaxUint64, Epoch: math.MaxUint64}, Origin: d.RequestOrigin{Run: math.MaxUint64, Epoch: math.MaxUint64, Machine: math.MaxUint64, TransitionSequence: 9007199254740994}, Complete: false}
	r := d.Correlate(e, timeline)
	if r.Kind != "retained" || r.LocalSequence != 1 || r.DetailComplete {
		t.Fatal(r)
	}
	timeline.Origin.TransitionSequence--
	if r = d.Correlate(e, timeline); r.Kind != "before-checkpoint" {
		t.Fatal(r)
	}
	timeline.Origin.TransitionSequence += 2
	if r = d.Correlate(e, timeline); r.Kind != "conflict" {
		t.Fatal(r)
	}
	e.Provenance.Run = ""
	if r = d.Correlate(e, timeline); r.Kind != "missing-identity" {
		t.Fatal(r)
	}
}
