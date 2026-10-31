package main

import (
	"errors"
	"sync/atomic"
	"testing"
	"time"

	s "github.com/Jagalite/statelessness/go"
)

// This test deliberately uses only the installed module's public API. Two
// factories rendezvous before cancellation so the expected accounting does not
// depend on worker completion order.
func TestPublicCampaignCancellationDrainsClaimedWorkers(t *testing.T) {
	c := s.DefaultCampaignConfig()
	c.FirstJob, c.Jobs, c.Workers, c.ResultBuffer = 0, 8, 2, 0
	c.Fuzz = s.FuzzConfig{Cases: 2, MaxSteps: 3, MaxTransitions: 6}
	var cancelled atomic.Bool
	var entered atomic.Int64
	both := make(chan struct{})
	var delivered []s.JobReport[uint64]
	report, err := s.RunCampaign(c, s.RunLimits{Cancellation: &cancelled}, func(id uint64) (s.CampaignModel[int, uint64, int], error) {
		if entered.Add(1) == 2 {
			cancelled.Store(true)
			close(both)
		}
		select {
		case <-both:
		case <-time.After(10 * time.Second):
			return s.CampaignModel[int, uint64, int]{}, errors.New("workers did not rendezvous")
		}
		model := s.GenerateModel[int, uint64, int]{Model: s.Model[int, uint64, int]{
			InitialState: func() (int, error) { return 0, nil },
			Step: func(state int, input uint64) (s.Transition[int, int], error) {
				return s.Accept[int, int](state + int(input)), nil
			},
			CheckState: func(int) ([]s.Check, error) { return nil, nil },
			CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[uint64], CloneOutput: s.ValueCopy[int],
			EqualStates: func(a, b int) bool { return a == b },
		}, Generate: func(int, *s.Rng) (uint64, bool, error) { return 1, true, nil }}
		return s.CampaignModel[int, uint64, int]{Model: model, Metadata: func() (s.Metadata, error) { return s.Identity("public-host", "v1"), nil }}, nil
	}, func(job s.JobReport[uint64]) error { delivered = append(delivered, job); return nil })
	if err != nil || report.Termination.Kind != "cancelled" || !report.AccountingComplete || report.Started != 2 || report.Finished != 2 || report.Delivered != 2 || report.NotStarted != 6 || len(delivered) != 2 {
		t.Fatalf("unexpected accounting: %v %+v %+v", err, report, delivered)
	}
	seen := map[uint64]bool{}
	for _, job := range delivered {
		if seen[job.JobID] || job.Outcome.Report == nil || job.Outcome.Report.Termination != "cancelled" {
			t.Fatalf("unexpected report: %+v", job)
		}
		seen[job.JobID] = true
	}
	if !seen[0] || !seen[1] {
		t.Fatalf("wrong claimed jobs: %+v", seen)
	}
}

// Job zero cannot finish until the sink has received job one. The callbacks
// check their job-bound inputs and record exactly one step for each job.
func TestPublicCampaignOutOfOrderAndJobBoundInputs(t *testing.T) {
	c := s.DefaultCampaignConfig()
	c.FirstJob, c.Jobs, c.Workers, c.ResultBuffer = 0, 2, 2, 0
	c.Fuzz = s.FuzzConfig{Cases: 1, MaxSteps: 1, MaxTransitions: 1}
	release := make(chan struct{})
	var calls [2]atomic.Int64
	var delivered []uint64
	report, err := s.RunCampaign(c, s.RunLimits{}, func(id uint64) (s.CampaignModel[int, uint64, int], error) {
		model := s.GenerateModel[int, uint64, int]{Model: s.Model[int, uint64, int]{
			InitialState: func() (int, error) { return 0, nil },
			Step: func(state int, input uint64) (s.Transition[int, int], error) {
				if id >= uint64(len(calls)) || calls[id].Add(1) != 1 || input != id {
					return s.Transition[int, int]{}, errors.New("factory state was shared or input changed")
				}
				if id == 0 {
					select {
					case <-release:
					case <-time.After(10 * time.Second):
						return s.Transition[int, int]{}, errors.New("job one was not delivered")
					}
				}
				return s.Accept[int, int](state + 1), nil
			},
			CheckState: func(int) ([]s.Check, error) { return nil, nil },
			CloneState: s.ValueCopy[int], CloneInput: s.ValueCopy[uint64], CloneOutput: s.ValueCopy[int],
			EqualStates: func(a, b int) bool { return a == b },
		}, Generate: func(int, *s.Rng) (uint64, bool, error) { return id, true, nil }}
		return s.CampaignModel[int, uint64, int]{Model: model, Metadata: func() (s.Metadata, error) { return s.Identity("public-order", "v1"), nil }}, nil
	}, func(job s.JobReport[uint64]) error {
		delivered = append(delivered, job.JobID)
		if job.JobID == 1 {
			close(release)
		}
		return nil
	})
	if err != nil || report.Termination.Kind != "jobs_completed" || !report.AccountingComplete || report.Started != 2 || report.Finished != 2 || report.Delivered != 2 || report.NotStarted != 0 || report.Errors != 0 {
		t.Fatalf("unexpected accounting: %v %+v", err, report)
	}
	if len(delivered) != 2 || delivered[0] != 1 || delivered[1] != 0 || calls[0].Load() != 1 || calls[1].Load() != 1 {
		t.Fatalf("unexpected order or factory calls: %v %d %d", delivered, calls[0].Load(), calls[1].Load())
	}
}
