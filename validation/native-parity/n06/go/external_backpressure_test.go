//go:build n06_causal_probe

package main

import (
	"fmt"
	"sync/atomic"
	"testing"
	"time"

	s "github.com/Jagalite/statelessness/go"
)

// The overlay adds only observational hooks; the package's production channel
// and fuzz runner remain the ones built from the installed module.
func TestInstalledInstrumentedBackpressure(t *testing.T) {
	for _, buffer := range []int{0, 1} {
		t.Run(fmt.Sprint(buffer), func(t *testing.T) {
			c := s.DefaultCampaignConfig64()
			c.FirstJob, c.Jobs, c.Workers, c.ResultBuffer = 0, 20, 2, uint64(buffer)
			c.Fuzz = s.FuzzConfig64{Cases: 1, MaxSteps: 1, MaxTransitions: 1}
			entered := make(chan struct{})
			blocked := make(chan uint64, 20)
			var started, completed, sent atomic.Int64
			var phase [20]atomic.Uint32
			var held atomic.Bool
			sinks := 0
			r, err := s.RunN06CausalProbe(c, s.RunLimits{}, func(id uint64) (s.CampaignModel[int, uint64, int], error) {
				started.Add(1)
				m := s.GenerateModel[int, uint64, int]{Model: s.Model[int, uint64, int]{InitialState: func() (int, error) { return 0, nil }, Step: func(x int, _ uint64) (s.Transition[int, int], error) { return s.Accept[int, int](x + 1), nil }, CheckState: func(int) ([]s.Check, error) { return nil, nil }, CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[uint64], CloneOutput: s.ValueCopy[int], EqualStates: func(a, b int) bool { return a == b }}, Generate: func(int, *s.Rng) (uint64, bool, error) { return 1, true, nil }}
				return s.CampaignModel[int, uint64, int]{Model: m, Metadata: func() (s.Metadata, error) { return s.Identity("n06-probe", "v1"), nil }}, nil
			}, func(j s.JobReport64[uint64]) error {
				sinks++
				if sinks != 1 {
					return nil
				}
				if j.JobID != 0 {
					t.Errorf("first job %d", j.JobID)
				}
				held.Store(true)
				defer held.Store(false)
				close(entered)
				seen := map[uint64]bool{}
				for i := 0; i < 2; i++ {
					select {
					case id := <-blocked:
						if seen[id] {
							t.Errorf("duplicate blocked %d", id)
						}
						seen[id] = true
					case <-time.After(10 * time.Second):
						return fmt.Errorf("blocked admission timeout")
					}
				}
				for id := range seen {
					if phase[id].Load() != 2 {
						t.Errorf("sender %d progressed", id)
					}
				}
				bound := int64(buffer + 3)
				if started.Load() != bound || completed.Load() != bound || sent.Load() != int64(buffer+1) {
					t.Errorf("held sink starts/completes/sent %d/%d/%d", started.Load(), completed.Load(), sent.Load())
				}
				return nil
			}, s.N06CausalHooks{AfterComplete: func(id uint64) {
				phase[id].Store(1)
				completed.Add(1)
				if id != 0 {
					<-entered
				}
			}, SendBlocked: func(id uint64) {
				if id != 0 {
					if !phase[id].CompareAndSwap(1, 2) {
						t.Errorf("bad phase %d", id)
					}
					blocked <- id
				}
			}, AfterSend: func(id uint64) {
				p := phase[id].Swap(3)
				if p == 2 && held.Load() {
					t.Errorf("send returned while sink held: %d", id)
				}
				sent.Add(1)
			}})
			if err != nil || r.Termination.Kind != "jobs_completed" || !r.AccountingComplete || r.Started != 20 || r.Finished != 20 || r.Delivered != 20 || sinks != 20 {
				t.Fatalf("campaign: %v %+v sinks=%d", err, r, sinks)
			}
		})
	}
}
