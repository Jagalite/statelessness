import test from 'node:test';
import assert from 'node:assert/strict';
import { fuzz, fuzzGuided, runCampaign, runGuidedCampaign, CampaignCancellation, validateCampaignConfig,
    automatic, sampleEnumerated, containsEnumerated, shrink, Rng, NO_INPUT, check, accepted, metadata, ModelError } from '../dist/index.js';
import { runNodeCampaign, runNodeGuidedCampaign } from '../dist/node_campaign.js';
import { Lifecycle } from '../dist/campaign_lifecycle.js';
import { LifecycleCodec } from './fixtures/campaign-lifecycle.mjs';
import { arrayGrowth, countLike, increment64 } from '../dist/counts.js';
const MAX = (1n << 64n) - 1n;
const fuzzConfig = (extra = {}) => ({ seed: MAX, cases: MAX, maxSteps: MAX, maxTransitions: MAX, mutationPercent: 0, ...extra });
const guidance = (extra = {}) => ({ maxCorpusEntries: MAX, maxCorpusInputs: MAX, maxFeatures: MAX, maxFeaturesPerObservation: MAX, ...extra });
const campaign = (extra = {}) => ({ masterSeed: MAX, firstJob: 1n, jobs: MAX, workers: 1n, resultBuffer: 0n,
    fuzz: fuzzConfig({ cases: 0n }), stopOnFailure: false, ...extra });
function model(extra = {}) {
    return { metadata: () => metadata('u64-boundary', '1'), initialState: () => 0,
        generate: () => NO_INPUT, step: s => accepted(s + 1), checkState: () => [],
        cloneState: s => s, cloneInput: i => i, cloneOutput: o => o,
        equalInputs: (a,b) => a === b, ...extra };
}
function feedback(extra = {}) {
    return { metadata: () => ({ name: 'u64-boundary', version: 1, build: '1' }),
        cloneFeature: k => k, equalFeatures: (a,b) => a === b,
        initialFeatures: (s,out) => out.push(s),
        transitionFeatures: (_b,_i,t,out) => out.push(t.state), ...extra };
}
const fail = () => assert.fail('callback must not be reached');
const initialFailure = () => model({ checkState: () => [check('failure', 'failed', 'bounded initial failure')] });

test('Rust64 fuzz budgets preserve cancellation, zero work, empty domains and failure precedence', () => {
    for (const cases of [0n, 1n << 63n, MAX]) {
        const report = fuzz(model({ initialState: fail }), fuzzConfig({ cases }), { cancellation: () => true });
        assert.equal(report.termination, 'cancelled'); assert.equal(report.cases, 0n); assert.equal(report.transitions, 0n);
    }
    assert.equal(fuzz(model({ initialState: fail }), fuzzConfig({ cases: 0n })).termination, 'cases_completed');
    const empty = fuzz(model(), fuzzConfig({ cases: 1n }));
    assert.equal(empty.cases, 1n); assert.equal(empty.transitions, 0n); assert.equal(empty.termination, 'cases_completed');
    const initial = fuzz(initialFailure(), fuzzConfig());
    assert.equal(initial.cases, 1n); assert.equal(initial.transitions, 0n); assert.deepEqual(initial.failure.inputs, []);
    let cancel = false;
    const edge = fuzz(model({ generate: () => 7, step: s => { cancel = true; return accepted(s + 1); },
        checkState: s => s ? [check('failure', 'failed', 'step wins cancellation')] : [] }), fuzzConfig(), { cancellation: () => cancel });
    assert.equal(edge.termination, 'failure_found'); assert.equal(edge.transitions, 1n); assert.deepEqual(edge.failure.inputs, [7]);
    assert.throws(() => fuzz(model(), fuzzConfig({ cases: MAX + 1n })), /u64/);
    assert.throws(() => fuzz(model(), { ...fuzzConfig(), cases: Number.MAX_SAFE_INTEGER + 1 }), /safe integer/);
});

test('guided u64 limits are lazy; metadata, validation and bounded retention keep their ordering', () => {
    let metadataCalls = 0;
    const f = feedback({ metadata: () => { metadataCalls++; return { name: 'wide', version: 1, build: '1' }; }, initialFeatures: fail });
    const report = fuzzGuided(model({ initialState: fail }), fuzzConfig(), guidance(), f, { cancellation: () => true });
    assert.equal(metadataCalls, 1); assert.equal(report.cases, 0n); assert.equal(report.guidance.unique_features, 0n);
    assert.deepEqual(report.guidance.config, guidance());
    assert.throws(() => fuzzGuided(model(), fuzzConfig({ mutationPercent: 101 }), guidance({ maxFeatures: 0n }), f), /guidance limits/);
    assert.throws(() => fuzzGuided(model(), fuzzConfig({ mutationPercent: 101 }), guidance(), f), /mutation_percent/);
    assert.equal(metadataCalls, 1);
    const retained = fuzzGuided(model({ generate: () => 1 }), fuzzConfig({ cases: 1n, maxSteps: 3n }), guidance({ maxCorpusEntries: 1n, maxCorpusInputs: 3n }), feedback());
    assert.equal(retained.guidance.unique_features, 4n); assert.equal(retained.guidance.corpus_inputs, 3n);
    assert.equal(retained.guidance.corpus.length, 1); assert.equal(retained.guidance.evicted_prefixes, 2n);
    assert.equal(retained.guidance.corpus[0].discovered_case, 1n); assert.equal(retained.guidance.corpus[0].new_features, 1n);
    const boundedNumbers = { maxCorpusEntries: 1, maxCorpusInputs: 3, maxFeatures: 4, maxFeaturesPerObservation: 1 };
    const mixed = fuzzGuided(model({ generate: () => 1 }), fuzzConfig({ cases: 1n, maxSteps: 1n }), boundedNumbers, feedback());
    assert.equal(mixed.guidance.corpus[0].discovered_case, 1n); assert.equal(mixed.guidance.mutation_cases, 0n);
});

