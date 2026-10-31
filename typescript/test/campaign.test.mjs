import test from 'node:test';
import assert from 'node:assert/strict';
import { threadId } from 'node:worker_threads';
import { setTimeout as delay } from 'node:timers/promises';
import {
    accepted, check, metadata, ModelError, Rng, NO_INPUT,
    DEFAULT_FUZZ, DEFAULT_GUIDANCE, DEFAULT_CAMPAIGN, CampaignCancellation,
    jobSeed, fuzz, fuzzGuided, runCampaign, runGuidedCampaign,
} from '@jagalite/statelessness';
import { runNodeCampaign, runNodeGuidedCampaign } from '@jagalite/statelessness/node-campaign';
// Private fixed telemetry; the exercised public Node host still owns the real transport.
import { attachCampaignTransportProbe, TRANSPORT_PROBE_FIELDS, TRANSPORT_PROBE_WORDS } from '../dist/campaign_test_support.js';
import {
    CampaignInput, createModel, createFeedback, decodeInput,
    slots, SLOT_COUNT, waitForAtLeast,
} from './fixtures/campaign-worker.mjs';

// All campaigns use public entrypoints. Per-job comparisons exercise public N05
// engines independently of campaign scheduling; Rust golden comparison is a
// separate source-bound qualification gate, not implied by these unit tests.
const U64_MAX = (1n << 64n) - 1n;
const workerURL = new URL('./fixtures/campaign-worker.mjs', import.meta.url);
const fuzzConfig = (overrides = {}) => ({
    ...DEFAULT_FUZZ, seed: 999n, cases: 3, maxSteps: 4, maxTransitions: 100n,
    mutationPercent: 65, ...overrides,
});
const campaign = (overrides = {}) => ({
    ...DEFAULT_CAMPAIGN, masterSeed: 37n, firstJob: 0n, jobs: 4,
    fuzz: fuzzConfig(), ...overrides,
});
const guidance = { ...DEFAULT_GUIDANCE, maxCorpusEntries: 4, maxCorpusInputs: 12, maxFeatures: 20 };
const model = (overrides = {}) => ({
    metadata: () => metadata('campaign-unit', 'v1'),
    initialState: () => 0, generate: (_s, rng) => Number(rng.index(7n)),
    step: state => accepted(state + 1), checkState: () => [],
    cloneState: s => s, cloneInput: i => i, cloneOutput: o => o,
    equalInputs: (a, b) => a === b, isEnabled: () => true, ...overrides,
});
const feedback = (overrides = {}) => ({
    metadata: () => ({ name: 'unit-depth', version: 1, build: 'v1' }),
    cloneFeature: x => x, equalFeatures: (a, b) => a === b,
    transitionFeatures: (_b, _i, after, out) => { out.push(after.state); },
    ...overrides,
});
const fixture = (data = {}) => ({ module: workerURL, data, decodeInput });
function shared() { return new Int32Array(new SharedArrayBuffer(SLOT_COUNT * Int32Array.BYTES_PER_ELEMENT)); }
function release(view) { Atomics.store(view, slots.release, 1); Atomics.notify(view, slots.release); }
function waitForTransport(probe, predicate) {
    const end = performance.now() + 8_000;
    for (;;) {
        const version = Atomics.load(probe, TRANSPORT_PROBE_FIELDS.version);
        const snapshot = Object.fromEntries(Object.entries(TRANSPORT_PROBE_FIELDS)
            .map(([key, slot]) => [key, Atomics.load(probe, slot)]));
        if (version % 2n === 0n && version === Atomics.load(probe, TRANSPORT_PROBE_FIELDS.version)
            && predicate(snapshot)) return snapshot;
        const remaining = end - performance.now();
        if (remaining <= 0) assert.fail(`transport never reached actual blocked sends: ${JSON.stringify(snapshot, (_k, v) => String(v))}`);
        // This timeout is a watchdog, not evidence that a sender is blocked.
        Atomics.wait(probe, TRANSPORT_PROBE_FIELDS.version, version, remaining);
    }
}
function sorted(jobs) { return [...jobs].sort((a, b) => a.jobId < b.jobId ? -1 : a.jobId > b.jobId ? 1 : 0); }
function completed(job) {
    assert.equal(job.outcome.kind, 'Completed', JSON.stringify(job.outcome, (_k, v) => typeof v === 'bigint' ? String(v) : v));
    return job.outcome.report;
}
function inputValues(job) {
    const report = completed(job);
    return {
        failure: report.failure?.inputs.map(input => input.value()) ?? null,
        corpus: report.guidance?.corpus.map(entry => entry.inputs.map(input => input.value())) ?? null,
    };
}
function assertAccounting(report, expected = {}) {
    assert.equal(report.requested, report.started + report.notStarted);
    assert.ok(report.delivered <= report.finished);
    assert.ok(report.finished <= report.started);
    for (const key of ['requested', 'started', 'finished', 'notStarted', 'delivered', 'failures', 'errors'])
        assert.equal(typeof report[key], 'number', key);
    for (const key of ['reportedTransitions', 'reportedCases', 'reportedSkippedChecks'])
        assert.equal(typeof report[key], 'bigint', key);
    for (const [key, value] of Object.entries(expected)) assert.deepEqual(report[key], value, key);
}
function expectedSums(jobs) {
    const reports = jobs.filter(j => j.outcome.kind === 'Completed').map(j => j.outcome.report);
    return {
        reportedTransitions: reports.reduce((n, r) => n + r.transitions, 0n),
        reportedCases: reports.reduce((n, r) => n + BigInt(r.cases), 0n),
        reportedSkippedChecks: reports.reduce((n, r) => n + r.skipped_checks, 0n),
    };
}
function referenceJob(config, jobId, guided = false, data = {}) {
    const seed = new Rng(BigInt.asUintN(64, config.masterSeed + jobId)).nextU64();
    const perJob = { ...config.fuzz, seed };
    const m = createModel(jobId, data);
    return {
        jobId, seed, config: perJob, metadata: m.metadata(),
        outcome: { kind: 'Completed', report: guided
            ? fuzzGuided(m, perJob, guidance, createFeedback(jobId, m, data))
            : fuzz(m, perJob) },
    };
}
function assertTransferredInputs(job) {
    const report = completed(job);
    const inputs = [
        ...(report.failure?.inputs ?? []),
        ...(report.guidance?.corpus.flatMap(entry => entry.inputs) ?? []),
    ];
    assert.ok(inputs.length > 0, 'fixture must expose transferred report inputs');
    for (const input of inputs) {
        assert.ok(input instanceof CampaignInput, 'decodeInput must restore the application class');
        assert.equal(typeof input.value(), 'bigint', 'private input data survives transfer');
    }
}

