package statelessness

import (
	"math"
	"math/bits"
	"sync"
	"time"
)

// CampaignConfig bounds independent jobs and queued reports. Zero ResultBuffer
// is a rendezvous channel. Limits count objects, not bytes or process memory.
type CampaignConfig struct {
	MasterSeed, FirstJob        uint64
	Jobs, Workers, ResultBuffer int
	Fuzz                        FuzzConfig
	StopOnFailure               bool
}

func DefaultCampaignConfig() CampaignConfig {
	return CampaignConfig{Jobs: 16, Workers: 1, ResultBuffer: 1, Fuzz: DefaultFuzzConfig()}
}

// Validate runs before factories, goroutines, metadata and sink callbacks.
func (c CampaignConfig) Validate() error {
	bad := func(s string) error { return &ConfigError{s} }
	if c.Jobs <= 0 {
		return bad("campaign jobs must be positive")
	}
	if c.Workers < 1 || c.Workers > 256 {
		return bad("campaign workers must be in 1..=256")
	}
	if c.ResultBuffer < 0 || c.ResultBuffer > 1024 {
		return bad("campaign result_buffer must be in 0..=1024")
	}
	if c.Fuzz.MutationPercent < 0 || c.Fuzz.MutationPercent > 100 {
		return bad("mutation_percent must be in 0..=100")
	}
	jobs := uint64(c.Jobs)
	if jobs-1 > math.MaxUint64-c.FirstJob {
		return bad("campaign job ID range overflows u64")
	}
	if c.Fuzz.Cases < 0 {
		return bad("campaign per-job cases must be nonnegative")
	}
	if c.Fuzz.MaxSteps < 0 {
		return bad("campaign per-job steps must be nonnegative")
	}
	cases, steps := uint64(c.Fuzz.Cases), uint64(c.Fuzz.MaxSteps)
	if hi, _ := bits.Mul64(jobs, cases); hi != 0 {
		return bad("campaign aggregate cases overflow u64")
	}
	hi, transitions := bits.Mul64(cases, steps)
	if hi != 0 || transitions > c.Fuzz.MaxTransitions {
		transitions = c.Fuzz.MaxTransitions
	}
	if hi, _ := bits.Mul64(jobs, transitions); hi != 0 {
		return bad("campaign aggregate transitions overflow u64")
	}
	return nil
}

// JobSeed uses wrapping addition followed by one SplitMix64 output.
func JobSeed(masterSeed, jobID uint64) uint64 { return NewRng(masterSeed + jobID).NextU64() }

// CampaignModel owns one job's callbacks and resources. Metadata is independent
// of Model.Codec. Close, when present, executes exactly once on the job worker
// after search and before Complete/send, including metadata/search failure.
// CloneInput must detach all mutable report input storage. Factories must return
// fresh models; Go cannot prevent aliased closure captures or data races.
type CampaignModel[S, I, O any] struct {
	Model    GenerateModel[S, I, O]
	Metadata func() (Metadata, error)
	Close    func() error
	panics   *campaignPanic
}

// CampaignAuto installs campaign panic tracking before Auto captures model and
// iterator callbacks. Use it inside the model factory instead of constructing
// Auto beforehand: errors already caught inside opaque closures cannot later be
// distinguished from intentionally returned ModelErrors. N05's Auto is unchanged.
func CampaignAuto[S, I, O any](model CampaignModel[S, I, O], equal func(I, I) (bool, error), options AutoOptions) (CampaignModel[S, I, O], error) {
	p := model.panics
	if p == nil {
		p = &campaignPanic{}
	}
	guarded := campaignGuardModel(model.Model, p)
	var equality func(I, I) (bool, error)
	if equal != nil {
		equality = func(a, b I) (bool, error) { defer p.guard(); v, e := equal(a, b); return v, p.ownedError(e) }
	}
	g, err := Auto(guarded.Model, equality, options)
	if p.seen {
		panic(p.message)
	}
	if err != nil {
		return model, err
	}
	model.Model, model.panics = g, p
	return model, nil
}

// CampaignFeedback gives guided jobs explicit deterministic cleanup. Close runs
// before model Close. A factory returning an error retains its own cleanup duty.
type CampaignFeedback[S, I, O, K any] struct {
	Feedback Feedback[S, I, O, K]
	Close    func() error
}

