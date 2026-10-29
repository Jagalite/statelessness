package statelessness

import (
	"errors"
	"fmt"
	"math"
	"reflect"
	"runtime"
	"sort"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func campaignTestConfig(workers int) CampaignConfig {
	c := DefaultCampaignConfig()
	c.MasterSeed, c.FirstJob, c.Jobs, c.Workers = 17, 50, 12, workers
	c.Fuzz = FuzzConfig{Seed: 999, Cases: 2, MaxSteps: 4, MaxTransitions: 8, MutationPercent: 50}
	return c
}
func campaignTestModel(id uint64, fail bool) CampaignModel[int, uint64, int] {
	g := GenerateModel[int, uint64, int]{Model: Model[int, uint64, int]{
		InitialState: func() (int, error) { return 0, nil },
		Step:         func(s int, _ uint64) (Transition[int, int], error) { return Accept[int, int](s + 1), nil },
		CheckState: func(s int) ([]Check, error) {
			check := Passed("fixture")
			if fail && s == 3 {
				check = Failed("fixture", "third step")
			}
			return []Check{check, Skipped("optional", "fixture")}, nil
		},
		CloneState: ValueCopy[int], CloneInput: ValueCopy[uint64], CloneOutput: ValueCopy[int],
		EqualStates: func(a, b int) bool { return a == b },
	}, Generate: func(_ int, r *Rng) (uint64, bool, error) { return r.NextU64(), true, nil }}
	return CampaignModel[int, uint64, int]{Model: g, Metadata: func() (Metadata, error) { return Identity(fmt.Sprintf("job-%d", id), "fixture"), nil }}
}
func campaignTestRunner(_ uint64, g GenerateModel[int, uint64, int], c FuzzConfig, limits RunLimits, _ *campaignPanic) (*FuzzReport[uint64], error) {
	return FuzzWithLimits(g, c, limits)
}

func TestCampaignSeedsAndValidation(t *testing.T) {
	for _, x := range []struct{ master, id, seed uint64 }{
		{0, 0, 0xe220a8397b1dcdaf}, {0, 7, 7191089600892374487},
		{math.MaxUint64, 1, 0xe220a8397b1dcdaf}, {1, math.MaxUint64, 0xe220a8397b1dcdaf},
	} {
		if got := JobSeed(x.master, x.id); got != x.seed {
			t.Fatalf("seed %+v = %d", x, got)
		}
	}
	c := campaignTestConfig(1)
	c.FirstJob, c.Jobs = math.MaxUint64, 1
	if e := c.Validate(); e != nil {
		t.Fatal(e)
	}
	c.Jobs = 2
	if e := c.Validate(); e == nil || e.Error() != "campaign job ID range overflows u64" {
		t.Fatal(e)
	}
	base := campaignTestConfig(1)
	bad := []func(*CampaignConfig){func(c *CampaignConfig) { c.Jobs = 0 }, func(c *CampaignConfig) { c.Workers = 0 }, func(c *CampaignConfig) { c.Workers = 257 }, func(c *CampaignConfig) { c.ResultBuffer = -1 }, func(c *CampaignConfig) { c.ResultBuffer = 1025 }, func(c *CampaignConfig) { c.Fuzz.MutationPercent = 101 }, func(c *CampaignConfig) { c.Fuzz.Cases = -1 }, func(c *CampaignConfig) { c.Fuzz.MaxSteps = -1 }}
	for _, mutate := range bad {
		c := base
		mutate(&c)
		_, e := RunCampaign(c, RunLimits{}, func(uint64) (CampaignModel[int, uint64, int], error) {
			t.Error("invalid factory")
			return campaignTestModel(0, false), nil
		}, func(JobReport[uint64]) error { t.Error("invalid sink"); return nil })
		if e == nil {
			t.Fatal("invalid admission accepted")
		}
	}
	c = base
	c.Jobs = 1
	c.Fuzz.Cases = int(^uint(0) >> 1)
	c.Fuzz.MaxSteps = int(^uint(0) >> 1)
	c.Fuzz.MaxTransitions = 1
	if e := c.Validate(); e != nil {
		t.Fatalf("wide product capped safely: %v", e)
	}
	if ^uint(0)>>63 != 0 {
		c.Jobs = 3
		if e := c.Validate(); e == nil || e.Error() != "campaign aggregate cases overflow u64" {
			t.Fatal(e)
		}
		c.Fuzz.Cases = 1
		c.Fuzz.MaxTransitions = math.MaxUint64
		if e := c.Validate(); e == nil || e.Error() != "campaign aggregate transitions overflow u64" {
			t.Fatal(e)
		}
	}
}

func TestCampaignDeterministicJobsAndRange(t *testing.T) {
	run := func(c CampaignConfig) (CampaignReport, []JobReport[uint64]) {
		var jobs []JobReport[uint64]
		r, e := RunCampaign(c, RunLimits{}, func(id uint64) (CampaignModel[int, uint64, int], error) { return campaignTestModel(id, true), nil }, func(j JobReport[uint64]) error { jobs = append(jobs, j); return nil })
		if e != nil {
			t.Fatal(e)
		}
		sort.Slice(jobs, func(i, j int) bool { return jobs[i].JobID < jobs[j].JobID })
		return r, jobs
	}
	r, one := run(campaignTestConfig(1))
	parallel, many := run(campaignTestConfig(4))
	if !reflect.DeepEqual(r, parallel) || !reflect.DeepEqual(one, many) {
		t.Fatal("job identity differs by worker assignment")
	}
	if r.Started != 12 || r.Finished != 12 || r.Delivered != 12 || r.Failures != 12 || r.ReportedCases != 12 || r.ReportedTransitions != 36 || r.ReportedSkippedChecks != 48 || !r.AccountingComplete || r.Termination.Kind != "jobs_completed" {
		t.Fatalf("%+v", r)
	}
	c := campaignTestConfig(1)
	c.FirstJob = 54
	c.Jobs = 1
	_, slice := run(c)
	if !reflect.DeepEqual(slice[0], one[4]) {
		t.Fatal("range replay differs")
	}
	for _, j := range one {
		ref := campaignTestModel(j.JobID, true)
		expected, e := FuzzWithLimits(ref.Model, j.Config, RunLimits{})
		if e != nil || !reflect.DeepEqual(j.Outcome.Report, expected) || j.Seed != JobSeed(17, j.JobID) || j.Config.Seed != j.Seed {
			t.Fatal("per-job public fuzz differs")
		}
	}
}

func TestCampaignGuidedFactoryOrderAndIsolation(t *testing.T) {
	var log []string
	var flag atomic.Bool
	c := campaignTestConfig(1)
	c.Jobs = 1
	r, e := RunGuidedCampaign(c, RunLimits{Cancellation: &flag}, DefaultGuidanceConfig(), func(id uint64) (CampaignModel[int, uint64, int], error) {
		log = append(log, "factory")
		flag.Store(true)
		m := campaignTestModel(id, false)
		m.Metadata = func() (Metadata, error) { log = append(log, "model metadata"); return Identity("local", "test"), nil }
		m.Close = func() error { log = append(log, "model close"); return nil }
		return m, nil
	}, func(_ uint64, g GenerateModel[int, uint64, int]) (CampaignFeedback[int, uint64, int, int], error) {
		log = append(log, "feedback factory")
		f := ExactStateFeedback(g.Model)
		f.Metadata = func() (FeedbackMetadata, error) {
			log = append(log, "feedback metadata")
			return FeedbackMetadata{}, nil
		}
		f.InitialFeatures = func(int, *FeatureBuffer[int]) error { t.Error("pre-cancel initial feedback"); return nil }
		return CampaignFeedback[int, uint64, int, int]{Feedback: f, Close: func() error { log = append(log, "feedback close"); return nil }}, nil
	}, func(j JobReport[uint64]) error {
		log = append(log, "sink")
		if j.Metadata == nil || j.Outcome.Kind != "completed" || j.Outcome.Report.Termination != "cancelled" {
			t.Errorf("%+v", j)
		}
		return nil
	})
	want := []string{"factory", "model metadata", "feedback factory", "feedback metadata", "feedback close", "model close", "sink"}
	if e != nil || !reflect.DeepEqual(log, want) || r.Termination.Kind != "cancelled" {
		t.Fatalf("%v %+v %v", e, r, log)
	}
	for _, workers := range []int{1, 4} {
		c = campaignTestConfig(workers)
		var jobs []JobReport[uint64]
		r, e = RunGuidedCampaign(c, RunLimits{}, DefaultGuidanceConfig(), func(id uint64) (CampaignModel[int, uint64, int], error) { return campaignTestModel(id, false), nil }, func(_ uint64, g GenerateModel[int, uint64, int]) (CampaignFeedback[int, uint64, int, int], error) {
			return CampaignFeedback[int, uint64, int, int]{Feedback: ExactStateFeedback(g.Model)}, nil
		}, func(j JobReport[uint64]) error { jobs = append(jobs, j); return nil })
		if e != nil || r.Delivered != 12 {
			t.Fatal(e, r)
		}
		for _, j := range jobs {
			g := j.Outcome.Report.Guidance
			if g == nil || g.UniqueFeatures != 5 || len(g.Corpus) != 4 {
				t.Fatalf("nonlocal corpus: %+v", g)
			}
		}
	}
}

type campaignHostileError struct{}

func (campaignHostileError) Error() string { panic("hostile Error") }

type campaignHostilePanic struct{}

func (campaignHostilePanic) String() string { panic("hostile String") }

func TestCampaignErrorClassesAndHostileDiagnostics(t *testing.T) {
	for _, mode := range []string{"factory", "metadata_error", "metadata_panic", "step_error", "step_panic", "check_panic", "close_error", "close_panic", "hostile_factory", "hostile_close", "hostile_payload"} {
		t.Run(mode, func(t *testing.T) {
			c := campaignTestConfig(1)
			c.Jobs = 2
			c.StopOnFailure = true
			var jobs []JobReport[uint64]
			r, e := RunCampaign(c, RunLimits{}, func(id uint64) (CampaignModel[int, uint64, int], error) {
				m := campaignTestModel(id, false)
				switch mode {
				case "factory":
					return m, errors.New("factory")
				case "hostile_factory":
					return m, campaignHostileError{}
				case "metadata_error":
					m.Metadata = func() (Metadata, error) { return Metadata{}, errors.New("metadata") }
				case "metadata_panic":
					m.Metadata = func() (Metadata, error) { panic("metadata") }
				case "step_error":
					m.Model.Step = func(int, uint64) (Transition[int, int], error) { return Transition[int, int]{}, errors.New("step") }
				case "step_panic":
					m.Model.Step = func(int, uint64) (Transition[int, int], error) { panic("step") }
				case "check_panic":
					m.Model.CheckState = func(int) ([]Check, error) { panic("check") }
				case "close_error":
					m.Close = func() error { return errors.New("close") }
				case "hostile_close":
					m.Close = func() error { return campaignHostileError{} }
				case "close_panic":
					m.Close = func() error { panic("close") }
				case "hostile_payload":
					m.Model.Step = func(int, uint64) (Transition[int, int], error) { panic(campaignHostilePanic{}) }
				}
				return m, nil
			}, func(j JobReport[uint64]) error { jobs = append(jobs, j); return nil })
			if e != nil || r.Started != 2 || r.Errors != 2 || r.Failures != 0 || r.Termination.Kind != "jobs_completed" {
				t.Fatal(e, r)
			}
			factory := mode == "factory" || mode == "hostile_factory"
			want := "model_error"
			if factory {
				want = "factory_error"
			} else if strings.Contains(mode, "panic") || mode == "hostile_payload" {
				want = "panicked"
			}
			if r.AccountingComplete != factory {
				t.Fatal("wrong accounting", r)
			}
			for _, j := range jobs {
				if j.Outcome.Kind != want || (j.Metadata == nil) != (factory || strings.HasPrefix(mode, "metadata")) {
					t.Fatalf("%+v", j)
				}
				if mode == "hostile_factory" || mode == "hostile_close" {
					if j.Outcome.Error.Error() != "error diagnostic unavailable" {
						t.Fatal(j.Outcome)
					}
				}
				if mode == "hostile_payload" && j.Outcome.Panic != "non-string panic payload" {
					t.Fatal(j.Outcome)
				}
			}
		})
	}
	c := campaignTestConfig(1)
	c.Jobs = 1
	r, e := RunGuidedCampaign(c, RunLimits{}, DefaultGuidanceConfig(), func(id uint64) (CampaignModel[int, uint64, int], error) { return campaignTestModel(id, false), nil }, func(uint64, GenerateModel[int, uint64, int]) (CampaignFeedback[int, uint64, int, int], error) {
		return CampaignFeedback[int, uint64, int, int]{}, errors.New("feedback factory")
	}, func(j JobReport[uint64]) error {
		if j.Metadata == nil || j.Outcome.Kind != "model_error" {
			t.Errorf("%+v", j)
		}
		return nil
	})
	if e != nil || r.Errors != 1 || r.AccountingComplete {
		t.Fatal(e, r)
	}
}

func campaignWait(t *testing.T, ch <-chan struct{}) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(5 * time.Second):
		t.Fatal("campaign barrier timeout")
	}
}

