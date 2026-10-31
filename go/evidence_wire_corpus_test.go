package statelessness_test

import (
	"bytes"
	"encoding/json"
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	"io"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

type evidenceWireUint uint64

func (v *evidenceWireUint) UnmarshalJSON(data []byte) error {
	var text string
	if e := json.Unmarshal(data, &text); e != nil {
		return e
	}
	n, e := strconv.ParseUint(text, 10, 64)
	*v = evidenceWireUint(n)
	return e
}

type evidenceWireValue struct {
	Trace           binaryValue
	CheckpointAfter evidenceWireUint `json:"checkpoint_after"`
	Provenance      struct {
		Run              string
		Epoch            evidenceWireUint
		Manifest, Source *string
		Environment      [][2]string
	}
	Steps []struct {
		Sequence        evidenceWireUint
		StateCheckCount uint32 `json:"state_check_count"`
		Causal          struct {
			Machine, Correlation *string
			Generation           *evidenceWireUint
			LogicalTime          *evidenceWireUint `json:"logical_time"`
			Causes               []struct {
				Sequence evidenceWireUint
				Output   uint32
			}
		}
	}
}

func evidenceWireConvert(t *testing.T, v evidenceWireValue) *s.TraceV2 {
	t.Helper()
	p := v.Provenance
	out := &s.TraceV2{Trace: *binaryAudit(t, v.Trace), CheckpointAfter: uint64(v.CheckpointAfter), Provenance: s.Provenance{Run: p.Run, Epoch: uint64(p.Epoch), Manifest: p.Manifest, Source: p.Source, Environment: p.Environment}, Steps: make([]s.StepEvidence, len(v.Steps))}
	for i, step := range v.Steps {
		c := step.Causal
		context := s.CausalContext{Machine: c.Machine, Correlation: c.Correlation, Causes: make([]s.OutputOrigin, len(c.Causes))}
		if c.Generation != nil {
			v := uint64(*c.Generation)
			context.Generation = &v
		}
		if c.LogicalTime != nil {
			v := uint64(*c.LogicalTime)
			context.LogicalTime = &v
		}
		for k, o := range c.Causes {
			context.Causes[k] = s.OutputOrigin{Sequence: uint64(o.Sequence), Output: o.Output}
		}
		out.Steps[i] = s.StepEvidence{Sequence: uint64(step.Sequence), StateCheckCount: step.StateCheckCount, Causal: context}
	}
	return out
}
func evidenceWireLimits(t *testing.T, raw map[string]json.RawMessage) s.EvidenceLimits {
	t.Helper()
	l := s.DefaultEvidenceLimits()
	for name, data := range raw {
		if name == "trace" {
			var rawValues map[string]json.RawMessage
			if e := json.Unmarshal(data, &rawValues); e != nil {
				t.Fatal(e)
			}
			values := map[string]uint64{}
			for key, value := range rawValues {
				if key == "max_checks_per_batch" {
					key = "max_checks_per_step"
				}
				var n uint64
				if len(value) > 0 && value[0] == '"' {
					var text string
					if e := json.Unmarshal(value, &text); e != nil {
						t.Fatal(e)
					}
					var e error
					n, e = strconv.ParseUint(text, 10, 64)
					if e != nil {
						t.Fatal(e)
					}
				} else if e := json.Unmarshal(value, &n); e != nil {
					t.Fatal(e)
				}
				values[key] = n
			}
			l.Trace = binaryLimits(values)
			continue
		}
		var n uint64
		if e := json.Unmarshal(data, &n); e != nil {
			t.Fatal(e)
		}
		switch name {
		case "max_metadata_bytes":
			l.MaxMetadataBytes = n
		case "max_labels":
			l.MaxLabels = n
		case "max_label_bytes":
			l.MaxLabelBytes = n
		case "max_causes_per_step":
			l.MaxCausesPerStep = n
		case "max_total_causes":
			l.MaxTotalCauses = n
		default:
			t.Fatal("unknown evidence limit", name)
		}
	}
	return l
}
func TestEvidenceWireSharedCorpus(t *testing.T) {
	path := os.Getenv("STATELESS_CAUSAL_CORPUS")
	if path == "" {
		path = "../conformance/causal-evidence-v2.json"
	}
	data, e := os.ReadFile(path)
	if e != nil {
		t.Fatal(e)
	}
	var corpus struct {
		WireCases []struct {
			Case          string
			Valid         bool
			ArtifactKind  string `json:"artifact_kind"`
			Evidence      *evidenceWireValue
			Trace         *binaryValue
			BytesHex      string  `json:"bytes_hex"`
			ErrorContains *string `json:"error_contains"`
			Limits        map[string]json.RawMessage
		} `json:"wire_cases"`
	}
	if e = json.Unmarshal(data, &corpus); e != nil {
		t.Fatal(e)
	}
	if len(corpus.WireCases) == 0 {
		t.Fatal("empty causal wire corpus")
	}
	for _, c := range corpus.WireCases {
		t.Run(c.Case, func(t *testing.T) {
			wire := binaryBytes(t, c.BytesHex)
			limits := evidenceWireLimits(t, c.Limits)
			artifact, e := s.ParseArtifact(wire, limits)
			if !c.Valid {
				if e == nil {
					t.Fatal("invalid artifact accepted")
				}
				if c.ErrorContains != nil && !strings.Contains(e.Error(), *c.ErrorContains) {
					t.Fatalf("error %v must contain %q", e, *c.ErrorContains)
				}
				if c.Evidence != nil {
					var published bytes.Buffer
					if e = evidenceWireConvert(t, *c.Evidence).Write(&published, limits); e == nil || published.Len() != 0 {
						t.Fatalf("invalid evidence writer published %d bytes: %v", published.Len(), e)
					}
				}
				return
			}
			if e != nil {
				t.Fatal(e)
			}
			var encoded []byte
			switch c.ArtifactKind {
			case "v1":
				if artifact.V1 == nil || artifact.V2 != nil {
					t.Fatal("lost v1 identity")
				}
				expected := binaryAudit(t, *c.Trace)
				if !reflect.DeepEqual(artifact.V1, expected) {
					t.Fatal("v1 mismatch")
				}
				encoded, e = s.MarshalAuditTrace(artifact.V1, limits.Trace)
				if _, ok := artifact.CheckPhase(0, 0); ok {
					t.Fatal("invented v1 phase")
				}
			case "v2":
				if artifact.V2 == nil || artifact.V1 != nil {
					t.Fatal("lost v2 identity")
				}
				expected := evidenceWireConvert(t, *c.Evidence)
				if !reflect.DeepEqual(artifact.V2, expected) {
					t.Fatalf("v2 mismatch\ngot %#v\nwant %#v", artifact.V2, expected)
				}
				encoded, e = s.MarshalTraceV2(artifact.V2, limits)
			default:
				t.Fatal("unknown artifact kind", c.ArtifactKind)
			}
			if e != nil || !bytes.Equal(encoded, wire) {
				t.Fatalf("reencoding mismatch: %v", e)
			}
			// Every truncation is rejected, independently of transport chunk boundaries.
			for n := 0; n < len(wire); n++ {
				if _, e = s.ParseArtifact(wire[:n], limits); e == nil {
					t.Fatalf("accepted prefix %d", n)
				}
			}
			streamed, e := s.ReadArtifact(&evidenceChunkReader{data: append([]byte{}, wire...), chunk: 3}, limits)
			if e != nil || !reflect.DeepEqual(streamed, artifact) {
				t.Fatalf("chunked read mismatch: %v", e)
			}
		})
	}
}

type evidenceChunkReader struct {
	data  []byte
	chunk int
}

func (r *evidenceChunkReader) Read(p []byte) (int, error) {
	if len(r.data) == 0 {
		return 0, io.EOF
	}
	n := min(len(p), r.chunk, len(r.data))
	copy(p, r.data[:n])
	r.data = r.data[n:]
	return n, nil
}
func TestEvidenceWireReadIOError(t *testing.T) {
	_, e := s.ReadArtifact(evidenceFailureReader{}, s.DefaultEvidenceLimits())
	if e == nil || !strings.Contains(e.Error(), "injected") {
		t.Fatal(e)
	}
}

type evidenceFailureReader struct{}

func (evidenceFailureReader) Read([]byte) (int, error) {
	return 0, fmt.Errorf("injected evidence read failure")
}