type JobOutcome[I any] struct {
	Kind   string // completed, factory_error, model_error, panicked
	Report *FuzzReport[I]
	Error  error
	Panic  string
}
type JobReport[I any] struct {
	JobID, Seed uint64
	Config      FuzzConfig
	Metadata    *Metadata
	Outcome     JobOutcome[I]
}
type CampaignTermination struct {
	Kind  string // jobs_completed, failure_found, cancelled, deadline, callback_error, worker_error
	Error error
}
type CampaignReport struct {
	Requested, Started, Finished, NotStarted, Delivered, Failures, Errors int
	ReportedTransitions, ReportedCases, ReportedSkippedChecks             uint64
	AccountingComplete                                                    bool
	Termination                                                           CampaignTermination
}

// RunCampaign executes workers=1 on its calling goroutine. Configured workers>1
// starts min(workers,jobs) goroutines, including workers>1/jobs=1. The sink always
// runs serially on the caller in channel receipt order. Factory captures must be
// safe for concurrent calls; each returned model and its state stay worker-local.
// Cancellation/deadlines are cooperative. A failing sink is never retried: active
// reports are drained, workers joined, and its first error retained.
func RunCampaign[S, I, O any](config CampaignConfig, limits RunLimits,
	factory func(uint64) (CampaignModel[S, I, O], error), sink func(JobReport[I]) error,
) (CampaignReport, error) {
	return runCampaignUsing(config, limits, factory, sink,
		func(_ uint64, model GenerateModel[S, I, O], config FuzzConfig, limits RunLimits, _ *campaignPanic) (*FuzzReport[I], error) {
			return FuzzWithLimits(model, config, limits)
		}, campaignRuntime{})
}

// RunGuidedCampaign invokes the public guided engine with fresh feedback and
// corpus for every job. Guidance validation precedes campaign validation.
func RunGuidedCampaign[S, I, O, K any](config CampaignConfig, limits RunLimits, guidance GuidanceConfig,
	factory func(uint64) (CampaignModel[S, I, O], error),
	feedbackFactory func(uint64, GenerateModel[S, I, O]) (CampaignFeedback[S, I, O, K], error),
	sink func(JobReport[I]) error,
) (CampaignReport, error) {
	if err := guidance.Validate(); err != nil {
		return CampaignReport{}, err
	}
	return runCampaignUsing(config, limits, factory, sink,
		func(id uint64, model GenerateModel[S, I, O], config FuzzConfig, limits RunLimits, panics *campaignPanic) (report *FuzzReport[I], err error) {
			control, err := newRunControl(limits)
			if err != nil {
				return nil, err
			}
			if feedbackFactory == nil {
				return nil, &ModelError{"nil campaign feedback factory"}
			}
			feedback, err := feedbackFactory(id, model)
			if err != nil {
				return nil, panics.ownedError(err)
			}
			if feedback.Close != nil {
				defer func() {
					if e := feedback.Close(); e != nil {
						report, err = nil, panics.ownedError(e)
					}
				}()
			}
			return GuidedFuzzWithLimits(model, config, guidance, campaignGuardFeedback(feedback.Feedback, panics), campaignRemaining(control))
		}, campaignRuntime{})
}

func campaignRemaining(c runControl) RunLimits {
	limits := c.limits
	if limits.MaxDuration != nil {
		duration := *limits.MaxDuration - time.Since(c.started)
		if duration < 0 {
			duration = 0
		}
		limits.MaxDuration = &duration
	}
	return limits
}

type campaignCoordinator struct {
	mu    sync.Mutex
	model campaignLifecycle
	state campaignState
}

func (c *campaignCoordinator) applyLocked(event campaignEvent) []campaignEffect {
	t := c.model.stepOwned(c.state, event)
	if t.Disposition.Kind != "accepted" && event.kind != campaignClaim {
		panic("invalid campaign lifecycle event: " + t.Disposition.Reason)
	}
	c.state = t.State
	return t.Outputs
}
func (c *campaignCoordinator) apply(event campaignEvent) []campaignEffect {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.applyLocked(event)
}
func (c *campaignCoordinator) claim(control runControl) (uint64, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	effects := c.applyLocked(campaignEvent{kind: campaignClaim, stop: campaignStop(control.stop())})
	if len(effects) == 0 {
		return 0, false
	}
	if len(effects) != 1 || effects[0].kind != campaignRunJob {
		panic("invalid claim effect")
	}
	return effects[0].jobID, true
}

// Private dependency seam for start/join failure and scheduling tests. No global
// hooks or public arbitrary worker executor are installed by production calls.
type campaignRuntime struct {
	start         func(int, func()) error
	afterComplete func(uint64)
	sendBlocked   func(uint64)
	afterSend     func(uint64)
}