func TestCampaignRealWorkersOutOfOrderAndCallerSink(t *testing.T) {
	// The stack header is only a test observation of goroutine identity.
	gid := func() string {
		var b [80]byte
		n := runtime.Stack(b[:], false)
		return strings.Fields(string(b[:n]))[1]
	}
	caller := gid()
	for _, workers := range []int{1, 2} {
		c := campaignTestConfig(workers)
		c.Jobs = 1
		_, e := RunCampaign(c, RunLimits{}, func(id uint64) (CampaignModel[int, uint64, int], error) {
			if (gid() == caller) != (workers == 1) {
				t.Error("configured worker path")
			}
			return campaignTestModel(id, false), nil
		}, func(JobReport[uint64]) error {
			if gid() != caller {
				t.Error("sink left caller")
			}
			return nil
		})
		if e != nil {
			t.Fatal(e)
		}
	}
	c := campaignTestConfig(2)
	c.FirstJob = 0
	c.Jobs = 2
	c.ResultBuffer = 0
	firstEntered := make(chan struct{})
	release := make(chan struct{})
	var order []uint64
	r, e := RunCampaign(c, RunLimits{}, func(id uint64) (CampaignModel[int, uint64, int], error) {
		if id == 0 {
			close(firstEntered)
			<-release
		} else {
			<-firstEntered
		}
		return campaignTestModel(id, false), nil
	}, func(j JobReport[uint64]) error {
		order = append(order, j.JobID)
		if j.JobID == 1 {
			close(release)
		}
		return nil
	})
	if e != nil || r.Delivered != 2 || !reflect.DeepEqual(order, []uint64{1, 0}) {
		t.Fatal(e, r, order)
	}
}