test('public defaults and SplitMix64 job seeds include wrapping addition and max IDs', () => {
    assert.deepEqual(DEFAULT_CAMPAIGN, {
        masterSeed: 0n, firstJob: 0n, jobs: 16, workers: 1, resultBuffer: 1,
        fuzz: DEFAULT_FUZZ, stopOnFailure: false,
    });
    assert.equal(jobSeed(0n, 0n), 16294208416658607535n);
    assert.equal(jobSeed(0n, 1n), 10451216379200822465n);
    assert.equal(jobSeed(0n, 7n), 7191089600892374487n);
    assert.equal(jobSeed(U64_MAX, 1n), jobSeed(0n, 0n));
    assert.equal(jobSeed(1n, U64_MAX), jobSeed(0n, 0n));
    for (const master of [0n, 37n, U64_MAX]) for (const id of [0n, 7n, U64_MAX])
        assert.equal(jobSeed(master, id), new Rng(BigInt.asUintN(64, master + id)).nextU64());
    assert.equal(new Set(Array.from({ length: 32 }, (_, i) => jobSeed(37n, BigInt(i)))).size, 32);
});

test('invalid admission calls neither factory nor sink, even when pre-cancelled', async () => {
    let calls = 0;
    const forbidden = () => { calls++; throw new Error('callback before valid admission'); };
    const invalid = [
        { jobs: 0 }, { jobs: -1 }, { jobs: 1.5 }, { jobs: Number.MAX_SAFE_INTEGER + 1 },
        { workers: 0 }, { workers: 257 }, { workers: 1.5 },
        { resultBuffer: -1 }, { resultBuffer: 1025 }, { resultBuffer: 0.5 },
        { firstJob: U64_MAX, jobs: 2 }, { firstJob: -1n }, { masterSeed: U64_MAX + 1n },
        { fuzz: fuzzConfig({ mutationPercent: 101 }) },
        { jobs: 4096, fuzz: fuzzConfig({ cases: Number.MAX_SAFE_INTEGER, maxSteps: 0 }) },
        { jobs: 2, fuzz: fuzzConfig({ cases: Number.MAX_SAFE_INTEGER, maxSteps: Number.MAX_SAFE_INTEGER, maxTransitions: U64_MAX }) },
    ];
    for (const patch of invalid)
        await assert.rejects(() => runCampaign(campaign(patch), { cancellation: () => true }, forbidden, forbidden));
    await assert.rejects(() => runCampaign(campaign({ workers: 2 }), {}, forbidden, forbidden), /worker|Node/i);
    for (const maxDurationMs of [-1, NaN, Infinity])
        await assert.rejects(() => runCampaign(campaign(), { maxDurationMs }, forbidden, forbidden));
    assert.equal(calls, 0);
});

test('guided validation precedes campaign validation and all callback admission', async () => {
    let calls = 0;
    const forbidden = () => { calls++; };
    for (const field of ['maxCorpusEntries', 'maxCorpusInputs', 'maxFeatures', 'maxFeaturesPerObservation']) {
        await assert.rejects(() => runGuidedCampaign(campaign({ jobs: 0 }), {},
            { ...guidance, [field]: 0 }, forbidden, forbidden, forbidden), /guidance/);
        await assert.rejects(() => runNodeGuidedCampaign(campaign({ workers: 2, jobs: 0 }), {},
            { ...guidance, [field]: 0 }, fixture(), forbidden), /guidance/);
    }
    assert.equal(calls, 0);
});

test('wide cases*steps is capped before campaign aggregate overflow admission', async () => {
    const report = await runCampaign(campaign({ jobs: 2, fuzz: fuzzConfig({
        cases: Number.MAX_SAFE_INTEGER, maxSteps: Number.MAX_SAFE_INTEGER, maxTransitions: 1n,
    }) }), { cancellation: () => true }, () => { throw new Error('must not start'); }, () => {});
    assertAccounting(report, { started: 0, finished: 0, accountingComplete: true, termination: { kind: 'Cancelled' } });
});

test('pre-cancellation and zero duration do no job work; cancellation wins their tie', async () => {
    const token = new CampaignCancellation();
    assert.equal(token.isCancelled(), false);
    token.cancel(); token.cancel();
    assert.equal(token.isCancelled(), true);
    let calls = 0;
    const forbidden = () => { calls++; throw new Error('preflight callback'); };
    for (const [limits, kind] of [
        [{ cancellation: token }, 'Cancelled'], [{ cancellation: () => true }, 'Cancelled'],
        [{ maxDurationMs: 0 }, 'Deadline'], [{ maxDurationMs: 0, cancellation: token }, 'Cancelled'],
    ]) {
        const report = await runGuidedCampaign(campaign(), limits, guidance, forbidden, forbidden, forbidden);
        assertAccounting(report, {
            requested: 4, started: 0, finished: 0, delivered: 0, notStarted: 4,
            failures: 0, errors: 0, reportedCases: 0n, reportedTransitions: 0n,
            reportedSkippedChecks: 0n, accountingComplete: true, termination: { kind },
        });
    }
    assert.equal(calls, 0);
});

test('each ordinary/guided job equals an independent public fuzz call and range slices replay', async () => {
    for (const guided of [false, true]) {
        const config = campaign({ firstJob: 7n, jobs: 5 });
        const jobs = [];
        const factory = id => createModel(id, { failAt: guided ? undefined : 3, skipped: true });
        const fb = (id, m) => createFeedback(id, m);
        const report = guided
            ? await runGuidedCampaign(config, {}, guidance, factory, fb, j => { jobs.push(j); })
            : await runCampaign(config, {}, factory, j => { jobs.push(j); });
        assert.deepEqual(jobs.map(j => j.jobId), [7n, 8n, 9n, 10n, 11n]);
        for (const job of jobs) {
            const expected = referenceJob(config, job.jobId, guided, { failAt: guided ? undefined : 3, skipped: true });
            assert.deepEqual(job, expected);
            // Private slots are not compared by deepEqual: explicitly compare contents.
            assert.deepEqual(inputValues(job), inputValues(expected));
        }
        assertAccounting(report, { ...expectedSums(jobs), started: 5, finished: 5, delivered: 5,
            errors: 0, failures: guided ? 0 : 5, accountingComplete: true, termination: { kind: 'JobsCompleted' } });
        const slice = [];
        const range = { ...config, firstJob: 9n, jobs: 2 };
        if (guided) await runGuidedCampaign(range, {}, guidance, factory, fb, j => { slice.push(j); });
        else await runCampaign(range, {}, factory, j => { slice.push(j); });
        assert.deepEqual(slice, jobs.slice(2, 4));
        assert.deepEqual(slice.map(inputValues), jobs.slice(2, 4).map(inputValues));
    }
    for (const guided of [false, true]) {
        const maximum = [];
        const config = campaign({ firstJob: U64_MAX, jobs: 1 });
        const data = { failAt: guided ? undefined : 3 };
        const factory = id => createModel(id, data);
        if (guided) await runGuidedCampaign(config, {}, guidance, factory, createFeedback, j => { maximum.push(j); });
        else await runCampaign(config, {}, factory, j => { maximum.push(j); });
        const expected = [referenceJob(config, U64_MAX, guided, data)];
        assert.deepEqual(maximum, expected);
        assertTransferredInputs(maximum[0]);
        assert.deepEqual(maximum.map(inputValues), expected.map(inputValues));
    }
});

