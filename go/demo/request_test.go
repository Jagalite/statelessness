package demo

import (
	"bytes"
	"testing"

	s "github.com/Jagalite/statelessness/go"
)

func TestRequestDemoWitnessAndFixedControl(t *testing.T) {
	witness := []Input{{Kind: "start"}, {Kind: "cancel"}, {Kind: "complete", Generation: 1}}
	for _, tc := range []struct {
		name    string
		model   RequestModel
		failure bool
	}{{"fixed", Fixed(), false}, {"buggy", Buggy(), true}} {
		t.Run(tc.name, func(t *testing.T) {
			model := tc.model.Model()
			trace, err := s.Record(model, s.Values(witness), len(witness))
			if err != nil {
				t.Fatal(err)
			}
			if (trace.Termination == "property_failed") != tc.failure {
				t.Fatal(trace.Termination)
			}
			report, err := s.Replay(model, trace, false)
			if err != nil || report.Outcome != "exact" || report.FailureReproduced != tc.failure {
				t.Fatal(report, err)
			}
			if tc.failure {
				found := false
				for _, c := range trace.Steps[len(trace.Steps)-1].Checks {
					if c.ID == "ready_requires_active" && c.Status == "failed" {
						found = true
					}
				}
				if !found {
					t.Fatal("missing stale completion failure")
				}
			}
		})
	}
}
func TestRequestDemoCodecAndGeneration(t *testing.T) {
	model := Fixed().Model()
	v := State{Generation: 2, Active: true, Ready: false, Pending: []uint8{1, 2}}
	estimated, err := model.EstimatedStateBytes(v)
	if err != nil || estimated == nil || *estimated < uint64(cap(v.Pending)) {
		t.Fatal("invalid state estimate", estimated, err)
	}
	data, err := model.Codec.EncodeState(v)
	if err != nil || !bytes.Equal(data, []byte{2, 1, 0, 2, 1, 2}) {
		t.Fatal(data, err)
	}
	restored, err := model.Codec.DecodeState(data)
	if err != nil || !model.EqualStates(v, restored) {
		t.Fatal(restored, err)
	}
	for _, bad := range [][]byte{{0, 2, 0, 0}, {0, 0, 0, 1}, {0, 0, 0, 0, 1}} {
		if _, err = model.Codec.DecodeState(bad); err == nil {
			t.Fatal("accepted malformed state", bad)
		}
	}
	for _, in := range []Input{{Kind: "start"}, {Kind: "cancel"}, {Kind: "complete", Generation: 2}} {
		raw, e := model.Codec.EncodeInput(in)
		if e != nil {
			t.Fatal(e)
		}
		got, e := model.Codec.DecodeInput(raw)
		if e != nil || got != in {
			t.Fatal(got, e)
		}
	}
	gen := Fixed().Generator()
	rng := s.NewRng(7)
	for i := 0; i < 20; i++ {
		value, ok, e := gen.Generate(v, rng)
		if e != nil || !ok {
			t.Fatal(value, ok, e)
		}
		enabled, e := gen.IsEnabled(v, value)
		if e != nil || !enabled {
			t.Fatal(value, e)
		}
	}
}
