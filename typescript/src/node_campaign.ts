import { count64, type Count } from './counts.js';
/** Node-only campaign host. No closures, model objects or feature keys cross worker boundaries. */
// @ts-ignore Node built-in; the browser package entry does not import this file.
import { Worker } from 'node:worker_threads';
import { ConfigError, ModelError } from './errors.js';
import { fuzzGuidedAuto, validateGuidanceConfig, type GuidanceConfig } from './guided.js';
import { fuzzAuto, type AutoOptions } from './search.js';
import { Lifecycle, type CampaignReport } from './campaign_lifecycle.js';
import { applyCampaignEvent, CampaignCancellation, CampaignControl, campaignPanicMessage, invokeCampaignSink, runCallerCampaign, validateCampaignConfig, requireAutoCampaignModel, validateAutoCampaignModule,
    type CampaignConfig, type CampaignLimits, type CampaignModel, type AutoCampaignModel, type CampaignFeedback, type CampaignSink, type CampaignJobHooks, type CampaignJobRunners } from './campaign.js';
import { unpackCampaignJob } from './campaign_wire.js';
import { campaignTransportProbe } from './campaign_test_support.js';
import {admitAssuranceManifest,snapshotAssuranceObservationLimits,validateAssuranceReport,runWorkerAssured,runWorkerAssuredGuided,finishAssuredCampaign,checkAssuranceJobLimit,snapshotAssuranceCampaignLimits,AssuranceReportStore,type AssuranceCoverage,type AssuranceManifest,type MergedAssurance,type AssuranceCampaignOptions,type AssuranceObservationLimits} from './assurance.js';
import type {Metadata} from './core.js';