test('zero fuzz budgets preserve initial work and final-step cap boundaries', async () => {
    for (const [patch, termination, cases, transitions, initials] of [
        [{ cases: 0 }, 'cases_completed', 0, 0n, 0],
        [{ cases: 3, maxSteps: 0, maxTransitions: 0n }, 'cases_completed', 3, 0n, 3],
        [{ cases: 3, maxSteps: 1, maxTransitions: 0n }, 'transition_limit', 1, 0n, 1],
        [{ cases: 1, maxSteps: 2, maxTransitions: 2n }, 'cases_completed', 1, 2n, 1],
        [{ cases: 2, maxSteps: 2, maxTransitions: 2n }, 'transition_limit', 2, 2n, 2],
    ]) {
        let calls = 0;
        const jobs = [];
        const report = await runCampaign(campaign({ jobs: 1, fuzz: fuzzConfig(patch) }), {},
            () => model({ initialState() { calls++; return 0; } }), j => { jobs.push(j); });
        assert.equal(calls, initials);
        assert.equal(completed(jobs[0]).termination, termination);
        assertAccounting(report, { reportedCases: BigInt(cases), reportedTransitions: transitions, termination: { kind: 'JobsCompleted' } });
    }
    const jobs = [];
    await runCampaign(campaign({ jobs: 1 }), {}, () => model({ generate: () => NO_INPUT }), j => { jobs.push(j); });
    assert.equal(completed(jobs[0]).cases, 3);
    assert.equal(completed(jobs[0]).transitions, 0n);
});

test('caller factories and feedback are fresh and sink promises are acknowledged serially', async () => {
    const events = [], models = new Set(), feedbacks = new Set();
    let inSink = false;
    const report = await runGuidedCampaign(campaign({ jobs: 3 }), {}, guidance, id => {
        assert.equal(inSink, false);
        const m = model({ metadata() { events.push(`metadata:${id}`); return metadata(`job:${id}`, String(threadId)); } });
        models.add(m); events.push(`factory:${id}`); return m;
    }, (id, m) => {
        assert.ok(models.has(m));
        const fb = feedback(); feedbacks.add(fb); events.push(`feedback:${id}`); return fb;
    }, async job => {
        assert.equal(inSink, false); inSink = true;
        events.push(`sink:${job.jobId}`); await delay(1);
        inSink = false; events.push(`ack:${job.jobId}`);
    });
    assert.equal(models.size, 3); assert.equal(feedbacks.size, 3);
    assert.deepEqual(events, [0, 1, 2].flatMap(i => [`factory:${i}`, `metadata:${i}`, `feedback:${i}`, `sink:${i}`, `ack:${i}`]));
    assertAccounting(report, { delivered: 3, accountingComplete: true, termination: { kind: 'JobsCompleted' } });
});

test('model-factory errors, metadata throws and normalized runner errors retain distinct accounting', async () => {
    for (const [stage, error, kind, hasMetadata, complete] of [
        ['factory', new ModelError('factory failed'), 'FactoryError', false, true],
        ['factory', new Error('factory panicked'), 'Panicked', false, false],
        ['metadata', new ModelError('metadata failed'), 'Panicked', false, false],
        ['metadata', new Error('metadata panicked'), 'Panicked', false, false],
        ['initialState', new Error('initial failed'), 'ModelError', true, false],
        ['generate', new ModelError('generation failed'), 'ModelError', true, false],
        ['step', new Error('step failed'), 'ModelError', true, false],
    ]) {
        const jobs = [];
        const report = await runCampaign(campaign({ jobs: 3, stopOnFailure: true }), {}, () => {
            if (stage === 'factory') throw error;
            return model({ [stage]() { throw error; } });
        }, j => { jobs.push(j); });
        assert.equal(jobs.length, 3, 'job errors do not activate stopOnFailure');
        for (const job of jobs) {
            assert.equal(job.outcome.kind, kind);
            assert.equal(job.metadata !== null, hasMetadata);
            if (kind !== 'Panicked') assert.ok(job.outcome.error instanceof ModelError);
        }
        assertAccounting(report, { started: 3, finished: 3, delivered: 3, errors: 3, failures: 0,
            reportedCases: 0n, reportedTransitions: 0n, accountingComplete: complete, termination: { kind: 'JobsCompleted' } });
    }
});

test('feedback-factory errors and normalized feedback errors keep model metadata', async () => {
    for (const [factoryError, callbackError, kind] of [
        [new ModelError('feedback factory failed'), null, 'ModelError'],
        [new Error('feedback factory panicked'), null, 'Panicked'],
        [null, new Error('feedback callback failed'), 'ModelError'],
    ]) {
        const jobs = [];
        const report = await runGuidedCampaign(campaign({ jobs: 2, stopOnFailure: true }), {}, guidance,
            () => model(), () => {
                if (factoryError) throw factoryError;
                return feedback({ transitionFeatures() { throw callbackError; } });
            }, j => { jobs.push(j); });
        for (const job of jobs) {
            assert.equal(job.outcome.kind, kind); assert.equal(job.metadata.name, 'campaign-unit');
        }
        assertAccounting(report, { started: 2, errors: 2, failures: 0, accountingComplete: false,
            reportedCases: 0n, reportedTransitions: 0n, termination: { kind: 'JobsCompleted' } });
    }
});

test('factory cancellation still runs model metadata, feedback factory and feedback metadata', async () => {
    const events = [], token = new CampaignCancellation(), jobs = [];
    const report = await runGuidedCampaign(campaign({ jobs: 3 }), { cancellation: token }, guidance, () => {
        events.push('factory'); token.cancel();
        return model({ metadata() { events.push('metadata'); return metadata('cancelled'); },
            initialState() { events.push('initial'); return 0; } });
    }, () => {
        events.push('feedbackFactory');
        return feedback({ metadata() { events.push('feedbackMetadata'); return { name: 'f', version: 1, build: 'v1' }; } });
    }, j => { events.push('sink'); jobs.push(j); });
    assert.deepEqual(events, ['factory', 'metadata', 'feedbackFactory', 'feedbackMetadata', 'sink']);
    assert.equal(completed(jobs[0]).termination, 'cancelled');
    assertAccounting(report, { started: 1, finished: 1, delivered: 1, notStarted: 2, accountingComplete: true,
        reportedCases: 0n, termination: { kind: 'Cancelled' } });
});

