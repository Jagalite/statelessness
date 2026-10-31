package statelessness

import (
	"math"
	"reflect"
	"sync/atomic"
	"testing"
)

func wideModel() GenerateModel[int, int, int] {
	return GenerateModel[int, int, int]{Model: Model[int, int, int]{
		InitialState: func() (int, error) { return 0, nil }, Step: func(s, i int) (Transition[int, int], error) { return Accept[int, int](s + i), nil },
		CloneState: ValueCopy[int], CloneInput: ValueCopy[int], CloneOutput: ValueCopy[int], CheckState: func(int) ([]Check, error) { return nil, nil },
	}, Generate: func(int, *Rng) (int, bool, error) { return 1, true, nil }, InputEqual: func(a, b int) (bool, error) { return a == b, nil }}
}
func TestRust64FuzzBoundedWork(t *testing.T) {
	g := wideModel()
	var cancel atomic.Bool
	cancel.Store(true)
	calls := 0
	g.InitialState = func() (int, error) { calls++; return 0, nil }
	c := FuzzConfig64{Cases: math.MaxUint64, MaxSteps: math.MaxUint64, MaxTransitions: math.MaxUint64}
	r, e := FuzzWithLimits64(g, c, RunLimits{Cancellation: &cancel})
	if e != nil || r.Termination != "cancelled" || r.Cases != 0 || calls != 0 {
		t.Fatal(r, e, calls)
	}
	c.Cases = 0
	r, e = Fuzz64(g, c)
	if e != nil || r.Termination != "cases_completed" || calls != 0 {
		t.Fatal(r, e, calls)
	}
	r, e = FuzzWithLimits64(g, c, RunLimits{Cancellation: &cancel})
	if e != nil || r.Termination != "cancelled" {
		t.Fatal(r, e)
	}
	c.Cases = 1
	g.Generate = func(int, *Rng) (int, bool, error) { return 0, false, nil }
	r, e = Fuzz64(g, c)
	if e != nil || r.Cases != 1 || r.Transitions != 0 || r.Termination != "cases_completed" {
		t.Fatal(r, e)
	}
	c.Cases = math.MaxUint64
	g.CheckState = func(int) ([]Check, error) { return []Check{Failed("bound", "initial")}, nil }
	r, e = Fuzz64(g, c)
	if e != nil || r.Cases != 1 || r.Transitions != 0 || r.Termination != "failure_found" || len(r.Failure.Inputs) != 0 {
		t.Fatal(r, e)
	}
	g = wideModel()
	g.CheckState = func(s int) ([]Check, error) {
		if s > 0 {
			return []Check{Failed("bound", "step")}, nil
		}
		return nil, nil
	}
	r, e = Fuzz64(g, c)
	if e != nil || r.Cases != 1 || r.Transitions != 1 || !reflect.DeepEqual(r.Failure.Inputs, []int{1}) {
		t.Fatal(r, e)
	}
	c.MutationPercent = 101
	_, e = FuzzWithLimits64(g, c, RunLimits{Cancellation: &cancel})
	if e == nil {
		t.Fatal("cancellation bypassed invalid mutation")
	}
}
func TestRust64GuidedMetadataAndRetention(t *testing.T) {
	g := wideModel()
	var cancel atomic.Bool
	cancel.Store(true)
	metadata, observations := 0, 0
	f := StateFeedback[int, int, int](FeedbackMetadata{"wide", 1, "test"}, FeatureKey[int]{Clone: ValueCopy[int], Equal: func(a, b int) bool { return a == b }}, func(s int) (int, error) { observations++; return s, nil })
	f.Metadata = func() (FeedbackMetadata, error) { metadata++; return FeedbackMetadata{"wide", 1, "test"}, nil }
	c := FuzzConfig64{Cases: math.MaxUint64, MaxSteps: math.MaxUint64, MaxTransitions: math.MaxUint64}
	h := GuidanceConfig64{math.MaxUint64, math.MaxUint64, math.MaxUint64, math.MaxUint64}
	r, e := GuidedFuzzWithLimits64(g, c, h, f, RunLimits{Cancellation: &cancel})
	if e != nil || r.Termination != "cancelled" || metadata != 1 || observations != 0 {
		t.Fatal(r, e, metadata, observations)
	}
	c.MutationPercent = 101
	bad := h
	bad.MaxFeatures = 0
	_, e = GuidedFuzz64(g, c, bad, f)
	if e == nil || e.Error() != "guidance limits must be positive" || metadata != 1 {
		t.Fatal(e, metadata)
	}
	_, e = GuidedFuzz64(g, c, h, f)
	if e == nil || metadata != 1 {
		t.Fatal(e, metadata)
	}
	c.MutationPercent = 0
	c.Cases = 1
	c.MaxSteps = 2
	c.MaxTransitions = 2
	r, e = GuidedFuzz64(g, c, h, f)
	if e != nil || r.Cases != 1 || r.Transitions != 2 || r.Guidance.UniqueFeatures != 3 || r.Guidance.CorpusInputs != 3 || len(r.Guidance.Corpus) != 2 {
		t.Fatal(r, e)
	}
	if _, e = r.Legacy(); e == nil {
		t.Fatal("wide guidance silently narrowed")
	}
	h.MaxCorpusEntries = 1
	h.MaxCorpusInputs = 1
	r, e = GuidedFuzz64(g, c, h, f)
	if e != nil || len(r.Guidance.Corpus) != 1 || r.Guidance.CorpusInputs != 1 || r.Guidance.DroppedPrefixes != 1 {
		t.Fatal(r, e)
	}
}
func TestRust64CampaignAdmissionAndSingleFailure(t *testing.T) {
	c := DefaultCampaignConfig64()
	c.Jobs = math.MaxUint64
	c.FirstJob = 1
	c.Fuzz = FuzzConfig64{Cases: 1, MaxSteps: math.MaxUint64, MaxTransitions: 1}
	if e := c.Validate(); e != nil {
		t.Fatal(e)
	}
	var cancel atomic.Bool
	cancel.Store(true)
	calls, sinks := 0, 0
	factory := func(uint64) (CampaignModel[int, int, int], error) {
		calls++
		g := wideModel()
		g.CheckState = func(int) ([]Check, error) { return []Check{Failed("first", "stop")}, nil }
		return CampaignModel[int, int, int]{Model: g, Metadata: func() (Metadata, error) { return Metadata{}, nil }}, nil
	}
	sink := func(JobReport64[int]) error { sinks++; return nil }
	c.Workers = 256
	c.ResultBuffer = 1024
	r, e := RunCampaign64(c, RunLimits{Cancellation: &cancel}, factory, sink)
	if e != nil || r.Requested != math.MaxUint64 || r.NotStarted != math.MaxUint64 || r.Started != 0 || calls != 0 || sinks != 0 || r.Termination.Kind != "cancelled" {
		t.Fatal(r, e, calls, sinks)
	}
	c.Workers = 1
	c.StopOnFailure = true
	r, e = RunCampaign64(c, RunLimits{}, factory, sink)
	if e != nil || r.NotStarted != math.MaxUint64-1 || r.Started != 1 || r.Finished != 1 || r.Delivered != 1 || r.Failures != 1 || calls != 1 || sinks != 1 || r.Termination.Kind != "failure_found" {
		t.Fatal(r, e, calls, sinks)
	}
	if _, e = r.Legacy(); e == nil {
		t.Fatal("wide campaign report narrowed")
	}
	tests := []struct {
		edit func(*CampaignConfig64)
		want string
	}{
		{func(c *CampaignConfig64) { c.Jobs = 0; c.Workers = 0 }, "campaign jobs must be positive"},
		{func(c *CampaignConfig64) { c.Workers = math.MaxUint64; c.ResultBuffer = math.MaxUint64 }, "campaign workers must be in 1..=256"},
		{func(c *CampaignConfig64) { c.ResultBuffer = math.MaxUint64; c.Fuzz.MutationPercent = 101 }, "campaign result_buffer must be in 0..=1024"},
		{func(c *CampaignConfig64) { c.Fuzz.MutationPercent = 101; c.FirstJob = 2 }, "mutation_percent must be in 0..=100"},
		{func(c *CampaignConfig64) { c.FirstJob = 2; c.Fuzz.Cases = math.MaxUint64 }, "campaign job ID range overflows u64"},
		{func(c *CampaignConfig64) { c.FirstJob = 0; c.Jobs = 2; c.Fuzz.Cases = math.MaxUint64 }, "campaign aggregate cases overflow u64"},
		{func(c *CampaignConfig64) {
			c.FirstJob = 0
			c.Jobs = 2
			c.Fuzz.Cases = 1
			c.Fuzz.MaxSteps = math.MaxUint64
			c.Fuzz.MaxTransitions = math.MaxUint64
		}, "campaign aggregate transitions overflow u64"},
	}
	for _, tc := range tests {
		bad := c
		tc.edit(&bad)
		_, e := RunCampaign64(bad, RunLimits{Cancellation: &cancel}, factory, sink)
		if e == nil || e.Error() != tc.want {
			t.Fatal(tc.want, e)
		}
	}
	c.Fuzz.Cases = 0
	c.Fuzz.MaxSteps = math.MaxUint64
	c.Fuzz.MaxTransitions = math.MaxUint64
	if e := c.Validate(); e != nil {
		t.Fatal(e)
	}
	c.Jobs = 1
	c.FirstJob = math.MaxUint64
	c.StopOnFailure = false
	r, e = RunCampaign64(c, RunLimits{}, factory, sink)
	if e != nil || r.ReportedCases != 0 || r.Delivered != 1 || r.Termination.Kind != "jobs_completed" {
		t.Fatal(r, e)
	}
}
func TestRust64AutoShrinkAndConvenience(t *testing.T) {
	g := wideModel()
	g.Inputs = func(int) (Iterator[int], error) { return Values([]int{}), nil }
	auto, e := Auto64(g.Model, g.InputEqual, AutoOptions64{math.MaxUint64})
	if e != nil {
		t.Fatal(e)
	}
	r, e := Fuzz64(auto, FuzzConfig64{Cases: 1, MaxSteps: math.MaxUint64, MaxTransitions: math.MaxUint64})
	if e != nil || r.Cases != 1 || r.Transitions != 0 {
		t.Fatal(r, e)
	}
	var cancel atomic.Bool
	cancel.Store(true)
	failure := Failure[int]{Inputs: []int{1}, Violations: []Violation{{Phase: "state", Check: Failed("target", "stop")}}}
	shr, e := ShrinkWithLimits64(g, failure, ShrinkConfig64{math.MaxUint64}, ShrinkLimits{Control: RunLimits{Cancellation: &cancel}})
	if e != nil || shr.Termination != "cancelled" || shr.Attempts != 1 || shr.ReplayedTransitions != 0 {
		t.Fatal(shr, e)
	}
	c := FuzzConfig{Seed: 42, Cases: 3, MaxSteps: 4, MaxTransitions: 12, MutationPercent: 100}
	wide, _ := c.Exact()
	a, e := Fuzz(g, c)
	if e != nil {
		t.Fatal(e)
	}
	b, e := Fuzz64(g, wide)
	if e != nil {
		t.Fatal(e)
	}
	converted, e := b.Legacy()
	if e != nil || !reflect.DeepEqual(a, converted) {
		t.Fatal(a, b, e)
	}
	if _, e = (FuzzConfig64{Cases: math.MaxUint64}).Legacy(); e == nil {
		t.Fatal("wide config narrowed")
	}
	if e := countCanAppend[uint64](int(^uint(0)>>1) / 8); e == nil {
		t.Fatal("byte count overflow accepted")
	}
}

func TestRust64SimplificationIndexChecked(t *testing.T) {
	next, e := nextSimplificationIndex(math.MaxUint64 - 1)
	if e != nil || next != math.MaxUint64 {
		t.Fatal(next, e)
	}
	if _, e = nextSimplificationIndex(math.MaxUint64); e == nil || e.Error() != "simplification index overflow" {
		t.Fatal(e)
	}
}

func TestRust64ShrinkReplayOverflow(t *testing.T) {
	g := wideModel()
	checks, steps := 0, 0
	g.Step = func(s, i int) (Transition[int, int], error) { steps++; return Accept[int, int](s + i), nil }
	g.CheckState = func(int) ([]Check, error) { checks++; return nil, nil }
	w := shrinkWork[int, int, int]{g: g, transitions: math.MaxUint64}
	_, err := w.replay([]int{1})
	if err == nil || err.Error() != "replayed transition count overflow" || w.transitions != math.MaxUint64 || steps != 1 || checks != 1 {
		t.Fatal(err, w.transitions, steps, checks)
	}
}
