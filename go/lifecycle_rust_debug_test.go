package statelessness

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
	"testing"
)

func TestIndexedRustDebugDiagnostics(t *testing.T) {
	base := NewIndexedLifecycleMonitor[int](RetentionLimits{4, 4, 4})
	keyFmt := func(k int) (string, error) { return fmt.Sprint(k), nil }
	strFmt := func(s string) (string, error) { return strconv.Quote(s), nil } // ASCII fixture
	m, err := WithIndexedRustDebugDiagnostics(base, "numeric-v1", keyFmt, strFmt)
	if err != nil {
		t.Fatal(err)
	}
	k1 := ScopedKey[int]{Epoch: 2, Generation: 1, Key: 3}
	k2 := ScopedKey[int]{Epoch: 2, Generation: 1, Key: 4}
	prior := LifecycleObligation{Deadline: 7, Settled: true, Published: true}
	h := LifecycleHistory[ScopedKey[int]]{Now: 8, RetiredBefore: 2,
		Obligations: []LifecycleEntry[ScopedKey[int]]{{k1, LifecycleObligation{Deadline: 7}}, {k2, LifecycleObligation{Deadline: 7}}},
		Resources:   []LifecycleResource[ScopedKey[int]]{{k1, true}},
		Violations:  []LifecycleViolation[ScopedKey[int]]{{Key: &k2, Code: "bad\ncode", Event: "observe", ObservedAt: 8, Prior: &prior}, {Code: "other", Event: "out", ObservedAt: 8}},
	}
	checks, err := m.Checks(h)
	if err != nil {
		t.Fatal(err)
	}
	retention := "RetentionStatus { active_obligations: 2, historical_obligations: 0, acquired_resources: 1, historical_resources: 0, oldest_unresolved: Some((ScopedKey { epoch: 2, generation: 1, key: 3 }, 7)), retired_before: 2 }"
	want := `violations=[Violation { key: Some(ScopedKey { epoch: 2, generation: 1, key: 4 }), code: "bad\ncode", event: "observe", observed_at: 8, prior: Some(Obligation { deadline: 7, cancelled: false, settled: true, published: true }) }, Violation { key: None, code: "other", event: "out", observed_at: 8, prior: None }]; retention=` + retention
	if checks[0].Details != want || checks[1].Details != "finite history: "+retention {
		t.Fatalf("unexpected checks: %+v", checks)
	}
	h.Violations = nil
	checks, err = m.Checks(h)
	if err != nil {
		t.Fatal(err)
	}
	if checks[0].Details != "violations=[]; retention="+retention {
		t.Fatal(checks[0])
	}
	old, err := base.Checks(h)
	if err != nil || old[0].Details != "missing_at_deadline" || old[1].Details != "2 pending obligations in finite history" {
		t.Fatalf("default changed: %+v %v", old, err)
	}
	oracle := IndexedRustDebugOracle[int, int, int](m, Identity("fixture", "test"), nil, nil)
	throughOracle, err := oracle.CheckState(h, 0)
	if err != nil || throughOracle[0].Details != "violations=[]; retention="+retention {
		t.Fatalf("oracle did not apply diagnostics: %+v %v", throughOracle, err)
	}
	metadata, err := oracle.Metadata()
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(metadata.Name, ";diagnostics=rust-debug-v1;formatter=10:numeric-v1") {
		t.Fatal(metadata.Name)
	}
}

func TestIndexedRustDebugFailureIsAtomic(t *testing.T) {
	base := NewIndexedLifecycleMonitor[int](RetentionLimits{4, 4, 4})
	_, err := WithIndexedRustDebugDiagnostics(base, "", func(int) (string, error) { return "", nil }, func(string) (string, error) { return "", nil })
	if err == nil {
		t.Fatal("empty identity accepted")
	}
	m, err := WithIndexedRustDebugDiagnostics(base, "broken", func(int) (string, error) { return "", errors.New("broken") }, func(s string) (string, error) { return strconv.Quote(s), nil })
	if err != nil {
		t.Fatal(err)
	}
	k := ScopedKey[int]{Key: 1}
	h := LifecycleHistory[ScopedKey[int]]{Obligations: []LifecycleEntry[ScopedKey[int]]{{k, LifecycleObligation{Deadline: 1}}}}
	checks, err := m.Checks(h)
	if err == nil || checks != nil {
		t.Fatalf("formatter failure returned partial checks: %+v %v", checks, err)
	}
}

func TestIndexedRustDebugFormatterCannotMutateRetainedObservation(t *testing.T) {
	base := NewIndexedLifecycleMonitor[int](RetentionLimits{4, 4, 4})
	k := ScopedKey[int]{Key: 1}
	h := LifecycleHistory[ScopedKey[int]]{Now: 1, Obligations: []LifecycleEntry[ScopedKey[int]]{{k, LifecycleObligation{Deadline: 1}}}}
	m, err := WithIndexedRustDebugDiagnostics(base, "mutator", func(int) (string, error) {
		h.Obligations[0].Obligation.Cancelled = true
		return "1", nil
	}, func(s string) (string, error) { return strconv.Quote(s), nil })
	if err != nil {
		t.Fatal(err)
	}
	checks, err := m.Checks(h)
	if err != nil || checks[0].Status != "failed" || checks[1].Status != "skipped" || !strings.Contains(checks[1].Details, "active_obligations: 1") {
		t.Fatalf("formatter altered observed history: %+v %v", checks, err)
	}
}

func TestIndexedRustDebugMatchesRustMonitorReference(t *testing.T) {
	base := NewIndexedLifecycleMonitor[uint8](RetentionLimits{1024, 1024, 1024})
	m, err := WithIndexedRustDebugDiagnostics(base, "u8-v1", func(k uint8) (string, error) { return fmt.Sprint(k), nil }, func(s string) (string, error) { return strconv.Quote(s), nil })
	if err != nil {
		t.Fatal(err)
	}
	k := ScopedKey[uint8]{Epoch: 2, Generation: 3, Key: 7}
	begin := LifecycleEvent[ScopedKey[uint8]]{Kind: "begin", Key: k, Time: 9}
	h, err := base.Advance(LifecycleHistory[ScopedKey[uint8]]{}, []LifecycleEvent[ScopedKey[uint8]]{begin, begin})
	if err != nil {
		t.Fatal(err)
	}
	checks, err := m.Checks(h)
	if err != nil {
		t.Fatal(err)
	}
	retention := "RetentionStatus { active_obligations: 1, historical_obligations: 0, acquired_resources: 0, historical_resources: 0, oldest_unresolved: Some((ScopedKey { epoch: 2, generation: 3, key: 7 }, 9)), retired_before: 0 }"
	want := `violations=[Violation { key: Some(ScopedKey { epoch: 2, generation: 3, key: 7 }), code: "key_reused", event: "begin", observed_at: 0, prior: Some(Obligation { deadline: 9, cancelled: false, settled: false, published: false }) }]; retention=` + retention
	if checks[0].Details != want || checks[1].Details != "finite history: "+retention {
		t.Fatalf("Rust monitor reference mismatch: %+v", checks)
	}
}