test('deadline includes factory and feedback construction; no callback is preempted', async () => {
    const descriptor = Object.getOwnPropertyDescriptor(globalThis, 'performance');
    let time = 0;
    try {
        Object.defineProperty(globalThis, 'performance', { configurable: true, value: { timeOrigin: 0, now: () => time } });
        for (const delayed of ['factory', 'feedbackFactory']) {
            time = 0;
            const events = [], jobs = [];
            const report = await runGuidedCampaign(campaign({ jobs: 2 }), { maxDurationMs: 5 }, guidance, () => {
                events.push('factory'); if (delayed === 'factory') time = 6;
                return model({ metadata() { events.push('metadata'); return metadata('deadline'); } });
            }, () => {
                events.push('feedbackFactory'); if (delayed === 'feedbackFactory') time = 6;
                return feedback({ metadata() { events.push('feedbackMetadata'); return { name: 'f', version: 1, build: 'v1' }; } });
            }, j => { jobs.push(j); });
            assert.deepEqual(events, ['factory', 'metadata', 'feedbackFactory', 'feedbackMetadata']);
            assert.equal(completed(jobs[0]).termination, 'deadline');
            assertAccounting(report, { started: 1, finished: 1, delivered: 1, reportedCases: 0n, termination: { kind: 'Deadline' } });
        }
    } finally { Object.defineProperty(globalThis, 'performance', descriptor); }
});

test('last-sink cancellation is inert, while a remaining job claim observes it', async () => {
    for (const jobs of [1, 3]) {
        const token = new CampaignCancellation();
        const report = await runCampaign(campaign({ jobs }), { cancellation: token }, () => model(), () => { token.cancel(); });
        assertAccounting(report, { started: 1, finished: 1, delivered: 1, accountingComplete: true,
            termination: { kind: jobs === 1 ? 'JobsCompleted' : 'Cancelled' } });
    }
    const token = new CampaignCancellation();
    const report = await runCampaign(campaign({ jobs: 3, stopOnFailure: true }), { cancellation: token },
        () => model({ checkState: () => [check('bad', 'failed', 'failure')] }), () => { token.cancel(); });
    assertAccounting(report, { started: 1, failures: 1, delivered: 1, termination: { kind: 'FailureFound' } });
});

test('sink throws and async rejections are CallbackError, never delivered or counted as job errors', async () => {
    for (const asynchronous of [false, true]) for (const error of [new ModelError('sink primary'), new Error('sink panic')]) {
        let attempts = 0;
        const sink = asynchronous
            ? async () => { attempts++; await delay(1); throw error; }
            : () => { attempts++; throw error; };
        const report = await runCampaign(campaign(), {}, () => model(), sink);
        assert.equal(attempts, 1);
        assert.equal(report.termination.kind, 'CallbackError');
        assert.ok(report.termination.error instanceof ModelError);
        assert.match(report.termination.error.message, /sink primary|sink panic/);
        assertAccounting(report, { started: 1, finished: 1, delivered: 0, errors: 0, accountingComplete: true,
            reportedTransitions: 12n, reportedCases: 3n });
    }
});

test('admitted configuration and metadata are detached from subsequent caller mutation', async () => {
    const config = campaign({ jobs: 2 }), original = { ...config.fuzz };
    const identity = metadata('snapshot', 'v1'), jobs = [];
    const report = await runCampaign(config, {}, id => {
        config.jobs = 0; config.fuzz.cases = 0; config.fuzz.maxSteps = 0;
        return model({ metadata: () => identity });
    }, job => {
        jobs.push(job);
        if (job.jobId === 0n) identity.name = 'later identity';
    });
    assertAccounting(report, { requested: 2, started: 2, finished: 2, delivered: 2, reportedTransitions: 24n });
    for (const job of jobs) assert.deepEqual(job.config, { ...original, seed: jobSeed(37n, job.jobId) });
    assert.equal(jobs[0].metadata.name, 'snapshot');
    assert.equal(jobs[1].metadata.name, 'later identity');
});

test('Node validates before workers and rejects function cancellation on multiworker calls', async () => {
    const view = shared();
    let sinks = 0;
    for (const patch of [{ jobs: 0 }, { workers: 257 }, { resultBuffer: 1025 }, { firstJob: U64_MAX, jobs: 2 }]) {
        await assert.rejects(() => runNodeCampaign(campaign({ workers: 2, ...patch }), {},
            fixture({ shared: view.buffer }), () => { sinks++; }));
    }
    await assert.rejects(() => runNodeCampaign(campaign({ workers: 2 }), { cancellation: () => false },
        fixture({ shared: view.buffer }), () => { sinks++; }), /cancellation|token|function/i);
    assert.equal(Atomics.load(view, slots.started), 0);
    assert.equal(sinks, 0);
    const token = new CampaignCancellation(); token.cancel();
    assert.ok(token.sharedBuffer instanceof SharedArrayBuffer);
    const report = await runNodeCampaign(campaign({ workers: 2 }), { cancellation: token },
        fixture({ shared: view.buffer }), () => { sinks++; });
    assertAccounting(report, { started: 0, finished: 0, delivered: 0, termination: { kind: 'Cancelled' } });
    assert.equal(Atomics.load(view, slots.started), 0);
    assert.equal(sinks, 0);
});

test('Node workers1 uses the caller; configured workers>1/jobs1 still spawns', { timeout: 30_000 }, async () => {
    for (const workers of [1, 4]) {
        const jobs = [];
        const report = await runNodeCampaign(campaign({ jobs: 1, workers }), {},
            { ...fixture({ exposeHost: true }), module: workers === 1 ? workerURL.href : workerURL },
            job => { assert.equal(threadId, 0); jobs.push(job); });
        assert.equal(jobs.length, 1);
        completed(jobs[0]);
        const host = JSON.parse(jobs[0].metadata.build);
        assert.equal(host.isMainThread, workers === 1);
        if (workers === 1) assert.equal(host.threadId, threadId);
        else assert.ok(host.threadId > 0);
        assertAccounting(report, { started: 1, finished: 1, delivered: 1, errors: 0, termination: { kind: 'JobsCompleted' } });
    }
});

test('shared barriers prove simultaneous workers and fresh worker-local models', { timeout: 30_000 }, async () => {
    const view = shared(), token = new CampaignCancellation(), jobs = [];
    try {
        const report = await runNodeCampaign(campaign({ workers: 8, jobs: 2 }), { cancellation: token },
            fixture({ shared: view.buffer, mode: 'overlap', participants: 2, exposeHost: true }),
            job => { jobs.push(job); });
        assert.equal(Atomics.load(view, slots.peak), 2, 'both model lifetimes must overlap');
        assert.equal(Atomics.load(view, slots.started), 2);
        assert.equal(Atomics.load(view, slots.disposed), 2);
        const hosts = jobs.map(j => { completed(j); return JSON.parse(j.metadata.build); });
        assert.equal(new Set(hosts.map(h => h.threadId)).size, 2);
        assert.ok(hosts.every(h => !h.isMainThread));
        assertAccounting(report, { started: 2, finished: 2, delivered: 2, errors: 0, termination: { kind: 'JobsCompleted' } });
    } finally {
        token.cancel(); release(view);
        Atomics.store(view, slots.arrived, 2); Atomics.notify(view, slots.arrived);
    }
    const many = [];
    await runNodeCampaign(campaign({ workers: 2, jobs: 7 }), {}, fixture({ exposeHost: true }), j => { many.push(j); });
    const identities = many.map(j => {
        completed(j); const h = JSON.parse(j.metadata.build); return `${h.threadId}:${h.instance}`;
    });
    assert.equal(identities.length, 7);
    assert.equal(new Set(identities).size, 7, 'a worker must create a new model for every claimed job');
});

