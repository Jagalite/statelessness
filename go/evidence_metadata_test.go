package statelessness

import (
	"encoding/binary"
	"encoding/hex"
	"reflect"
	"strings"
	"testing"
)

func metadataPointer[T any](v T) *T { return &v }
func metadataFixture() *TraceV2 {
	return &TraceV2{CheckpointAfter: 7, Provenance: Provenance{Run: "run雪", Epoch: 9, Manifest: metadataPointer(""), Source: metadataPointer("src"), Environment: [][2]string{{"k", "v"}, {"k", "two"}}}, Steps: []StepEvidence{{Sequence: 8, StateCheckCount: 2, Causal: CausalContext{Machine: metadataPointer("m"), Generation: metadataPointer(uint64(3)), Correlation: metadataPointer("c"), LogicalTime: metadataPointer(uint64(5)), Causes: []OutputOrigin{{Sequence: 7, Output: 1}}}}}}
}
func TestEvidenceMetadataCanonicalEmpty(t *testing.T) {
	data, e := encodeEvidenceMetadata(&TraceV2{}, 30)
	if e != nil {
		t.Fatal(e)
	}
	if hex.EncodeToString(data) != strings.Repeat("00", 30) {
		t.Fatalf("%x", data)
	}
	checkpoint, p, steps, e := decodeEvidenceMetadata(data, 30)
	if e != nil || checkpoint != 0 || p.Run != "" || p.Epoch != 0 || len(p.Environment) != 0 || len(steps) != 0 {
		t.Fatal(checkpoint, p, steps, e)
	}
	if _, e = encodeEvidenceMetadata(&TraceV2{}, 29); e == nil {
		t.Fatal("missing encoding limit")
	}
	if _, _, _, e = decodeEvidenceMetadata(data, 29); e == nil {
		t.Fatal("missing byte limit")
	}
}
func TestEvidenceMetadataRoundtripAndOwnership(t *testing.T) {
	v := metadataFixture()
	data, e := encodeEvidenceMetadata(v, 4096)
	if e != nil {
		t.Fatal(e)
	}
	if hex.EncodeToString(data) != "07000000000000000600000072756ee99baa09000000000000000100000000010300000073726302000000010000006b0100000076010000006b0300000074776f0100000008000000000000000200000001010000006d01030000000000000001010000006301050000000000000001000000070000000000000001000000" {
		t.Fatalf("noncanonical metadata: %x", data)
	}
	checkpoint, p, steps, e := decodeEvidenceMetadata(data, 4096)
	if e != nil {
		t.Fatal(e)
	}
	if checkpoint != v.CheckpointAfter || !reflect.DeepEqual(p, v.Provenance) || !reflect.DeepEqual(steps, v.Steps) {
		t.Fatal(checkpoint, p, steps)
	}
	encoded, e := encodeEvidenceMetadata(&TraceV2{CheckpointAfter: checkpoint, Provenance: p, Steps: steps}, uint64(len(data)))
	if e != nil || !reflect.DeepEqual(data, encoded) {
		t.Fatal(e)
	}
	for i := range data {
		data[i] = 0
	}
	if p.Run != "run雪" || *p.Source != "src" || p.Environment[1][1] != "two" {
		t.Fatal("decoded values alias input")
	}
	cloned := cloneProvenance(p)
	*cloned.Source = "changed"
	cloned.Environment[0][0] = "changed"
	if *p.Source != "src" || p.Environment[0][0] != "k" {
		t.Fatal("provenance clone aliases")
	}
	step := cloneStepEvidence(steps[0])
	*step.Causal.Machine = "changed"
	*step.Causal.Generation = 99
	step.Causal.Causes[0].Output = 9
	if *steps[0].Causal.Machine != "m" || *steps[0].Causal.Generation != 3 || steps[0].Causal.Causes[0].Output != 1 {
		t.Fatal("causal clone aliases")
	}
}
func TestEvidenceMetadataRejectsMalformedAndTruncated(t *testing.T) {
	data, e := encodeEvidenceMetadata(metadataFixture(), 4096)
	if e != nil {
		t.Fatal(e)
	}
	for n := 0; n < len(data); n++ {
		if _, _, _, e = decodeEvidenceMetadata(data[:n], 4096); e == nil {
			t.Fatalf("accepted prefix %d", n)
		}
	}
	if _, _, _, e = decodeEvidenceMetadata(append(data, 0), 4096); e == nil {
		t.Fatal("trailing data accepted")
	}
	empty, e := encodeEvidenceMetadata(&TraceV2{}, 4096)
	if e != nil {
		t.Fatal(e)
	}
	tag := append([]byte{}, empty...)
	tag[20] = 2
	if _, _, _, e = decodeEvidenceMetadata(tag, 4096); e == nil || !strings.Contains(e.Error(), "option") {
		t.Fatal(e)
	}
	badString := append([]byte{}, empty...)
	binary.LittleEndian.PutUint32(badString[8:12], 1)
	badString = append(badString[:12], append([]byte{255}, badString[12:]...)...)
	if _, _, _, e = decodeEvidenceMetadata(badString, 4096); e == nil || !strings.Contains(e.Error(), "UTF-8") {
		t.Fatal(e)
	}
	bad := metadataFixture()
	bad.Provenance.Run = string([]byte{255})
	if _, e = encodeEvidenceMetadata(bad, 4096); e == nil {
		t.Fatal("invalid UTF8 encoded")
	}
	for _, offset := range []int{8, 22, 26} {
		b := append([]byte{}, empty...)
		binary.LittleEndian.PutUint32(b[offset:offset+4], ^uint32(0))
		if _, _, _, e = decodeEvidenceMetadata(b, ^uint64(0)); e == nil {
			t.Fatalf("forged collection %d accepted", offset)
		}
	}
}
func TestEvidenceMetadataIndependentStructuralBudgets(t *testing.T) {
	if evidenceTimes16(^uint64(0)) != ^uint64(0) || evidenceTimes16(3) != 48 {
		t.Fatal("saturation")
	}
	for _, tc := range []struct {
		name string
		d    evidenceMetadataDecoder
		run  func(*evidenceMetadataDecoder)
	}{
		{"work", evidenceMetadataDecoder{data: []byte{0}, work: 0}, func(d *evidenceMetadataDecoder) { d.u8() }},
		{"depth", evidenceMetadataDecoder{data: []byte{0}, work: 10, depth: 16}, func(d *evidenceMetadataDecoder) { d.u8() }},
		{"elements", evidenceMetadataDecoder{elements: 0, allocation: 100, work: 100}, func(d *evidenceMetadataDecoder) { d.collection(1, 1, 0) }},
		{"allocation", evidenceMetadataDecoder{elements: 100, allocation: 0, work: 100}, func(d *evidenceMetadataDecoder) { d.collection(1, evidenceStepWeight, 0) }},
		{"collection-work", evidenceMetadataDecoder{elements: 100, allocation: 100, work: 0}, func(d *evidenceMetadataDecoder) { d.collection(1, 1, 0) }},
		{"overflow", evidenceMetadataDecoder{elements: ^uint64(0), allocation: ^uint64(0), work: ^uint64(0)}, func(d *evidenceMetadataDecoder) { d.collection(^uint64(0), 2, 0) }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tc.run(&tc.d)
			if tc.d.err == nil {
				t.Fatal("missing structural budget error")
			}
			first := tc.d.err
			tc.d.take(0)
			if tc.d.err != first {
				t.Fatal("failure not sticky")
			}
		})
	}
}

func TestEvidenceMetadataRust64LogicalAllocationWeights(t *testing.T) {
	if evidenceEnvironmentWeight != 48 || evidenceOriginWeight != 16 || evidenceStepWeight != 120 {
		t.Fatal("changed canonical schema weights")
	}
	// Four phase rows fit the 480-unit allocation budget, five exceed it.
	// Admission charges allocation before inspecting absent element bodies.
	for _, tc := range []struct {
		count uint32
		want  string
	}{{4, "truncated value"}, {5, "decode allocation limit"}} {
		data, e := encodeEvidenceMetadata(&TraceV2{}, 30)
		if e != nil {
			t.Fatal(e)
		}
		binary.LittleEndian.PutUint32(data[26:], tc.count)
		_, _, _, e = decodeEvidenceMetadata(data, 30)
		if e == nil || !strings.Contains(e.Error(), tc.want) {
			t.Fatalf("count%d: %v, want%s", tc.count, e, tc.want)
		}
	}
}
