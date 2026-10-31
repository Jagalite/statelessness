package debug_test

import (
	"testing"

	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
)

func TestLiveEventsPreserveDispatchOriginAndGapEvidence(t *testing.T) {
	calls := 0
	m := model(&calls).Model
	initial, err := s.CheckInitial(m, 10)
	if err != nil {
		t.Fatal(err)
	}
	bridge, err := d.AttachLiveBridge[int, int, int](4, 0, initial, s.RunConfig{Strategy: "manual"}, s.DefaultRecorderOptions(), d.DefaultLiveLimits())
	if err != nil {
		t.Fatal(err)
	}
	turn, err := s.CheckTurn(m, 10, 1, s.Accept(11, 11), 1, s.FullChecks())
	if err != nil {
		t.Fatal(err)
	}
	if err = bridge.Observe(4, 1, turn); err != nil {
		t.Fatal(err)
	}
	origin := d.EffectOrigin{Epoch: 4, Transition: 1, OutputIndex: 0}
	if err = bridge.EffectDispatched(origin); err != nil {
		t.Fatal(err)
	}
	turn, err = s.CheckTurn(m, 11, 1, s.Accept[int, int](12), 3, s.FullChecks())
	if err != nil {
		t.Fatal(err)
	}
	if err = bridge.Observe(4, 3, turn); err == nil {
		t.Fatal("host sequence gap accepted")
	}
	var dispatch, gap *d.LiveEvent
	for {
		event, ok := bridge.PopEvent()
		if !ok {
			break
		}
		switch event.Kind {
		case "effect-dispatched":
			copy := event
			dispatch = &copy
		case "gap":
			copy := event
			gap = &copy
		}
	}
	if dispatch == nil || dispatch.Origin != origin || dispatch.HostSequence != 1 {
		t.Fatalf("dispatch evidence: %+v", dispatch)
	}
	if gap == nil || gap.ExpectedSequence != 2 || gap.ActualSequence != 3 || gap.HostSequence != 3 {
		t.Fatalf("gap evidence: %+v", gap)
	}
	if bridge.Health().CaptureComplete {
		t.Fatal("gap did not end exact capture")
	}
}
