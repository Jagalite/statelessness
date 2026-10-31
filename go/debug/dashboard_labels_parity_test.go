package debug_test

import (
	"encoding/json"
	"testing"

	d "github.com/Jagalite/statelessness/go/debug"
)

func TestDashboardJSONUsesReferenceLabelVariants(t *testing.T) {
	for _, tc := range []struct {
		origin                             d.MeasurementOrigin
		outcome                            d.Outcome
		scope                              d.GaugeScope
		wantOrigin, wantOutcome, wantScope string
	}{
		{d.OriginLive, d.OutcomeNone, d.HostSnapshot, "Live", "None", "HostSnapshot"},
		{d.OriginDebugLive, d.OutcomeFailure, d.CompleteParticipation, "DebugControlledLive", "Failure", "CompleteParticipation"},
		{d.OriginTestClock, d.OutcomeCancelled, d.ObservedSinceAttach, "TestClock", "Cancelled", "ObservedSinceAttach"},
		{d.OriginReplay, d.OutcomeIgnored, d.UnknownGaugeScope, "ReplayHarness", "Ignored", "Unknown"},
		{d.OriginImported, d.OutcomeAbandoned, d.HostSnapshot, "ImportedLive", "Abandoned", "HostSnapshot"},
	} {
		view := d.Dashboard{Rows: []d.DashboardRow{{Family: d.Running, Labels: d.Labels{Origin: tc.origin, Outcome: tc.outcome}, GaugeScope: &tc.scope}}}
		data, err := view.JSON(4096)
		if err != nil {
			t.Fatal(err)
		}
		var result struct {
			Rows []map[string]any `json:"rows"`
		}
		if err = json.Unmarshal(data, &result); err != nil {
			t.Fatal(err)
		}
		row := result.Rows[0]
		if row["measurement_origin"] != tc.wantOrigin || row["outcome"] != tc.wantOutcome || row["gauge_scope"] != tc.wantScope {
			t.Fatalf("wrong wire variants: %+v", row)
		}
	}
}
