import { count64, countLike, arrayGrowth, type Count, type CountValue } from './counts.js';
/** Feature novelty guides generation; it never merges model states or replaces checks. */
import { ModelError, ConfigError, natural, scalar, disposition, type Model, type EnumerateModel, type Transition, type Rng } from './core.js';
import { automaticControlled, type AutoOptions, type GenerateModel, type FuzzConfig, type FuzzReport } from './search.js';
import { runFuzz, snapshotFuzzConfig, type FuzzDriver } from './fuzz_kernel.js';
import { runControl, addSkippedChecks, type RunLimits, type SearchObserver } from './search_support.js';
import { feedbackMetadataCall, guidedCall, synchronous, synchronousVoid } from './guided_sync.js';

export const GUIDANCE_ALGORITHM = 'feature-corpus-v2';
export interface FeedbackMetadata { readonly name: string; readonly version: number; readonly build: string }
export interface FeatureIdentity<K> {
    /** Must detach mutable values, preserve lawful equality, and not mutate its argument. */
    cloneFeature(feature: K): K;
    equalFeatures(left: K, right: K): boolean;
    /** Optional acceleration: equal keys must hash equally. Collisions use equalFeatures. */
    hashFeature?(feature: K): string | number | bigint;
}
export interface Feedback<S, I, O, K> extends FeatureIdentity<K> {
    metadata(): FeedbackMetadata;
    /** Omission means no initial emissions. */
    initialFeatures?(state: S, out: FeatureBuffer<K>): void;
    transitionFeatures(before: S, input: I, after: Transition<S, O>, out: FeatureBuffer<K>): void;
}

interface FeatureLease<K> {
    values: K[] | null;
    clone: ((key: K) => K) | null;
    maximum: bigint;
    active: boolean;
    pushing: boolean;
    failed: boolean;
    error?: unknown;
}
const leases = new WeakMap<FeatureBuffer<any>, FeatureLease<any>>();
const overrun = 'feedback exceeds per-observation feature limit';

/** Engine-issued append-only callback lease. Duplicates count toward the emission bound. */
export class FeatureBuffer<K> {
    constructor() { throw new ModelError('feature buffers are issued by guided feedback dispatch'); }
    push(feature: K): void {
        const lease = leases.get(this) as FeatureLease<K> | undefined;
        if (!lease?.active) throw new ModelError('feature buffer lease expired');
        if (lease.failed) throw lease.error;
        if (lease.pushing) {
            lease.failed = true;
            lease.error = new ModelError('feature buffer push is not reentrant');
            throw lease.error;
        }
        lease.pushing = true;
        try {
            if (BigInt(lease.values!.length) >= lease.maximum) throw new ModelError(overrun);
            const owned = lease.clone!(feature);
            if (lease.failed) throw lease.error;
            arrayGrowth(lease.values!.length);
            lease.values!.push(owned);
        } catch (error) {
            if (!lease.failed) { lease.failed = true; lease.error = error; }
            throw error;
        } finally {
            lease.pushing = false;
        }
    }
}

function collect<K>(maximum: bigint, clone: (key: K) => K, callback: (out: FeatureBuffer<K>) => void): K[] {
    const values: K[] = [];
    const out = Object.create(FeatureBuffer.prototype) as FeatureBuffer<K>;
    const lease: FeatureLease<K> = { values, clone, maximum, active: true, pushing: false, failed: false };
    leases.set(out, lease);
    try {
        // A callback's thrown value wins over a swallowed push error. No learning on error.
        const returned = callback(out);
        lease.active = false;
        synchronousVoid(returned);
        if (lease.failed) throw lease.error;
        return values;
    } finally {
        lease.active = false;
        lease.values = null;
        lease.clone = null;
        lease.error = undefined;
    }
}

function copyMetadata(metadata: FeedbackMetadata): FeedbackMetadata {
    const { name, version, build } = synchronous(metadata);
    scalar(name); scalar(build); natural(version, 'feedback version', 0, 0xffffffff);
    return { name, version, build };
}

