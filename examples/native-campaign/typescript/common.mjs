import { DEFAULT_CAMPAIGN, DEFAULT_FUZZ, DEFAULT_GUIDANCE } from '@jagalite/statelessness';
import { QueueInput } from './queue-model.mjs';

export function campaignConfig(workers, stopOnFailure = false) {
  return {
    ...DEFAULT_CAMPAIGN,
    // masterSeed + firstJob wraps u64. Use jobSeed, never a shared worker RNG.
    masterSeed: (1n << 64n) - 3n,
    firstJob: 40n,
    jobs: 6,
    workers,
    resultBuffer: 0, // Rendezvous: each worker may still hold a completed report.
    fuzz: { ...DEFAULT_FUZZ, cases: 12, maxSteps: 20, maxTransitions: 240n },
    stopOnFailure,
  };
}

export function guidanceConfig() {
  return {
    ...DEFAULT_GUIDANCE,
    maxCorpusEntries: 8,
    maxCorpusInputs: 64,
    maxFeatures: 32,
    maxFeaturesPerObservation: 2,
  };
}

// Application display JSON, not a campaign checkpoint or portable trace schema.
// Exact bigint values become decimal strings; custom inputs keep a readable label.
export function displayJSON(value) {
  return JSON.stringify(value, (_key, item) => {
    if (typeof item === 'bigint') return item.toString();
    if (item instanceof QueueInput) return { ...item.toWire(), label: item.describe() };
    if (item instanceof Error) return { name: item.name, message: item.message };
    return item;
  });
}

export function inputLabels(job) {
  if (job.outcome.kind !== 'Completed') return [];
  const report = job.outcome.report;
  const inputs = report.failure?.inputs ?? report.guidance?.corpus[0]?.inputs ?? [];
  // This calls an instance method backed by private fields. In Node it relies on
  // workerModule.decodeInput, not structuredClone of a class instance.
  return inputs.map(input => input.describe());
}
