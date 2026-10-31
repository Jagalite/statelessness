package debug_test

import (
	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
	"testing"
)

func TestLiveDroppedTurnPreservesRetainedDisposition(t *testing.T) {
	for _, bound := range []string{"events", "bytes"} {
		t.Run(bound, func(t *testing.T) {
			calls := 0
			m := model(&calls).Model
			initial, err := s.CheckInitial(m, 10)
			if err != nil {
				t.Fatal(err)
			}
			limits := d.DefaultLiveLimits()
			if bound == "events" {
				limits.MaxEvents = 1
			} else {
				limits.MaxEventBytes = 128
			}
			bridge, err := d.AttachLiveBridge[int, int, int](4, 0, initial, s.RunConfig{Strategy: "manual"}, s.DefaultRecorderOptions(), limits)
			if err != nil {
				t.Fatal(err)
			}
			if event, ok := bridge.PopEvent(); !ok || event.Kind != "checkpoint" {
				t.Fatal(event, ok)
			}
			for i, disposition := range []s.Disposition{s.Accepted(), s.Rejected("not allowed")} {
				sequence := uint64(i + 1)
				turn, err := s.CheckTurn(m, 10, 0, s.Transition[int, int]{State: 10, Disposition: disposition}, sequence, s.FullChecks())
				if err != nil {
					t.Fatal(err)
				}
				if err := bridge.Observe(4, sequence, turn); err != nil {
					t.Fatal(err)
				}
			}
			want := d.LiveEvent{Epoch: 4, StreamSequence: 2, HostSequence: 1, Kind: "turn", FullChecking: true, Disposition: "accepted"}
			if event, ok := bridge.PopEvent(); !ok || event != want {
				t.Fatalf("retained event changed: got %+v (present %v), want %+v", event, ok, want)
			}
			if event, ok := bridge.PopEvent(); ok {
				t.Fatalf("dropped event retained: %+v", event)
			}
			if h := bridge.Health(); h.EventDrops != 1 || h.RecordedSteps != 2 || h.HostSequence != 2 || !h.CaptureComplete {
				t.Fatalf("unexpected health: %+v", h)
			}
			turn, err := s.CheckTurn(m, 10, 0, s.Transition[int, int]{State: 10, Disposition: s.Ignored("ignored")}, 3, s.FullChecks())
			if err != nil {
				t.Fatal(err)
			}
			if err := bridge.Observe(4, 3, turn); err != nil {
				t.Fatal(err)
			}
			if event, ok := bridge.PopEvent(); !ok || event.StreamSequence != 4 || event.HostSequence != 3 || event.Disposition != "ignored" {
				t.Fatalf("wrong event after drop: %+v (present %v)", event, ok)
			}
			if calls != 0 {
				t.Fatal("bridge reexecuted reducer")
			}
		})
	}
}