func TestCampaignRendezvousSlowSinkBoundDrainAndFirstError(t *testing.T) {
	for _, buffer := range []int{0, 1} {
		for _, mode := range []string{"success", "error", "panic", "hostile"} {
			t.Run(fmt.Sprintf("buffer%d-%s", buffer, mode), func(t *testing.T) {
				c := campaignTestConfig(2)
				c.FirstJob = 0
				c.Jobs = 20
				c.ResultBuffer = buffer
				sinkEntered := make(chan struct{})
				blocked := make(chan uint64, c.Jobs)
				// Each report moves Complete -> rejected admission -> sent, or
				// Complete -> sent when the native channel immediately accepts.
				const complete, waiting, sent = 1, 2, 3
				phases := make([]atomic.Uint32, c.Jobs)
				var started, completed, sendReturns, closeCalls atomic.Int64
				var sinkHeld atomic.Bool
				bound := buffer + c.Workers + 1
				sinks := 0
				r, e := runCampaignUsing(c, RunLimits{}, func(id uint64) (CampaignModel[int, uint64, int], error) {
					started.Add(1)
					m := campaignTestModel(id, false)
					m.Close = func() error {
						closeCalls.Add(1)
						if mode != "success" {
							return errors.New("cleanup")
						}
						return nil
					}
					return m, nil
				}, func(j JobReport[uint64]) error {
					sinks++
					if sinks != 1 {
						return nil
					}
					if j.JobID != 0 {
						t.Errorf("first receipt=%d", j.JobID)
					}
					sinkHeld.Store(true)
					defer sinkHeld.Store(false)
					close(sinkEntered)
					// All noninitial reports attempt transport only after this
					// sink is held. Each witness therefore means native select
					// rejected admission with no receiver able to free capacity;
					// it cannot be a stale pre-receipt blocked notification.
					waitingIDs := make(map[uint64]bool)
					for i := 0; i < c.Workers; i++ {
						select {
						case id := <-blocked:
							if waitingIDs[id] {
								t.Errorf("duplicate blocked report %d", id)
							}
							waitingIDs[id] = true
						case <-time.After(5 * time.Second):
							t.Error("workers did not reach actual blocked-send paths")
							return errors.New("barrier timeout")
						}
					}
					for id := range waitingIDs {
						if phases[id].Load() != waiting {
							t.Errorf("blocked report %d progressed before receipt", id)
						}
					}
					if started.Load() != int64(bound) || completed.Load() != int64(bound) || sendReturns.Load() != int64(buffer+1) {
						t.Errorf("held sink: starts=%d completes=%d send returns=%d; want %d/%d/%d", started.Load(), completed.Load(), sendReturns.Load(), bound, bound, buffer+1)
					}
					// Both workers are on proven non-admissible sends. Neither
					// can return its send or claim again until this sink releases
					// the caller to receive. No sleep/absence window is the proof.
					switch mode {
					case "success":
						return nil
					case "panic":
						panic("sink panic")
					case "hostile":
						return campaignHostileError{}
					default:
						return errors.New("first sink")
					}
				}, campaignTestRunner, campaignRuntime{
					afterComplete: func(id uint64) {
						phases[id].Store(complete)
						completed.Add(1)
						if id != 0 {
							<-sinkEntered
						}
					},
					sendBlocked: func(id uint64) {
						// Job0 may try before the caller reaches its first receive.
						// It is deliberately excluded from the held-sink witnesses.
						if id == 0 {
							return
						}
						if !phases[id].CompareAndSwap(complete, waiting) {
							t.Errorf("blocked before Complete or twice: %d", id)
						}
						blocked <- id
					},
					afterSend: func(id uint64) {
						prior := phases[id].Swap(sent)
						if prior != complete && prior != waiting {
							t.Errorf("send without Complete: %d", id)
						}
						if prior == waiting && sinkHeld.Load() {
							t.Errorf("blocked send %d returned while sink held", id)
						}
						sendReturns.Add(1)
					},
				})
				if mode == "success" {
					if e != nil || r.Termination.Kind != "jobs_completed" || r.Started != c.Jobs || r.Finished != c.Jobs || r.Delivered != c.Jobs || r.Errors != 0 || sinks != c.Jobs || !r.AccountingComplete {
						t.Fatalf("%v %+v sinks=%d", e, r, sinks)
					}
				} else {
					want := "first sink"
					if mode == "panic" {
						want = "campaign callback panicked: sink panic"
					}
					if mode == "hostile" {
						want = "error diagnostic unavailable"
					}
					if e != nil || r.Termination.Kind != "callback_error" || r.Termination.Error.Error() != want || sinks != 1 || r.Delivered != 0 || r.Started != r.Finished || r.Started != bound || r.Errors != bound || r.AccountingComplete {
						t.Fatalf("%v %+v sinks=%d", e, r, sinks)
					}
				}
				if closeCalls.Load() != int64(r.Started) || sendReturns.Load() != int64(r.Finished) {
					t.Fatalf("drain/cleanup: %+v closes=%d sends=%d", r, closeCalls.Load(), sendReturns.Load())
				}
				for id := 0; id < r.Started; id++ {
					if phases[id].Load() != sent {
						t.Errorf("report %d did not finish send before return", id)
					}
				}
			})
		}
	}
}