test('a held earlier job is delivered after its later sibling, without sorting', { timeout: 20_000 }, async () => {
    const view = shared(), token = new CampaignCancellation(), jobs = [];
    try {
        const report = await runNodeCampaign(campaign({ workers: 2, jobs: 2, firstJob: 40n, resultBuffer: 0 }),
            { cancellation: token }, fixture({ shared: view.buffer, mode: 'hold-first', firstJob: 40n }), job => {
                try {
                    if (jobs.length === 0) assert.equal(job.jobId, 41n, 'sink must not wait for lower job IDs');
                    jobs.push(job);
                } finally { release(view); }
            });
        assert.deepEqual(jobs.map(j => j.jobId), [41n, 40n]);
        assertAccounting(report, { delivered: 2, errors: 0, termination: { kind: 'JobsCompleted' } });
    } finally { token.cancel(); release(view); }
});

for (const resultBuffer of [0, 1, 2]) {
    test(`synchronous blocked sink permits and bounds worker progress with buffer ${resultBuffer}`, { timeout: 25_000 }, async () => {
        const workers = 2, bound = resultBuffer + workers + 1;
        const view = shared(), token = new CampaignCancellation(), jobs = [];
        const probe = new BigInt64Array(new SharedArrayBuffer(TRANSPORT_PROBE_WORDS * 8));
        const descriptor = fixture({ shared: view.buffer });
        attachCampaignTransportProbe(descriptor, probe);
        try {
            const report = await runNodeCampaign(campaign({ workers, resultBuffer, jobs: bound + 5 }),
                { cancellation: token }, descriptor, job => {
                    if (jobs.length === 0) {
                        // Atomics.wait blocks the caller's JS event loop. Reaching
                        // this bound requires independent coordinator progress and
                        // receipt acknowledgement before this sink returns.
                        const transport = waitForTransport(probe, snapshot => snapshot.blocked === BigInt(workers)
                            && snapshot.queued === BigInt(resultBuffer) && snapshot.delivering === 1n);
                        // Each worker's real send was processed and withheld admission.
                        // There are no remaining worker-side Complete/send operations to race.
                        assert.equal(transport.started, BigInt(bound));
                        assert.equal(transport.completed, BigInt(bound));
                        assert.equal(transport.sends, BigInt(bound));
                        assert.equal(transport.accepted, BigInt(resultBuffer + 1));
                        assert.equal(transport.received, 1n);
                        assert.equal(transport.workers, BigInt(workers));
                        assert.equal(Atomics.load(view, slots.started), bound);
                        assert.equal(Atomics.load(view, slots.disposed), bound);
                    }
                    jobs.push(job);
                });
            assert.equal(jobs.length, bound + 5);
            assert.equal(new Set(jobs.map(j => j.jobId)).size, jobs.length, 'sink sees each job exactly once');
            assertAccounting(report, { started: bound + 5, finished: bound + 5, delivered: bound + 5,
                errors: 0, accountingComplete: true, termination: { kind: 'JobsCompleted' } });
        } finally { token.cancel(); release(view); }
    });
}

test('ordinary and guided Node jobs match caller jobs, including application input reconstruction', { timeout: 45_000 }, async () => {
    for (const guided of [false, true]) {
        const config = campaign({ firstJob: 11n, jobs: 4 });
        const data = { failAt: guided ? undefined : 3, skipped: true };
        const expected = Array.from({ length: config.jobs }, (_, i) => referenceJob(config, config.firstJob + BigInt(i), guided, data));
        for (const workers of [1, 2, 8]) {
            const jobs = [];
            const report = guided
                ? await runNodeGuidedCampaign({ ...config, workers }, {}, guidance, fixture(data), j => { jobs.push(j); })
                : await runNodeCampaign({ ...config, workers }, {}, fixture(data), j => { jobs.push(j); });
            const actual = sorted(jobs);
            assert.deepEqual(actual, expected);
            for (let i = 0; i < actual.length; i++) {
                assertTransferredInputs(actual[i]);
                assert.deepEqual(inputValues(actual[i]), inputValues(expected[i]));
            }
            assertAccounting(report, { ...expectedSums(jobs), started: 4, finished: 4, delivered: 4,
                failures: guided ? 0 : 4, errors: 0, accountingComplete: true, termination: { kind: 'JobsCompleted' } });
        }
    }
});

test('Node async sinks never overlap and aggregate counters ignore sink mutation', { timeout: 20_000 }, async () => {
    let active = 0, calls = 0;
    const report = await runNodeCampaign(campaign({ workers: 3, jobs: 6 }), {}, fixture(), async job => {
        assert.equal(active++, 0);
        calls++;
        const r = completed(job);
        r.transitions = U64_MAX; r.cases = 999; r.skipped_checks = U64_MAX;
        await delay(2);
        active--;
    });
    assert.equal(calls, 6); assert.equal(active, 0);
    assertAccounting(report, { delivered: 6, reportedTransitions: 72n, reportedCases: 18n,
        reportedSkippedChecks: 0n, termination: { kind: 'JobsCompleted' } });
});

test('stopOnFailure leaves already claimed siblings running and suppresses further claims', { timeout: 20_000 }, async () => {
    const view = shared(), token = new CampaignCancellation(), jobs = [];
    try {
        const report = await runNodeCampaign(campaign({ workers: 2, jobs: 6, stopOnFailure: true }),
            { cancellation: token }, fixture({ shared: view.buffer, mode: 'hold-sibling', failAt: 0, failJob: 0n }), job => {
                try { jobs.push(job); } finally { release(view); }
            });
        assert.deepEqual(jobs.map(j => j.jobId), [0n, 1n]);
        assert.equal(completed(jobs[0]).termination, 'failure_found');
        assert.equal(completed(jobs[1]).termination, 'cases_completed', 'property stop must not cancel an active sibling');
        assertAccounting(report, { started: 2, finished: 2, delivered: 2, notStarted: 4, failures: 1,
            errors: 0, accountingComplete: true, termination: { kind: 'FailureFound' } });
    } finally { token.cancel(); release(view); }
});