export interface NodeCampaignContext { readonly cancellation: CampaignCancellation }
/** The module executes locally for workers=1, inside each job worker otherwise. */
export interface NodeCampaignModule<S, I, O, K, W = unknown, D = unknown> {
    createModel(jobId: bigint, data: D, context: NodeCampaignContext): CampaignModel<S, I, O>;
    createFeedback?(jobId: bigint, model: CampaignModel<S, I, O>, data: D, context: NodeCampaignContext): CampaignFeedback<S, I, O, K>;
    createCoverage?(jobId:bigint,model:CampaignModel<S,I,O>,data:D,context:NodeCampaignContext):AssuranceCoverage<S,I,O>;
    encodeInput(input: I): W;
    disposeModel?(model: CampaignModel<S, I, O>): void;
    disposeFeedback?(feedback: CampaignFeedback<S, I, O, K>): void;
}
/** Module contract for automatic Node campaigns: enumeration and input equality replace generate. */
export interface NodeAutoCampaignModule<S, I, O, K, W = unknown, D = unknown> {
    createModel(jobId: bigint, data: D, context: NodeCampaignContext): AutoCampaignModel<S, I, O>;
    createFeedback?(jobId: bigint, model: AutoCampaignModel<S, I, O>, data: D, context: NodeCampaignContext): CampaignFeedback<S, I, O, K>;
    encodeInput(input: I): W;
    disposeModel?(model: AutoCampaignModel<S, I, O>): void;
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
export async function runNodeCampaign<I, W, D, C extends Count = number>(config: CampaignConfig<C>, limits: CampaignLimits, worker: NodeCampaignWorker<I, W, D>, sink: CampaignSink<I, C>): Promise<CampaignReport<C>> {
    return runNodeUsing(config, limits, worker, sink);
}
export async function runNodeGuidedCampaign<I, W, D, C extends Count = number>(config: CampaignConfig<C>, limits: CampaignLimits, guidance: GuidanceConfig<C>, worker: NodeCampaignWorker<I, W, D>, sink: CampaignSink<I, C>): Promise<CampaignReport<C>> {
    return runNodeUsing(config, limits, worker, sink, validateGuidanceConfig(guidance));
}
/** Node module workers enumerate and compare inputs locally; no model closures cross threads. */
export async function runNodeAutoCampaign<I, W, D, C extends Count = number>(config: CampaignConfig<C>, limits: CampaignLimits, options: AutoOptions<Count>, worker: NodeCampaignWorker<I, W, D>, sink: CampaignSink<I, C>): Promise<CampaignReport<C>> {
    const maxCandidates = options.maxCandidates;
    if (count64(maxCandidates, 'maxCandidates') === 0n) throw new ConfigError('maxCandidates must be positive');
    return runNodeUsing(config, limits, worker, sink, undefined, undefined, { maxCandidates });
}
export async function runNodeGuidedAutoCampaign<I, W, D, C extends Count = number>(config: CampaignConfig<C>, limits: CampaignLimits, options: AutoOptions<Count>, guidance: GuidanceConfig<C>, worker: NodeCampaignWorker<I, W, D>, sink: CampaignSink<I, C>): Promise<CampaignReport<C>> {
    const maxCandidates = options.maxCandidates;
    if (count64(maxCandidates, 'maxCandidates') === 0n) throw new ConfigError('maxCandidates must be positive');
    return runNodeUsing(config, limits, worker, sink, validateGuidanceConfig(guidance), undefined, { maxCandidates });
}
export async function runNodeAssuredCampaign<I,W,D,C extends Count=number>(value:CampaignConfig<C>,limits:CampaignLimits,manifest:AssuranceManifest,metadata:Metadata,worker:NodeCampaignWorker<I,W,D>,sink:CampaignSink<I,C>,options:AssuranceCampaignOptions={}):Promise<{campaign:CampaignReport<C>;assurance:MergedAssurance}>{
 return runNodeAssuredUsing(value,limits,manifest,metadata,worker,sink,undefined,options);
}
export async function runNodeAssuredGuidedCampaign<I,W,D,C extends Count=number>(value:CampaignConfig<C>,limits:CampaignLimits,manifest:AssuranceManifest,metadata:Metadata,guidance:GuidanceConfig<C>,worker:NodeCampaignWorker<I,W,D>,sink:CampaignSink<I,C>,options:AssuranceCampaignOptions={}):Promise<{campaign:CampaignReport<C>;assurance:MergedAssurance}>{
 return runNodeAssuredUsing(value,limits,manifest,metadata,worker,sink,validateGuidanceConfig(guidance),options);
}
async function runNodeAssuredUsing<I,W,D,C extends Count>(value:CampaignConfig<C>,limits:CampaignLimits,manifest:AssuranceManifest,metadata:Metadata,worker:NodeCampaignWorker<I,W,D>,sink:CampaignSink<I,C>,guidance?:GuidanceConfig<C>,options:AssuranceCampaignOptions={}):Promise<{campaign:CampaignReport<C>;assurance:MergedAssurance}>{
 const config=validateCampaignConfig(value),observationLimits=snapshotAssuranceObservationLimits(options.observationLimits),owned=admitAssuranceManifest(manifest,observationLimits),identity={...metadata},control=snapshotAssuranceCampaignLimits(limits);if(owned.model!==identity.name)throw new ModelError('campaign model identity mismatch');checkAssuranceJobLimit(config.jobs,options);
 const reports=new AssuranceReportStore(options),campaign=await runNodeUsing(config,control,worker,sink,guidance,{manifest:owned,metadata:identity,reports,observationLimits});
 const expected=Array.from({length:Number(config.jobs)},(_,i)=>config.firstJob+BigInt(i)),assurance=finishAssuredCampaign(config,owned,identity,expected,reports.entries(),campaign,control,reports.mergeLimits(observationLimits.maxManifestBytes,observationLimits.maxManifestEntries,expected.length));reports.finish(assurance);return {campaign,assurance};
}
function infrastructureReport<C extends Count = number>(config: CampaignConfig<C>, error: unknown): CampaignReport<C> {
    const lifecycle = new Lifecycle(config.firstJob, config.jobs, config.stopOnFailure);
    applyCampaignEvent(lifecycle, { kind: 'WorkerFailed', error: new ModelError(`unable to start campaign worker: ${campaignPanicMessage(error)}`), lostWork: false });
    applyCampaignEvent(lifecycle, { kind: 'Finish' });
    return lifecycle.state.report;
}
async function runNodeUsing<I, W, D, C extends Count = number>(value: CampaignConfig<C>, limits: CampaignLimits, descriptor: NodeCampaignWorker<I, W, D>, sink: CampaignSink<I, C>, guidance?: GuidanceConfig<C>,assurance?:{manifest:AssuranceManifest;metadata:Metadata;reports:AssuranceReportStore;observationLimits:AssuranceObservationLimits},auto?:AutoOptions<Count>): Promise<CampaignReport<C>> {
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
    if (BigInt(config.workers) === 1n) {
        let loaded: NodeCampaignModule<any, I, any, any, W, D>;
        try { loaded = await import(module); if (auto) validateAutoCampaignModule(loaded, guidance !== undefined); }
        catch (error) { return infrastructureReport(config, error); }
        const context = { cancellation };
        const hooks: CampaignJobHooks<any, I, any, any, C> = {
            guidance,
            feedbackFactory: (jobId, model) => loaded.createFeedback!(jobId, model, data as D, context),
            disposeModel: loaded.disposeModel?.bind(loaded), disposeFeedback: loaded.disposeFeedback?.bind(loaded),
        };
        const runners: CampaignJobRunners<any, I, any, any, C> = {
            ...(auto?{runJob:(_id:any,model:any,jobConfig:any,jobLimits:any)=>fuzzAuto(model,auto,jobConfig,jobLimits),runGuidedJob:(_id:any,model:any,jobConfig:any,jobLimits:any,_actual:any,feedback:any,guided:any)=>fuzzGuidedAuto(model,auto,jobConfig,guided,feedback,jobLimits)}:{}),
            ...(assurance?{runJob:(id,model,jobConfig,jobLimits,actual)=>{if(typeof loaded.createCoverage!=='function')throw new ModelError('worker module requires createCoverage');const coverage=loaded.createCoverage(id,model,data as D,context);if(typeof coverage!=='function')throw new ModelError('coverage factory must return a function');const result=runWorkerAssured(model,actual,assurance.metadata,assurance.manifest,coverage,jobConfig,jobLimits,assurance.observationLimits);assurance.reports.retain(id,result.assurance);if(!result.search.ok)throw result.search.error;return result.search.value;}}:{}),
            ...(assurance?{runGuidedJob:(id,model,jobConfig,jobLimits,actual,feedback,guided)=>{if(typeof loaded.createCoverage!=='function')throw new ModelError('worker module requires createCoverage');const coverage=loaded.createCoverage(id,model,data as D,context);if(typeof coverage!=='function')throw new ModelError('coverage factory must return a function');const result=runWorkerAssuredGuided(model,actual,assurance.metadata,assurance.manifest,coverage,jobConfig,jobLimits,guided,feedback,assurance.observationLimits);assurance.reports.retain(id,result.assurance);if(!result.search.ok)throw result.search.error;return result.search.value;}}:{}),
        };
        // No input transfer is needed on the caller path; no model codec is involved.
        // The module always receives a usable token, even when the caller supplied a
        // function. Keep the original control (and its limits receiver) in that union;
        // passing its absolute deadline avoids restarting the budget after import.
        const localControl = new CampaignControl({ cancellation: () => cancellation.isCancelled() || control.cancelled() }, control.deadline);
        return runCallerCampaign(config, localControl, id => { const model = loaded.createModel(id, data as D, context); return auto ? requireAutoCampaignModel(model) : model; }, sink, hooks, runners);
    }
    if (cancellation.sharedBuffer === undefined) throw new ConfigError('Node workers require shared cancellation storage');
    let coordinator: any;
    try {
        coordinator = new Worker(new URL('./node_campaign_worker.js', import.meta.url), { workerData: { role: 'coordinator', config, guidance, module, data, auto, cancellation: cancellation.sharedBuffer, deadline: control.deadline, testProbe: campaignTransportProbe(descriptor),assurance:assurance?{manifest:assurance.manifest,metadata:assurance.metadata,observationLimits:assurance.observationLimits}:undefined } });
    } catch (error) { return infrastructureReport(config, error); }
    // Resolve only after the coordinator has drained, joined its job workers, and itself exited.
    return new Promise<CampaignReport<C>>((resolve, reject) => {
        let report: CampaignReport<C> | undefined;
        let failure: unknown;
        let delivering = false;
        coordinator.on('error', (error: unknown) => { failure = error; });
        coordinator.on('message', (message: any) => {
            if (message.kind === 'report') { report = message.report; return; }
            if (message.kind === 'assurance' && assurance) {try{validateAssuranceReport(message.report);assurance.reports.retain(message.jobId,message.report);}catch(error){failure=error;}return;}
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
                    const job = unpackCampaignJob<I, W, C>(message.job, decodeInput);
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