func TestCampaignControlsAdmissionAndBoundaries(t *testing.T) {
	var cancel atomic.Bool
	cancel.Store(true)
	zero := time.Duration(0)
	c := campaignTestConfig(4)
	for _, limits := range []RunLimits{{Cancellation: &cancel, MaxDuration: &zero}, {MaxDuration: &zero}} {
		r, e := RunCampaign(c, limits, func(uint64) (CampaignModel[int, uint64, int], error) {
			t.Error("preflight factory")
			return CampaignModel[int, uint64, int]{}, nil
		}, func(JobReport[uint64]) error { t.Error("preflight sink"); return nil })
		want := "deadline"
		if limits.Cancellation != nil {
			want = "cancelled"
		}
		if e != nil || r.Started != 0 || r.NotStarted != 12 || r.Termination.Kind != want || !r.AccountingComplete {
			t.Fatal(e, r)
		}
	}
	c.Jobs = 0
	_, e := RunGuidedCampaign(c, RunLimits{Cancellation: &cancel}, GuidanceConfig{}, func(uint64) (CampaignModel[int, uint64, int], error) { panic("factory") }, func(uint64, GenerateModel[int, uint64, int]) (CampaignFeedback[int, uint64, int, int], error) {
		panic("feedback")
	}, func(JobReport[uint64]) error { panic("sink") })
	if e == nil || e.Error() != "guidance limits must be positive" {
		t.Fatal(e)
	}
	c = campaignTestConfig(1)
	c.Jobs = 1
	cancel.Store(false)
	r, e := RunCampaign(c, RunLimits{Cancellation: &cancel}, func(id uint64) (CampaignModel[int, uint64, int], error) { return campaignTestModel(id, false), nil }, func(JobReport[uint64]) error { cancel.Store(true); return nil })
	if e != nil || r.Termination.Kind != "jobs_completed" {
		t.Fatal("late control changed outcome", e, r)
	}
	duration := time.Millisecond
	r, e = RunCampaign(c, RunLimits{MaxDuration: &duration}, func(id uint64) (CampaignModel[int, uint64, int], error) {
		time.Sleep(3 * time.Millisecond)
		return campaignTestModel(id, false), nil
	}, func(j JobReport[uint64]) error {
		if j.Metadata == nil || j.Outcome.Report.Cases != 0 {
			t.Errorf("%+v", j)
		}
		return nil
	})
	if e != nil || r.Termination.Kind != "deadline" {
		t.Fatal(e, r)
	}
}

