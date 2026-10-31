// A production-shaped host runs worker effects outside the reducer. The same
// controller model verifies the exact execution captured by the host.
package main

import (
	"fmt"
	"os"
	"time"

	s "github.com/Jagalite/statelessness/go"
	"github.com/Jagalite/statelessness/go/reference"
)

func run() error {
	started := make(chan uint64, 2)
	release := make(chan struct{})
	host, err := reference.NewHost(reference.DefaultBounds(), func(generation uint64, _ uint8) string {
		started <- generation
		<-release // A real host would do bounded I/O here.
		return "success"
	}, true)
	if err != nil {
		return err
	}
	defer host.Close()
	// A second request supersedes the first. The old worker may finish, but its
	// completion cannot publish the now-obsolete result.
	for i := 0; i < 2; i++ {
		if _, err = host.Deliver(reference.Input{Kind: "submit"}); err != nil {
			return err
		}
	}
	<-started
	<-started
	close(release)
	for i := 0; i < 2; i++ {
		received, e := host.Receive(5 * time.Second)
		if e != nil {
			return e
		}
		if !received {
			return fmt.Errorf("timed out waiting for worker completion")
		}
	}
	snapshot, err := host.Snapshot()
	if err != nil {
		return err
	}
	if len(snapshot.Published) != 1 || snapshot.Published[0] != 2 || snapshot.Settled[1] != "cancelled" || snapshot.Settled[2] != "succeeded" || snapshot.PendingWorkers != 0 || len(snapshot.Diagnostics) != 0 {
		return fmt.Errorf("unexpected host outcome: %+v", snapshot)
	}
	audit, err := host.Recording()
	if err != nil {
		return err
	}
	if audit == nil {
		return fmt.Errorf("recording unavailable")
	}
	trace, _, err := audit.Portable()
	if err != nil {
		return err
	}
	replay, err := s.Replay((reference.Controller{Bounds: reference.DefaultBounds()}).Model(), &trace, false)
	if err != nil {
		return err
	}
	if replay.Outcome != "exact" {
		return fmt.Errorf("recorded execution did not replay exactly: %s", replay.Outcome)
	}
	fmt.Printf("published=%v settled[1]=%s settled[2]=%s replay=%s\n", snapshot.Published, snapshot.Settled[1], snapshot.Settled[2], replay.Outcome)
	return nil
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
