package statelessness

// autoFuzzControl shares the fuzz run's deadline and cancellation flag with the
// built-in finite-domain scanner. Its marker is private to this invocation.
type autoFuzzControl struct {
	fuzzControl
	interruptedReason string
}

type autoScanStopped struct{}

func (autoScanStopped) Error() string { return "automatic generation interrupted" }
func (c *autoFuzzControl) pollScan() error {
	if reason := c.stop(); reason != "" {
		c.interruptedReason = reason
		return autoScanStopped{}
	}
	return nil
}
func (c *autoFuzzControl) scanInterrupted() string { return c.interruptedReason }

// FuzzAutoWithLimits64 uses Auto64's finite-domain sampling with the fuzz run's
// own cooperative cancellation and deadline. It polls between iterator calls;
// it cannot interrupt application callbacks already in progress. A scan that
// stops does not commit its tentative sampling RNG state. This is an additive
// entry point: FuzzWithLimits64 applied to an Auto64 generator retains its
// existing callback-boundary behavior.
func FuzzAutoWithLimits64[S, I, O any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64, config FuzzConfig64, limits RunLimits) (*FuzzReport64[I], error) {
	return fuzzAutoObserved64(m, equal, options, config, limits, nil)
}

// FuzzAutoObserved64 observes automatic fuzzing while sharing its run control with scans.
func FuzzAutoObserved64[S, I, O any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64, config FuzzConfig64, limits RunLimits, observer SearchObserver[S, I, O]) (*FuzzReport64[I], error) {
	return fuzzAutoObserved64(m, equal, options, config, limits, &observer)
}

func fuzzAutoObserved64[S, I, O any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64, config FuzzConfig64, limits RunLimits, observer *SearchObserver[S, I, O]) (*FuzzReport64[I], error) {
	// Match ordinary fuzz: validate before starting the run deadline.
	g, err := Auto64(m, equal, options)
	if err != nil {
		return nil, err
	}
	if err = validateFuzzModel(g); err != nil {
		return nil, err
	}
	if err = validateFuzzConfig64(config); err != nil {
		return nil, err
	}
	control, err := newRunControl(limits)
	if err != nil {
		return nil, err
	}
	return fuzzAutoKernelObserved64(m, equal, options, config, control, observer)
}

func fuzzAutoKernel64[S, I, O any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64, config FuzzConfig64, control fuzzControl) (*FuzzReport64[I], error) {
	return fuzzAutoKernelObserved64(m, equal, options, config, control, nil)
}

func fuzzAutoKernelObserved64[S, I, O any](m Model[S, I, O], equal func(I, I) (bool, error), options AutoOptions64, config FuzzConfig64, control fuzzControl, observer *SearchObserver[S, I, O]) (*FuzzReport64[I], error) {
	owned := &autoFuzzControl{fuzzControl: control}
	g, err := auto64(m, equal, options, owned.pollScan)
	if err != nil {
		return nil, err
	}
	if err = validateFuzzModel(g); err != nil {
		return nil, err
	}
	if err = validateFuzzConfig64(config); err != nil {
		return nil, err
	}
	return fuzzKernel64(g, config, owned, observer, nil)
}
