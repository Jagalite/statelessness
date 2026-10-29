import { metadataPanic } from './guided_sync.js';
/** Bounded independent jobs. The lifecycle law is private; report inputs belong to the application. */
import { ConfigError, ModelError, Rng, natural, validateMetadata, type Metadata } from './core.js';
import { DEFAULT_FUZZ, fuzz, type FuzzConfig, type FuzzReport, type GenerateModel } from './search.js';
import { fuzzGuided, validateGuidanceConfig, type Feedback, type GuidanceConfig, type GuidedFuzzReport } from './guided.js';
import { snapshotFuzzConfig } from './fuzz_kernel.js';
import { synchronous } from './guided_sync.js';
import { Lifecycle, type CampaignReport, type Summary, type LifecycleInput, type LifecycleOutput } from './campaign_lifecycle.js';
import type { RunLimits } from './search_support.js';

export type { CampaignReport, CampaignTermination } from './campaign_lifecycle.js';
const U64_MAX = (1n << 64n) - 1n;
function u64(value: bigint, name: string): void {
    if (typeof value !== 'bigint' || value < 0n || value > U64_MAX) throw new ConfigError(`${name} must be u64 bigint`);
}
export interface CampaignConfig {
    readonly masterSeed: bigint;
    readonly firstJob: bigint;
    /** JavaScript safe integers, as are the existing FuzzConfig case/step counts. */
    readonly jobs: number;
    readonly workers: number;
    readonly resultBuffer: number;
    readonly fuzz: FuzzConfig;
    readonly stopOnFailure: boolean;
}
export const DEFAULT_CAMPAIGN: CampaignConfig = Object.freeze({ masterSeed: 0n, firstJob: 0n, jobs: 16, workers: 1, resultBuffer: 1, fuzz: DEFAULT_FUZZ, stopOnFailure: false });

/** Validation and defensive configuration snapshot precede factories, workers and sink calls. */
export function validateCampaignConfig(value: CampaignConfig): CampaignConfig {
    const { masterSeed, firstJob, jobs, workers, resultBuffer, fuzz: source, stopOnFailure } = value;
    if (jobs === 0) throw new ConfigError('campaign jobs must be positive');
    if (!Number.isSafeInteger(jobs) || jobs < 1) throw new ConfigError('campaign jobs must be positive safe integer');
    if (!Number.isSafeInteger(workers) || workers < 1 || workers > 256) throw new ConfigError('campaign workers must be in 1..=256');
    if (!Number.isSafeInteger(resultBuffer) || resultBuffer < 0 || resultBuffer > 1024) throw new ConfigError('campaign result_buffer must be in 0..=1024');
    const fuzz = { seed: source.seed, cases: source.cases, maxSteps: source.maxSteps, maxTransitions: source.maxTransitions, mutationPercent: source.mutationPercent };
    if (!Number.isSafeInteger(fuzz.mutationPercent) || fuzz.mutationPercent < 0 || fuzz.mutationPercent > 100) throw new ConfigError('mutation_percent must be in 0..=100');
    u64(masterSeed, 'masterSeed'); u64(firstJob, 'firstJob');
    if (firstJob + BigInt(jobs) - 1n > U64_MAX) throw new ConfigError('campaign job ID range overflows u64');
    natural(fuzz.cases, 'cases'); natural(fuzz.maxSteps, 'maxSteps');
    u64(fuzz.maxTransitions, 'maxTransitions'); u64(fuzz.seed, 'seed');
    if (BigInt(jobs) * BigInt(fuzz.cases) > U64_MAX) throw new ConfigError('campaign aggregate cases overflow u64');
    const wide = BigInt(fuzz.cases) * BigInt(fuzz.maxSteps);
    const transitions = wide < fuzz.maxTransitions ? wide : fuzz.maxTransitions;
    if (BigInt(jobs) * transitions > U64_MAX) throw new ConfigError('campaign aggregate transitions overflow u64');
    if (typeof stopOnFailure !== 'boolean') throw new ConfigError('stopOnFailure must be boolean');
    return { masterSeed, firstJob, jobs, workers, resultBuffer, fuzz: Object.freeze(fuzz), stopOnFailure };
}
export function jobSeed(masterSeed: bigint, jobId: bigint): bigint {
    u64(masterSeed, 'masterSeed'); u64(jobId, 'jobId');
    return new Rng((masterSeed + jobId) & U64_MAX).nextU64();
}

