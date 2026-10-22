/** Node-only campaign host. No closures, model objects or feature keys cross worker boundaries. */
// @ts-ignore Node built-in; the browser package entry does not import this file.
import { Worker } from 'node:worker_threads';
import { ConfigError, ModelError } from './errors.js';
import { validateGuidanceConfig, type GuidanceConfig } from './guided.js';
import { Lifecycle, type CampaignReport } from './campaign_lifecycle.js';
import { applyCampaignEvent, CampaignCancellation, CampaignControl, campaignPanicMessage, invokeCampaignSink, runCallerCampaign, validateCampaignConfig,
    type CampaignConfig, type CampaignLimits, type CampaignModel, type CampaignFeedback, type CampaignSink, type CampaignJobHooks } from './campaign.js';
import { unpackCampaignJob } from './campaign_wire.js';
import { campaignTransportProbe } from './campaign_test_support.js';

export interface NodeCampaignContext { readonly cancellation: CampaignCancellation }
/** The module executes locally for workers=1, inside each job worker otherwise. */
export interface NodeCampaignModule<S, I, O, K, W = unknown, D = unknown> {
    createModel(jobId: bigint, data: D, context: NodeCampaignContext): CampaignModel<S, I, O>;
    createFeedback?(jobId: bigint, model: CampaignModel<S, I, O>, data: D, context: NodeCampaignContext): CampaignFeedback<S, I, O, K>;
    encodeInput(input: I): W;
    disposeModel?(model: CampaignModel<S, I, O>): void;
    disposeFeedback?(feedback: CampaignFeedback<S, I, O, K>): void;
}
export interface NodeCampaignWorker<I, W = unknown, D = unknown> {
    /** Absolute module URL. The adapter imports this module; it never serializes a closure. */
    readonly module: URL | string;
    /** Structured-cloneable application initialization, optionally including explicit shared barriers. */
    readonly data?: D;
    /** Caller-owned faithful reconstruction of report inputs, including user classes. */
    readonly decodeInput: (wire: W) => I;
}
export async function runNodeCampaign<I, W, D>(config: CampaignConfig, limits: CampaignLimits, worker: NodeCampaignWorker<I, W, D>, sink: CampaignSink<I>): Promise<CampaignReport> {
    return runNodeUsing(config, limits, worker, sink);
}
export async function runNodeGuidedCampaign<I, W, D>(config: CampaignConfig, limits: CampaignLimits, guidance: GuidanceConfig, worker: NodeCampaignWorker<I, W, D>, sink: CampaignSink<I>): Promise<CampaignReport> {
    return runNodeUsing(config, limits, worker, sink, validateGuidanceConfig(guidance));
}
function infrastructureReport(config: CampaignConfig, error: unknown): CampaignReport {
    const lifecycle = new Lifecycle(config.firstJob, config.jobs, config.stopOnFailure);
    applyCampaignEvent(lifecycle, { kind: 'WorkerFailed', error: new ModelError(`unable to start campaign worker: ${campaignPanicMessage(error)}`), lostWork: false });
    applyCampaignEvent(lifecycle, { kind: 'Finish' });
    return lifecycle.state.report;
}
async function runNodeUsing<I, W, D>(value: CampaignConfig, limits: CampaignLimits, descriptor: NodeCampaignWorker<I, W, D>, sink: CampaignSink<I>, guidance?: GuidanceConfig): Promise<CampaignReport> {
    const config = validateCampaignConfig(value);
    if (config.workers > 1 && typeof limits.cancellation === 'function') throw new ConfigError('Node workers require CampaignCancellation; cancellation closures cannot cross workers');
    const module = new URL(descriptor.module).href;
    if (typeof descriptor.decodeInput !== 'function') throw new ConfigError('Node workers require explicit decodeInput');
    const decodeInput = descriptor.decodeInput;
    const data = descriptor.data;
    const control = new CampaignControl(limits);
    const stopped = control.stop();
    if (stopped) {
        const lifecycle = new Lifecycle(config.firstJob, config.jobs, config.stopOnFailure);
        applyCampaignEvent(lifecycle, { kind: 'Claim', stop: stopped }); applyCampaignEvent(lifecycle, { kind: 'Finish' });
        return lifecycle.state.report;
    }
    const cancellation = limits.cancellation instanceof CampaignCancellation ? limits.cancellation : new CampaignCancellation();
    if (config.workers === 1) {
        let loaded: NodeCampaignModule<any, I, any, any, W, D>;
        try { loaded = await import(module); }
        catch (error) { return infrastructureReport(config, error); }
        const context = { cancellation };
        const hooks: CampaignJobHooks<any, I, any, any> = {
            guidance,
            feedbackFactory: (jobId, model) => loaded.createFeedback!(jobId, model, data as D, context),
            disposeModel: loaded.disposeModel?.bind(loaded), disposeFeedback: loaded.disposeFeedback?.bind(loaded),
        };
        // No input transfer is needed on the caller path; no model codec is involved.
        // The module always receives a usable token, even when the caller supplied a
        // function. Keep the original control (and its limits receiver) in that union;
        // passing its absolute deadline avoids restarting the budget after import.
        const localControl = new CampaignControl({ cancellation: () => cancellation.isCancelled() || control.cancelled() }, control.deadline);
        return runCallerCampaign(config, localControl, id => loaded.createModel(id, data as D, context), sink, hooks);
    }
    if (cancellation.sharedBuffer === undefined) throw new ConfigError('Node workers require shared cancellation storage');
    let coordinator: any;
    try {
        coordinator = new Worker(new URL('./node_campaign_worker.js', import.meta.url), { workerData: { role: 'coordinator', config, guidance, module, data, cancellation: cancellation.sharedBuffer, deadline: control.deadline, testProbe: campaignTransportProbe(descriptor) } });
    } catch (error) { return infrastructureReport(config, error); }
    // Resolve only after the coordinator has drained, joined its job workers, and itself exited.
    return new Promise<CampaignReport>((resolve, reject) => {
        let report: CampaignReport | undefined;
        let failure: unknown;
        let delivering = false;
        coordinator.on('error', (error: unknown) => { failure = error; });
        coordinator.on('message', (message: any) => {
            if (message.kind === 'report') { report = message.report; return; }
            if (message.kind !== 'deliver' || delivering) {
                failure = new ModelError('invalid campaign coordinator message');
                return;
            }
            delivering = true;
            const receivedJobId: bigint = message.job.jobId;
            // The coordinator already applied Receive and released the sender before this callback.
            const deliver = async (): Promise<void> => {
                let error: ModelError | null;
                try {
                    const job = unpackCampaignJob<I, W>(message.job, decodeInput);
                    error = await invokeCampaignSink(sink, job);
                } catch (cause) { error = new ModelError(`campaign input delivery failed: ${campaignPanicMessage(cause)}`); }
                coordinator.postMessage({ kind: 'sinkReturned', jobId: receivedJobId, error: error === null ? null : campaignPanicMessage(error) });
                delivering = false;
            };
            void deliver().catch(error => { failure = error; });
        });
        coordinator.on('exit', (code: number) => {
            if (failure !== undefined || code !== 0 || report === undefined) {
                reject(new ModelError(`campaign coordinator failed; aggregate unavailable: ${campaignPanicMessage(failure ?? `exit ${code}`)}`));
                return;
            }
            if (report.termination.kind === 'CallbackError' || report.termination.kind === 'WorkerError') {
                const wire = report.termination as any;
                report.termination = { kind: wire.kind, error: new ModelError(wire.error) };
            }
            resolve(report);
        });
    });
}
