package debug

import (
	"math"
	"strings"
	"testing"
)

func TestDiagnosticDroppedCountsSaturate(t *testing.T) {
	limits := DefaultDiagnosticLimits()
	limits.MaxTurnRecords = 0
	hub, err := NewDiagnostics(limits)
	if err != nil {
		t.Fatal(err)
	}
	sink, err := hub.AddSink(AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	if err = hub.RegisterProducer(1, 1); err != nil {
		t.Fatal(err)
	}
	queue := hub.sinks[sink].queue
	queue.dropped, queue.droppedTotal = math.MaxUint64, math.MaxUint64
	if queue.push(WatchEvent{Kind: strings.Repeat("x", int(queue.limits.MaxEventBytes)+1)}) {
		t.Fatal("oversized record admitted")
	}
	scope, err := hub.BeginTurn(CaptureContext{Producer: 1, Origin: "simulation", Snapshot: SnapshotID{Sequence: 1}})
	if err != nil {
		t.Fatal(err)
	}
	hub.emitDiagnostic(sink, WatchEvent{Kind: "marker"})
	if err = scope.Finish(false); err != nil {
		t.Fatal(err)
	}
	if queue.dropped != math.MaxUint64 || queue.droppedTotal != math.MaxUint64 {
		t.Fatal("loss counters wrapped")
	}
	queue.observationGaps = math.MaxUint64
	if err = queue.Gap(WatchContext{}); err != nil || queue.observationGaps != math.MaxUint64 {
		t.Fatal("gap counter wrapped", err)
	}
}

type nativePathFixture struct{ segment Segment }

type panickingFormatError struct{}

func (panickingFormatError) Error() string { panic("host formatting failure") }

type panickingErrorProjection struct{}

func (panickingErrorProjection) DisplaySchema() DisplaySchema {
	return DisplaySchema{Name: "fixture", Version: 1}
}
func (panickingErrorProjection) Inspect([]Segment, *InspectContext) (InspectNode, error) {
	return InspectNode{}, panickingFormatError{}
}

func TestWatchHandlesPanickingHostErrorFormatter(t *testing.T) {
	q, err := NewWatchRegistry(DefaultWatchLimits(), AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	if _, err = q.Add(ChangedWatch(nil)); err != nil {
		t.Fatal(err)
	}
	if err = q.Observe(WatchContext{Snapshot: SnapshotID{Sequence: 1}}, func() InspectAdapter { return panickingErrorProjection{} }); err != nil {
		t.Fatal(err)
	}
	events, err := q.Drain()
	if err != nil || len(events) < 1 || events[len(events)-1].Kind != "inspection_error" || events[len(events)-1].InspectionError != "InvalidMetadata" {
		t.Fatal(events, err)
	}
	if code := watchProtocolError(panickingFormatError{}); code != "callback" {
		t.Fatal("wrong protocol classification", code)
	}
}

func TestWatchContainsAdapterFactoryPanicOncePerObservation(t *testing.T) {
	q, err := NewWatchRegistry(DefaultWatchLimits(), AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		if _, err = q.Add(ChangedWatch(nil)); err != nil {
			t.Fatal(err)
		}
	}
	if _, err = q.Drain(); err != nil {
		t.Fatal(err)
	}
	calls := 0
	if err = q.Observe(WatchContext{Snapshot: SnapshotID{Sequence: 1}}, func() InspectAdapter { calls++; panic("host failure") }); err != nil {
		t.Fatal(err)
	}
	events, err := q.Drain()
	if err != nil || calls != 1 || len(events) != 2 || events[0].Kind != "inspection_error" || events[1].Kind != "inspection_error" || q.inspectionFailures != 2 {
		t.Fatal("factory failure was not isolated", calls, events, err)
	}
}

type recoveredFixtureScalar struct{ evictionScalar }

func (recoveredFixtureScalar) DisplaySchema() DisplaySchema {
	return DisplaySchema{Name: "fixture", Version: 1}
}

func TestWatchInspectionFailureRespectsTriggerAndSampling(t *testing.T) {
	for _, tc := range []struct {
		name, trigger string
		interval      uint64
		want          int
	}{
		{"equals filters unavailable", "equals", 1, 0},
		{"changed samples unavailable", "changed", 2, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			q, err := NewWatchRegistry(DefaultWatchLimits(), AllowAllWatches())
			if err != nil {
				t.Fatal(err)
			}
			config := ChangedWatch(nil)
			config.Trigger, config.DeliveryInterval = tc.trigger, tc.interval
			if tc.trigger == "equals" {
				config.Equals = &Scalar{Kind: "u64", Decimal: "7"}
			}
			ack, err := q.Add(config)
			if err != nil {
				t.Fatal(err)
			}
			if _, err = q.Drain(); err != nil {
				t.Fatal(err)
			}
			for seq := uint64(1); seq <= 2; seq++ {
				if err = q.Observe(WatchContext{Snapshot: SnapshotID{Sequence: seq}}, func() InspectAdapter { return panickingErrorProjection{} }); err != nil {
					t.Fatal(err)
				}
			}
			events, err := q.Drain()
			if err != nil || len(events) != tc.want || q.inspectionFailures != 2 {
				t.Fatal(events, err, q.inspectionFailures)
			}
			if current := q.entries[ack.WatchID].current; current == nil || current.Kind != "inspection_error" || current.Snapshot.Sequence != 2 {
				t.Fatal("missing current failure", current)
			}
			if err = q.Observe(WatchContext{Snapshot: SnapshotID{Sequence: 3}}, func() InspectAdapter { return recoveredFixtureScalar{} }); err != nil {
				t.Fatal(err)
			}
			if current := q.entries[ack.WatchID].current; current == nil || current.Kind != "baseline_observation_gap" {
				t.Fatal("recovery lost gap", current)
			}
		})
	}
}

func TestWatchGapInvalidatesFilteredBaseline(t *testing.T) {
	q, err := NewWatchRegistry(DefaultWatchLimits(), AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	config := ChangedWatch(nil)
	config.Selector.Model = "selected"
	ack, err := q.Add(config)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = q.Drain(); err != nil {
		t.Fatal(err)
	}
	observe := func(seq uint64, model string) {
		t.Helper()
		if err := q.Observe(WatchContext{Snapshot: SnapshotID{Session: 1, Sequence: seq}, Model: model}, func() InspectAdapter { return evictionScalar{} }); err != nil {
			t.Fatal(err)
		}
	}
	observe(1, "selected")
	if _, err = q.Drain(); err != nil {
		t.Fatal(err)
	}
	observe(3, "other")
	events, err := q.Drain()
	if err != nil || len(events) != 1 || events[0].Kind != "observation_gap" || q.observationGaps != 1 {
		t.Fatal(events, err)
	}
	observe(4, "selected")
	current := q.entries[ack.WatchID].current
	if current == nil || current.Kind != "baseline_observation_gap" {
		t.Fatal("gap lost through selector", current)
	}
	before := q.entries[ack.WatchID].evaluated
	observe(4, "selected")
	if q.entries[ack.WatchID].evaluated != before {
		t.Fatal("stale observation was evaluated")
	}
}

func (nativePathFixture) DisplaySchema() DisplaySchema {
	return DisplaySchema{Name: "fixture", Version: 1}
}
func (f nativePathFixture) Inspect(path []Segment, cx *InspectContext) (InspectNode, error) {
	fields := []InspectField{{Segment: f.segment, Label: "value", Value: func(rest []Segment, cx *InspectContext) (InspectNode, error) {
		return cx.Scalar(rest, Scalar{Kind: "u64", Decimal: "7"})
	}}}
	switch f.segment.Kind {
	case "field":
		return cx.Object(path, "fixture", fields)
	case "index":
		return cx.SequenceIndexed(path, "fixture", f.segment.Index+1, func(_ uint64, rest []Segment, cx *InspectContext) (InspectNode, error) {
			return cx.Scalar(rest, Scalar{Kind: "u64", Decimal: "7"})
		})
	default:
		return cx.Map(path, "stable", fields)
	}
}

func TestProtocolPathMatchesNativeSegments(t *testing.T) {
	cases := []struct {
		wire   string
		native Segment
	}{
		{"field:name", FieldPath("name")},
		{"index:3", IndexPath(3)},
		{"key.string:name", Segment{Kind: "map_key", KeyType: "string", Name: "name"}},
		{"key.char:x", Segment{Kind: "map_key", KeyType: "char", Name: "x"}},
		{"key.u64:17", Segment{Kind: "map_key", KeyType: "u64", Decimal: "17"}},
		{"key.bool:true", Segment{Kind: "map_key", KeyType: "bool", Decimal: "true"}},
	}
	for _, tc := range cases {
		path := protocolPath([]string{tc.wire})
		if len(path) != 1 || path[0] != tc.native {
			t.Fatalf("%s decoded as %#v", tc.wire, path)
		}
		query := DefaultInspectQuery()
		query.Path = path
		result, err := Inspect(nativePathFixture{segment: tc.native}, query)
		if err != nil || result.Node.Scalar == nil || result.Node.Scalar.Decimal != "7" {
			t.Fatalf("%s failed inspection: %#v %v", tc.wire, result.Node, err)
		}
	}
}

func TestProtocolNodeUsesReferenceFieldOrder(t *testing.T) {
	count := uint64(1)
	node := InspectNode{Kind: "sequence", Name: "list", ChildCount: &count, Page: &PageInfo{Returned: 1, Total: 1}, Children: []ChildNode{{Segment: IndexPath(0), Label: "item", Node: InspectNode{Kind: "scalar", Scalar: &Scalar{Kind: "u64", Decimal: "7"}}}}}
	fields, err := protocolNode("node", node)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"node.completeness", "node.child_count", "node.page.offset", "node.page.returned", "node.page.total", "node.page.next", "node.kind", "node.value", "node.children.returned", "node.children.0.segment", "node.children.0.label", "node.children.0.node.completeness", "node.children.0.node.child_count", "node.children.0.node.kind", "node.children.0.node.value", "node.children.0.node.children.returned"}
	if len(fields) != len(want) {
		t.Fatal(fields)
	}
	for i, key := range want {
		if fields[i].Key != key {
			t.Fatalf("field %d: got %s want %s", i, fields[i].Key, key)
		}
	}
}

func TestProtocolNodeRejectsExcessiveDepth(t *testing.T) {
	node := InspectNode{Kind: "scalar", Scalar: &Scalar{Kind: "u64", Decimal: "1"}}
	for i := 0; i < 33; i++ {
		node = InspectNode{Kind: "sequence", Children: []ChildNode{{Segment: IndexPath(0), Node: node}}}
	}
	if _, err := protocolNode("node", node); err != ProtocolError("limit") {
		t.Fatal("excessive depth accepted", err)
	}
}

func TestSubscribeDuringCaptureDoesNotReclaimState(t *testing.T) {
	hub, err := NewDiagnostics(DefaultDiagnosticLimits())
	if err != nil {
		t.Fatal(err)
	}
	hub.subscriptions[1] = &probeEntry{removedRevision: 1, registries: map[uint64]*WatchRegistry{}, markerCounts: map[uint64]uint64{}, pendingReasons: map[uint64]string{}}
	hub.busy = true
	if _, err := hub.Subscribe(0, ProbeSubscription{}); err == nil {
		t.Fatal("active capture accepted subscription")
	}
	if hub.subscriptions[1] == nil {
		t.Fatal("rejected reentrant subscription reclaimed state")
	}
}

func TestScopeContextLabelAdmissionIsAtomic(t *testing.T) {
	hub, err := NewDiagnostics(DefaultDiagnosticLimits())
	if err != nil {
		t.Fatal(err)
	}
	if err = hub.RegisterProducer(1, 1); err != nil {
		t.Fatal(err)
	}
	ctx := CaptureContext{Producer: 1, Origin: "simulation", Model: strings.Repeat("é", 129), Snapshot: SnapshotID{Sequence: 1}}
	if _, err = hub.BeginTurn(ctx); err == nil {
		t.Fatal("oversized UTF-8 label accepted")
	}
	ctx.Model = string([]byte{0xff})
	if _, err = hub.BeginTurn(ctx); err == nil {
		t.Fatal("invalid UTF-8 accepted")
	}
	if hub.producers[1].observed || hub.scope != nil || hub.busy {
		t.Fatal("rejected admission changed producer")
	}
	ctx.Model = strings.Repeat("é", 128)
	scope, err := hub.BeginTurn(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err = scope.Finish(true); err != nil {
		t.Fatal(err)
	}
}

func TestDiagnosticSelectorLabelAdmissionIsAtomic(t *testing.T) {
	hub, err := NewDiagnostics(DefaultDiagnosticLimits())
	if err != nil {
		t.Fatal(err)
	}
	sink, err := hub.AddSink(AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	config := ProbeSubscription{Sink: sink, Site: "value", Kind: "probe", Scope: ScopeSelector{Model: strings.Repeat("é", 129)}, Watch: ChangedWatch(nil)}
	if _, err = hub.ConfigureSubscriptions(0, []ProbeSubscription{config}); err == nil {
		t.Fatal("oversized selector accepted")
	}
	if hub.revision != 0 || len(hub.subscriptions) != 0 {
		t.Fatal("rejected selector mutated configuration")
	}
	config.Scope.Model = ""
	config.Scope.Origin = "nonexistent"
	if _, err = hub.ConfigureSubscriptions(0, []ProbeSubscription{config}); err == nil {
		t.Fatal("invalid selector origin accepted")
	}
	config.Scope.Origin = ""
	config.Site = string([]byte{0xff})
	if _, err = hub.ConfigureSubscriptions(0, []ProbeSubscription{config}); err == nil {
		t.Fatal("invalid UTF-8 site accepted")
	}
	if hub.revision != 0 || len(hub.subscriptions) != 0 {
		t.Fatal("invalid configuration mutated state")
	}
	config.Site = "value"
	config.Scope.Model = strings.Repeat("é", 128)
	if _, err = hub.ConfigureSubscriptions(0, []ProbeSubscription{config}); err != nil {
		t.Fatal(err)
	}
}

type evictionScalar struct{}

func (evictionScalar) DisplaySchema() DisplaySchema {
	return DisplaySchema{Name: "counter", Version: 1}
}
func (evictionScalar) Inspect(path []Segment, cx *InspectContext) (InspectNode, error) {
	return cx.Scalar(path, Scalar{Kind: "u64", Decimal: "7"})
}

func TestDiagnosticEvictionPreservesTriggerAndNextBaseline(t *testing.T) {
	limits := DefaultDiagnosticLimits()
	limits.MaxProbeStates = 1
	limits.Watch.MaxBaselineBytes = 64
	hub, err := NewDiagnostics(limits)
	if err != nil {
		t.Fatal(err)
	}
	sink, err := hub.AddSink(AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	config := ProbeSubscription{Sink: sink, Site: "value", Kind: "probe", Watch: ChangedWatch(nil)}
	if _, err = hub.ConfigureSubscriptions(0, []ProbeSubscription{config}); err != nil {
		t.Fatal(err)
	}
	if err = hub.RegisterProducer(1, 1); err != nil {
		t.Fatal(err)
	}
	if _, err = hub.Acknowledge(1, 1, 1); err != nil {
		t.Fatal(err)
	}
	for seq := uint64(1); seq <= 2; seq++ {
		scope, e := hub.BeginTurn(CaptureContext{Producer: 1, Origin: "simulation", Snapshot: SnapshotID{Sequence: seq}})
		if e != nil {
			t.Fatal(e)
		}
		if e = scope.Probe("value", InfoSeverity, func() InspectAdapter { return evictionScalar{} }); e != nil {
			t.Fatal(e)
		}
		if e = scope.Finish(true); e != nil {
			t.Fatal(e)
		}
	}
	q := hub.sinks[sink].queue
	if len(q.ring) != 2 || q.ring[0].Diagnostic.BaselineReason != "initial" || q.ring[1].Diagnostic.BaselineReason != "evicted" || hub.sinks[sink].baselineEvicted != 2 {
		t.Fatal("incorrect eviction behavior", q.ring, hub.sinks[sink].baselineEvicted)
	}
}

func TestDiagnosticEvictionDoesNotForceFilteredEvent(t *testing.T) {
	limits := DefaultDiagnosticLimits()
	limits.MaxProbeStates = 1
	limits.Watch.MaxBaselineBytes = 64
	hub, err := NewDiagnostics(limits)
	if err != nil {
		t.Fatal(err)
	}
	sink, err := hub.AddSink(AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	watch := ChangedWatch(nil)
	watch.Trigger = "equals"
	watch.Equals = &Scalar{Kind: "u64", Decimal: "1"}
	if _, err = hub.ConfigureSubscriptions(0, []ProbeSubscription{{Sink: sink, Site: "value", Kind: "probe", Watch: watch}}); err != nil {
		t.Fatal(err)
	}
	if err = hub.RegisterProducer(1, 1); err != nil {
		t.Fatal(err)
	}
	if _, err = hub.Acknowledge(1, 1, 1); err != nil {
		t.Fatal(err)
	}
	scope, err := hub.BeginTurn(CaptureContext{Producer: 1, Origin: "simulation", Snapshot: SnapshotID{Sequence: 1}})
	if err != nil {
		t.Fatal(err)
	}
	if err = scope.Probe("value", InfoSeverity, func() InspectAdapter { return evictionScalar{} }); err != nil {
		t.Fatal(err)
	}
	if err = scope.Finish(true); err != nil {
		t.Fatal(err)
	}
	if len(hub.sinks[sink].queue.ring) != 0 || hub.sinks[sink].filtered != 1 || hub.sinks[sink].baselineEvicted != 1 {
		t.Fatal("eviction forced a filtered event", hub.sinks[sink])
	}
}

func TestDiagnosticPreviewAndCommitOwnQueue(t *testing.T) {
	hub, err := NewDiagnostics(DefaultDiagnosticLimits())
	if err != nil {
		t.Fatal(err)
	}
	id, err := hub.AddSink(AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	q := hub.sinks[id].queue
	q.push(WatchEvent{Kind: "first", Node: &InspectNode{Kind: "bytes", Bytes: []byte{1, 2}, TotalBytes: 2}, Path: []Segment{{Kind: "field", Name: "name"}}, FailingProperties: []string{"failure"}})
	q.push(WatchEvent{Kind: "second"})
	before := q.ringBytes
	page, err := hub.previewSink(id, 1)
	if err != nil || len(page) != 1 {
		t.Fatal(page, err)
	}
	page[0].Path[0].Name = "changed"
	page[0].FailingProperties[0] = "changed"
	page[0].Node.Bytes[0] = 9
	if len(q.ring) != 2 || q.ringBytes != before || q.ring[0].Path[0].Name != "name" || q.ring[0].FailingProperties[0] != "failure" || q.ring[0].Node.Bytes[0] != 1 {
		t.Fatal("preview changed queue")
	}
	if err = hub.consumeSink(id, 3); err == nil || len(q.ring) != 2 {
		t.Fatal("invalid commit changed queue", err)
	}
	if err = hub.consumeSink(id, 1); err != nil || len(q.ring) != 1 || q.ring[0].Kind != "second" || q.ringBytes != retainedEvent(q.ring[0]) {
		t.Fatal("incorrect commit", err)
	}
	hub.busy = true
	if _, err = hub.previewSink(id, 1); err == nil {
		t.Fatal("active capture allowed preview")
	}
	hub.busy = false
}

// Previews share the queue's admission copy, so every retained field, including
// the failure baseline reason, survives a non-destructive protocol read.
func TestDiagnosticPreviewPreservesEveryQueuedField(t *testing.T) {
	q, err := NewWatchRegistry(DefaultWatchLimits(), AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	q.push(WatchEvent{Kind: "inspection_error", FailureBaselineReason: "inspection_incomplete", Diagnostic: &DiagnosticEventMetadata{Kind: "probe", BaselineReason: "inspection_incomplete"}})
	page, err := previewDiagnosticQueue(q, 1)
	if err != nil || len(page) != 1 || page[0].FailureBaselineReason != "inspection_incomplete" || page[0].Diagnostic == nil || page[0].Diagnostic == q.ring[0].Diagnostic {
		t.Fatal("preview omitted or aliased a retained field", page, err)
	}
}

func TestWatchQueueOwnsRetainedMetadata(t *testing.T) {
	q, err := NewWatchRegistry(DefaultWatchLimits(), AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	path := []Segment{{Kind: "field", Name: "original"}}
	properties := []string{"failure"}
	previous := SnapshotID{Sequence: 3}
	if !q.push(WatchEvent{Path: path, FailingProperties: properties, PreviousSnapshot: &previous}) {
		t.Fatal("admission failed")
	}
	path[0].Name = "changed"
	properties[0] = "changed"
	previous.Sequence = 4
	if q.ring[0].Path[0].Name != "original" || q.ring[0].FailingProperties[0] != "failure" || q.ring[0].PreviousSnapshot.Sequence != 3 {
		t.Fatal("queue retained caller-owned metadata", q.ring[0])
	}
}

func TestDiagnosticScopeUsesIndependentTurnBudgets(t *testing.T) {
	for _, tc := range []struct {
		name    string
		records int
		bytes   uint64
		want    int
	}{
		{"record limit", 1, 1 << 20, 1},
		{"byte limit", 10, 1, 0},
		{"zero record limit", 0, 1 << 20, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			limits := DefaultDiagnosticLimits()
			limits.MaxTurnRecords, limits.MaxTurnBytes = tc.records, tc.bytes
			hub, err := NewDiagnostics(limits)
			if err != nil {
				t.Fatal(err)
			}
			sink, err := hub.AddSink(AllowAllWatches())
			if err != nil {
				t.Fatal(err)
			}
			if err = hub.RegisterProducer(1, 1); err != nil {
				t.Fatal(err)
			}
			scope, err := hub.BeginTurn(CaptureContext{Producer: 1, Origin: "simulation", Snapshot: SnapshotID{Sequence: 1}})
			if err != nil {
				t.Fatal(err)
			}
			for i := 0; i < 2; i++ {
				hub.emitDiagnostic(sink, WatchEvent{Kind: "marker", Diagnostic: &DiagnosticEventMetadata{}})
			}
			q := hub.sinks[sink].queue
			if len(q.ring) != 0 || len(scope.staged) != tc.want || q.droppedTotal != uint64(2-tc.want) {
				t.Fatalf("before finish: queued=%d staged=%d dropped=%d", len(q.ring), len(scope.staged), q.droppedTotal)
			}
			if hub.sinks[sink].captured != 0 {
				t.Fatal("staged records counted as captured")
			}
			if _, err = hub.BeginTurn(CaptureContext{}); err == nil {
				t.Fatal("nested turn allowed")
			}
			if err = scope.Finish(false); err != nil {
				t.Fatal(err)
			}
			if len(q.ring) != tc.want {
				t.Fatalf("published %d records", len(q.ring))
			}
			if hub.sinks[sink].captured != uint64(tc.want) {
				t.Fatal("incorrect captured count", hub.sinks[sink].captured)
			}
			if tc.want != 0 && (!q.ring[0].Diagnostic.TurnCompletenessKnown || !q.ring[0].Diagnostic.IncompleteTurn) {
				t.Fatal("incomplete turn metadata missing")
			}
		})
	}
}

func TestScopePendingContinuityCapacityIsAtomic(t *testing.T) {
	limits := DefaultDiagnosticLimits()
	limits.MaxProbeStates = 1
	hub, err := NewDiagnostics(limits)
	if err != nil {
		t.Fatal(err)
	}
	sink, err := hub.AddSink(AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	configs := []ProbeSubscription{
		{Sink: sink, Site: "a", Kind: "probe", Scope: ScopeSelector{Entity: "selected"}, Watch: ChangedWatch(nil)},
		{Sink: sink, Site: "b", Kind: "probe", Scope: ScopeSelector{Entity: "selected"}, Watch: ChangedWatch(nil)},
	}
	if _, err = hub.ConfigureSubscriptions(0, configs); err != nil {
		t.Fatal(err)
	}
	if err = hub.RegisterProducer(1, 1); err != nil {
		t.Fatal(err)
	}
	if _, err = hub.Acknowledge(1, 1, 1); err != nil {
		t.Fatal(err)
	}
	ctx := CaptureContext{Producer: 1, Entity: "other", Origin: "simulation", Snapshot: SnapshotID{Sequence: 1}}
	if _, err = hub.BeginTurn(ctx); err == nil {
		t.Fatal("expected pending-state capacity failure")
	}
	if hub.probeStates != 0 || hub.producers[1].observed || hub.scope != nil || hub.busy {
		t.Fatal("failed turn mutated state")
	}
	for _, entry := range hub.subscriptions {
		if len(entry.pendingReasons) != 0 {
			t.Fatal("pending state leaked")
		}
	}
}

func TestDropScopeBeforeFirstProbeRetainsGapAndReservesAtomically(t *testing.T) {
	for _, capacity := range []int{1, 2} {
		limits := DefaultDiagnosticLimits()
		limits.MaxProbeStates = capacity
		hub, err := NewDiagnostics(limits)
		if err != nil {
			t.Fatal(err)
		}
		sink, err := hub.AddSink(AllowAllWatches())
		if err != nil {
			t.Fatal(err)
		}
		configs := []ProbeSubscription{
			{Sink: sink, Site: "site", Kind: "probe", Watch: ChangedWatch(nil)},
			{Sink: sink, Site: "site", Kind: "probe", Watch: ChangedWatch(nil)},
		}
		if _, err = hub.ConfigureSubscriptions(0, configs); err != nil {
			t.Fatal(err)
		}
		if err = hub.RegisterProducer(1, 1); err != nil {
			t.Fatal(err)
		}
		if _, err = hub.Acknowledge(1, 1, 1); err != nil {
			t.Fatal(err)
		}
		ctx := CaptureContext{Producer: 1, Origin: "simulation", Snapshot: SnapshotID{Sequence: 1}}
		err = hub.DropScope(ctx, "site")
		if capacity == 1 {
			if err == nil || hub.probeStates != 0 || hub.producers[1].observed || len(hub.sinks[sink].queue.ring) != 0 {
				t.Fatal("non-atomic rejection", err)
			}
			continue
		}
		if err != nil || hub.probeStates != 2 || len(hub.sinks[sink].queue.ring) != 2 {
			t.Fatal(err, hub.probeStates)
		}
		records := hub.sinks[sink].queue.ring
		if records[0].WatchID >= records[1].WatchID || hub.sinks[sink].captured != 2 {
			t.Fatal("omitted-scope delivery order or health", records, hub.sinks[sink].captured)
		}
		for _, entry := range hub.subscriptions {
			if entry.pendingReasons[1] != "observation_gap" {
				t.Fatal("missing pending gap")
			}
			registry, err := hub.producerRegistry(entry, 1)
			if err != nil || registry.entries[1].pendingBaseline != "observation_gap" {
				t.Fatal("gap lost on first probe", err)
			}
		}
		if hub.probeStates != 2 {
			t.Fatal("state accounting changed")
		}
	}
}

func TestScopedDropStagesAndHonorsTurnRecordLimit(t *testing.T) {
	for _, maxRecords := range []int{0, 1} {
		limits := DefaultDiagnosticLimits()
		limits.MaxTurnRecords = maxRecords
		hub, err := NewDiagnostics(limits)
		if err != nil {
			t.Fatal(err)
		}
		sink, err := hub.AddSink(AllowAllWatches())
		if err != nil {
			t.Fatal(err)
		}
		config := ProbeSubscription{Sink: sink, Site: "site", Kind: "probe", Watch: ChangedWatch(nil)}
		if _, err = hub.ConfigureSubscriptions(0, []ProbeSubscription{config}); err != nil {
			t.Fatal(err)
		}
		if err = hub.RegisterProducer(1, 1); err != nil {
			t.Fatal(err)
		}
		if _, err = hub.Acknowledge(1, 1, 1); err != nil {
			t.Fatal(err)
		}
		scope, err := hub.BeginTurn(CaptureContext{Producer: 1, Origin: "simulation", Snapshot: SnapshotID{Sequence: 1}})
		if err != nil {
			t.Fatal(err)
		}
		if err = scope.DropScope("site"); err != nil {
			t.Fatal(err)
		}
		if len(hub.sinks[sink].queue.ring) != 0 || len(scope.staged) != maxRecords {
			t.Fatal("scope publication escaped staging")
		}
		if err = scope.Finish(false); err != nil {
			t.Fatal(err)
		}
		if len(hub.sinks[sink].queue.ring) != maxRecords || hub.sinks[sink].queue.droppedTotal != uint64(1-maxRecords) {
			t.Fatal("turn budget not enforced")
		}
		if err = scope.DropScope("site"); err == nil {
			t.Fatal("closed scope accepted drop")
		}
		for _, entry := range hub.subscriptions {
			if entry.pendingReasons[1] != "observation_gap" {
				t.Fatal("gap lost on dropped notification")
			}
		}
	}
}

func TestProbeCapacityFailureDoesNotPartiallyCapture(t *testing.T) {
	limits := DefaultDiagnosticLimits()
	limits.MaxProbeStates = 1
	hub, err := NewDiagnostics(limits)
	if err != nil {
		t.Fatal(err)
	}
	sink, err := hub.AddSink(AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	configs := []ProbeSubscription{
		{Sink: sink, Site: "site", Kind: "probe", Watch: ChangedWatch(nil)},
		{Sink: sink, Site: "site", Kind: "probe", Watch: ChangedWatch(nil)},
	}
	if _, err = hub.ConfigureSubscriptions(0, configs); err != nil {
		t.Fatal(err)
	}
	if err = hub.RegisterProducer(1, 1); err != nil {
		t.Fatal(err)
	}
	if _, err = hub.Acknowledge(1, 1, 1); err != nil {
		t.Fatal(err)
	}
	calls := 0
	ctx := CaptureContext{Producer: 1, Origin: "simulation", Snapshot: SnapshotID{Sequence: 1}}
	err = hub.Probe(ctx, "site", InfoSeverity, func() InspectAdapter { calls++; return evictionScalar{} })
	if err == nil || calls != 0 || hub.probeStates != 0 || hub.producers[1].observed || hub.producers[1].eventSequence != 0 || len(hub.sinks[sink].queue.ring) != 0 {
		t.Fatal("capacity rejection partially captured", err, calls)
	}
}

func TestMarkerCapacityFailureDoesNotPartiallyCapture(t *testing.T) {
	limits := DefaultDiagnosticLimits()
	limits.MaxProbeStates = 1
	hub, err := NewDiagnostics(limits)
	if err != nil {
		t.Fatal(err)
	}
	sink, err := hub.AddSink(AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	configs := []ProbeSubscription{
		{Sink: sink, Site: "site", Kind: "marker", Watch: ChangedWatch(nil)},
		{Sink: sink, Site: "site", Kind: "marker", Watch: ChangedWatch(nil)},
	}
	if _, err = hub.ConfigureSubscriptions(0, configs); err != nil {
		t.Fatal(err)
	}
	if err = hub.RegisterProducer(1, 1); err != nil {
		t.Fatal(err)
	}
	if _, err = hub.Acknowledge(1, 1, 1); err != nil {
		t.Fatal(err)
	}
	ctx := CaptureContext{Producer: 1, Origin: "simulation", Snapshot: SnapshotID{Sequence: 1}}
	err = hub.Marker(ctx, "site", InfoSeverity)
	if err == nil || hub.probeStates != 0 || hub.producers[1].observed || hub.producers[1].eventSequence != 0 || len(hub.sinks[sink].queue.ring) != 0 || hub.sinks[sink].captured != 0 {
		t.Fatal("marker capacity rejection partially captured", err)
	}
}

func TestBeginTurnRejectsInvalidUTF8BeforeProducerAdvance(t *testing.T) {
	for _, bad := range []string{"phase", "property"} {
		t.Run(bad, func(t *testing.T) {
			hub, err := NewDiagnostics(DefaultDiagnosticLimits())
			if err != nil {
				t.Fatal(err)
			}
			if err = hub.RegisterProducer(1, 1); err != nil {
				t.Fatal(err)
			}
			context := CaptureContext{Producer: 1, Origin: "simulation", Snapshot: SnapshotID{Sequence: 1}}
			if bad == "phase" {
				context.Phase = string([]byte{0xff})
			} else {
				context.FailingProperties = []string{string([]byte{0xff})}
			}
			if _, err = hub.BeginTurn(context); err == nil {
				t.Fatal("invalid UTF-8 accepted")
			}
			if hub.producers[1].observed || hub.scope != nil || hub.busy {
				t.Fatal("rejection changed producer or scope")
			}
			context.Phase = ""
			context.FailingProperties = []string{"valid-é"}
			scope, err := hub.BeginTurn(context)
			if err != nil {
				t.Fatal("valid retry failed", err)
			}
			if err = scope.Finish(true); err != nil {
				t.Fatal(err)
			}
		})
	}
}