test('accepted active-job cancellation upgrades a prior property stop', { timeout: 20_000 }, async () => {
    const view = shared(), token = new CampaignCancellation(), jobs = [];
    try {
        const report = await runNodeCampaign(campaign({ workers: 2, jobs: 6, stopOnFailure: true }),
            { cancellation: token }, fixture({ shared: view.buffer, mode: 'hold-sibling', failAt: 0, failJob: 0n }), job => {
                try {
                    jobs.push(job);
                    if (job.jobId === 0n) token.cancel();
                } finally { release(view); }
            });
        assert.equal(completed(jobs[0]).termination, 'failure_found');
        assert.equal(completed(jobs[1]).termination, 'cancelled');
        assertAccounting(report, { started: 2, finished: 2, delivered: 2, failures: 1,
            accountingComplete: true, termination: { kind: 'Cancelled' } });
    } finally { token.cancel(); release(view); }
});

test('Complete prevents later claims while its failed job is still blocked inside input encoding', { timeout: 25_000 }, async () => {
    const view = shared(), token = new CampaignCancellation(), jobs = [];
    const probe = new BigInt64Array(new SharedArrayBuffer(TRANSPORT_PROBE_WORDS * 8));
    const descriptor = fixture({ shared: view.buffer, mode: 'complete-before-send', failAt: 2, failJob: 0n });
    attachCampaignTransportProbe(descriptor, probe);
    try {
        const report = await runNodeCampaign(campaign({ workers: 2, jobs: 6, resultBuffer: 0, stopOnFailure: true }),
            { cancellation: token }, descriptor, job => {
                try {
                    if (jobs.length === 0) {
                        assert.equal(job.jobId, 1n, 'failed job has completed but cannot send yet');
                        const transport = waitForTransport(probe, snapshot => snapshot.denied === 1n);
                        assert.equal(transport.started, 2n);
                        assert.equal(transport.completed, 2n);
                        assert.equal(transport.sends, 1n);
                        assert.equal(transport.accepted, 1n);
                        assert.equal(Atomics.load(view, slots.encoding), 1);
                        assert.equal(Atomics.load(view, slots.started), 2);
                    }
                    jobs.push(job);
                } finally { release(view); }
            });
        assert.deepEqual(jobs.map(job => job.jobId), [1n, 0n]);
        assertAccounting(report, { started: 2, finished: 2, delivered: 2, notStarted: 4,
            failures: 1, errors: 0, accountingComplete: true, termination: { kind: 'FailureFound' } });
    } finally { token.cancel(); release(view); }
});

test('worker factory cancellation shares the token and preserves metadata/feedback ordering', { timeout: 20_000 }, async () => {
    const view = shared(), token = new CampaignCancellation(), jobs = [];
    const report = await runNodeGuidedCampaign(campaign({ workers: 4, jobs: 1 }), { cancellation: token }, guidance,
        fixture({ shared: view.buffer, cancelInFactory: true, expectFeedbackDispose: true }), j => { jobs.push(j); });
    assert.equal(token.isCancelled(), true);
    assert.equal(Atomics.load(view, slots.started), 1);
    assert.equal(Atomics.load(view, slots.metadata), 1);
    assert.equal(Atomics.load(view, slots.feedbackCreated), 1);
    assert.equal(Atomics.load(view, slots.initialized), 0);
    assert.equal(Atomics.load(view, slots.feedbackDisposed), 1);
    assert.equal(Atomics.load(view, slots.disposed), 1);
    assert.equal(completed(jobs[0]).termination, 'cancelled');
    assertAccounting(report, { delivered: 1, errors: 0, reportedCases: 0n, accountingComplete: true, termination: { kind: 'Cancelled' } });
});

test('Node caller composes its context token with a false cancellation method and retains its receiver', async () => {
    const view = shared(), jobs = [];
    let polls = 0;
    const limits = { maxDurationMs: 8_000, cancelled: false, cancellation() {
        assert.equal(this, limits); polls++; return this.cancelled;
    } };
    const report = await runNodeGuidedCampaign(campaign({ workers: 1, jobs: 3 }), limits, guidance,
        fixture({ shared: view.buffer, cancelInFactory: true, expectFeedbackDispose: true }), j => { jobs.push(j); });
    assert.ok(polls > 0);
    assert.equal(Atomics.load(view, slots.started), 1);
    assert.equal(Atomics.load(view, slots.metadata), 1);
    assert.equal(Atomics.load(view, slots.feedbackCreated), 1);
    assert.equal(Atomics.load(view, slots.initialized), 0);
    assert.equal(Atomics.load(view, slots.feedbackDisposed), 1);
    assert.equal(Atomics.load(view, slots.disposed), 1);
    assert.equal(completed(jobs[0]).termination, 'cancelled');
    assertAccounting(report, { started: 1, finished: 1, delivered: 1, notStarted: 2,
        errors: 0, reportedCases: 0n, accountingComplete: true, termination: { kind: 'Cancelled' } });
});

test('uncloneable module initialization fails worker construction with no lost job work', async () => {
    let sinks = 0;
    const report = await runNodeCampaign(campaign({ workers: 2 }), {}, fixture({ uncloneable: () => {} }), () => { sinks++; });
    assert.equal(sinks, 0);
    assert.equal(report.termination.kind, 'WorkerError');
    assert.match(report.termination.error.message, /unable to start campaign worker/);
    assertAccounting(report, { started: 0, finished: 0, delivered: 0, notStarted: 4, errors: 0,
        reportedCases: 0n, reportedTransitions: 0n, accountingComplete: true });
});

test('Node error transfer and disposal preserve outcome type, metadata and missing-work accounting', { timeout: 45_000 }, async () => {
    for (const [errorStage, errorKind, guided, kind, hasMetadata, complete] of [
        ['factory', 'model', false, 'FactoryError', false, true],
        ['factory', 'panic', false, 'Panicked', false, false],
        ['metadata', 'model', false, 'Panicked', false, false],
        ['generate', 'panic', false, 'ModelError', true, false],
        ['feedbackFactory', 'model', true, 'ModelError', true, false],
        ['feedbackFactory', 'panic', true, 'Panicked', true, false],
        ['feedback', 'panic', true, 'ModelError', true, false],
        ['disposeModel', 'model', false, 'Panicked', true, false],
        ['disposeFeedback', 'model', true, 'Panicked', true, false],
    ]) {
        const view = shared(), jobs = [];
        const config = campaign({ jobs: 2, workers: 2, stopOnFailure: true });
        const module = fixture({ shared: view.buffer, errorStage, errorKind, errorJob: 0n,
            expectFeedbackDispose: guided && errorStage !== 'feedbackFactory' });
        const report = guided
            ? await runNodeGuidedCampaign(config, {}, guidance, module, j => { jobs.push(j); })
            : await runNodeCampaign(config, {}, module, j => { jobs.push(j); });
        const [failed, healthy] = sorted(jobs);
        assert.equal(failed.outcome.kind, kind, errorStage);
        assert.equal(failed.metadata !== null, hasMetadata, errorStage);
        if (kind !== 'Panicked') assert.ok(failed.outcome.error instanceof ModelError);
        completed(healthy);
        assert.equal(Atomics.load(view, slots.disposed), errorStage === 'factory' ? 1 : 2);
        assertAccounting(report, { ...expectedSums(jobs), started: 2, finished: 2, delivered: 2,
            errors: 1, failures: 0, accountingComplete: complete, termination: { kind: 'JobsCompleted' } });
    }
});

