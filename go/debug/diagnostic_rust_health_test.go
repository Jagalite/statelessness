package debug_test

import (
	"fmt"
	"strings"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

func markerProbeFixture(t *testing.T) (func(string, ...string) d.ProtocolResponse, *d.Diagnostics, uint64) {
	t.Helper()
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
	return send, hub, sink
}

// Rust protocol.rs append_probe_health emits all ten SinkHealth counters in
// declaration order after id, queued_records and queued_bytes; Rust
// diagnostic.rs DiagnosticHub::abandon clears the queue and counts abandoned.
func TestProbeStatusSinkHealthMatchesRustOrderAndAbandon(t *testing.T) {
	send, hub, sink := markerProbeFixture(t)
	complete := true
	for seq := uint64(1); seq <= 3; seq++ {
		if err := hub.Marker(d.CaptureContext{Producer: 9, Origin: "simulation", TurnComplete: &complete, Snapshot: d.SnapshotID{Sequence: seq}}, "site", d.InfoSeverity); err != nil {
			t.Fatal(err)
		}
	}
	n, err := hub.Abandon(sink)
	if err != nil || n != 3 {
		t.Fatal(n, err)
	}
	if health, err := hub.Health(sink); err != nil || health.Abandoned != 3 || health.Captured != 3 {
		t.Fatal("host health", health, err)
	}
	if queued, _ := hub.SinkHealth(sink); queued.Records != 0 || queued.Bytes != 0 {
		t.Fatal("abandon left records", queued)
	}
	if _, err = hub.Abandon(99); err == nil {
		t.Fatal("unknown sink abandoned")
	}
	status := send("probe_status", "u64:0", "u64:1")
	if status.Error != "" {
		t.Fatal(status)
	}
	var keys []string
	values := map[string]string{}
	for _, f := range status.Fields {
		if strings.HasPrefix(f.Key, "probes.sinks.0.") {
			keys = append(keys, strings.TrimPrefix(f.Key, "probes.sinks.0."))
			values[f.Key] = f.Value
		}
	}
	want := "id,queued_records,queued_bytes,health.captured,health.filtered,health.sampled,health.dropped,health.truncated,health.exporter_failed,health.abandoned,health.inspection_failed,health.baseline_evicted,health.observation_gaps"
	if strings.Join(keys, ",") != want {
		t.Fatalf("sink health order %v", keys)
	}
	if values["probes.sinks.0.queued_records"] != "u64:0" || values["probes.sinks.0.health.captured"] != "u64:3" || values["probes.sinks.0.health.abandoned"] != "u64:3" {
		t.Fatal("protocol queue health", values)
	}
	events := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:4", "bool:false")
	for _, f := range events.Fields {
		if f.Key == "probes.events.returned" && f.Value != "u64:0" {
			t.Fatal("abandoned records were still polled", events)
		}
	}
}

// Rust diagnostic.rs impl Drop for ScopedDiagnostics publishes an unfinished
// turn as incomplete. Close is the deferred Go equivalent.
func TestDiagnosticScopeCloseReleasesHubAndPublishesIncomplete(t *testing.T) {
	send, hub, sink := markerProbeFixture(t)
	func() {
		defer func() { _ = recover() }()
		scope, err := hub.BeginTurn(d.CaptureContext{Producer: 9, RunID: 4, Origin: "simulation", Snapshot: d.SnapshotID{Sequence: 1}})
		if err != nil {
			t.Fatal(err)
		}
		defer scope.Close()
		if err = scope.Marker("site", d.InfoSeverity); err != nil {
			t.Fatal(err)
		}
		panic("host turn failed")
	}()
	r := send("probe_events", fmt.Sprintf("u64:%d", sink), "u64:1", "bool:false")
	fields := map[string]string{}
	for _, f := range r.Fields {
		fields[f.Key] = f.Value
	}
	if r.Error != "" || fields["probes.events.returned"] != "u64:1" || fields["probes.events.0.incomplete_turn"] != "bool:true" {
		t.Fatal("abandoned turn not published as incomplete", r)
	}
	scope, err := hub.BeginTurn(d.CaptureContext{Producer: 9, Origin: "simulation", Snapshot: d.SnapshotID{Sequence: 2}})
	if err != nil {
		t.Fatal("closed scope kept the hub locked", err)
	}
	if err = scope.Close(); err != nil {
		t.Fatal(err)
	}
	if err = scope.Close(); err != nil {
		t.Fatal("close is not idempotent", err)
	}
	if err = scope.Finish(true); err != nil {
		t.Fatal("finish after close must be a no-op", err)
	}
	finished, err := hub.BeginTurn(d.CaptureContext{Producer: 9, Origin: "simulation", Snapshot: d.SnapshotID{Sequence: 3}})
	if err != nil {
		t.Fatal(err)
	}
	if err = finished.Finish(true); err != nil {
		t.Fatal(err)
	}
	if err = finished.Close(); err != nil {
		t.Fatal("close after finish must be a no-op", err)
	}
}
