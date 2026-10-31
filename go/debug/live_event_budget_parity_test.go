package debug_test

import (
	"testing"
	"unsafe"

	s "github.com/Jagalite/statelessness/go"
	d "github.com/Jagalite/statelessness/go/debug"
)

func TestLiveAttachmentRequiresSpaceForOneEvent(t *testing.T) {
	calls := 0
	initial, err := s.CheckInitial(model(&calls).Model, 10)
	if err != nil {
		t.Fatal(err)
	}
	size := int(unsafe.Sizeof(d.LiveEvent{}))
	limits := d.DefaultLiveLimits()
	limits.MaxEventBytes = size - 1
	if _, err = d.AttachLiveBridge[int, int, int](4, 0, initial, s.RunConfig{Strategy: "manual"}, s.DefaultRecorderOptions(), limits); err == nil {
		t.Fatal("accepted a budget smaller than one event")
	}
	limits.MaxEventBytes = size
	bridge, err := d.AttachLiveBridge[int, int, int](4, 0, initial, s.RunConfig{Strategy: "manual"}, s.DefaultRecorderOptions(), limits)
	if err != nil {
		t.Fatal(err)
	}
	if event, ok := bridge.PopEvent(); !ok || event.Kind != "checkpoint" {
		t.Fatalf("checkpoint missing: %+v", event)
	}
	if bridge.Health().EventDrops != 0 {
		t.Fatal("checkpoint dropped at exact event budget")
	}
}