test('sink rejection drains claimed work once without extra sink calls', { timeout: 25_000 }, async () => {
    for (const asynchronous of [false, true]) {
        const view = shared(), token = new CampaignCancellation();
        let attempts = 0;
        const reject = () => {
            attempts++;
            try { throw new ModelError('first sink rejection'); } finally { release(view); }
        };
        try {
            const report = await runNodeCampaign(campaign({ workers: 2, jobs: 2, resultBuffer: 0 }),
                { cancellation: token }, fixture({ shared: view.buffer, mode: 'hold-sibling' }),
                asynchronous ? async () => { await delay(1); reject(); } : reject);
            assert.equal(attempts, 1);
            assert.equal(Atomics.load(view, slots.disposed), 2, 'active sibling completes before return');
            assert.equal(report.termination.kind, 'CallbackError');
            assert.match(report.termination.error.message, /first sink rejection/);
            assertAccounting(report, { started: 2, finished: 2, delivered: 0, errors: 0,
                reportedTransitions: 24n, reportedCases: 6n, accountingComplete: true });
        } finally { token.cancel(); release(view); }
    }
});

test('real worker exit reports lost work while surviving worker reports still reach the sink', { timeout: 20_000 }, async () => {
    const view = shared(), token = new CampaignCancellation(), jobs = [];
    try {
        const report = await runNodeCampaign(campaign({ workers: 2, jobs: 2 }), { cancellation: token },
            fixture({ shared: view.buffer, mode: 'worker-exit' }), j => { jobs.push(j); });
        assert.deepEqual(jobs.map(j => j.jobId), [1n], 'no fabricated job report for the exited worker');
        completed(jobs[0]);
        assert.equal(report.termination.kind, 'WorkerError');
        assert.ok(report.termination.error instanceof ModelError);
        assertAccounting(report, { started: 2, finished: 1, delivered: 1, errors: 0,
            reportedTransitions: 12n, reportedCases: 3n, accountingComplete: false });
    } finally { token.cancel(); release(view); }
});

test('first sink error remains authoritative over a subsequently exiting worker', { timeout: 20_000 }, async () => {
    const view = shared(), token = new CampaignCancellation();
    let attempts = 0;
    try {
        const report = await runNodeCampaign(campaign({ workers: 2, jobs: 2 }), { cancellation: token },
            fixture({ shared: view.buffer, mode: 'exit-after-sink' }), () => {
                attempts++;
                try { throw new ModelError('authoritative sink error'); } finally { release(view); }
            });
        assert.equal(attempts, 1);
        assert.equal(report.termination.kind, 'CallbackError');
        assert.match(report.termination.error.message, /authoritative sink error/);
        assertAccounting(report, { started: 2, finished: 1, delivered: 0, errors: 0, accountingComplete: false });
    } finally { token.cancel(); release(view); }
});

test('caller teardown precedes delivery, preserves the first cleanup error and still disposes both objects', async () => {
    for (const failing of ['neither', 'model', 'both']) {
        const events = [], jobs = [];
        const report = await runGuidedCampaign(campaign({ jobs: 1, fuzz: fuzzConfig({ cases: 0 }) }), {}, guidance,
            () => model({ dispose() {
                events.push('disposeModel');
                if (failing !== 'neither') throw new ModelError('second cleanup error');
            } }), () => feedback({ dispose() {
                events.push('disposeFeedback');
                if (failing === 'both') throw new Error('first cleanup error');
            } }), job => { events.push('sink'); jobs.push(job); });
        assert.deepEqual(events, ['disposeFeedback', 'disposeModel', 'sink']);
        assert.equal(jobs[0].metadata.name, 'campaign-unit');
        if (failing === 'neither') completed(jobs[0]);
        else {
            assert.equal(jobs[0].outcome.kind, 'Panicked');
            assert.equal(jobs[0].outcome.message, failing === 'both' ? 'first cleanup error' : 'second cleanup error');
        }
        assertAccounting(report, { delivered: 1, errors: failing === 'neither' ? 0 : 1,
            accountingComplete: failing === 'neither', termination: { kind: 'JobsCompleted' } });
    }
});

test('a teardown error in a drained sibling cannot replace the primary sink error', { timeout: 20_000 }, async () => {
    const view = shared(), token = new CampaignCancellation();
    let attempts = 0;
    try {
        const report = await runNodeCampaign(campaign({ workers: 2, jobs: 2 }), { cancellation: token },
            fixture({ shared: view.buffer, mode: 'hold-sibling', errorStage: 'disposeModel', errorKind: 'model', errorJob: 1n }),
            () => {
                attempts++;
                try { throw new ModelError('sink before cleanup'); } finally { release(view); }
            });
        assert.equal(attempts, 1);
        assert.equal(Atomics.load(view, slots.disposed), 2);
        assert.equal(report.termination.kind, 'CallbackError');
        assert.match(report.termination.error.message, /sink before cleanup/);
        assertAccounting(report, { started: 2, finished: 2, delivered: 0, errors: 1,
            reportedTransitions: 12n, reportedCases: 3n, accountingComplete: false });
    } finally { token.cancel(); release(view); }
});

test('input encoding loss is WorkerError with no invented completed job', { timeout: 20_000 }, async () => {
    const jobs = [];
    const report = await runNodeCampaign(campaign({ workers: 4, jobs: 1 }), {},
        fixture({ failAt: 2, errorStage: 'encode' }), job => { jobs.push(job); });
    assert.deepEqual(jobs, []);
    assert.equal(report.termination.kind, 'WorkerError');
    assert.match(report.termination.error.message, /fixture encode failure/);
    assertAccounting(report, { started: 1, finished: 0, delivered: 0, errors: 0, failures: 0,
        reportedCases: 0n, reportedTransitions: 0n, accountingComplete: false });
});

test('decode failure is CallbackError before the user sink and drains further reports without decoding', { timeout: 20_000 }, async () => {
    const view = shared(), token = new CampaignCancellation();
    let decodeCalls = 0, sinks = 0;
    try {
        const report = await runNodeCampaign(campaign({ workers: 2, jobs: 2 }), { cancellation: token },
            { ...fixture({ shared: view.buffer, mode: 'overlap', participants: 2, failAt: 2 }),
                decodeInput() { decodeCalls++; throw new ModelError('input cannot be decoded'); } },
            () => { sinks++; });
        assert.equal(decodeCalls, 1); assert.equal(sinks, 0);
        assert.equal(report.termination.kind, 'CallbackError');
        assert.match(report.termination.error.message, /input cannot be decoded/);
        assertAccounting(report, { started: 2, finished: 2, delivered: 0, failures: 2, errors: 0,
            reportedTransitions: 4n, reportedCases: 2n, accountingComplete: true });
    } finally {
        token.cancel(); release(view);
        Atomics.store(view, slots.arrived, 2); Atomics.notify(view, slots.arrived);
    }
});