test('guided metadata mutation cannot alter the admitted count representation', () => {
    const config = fuzzConfig({ cases: 0n });
    const result = fuzzGuided(model({ initialState: fail }), config, guidance(), feedback({ metadata: () => {
        config.cases = 123; config.maxSteps = 2;
        return { name: 'mutating-metadata', version: 1, build: '1' };
    } }));
    assert.equal(result.cases, 0n); assert.equal(result.guidance.unique_features, 0n);
    assert.equal(result.guidance.mutation_cases, 0n); assert.equal(result.guidance.corpus_inputs, 0n);
});

test('campaign u64 admission precedes control and computes full products before transition min', async () => {
    const limits = { cancellation: () => true };
    await assert.rejects(runCampaign(campaign({ jobs: 0n, workers: 0n, fuzz: fuzzConfig({ mutationPercent: 101 }) }), limits, fail, fail), /jobs must be positive/);
    await assert.rejects(runCampaign(campaign({ workers: MAX, fuzz: fuzzConfig({ mutationPercent: 101 }) }), limits, fail, fail), /workers/);
    await assert.rejects(runCampaign(campaign({ resultBuffer: MAX, fuzz: fuzzConfig({ mutationPercent: 101 }) }), limits, fail, fail), /result_buffer/);
    await assert.rejects(runCampaign(campaign({ firstJob: 2n, fuzz: fuzzConfig({ mutationPercent: 101 }) }), limits, fail, fail), /mutation_percent/);
    await assert.rejects(runCampaign(campaign({ firstJob: 2n }), limits, fail, fail), /job ID range/);
    await assert.rejects(runCampaign(campaign({ jobs: 2n, fuzz: fuzzConfig() }), limits, fail, fail), /aggregate cases/);
    await assert.rejects(runCampaign(campaign({ jobs: 2n, fuzz: fuzzConfig({ cases: 1n }) }), limits, fail, fail), /aggregate transitions/);
    await assert.rejects(runGuidedCampaign(campaign({ jobs: 0n }), limits, guidance({ maxFeatures: 0n }), fail, fail, fail), /guidance limits/);
    const admitted = campaign({ fuzz: fuzzConfig({ cases: 1n, maxTransitions: 1n }) });
    assert.deepEqual(validateCampaignConfig(admitted), admitted);
    const report = await runCampaign(admitted, limits, fail, fail);
    assert.equal(report.requested, MAX); assert.equal(report.notStarted, MAX);
    assert.equal(report.started, 0n); assert.equal(report.delivered, 0n); assert.equal(report.termination.kind, 'Cancelled');
});

test('huge campaign stops after one real failure and acknowledges after sink return', async () => {
    const jobs = [];
    const report = await runCampaign(campaign({ fuzz: fuzzConfig({ cases: 1n, maxTransitions: 1n }), stopOnFailure: true }), {}, initialFailure,
        async job => { jobs.push(job); await Promise.resolve(); });
    assert.equal(jobs.length, 1); assert.equal(jobs[0].jobId, 1n); assert.equal(jobs[0].config.maxSteps, MAX);
    assert.equal(jobs[0].outcome.report.cases, 1n); assert.equal(report.finished, 1n); assert.equal(report.delivered, 1n);
    assert.equal(report.failures, 1n); assert.equal(report.notStarted, MAX - 1n); assert.equal(report.termination.kind, 'FailureFound');
    const zero = await runCampaign(campaign({ jobs: 1n }), {}, model, () => {});
    assert.equal(zero.reportedCases, 0n); assert.equal(zero.delivered, 1n); assert.equal(zero.termination.kind, 'JobsCompleted');
});

