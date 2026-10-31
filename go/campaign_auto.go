package statelessness

// RunAutoCampaign64 runs independent automatic fuzz jobs with each job's
// campaign cancellation and remaining deadline shared with its domain scan.
// Factory results need an ordinary Model in Model.Model; Generate and IsEnabled
// are supplied by Auto64. equal is shared by workers and must be concurrency-safe.
// Callbacks already executing cannot be preempted.
func RunAutoCampaign64[S, I, O any](config CampaignConfig64, limits RunLimits,
	factory func(uint64) (CampaignModel[S, I, O], error), equal func(I, I) (bool, error), options AutoOptions64,
	sink func(JobReport64[I]) error,
) (CampaignReport64, error) {
	return runCampaignUsing64(config, limits, factory, sink,
		func(_ uint64, model GenerateModel[S, I, O], cfg FuzzConfig64, remaining RunLimits, p *campaignPanic) (*FuzzReport64[I], error) {
			return FuzzAutoWithLimits64(model.Model, campaignAutoEqual(equal, p), options, cfg, remaining)
		}, campaignRuntime{})
}

func campaignAutoEqual[I any](equal func(I, I) (bool, error), p *campaignPanic) func(I, I) (bool, error) {
	if equal == nil {
		return nil
	}
	return func(a, b I) (bool, error) { defer p.guard(); v, err := equal(a, b); return v, p.ownedError(err) }
}

// RunGuidedAutoCampaign64 runs automatic guided fuzz jobs with a fresh feedback
// object per job. The feedback factory receives an ordinary Auto64 model; the
// search itself uses a scanner bound to that job's campaign control. equal and
// factory captures must be safe for concurrent workers. Feedback closes before
// its corresponding model, including when search stops or errors.
func RunGuidedAutoCampaign64[S, I, O, K any](config CampaignConfig64, limits RunLimits, guidance GuidanceConfig64,
	factory func(uint64) (CampaignModel[S, I, O], error), equal func(I, I) (bool, error), options AutoOptions64,
	feedbackFactory func(uint64, GenerateModel[S, I, O]) (CampaignFeedback[S, I, O, K], error), sink func(JobReport64[I]) error,
) (CampaignReport64, error) {
	if err := guidance.Validate(); err != nil {
		return CampaignReport64{}, err
	}
	return runCampaignUsing64(config, limits, factory, sink,
		func(id uint64, model GenerateModel[S, I, O], cfg FuzzConfig64, remaining RunLimits, p *campaignPanic) (report *FuzzReport64[I], err error) {
			control, err := newRunControl(remaining)
			if err != nil {
				return nil, err
			}
			g, err := Auto64(model.Model, campaignAutoEqual(equal, p), options)
			if err != nil {
				return nil, err
			}
			if feedbackFactory == nil {
				return nil, &ModelError{"nil campaign feedback factory"}
			}
			feedback, err := feedbackFactory(id, g)
			if err != nil {
				return nil, p.ownedError(err)
			}
			if feedback.Close != nil {
				defer func() {
					if e := feedback.Close(); e != nil {
						report, err = nil, p.ownedError(e)
					}
				}()
			}
			return GuidedFuzzAutoWithLimits64(model.Model, campaignAutoEqual(equal, p), options, cfg, guidance, campaignGuardFeedback(feedback.Feedback, p), campaignRemaining(control))
		}, campaignRuntime{})
}
