// After the parent builds the candidate: npm install --ignore-scripts
// A packed candidate can instead be installed with:
// npm install --ignore-scripts /absolute/path/to/candidate.tgz
// npm run ordinary -- --bug
// npm run guided -- --cancel-after-first
// npm run guided -- --feedback-error --reject-sink
import { CampaignCancellation, ModelError, jobSeed } from '@jagalite/statelessness';
import { runNodeCampaign, runNodeGuidedCampaign } from '@jagalite/statelessness/node-campaign';
import { campaignConfig, displayJSON, guidanceConfig, inputLabels } from './common.mjs';
import { QueueInput } from './queue-model.mjs';

const [mode = 'ordinary', ...args] = process.argv.slice(2);
const allowed = new Set([
  '--bug', '--cancel-after-first', '--reject-sink', '--factory-error', '--feedback-error', '--panic',
]);
if (!['ordinary', 'guided'].includes(mode) || args.some(arg => !allowed.has(arg))
  || (mode === 'ordinary' && args.includes('--feedback-error'))) {
  throw new Error('Usage: node node.mjs ordinary|guided [--bug] [--cancel-after-first] '
    + '[--reject-sink] [--factory-error] [--panic] [--feedback-error (guided only)]');
}
const options = new Set(args);
const config = campaignConfig(3, options.has('--bug'));
const cancellation = new CampaignCancellation();
const cancel = () => cancellation.cancel();
process.on('SIGINT', cancel);

const workerModule = {
  module: new URL('./worker-model.mjs', import.meta.url),
  data: {
    bug: options.has('--bug'),
    factoryErrorJob: options.has('--factory-error') ? config.firstJob : null,
    feedbackErrorJob: options.has('--feedback-error') ? config.firstJob + 1n : null,
    panicJob: options.has('--panic') ? config.firstJob + 2n : null,
  },
  decodeInput: wire => QueueInput.fromWire(wire),
};

const collected = [];
let sinkCalls = 0;
async function sink(job) {
  sinkCalls++;
  // Slow asynchronous delivery exercises the bounded result queue. Receipt order
  // is host-dependent; no sorting occurs before sink invocation.
  await new Promise(resolve => setTimeout(resolve, 15));
  console.log(displayJSON({
    type: 'job',
    expectedSeed: jobSeed(config.masterSeed, job.jobId),
    inputLabels: inputLabels(job),
    job,
  }));
  if (options.has('--reject-sink')) {
    // The log above already happened. A rejected delivery is not retried or
    // counted as delivered; queued reports are still drained into the aggregate.
    throw new ModelError('example sink rejected after printing its first report');
  }
  collected.push({ jobId: job.jobId, seed: job.seed, outcome: job.outcome.kind });
  if (sinkCalls === 1 && options.has('--cancel-after-first')) cancellation.cancel();
}

try {
  // Multiworker limits use a shared token, never a caller-only cancellation
  // closure. Add maxDurationMs for a cooperative deadline (timing then varies).
  const limits = { cancellation };
  const report = mode === 'guided'
    ? await runNodeGuidedCampaign(config, limits, guidanceConfig(), workerModule, sink)
    : await runNodeCampaign(config, limits, workerModule, sink);
  // Sorting collected identities is an application presentation choice, not a
  // CampaignReport merge, queue checkpoint, or guarantee about receipt order.
  collected.sort((left, right) => left.jobId < right.jobId ? -1 : left.jobId > right.jobId ? 1 : 0);
  console.log(displayJSON({ type: 'campaign', report, collectedJobsById: collected }));
  // JobsCompleted alone does not imply success; errors and property failures are
  // distinct, and accountingComplete is not a claim that every job was delivered.
  if (report.errors || report.failures || !report.accountingComplete
    || report.termination.kind !== 'JobsCompleted') process.exitCode = 1;
} finally {
  process.off('SIGINT', cancel);
}