func TestCampaignWorkerFailureAfterDrain(t *testing.T) {
	c := campaignTestConfig(2)
	c.FirstJob = 0
	c.Jobs = 5
	c.ResultBuffer = 0
	var sinks int
	r, e := runCampaignUsing(c, RunLimits{}, func(id uint64) (CampaignModel[int, uint64, int], error) { return campaignTestModel(id, false), nil }, func(JobReport[uint64]) error { sinks++; return nil }, campaignTestRunner, campaignRuntime{afterComplete: func(id uint64) {
		if id == 0 {
			panic("lost completed report")
		}
	}})
	if e != nil || r.Started != 5 || r.Finished != 4 || r.Delivered != 4 || sinks != 4 || r.Errors != 0 || r.AccountingComplete || r.Termination.Kind != "worker_error" {
		t.Fatal(e, r, sinks)
	}
	// Missing outcomes are never fabricated; sink failure remains authoritative.
	r, e = runCampaignUsing(c, RunLimits{}, func(id uint64) (CampaignModel[int, uint64, int], error) { return campaignTestModel(id, false), nil }, func(JobReport[uint64]) error { return errors.New("sink wins") }, campaignTestRunner, campaignRuntime{afterComplete: func(id uint64) {
		if id == 0 {
			panic("lost completed report")
		}
	}})
	if e != nil || r.Termination.Kind != "callback_error" || r.Termination.Error.Error() != "sink wins" || r.AccountingComplete {
		t.Fatal(e, r)
	}
	entered := make(chan struct{})
	release := make(chan struct{})
	r, e = runCampaignUsing(c, RunLimits{}, func(id uint64) (CampaignModel[int, uint64, int], error) {
		if id == 0 {
			close(entered)
			<-release
		}
		return campaignTestModel(id, false), nil
	}, func(JobReport[uint64]) error { return nil }, campaignTestRunner, campaignRuntime{start: func(index int, f func()) error {
		if index == 0 {
			go f()
			return nil
		}
		<-entered
		close(release)
		return errors.New("start rejected")
	}})
	if e != nil || r.Started < 1 || r.Started != r.Finished || r.Delivered != r.Finished || !r.AccountingComplete || r.Errors != 0 || r.Termination.Kind != "worker_error" {
		t.Fatal(e, r)
	}
}

