package debug_test

import (
	"fmt"
	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

func probeFixture(t *testing.T, limits d.ProtocolLimits) (*d.ProtocolSession[int, byte, int], *d.Diagnostics, uint64) {
	t.Helper()
	model := s.Model[int, byte, int]{InitialState: func() (int, error) { return 0, nil }, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[byte], CloneOutput: s.ValueCopy[int], Step: func(v int, _ byte) (s.Transition[int, int], error) { return s.Accept[int, int](v), nil }, CheckState: func(int) ([]s.Check, error) { return nil, nil }}
	session, err := d.NewSession("p", model, d.Unrestricted[int, byte](), func(a, b byte) (bool, error) { return a == b, nil }, d.DefaultSessionLimits())
	if err != nil {
		t.Fatal(err)
	}
	p, err := d.NewProtocolSession(session, func(b []byte) (byte, error) { return b[0], nil }, d.ProtocolAuthority{ConfigureDiagnostics: true}, limits)
	if err != nil {
		t.Fatal(err)
	}
	hub, err := d.NewDiagnostics(d.DefaultDiagnosticLimits())
	if err != nil {
		t.Fatal(err)
	}
	sink, err := hub.AddSink(d.AllowAllWatches())
	if err != nil {
		t.Fatal(err)
	}
	return p, hub, sink
}
func TestAttachedProbeProfileConfiguration(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	profile := d.ProtocolProbeProfile{ID: 2, Label: "marker", Subscription: d.ProbeSubscription{Sink: sink, Site: "site", Kind: "marker", Watch: d.ChangedWatch(nil)}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	capabilities := send("status")
	capFields := map[string]string{}
	for _, field := range capabilities.Fields {
		capFields[field.Key] = field.Value
	}
	if capabilities.Error != "" || capFields["capability.probe_queries"] != "bool:true" || capFields["capability.probe_configuration"] != "bool:true" {
		t.Fatal("attached probes not advertised", capabilities)
	}
	result := send("probe_configure", "u64:0", "u64:2")
	if result.Error != "" || hub.CaptureRevision() != 1 {
		t.Fatal(result)
	}
	if status := hub.ProducerStatus(); len(status) != 1 || status[0].CaptureRevision != 0 {
		t.Fatal("host acknowledgement was forged", status)
	}
	if r := send("probe_configure", "u64:0", "u64:2"); r.Error != "stale_configuration" {
		t.Fatal(r)
	}
	if r := send("probe_configure", "u64:1", "u64:99"); r.Error != "unauthorized" {
		t.Fatal(r)
	}
	if r := send("probe_configure", "u64:1", "u64:2", "u64:2"); r.Error != "invalid_input" {
		t.Fatal(r)
	}
	status := send("probe_status", "u64:0", "u64:10")
	if status.Error != "" {
		t.Fatal(status)
	}
	fields := map[string]string{}
	for _, f := range status.Fields {
		fields[f.Key] = f.Value
	}
	if fields["probes.producers.0.pending"] != "bool:true" || fields["probes.profiles.0.label"] != "string:marker" {
		t.Fatal(fields)
	}
	if r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:false"); r.Error != "" {
		t.Fatal(r)
	}
}
func TestAttachedProbeConfigurationPreflightsBeforeMutation(t *testing.T) {
	limits := d.DefaultProtocolLimits()
	limits.MaxFields = 64
	p, hub, sink := probeFixture(t, limits)
	for i := uint64(0); i < 64; i++ {
		if err := hub.RegisterProducer(i, 1); err != nil {
			t.Fatal(err)
		}
	}
	profile := d.ProtocolProbeProfile{ID: 1, Label: "p", Subscription: d.ProbeSubscription{Sink: sink, Site: "site", Kind: "marker", Watch: d.ChangedWatch(nil)}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	r := p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: 1, Command: "probe_configure", Args: []string{"u64:0", "u64:1"}})
	fields := map[string]string{}
	for _, field := range r.Fields {
		fields[field.Key] = field.Value
	}
	if r.Error != "" || hub.CaptureRevision() != 1 || fields["probes.pending_producers.total"] != "u64:64" || fields["probes.pending_producers.truncated"] != "bool:true" {
		t.Fatal("pending producer acknowledgement must be bounded and truthful", r, hub.CaptureRevision())
	}
	for _, producer := range hub.ProducerStatus() {
		if producer.CaptureRevision != 0 {
			t.Fatal("host acknowledgement was forged", producer)
		}
	}
}