type campaignRunner[S, I, O any] func(uint64, GenerateModel[S, I, O], FuzzConfig, RunLimits, *campaignPanic) (*FuzzReport[I], error)

func runCampaignUsing[S, I, O any](config CampaignConfig, limits RunLimits,
	factory func(uint64) (CampaignModel[S, I, O], error), sink func(JobReport[I]) error,
	runner campaignRunner[S, I, O], runtime campaignRuntime,
) (CampaignReport, error) {
	if err := config.Validate(); err != nil {
		return CampaignReport{}, err
	}
	control, err := newRunControl(limits)
	if err != nil {
		return CampaignReport{}, err
	}
	model := newCampaignLifecycle(config)
	state, err := model.initial()
	if err != nil {
		return CampaignReport{}, err
	}
	c := &campaignCoordinator{model: model, state: state}
	if stop := control.stop(); stop != "" {
		c.apply(campaignEvent{kind: campaignClaim, stop: campaignStop(stop)})
	} else if config.Workers == 1 {
		for {
			id, ok := c.claim(control)
			if !ok {
				break
			}
			job := campaignExecute(config, control, factory, id, runner)
			c.apply(campaignEvent{kind: campaignComplete, jobID: id, summary: campaignSummaryFromJob(job)})
			if !campaignDeliverJob(job, c, sink) {
				break
			}
		}
	} else {
		results := make(chan JobReport[I], config.ResultBuffer)
		var workers sync.WaitGroup
		joins := []chan error{}
		start := runtime.start
		if start == nil {
			start = func(_ int, f func()) error { go f(); return nil }
		}
		for worker := 0; worker < min(config.Workers, config.Jobs); worker++ {
			joined := make(chan error, 1)
			workers.Add(1)
			err := start(worker, func() {
				returned := false
				defer func() {
					var err error
					if p := recover(); p != nil {
						err = &ModelError{"campaign worker panicked: " + campaignPanicMessage(p)}
					} else if !returned {
						err = &ModelError{"campaign worker exited without returning"}
					}
					joined <- err
					workers.Done()
				}()
				for {
					id, ok := c.claim(control)
					if !ok {
						break
					}
					job := campaignExecute(config, control, factory, id, runner)
					c.apply(campaignEvent{kind: campaignComplete, jobID: id, summary: campaignSummaryFromJob(job)})
					if runtime.afterComplete != nil {
						runtime.afterComplete(id)
					}
					select {
					case results <- job:
					default:
						// This native admission attempt proves that the actual
						// channel cannot accept now. The private observation is
						// outside the coordinator lock, before the blocking send.
						// With a held sink, no receive can free channel capacity.
						if runtime.sendBlocked != nil {
							runtime.sendBlocked(id)
						}
						results <- job
					}
					if runtime.afterSend != nil {
						runtime.afterSend(id)
					}
				}
				returned = true
			})
			if err != nil {
				workers.Done()
				c.apply(campaignEvent{kind: campaignWorkerFailed, err: &ModelError{"unable to start campaign worker: " + campaignOwnedError(err).Error()}})
				break
			}
			joins = append(joins, joined)
		}
		go func() { workers.Wait(); close(results) }()
		for job := range results {
			campaignDeliverJob(job, c, sink)
		}
		// Observe join failures after channel drain, in launch order.
		for _, joined := range joins {
			if err := <-joined; err != nil {
				c.apply(campaignEvent{kind: campaignWorkerFailed, err: err, lostWork: true})
			}
		}
	}
	c.apply(campaignEvent{kind: campaignFinish})
	return c.state.report, nil
}