/** Projects initial and successor states. Feature ownership/equality stays application-defined. */
export class StateFeedback<S, K> implements Feedback<S, unknown, unknown, K> {
    readonly #identity: FeatureIdentity<K>;
    readonly #metadata: FeedbackMetadata;
    readonly #project: (state: S) => K;
    constructor(metadata: FeedbackMetadata, project: (state: S) => K, identity: FeatureIdentity<K>) {
        this.#metadata = copyMetadata(metadata);
        this.#project = project;
        this.#identity = identity;
    }
    metadata(): FeedbackMetadata { return { ...this.#metadata }; }
    cloneFeature(feature: K): K { return synchronous(this.#identity.cloneFeature(feature)); }
    equalFeatures(left: K, right: K): boolean { return synchronous(this.#identity.equalFeatures(left, right)); }
    hashFeature(feature: K): string | number | bigint { return this.#identity.hashFeature ? synchronous(this.#identity.hashFeature(feature)) : 0; }
    initialFeatures(state: S, out: FeatureBuffer<K>): void { out.push(synchronous(this.#project(state))); }
    transitionFeatures(_before: S, _input: unknown, after: Transition<S, unknown>, out: FeatureBuffer<K>): void { out.push(synchronous(this.#project(after.state))); }
}

/** Full cloned-state novelty, including represented oracle/history fields. */
export function exactStateFeedback<S>(model: {
    cloneState(state: S): S;
    equalStates(left: S, right: S): boolean;
    hashState?(state: S): string | number | bigint;
}): StateFeedback<S, S> {
    return new StateFeedback<S, S>({ name: 'exact-state', version: 1, build: 'clone-eq-hash-v1' }, state => state, {
        cloneFeature: state => model.cloneState(state),
        equalFeatures: (left, right) => model.equalStates(left, right),
        ...(model.hashState ? { hashFeature: (state: S) => model.hashState!(state) } : {}),
    });
}

export interface GuidanceConfig<C extends Count = number> {
    readonly maxCorpusEntries: C;
    readonly maxCorpusInputs: C;
    readonly maxFeatures: C;
    readonly maxFeaturesPerObservation: C;
}
export const DEFAULT_GUIDANCE: GuidanceConfig = Object.freeze({ maxCorpusEntries: 256, maxCorpusInputs: 25600, maxFeatures: 100000, maxFeaturesPerObservation: 256 });
export function validateGuidanceConfig<C extends Count = number>(config: GuidanceConfig<C>): GuidanceConfig<C> {
    const { maxCorpusEntries, maxCorpusInputs, maxFeatures, maxFeaturesPerObservation } = config;
    const result = { maxCorpusEntries, maxCorpusInputs, maxFeatures, maxFeaturesPerObservation };
    if (Object.values(result).some(value => value === 0 || value === 0n)) throw new ConfigError('guidance limits must be positive');
    for (const [name, value] of Object.entries(result)) count64(value, name);
    return result;
}
export interface CorpusEntry<I, C extends Count = number> {
    inputs: I[];
    /** Zero when recovering a known key first discovered on an overlong prefix. */
    new_features: CountValue<C>;
    discovered_case: CountValue<C>;
    discovered_transition: bigint;
}
export interface GuidanceReport<I, C extends Count = number> {
    algorithm: typeof GUIDANCE_ALGORITHM;
    config: GuidanceConfig<C>;
    feedback: FeedbackMetadata;
    unique_features: CountValue<C>;
    corpus_inputs: CountValue<C>;
    corpus: CorpusEntry<I, C>[];
    mutation_cases: CountValue<C>;
    dropped_prefixes: bigint;
    evicted_prefixes: bigint;
    feature_limit_reached: boolean;
    corpus_limit_reached: boolean;
}
export type GuidedFuzzReport<I, C extends Count = number> = FuzzReport<I, C> & { guidance: GuidanceReport<I, C> };

class Driver<S, I, O, K, C extends Count> implements FuzzDriver<S, I, O> {
    readonly report: GuidanceReport<I, C>;
    readonly #seen = new Map<string | number | bigint, { key: K; witnessed: boolean }[]>();
    constructor(readonly model: Model<S, I, O>, readonly feedback: Feedback<S, I, O, K>, config: GuidanceConfig<C>, metadata: FeedbackMetadata, readonly representation: C) {
        this.report = { algorithm: GUIDANCE_ALGORITHM, config, feedback: metadata, unique_features: countLike(0n, this.representation), corpus_inputs: countLike(0n, this.representation), corpus: [], mutation_cases: countLike(0n, this.representation), dropped_prefixes: 0n, evicted_prefixes: 0n, feature_limit_reached: false, corpus_limit_reached: false };
    }
    clone(key: K): K { return guidedCall('clone feature', () => this.feedback.cloneFeature(key)); }
    learn(keys: K[], inputs: readonly I[], caseNumber: bigint, transition: bigint): void {
        const r = this.report, retainable = BigInt(inputs.length) <= BigInt(r.config.maxCorpusInputs);
        let novel = 0, recovered = false;
        for (const key of keys) {
            const hash = this.feedback.hashFeature ? guidedCall('feature hash', () => this.feedback.hashFeature!(this.clone(key))) : 0;
            if (!(typeof hash === 'string' || typeof hash === 'bigint' || typeof hash === 'number' && Number.isFinite(hash))) throw new ModelError('feature hash must be a string, finite number or bigint');
            const bucket = this.#seen.get(hash) ?? [];
            const entry = bucket.find(entry => guidedCall('feature equality', () => {
                const equal = synchronous(this.feedback.equalFeatures(this.clone(entry.key), this.clone(key)));
                if (typeof equal !== 'boolean') throw new ModelError('equalFeatures must return boolean');
                return equal;
            }));
            if (entry) {
                if (retainable && !entry.witnessed) { entry.witnessed = true; recovered = true; }
            } else if (BigInt(r.unique_features) === BigInt(r.config.maxFeatures)) {
                r.feature_limit_reached = true;
            } else {
                arrayGrowth(bucket.length);
                bucket.push({ key, witnessed: retainable });
                this.#seen.set(hash, bucket);
                r.unique_features = countLike(BigInt(r.unique_features) + 1n, this.representation);
                novel++;
            }
        }
        if ((!novel && !recovered) || inputs.length === 0) return;
        if (!retainable) {
            r.corpus_limit_reached = true;
            r.dropped_prefixes = addSkippedChecks(r.dropped_prefixes, 1n);
            return;
        }
        const allowance = BigInt(r.config.maxCorpusInputs) - BigInt(inputs.length);
        while (BigInt(r.corpus.length) >= BigInt(r.config.maxCorpusEntries) || BigInt(r.corpus_inputs) > allowance) {
            r.corpus_inputs = countLike(BigInt(r.corpus_inputs) - BigInt(r.corpus.shift()!.inputs.length), this.representation);
            r.evicted_prefixes = addSkippedChecks(r.evicted_prefixes, 1n);
            r.corpus_limit_reached = true;
        }
        const owned = inputs.map(value => guidedCall('snapshot input', () => this.model.cloneInput(value)));
        arrayGrowth(r.corpus.length);
        r.corpus.push({ inputs: owned, new_features: countLike(BigInt(novel), this.representation), discovered_case: countLike(caseNumber, this.representation), discovered_transition: transition });
        r.corpus_inputs = countLike(BigInt(r.corpus_inputs) + BigInt(owned.length), this.representation);
    }
    initial(state: S, caseNumber: bigint): void {
        const keys = collect<K>(BigInt(this.report.config.maxFeaturesPerObservation), key => this.clone(key), out => {
            if (this.feedback.initialFeatures) return this.feedback.initialFeatures(this.model.cloneState(state), out);
        });
        this.learn(keys, [], caseNumber, 0n);
    }
    transition(before: S, input: I, after: Transition<S, O>, inputs: readonly I[], caseNumber: bigint, transition: bigint): void {
        const keys = collect<K>(BigInt(this.report.config.maxFeaturesPerObservation), key => this.clone(key), out => this.feedback.transitionFeatures(
            this.model.cloneState(before), this.model.cloneInput(input),
            { state: this.model.cloneState(after.state), outputs: after.outputs.map(value => this.model.cloneOutput(value)), disposition: disposition(after.disposition.kind, after.disposition.reason) }, out));
        this.learn(keys, inputs, caseNumber, transition);
    }
    candidate(_previous: readonly I[], rng: Rng, percent: number): { inputs: I[]; replace: number | null } {
        const r = this.report;
        if (!r.corpus.length || rng.index(100n)! >= BigInt(percent)) return { inputs: [], replace: null };
        r.mutation_cases = countLike(BigInt(r.mutation_cases) + 1n, this.representation);
        const index = Number(rng.index(BigInt(r.corpus.length)));
        const inputs = r.corpus[index].inputs.map(value => guidedCall('snapshot input', () => this.model.cloneInput(value)));
        switch (rng.index(3n)) {
            case 0n: return { inputs, replace: null };
            case 1n: {
                const start = Number(rng.index(BigInt(inputs.length)));
                const length = 1 + Number(rng.index(BigInt(inputs.length - start)));
                inputs.splice(start, length);
                return { inputs, replace: Number(rng.index(BigInt(inputs.length) + 1n)) };
            }
            default: return { inputs, replace: Number(rng.index(BigInt(inputs.length))) };
        }
    }
}

/** Enforce synchronous native hooks without losing private-field receivers. */
function synchronousModel<S, I, O>(model: GenerateModel<S, I, O>): GenerateModel<S, I, O> {
    return new Proxy(Object.create(null), { get(_target, key) {
        const value = Reflect.get(model, key, model);
        return typeof value === 'function' ? (...args: unknown[]) => synchronous(value.apply(model, args)) : value;
    } });
}
function synchronousObserver<S, I, O>(observer?: SearchObserver<S, I, O>): SearchObserver<S, I, O> | undefined {
    if (!observer) return undefined;
    return new Proxy(Object.create(null), { get(_target, key) {
        const value = Reflect.get(observer, key, observer);
        return typeof value === 'function' ? (...args: unknown[]) => synchronousVoid(value.apply(observer, args)) : value;
    } });
}

/** Bare synchronous guided search. Metadata is queried once, after validation and clock start. */
export function fuzzGuided<S, I, O, K, C extends Count = number>(model: GenerateModel<S, I, O>, config: FuzzConfig<C>, guidance: GuidanceConfig<C>, feedback: Feedback<S, I, O, K>, limits: RunLimits = {}, observer?: SearchObserver<S, I, O>): GuidedFuzzReport<I, C> {
    const admitted = validateGuidanceConfig(guidance);
    const fuzz = snapshotFuzzConfig(config, true);
    const stop = runControl(limits);
    const metadata = feedbackMetadataCall(() => copyMetadata(feedback.metadata()));
    const m = synchronousModel(model);
    const driver = new Driver(m, feedback, admitted, metadata, fuzz.cases);
    const report = runFuzz(m, fuzz, stop, synchronousObserver(observer), driver);
    return { ...report, guidance: driver.report };
}
/** Guided fuzz with cooperative finite-domain scans; callbacks are not preempted. */
export function fuzzGuidedAuto<S, I, O, K, C extends Count = number>(model: EnumerateModel<S, I, O> & { equalInputs(left: I, right: I): boolean }, options: AutoOptions<Count>, config: FuzzConfig<C>, guidance: GuidanceConfig<C>, feedback: Feedback<S, I, O, K>, limits: RunLimits = {}, observer?: SearchObserver<S, I, O>): GuidedFuzzReport<I, C> {
    const maxCandidates = options.maxCandidates;
    if (count64(maxCandidates, 'maxCandidates') === 0n) throw new ConfigError('maxCandidates must be positive');
    const admitted = validateGuidanceConfig(guidance);
    const fuzz = snapshotFuzzConfig(config, true);
    const base = runControl(limits);
    let reason: 'cancelled' | 'deadline' | null = null;
    const stop = () => reason ?? (reason = base());
    const auto = automaticControlled(model, { maxCandidates }, () => stop() !== null);
    const metadata = feedbackMetadataCall(() => copyMetadata(feedback.metadata()));
    const m = synchronousModel(auto);
    const driver = new Driver(m, feedback, admitted, metadata, fuzz.cases);
    const report = runFuzz(m, fuzz, stop, synchronousObserver(observer), driver);
    return { ...report, guidance: driver.report };
}
export function fuzzGuidedObserved<S, I, O, K, C extends Count = number>(model: GenerateModel<S, I, O>, config: FuzzConfig<C>, guidance: GuidanceConfig<C>, feedback: Feedback<S, I, O, K>, observer: SearchObserver<S, I, O>, limits: RunLimits = {}): GuidedFuzzReport<I, C> {
    return fuzzGuided(model, config, guidance, feedback, limits, observer);
}

export type GuidanceConfig64 = GuidanceConfig<bigint>;
export type GuidanceReport64<I> = GuidanceReport<I, bigint>;
export type GuidedFuzzReport64<I> = GuidedFuzzReport<I, bigint>;
