package reference

import (
	"errors"
	"runtime"
	"testing"
	"time"
)

func TestHostExecutesOrderedEffectsAndAcknowledgesCancellation(t *testing.T) {
	started := make(chan uint64, 2)
	release := make(chan struct{})
	host, err := NewHost(DefaultBounds(), func(g uint64, _ uint8) string { started <- g; <-release; return "success" }, true)
	if err != nil {
		t.Fatal(err)
	}
	defer host.Close()
	if _, err = host.Deliver(Input{Kind: "submit"}); err != nil {
		t.Fatal(err)
	}
	if _, err = host.Deliver(Input{Kind: "submit"}); err != nil {
		t.Fatal(err)
	}
	<-started
	<-started
	close(release)
	for i := 0; i < 2; i++ {
		ok, err := host.Receive(time.Second)
		if err != nil || !ok {
			t.Fatal(ok, err)
		}
	}
	snapshot, err := host.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	state, err := host.State()
	if err != nil {
		t.Fatal(err)
	}
	if len(snapshot.Published) != 1 || snapshot.Published[0] != 2 || snapshot.Settled[1] != "cancelled" || snapshot.Settled[2] != "succeeded" || snapshot.PendingWorkers != 0 || state.Pending() != 0 {
		t.Fatalf("host state: %+v", host)
	}
	recording, err := host.Recording()
	if err != nil || recording == nil || len(snapshot.Diagnostics) != 0 {
		t.Fatalf("recording: %+v", snapshot.Diagnostics)
	}
	snapshot.Settled[1] = "forged"
	snapshot.Published[0] = 99
	again, err := host.Snapshot()
	if err != nil || again.Settled[1] != "cancelled" || again.Published[0] != 2 {
		t.Fatal("snapshot mutated host authority")
	}
}
func TestHostTimeoutAndCloseDoNotFabricateSettlement(t *testing.T) {
	release := make(chan struct{})
	host, err := NewHost(DefaultBounds(), func(uint64, uint8) string { <-release; return "success" }, false)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = host.Deliver(Input{Kind: "submit"}); err != nil {
		t.Fatal(err)
	}
	if ok, err := host.Receive(time.Millisecond); err != nil || ok {
		t.Fatal(ok, err)
	}
	host.Close()
	close(release)
	snapshot, _ := host.Snapshot()
	state, _ := host.State()
	if len(snapshot.Settled) != 0 || state.Pending() != 1 {
		t.Fatal("close invented settlement")
	}
}

func TestHostRejectsConcurrentOperationsWithoutLosingQueuedEvents(t *testing.T) {
	release := make(chan struct{})
	started := make(chan struct{})
	host, err := NewHost(DefaultBounds(), func(uint64, uint8) string { close(started); <-release; return "success" }, false)
	if err != nil {
		t.Fatal(err)
	}
	defer host.Close()
	if _, err = host.Deliver(Input{Kind: "submit"}); err != nil {
		t.Fatal(err)
	}
	<-started
	done := make(chan error, 1)
	go func() { _, err := host.Receive(time.Second); done <- err }()
	for !host.busy.Load() {
		runtime.Gosched()
	}
	if _, err = host.Deliver(Input{Kind: "submit"}); !errors.Is(err, ErrHostBusy) {
		t.Fatalf("concurrent delivery: %v", err)
	}
	if _, err = host.State(); !errors.Is(err, ErrHostBusy) {
		t.Fatalf("concurrent view: %v", err)
	}
	if err = host.Close(); !errors.Is(err, ErrHostBusy) {
		t.Fatalf("concurrent close: %v", err)
	}
	close(release)
	if err = <-done; err != nil {
		t.Fatal(err)
	}
	snapshot, err := host.Snapshot()
	if err != nil || snapshot.Settled[1] != "succeeded" {
		t.Fatal(snapshot, err)
	}
}
func TestHostGoexitPostsFailureWithoutFalseCancellationAck(t *testing.T) {
	host, err := NewHost(DefaultBounds(), func(uint64, uint8) string { runtime.Goexit(); return "success" }, false)
	if err != nil {
		t.Fatal(err)
	}
	defer host.Close()
	if _, err = host.Deliver(Input{Kind: "submit"}); err != nil {
		t.Fatal(err)
	}
	if ok, err := host.Receive(time.Second); err != nil || !ok {
		t.Fatal(ok, err)
	}
	state, err := host.State()
	if err != nil || state.Jobs[0].Phase != "waiting" {
		t.Fatalf("missing failure completion: %+v %v", state, err)
	}
}

func TestHostGoexitAfterCancelReportsFailureNotAcknowledgment(t *testing.T) {
	gate := make(chan struct{})
	started := make(chan struct{})
	host, err := NewHost(DefaultBounds(), func(uint64, uint8) string { close(started); <-gate; runtime.Goexit(); return "success" }, false)
	if err != nil {
		t.Fatal(err)
	}
	defer host.Close()
	if _, err = host.Deliver(Input{Kind: "submit"}); err != nil {
		t.Fatal(err)
	}
	<-started
	if _, err = host.Deliver(Input{Kind: "cancel", Generation: 1}); err != nil {
		t.Fatal(err)
	}
	worker := host.workers[workerID{1, 1}]
	close(gate)
	<-worker.done
	host.queueMu.Lock()
	queued := append([]Input(nil), host.queue...)
	host.queueMu.Unlock()
	if len(queued) != 1 || queued[0].Kind != "complete" || queued[0].Outcome != "retryable_failure" {
		t.Fatalf("fabricated cancellation acknowledgment: %+v", queued)
	}
	if ok, err := host.Receive(time.Second); err != nil || !ok {
		t.Fatal(ok, err)
	}
}
