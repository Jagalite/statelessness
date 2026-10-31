package statelessness

import (
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

func TestAutoCampaignCancellationInsideDomainAndCleanup(t *testing.T) {
	for _, guided := range []bool{false, true} {
		t.Run(map[bool]string{false: "ordinary", true: "guided"}[guided], func(t *testing.T) {
			var stop atomic.Bool
			calls, modelClosed, feedbackClosed := 0, 0, 0
			factory := func(uint64) (CampaignModel[int, int, int], error) {
				m := autoFuzzTestModel(func(int) (Iterator[int], error) {
					return func() (int, bool, error) {
						calls++
						if calls == 2 {
							stop.Store(true)
						}
						return calls, true, nil
					}, nil
				})
				return CampaignModel[int, int, int]{Model: GenerateModel[int, int, int]{Model: m}, Metadata: func() (Metadata, error) { return Identity("auto-campaign", "test"), nil }, Close: func() error { modelClosed++; return nil }}, nil
			}
			cfg := DefaultCampaignConfig64()
			cfg.Jobs = 1
			cfg.Workers = 1
			cfg.Fuzz = autoFuzzTestConfig()
			var jobs []JobReport64[int]
			sink := func(j JobReport64[int]) error { jobs = append(jobs, j); return nil }
			equal := func(a, b int) (bool, error) { return a == b, nil }
			var r CampaignReport64
			var err error
			if guided {
				feedback := func(_ uint64, _ GenerateModel[int, int, int]) (CampaignFeedback[int, int, int, int], error) {
					return CampaignFeedback[int, int, int, int]{Feedback: autoGuidedFeedback(), Close: func() error { feedbackClosed++; return nil }}, nil
				}
				r, err = RunGuidedAutoCampaign64(cfg, RunLimits{Cancellation: &stop}, DefaultGuidanceConfig64(), factory, equal, AutoOptions64{10}, feedback, sink)
			} else {
				r, err = RunAutoCampaign64(cfg, RunLimits{Cancellation: &stop}, factory, equal, AutoOptions64{10}, sink)
			}
			if err != nil || calls != 2 || modelClosed != 1 || len(jobs) != 1 || jobs[0].Outcome.Kind != "completed" || jobs[0].Outcome.Report.Termination != "cancelled" || jobs[0].Outcome.Report.Transitions != 0 {
				t.Fatalf("report=%+v jobs=%+v err=%v calls=%d closed=%d", r, jobs, err, calls, modelClosed)
			}
			if guided && feedbackClosed != 1 {
				t.Fatalf("feedback closed=%d", feedbackClosed)
			}
		})
	}
}

func TestAutoCampaignCallbackErrorAndPanicProvenance(t *testing.T) {
	for _, panics := range []bool{false, true} {
		cfg := DefaultCampaignConfig64()
		cfg.Jobs = 1
		cfg.Fuzz = autoFuzzTestConfig()
		factory := func(uint64) (CampaignModel[int, int, int], error) {
			m := autoFuzzTestModel(func(int) (Iterator[int], error) { return Values([]int{1}), nil })
			return CampaignModel[int, int, int]{Model: GenerateModel[int, int, int]{Model: m}, Metadata: func() (Metadata, error) { return Identity("a", "b"), nil }}, nil
		}
		equal := func(int, int) (bool, error) {
			if panics {
				panic("equal panic")
			}
			return false, errors.New("equal failed")
		}
		var got JobReport64[int]
		_, err := RunAutoCampaign64(cfg, RunLimits{}, factory, equal, AutoOptions64{10}, func(j JobReport64[int]) error { got = j; return nil })
		want := "model_error"
		if panics {
			want = "panicked"
		}
		if err != nil || got.Outcome.Kind != want {
			t.Fatalf("panic=%v job=%+v err=%v", panics, got, err)
		}
	}
}

func TestAutoCampaignConcurrentWorkersCancelAndClose(t *testing.T) {
	var stop atomic.Bool
	var closed atomic.Int32
	entered := make(chan struct{}, 2)
	release := make(chan struct{})
	factory := func(uint64) (CampaignModel[int, int, int], error) {
		m := autoFuzzTestModel(func(int) (Iterator[int], error) {
			return func() (int, bool, error) { entered <- struct{}{}; <-release; return 1, true, nil }, nil
		})
		return CampaignModel[int, int, int]{Model: GenerateModel[int, int, int]{Model: m}, Metadata: func() (Metadata, error) { return Identity("multi", "test"), nil }, Close: func() error { closed.Add(1); return nil }}, nil
	}
	cfg := DefaultCampaignConfig64()
	cfg.Jobs = 2
	cfg.Workers = 2
	cfg.ResultBuffer = 2
	cfg.Fuzz = autoFuzzTestConfig()
	type outcome struct {
		report CampaignReport64
		err    error
	}
	result := make(chan outcome, 1)
	go func() {
		r, e := RunAutoCampaign64(cfg, RunLimits{Cancellation: &stop}, factory, func(a, b int) (bool, error) { return a == b, nil }, AutoOptions64{10}, func(JobReport64[int]) error { return nil })
		result <- outcome{r, e}
	}()
	for i := 0; i < 2; i++ {
		select {
		case <-entered:
		case <-time.After(5 * time.Second):
			close(release)
			t.Fatal("workers did not enter domain scan")
		}
	}
	stop.Store(true)
	close(release)
	got := <-result
	if got.err != nil || got.report.Started != 2 || got.report.Finished != 2 || !got.report.AccountingComplete || closed.Load() != 2 {
		t.Fatalf("outcome=%+v closed=%d", got, closed.Load())
	}
}

func TestGuidedAutoCampaignFeedbackErrorStillClosesModel(t *testing.T) {
	cfg := DefaultCampaignConfig64()
	cfg.Jobs = 1
	cfg.Fuzz = autoFuzzTestConfig()
	closed := 0
	factory := func(uint64) (CampaignModel[int, int, int], error) {
		m := autoFuzzTestModel(func(int) (Iterator[int], error) { return Values([]int{1}), nil })
		return CampaignModel[int, int, int]{Model: GenerateModel[int, int, int]{Model: m}, Metadata: func() (Metadata, error) { return Identity("a", "b"), nil }, Close: func() error { closed++; return nil }}, nil
	}
	feedback := func(uint64, GenerateModel[int, int, int]) (CampaignFeedback[int, int, int, int], error) {
		return CampaignFeedback[int, int, int, int]{}, errors.New("feedback setup")
	}
	var got JobReport64[int]
	_, err := RunGuidedAutoCampaign64(cfg, RunLimits{}, DefaultGuidanceConfig64(), factory, func(a, b int) (bool, error) { return a == b, nil }, AutoOptions64{10}, feedback, func(j JobReport64[int]) error { got = j; return nil })
	if err != nil || got.Outcome.Kind != "model_error" || closed != 1 {
		t.Fatalf("job=%+v err=%v closed=%d", got, err, closed)
	}
}