/** Shared cooperative flag. It never preempts a callback or a blocked sink. */
export class CampaignCancellation {
    readonly #word?: Int32Array;
    #local = false;
    constructor(buffer?: SharedArrayBuffer) {
        if (buffer !== undefined && (!(buffer instanceof SharedArrayBuffer) || buffer.byteLength !== 4)) throw new ConfigError('cancellation buffer must be a four-byte SharedArrayBuffer');
        if (buffer !== undefined || typeof SharedArrayBuffer !== 'undefined') this.#word = new Int32Array(buffer ?? new SharedArrayBuffer(4));
    }
    get sharedBuffer(): SharedArrayBuffer | undefined { return this.#word?.buffer as SharedArrayBuffer | undefined; }
    cancel(): void { if (this.#word) Atomics.store(this.#word, 0, 1); else this.#local = true; }
    isCancelled(): boolean { return this.#word ? Atomics.load(this.#word, 0) !== 0 : this.#local; }
}
export interface CampaignLimits {
    readonly maxDurationMs?: number;
    /** Function controls are caller-only. Worker hosts require the shared token. */
    readonly cancellation?: CampaignCancellation | (() => boolean);
}
/** Internal cross-worker clock representation; absolute monotonic epoch milliseconds. */
export class CampaignControl {
    readonly deadline: number | undefined;
    readonly cancellation: CampaignLimits['cancellation'];
    readonly #limitsReceiver: CampaignLimits;
    constructor(limits: CampaignLimits, deadline?: number) {
        const duration = limits.maxDurationMs;
        if (duration !== undefined && (!Number.isFinite(duration) || duration < 0)) throw new ConfigError('maxDurationMs must be finite and nonnegative');
        this.cancellation = limits.cancellation;
        this.#limitsReceiver = limits;
        if (this.cancellation !== undefined && !(this.cancellation instanceof CampaignCancellation) && typeof this.cancellation !== 'function') throw new ConfigError('invalid campaign cancellation');
        this.deadline = deadline ?? (duration === undefined ? undefined : CampaignControl.now() + duration);
    }
    static now(): number { return performance.timeOrigin + performance.now(); }
    cancelled(): boolean {
        const flag = this.cancellation;
        const value = flag instanceof CampaignCancellation ? flag.isCancelled() : flag?.call(this.#limitsReceiver);
        if (value !== undefined && typeof value !== 'boolean') throw new ModelError('cancellation must return boolean');
        return value === true;
    }
    stop(): 'Cancelled' | 'Deadline' | null {
        return this.cancelled() ? 'Cancelled' : this.deadline !== undefined && CampaignControl.now() >= this.deadline ? 'Deadline' : null;
    }
    remaining(): RunLimits {
        return { ...(this.deadline === undefined ? {} : { maxDurationMs: Math.max(0, this.deadline - CampaignControl.now()) }), cancellation: () => this.cancelled() };
    }
}
export interface CampaignModel<S, I, O> extends GenerateModel<S, I, O> {
    metadata(): Metadata;
    /** Explicit deterministic teardown; JavaScript garbage collection is not a destructor. */
    dispose?(): void;
}
export type CampaignFactory<S, I, O> = (jobId: bigint) => CampaignModel<S, I, O>;
export type CampaignFeedback<S, I, O, K> = Feedback<S, I, O, K> & { dispose?(): void };
export type JobOutcome<I> = { kind: 'Completed'; report: FuzzReport<I> | GuidedFuzzReport<I> }
    | { kind: 'FactoryError' | 'ModelError'; error: ModelError }
    | { kind: 'Panicked'; message: string };
export interface JobReport<I> {
    jobId: bigint;
    seed: bigint;
    config: FuzzConfig;
    metadata: Metadata | null;
    outcome: JobOutcome<I>;
}
export type CampaignSink<I> = (job: JobReport<I>) => void | Promise<void>;
export interface CampaignJobHooks<S, I, O, K> {
    guidance?: GuidanceConfig;
    feedbackFactory?: (jobId: bigint, model: CampaignModel<S, I, O>) => CampaignFeedback<S, I, O, K>;
    disposeModel?: (model: CampaignModel<S, I, O>) => void;
    disposeFeedback?: (feedback: CampaignFeedback<S, I, O, K>) => void;
}
/** Never stringify arbitrary exception payloads or invoke a custom formatter. */
export function campaignPanicMessage(value: unknown): string {
    if (typeof value === 'string') return value;
    try {
        if (value instanceof Error) {
            const descriptor = Object.getOwnPropertyDescriptor(value, 'message');
            if (descriptor && 'value' in descriptor && typeof descriptor.value === 'string') return descriptor.value;
        }
    } catch { /* hostile proxies are not inspected further */ }
    return 'non-string panic payload';
}
function isCampaignModelError(value: unknown): boolean {
    // instanceof can invoke a proxy's getPrototypeOf trap (including a revoked proxy).
    try { return value instanceof ModelError; } catch { return false; }
}
function voidCallback(callback: () => unknown): void {
    if (synchronous(callback()) !== undefined) throw new ModelError('campaign cleanup must return undefined synchronously');
}

/** Uses the actual public fuzz/guided functions; their native ModelError wrapping is preserved. */
export function executeCampaignJob<S, I, O, K>(campaign: CampaignConfig, control: CampaignControl, factory: CampaignFactory<S, I, O>, jobId: bigint, hooks: CampaignJobHooks<S, I, O, K> = {}): JobReport<I> {
    const seed = jobSeed(campaign.masterSeed, jobId);
    const config = snapshotFuzzConfig({ ...campaign.fuzz, seed });
    let metadata: Metadata | null = null;
    let model: CampaignModel<S, I, O> | undefined;
    let feedback: CampaignFeedback<S, I, O, K> | undefined;
    let outcome: JobOutcome<I>;
    try {
        try { model = synchronous(factory(jobId)); }
        catch (error) {
            if (!isCampaignModelError(error)) throw error;
            return { jobId, seed, config, metadata, outcome: { kind: 'FactoryError', error: new ModelError(campaignPanicMessage(error)) } };
        }
        metadata = validateMetadata(synchronous(model.metadata()));
        try {
            if (hooks.guidance !== undefined) {
                // remaining() includes model factory and metadata time; feedback construction is also timed.
                feedback = synchronous(hooks.feedbackFactory!(jobId, model));
                outcome = { kind: 'Completed', report: fuzzGuided(model, config, hooks.guidance, feedback, control.remaining()) };
            } else outcome = { kind: 'Completed', report: fuzz(model, config, control.remaining()) };
        } catch (error) {
            const panic = metadataPanic(error);
            if (panic) throw panic.value;
            if (!isCampaignModelError(error)) throw error;
            outcome = { kind: 'ModelError', error: new ModelError(campaignPanicMessage(error)) };
        }
    } catch (error) { outcome = { kind: 'Panicked', message: campaignPanicMessage(error) }; }
    // Feedback dies before the model. A recoverable teardown exception loses hidden job accounting.
    let cleanupFailed = false;
    if (feedback !== undefined) {
        try { voidCallback(() => hooks.disposeFeedback ? hooks.disposeFeedback(feedback!) : feedback!.dispose?.()); }
        catch (error) { cleanupFailed = true; outcome = { kind: 'Panicked', message: campaignPanicMessage(error) }; }
    }
    if (model !== undefined) {
        try { voidCallback(() => hooks.disposeModel ? hooks.disposeModel(model!) : model!.dispose?.()); }
        catch (error) { if (!cleanupFailed) outcome = { kind: 'Panicked', message: campaignPanicMessage(error) }; }
    }
    return { jobId, seed, config, metadata, outcome: outcome! };
}
export function campaignSummary(job: JobReport<unknown>): Summary {
    const outcome = job.outcome;
    if (outcome.kind !== 'Completed') return { outcome: outcome.kind, failure: false, transitions: 0n, cases: 0n, skippedChecks: 0n };
    const report = outcome.report;
    return { outcome: report.termination === 'failure_found' ? 'Failure' : report.termination === 'cancelled' ? 'Cancelled' : report.termination === 'deadline' ? 'Deadline' : 'Completed', failure: report.failure !== null, transitions: report.transitions, cases: BigInt(report.cases), skippedChecks: report.skipped_checks };
}
/** Private coordinator guard. Only an exhausted/stopped Claim may reject at runtime. */
export function applyCampaignEvent(lifecycle: Lifecycle, event: LifecycleInput): LifecycleOutput[] {
    const effects = lifecycle.apply(event);
    if (!lifecycle.lastAccepted && event.kind !== 'Claim') throw new Error(`invalid campaign lifecycle event ${event.kind}: ${lifecycle.lastRejection}`);
    return effects;
}
export async function invokeCampaignSink<I>(sink: CampaignSink<I>, job: JobReport<I>): Promise<ModelError | null> {
    try {
        const returned = await sink(job);
        if (returned !== undefined) throw new ModelError('campaign sink must return undefined');
        return null;
    } catch (error) {
        // Snapshot the diagnostic before it enters lifecycle state. A hostile message getter
        // or later mutation must not replace the first failed delivery or prevent draining.
        return new ModelError(isCampaignModelError(error) ? campaignPanicMessage(error) : `campaign callback panicked: ${campaignPanicMessage(error)}`);
    }
}
/** Internal serial runtime also used by the Node module host when configured workers=1. */
export async function runCallerCampaign<S, I, O, K>(config: CampaignConfig, control: CampaignControl, factory: CampaignFactory<S, I, O>, sink: CampaignSink<I>, hooks: CampaignJobHooks<S, I, O, K> = {}): Promise<CampaignReport> {
    const lifecycle = new Lifecycle(config.firstJob, config.jobs, config.stopOnFailure);
    for (;;) {
        const effects = applyCampaignEvent(lifecycle, { kind: 'Claim', stop: control.stop() });
        if (effects.length === 0) break;
        const job = executeCampaignJob(config, control, factory, effects[0].jobId, hooks);
        applyCampaignEvent(lifecycle, { kind: 'Complete', jobId: job.jobId, summary: campaignSummary(job) });
        const receipt = applyCampaignEvent(lifecycle, { kind: 'Receive', jobId: job.jobId });
        if (receipt[0]?.kind !== 'Deliver') throw new Error('invalid campaign receipt');
        const receivedJobId = job.jobId;
        const error = await invokeCampaignSink(sink, job);
        applyCampaignEvent(lifecycle, { kind: 'SinkReturned', jobId: receivedJobId, error });
        if (error) break;
    }
    applyCampaignEvent(lifecycle, { kind: 'Finish' });
    return lifecycle.state.report;
}
/** Browser-safe caller-thread entry. Use the Node host's explicit module API for workers>1. */
export async function runCampaign<S, I, O>(value: CampaignConfig, limits: CampaignLimits, factory: CampaignFactory<S, I, O>, sink: CampaignSink<I>): Promise<CampaignReport> {
    const config = validateCampaignConfig(value);
    if (config.workers !== 1) throw new ConfigError('multiple campaign workers require an explicit host adapter; use node-campaign in Node');
    return runCallerCampaign(config, new CampaignControl(limits), factory, sink);
}
export async function runGuidedCampaign<S, I, O, K>(value: CampaignConfig, limits: CampaignLimits, guidance: GuidanceConfig, factory: CampaignFactory<S, I, O>, feedbackFactory: (jobId: bigint, model: CampaignModel<S, I, O>) => CampaignFeedback<S, I, O, K>, sink: CampaignSink<I>): Promise<CampaignReport> {
    const admitted = validateGuidanceConfig(guidance);
    const config = validateCampaignConfig(value);
    if (config.workers !== 1) throw new ConfigError('multiple campaign workers require an explicit host adapter; use node-campaign in Node');
    return runCallerCampaign(config, new CampaignControl(limits), factory, sink, { guidance: admitted, feedbackFactory });
}