func TestCampaignFailureStopsClaimsButNotClaimedJobs(t *testing.T) {
	c := campaignTestConfig(2)
	c.FirstJob, c.Jobs, c.StopOnFailure, c.ResultBuffer = 0, 10, true, 0
	both := make(chan struct{})
	release := make(chan struct{})
	var entered atomic.Int64
	var jobs []JobReport[uint64]
	r, e := RunCampaign(c, RunLimits{}, func(id uint64) (CampaignModel[int, uint64, int], error) {
		if entered.Add(1) == 2 {
			close(both)
		}
		<-both
		if id == 1 {
			<-release
		}
		return campaignTestModel(id, id == 0), nil
	}, func(j JobReport[uint64]) error {
		jobs = append(jobs, j)
		if j.JobID == 0 {
			close(release)
		}
		return nil
	})
	if e != nil || r.Started != 2 || r.Finished != 2 || r.Delivered != 2 || r.Failures != 1 || r.NotStarted != 8 || r.Termination.Kind != "failure_found" || jobs[1].Outcome.Report.Termination != "cases_completed" {
		t.Fatal(e, r, jobs)
	}
}

func TestCampaignReportInputTransferBeforeCleanup(t *testing.T) {
	c := campaignTestConfig(2)
	c.FirstJob, c.Jobs, c.ResultBuffer = 0, 2, 0
	c.Fuzz.Cases = 1
	var jobs []JobReport[[]uint64]
	r, e := RunCampaign(c, RunLimits{}, func(id uint64) (CampaignModel[int, []uint64, int], error) {
		storage := []uint64{id + 41}
		g := GenerateModel[int, []uint64, int]{Model: Model[int, []uint64, int]{
			InitialState: func() (int, error) { return 0, nil },
			Step: func(s int, i []uint64) (Transition[int, int], error) {
				i[0] += 1000
				return Accept[int, int](s + 1), nil
			},
			CheckState: func(s int) ([]Check, error) {
				if s != 0 {
					return []Check{Failed("input", "retain")}, nil
				}
				return nil, nil
			},
			CloneState: ValueCopy[int], CloneOutput: ValueCopy[int],
			CloneInput: func(i []uint64) []uint64 { return append([]uint64(nil), i...) },
		}, Generate: func(int, *Rng) ([]uint64, bool, error) { return storage, true, nil }}
		return CampaignModel[int, []uint64, int]{Model: g,
			Metadata: func() (Metadata, error) { return Identity("mutable", "fixture"), nil },
			Close:    func() error { storage[0] = 999; return nil },
		}, nil
	}, func(j JobReport[[]uint64]) error { jobs = append(jobs, j); return nil })
	if e != nil || r.Failures != 2 || !r.AccountingComplete {
		t.Fatal(e, r)
	}
	for _, j := range jobs {
		if !reflect.DeepEqual(j.Outcome.Report.Failure.Inputs, [][]uint64{{j.JobID + 41}}) {
			t.Fatal("report alias crossed worker cleanup", j)
		}
	}
}

