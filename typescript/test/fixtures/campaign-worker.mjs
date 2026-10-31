// A real worker module: no model/state/output/feature key is transferable.
// Only CampaignInput values in reports cross the explicit application codec.
import { isMainThread, threadId } from 'node:worker_threads';
import { accepted, check, metadata, ModelError } from '@jagalite/statelessness';

export const slots = Object.freeze({
    started: 0, active: 1, peak: 2, release: 3, disposed: 4,
    generated: 5, feedbackCreated: 6, initialized: 7, feedbackDisposed: 8,
    arrived: 9, metadata: 10, beforeExit: 11, encoding: 12,
});
export const SLOT_COUNT = 13;
const WAIT_MS = 8_000;
let nextInstance = 0;

export function waitForAtLeast(view, index, minimum, timeoutMs = WAIT_MS) {
    const end = performance.now() + timeoutMs;
    for (;;) {
        const value = Atomics.load(view, index);
        if (value >= minimum) return value;
        const remaining = end - performance.now();
        if (remaining <= 0) throw new Error(`campaign fixture barrier ${index}: ${value} < ${minimum}`);
        Atomics.wait(view, index, value, remaining);
    }
}

function increment(view, index) {
    if (!view) return;
    const value = Atomics.add(view, index, 1) + 1;
    Atomics.notify(view, index);
    return value;
}

function fail(data, stage, jobId) {
    if (data.errorStage !== stage || (data.errorJob !== undefined && BigInt(data.errorJob) !== jobId)) return;
    const message = `fixture ${stage} failure`;
    if (data.errorKind === 'model') throw new ModelError(message);
    throw new Error(message);
}

