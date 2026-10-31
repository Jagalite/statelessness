package statelessness

// Legacy signed-count APIs remain checked adapters over the exact-width kernels.

type FuzzConfig struct {
	Seed            uint64
	Cases, MaxSteps int
	MaxTransitions  uint64
	MutationPercent int
}

func DefaultFuzzConfig() FuzzConfig { return FuzzConfig{0, 1000, 100, 100000, 50} }

type FuzzReport[I any] struct {
	Termination                string
	Seed                       uint64
	Cases                      int
	Transitions, SkippedChecks uint64
	Failure                    *Failure[I]
	// Guidance is nil for ordinary fuzz and owned by a successful guided report.
	Guidance *GuidanceReport[I]
}

type GuidanceConfig struct {
	MaxCorpusEntries          int `json:"max_corpus_entries"`
	MaxCorpusInputs           int `json:"max_corpus_inputs"`
	MaxFeatures               int `json:"max_features"`
	MaxFeaturesPerObservation int `json:"max_features_per_observation"`
}

func DefaultGuidanceConfig() GuidanceConfig { return GuidanceConfig{256, 25600, 100000, 256} }

type CorpusEntry[I any] struct {
	Inputs []I `json:"inputs"`
	// NewFeatures is zero for recovery of previously dropped discoveries.
	NewFeatures          int    `json:"new_features"`
	DiscoveredCase       int    `json:"discovered_case"`
	DiscoveredTransition uint64 `json:"discovered_transition"`
}

type GuidanceReport[I any] struct {
	Algorithm           string           `json:"algorithm"`
	Config              GuidanceConfig   `json:"config"`
	Feedback            FeedbackMetadata `json:"feedback"`
	UniqueFeatures      int              `json:"unique_features"`
	CorpusInputs        int              `json:"corpus_inputs"`
	Corpus              []CorpusEntry[I] `json:"corpus"`
	MutationCases       int              `json:"mutation_cases"`
	DroppedPrefixes     uint64           `json:"dropped_prefixes"`
	EvictedPrefixes     uint64           `json:"evicted_prefixes"`
	FeatureLimitReached bool             `json:"feature_limit_reached"`
	CorpusLimitReached  bool             `json:"corpus_limit_reached"`
}

type CampaignConfig struct {
	MasterSeed, FirstJob        uint64
	Jobs, Workers, ResultBuffer int
	Fuzz                        FuzzConfig
	StopOnFailure               bool
}

