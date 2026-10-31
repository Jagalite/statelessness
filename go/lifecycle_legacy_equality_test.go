package statelessness

import "testing"

func TestLegacyLifecycleChecksPreserveEncodedViolationText(t *testing.T) {
	m := NewLifecycleMonitor[uint8](8)
	h := LifecycleHistory[uint8]{Violations: []LifecycleViolation[uint8]{{Code: "time_backwards"}, {Code: "custom message"}}}
	codec, err := LifecycleHistoryCodec(m, Uint8Codec())
	if err != nil {
		t.Fatal(err)
	}
	data, err := EncodeValue(codec, h, 4096)
	if err != nil {
		t.Fatal(err)
	}
	restored, err := FromValueBytes(codec, data, DefaultDecodeLimits())
	if err != nil {
		t.Fatal(err)
	}
	before, err := m.Checks(h)
	if err != nil {
		t.Fatal(err)
	}
	after, err := m.Checks(restored)
	if err != nil {
		t.Fatal(err)
	}
	want := "logical time moved backwards; custom message"
	if before[0].Details != want || after[0].Details != want {
		t.Fatalf("check detail changed across codec: before=%q after=%q", before[0].Details, after[0].Details)
	}
}

func TestLegacyLifecycleEqualityUsesEncodedViolationText(t *testing.T) {
	m := NewLifecycleMonitor[uint8](8)
	o := LifecycleOracle[int, int, int](m, Identity("fixture", "test"), func(int, Disposition) ([]LifecycleEvent[uint8], error) { return nil, nil }, func([]int) ([]LifecycleEvent[uint8], error) { return nil, nil })
	a := LifecycleHistory[uint8]{Violations: []LifecycleViolation[uint8]{{Code: "time_backwards"}}}
	b := LifecycleHistory[uint8]{Violations: []LifecycleViolation[uint8]{{Code: "duplicate_release"}}}
	if o.EqualHistory(a, b) {
		t.Fatal("different encoded messages must not compare equal")
	}
	text := "logical time moved backwards"
	c := LifecycleHistory[uint8]{Violations: []LifecycleViolation[uint8]{{Legacy: &text}}}
	if !o.EqualHistory(a, c) {
		t.Fatal("equivalent encoded messages must compare equal")
	}
}

func TestLifecycleOraclePreflightBeforeClassifiers(t *testing.T) {
	m := NewLifecycleMonitor[uint8](1)
	calls := 0
	o := LifecycleOracle[int, int, int](m, Identity("fixture", "test"), func(int, Disposition) ([]LifecycleEvent[uint8], error) { calls++; return nil, nil }, func([]int) ([]LifecycleEvent[uint8], error) { calls++; return nil, nil })
	cases := []LifecycleHistory[uint8]{
		{Obligations: []LifecycleEntry[uint8]{{Key: 1}, {Key: 2}}},
		{Now: 1, Obligations: []LifecycleEntry[uint8]{{Key: 1, Obligation: LifecycleObligation{Deadline: 1}}}, Violations: []LifecycleViolation[uint8]{{Code: "key_reused"}}},
	}
	for _, history := range cases {
		calls = 0
		if _, err := o.Advance(history, 0, nil, Disposition{}); err == nil {
			t.Fatal("expected preflight error")
		}
		if calls != 0 {
			t.Fatalf("classifiers ran %d times before preflight failure", calls)
		}
	}
}