for (const host of ['caller', 'node']) {
    for (const mutation of ['wrong-id', 'throwing-getter']) {
        for (const rejects of [false, true]) {
            test(`${host} sink ${mutation} then ${rejects ? 'throw' : 'return'} acknowledges the original job identity`, { timeout: 20_000 }, async () => {
                const view = shared(), token = new CampaignCancellation(), attempted = [];
                const sink = job => {
                    attempted.push(job.jobId);
                    if (mutation === 'wrong-id') job.jobId = U64_MAX;
                    else Object.defineProperty(job, 'jobId', { get() { throw new Error('post-callback jobId access'); } });
                    if (rejects) throw new ModelError('mutation primary sink error');
                };
                try {
                    const config = campaign({ workers: host === 'caller' ? 1 : 2, jobs: 2 });
                    const report = host === 'caller'
                        ? await runCampaign(config, { cancellation: token }, () => model(), sink)
                        : await runNodeCampaign(config, { cancellation: token },
                            fixture({ shared: view.buffer, mode: 'overlap', participants: 2 }), sink);
                    assert.equal(attempted.length, rejects ? 1 : 2);
                    assert.equal(new Set(attempted).size, attempted.length);
                    if (rejects) {
                        assert.equal(report.termination.kind, 'CallbackError');
                        assert.match(report.termination.error.message, /mutation primary sink error/);
                    } else {
                        assert.deepEqual(new Set(attempted), new Set([0n, 1n]));
                        assert.equal(report.termination.kind, 'JobsCompleted');
                    }
                    const ran = host === 'caller' && rejects ? 1 : 2;
                    assertAccounting(report, { started: ran, finished: ran, delivered: rejects ? 0 : 2,
                        errors: 0, accountingComplete: true, reportedCases: BigInt(ran * 3), reportedTransitions: BigInt(ran * 12) });
                } finally {
                    token.cancel(); release(view);
                    Atomics.store(view, slots.arrived, 2); Atomics.notify(view, slots.arrived);
                }
            });
        }
    }
}

test('caller cancellation methods retain their original limits receiver', async () => {
    let factories = 0, sinks = 0;
    const limits = {
        cancelled: true,
        cancellation() { return this.cancelled; },
    };
    const report = await runCampaign(campaign(), limits, () => { factories++; return model(); }, () => { sinks++; });
    assert.equal(factories, 0); assert.equal(sinks, 0);
    assertAccounting(report, { started: 0, finished: 0, delivered: 0, accountingComplete: true, termination: { kind: 'Cancelled' } });
});

for (const host of ['caller', 'node']) {
    test(`${host} hostile sink Error.message getter is never invoked and cannot lose the aggregate`, { timeout: 20_000 }, async () => {
        const view = shared(), token = new CampaignCancellation();
        let getterCalls = 0, attempts = 0;
        const hostile = new ModelError('unreadable application error');
        Object.defineProperty(hostile, 'message', {
            get() { getterCalls++; throw new Error('hostile message getter was invoked'); },
        });
        const sink = () => { attempts++; throw hostile; };
        try {
            const config = campaign({ jobs: 2, workers: host === 'caller' ? 1 : 2 });
            const report = host === 'caller'
                ? await runCampaign(config, { cancellation: token }, () => model(), sink)
                : await runNodeCampaign(config, { cancellation: token },
                    fixture({ shared: view.buffer, mode: 'overlap', participants: 2 }), sink);
            assert.equal(attempts, 1); assert.equal(getterCalls, 0);
            assert.equal(report.termination.kind, 'CallbackError');
            assert.ok(report.termination.error instanceof ModelError);
            assert.equal(report.termination.error.message, 'non-string panic payload');
            const ran = host === 'caller' ? 1 : 2;
            assertAccounting(report, { started: ran, finished: ran, delivered: 0, errors: 0,
                reportedTransitions: BigInt(ran * 12), reportedCases: BigInt(ran * 3), accountingComplete: true });
        } finally {
            token.cancel(); release(view);
            Atomics.store(view, slots.arrived, 2); Atomics.notify(view, slots.arrived);
        }
    });
}

for (const host of ['caller', 'node-caller', 'node']) for (const payload of ['revoked', 'prototype-trap']) for (const asynchronous of [false, true]) {
    test(`${host} ${payload} sink ${asynchronous ? 'rejection' : 'throw'} is contained and remains authoritative during drain`, { timeout: 20_000 }, async () => {
        const view = shared(), token = new CampaignCancellation();
        const revoked = Proxy.revocable({}, {});
        revoked.revoke();
        const error = payload === 'revoked' ? revoked.proxy : new Proxy({}, {
            getPrototypeOf() { throw new Error('secondary prototype trap'); },
        });
        let calls = 0;
        const reject = () => { calls++; release(view); throw error; };
        const sink = asynchronous ? async () => { reject(); } : reject;
        try {
            const config = campaign({ workers: host === 'node' ? 2 : 1, jobs: 2 });
            const report = host === 'caller'
                ? await runCampaign(config, { cancellation: token }, () => model(), sink)
                : await runNodeCampaign(config, { cancellation: token }, fixture({
                    shared: view.buffer, ...(host === 'node' ? { mode: 'hold-sibling' } : {}),
                    errorStage: 'disposeModel', errorJob: 1n,
                }), sink);
            assert.equal(calls, 1);
            assert.equal(report.termination.kind, 'CallbackError');
            assert.equal(report.termination.error.message, 'campaign callback panicked: non-string panic payload');
            assertAccounting(report, { started: host === 'node' ? 2 : 1, finished: host === 'node' ? 2 : 1,
                delivered: 0, errors: host === 'node' ? 1 : 0, accountingComplete: host !== 'node' });
        } finally { token.cancel(); release(view); }
    });
}

test('feedback metadata panic retains metadata and unknown accounting', async () => {
    const jobs = [];
    const report = await runGuidedCampaign(campaign({jobs: 1}), {}, guidance,
        () => model(), () => feedback({ metadata() { throw new Error('metadata panic'); } }), j => { jobs.push(j); });
    assert.equal(jobs.length, 1);
    assert.equal(jobs[0].outcome.kind, 'Panicked');
    assert.equal(jobs[0].outcome.message, 'metadata panic');
    assert.equal(jobs[0].metadata.name, 'campaign-unit');
    assert.equal(report.accountingComplete, false);
});