func DefaultCampaignConfig() CampaignConfig {
	return CampaignConfig{Jobs: 16, Workers: 1, ResultBuffer: 1, Fuzz: DefaultFuzzConfig()}
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

type CampaignReport struct {
	Requested, Started, Finished, NotStarted, Delivered, Failures, Errors int
	ReportedTransitions, ReportedCases, ReportedSkippedChecks             uint64
	AccountingComplete                                                    bool
	Termination                                                           CampaignTermination
}

// Exact widens every count losslessly after validating the legacy representation.
func (c FuzzConfig) Exact() (FuzzConfig64, error) {
	if err := validateFuzzConfig(c); err != nil {
		return FuzzConfig64{}, err
	}
	return FuzzConfig64{c.Seed, uint64(c.Cases), uint64(c.MaxSteps), c.MaxTransitions, c.MutationPercent}, nil
}
func validateFuzzConfig(c FuzzConfig) error {
	if c.Cases < 0 || c.MaxSteps < 0 || c.MutationPercent < 0 || c.MutationPercent > 100 {
		return &ConfigError{"invalid fuzz configuration"}
	}
	return nil
}
func (c GuidanceConfig) Validate() error {
	if c.MaxCorpusEntries <= 0 || c.MaxCorpusInputs <= 0 || c.MaxFeatures <= 0 || c.MaxFeaturesPerObservation <= 0 {
		return &ConfigError{"guidance limits must be positive"}
	}
	return nil
}
func (c GuidanceConfig) Exact() (GuidanceConfig64, error) {
	if err := c.Validate(); err != nil {
		return GuidanceConfig64{}, err
	}
	return GuidanceConfig64{uint64(c.MaxCorpusEntries), uint64(c.MaxCorpusInputs), uint64(c.MaxFeatures), uint64(c.MaxFeaturesPerObservation)}, nil
}
func (c CampaignConfig) Validate() error { _, err := c.Exact(); return err }
func (c CampaignConfig) Exact() (CampaignConfig64, error) {
	// Keep native negative-value diagnostics and Rust's admission precedence.
	if c.Jobs <= 0 {
		return CampaignConfig64{}, &ConfigError{"campaign jobs must be positive"}
	}
	if c.Workers < 1 || c.Workers > 256 {
		return CampaignConfig64{}, &ConfigError{"campaign workers must be in 1..=256"}
	}
	if c.ResultBuffer < 0 || c.ResultBuffer > 1024 {
		return CampaignConfig64{}, &ConfigError{"campaign result_buffer must be in 0..=1024"}
	}
	if c.Fuzz.MutationPercent < 0 || c.Fuzz.MutationPercent > 100 {
		return CampaignConfig64{}, &ConfigError{"mutation_percent must be in 0..=100"}
	}
	if uint64(c.Jobs)-1 > ^uint64(0)-c.FirstJob {
		return CampaignConfig64{}, &ConfigError{"campaign job ID range overflows u64"}
	}
	if c.Fuzz.Cases < 0 {
		return CampaignConfig64{}, &ConfigError{"campaign per-job cases must be nonnegative"}
	}
	if c.Fuzz.MaxSteps < 0 {
		return CampaignConfig64{}, &ConfigError{"campaign per-job steps must be nonnegative"}
	}
	f, _ := c.Fuzz.Exact()
	exact := CampaignConfig64{MasterSeed: c.MasterSeed, FirstJob: c.FirstJob, Jobs: uint64(c.Jobs), Workers: uint64(c.Workers), ResultBuffer: uint64(c.ResultBuffer), Fuzz: f, StopOnFailure: c.StopOnFailure}
	return exact, exact.Validate()
}
func countsFit(values ...uint64) error {
	for _, v := range values {
		if v > uint64(^uint(0)>>1) {
			return &ConfigError{"count exceeds native int range; use exact-width API"}
		}
	}
	return nil
}
func (c FuzzConfig64) Legacy() (FuzzConfig, error) {
	if err := countsFit(c.Cases, c.MaxSteps); err != nil {
		return FuzzConfig{}, err
	}
	return FuzzConfig{c.Seed, int(c.Cases), int(c.MaxSteps), c.MaxTransitions, c.MutationPercent}, nil
}
func (c GuidanceConfig64) Legacy() (GuidanceConfig, error) {
	if err := countsFit(c.MaxCorpusEntries, c.MaxCorpusInputs, c.MaxFeatures, c.MaxFeaturesPerObservation); err != nil {
		return GuidanceConfig{}, err
	}
	return GuidanceConfig{int(c.MaxCorpusEntries), int(c.MaxCorpusInputs), int(c.MaxFeatures), int(c.MaxFeaturesPerObservation)}, nil
}
func (r *FuzzReport64[I]) Legacy() (*FuzzReport[I], error) {
	if r == nil {
		return nil, nil
	}
	if err := countsFit(r.Cases); err != nil {
		return nil, err
	}
	out := &FuzzReport[I]{Termination: r.Termination, Seed: r.Seed, Cases: int(r.Cases), Transitions: r.Transitions, SkippedChecks: r.SkippedChecks, Failure: r.Failure}
	if g := r.Guidance; g != nil {
		c, err := g.Config.Legacy()
		if err != nil {
			return nil, err
		}
		if err := countsFit(g.UniqueFeatures, g.CorpusInputs, g.MutationCases); err != nil {
			return nil, err
		}
		v := &GuidanceReport[I]{Algorithm: g.Algorithm, Config: c, Feedback: g.Feedback, UniqueFeatures: int(g.UniqueFeatures), CorpusInputs: int(g.CorpusInputs), MutationCases: int(g.MutationCases), DroppedPrefixes: g.DroppedPrefixes, EvictedPrefixes: g.EvictedPrefixes, FeatureLimitReached: g.FeatureLimitReached, CorpusLimitReached: g.CorpusLimitReached, Corpus: make([]CorpusEntry[I], len(g.Corpus))}
		for i, e := range g.Corpus {
			if err := countsFit(e.NewFeatures, e.DiscoveredCase); err != nil {
				return nil, err
			}
			v.Corpus[i] = CorpusEntry[I]{e.Inputs, int(e.NewFeatures), int(e.DiscoveredCase), e.DiscoveredTransition}
		}
		out.Guidance = v
	}
	return out, nil
}
func (r CampaignReport64) Legacy() (CampaignReport, error) {
	if err := countsFit(r.Requested, r.Started, r.Finished, r.NotStarted, r.Delivered, r.Failures, r.Errors); err != nil {
		return CampaignReport{}, err
	}
	return CampaignReport{int(r.Requested), int(r.Started), int(r.Finished), int(r.NotStarted), int(r.Delivered), int(r.Failures), int(r.Errors), r.ReportedTransitions, r.ReportedCases, r.ReportedSkippedChecks, r.AccountingComplete, r.Termination}, nil
}
func (j JobReport64[I]) Legacy() (JobReport[I], error) {
	c, err := j.Config.Legacy()
	if err != nil {
		return JobReport[I]{}, err
	}
	r, err := j.Outcome.Report.Legacy()
	if err != nil {
		return JobReport[I]{}, err
	}
	return JobReport[I]{j.JobID, j.Seed, c, j.Metadata, JobOutcome[I]{j.Outcome.Kind, r, j.Outcome.Error, j.Outcome.Panic}}, nil
}
func Fuzz[S, I, O any](g GenerateModel[S, I, O], c FuzzConfig) (*FuzzReport[I], error) {
	return FuzzWithLimits(g, c, RunLimits{})
}
func FuzzWithLimits[S, I, O any](g GenerateModel[S, I, O], c FuzzConfig, l RunLimits) (*FuzzReport[I], error) {
	return fuzzObserved(g, c, l, nil)
}
func fuzzObserved[S, I, O any](g GenerateModel[S, I, O], c FuzzConfig, l RunLimits, o *SearchObserver[S, I, O]) (*FuzzReport[I], error) {
	if err := validateFuzzModel(g); err != nil {
		return nil, err
	}
	exact, err := c.Exact()
	if err != nil {
		return nil, err
	}
	r, err := fuzzObserved64(g, exact, l, o)
	if err != nil {
		return nil, err
	}
	return r.Legacy()
}
func FuzzObserved64[S, I, O any](g GenerateModel[S, I, O], c FuzzConfig64, l RunLimits, o SearchObserver[S, I, O]) (*FuzzReport64[I], error) {
	return fuzzObserved64(g, c, l, &o)
}
func fuzzKernel[S, I, O any](g GenerateModel[S, I, O], c FuzzConfig, control fuzzControl, o *SearchObserver[S, I, O], d fuzzDriver[S, I, O]) (*FuzzReport[I], error) {
	exact, err := c.Exact()
	if err != nil {
		return nil, err
	}
	r, err := fuzzKernel64(g, exact, control, o, d)
	if err != nil {
		return nil, err
	}
	return r.Legacy()
}
func GuidedFuzz[S, I, O, K any](g GenerateModel[S, I, O], c FuzzConfig, h GuidanceConfig, f Feedback[S, I, O, K]) (*FuzzReport[I], error) {
	return GuidedFuzzWithLimits(g, c, h, f, RunLimits{})
}
func GuidedFuzzWithLimits[S, I, O, K any](g GenerateModel[S, I, O], c FuzzConfig, h GuidanceConfig, f Feedback[S, I, O, K], l RunLimits) (*FuzzReport[I], error) {
	return guidedFuzzObserved(g, c, h, f, l, nil)
}
func GuidedFuzzObserved[S, I, O, K any](g GenerateModel[S, I, O], c FuzzConfig, h GuidanceConfig, f Feedback[S, I, O, K], l RunLimits, o SearchObserver[S, I, O]) (*FuzzReport[I], error) {
	return guidedFuzzObserved(g, c, h, f, l, &o)
}
func guidedFuzzObserved[S, I, O, K any](g GenerateModel[S, I, O], c FuzzConfig, h GuidanceConfig, f Feedback[S, I, O, K], l RunLimits, o *SearchObserver[S, I, O]) (*FuzzReport[I], error) {
	exactH, err := h.Exact()
	if err != nil {
		return nil, err
	}
	exactC, err := c.Exact()
	if err != nil {
		return nil, err
	}
	r, err := guidedFuzzObserved64(g, exactC, exactH, f, l, o)
	if err != nil {
		return nil, err
	}
	return r.Legacy()
}
func campaignLegacySink[I any](sink func(JobReport[I]) error) func(JobReport64[I]) error {
	if sink == nil {
		return nil
	}
	return func(j JobReport64[I]) error {
		old, err := j.Legacy()
		if err != nil {
			return err
		}
		return sink(old)
	}
}
func RunCampaign[S, I, O any](c CampaignConfig, l RunLimits, f func(uint64) (CampaignModel[S, I, O], error), s func(JobReport[I]) error) (CampaignReport, error) {
	exact, err := c.Exact()
	if err != nil {
		return CampaignReport{}, err
	}
	r, err := RunCampaign64(exact, l, f, campaignLegacySink(s))
	if err != nil {
		return CampaignReport{}, err
	}
	return r.Legacy()
}
func RunGuidedCampaign[S, I, O, K any](c CampaignConfig, l RunLimits, h GuidanceConfig, f func(uint64) (CampaignModel[S, I, O], error), feedback func(uint64, GenerateModel[S, I, O]) (CampaignFeedback[S, I, O, K], error), s func(JobReport[I]) error) (CampaignReport, error) {
	exactH, err := h.Exact()
	if err != nil {
		return CampaignReport{}, err
	}
	exact, err := c.Exact()
	if err != nil {
		return CampaignReport{}, err
	}
	r, err := RunGuidedCampaign64(exact, l, exactH, f, feedback, campaignLegacySink(s))
	if err != nil {
		return CampaignReport{}, err
	}
	return r.Legacy()
}

// Private compatibility seam used by executor failure-injection tests.
type campaignRunner[S, I, O any] func(uint64, GenerateModel[S, I, O], FuzzConfig, RunLimits, *campaignPanic) (*FuzzReport[I], error)

func runCampaignUsing[S, I, O any](c CampaignConfig, l RunLimits, f func(uint64) (CampaignModel[S, I, O], error), s func(JobReport[I]) error, runner campaignRunner[S, I, O], runtime campaignRuntime) (CampaignReport, error) {
	exact, err := c.Exact()
	if err != nil {
		return CampaignReport{}, err
	}
	r, err := runCampaignUsing64(exact, l, f, campaignLegacySink(s), func(id uint64, g GenerateModel[S, I, O], c FuzzConfig64, l RunLimits, p *campaignPanic) (*FuzzReport64[I], error) {
		old, err := c.Legacy()
		if err != nil {
			return nil, err
		}
		r, err := runner(id, g, old, l, p)
		if err != nil {
			return nil, err
		}
		return r.exact()
	}, runtime)
	if err != nil {
		return CampaignReport{}, err
	}
	return r.Legacy()
}
func (r *FuzzReport[I]) exact() (*FuzzReport64[I], error) {
	if r == nil {
		return nil, nil
	}
	if r.Cases < 0 {
		return nil, &ConfigError{"negative report count"}
	}
	out := &FuzzReport64[I]{Termination: r.Termination, Seed: r.Seed, Cases: uint64(r.Cases), Transitions: r.Transitions, SkippedChecks: r.SkippedChecks, Failure: r.Failure}
	if g := r.Guidance; g != nil {
		c, err := g.Config.Exact()
		if err != nil {
			return nil, err
		}
		if g.UniqueFeatures < 0 || g.CorpusInputs < 0 || g.MutationCases < 0 {
			return nil, &ConfigError{"negative report count"}
		}
		v := &GuidanceReport64[I]{Algorithm: g.Algorithm, Config: c, Feedback: g.Feedback, UniqueFeatures: uint64(g.UniqueFeatures), CorpusInputs: uint64(g.CorpusInputs), MutationCases: uint64(g.MutationCases), DroppedPrefixes: g.DroppedPrefixes, EvictedPrefixes: g.EvictedPrefixes, FeatureLimitReached: g.FeatureLimitReached, CorpusLimitReached: g.CorpusLimitReached, Corpus: make([]CorpusEntry64[I], len(g.Corpus))}
		for i, e := range g.Corpus {
			if e.NewFeatures < 0 || e.DiscoveredCase < 0 {
				return nil, &ConfigError{"negative report count"}
			}
			v.Corpus[i] = CorpusEntry64[I]{e.Inputs, uint64(e.NewFeatures), uint64(e.DiscoveredCase), e.DiscoveredTransition}
		}
		out.Guidance = v
	}
	return out, nil
}

type ShrinkConfig struct{ MaxAttempts int }

func DefaultShrinkConfig() ShrinkConfig { return ShrinkConfig{10000} }

type ShrinkOperation struct {
	Kind                           string
	Start, End, Index, Alternative int
}

type ShrinkAttempt struct {
	Attempt, CandidateLength int
	Operation                ShrinkOperation
	Outcome                  string
}

type ShrinkReport[I any] struct {
	Original, Minimized Failure[I]
	Attempts            int
	Termination         string
	History             []ShrinkAttempt
	ReplayedTransitions uint64
	ValidatedOriginal   bool
}

type AutoOptions struct{ MaxCandidates int }

func DefaultAutoOptions() AutoOptions { return AutoOptions{100000} }
func Auto[S, I, O any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions) (GenerateModel[S, I, O], error) {
	if e := m.validate(); e != nil {
		return GenerateModel[S, I, O]{}, e
	}
	if options.MaxCandidates <= 0 {
		return GenerateModel[S, I, O]{}, &ConfigError{"auto requires inputs, equality, and positive candidate cap"}
	}
	return Auto64(m, equal, AutoOptions64{uint64(options.MaxCandidates)})
}
func CampaignAuto[S, I, O any](m CampaignModel[S, I, O], equal func(I, I) (bool, error), options AutoOptions) (CampaignModel[S, I, O], error) {
	if options.MaxCandidates <= 0 {
		_, err := Auto(m.Model.Model, equal, options)
		return m, err
	}
	return CampaignAuto64(m, equal, AutoOptions64{uint64(options.MaxCandidates)})
}
func Shrink[S, I, O any](g GenerateModel[S, I, O], failure Failure[I], c ShrinkConfig) (*ShrinkReport[I], error) {
	return ShrinkWithLimits(g, failure, c, ShrinkLimits{})
}
func ShrinkWithLimits[S, I, O any](g GenerateModel[S, I, O], failure Failure[I], c ShrinkConfig, l ShrinkLimits) (*ShrinkReport[I], error) {
	// Model admission precedes configuration validation, as in the exact kernel.
	if e := g.validate(); e != nil {
		return nil, e
	}
	if g.InitialState == nil || g.Step == nil || g.InputEqual == nil {
		return nil, &ModelError{"shrink requires initial, step and input equality callbacks"}
	}
	if c.MaxAttempts <= 0 {
		return nil, &ConfigError{"shrink max attempts must be positive"}
	}
	r, e := ShrinkWithLimits64(g, failure, ShrinkConfig64{uint64(c.MaxAttempts)}, l)
	if e != nil {
		return nil, e
	}
	return r.Legacy()
}
func (r *ShrinkReport64[I]) Legacy() (*ShrinkReport[I], error) {
	if r == nil {
		return nil, nil
	}
	if e := countsFit(r.Attempts); e != nil {
		return nil, e
	}
	out := &ShrinkReport[I]{r.Original, r.Minimized, int(r.Attempts), r.Termination, make([]ShrinkAttempt, len(r.History)), r.ReplayedTransitions, r.ValidatedOriginal}
	for i, a := range r.History {
		op := a.Operation
		if e := countsFit(a.Attempt, a.CandidateLength, op.Start, op.End, op.Index, op.Alternative); e != nil {
			return nil, e
		}
		out.History[i] = ShrinkAttempt{int(a.Attempt), int(a.CandidateLength), ShrinkOperation{op.Kind, int(op.Start), int(op.End), int(op.Index), int(op.Alternative)}, a.Outcome}
	}
	return out, nil
}
