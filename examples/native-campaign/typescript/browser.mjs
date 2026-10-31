import { CampaignCancellation, runCampaign, runGuidedCampaign } from '@jagalite/statelessness';
import { campaignConfig, guidanceConfig } from './common.mjs';
import { QueueFeedback, QueueModel } from './queue-model.mjs';

// Browser-safe public entry point: no node:* imports, worker module, or private
// package paths. A host can supply its own async sink instead of the sample UI.
export async function runBrowserCampaign({
  guided = false,
  bug = false,
  cancellation = new CampaignCancellation(),
  maxDurationMs,
  onJob = () => {},
} = {}) {
  const config = campaignConfig(1, bug);
  const limits = { cancellation, ...(maxDurationMs === undefined ? {} : { maxDurationMs }) };
  const factory = jobId => new QueueModel(jobId, { bug });
  const feedbackFactory = (_jobId, model) => new QueueFeedback(model);
  const sink = async job => {
    // The synchronous fuzz job runs on the caller. Yielding between deliveries
    // lets the browser paint and dispatch Cancel; it cannot preempt a callback.
    await new Promise(resolve => setTimeout(resolve, 0));
    await onJob(job);
  };
  return guided
    ? await runGuidedCampaign(config, limits, guidanceConfig(), factory, feedbackFactory, sink)
    : await runCampaign(config, limits, factory, sink);
}
