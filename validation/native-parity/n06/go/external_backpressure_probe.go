//go:build n06_causal_probe

// This file is injected with Go's build overlay for qualification only. It is
// not part of the module or its ordinary exported API.
package statelessness

// N06CausalHooks observe the actual coordinator and channel admission path.
type N06CausalHooks struct {
	AfterComplete func(uint64)
	SendBlocked   func(uint64)
	AfterSend     func(uint64)
}

// RunN06CausalProbe invokes the same implementation and fuzz runner as the
// public RunCampaign64, with only private observational hooks enabled.
func RunN06CausalProbe[S, I, O any](c CampaignConfig64, l RunLimits, f func(uint64) (CampaignModel[S, I, O], error), s func(JobReport64[I]) error, h N06CausalHooks) (CampaignReport64, error) {
	return runCampaignUsing64(c, l, f, s, func(_ uint64, m GenerateModel[S, I, O], c FuzzConfig64, l RunLimits, _ *campaignPanic) (*FuzzReport64[I], error) {
		return FuzzWithLimits64(m, c, l)
	}, campaignRuntime{afterComplete: h.AfterComplete, sendBlocked: h.SendBlocked, afterSend: h.AfterSend})
}
