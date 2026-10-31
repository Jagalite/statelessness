package statelessness

import (
	"reflect"
	"testing"
)

func TestLifecycleFenceAndDeadline(t *testing.T) {
	m := NewIndexedLifecycleMonitor[uint8](RetentionLimits{2, 2, 8})
	h := LifecycleHistory[ScopedKey[uint8]]{}
	for epoch := uint64(0); epoch < 1000; epoch++ {
		k := ScopedKey[uint8]{epoch, 0, 7}
		var e error
		h, e = m.Advance(h, []LifecycleEvent[ScopedKey[uint8]]{{Kind: "begin", Key: k, Time: 10}, {Kind: "acquire", Key: k}, {Kind: "settle", Key: k}, {Kind: "release", Key: k}, {Kind: "fence", Time: epoch + 1}})
		if e != nil {
			t.Fatal(e)
		}
		if len(h.Obligations) != 0 || len(h.Resources) != 0 {
			t.Fatal("unbounded retained details")
		}
	}
	k := ScopedKey[uint8]{0, 0, 7}
	h, e := m.Advance(h, []LifecycleEvent[ScopedKey[uint8]]{{Kind: "settle", Key: k}})
	if e != nil || h.Violations[0].Code != "retired_epoch_reference" {
		t.Fatal(h, e)
	}
	legacy := NewLifecycleMonitor[int](8)
	a, e := legacy.Advance(LifecycleHistory[int]{}, []LifecycleEvent[int]{{Kind: "begin", Key: 7, Time: 10}, {Kind: "tick", Time: 11}, {Kind: "cancel", Key: 7}})
	if e != nil || a.Violations[0].Code != "missing_before_advance" {
		t.Fatal(a, e)
	}
	a, e = legacy.Advance(LifecycleHistory[int]{}, []LifecycleEvent[int]{{Kind: "begin", Key: 7, Time: 10}, {Kind: "tick", Time: 10}, {Kind: "settle", Key: 7}})
	if e != nil || len(a.Violations) != 0 {
		t.Fatal(a, e)
	}
}
func TestNetworkExplicitScheduling(t *testing.T) {
	child := Model[int, int, int]{InitialState: func() (int, error) { return 0, nil }, Step: func(s, i int) (Transition[int, int], error) {
		return Transition[int, int]{s + i, []int{i}, Ignored("observed")}, nil
	}, CloneState: ValueCopy[int], CloneInput: ValueCopy[int], CloneOutput: ValueCopy[int], CheckState: func(int) ([]Check, error) { return []Check{Passed("safe")}, nil }, Inputs: func(int) (Iterator[int], error) { return Values([]int{1}), nil }, EqualStates: func(a, b int) bool { return a == b }}
	wiring := NetworkWiring[int, int, int]{Routes: func(o NetworkAddress[int]) ([]NetworkRoute[int], error) {
		return []NetworkRoute[int]{{(o.Machine + 1) % 3, 7, o.Value}}, nil
	}, CheckState: func(_ NetworkState[int, int], c *CheckSink) error { return c.Push(Passed("global")) }}
	policy := DefaultQueuePolicy()
	policy.MaxPending = 2
	policy.MaxPerChannel = 2
	policy.AllowDrop = true
	policy.AllowDuplicate = true
	n, e := NewNetwork([]NetworkAddress[Model[int, int, int]]{{2, child}, {0, child}, {1, child}}, wiring, policy)
	if e != nil {
		t.Fatal(e)
	}
	initial, _ := n.InitialState()
	tr, e := n.Step(initial, NetworkInput[int]{Kind: "local", Local: NetworkAddress[int]{0, 1}})
	if e != nil {
		t.Fatal(e)
	}
	if len(initial.Pending) != 0 || tr.Disposition.Kind != "ignored" {
		t.Fatal("ownership/disposition")
	}
	checks, e := n.CheckState(tr.State)
	ids := []string{}
	for _, c := range checks {
		ids = append(ids, c.ID)
	}
	if e != nil || !reflect.DeepEqual(ids, []string{"machine/0::safe", "machine/1::safe", "machine/2::safe", "global::global"}) {
		t.Fatal(checks, e)
	}
	duplicate, e := n.Step(tr.State, NetworkInput[int]{Kind: "duplicate"})
	if e != nil {
		t.Fatal(e)
	}
	if _, e = n.Step(duplicate.State, NetworkInput[int]{Kind: "deliver", Index: 1}); e == nil {
		t.Fatal("FIFO accepted")
	}
	if _, e = n.Step(duplicate.State, NetworkInput[int]{Kind: "duplicate"}); e == nil {
		t.Fatal("overflow accepted")
	}
	for i := 0; i < 100; i++ {
		tr, e = n.Step(tr.State, NetworkInput[int]{Kind: "deliver"})
		if e != nil || len(tr.State.Pending) != 1 {
			t.Fatal(tr, e)
		}
	}
}
func TestLifecycleGoldenCodec(t *testing.T) {
	legacy := NewLifecycleMonitor[uint8](8)
	c, e := LifecycleHistoryCodec(legacy, Uint8Codec())
	if e != nil {
		t.Fatal(e)
	}
	b, e := EncodeValue(c, LifecycleHistory[uint8]{}, 1024)
	if e != nil || len(b) != 20 {
		t.Fatal(b, e)
	}
	for _, v := range b {
		if v != 0 {
			t.Fatal("legacy zero golden")
		}
	}
	key, e := ScopedKeyCodec(Uint8Codec())
	if e != nil {
		t.Fatal(e)
	}
	indexed := NewIndexedLifecycleMonitor[uint8](RetentionLimits{8, 8, 8})
	ic, e := LifecycleHistoryCodec(indexed, key)
	if e != nil {
		t.Fatal(e)
	}
	h := LifecycleHistory[ScopedKey[uint8]]{}
	b, e = EncodeValue(ic, h, 1024)
	if e != nil || len(b) != 29 || b[0] != 1 {
		t.Fatal(b, e)
	}
	h, e = indexed.Advance(h, []LifecycleEvent[ScopedKey[uint8]]{{Kind: "begin", Key: ScopedKey[uint8]{2, 3, 7}, Time: 10}})
	if e != nil {
		t.Fatal(e)
	}
	b, e = EncodeValue(ic, h, 1024)
	if e != nil || len(b) != 57 {
		t.Fatal(b, e)
	}
	restored, e := FromValueBytes(ic, b, DefaultDecodeLimits())
	if e != nil || restored.Obligations[0] != h.Obligations[0] {
		t.Fatal(restored, e)
	}
	if _, e = FromValueBytes(ic, append(b, 0), DefaultDecodeLimits()); e == nil {
		t.Fatal("trailing bytes accepted")
	}
}
func TestLegacyRawStringPreserved(t *testing.T) {
	m := NewLifecycleMonitor[uint8](8)
	c, e := LifecycleHistoryCodec(m, Uint8Codec())
	if e != nil {
		t.Fatal(e)
	}
	text := "time_backwards"
	h := LifecycleHistory[uint8]{Violations: []LifecycleViolation[uint8]{{Legacy: &text, Code: text}}}
	b, e := EncodeValue(c, h, 1024)
	if e != nil {
		t.Fatal(e)
	}
	h, e = FromValueBytes(c, b, DefaultDecodeLimits())
	if e != nil {
		t.Fatal(e)
	}
	again, e := EncodeValue(c, h, 1024)
	if e != nil || !reflect.DeepEqual(b, again) {
		t.Fatal("historical text changed", e)
	}
}
func TestPairExplicitHandoff(t *testing.T) {
	child := Model[int, int, int]{InitialState: func() (int, error) { return 0, nil }, Step: func(s, i int) (Transition[int, int], error) { return Accept[int, int](s+i, i), nil }, CloneState: ValueCopy[int], CloneInput: ValueCopy[int], CloneOutput: ValueCopy[int], CheckState: func(int) ([]Check, error) { return []Check{Passed("safe")}, nil }, Inputs: func(int) (Iterator[int], error) { return Values([]int{1}), nil }, EqualStates: func(a, b int) bool { return a == b }}
	w := PairWiring[int, int, int, int, int, int]{Messages: func(o PairAddress[int, int]) ([]PairAddress[int, int], error) {
		if o.Side == "left" {
			return []PairAddress[int, int]{PairRight[int, int](o.Left)}, nil
		}
		return []PairAddress[int, int]{PairLeft[int, int](o.Right)}, nil
	}, CheckState: func(_ PairState[int, int, int, int], c *CheckSink) error { return c.Push(Passed("global")) }}
	p, e := NewPair(child, child, w, 1, func(a, b PairAddress[int, int]) bool { return a == b })
	if e != nil {
		t.Fatal(e)
	}
	s, _ := p.InitialState()
	tr, e := p.Step(s, PairInput[int, int]{Kind: "local", Address: PairLeft[int, int](1)})
	if e != nil || tr.State.Left != 1 || tr.State.Right != 0 || len(tr.State.Pending) != 1 {
		t.Fatal(tr, e)
	}
	after, e := p.Step(tr.State, PairInput[int, int]{Kind: "deliver"})
	if e != nil || after.State.Right != 1 || len(after.State.Pending) != 1 {
		t.Fatal(after, e)
	}
	if p.EqualStates(tr.State, after.State) {
		t.Fatal("state identity ignored handoff")
	}
}
func TestFencedOracleRestartIdentity(t *testing.T) {
	m := NewIndexedLifecycleMonitor[uint8](RetentionLimits{2, 2, 8})
	k := ScopedKey[uint8]{2, 3, 7}
	h, e := m.Advance(LifecycleHistory[ScopedKey[uint8]]{}, []LifecycleEvent[ScopedKey[uint8]]{{Kind: "begin", Key: k, Time: 10}, {Kind: "settle", Key: k}, {Kind: "fence", Time: 3}})
	if e != nil {
		t.Fatal(e)
	}
	key, e := ScopedKeyCodec(Uint8Codec())
	if e != nil {
		t.Fatal(e)
	}
	c, e := LifecycleHistoryCodec(m, key)
	if e != nil {
		t.Fatal(e)
	}
	b, e := EncodeValue(c, h, 1024)
	if e != nil {
		t.Fatal(e)
	}
	restored, e := FromValueBytes(c, b, DefaultDecodeLimits())
	if e != nil {
		t.Fatal(e)
	}
	oracle := LifecycleOracle[uint8, uint8, uint8](m, Identity("fixture", "fixture"), func(uint8, Disposition) ([]LifecycleEvent[ScopedKey[uint8]], error) { return nil, nil }, func([]uint8) ([]LifecycleEvent[ScopedKey[uint8]], error) { return nil, nil })
	if !oracle.EqualHistory(h, restored) {
		t.Fatal("nil/empty history changed restart identity")
	}
}
func TestAcknowledgedOwnershipHandoff(t *testing.T) {
	makeNetwork := func(fault bool) *Network[int, int, int] {
		machines := []NetworkAddress[Model[int, int, int]]{}
		for _, id := range []MachineID{10, 20, 30} {
			role := id
			m := Model[int, int, int]{InitialState: func() (int, error) {
				if role == 20 {
					return 1, nil
				}
				return 0, nil
			}, Step: func(s, i int) (Transition[int, int], error) {
				if role == 10 && i == 1 {
					if fault {
						return Accept[int, int](1, 10, 11), nil
					}
					return Accept[int, int](1, 10), nil
				}
				if role == 10 && i == 4 {
					return Accept[int, int](2, 11), nil
				}
				if role == 20 && i == 2 {
					return Accept[int, int](0, 12), nil
				}
				if role == 30 && i == 3 {
					return Accept[int, int](1), nil
				}
				return Accept[int, int](s), nil
			}, CloneState: ValueCopy[int], CloneInput: ValueCopy[int], CloneOutput: ValueCopy[int], CheckState: func(int) ([]Check, error) { return nil, nil }}
			machines = append(machines, NetworkAddress[Model[int, int, int]]{id, m})
		}
		w := NetworkWiring[int, int, int]{Routes: func(o NetworkAddress[int]) ([]NetworkRoute[int], error) {
			r := map[int]NetworkRoute[int]{10: {20, 0, 2}, 11: {30, 0, 3}, 12: {10, 0, 4}}
			return []NetworkRoute[int]{r[o.Value]}, nil
		}, CheckState: func(s NetworkState[int, int], c *CheckSink) error {
			if s.Machines[1].Value+s.Machines[2].Value > 1 {
				return c.Push(Failed("unique-owner", "two owners"))
			}
			return c.Push(Passed("unique-owner"))
		}}
		n, e := NewNetwork(machines, w, DefaultQueuePolicy())
		if e != nil {
			t.Fatal(e)
		}
		return n
	}
	safe := makeNetwork(false)
	initial, _ := safe.InitialState()
	tr, e := safe.Step(initial, NetworkInput[int]{Kind: "local", Local: NetworkAddress[int]{10, 1}})
	if e != nil {
		t.Fatal(e)
	}
	for i := 0; i < 3; i++ {
		tr, e = safe.Step(tr.State, NetworkInput[int]{Kind: "deliver"})
		if e != nil {
			t.Fatal(e)
		}
		checks, e := safe.CheckState(tr.State)
		if e != nil || checks[0].Status != "passed" {
			t.Fatal(checks, e)
		}
	}
	bad := makeNetwork(true)
	initial, _ = bad.InitialState()
	tr, e = bad.Step(initial, NetworkInput[int]{Kind: "local", Local: NetworkAddress[int]{10, 1}})
	if e != nil {
		t.Fatal(e)
	}
	tr, e = bad.Step(tr.State, NetworkInput[int]{Kind: "deliver", Index: 1})
	if e != nil {
		t.Fatal(e)
	}
	checks, e := bad.CheckState(tr.State)
	if e != nil || checks[0].Status != "failed" {
		t.Fatal(checks, e)
	}
}