func TestAttachProbeProfileRejectsOversizedHostDataBeforeCommit(t *testing.T) {
	limits := d.DefaultProtocolLimits()
	limits.MaxCandidateBytes = 128
	p, hub, sink := probeFixture(t, limits)
	config := d.ProbeSubscription{Sink: sink, Site: string(make([]byte, 1024)), Watch: d.ChangedWatch(nil)}
	err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{{ID: 1, Label: "p", Subscription: config}}, []uint64{sink})
	if err == nil || hub.CaptureRevision() != 0 {
		t.Fatal("oversized profile accepted", err)
	}
	config.Site = "s"
	if err = p.AttachProbeProfiles(hub, nil, []uint64{sink}); err != nil {
		t.Fatal("failed attachment retained partial state", err)
	}
}

func TestAttachedMarkerPollingPreflightsAndRetainsProvenance(t *testing.T) {
	limits := d.DefaultProtocolLimits()
	limits.MaxFields = 64
	p, hub, sink := probeFixture(t, limits)
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	profile := d.ProtocolProbeProfile{ID: 2, Label: "marker", Subscription: d.ProbeSubscription{Sink: sink, Site: "site", Kind: "marker", Watch: d.ChangedWatch(nil)}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if r := send("probe_configure", "u64:0", "u64:2"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	complete := true
	for seq := uint64(1); seq <= 4; seq++ {
		if err := hub.Marker(d.CaptureContext{Producer: 9, RunID: 42, Origin: "simulation", TurnComplete: &complete, Snapshot: d.SnapshotID{Session: 3, Sequence: seq}}, "site", d.InfoSeverity); err != nil {
			t.Fatal(err)
		}
	}
	oversized := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:4", "bool:true")
	if oversized.Error != "limit" {
		t.Fatal("expected bounded failure", oversized)
	}
	health, err := hub.SinkHealth(sink)
	if err != nil || health.Records != 4 {
		t.Fatal("failed preflight consumed queue", health, err)
	}
	one := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:false")
	if one.Error != "" {
		t.Fatal(one)
	}
	fields := map[string]string{}
	for _, f := range one.Fields {
		fields[f.Key] = f.Value
	}
	for key, want := range map[string]string{"probes.events.0.run": "u64:42", "probes.events.0.producer": "u64:9", "probes.events.0.epoch": "u64:7", "probes.events.0.producer_sequence": "u64:1", "probes.events.0.origin": "enum:Simulation", "probes.events.0.payload": "omitted:by_request"} {
		if fields[key] != want {
			t.Fatalf("%s=%q want %q", key, fields[key], want)
		}
	}
	if _, exists := fields["probes.events.0.site"]; exists {
		t.Fatal("redacted poll leaked site")
	}
	health, err = hub.SinkHealth(sink)
	if err != nil || health.Records != 4 {
		t.Fatal("protocol poll changed host queue", health, err)
	}
}

func TestMarkerPollingDoesNotInventTurnCompleteness(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	profile := d.ProtocolProbeProfile{ID: 2, Label: "marker", Subscription: d.ProbeSubscription{Sink: sink, Site: "site", Kind: "marker", Watch: d.ChangedWatch(nil)}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if r := send("probe_configure", "u64:0", "u64:2"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	ctx := d.CaptureContext{Producer: 9, RunID: 42, Origin: "simulation", Snapshot: d.SnapshotID{Session: 3, Sequence: 1}}
	if err := hub.Marker(ctx, "site", d.InfoSeverity); err != nil {
		t.Fatal(err)
	}
	// Unscoped capture without a known turn outcome has no Rust wire envelope.
	// It stays a host-only record: it is neither exposed with invented
	// completeness nor allowed to block later protocol polling.
	empty := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:false")
	emptyFields := map[string]string{}
	for _, f := range empty.Fields {
		emptyFields[f.Key] = f.Value
	}
	if empty.Error != "" || emptyFields["probes.events.returned"] != "u64:0" {
		t.Fatal("unknown completeness exposed or blocked polling", empty)
	}
	if health, _ := hub.SinkHealth(sink); health.Records != 1 {
		t.Fatal("host record lost", health)
	}
	incomplete := false
	ctx.TurnComplete = &incomplete
	ctx.Snapshot.Sequence = 2
	if err := hub.Marker(ctx, "site", d.InfoSeverity); err != nil {
		t.Fatal(err)
	}
	r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:false")
	fields := map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["probes.events.0.incomplete_turn"] != "bool:true" {
		t.Fatal("incomplete turn was hidden", r)
	}
}

func TestAttachedProbePollingReportsCapturedValue(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	profile := d.ProtocolProbeProfile{ID: 2, Label: "counter", Subscription: d.ProbeSubscription{Sink: sink, Site: "value", Kind: "probe", Watch: d.ChangedWatch(nil)}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if r := send("probe_configure", "u64:0", "u64:2"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	complete := true
	ctx := d.CaptureContext{Producer: 9, RunID: 42, Origin: "simulation", TurnComplete: &complete, Snapshot: d.SnapshotID{Session: 3, Sequence: 1}}
	if err := hub.Probe(ctx, "value", d.InfoSeverity, func() d.InspectAdapter { return protocolScalar(7) }); err != nil {
		t.Fatal(err)
	}
	r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:true")
	fields := map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	for key, want := range map[string]string{"probes.events.0.kind": "enum:Probe", "probes.events.0.baseline": "bool:true", "probes.events.0.baseline_reason": "enum:Initial", "probes.events.0.change_unknown": "bool:false", "probes.events.0.display_schema": "string:counter", "probes.events.0.payload.value": "u64:7"} {
		if r.Error != "" || fields[key] != want {
			t.Fatalf("%s=%q want %q, response: %+v", key, fields[key], want, r)
		}
	}
}

func TestAttachedProbeMarksReconfiguredBaselineUnknown(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	profile := d.ProtocolProbeProfile{ID: 2, Label: "counter", Subscription: d.ProbeSubscription{Sink: sink, Site: "value", Kind: "probe", Watch: d.ChangedWatch(nil)}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if r := send("probe_configure", "u64:0", "u64:2"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	complete := true
	ctx := d.CaptureContext{Producer: 9, RunID: 42, Origin: "simulation", TurnComplete: &complete, Snapshot: d.SnapshotID{Session: 3, Sequence: 1}}
	if err := hub.Probe(ctx, "value", d.InfoSeverity, func() d.InspectAdapter { return protocolScalar(7) }); err != nil {
		t.Fatal(err)
	}
	if r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:true"); r.Error != "" {
		t.Fatal(r)
	}
	if r := send("probe_configure", "u64:1", "u64:2"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 2, 2); err != nil {
		t.Fatal(err)
	}
	ctx.Snapshot.Sequence = 2
	if err := hub.Probe(ctx, "value", d.InfoSeverity, func() d.InspectAdapter { return protocolScalar(7) }); err != nil {
		t.Fatal(err)
	}
	r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:true")
	fields := map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["probes.events.0.baseline_reason"] != "enum:Reconfigured" || fields["probes.events.0.change_unknown"] != "bool:true" {
		t.Fatal("reconfiguration lost baseline provenance", r)
	}
}

type failedProtocolProjection struct{}

type deepProtocolProjection struct{}

func (deepProtocolProjection) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "deep", Version: 1}
}
func (deepProtocolProjection) Inspect(_ []d.Segment, _ *d.InspectContext) (d.InspectNode, error) {
	node := d.InspectNode{Kind: "scalar", Scalar: &d.Scalar{Kind: "u64", Decimal: "7"}}
	for i := 0; i < 33; i++ {
		count := uint64(1)
		node = d.InspectNode{Kind: "sequence", Name: "nested", ChildCount: &count, Children: []d.ChildNode{{Segment: d.IndexPath(0), Label: "value", Node: node}}}
	}
	return node, nil
}

func TestProbeDepthLimitDoesNotConsumeEvent(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	watch := d.ChangedWatch(nil)
	watch.Limits.MaxDepth = 64
	profile := d.ProtocolProbeProfile{ID: 1, Label: "deep", Subscription: d.ProbeSubscription{Sink: sink, Site: "value", Kind: "probe", Watch: watch}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if r := send("probe_configure", "u64:0", "u64:1"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	complete := true
	ctx := d.CaptureContext{Producer: 9, Origin: "simulation", TurnComplete: &complete, Snapshot: d.SnapshotID{Sequence: 1}}
	if err := hub.Probe(ctx, "value", d.InfoSeverity, func() d.InspectAdapter { return deepProtocolProjection{} }); err != nil {
		t.Fatal(err)
	}
	if r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:true"); r.Error != "limit" {
		t.Fatal("deep payload accepted", r)
	}
	if health, err := hub.SinkHealth(sink); err != nil || health.Records != 1 {
		t.Fatal("failed preflight consumed event", health, err)
	}
	if r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:false"); r.Error != "" {
		t.Fatal("metadata retry failed", r)
	}
}

func (failedProtocolProjection) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "counter", Version: 1}
}
func (failedProtocolProjection) Inspect(_ []d.Segment, _ *d.InspectContext) (d.InspectNode, error) {
	return d.InspectNode{}, fmt.Errorf("projection unavailable")
}

func TestAttachedProbeFailureWithoutSchemaIsPollable(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	profile := d.ProtocolProbeProfile{ID: 1, Label: "value", Subscription: d.ProbeSubscription{Sink: sink, Site: "value", Kind: "probe", Watch: d.ChangedWatch(nil)}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if r := send("probe_configure", "u64:0", "u64:1"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	complete := true
	ctx := d.CaptureContext{Producer: 9, Origin: "simulation", TurnComplete: &complete, Snapshot: d.SnapshotID{Sequence: 1}}
	if err := hub.Probe(ctx, "value", d.InfoSeverity, func() d.InspectAdapter { return nil }); err != nil {
		t.Fatal(err)
	}
	r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:true")
	fields := map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["probes.events.0.payload"] != "none" || fields["probes.events.0.change_unknown"] != "bool:true" || fields["probes.events.returned"] != "u64:1" {
		t.Fatal(r)
	}
	if _, ok := fields["probes.events.0.display_schema"]; ok {
		t.Fatal("invented schema", r)
	}
	if health, err := hub.SinkHealth(sink); err != nil || health.Records != 1 || health.InspectionFailures != 1 {
		t.Fatal(health, err)
	}
}

func TestAttachedProbeFailureFilteringAndSampling(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	equals := d.ChangedWatch(nil)
	equals.Trigger = "equals"
	equals.Equals = &d.Scalar{Kind: "u128", Decimal: "1"}
	profiles := []d.ProtocolProbeProfile{
		{ID: 1, Label: "changed", Subscription: d.ProbeSubscription{Sink: sink, Site: "value", Kind: "probe", SampleEvery: 2, Watch: d.ChangedWatch(nil)}},
		{ID: 2, Label: "equals", Subscription: d.ProbeSubscription{Sink: sink, Site: "value", Kind: "probe", Watch: equals}},
	}
	if err := p.AttachProbeProfiles(hub, profiles, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if response := send("probe_configure", "u64:0", "u64:1", "u64:2"); response.Error != "" {
		t.Fatal(response)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	complete := true
	for seq := uint64(1); seq <= 2; seq++ {
		ctx := d.CaptureContext{Producer: 9, Origin: "simulation", TurnComplete: &complete, PropertyStatusKnown: true, PropertyFailed: true, Snapshot: d.SnapshotID{Sequence: seq}}
		if err := hub.Probe(ctx, "value", d.InfoSeverity, func() d.InspectAdapter { return failedProtocolProjection{} }); err != nil {
			t.Fatal(err)
		}
	}
	for seq := uint64(3); seq <= 4; seq++ {
		ctx := d.CaptureContext{Producer: 9, Origin: "simulation", TurnComplete: &complete, PropertyStatusKnown: true, PropertyFailed: true, Snapshot: d.SnapshotID{Sequence: seq}}
		if err := hub.Probe(ctx, "value", d.InfoSeverity, func() d.InspectAdapter { return protocolScalar(7) }); err != nil {
			t.Fatal(err)
		}
	}
	status := send("probe_status", "u64:0", "u64:1")
	fields := map[string]string{}
	for _, field := range status.Fields {
		fields[field.Key] = field.Value
	}
	for key, want := range map[string]string{"captured": "u64:2", "filtered": "u64:5", "sampled": "u64:1", "truncated": "u64:2", "inspection_failed": "u64:4"} {
		if status.Error != "" || fields["probes.sinks.0.health."+key] != want {
			t.Fatal("incorrect health", key, status)
		}
	}
	result := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:4", "bool:false")
	fields = map[string]string{}
	for _, field := range result.Fields {
		fields[field.Key] = field.Value
	}
	if result.Error != "" || fields["probes.events.returned"] != "u64:2" {
		t.Fatal("failure filtering or sampling", result)
	}
}

func TestAttachedProbeReportsFailureAndResetsBaseline(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	profile := d.ProtocolProbeProfile{ID: 2, Label: "counter", Subscription: d.ProbeSubscription{Sink: sink, Site: "value", Kind: "probe", Watch: d.ChangedWatch(nil)}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if r := send("probe_configure", "u64:0", "u64:2"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	complete := true
	ctx := d.CaptureContext{Producer: 9, RunID: 42, Origin: "simulation", TurnComplete: &complete, Snapshot: d.SnapshotID{Session: 3, Sequence: 1}}
	if err := hub.Probe(ctx, "value", d.InfoSeverity, func() d.InspectAdapter { return failedProtocolProjection{} }); err != nil {
		t.Fatal(err)
	}
	status := send("probe_status", "u64:0", "u64:1")
	var failures string
	for _, field := range status.Fields {
		if field.Key == "probes.sinks.0.health.inspection_failed" {
			failures = field.Value
		}
	}
	if status.Error != "" || failures != "u64:1" {
		t.Fatal("projection failure missing from sink health", status)
	}
	r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:true")
	fields := map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["probes.events.0.payload"] != "none" || fields["probes.events.0.change_unknown"] != "bool:true" || fields["probes.events.0.display_schema"] != "string:counter" {
		t.Fatal("failed projection evidence", r)
	}
	ctx.Snapshot.Sequence = 2
	if err := hub.Probe(ctx, "value", d.InfoSeverity, func() d.InspectAdapter { return protocolScalar(7) }); err != nil {
		t.Fatal(err)
	}
	r = send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:true")
	fields = map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["probes.events.0.baseline_reason"] != "enum:InspectionIncomplete" || fields["probes.events.0.change_unknown"] != "bool:true" {
		t.Fatal("failure did not reset baseline", r)
	}
	if health, err := hub.SinkHealth(sink); err != nil || health.InspectionFailures != 1 {
		t.Fatal("recovery changed failure count", health, err)
	}
}

type redactedProtocolProjection struct{}

func (redactedProtocolProjection) DisplaySchema() d.DisplaySchema {
	return d.DisplaySchema{Name: "counter", Version: 1}
}
func (redactedProtocolProjection) Inspect(_ []d.Segment, _ *d.InspectContext) (d.InspectNode, error) {
	return d.InspectNode{Kind: "redacted"}, nil
}
func TestAttachedProbeReportsRedactionAndResetsBaseline(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	profile := d.ProtocolProbeProfile{ID: 2, Label: "counter", Subscription: d.ProbeSubscription{Sink: sink, Site: "value", Kind: "probe", Watch: d.ChangedWatch(nil)}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if r := send("probe_configure", "u64:0", "u64:2"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	complete := true
	ctx := d.CaptureContext{Producer: 9, RunID: 42, Origin: "simulation", TurnComplete: &complete, Snapshot: d.SnapshotID{Session: 3, Sequence: 1}}
	if err := hub.Probe(ctx, "value", d.InfoSeverity, func() d.InspectAdapter { return redactedProtocolProjection{} }); err != nil {
		t.Fatal(err)
	}
	r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:true")
	fields := map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["probes.events.0.payload.kind"] != "enum:redacted" || fields["probes.events.0.change_unknown"] != "bool:true" {
		t.Fatal("redacted projection evidence", r)
	}
	ctx.Snapshot.Sequence = 2
	if err := hub.Probe(ctx, "value", d.InfoSeverity, func() d.InspectAdapter { return protocolScalar(7) }); err != nil {
		t.Fatal(err)
	}
	r = send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:true")
	fields = map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["probes.events.0.baseline_reason"] != "enum:InspectionIncomplete" || fields["probes.events.0.change_unknown"] != "bool:true" {
		t.Fatal("redaction did not reset baseline", r)
	}
}

func TestAttachedScopedTurnStagesAndMarksIncomplete(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	profile := d.ProtocolProbeProfile{ID: 2, Label: "marker", Subscription: d.ProbeSubscription{Sink: sink, Site: "site", Kind: "marker", SampleEvery: 2, Watch: d.ChangedWatch(nil)}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if r := send("probe_configure", "u64:0", "u64:2"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	scope, err := hub.BeginTurn(d.CaptureContext{Producer: 9, RunID: 42, Origin: "simulation", Snapshot: d.SnapshotID{Session: 3, Sequence: 1}})
	if err != nil {
		t.Fatal(err)
	}
	if err = scope.Marker("site", d.InfoSeverity); err != nil {
		t.Fatal(err)
	}
	if err = scope.Marker("site", d.InfoSeverity); err != nil {
		t.Fatal(err)
	}
	if r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:false"); r.Error != "busy" {
		t.Fatal("active turn allowed polling", r)
	}
	if err = scope.Finish(false); err != nil {
		t.Fatal(err)
	}
	if err = scope.Finish(true); err == nil {
		t.Fatal("scope finished twice")
	}
	status := send("probe_status", "u64:0", "u64:1")
	statusFields := map[string]string{}
	for _, field := range status.Fields {
		statusFields[field.Key] = field.Value
	}
	if status.Error != "" || statusFields["probes.sinks.0.health.captured"] != "u64:1" || statusFields["probes.sinks.0.health.sampled"] != "u64:1" {
		t.Fatal("incorrect sink health", status)
	}
	r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:false")
	fields := map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["probes.events.0.incomplete_turn"] != "bool:true" || fields["probes.events.0.run"] != "u64:42" {
		t.Fatal("incomplete staged event", r)
	}
}

func TestAttachedScopedTurnInvalidatesRetainedBaselines(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	profile := d.ProtocolProbeProfile{ID: 1, Label: "value", Subscription: d.ProbeSubscription{Sink: sink, Site: "value", Kind: "probe", Scope: d.ScopeSelector{Entity: "selected"}, Watch: d.ChangedWatch(nil)}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if r := send("probe_configure", "u64:0", "u64:1"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	turn := func(seq, run uint64, entity, reason string) {
		t.Helper()
		scope, err := hub.BeginTurn(d.CaptureContext{Producer: 9, RunID: run, Entity: entity, Origin: "simulation", Snapshot: d.SnapshotID{Sequence: seq}})
		if err != nil {
			t.Fatal(err)
		}
		if err = scope.Probe("value", d.InfoSeverity, func() d.InspectAdapter { return protocolScalar(7) }); err != nil {
			t.Fatal(err)
		}
		if err = scope.Finish(true); err != nil {
			t.Fatal(err)
		}
		r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:false")
		fields := map[string]string{}
		for _, field := range r.Fields {
			fields[field.Key] = field.Value
		}
		if r.Error != "" {
			t.Fatal(r)
		}
		if reason == "" {
			if fields["probes.events.returned"] != "u64:0" {
				t.Fatal(r)
			}
		} else if fields["probes.events.0.baseline_reason"] != "enum:"+reason || fields["probes.events.returned"] != "u64:1" {
			t.Fatal(reason, r)
		}
	}
	turn(1, 1, "selected", "Initial")
	turn(2, 1, "other", "")
	turn(3, 1, "selected", "SelectorGap")
	turn(4, 2, "selected", "SourceChanged")
	turn(6, 2, "selected", "ObservationGap")
	status := send("probe_status", "u64:0", "u64:1")
	fields := map[string]string{}
	for _, field := range status.Fields {
		fields[field.Key] = field.Value
	}
	if status.Error != "" || fields["probes.sinks.0.health.observation_gaps"] != "u64:1" {
		t.Fatal(status)
	}
}

func TestAttachedScopedTurnRemembersSelectorGapBeforeFirstProbe(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	profile := d.ProtocolProbeProfile{ID: 1, Label: "value", Subscription: d.ProbeSubscription{Sink: sink, Site: "value", Kind: "probe", Scope: d.ScopeSelector{Entity: "selected"}, Watch: d.ChangedWatch(nil)}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if r := send("probe_configure", "u64:0", "u64:1"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	for seq, entity := range []string{"other", "selected"} {
		scope, err := hub.BeginTurn(d.CaptureContext{Producer: 9, RunID: 1, Entity: entity, Origin: "simulation", Snapshot: d.SnapshotID{Sequence: uint64(seq + 1)}})
		if err != nil {
			t.Fatal(err)
		}
		if err = scope.Probe("value", d.InfoSeverity, func() d.InspectAdapter { return protocolScalar(7) }); err != nil {
			t.Fatal(err)
		}
		if err = scope.Finish(true); err != nil {
			t.Fatal(err)
		}
	}
	r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:false")
	fields := map[string]string{}
	for _, field := range r.Fields {
		fields[field.Key] = field.Value
	}
	if r.Error != "" || fields["probes.events.0.baseline_reason"] != "enum:SelectorGap" || fields["probes.events.0.change_unknown"] != "bool:true" {
		t.Fatal(r)
	}
}

func TestAttachedMarkerContinuityAndSampling(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	profile := d.ProtocolProbeProfile{ID: 1, Label: "marker", Subscription: d.ProbeSubscription{Sink: sink, Site: "marker", Kind: "marker", SampleEvery: 2, Scope: d.ScopeSelector{Entity: "selected"}, Watch: d.ChangedWatch(nil)}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if r := send("probe_configure", "u64:0", "u64:1"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	for seq, entity := range []string{"other", "selected", "other", "selected", "selected"} {
		scope, err := hub.BeginTurn(d.CaptureContext{Producer: 9, RunID: 1, Entity: entity, Origin: "simulation", Snapshot: d.SnapshotID{Sequence: uint64(seq + 1)}})
		if err != nil {
			t.Fatal(err)
		}
		if err = scope.Marker("marker", d.InfoSeverity); err != nil {
			t.Fatal(err)
		}
		if err = scope.Finish(true); err != nil {
			t.Fatal(err)
		}
	}
	r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:4", "bool:false")
	fields := map[string]string{}
	for _, field := range r.Fields {
		fields[field.Key] = field.Value
	}
	if r.Error != "" || fields["probes.events.returned"] != "u64:2" || fields["probes.events.0.baseline_reason"] != "enum:SelectorGap" || fields["probes.events.1.baseline_reason"] != "enum:Initial" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 6); err != nil {
		t.Fatal(err)
	}
}

func TestAttachedMarkerPayloadTriggersAreFiltered(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	changed := d.ChangedWatch(nil)
	every := d.ChangedWatch(nil)
	every.Trigger = "every"
	equals := d.ChangedWatch(nil)
	equals.Trigger = "equals"
	equals.Equals = &d.Scalar{Kind: "u128", Decimal: "1"}
	above := d.ChangedWatch(nil)
	above.Trigger = "above_u128"
	watches := []d.WatchConfig{changed, every, equals, above}
	profiles := make([]d.ProtocolProbeProfile, len(watches))
	for i, watch := range watches {
		profiles[i] = d.ProtocolProbeProfile{ID: uint64(i + 1), Label: "marker", Subscription: d.ProbeSubscription{Sink: sink, Site: "marker", Kind: "marker", Watch: watch}}
	}
	if err := p.AttachProbeProfiles(hub, profiles, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	id := uint64(0)
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if r := send("probe_configure", "u64:0", "u64:1", "u64:2", "u64:3", "u64:4"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	scope, err := hub.BeginTurn(d.CaptureContext{Producer: 9, Origin: "simulation", Snapshot: d.SnapshotID{Sequence: 1}})
	if err != nil {
		t.Fatal(err)
	}
	if err = scope.Marker("marker", d.InfoSeverity); err != nil {
		t.Fatal(err)
	}
	if err = scope.Finish(true); err != nil {
		t.Fatal(err)
	}
	status := send("probe_status", "u64:0", "u64:1")
	fields := map[string]string{}
	for _, field := range status.Fields {
		fields[field.Key] = field.Value
	}
	if status.Error != "" || fields["probes.sinks.0.health.captured"] != "u64:2" || fields["probes.sinks.0.health.filtered"] != "u64:2" {
		t.Fatal(status)
	}
	r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:4", "bool:false")
	fields = map[string]string{}
	for _, field := range r.Fields {
		fields[field.Key] = field.Value
	}
	if r.Error != "" || fields["probes.events.returned"] != "u64:2" {
		t.Fatal(r)
	}
}

func TestProbeConsumersAreIndependentAndBounded(t *testing.T) {
	p, hub, sink := probeFixture(t, d.DefaultProtocolLimits())
	if err := hub.RegisterProducer(9, 7); err != nil {
		t.Fatal(err)
	}
	profile := d.ProtocolProbeProfile{ID: 2, Label: "marker", Subscription: d.ProbeSubscription{Sink: sink, Site: "site", Kind: "marker", Watch: d.ChangedWatch(nil)}}
	if err := p.AttachProbeProfiles(hub, []d.ProtocolProbeProfile{profile}, []uint64{sink}); err != nil {
		t.Fatal(err)
	}
	var id uint64
	send := func(command string, args ...string) d.ProtocolResponse {
		id++
		return p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: id, Command: command, Args: args})
	}
	if r := send("probe_configure", "u64:0", "u64:2"); r.Error != "" {
		t.Fatal(r)
	}
	if _, err := hub.Acknowledge(9, 1, 1); err != nil {
		t.Fatal(err)
	}
	complete := true
	ctx := d.CaptureContext{Producer: 9, RunID: 42, Origin: "simulation", TurnComplete: &complete, Snapshot: d.SnapshotID{Sequence: 1}}
	if err := hub.DropScope(ctx, "site"); err != nil {
		t.Fatal(err)
	}
	if err := hub.Marker(ctx, "site", d.InfoSeverity); err != nil {
		t.Fatal(err)
	}
	host, err := hub.DrainSink(sink)
	if err != nil || len(host) != 2 || host[0].Kind != "scope_incomplete" || host[1].Kind != "marker" {
		t.Fatal(host, err)
	}
	r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:false")
	fields := map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["probes.events.returned"] != "u64:1" || fields["probes.events.0.kind"] != "enum:Marker" {
		t.Fatal(r)
	}
	// Fill the protocol queue while draining the host on each turn. The protocol
	// reports its own overflow without imposing loss on the host consumer.
	for n := uint64(2); n <= uint64(d.DefaultDiagnosticLimits().Watch.MaxRingRecords)+3; n++ {
		ctx.Snapshot.Sequence = n
		if err := hub.Marker(ctx, "site", d.InfoSeverity); err != nil {
			t.Fatal(err)
		}
		events, err := hub.DrainSink(sink)
		if err != nil || len(events) != 1 {
			t.Fatal(n, events, err)
		}
	}
	r = send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:false")
	fields = map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["probes.sink.health.dropped"] == "u64:0" {
		t.Fatal("missing protocol overflow", r)
	}
	if health, err := hub.SinkHealth(sink); err != nil || health.DroppedTotal != 0 {
		t.Fatal("host loss changed", health, err)
	}
	if err := p.DetachProbeProfiles(); err != nil {
		t.Fatal(err)
	}
	if err := p.AttachProbeProfiles(hub, nil, []uint64{sink}); err != nil {
		t.Fatal("queue not released", err)
	}
}
