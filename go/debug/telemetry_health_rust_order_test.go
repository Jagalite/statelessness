package debug_test

import (
	"encoding/json"
	"strings"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

// Rust protocol.rs append_telemetry_health field list and order. Go cannot
// observe dropped handles, so the two Drop-hook counts are explicitly unknown.
func TestTelemetryHealthWireMatchesRustOrder(t *testing.T) {
	p, _, _ := probeFixture(t, d.DefaultProtocolLimits())
	options := d.DefaultEffectOptions()
	options.Timings = false
	observer, err := d.NewEffectObserver(options, nil)
	if err != nil {
		t.Fatal(err)
	}
	if err = p.AttachEffectObserver(observer); err != nil {
		t.Fatal(err)
	}
	r := p.Handle(d.ProtocolRequest{Session: "p", Epoch: p.Epoch(), ID: 1, Command: "telemetry_health"})
	if r.Error != "" {
		t.Fatal(r)
	}
	var keys []string
	values := map[string]string{}
	for _, f := range r.Fields {
		if strings.HasPrefix(f.Key, "telemetry.") {
			keys = append(keys, strings.TrimPrefix(f.Key, "telemetry."))
			values[f.Key] = f.Value
		}
	}
	want := []string{"destructor_hook_losses", "callback_faults", "pause_interval_evictions", "detail_evictions", "detail_rejections", "detail_event_drops", "duplicate_observations", "duplicate_deliveries", "invalid_lifecycle", "abandoned", "clock_failures", "debugger_affected_measurements", "active_effect_handles", "retained_detail_records", "retained_detail_bytes", "metric.rejected_series", "metric.invalid_labels", "metric.arithmetic_errors", "metric.omitted_measurements", "metric.invalid_measurements", "metric.imported_ignored", "metric.snapshots_evicted", "metric.snapshot_rejections", "unobserved_native_debugger_interference_possible"}
	if strings.Join(keys, ",") != strings.Join(want, ",") {
		t.Fatalf("telemetry_health order %v", keys)
	}
	if values["telemetry.destructor_hook_losses"] != "unknown:explicit_disposal_only" || values["telemetry.active_effect_handles"] != "unknown:explicit_disposal_only" {
		t.Fatal("drop-hook counts must be unknown", values)
	}
	h := observer.TelemetryHealth()
	if h.ActiveEffectHandles != nil || h.DestructorHookLosses != nil || h.EffectHandleBytes == 0 || h.AttemptHandleBytes == 0 || h.DeliveryHandleBytes == 0 {
		t.Fatal("handle accounting", h)
	}
}

// Rust dashboard.rs to_json: fixed key order, metric counters nested inside
// health, host metric health as health.host_metric_health, rows last.
func TestDashboardJSONMatchesRustLayout(t *testing.T) {
	view := d.Dashboard{Epoch: 1, Revision: 2, Temporality: "cumulative", Health: d.TelemetryHealth{EffectHandleBytes: 3}, MetricHealth: d.MetricHealth{OmittedMeasurements: 4}}
	data, err := view.JSON(1 << 16)
	if err != nil {
		t.Fatal(err)
	}
	text := string(data)
	want := `{"schema":"statelessness.effect-dashboard/1","epoch":1,"revision":2,"temporality":"cumulative","start":null,"end":null,"truncated":false,"observation_complete":false,"health":{"dropped_detail_events":0,"detail_evictions":0,"detail_rejections":0,"clock_failures":0,"callback_faults":0,"destructor_hook_losses":null,"invalid_lifecycle":0,"active_effect_handles":null,"omitted_measurements":4,"invalid_measurements":0,"rejected_series":0,"invalid_labels":0,"arithmetic_errors":0,"imported_ignored":0,"snapshots_evicted":0,"snapshot_rejections":0,"duplicate_observations":0,"duplicate_deliveries":0,"abandoned":0,"pause_interval_evictions":0,"debugger_affected_measurements":0,"retained_detail_records":0,"retained_detail_bytes":0,"effect_handle_bytes":3,"attempt_handle_bytes":0,"delivery_handle_bytes":0,"unobserved_native_debugger_interference_possible":false,"host_metric_health":{"rejected_series":0,"invalid_labels":0,"arithmetic_errors":0,"omitted_measurements":0,"invalid_measurements":0,"imported_ignored":0,"snapshots_evicted":0,"snapshot_rejections":0}},"rows":[]}`
	if text != want {
		t.Fatalf("dashboard layout\n got %s\nwant %s", text, want)
	}
	if !json.Valid(data) {
		t.Fatal("invalid JSON")
	}
}
