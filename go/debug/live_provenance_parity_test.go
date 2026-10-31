package debug_test

import (
	"testing"

	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
)

func TestLiveBridgeProtectsAndRecordsProvenance(t *testing.T) {
	calls := 0
	initial, err := s.CheckInitial(model(&calls).Model, 10)
	if err != nil {
		t.Fatal(err)
	}
	limits := d.DefaultLiveLimits()
	limits.MaxEvents, limits.MaxEventBytes, limits.MaxEffectOrigins = 7, 2048, 9
	for _, key := range []string{"stateless.debug.origin", "stateless.debug.live.max_events", "stateless.debug.custom"} {
		config := s.RunConfig{Strategy: "manual", Parameters: [][2]string{{key, "forged"}}}
		if _, err := d.AttachLiveBridge[int, int, int](4, 0, initial, config, s.DefaultRecorderOptions(), limits); err == nil {
			t.Fatalf("accepted reserved parameter %s", key)
		}
	}
	config := s.RunConfig{Strategy: "manual", Parameters: [][2]string{{"caller", "value"}}}
	bridge, err := d.AttachLiveBridge[int, int, int](4, 0, initial, config, s.DefaultRecorderOptions(), limits)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{"caller": "value", "stateless.debug.origin": "recorded-live-observation", "stateless.debug.host_epoch": "4", "stateless.debug.live.max_events": "7", "stateless.debug.live.max_event_bytes": "2048", "stateless.debug.live.max_effect_origins": "9"}
	for _, pair := range bridge.Export().Config.Parameters {
		if value, ok := want[pair[0]]; ok {
			if pair[1] != value {
				t.Fatalf("%s=%q want %q", pair[0], pair[1], value)
			}
			delete(want, pair[0])
		}
	}
	if len(want) != 0 {
		t.Fatalf("missing provenance: %v", want)
	}
	if len(config.Parameters) != 1 || config.Parameters[0][0] != "caller" {
		t.Fatal("caller config mutated")
	}
}