func TestLegacyLifecycleRejectsIndexedOnlyState(t *testing.T) {
	m := NewLifecycleMonitor[uint8](8)
	codec, err := LifecycleHistoryCodec(m, Uint8Codec())
	if err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name    string
		history LifecycleHistory[uint8]
	}{
		{"retirement fence", LifecycleHistory[uint8]{RetiredBefore: 1}},
		{"published obligation", LifecycleHistory[uint8]{Obligations: []LifecycleEntry[uint8]{{Key: 1, Obligation: LifecycleObligation{Settled: true, Published: true}}}}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if err := m.Validate(tc.history); err == nil {
				t.Error("validation accepted indexed-only state")
			}
			if _, err := m.Advance(tc.history, nil); err == nil {
				t.Error("advance accepted indexed-only state")
			}
			if _, err := EncodeValue(codec, tc.history, 1024); err == nil {
				t.Error("legacy encoding silently lost indexed-only state")
			}
		})
	}
}

func TestLegacyLifecycleDuplicateKeysUseAnyForPublishAndRelease(t *testing.T) {
	m := NewLifecycleMonitor[uint8](8)
	h := LifecycleHistory[uint8]{
		Obligations: []LifecycleEntry[uint8]{
			{Key: 1, Obligation: LifecycleObligation{Deadline: 10, Cancelled: true}},
			{Key: 1, Obligation: LifecycleObligation{Deadline: 10, Settled: true}},
			{Key: 1, Obligation: LifecycleObligation{Deadline: 10}},
		},
		Resources: []LifecycleResource[uint8]{{Key: 1, Acquired: true}},
	}
	published, err := m.Advance(h, []LifecycleEvent[uint8]{{Kind: "publish", Key: 1}})
	if err != nil {
		t.Fatal(err)
	}
	if len(published.Violations) != 0 {
		t.Fatalf("publish should accept any settled noncancelled duplicate: %+v", published.Violations)
	}
	released, err := m.Advance(h, []LifecycleEvent[uint8]{{Kind: "release", Key: 1}})
	if err != nil {
		t.Fatal(err)
	}
	if len(released.Violations) != 1 || released.Violations[0].Code != "release_before_settlement" {
		t.Fatalf("release should report any pending duplicate: %+v", released.Violations)
	}
	if released.Resources[0].Acquired {
		t.Fatal("release must still release first matching resource")
	}
}