export class CampaignInput {
    #value;
    #wireFault;
    #encodeGate;
    constructor(value, wireFault = false, encodeGate) { this.#value = BigInt(value); this.#wireFault = wireFault; this.#encodeGate = encodeGate; }
    value() { return this.#value; }
    wireFault() { return this.#wireFault; }
    encodeGate() { return this.#encodeGate; }
}

class LocalState {
    #owner;
    #depth;
    constructor(owner, depth) { this.#owner = owner; this.#depth = depth; }
    depth(owner) {
        if (owner !== this.#owner) throw new Error('state escaped its model instance');
        return this.#depth;
    }
}

class LocalFeature {
    #owner;
    #depth;
    constructor(owner, depth) { this.#owner = owner; this.#depth = depth; }
    depth(owner) {
        if (owner !== this.#owner) throw new Error('feature escaped its feedback instance');
        return this.#depth;
    }
}

class CampaignModel {
    #owner = Symbol('worker-local model');
    #instance = ++nextInstance;
    #feedbackDisposed = false;
    constructor(jobId, data, view, context) {
        this.jobId = jobId;
        this.data = data;
        this.view = view;
        this.context = context;
    }
    metadata() {
        increment(this.view, slots.metadata);
        fail(this.data, 'metadata', this.jobId);
        const build = this.data.exposeHost
            ? JSON.stringify({ threadId, isMainThread, instance: this.#instance })
            : 'campaign-fixture-v1';
        return metadata(`campaign-fixture:${this.jobId}`, build);
    }
    initialState() {
        increment(this.view, slots.initialized);
        fail(this.data, 'initial', this.jobId);
        return new LocalState(this.#owner, 0);
    }
    depth(state) { return state.depth(this.#owner); }
    cloneState(state) { return new LocalState(this.#owner, this.depth(state)); }
    cloneInput(input) { return new CampaignInput(input.value(), input.wireFault(), input.encodeGate()); }
    cloneOutput(output) { return { local: output.local }; }
    equalInputs(a, b) { return a.value() === b.value(); }
    generate(state, rng) {
        this.depth(state);
        increment(this.view, slots.generated);
        fail(this.data, 'generate', this.jobId);
        const encodeGate = this.data.mode === 'complete-before-send' && this.jobId === 0n ? this.view : undefined;
        return new CampaignInput(rng.nextU64() & 255n, this.data.errorStage === 'encode', encodeGate);
    }
    isEnabled(state, input) { this.depth(state); return input.value() >= 0n; }
    step(state, input) {
        fail(this.data, 'step', this.jobId);
        input.value();
        // A function output deliberately cannot pass structuredClone.
        return accepted(new LocalState(this.#owner, this.depth(state) + 1), [{ local: () => this.#owner }]);
    }
    checkState(state) {
        fail(this.data, 'check', this.jobId);
        const failingJob = this.data.failJob === undefined || BigInt(this.data.failJob) === this.jobId;
        const failed = failingJob && this.depth(state) >= (this.data.failAt ?? Infinity);
        return [
            check('depth', failed ? 'failed' : 'passed', failed ? 'fixture depth failure' : ''),
            ...(this.data.skipped ? [check('fixture.skipped', 'skipped', 'fixture policy')] : []),
        ];
    }
    markFeedbackDisposed() { this.#feedbackDisposed = true; }
    dispose() {
        try {
            if (this.data.expectFeedbackDispose && !this.#feedbackDisposed)
                throw new Error('model disposal preceded feedback disposal');
            fail(this.data, 'disposeModel', this.jobId);
        } finally {
            increment(this.view, slots.disposed);
            if (this.view) Atomics.sub(this.view, slots.active, 1);
        }
    }
}

export function createModel(jobId, data = {}, context) {
    const view = data.shared ? new Int32Array(data.shared) : null;
    increment(view, slots.started);
    fail(data, 'factory', jobId);
    const active = increment(view, slots.active);
    if (view) {
        let old = Atomics.load(view, slots.peak);
        while (old < active) {
            const prior = Atomics.compareExchange(view, slots.peak, old, active);
            if (prior === old) break;
            old = prior;
        }
    }
    const relative = jobId - BigInt(data.firstJob ?? 0);
    if (data.mode === 'overlap') {
        increment(view, slots.arrived);
        waitForAtLeast(view, slots.arrived, data.participants);
    } else if (data.mode === 'hold-first' && relative === 0n) {
        waitForAtLeast(view, slots.release, 1);
    } else if (data.mode === 'hold-sibling') {
        if (relative === 0n) waitForAtLeast(view, slots.started, 2);
        else if (relative === 1n) waitForAtLeast(view, slots.release, 1);
    } else if (data.mode === 'complete-before-send') {
        if (relative === 0n) waitForAtLeast(view, slots.started, 2);
        else if (relative === 1n) waitForAtLeast(view, slots.encoding, 1);
    } else if (data.mode === 'worker-exit') {
        waitForAtLeast(view, slots.started, 2);
        if (relative === 0n) {
            if (isMainThread) throw new Error('exit fixture must run on a worker');
            increment(view, slots.beforeExit);
            process.exit(23);
        }
        waitForAtLeast(view, slots.beforeExit, 1);
    } else if (data.mode === 'exit-after-sink' && relative === 1n) {
        waitForAtLeast(view, slots.release, 1);
        if (isMainThread) throw new Error('exit fixture must run on a worker');
        process.exit(23);
    } else if (data.mode === 'exit-after-sink' && relative === 0n) {
        waitForAtLeast(view, slots.started, 2);
    }
    if (data.cancelInFactory) context.cancellation.cancel();
    return new CampaignModel(jobId, data, view, context);
}

class CampaignFeedback {
    #owner = Symbol('worker-local feature');
    #model;
    constructor(model) { this.#model = model; }
    metadata() {
        fail(this.#model.data, 'feedbackMetadata', this.#model.jobId);
        return { name: `campaign-depth:${this.#model.jobId}`, version: 1, build: 'campaign-fixture-v1' };
    }
    cloneFeature(key) { return new LocalFeature(this.#owner, key.depth(this.#owner)); }
    equalFeatures(a, b) { return a.depth(this.#owner) === b.depth(this.#owner); }
    hashFeature(key) { return key.depth(this.#owner) % 2; }
    initialFeatures(state, out) { out.push(new LocalFeature(this.#owner, this.#model.depth(state))); }
    transitionFeatures(_before, _input, after, out) {
        fail(this.#model.data, 'feedback', this.#model.jobId);
        out.push(new LocalFeature(this.#owner, this.#model.depth(after.state)));
    }
    dispose() {
        this.#model.markFeedbackDisposed();
        increment(this.#model.view, slots.feedbackDisposed);
        fail(this.#model.data, 'disposeFeedback', this.#model.jobId);
    }
}

export function createFeedback(jobId, model, data = {}) {
    if (model.jobId !== jobId) throw new Error('feedback received another job model');
    increment(model.view, slots.feedbackCreated);
    fail(data, 'feedbackFactory', jobId);
    return new CampaignFeedback(model);
}

export function encodeInput(input) {
    if (input.wireFault()) throw new ModelError('fixture encode failure');
    const gate = input.encodeGate();
    if (gate) { increment(gate, slots.encoding); waitForAtLeast(gate, slots.release, 1); }
    return { decimal: input.value().toString() };
}
export function decodeInput(wire) {
    if (!wire || typeof wire.decimal !== 'string' || !/^\d+$/.test(wire.decimal))
        throw new ModelError('invalid campaign fixture input wire');
    return new CampaignInput(wire.decimal);
}
export function disposeModel(model) { model.dispose(); }
export function disposeFeedback(feedback) { feedback.dispose(); }