func TestCampaignZeroBudgets(t *testing.T) {
	for _, tc := range []struct {
		cases, steps    int
		transitions     uint64
		wantCases       int
		wantTermination string
	}{
		{0, 2, 4, 0, "cases_completed"}, {2, 0, 0, 2, "cases_completed"}, {2, 2, 0, 1, "transition_limit"},
	} {
		c := campaignTestConfig(1)
		c.Jobs = 1
		c.Fuzz.Cases, c.Fuzz.MaxSteps, c.Fuzz.MaxTransitions = tc.cases, tc.steps, tc.transitions
		_, e := RunCampaign(c, RunLimits{}, func(id uint64) (CampaignModel[int, uint64, int], error) { return campaignTestModel(id, false), nil }, func(j JobReport[uint64]) error {
			f := j.Outcome.Report
			if f == nil || f.Termination != tc.wantTermination || f.Cases != tc.wantCases || f.Transitions != 0 {
				t.Errorf("%+v %+v", tc, j)
			}
			return nil
		})
		if e != nil {
			t.Fatal(e)
		}
	}
}

func TestCampaignControlsInsideChecksAndFeedbackErrors(t *testing.T) {
	for _, fail := range []bool{false, true} {
		c := campaignTestConfig(1)
		c.Jobs = 1
		var cancel atomic.Bool
		r, e := RunCampaign(c, RunLimits{Cancellation: &cancel}, func(id uint64) (CampaignModel[int, uint64, int], error) {
			m := campaignTestModel(id, false)
			m.Model.Step = func(state int, _ uint64) (Transition[int, int], error) {
				cancel.Store(true)
				return Accept[int, int](state + 1), nil
			}
			m.Model.CheckState = func(state int) ([]Check, error) {
				if state > 0 && fail {
					return []Check{Failed("precedence", "completed check wins")}, nil
				}
				return nil, nil
			}
			return m, nil
		}, func(j JobReport[uint64]) error {
			want := "cancelled"
			if fail {
				want = "failure_found"
			}
			if j.Outcome.Report == nil || j.Outcome.Report.Termination != want || j.Outcome.Report.Transitions != 1 {
				t.Errorf("%+v", j)
			}
			return nil
		})
		want := "cancelled"
		if fail {
			want = "jobs_completed"
		}
		if e != nil || r.Termination.Kind != want || r.ReportedTransitions != 1 || !r.AccountingComplete {
			t.Fatal(e, r)
		}
	}
	for _, panics := range []bool{false, true} {
		c := campaignTestConfig(1)
		c.Jobs = 1
		r, e := RunGuidedCampaign(c, RunLimits{}, DefaultGuidanceConfig(), func(id uint64) (CampaignModel[int, uint64, int], error) { return campaignTestModel(id, false), nil }, func(_ uint64, g GenerateModel[int, uint64, int]) (CampaignFeedback[int, uint64, int, int], error) {
			f := ExactStateFeedback(g.Model)
			f.TransitionFeatures = func(int, uint64, Transition[int, int], *FeatureBuffer[int]) error {
				if panics {
					panic("feedback panic")
				}
				return errors.New("feedback error")
			}
			return CampaignFeedback[int, uint64, int, int]{Feedback: f}, nil
		}, func(j JobReport[uint64]) error {
			want := "model_error"
			if panics {
				want = "panicked"
			}
			if j.Metadata == nil || j.Outcome.Kind != want || j.Outcome.Report != nil {
				t.Errorf("%+v", j)
			}
			return nil
		})
		if e != nil || r.AccountingComplete || r.Errors != 1 || r.ReportedTransitions != 0 {
			t.Fatal(e, r)
		}
	}
}