test('Node workers preserve bigint configs, reports and acknowledgements with bounded workers', async () => {
    const token = new CampaignCancellation(); token.cancel();
    const descriptor = { module: new URL('./fixtures/u64-worker.mjs', import.meta.url), decodeInput: x => x };
    const stopped = await runNodeCampaign(campaign({ workers: 256n }), { cancellation: token }, descriptor, fail);
    assert.equal(stopped.requested, MAX); assert.equal(stopped.started, 0n);
    const jobs = [];
    const report = await runNodeGuidedCampaign(campaign({ firstJob: 0n, jobs: 2n, workers: 2n, resultBuffer: 0n,
        fuzz: fuzzConfig({ cases: 1n, maxTransitions: 1n }) }), {}, guidance(), descriptor, job => { jobs.push(job); });
    assert.equal(report.started, 2n); assert.equal(report.finished, 2n); assert.equal(report.delivered, 2n); assert.equal(report.failures, 2n);
    for (const job of jobs) { assert.equal(job.config.maxSteps, MAX); assert.equal(job.outcome.report.cases, 1n); assert.equal(job.outcome.report.guidance.config.maxFeatures, MAX); }
});

test('exact lifecycle codec restart shares reducer and never treats completion as acknowledgement', () => {
    const core = new Lifecycle(1n, MAX, false), codec = new LifecycleCodec(1n, MAX, false);
    core.apply({ kind: 'Claim', stop: null });
    core.apply({ kind: 'Complete', jobId: 1n, summary: { outcome: 'Completed', failure: false, transitions: MAX, cases: MAX, skippedChecks: MAX } });
    assert.equal(core.state.report.finished, 0n); assert.equal(core.state.report.delivered, 0n);
    const restarted = new Lifecycle(1n, MAX, false);
    restarted.state = codec.decodeExactState(codec.encodeExactState(core.state));
    restarted.apply({ kind: 'Receive', jobId: 1n }); assert.equal(restarted.state.report.delivered, 0n);
    restarted.apply({ kind: 'SinkReturned', jobId: 1n, error: null });
    restarted.apply({ kind: 'Claim', stop: null });
    restarted.apply({ kind: 'Complete', jobId: 2n, summary: { outcome: 'Completed', failure: false, transitions: 1n, cases: 1n, skippedChecks: 1n } });
    restarted.apply({ kind: 'Receive', jobId: 2n }); restarted.apply({ kind: 'SinkReturned', jobId: 2n, error: null });
    restarted.apply({ kind: 'Claim', stop: 'Cancelled' }); restarted.apply({ kind: 'Finish' });
    const report = restarted.state.report;
    assert.equal(report.started, 2n); assert.equal(report.notStarted, MAX - 2n); assert.equal(report.delivered, 2n);
    assert.equal(report.reportedCases, MAX); assert.equal(report.reportedTransitions, MAX); assert.equal(report.reportedSkippedChecks, MAX);
    assert.equal(report.accountingComplete, false); assert.equal(report.termination.kind, 'Cancelled');
    assert.deepEqual(codec.decodeExactState(codec.encodeExactState(restarted.state)), restarted.state);
    // A valid wide checkpoint resumes high ordinal scheduling in one bounded step.
    const high = new Lifecycle(0n, MAX, false);
    high.state.report.started = 1n << 63n; high.state.report.finished = 1n << 63n; high.state.report.delivered = 1n << 63n;
    high.state.report.notStarted = MAX - (1n << 63n);
    const highCodec = new LifecycleCodec(0n, MAX);
    high.state = highCodec.decodeExactState(highCodec.encodeExactState(high.state));
    assert.deepEqual(high.apply({ kind: 'Claim', stop: null }), [{ kind: 'RunJob', jobId: 1n << 63n }]);
    assert.equal(high.state.report.started, (1n << 63n) + 1n);
});

test('automatic sampling and shrink use exact bounds without huge materialization', () => {
    const m = model({ inputs: () => [], equalInputs: (a,b) => a === b });
    assert.equal(sampleEnumerated(m, 0, new Rng(1n), { maxCandidates: MAX }), NO_INPUT);
    assert.equal(containsEnumerated(m, 0, 1, { maxCandidates: MAX }), false);
    assert.equal(automatic(m, { maxCandidates: MAX }).generate(0, new Rng(1n)), NO_INPUT);
    const failure = { inputs: [], violations: [{ phase: 'state', check: check('failure', 'failed', 'bounded initial failure') }] };
    const stopped = shrink(initialFailure(), failure, { maxAttempts: MAX }, { cancellation: () => true });
    assert.equal(stopped.termination, 'cancelled'); assert.equal(stopped.attempts, 1n); assert.equal(stopped.history[0].candidate_length, 0n);
    const result = shrink(initialFailure(), failure, { maxAttempts: MAX });
    assert.equal(result.termination, 'search_complete'); assert.equal(result.attempts, 1n);
    assert.throws(() => countLike(MAX, 0), /precision/);
    assert.throws(() => arrayGrowth(0xffffffff), /capacity/);
    assert.equal(countLike(MAX, 0n), MAX);
    assert.equal(increment64(MAX - 1n, 'counter overflow'), MAX);
    assert.throws(() => increment64(MAX, 'replayed transition count overflow'), { name: 'ModelError', message: 'replayed transition count overflow' });
    assert.throws(() => increment64(MAX, 'simplification index overflow'), { name: 'ModelError', message: 'simplification index overflow' });
});