func campaignExecute[S, I, O any](config CampaignConfig, control runControl,
	factory func(uint64) (CampaignModel[S, I, O], error), id uint64, runner campaignRunner[S, I, O],
) (job JobReport[I]) {
	job.JobID, job.Seed = id, JobSeed(config.MasterSeed, id)
	job.Config = config.Fuzz
	job.Config.Seed = job.Seed
	panics := &campaignPanic{}
	defer func() {
		if p := recover(); p != nil {
			panics.set(p)
		}
		if panics.seen {
			job.Outcome = JobOutcome[I]{Kind: "panicked", Panic: panics.message}
		}
	}()
	if factory == nil {
		job.Outcome = JobOutcome[I]{Kind: "factory_error", Error: &ModelError{"nil campaign model factory"}}
		return
	}
	model, err := factory(id)
	if err != nil {
		job.Outcome = JobOutcome[I]{Kind: "factory_error", Error: panics.ownedError(err)}
		return
	}
	if model.panics != nil {
		panics = model.panics
	}
	if model.Close != nil {
		defer func() {
			if err := model.Close(); err != nil {
				job.Outcome = JobOutcome[I]{Kind: "model_error", Error: panics.ownedError(err)}
			}
		}()
	}
	if model.Metadata == nil {
		job.Outcome = JobOutcome[I]{Kind: "model_error", Error: &ModelError{"campaign model requires metadata callback"}}
		return
	}
	metadata, err := model.Metadata()
	if err != nil {
		job.Outcome = JobOutcome[I]{Kind: "model_error", Error: panics.ownedError(err)}
		return
	}
	job.Metadata = &metadata
	g := campaignGuardModel(model.Model, panics)
	report, err := runner(id, g, job.Config, campaignRemaining(control), panics)
	if err != nil {
		job.Outcome = JobOutcome[I]{Kind: "model_error", Error: panics.ownedError(err)}
		return
	}
	// Inputs, unlike state/output/feature keys, leave the worker. Explicit native
	// clone hooks define application transfer and do not require a canonical codec.
	if report.Failure != nil {
		report.Failure.Inputs, err = cloneInputs(g, report.Failure.Inputs)
		if err != nil {
			job.Outcome = JobOutcome[I]{Kind: "model_error", Error: panics.ownedError(err)}
			return
		}
	}
	if report.Guidance != nil {
		for i := range report.Guidance.Corpus {
			report.Guidance.Corpus[i].Inputs, err = cloneInputs(g, report.Guidance.Corpus[i].Inputs)
			if err != nil {
				job.Outcome = JobOutcome[I]{Kind: "model_error", Error: panics.ownedError(err)}
				return
			}
		}
	}
	job.Outcome = JobOutcome[I]{Kind: "completed", Report: report}
	return
}

func campaignDeliverJob[I any](job JobReport[I], c *campaignCoordinator, sink func(JobReport[I]) error) bool {
	effects := c.apply(campaignEvent{kind: campaignReceive, jobID: job.JobID})
	if len(effects) != 1 || effects[0].jobID != job.JobID {
		panic("invalid campaign receipt effect")
	}
	if effects[0].kind == campaignDiscard {
		return false
	}
	if effects[0].kind != campaignDeliver {
		panic("invalid campaign delivery effect")
	}
	err := func() (err error) {
		defer func() {
			if p := recover(); p != nil {
				err = &ModelError{"campaign callback panicked: " + campaignPanicMessage(p)}
			}
		}()
		if sink == nil {
			return &ModelError{"nil campaign sink"}
		}
		if err := sink(job); err != nil {
			return campaignOwnedError(err)
		}
		return nil
	}()
	c.apply(campaignEvent{kind: campaignSinkReturned, jobID: job.JobID, err: err})
	return err == nil
}

// Never invoke arbitrary String/Error methods while formatting panic payloads.
func campaignPanicMessage(p any) string {
	if s, ok := p.(string); ok {
		return s
	}
	return "non-string panic payload"
}

// Returned errors keep their classification even when application diagnostics
// panic. A copied ModelError is safe without calling any application method;
// arbitrary errors get one guarded Error call and an owned fallback message.
func campaignOwnedError(err error) error {
	if err == nil {
		return nil
	}
	if e, ok := err.(*ModelError); ok && e != nil {
		return &ModelError{e.Message}
	}
	message := "error diagnostic unavailable"
	func() {
		defer func() { _ = recover() }()
		message = err.Error()
	}()
	return &ModelError{message}
}

type campaignPanic struct {
	seen    bool
	message string
}

// Diagnostic formatting is application code and can reenter guarded callbacks.
// Its panics must neither create job panic provenance nor overwrite a genuine
// earlier panic. The tracker is worker-local, as are all campaign callbacks.
func (p *campaignPanic) ownedError(err error) error {
	before := *p
	defer func() { *p = before }()
	return campaignOwnedError(err)
}

func (p *campaignPanic) set(value any) { p.seen, p.message = true, campaignPanicMessage(value) }

// The standalone Go fuzz API contains callback panics as ModelError. Campaign
// wrappers record recoverable callback unwinds before that boundary so campaigns
// still distinguish Panicked from returned ModelError, without changing N05.
func (p *campaignPanic) guard() {
	if v := recover(); v != nil {
		p.set(v)
		panic(p.message)
	}
}
