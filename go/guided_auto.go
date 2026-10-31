package statelessness

// GuidedFuzzAutoWithLimits64 runs guided fuzzing with Auto64 finite-domain
// generation and membership scans sharing the run's cancellation and deadline.
// Polling is cooperative between iterator calls; callbacks already running
// cannot be interrupted. An interrupted generation scan does not commit its
// tentative sampling RNG state. Existing GuidedFuzzWithLimits64 behavior is
// unchanged when passed an Auto64 generator.
func GuidedFuzzAutoWithLimits64[S, I, O, K any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64, config FuzzConfig64, guidance GuidanceConfig64, feedback Feedback[S, I, O, K], limits RunLimits) (*FuzzReport64[I], error) {
	return guidedFuzzAutoObserved64(m, equal, options, config, guidance, feedback, limits, nil)
}

// GuidedFuzzAutoObserved64 is the observed variant of GuidedFuzzAutoWithLimits64.
func GuidedFuzzAutoObserved64[S, I, O, K any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64, config FuzzConfig64, guidance GuidanceConfig64, feedback Feedback[S, I, O, K], limits RunLimits, observer SearchObserver[S, I, O]) (*FuzzReport64[I], error) {
	return guidedFuzzAutoObserved64(m, equal, options, config, guidance, feedback, limits, &observer)
}

func guidedFuzzAutoObserved64[S, I, O, K any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64, config FuzzConfig64, guidance GuidanceConfig64, feedback Feedback[S, I, O, K], limits RunLimits, observer *SearchObserver[S, I, O]) (*FuzzReport64[I], error) {
	if err := guidance.Validate(); err != nil {
		return nil, err
	}
	if err := validateFuzzConfig64(config); err != nil {
		return nil, err
	}
	g, err := Auto64(m, equal, options)
	if err != nil {
		return nil, err
	}
	if err = validateFuzzModel(g); err != nil {
		return nil, err
	}
	if feedback.Metadata == nil || feedback.TransitionFeatures == nil || feedback.Key.Clone == nil || feedback.Key.Equal == nil {
		return nil, &ModelError{"feedback requires metadata, transition, clone and equality callbacks"}
	}
	control, err := newRunControl(limits)
	if err != nil {
		return nil, err
	}
	return guidedAutoKernel64(m, equal, options, config, guidance, feedback, control, observer)
}

// guidedAutoKernel64 also lets tests exercise a deterministic control clock.
func guidedAutoKernel64[S, I, O, K any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64, config FuzzConfig64, guidance GuidanceConfig64, feedback Feedback[S, I, O, K], control fuzzControl, observer *SearchObserver[S, I, O]) (*FuzzReport64[I], error) {
	owned := &autoFuzzControl{fuzzControl: control}
	g, err := auto64(m, equal, options, owned.pollScan)
	if err != nil {
		return nil, err
	}
	metadata, err := call("feedback metadata", feedback.Metadata)
	if err != nil {
		return nil, err
	}
	driver := &guidedDriver[S, I, O, K]{model: g, feedback: feedback, seen: make(map[uint64][]*seenFeature[K]), report: GuidanceReport64[I]{Algorithm: GuidanceAlgorithm, Config: guidance, Feedback: metadata, Corpus: []CorpusEntry64[I]{}}}
	report, err := fuzzKernel64(g, config, owned, observer, driver)
	if err != nil {
		return nil, err
	}
	report.Guidance = &driver.report
	return report, nil
}
