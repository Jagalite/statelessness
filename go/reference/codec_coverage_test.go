package reference

import (
	"encoding/hex"
	s "github.com/Jagalite/statelessness/go"
	"reflect"
	"testing"
)

func TestControllerCodecReferenceWireAndCanonicalRejection(t *testing.T) {
	c := Controller{Bounds: DefaultBounds()}
	in := Input{Kind: "complete", Generation: 2, Attempt: 1, Outcome: "retryable_failure"}
	data, err := c.EncodeInput(in)
	if err != nil || hex.EncodeToString(data) != "0202000000000000000101" {
		t.Fatalf("input wire %x %v", data, err)
	}
	got, err := c.DecodeInput(data)
	if err != nil || got != in {
		t.Fatal(got, err)
	}
	data[10] = 2
	if _, err = c.DecodeInput(data); err == nil {
		t.Fatal("invalid outcome accepted")
	}
	g := uint64(1)
	st := State{Current: &g, Jobs: []Job{{Generation: 1, Attempt: 1, Phase: "running", ResourceOwned: false}}}
	encoded, err := c.EncodeState(st)
	if err != nil {
		t.Fatal(err)
	}
	// Structurally valid yet property-invalid states must survive replay.
	decoded, err := c.DecodeState(encoded)
	if err != nil || !reflect.DeepEqual(st, decoded) {
		t.Fatal(decoded, err)
	}
	checks, _ := c.CheckState(decoded)
	if checks[0].Status != "failed" {
		t.Fatal("property-invalid state erased")
	}
	encoded[len(encoded)-1] = 2
	if _, err = c.DecodeState(encoded); err == nil {
		t.Fatal("noncanonical boolean accepted")
	}
}
func TestReferenceAssuranceExhaustsIndependentManifest(t *testing.T) {
	c := Controller{Bounds: DefaultBounds()}
	manifest, err := c.Manifest()
	if err != nil {
		t.Fatal(err)
	}
	run, err := s.AssuranceEnumerate(c.Model(), manifest, Coverage, s.DefaultSearchConfig(), s.SearchLimits{})
	if err != nil || run.SearchError != nil || run.Search == nil || run.Search.Termination != "graph_exhausted" || !run.Assurance.AssessStrict().Adequate {
		t.Fatalf("assurance: %+v %v", run, err)
	}
	manifest.Properties = append(manifest.Properties, s.AssuranceProperty{ID: "independent.missing", Phase: "transition"})
	run, err = s.AssuranceEnumerate(c.Model(), manifest, Coverage, s.DefaultSearchConfig(), s.SearchLimits{})
	if err != nil || run.SearchError != nil || run.Assurance.AssessStrict().Adequate {
		t.Fatalf("missing property accepted: %+v %v", run, err)
	}
}
