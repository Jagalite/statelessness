/** Private Node worker bootstrap: a separate coordinator keeps progressing during a blocked caller sink. */
// @ts-ignore Node built-ins; not reachable from the browser core entry.
import { Worker, parentPort, workerData } from 'node:worker_threads';
import { ModelError } from './errors.js';
import { applyCampaignEvent, CampaignCancellation, CampaignControl, campaignPanicMessage, campaignSummary, executeCampaignJob, type CampaignConfig } from './campaign.js';
import { Lifecycle } from './campaign_lifecycle.js';
import { packCampaignJob } from './campaign_wire.js';
import type { NodeCampaignModule } from './node_campaign.js';
import { TRANSPORT_PROBE_FIELDS as probeFields } from './campaign_test_support.js';

if (workerData.role === 'coordinator') coordinate();
else void work().catch(error => { throw error; });

async function work(): Promise<void> {
    const { config, guidance, module, data, deadline, cancellation: buffer } = workerData;
    const cancellation = new CampaignCancellation(buffer);
    const control = new CampaignControl({ cancellation }, deadline);
    const implementation: NodeCampaignModule<any, any, any, any> = await import(module);
    if (typeof implementation.createModel !== 'function' || typeof implementation.encodeInput !== 'function') throw new Error('worker module requires createModel and encodeInput');
    if (guidance !== undefined && typeof implementation.createFeedback !== 'function') throw new Error('guided worker module requires createFeedback');
    let pending: ((reply: any) => void) | undefined;
    parentPort.on('message', (message: any) => {
        if (!pending) throw new Error('unsolicited campaign worker reply');
        const accept = pending; pending = undefined; accept(message);
    });
    function request(message: unknown): Promise<any> {
        return new Promise(resolve => { pending = resolve; parentPort.postMessage(message); });
    }
    for (;;) {
        const claim = await request({ kind: 'claim' });
        if (claim.kind === 'stop') break;
        if (claim.kind !== 'run') throw new Error('invalid campaign claim reply');
        const context = { cancellation };
        const job = executeCampaignJob(config, control, id => implementation.createModel(id, data, context), claim.jobId, {
            guidance,
            feedbackFactory: (id, model) => implementation.createFeedback!(id, model, data, context),
            disposeModel: implementation.disposeModel?.bind(implementation),
            disposeFeedback: implementation.disposeFeedback?.bind(implementation),
        });
        // Complete and its stop effect happen before input transfer and the bounded blocking send.
        const completed = await request({ kind: 'complete', jobId: job.jobId, summary: campaignSummary(job) });
        if (completed.kind !== 'completed') throw new Error('invalid campaign complete reply');
        const wire = packCampaignJob(job, input => implementation.encodeInput(input));
        const sent = await request({ kind: 'send', job: wire });
        if (sent.kind !== 'sent') throw new Error('invalid campaign send reply');
    }
    parentPort.postMessage({ kind: 'done' });
    parentPort.close();
}

function coordinate(): void {
    const { config, guidance, module, data, deadline, cancellation: buffer } = workerData as { config: CampaignConfig; guidance: any; module: string; data: unknown; deadline?: number; cancellation: SharedArrayBuffer };
    const lifecycle = new Lifecycle(config.firstJob, config.jobs, config.stopOnFailure);
    const control = new CampaignControl({ cancellation: new CampaignCancellation(buffer) }, deadline);
    type Sender = { worker: any; active: bigint | null; completed: boolean; blocked: boolean; done: boolean; exited: boolean; error?: string };
    type Packet = { sender: Sender; job: any; accepted: boolean };
    const workers: Sender[] = [];
    const queue: Packet[] = [];
    const blocked: Packet[] = [];
    let delivering = false;
    let finalized = false;
    let spawning = true;
    const probe = workerData.testProbe === undefined ? undefined : new BigInt64Array(workerData.testProbe);
    let completedCount = 0, sendCount = 0, acceptedCount = 0, deniedCount = 0;

    function publishProbe(): void {
        if (probe === undefined) return;
        Atomics.add(probe, probeFields.version, 1n);
        const values = { started: lifecycle.state.report.started, completed: completedCount,
            sends: sendCount, accepted: acceptedCount, received: lifecycle.state.report.finished,
            queued: queue.length, blocked: blocked.length, delivering: Number(delivering), workers: workers.length, denied: deniedCount };
        for (const [key, value] of Object.entries(values)) Atomics.store(probe, probeFields[key as keyof typeof values], BigInt(value));
        Atomics.add(probe, probeFields.version, 1n);
        Atomics.notify(probe, probeFields.version);
    }

    function accept(packet: Packet): void {
        packet.accepted = true;
        acceptedCount++;
        const sender = packet.sender;
        sender.active = null; sender.completed = false; sender.blocked = false;
        sender.worker.postMessage({ kind: 'sent' });
    }
    function pump(): void {
        // One caller delivery at a time. A full queue retains at most one blocked send per worker.
        while (!delivering) {
            const packet = queue.shift() ?? blocked.shift();
            if (packet === undefined) break;
            const effects = applyCampaignEvent(lifecycle, { kind: 'Receive', jobId: packet.job.jobId });
            if (!packet.accepted) accept(packet);
            if (effects[0]?.kind === 'Deliver') {
                delivering = true;
                parentPort.postMessage({ kind: 'deliver', job: packet.job });
            } else if (effects[0]?.kind !== 'Discard') throw new Error('invalid campaign receipt');
        }
        while (blocked.length && queue.length < config.resultBuffer) {
            const packet = blocked.shift()!;
            queue.push(packet); accept(packet);
        }
        publishProbe(); finish();
    }
    function finish(): void {
        if (spawning || finalized || delivering || queue.length || blocked.length || workers.some(worker => !worker.exited)) return;
        // Joined worker faults are observed after report draining. They do not disable healthy delivery.
        for (const worker of workers) {
            if (worker.error !== undefined || !worker.done) applyCampaignEvent(lifecycle, { kind: 'WorkerFailed', error: new ModelError(`campaign worker failed: ${worker.error ?? 'worker exited without completing its protocol'}`), lostWork: true });
        }
        applyCampaignEvent(lifecycle, { kind: 'Finish' });
        finalized = true;
        const report = lifecycle.state.report;
        const termination = report.termination;
        const wire = termination.kind === 'CallbackError' || termination.kind === 'WorkerError' ? { ...report, termination: { kind: termination.kind, error: campaignPanicMessage(termination.error) } } : report;
        parentPort.postMessage({ kind: 'report', report: wire });
        parentPort.close();
    }
    parentPort.on('message', (message: any) => {
        if (message.kind !== 'sinkReturned' || !delivering) throw new Error('invalid campaign sink acknowledgement');
        applyCampaignEvent(lifecycle, { kind: 'SinkReturned', jobId: message.jobId, error: message.error === null ? null : new ModelError(message.error) });
        delivering = false; pump();
    });
    const stopped = control.stop();
    if (stopped) applyCampaignEvent(lifecycle, { kind: 'Claim', stop: stopped });
    else for (let index = 0; index < Math.min(config.workers, config.jobs); index++) {
        let worker: any;
        try { worker = new Worker(new URL('./node_campaign_worker.js', import.meta.url), { workerData: { role: 'job', config, guidance, module, data, deadline, cancellation: buffer } }); }
        catch (error) {
            applyCampaignEvent(lifecycle, { kind: 'WorkerFailed', error: new ModelError(`unable to start campaign worker: ${campaignPanicMessage(error)}`), lostWork: false });
            break;
        }
        const sender: Sender = { worker, active: null, completed: false, blocked: false, done: false, exited: false };
        workers.push(sender);
        worker.on('error', (error: unknown) => { sender.error = campaignPanicMessage(error); });
        worker.on('exit', (code: number) => {
            sender.exited = true;
            if (code !== 0 && sender.error === undefined) sender.error = `exit ${code}`;
            pump();
        });
        worker.on('message', (message: any) => {
            switch (message.kind) {
                case 'claim': {
                    if (sender.active !== null || sender.done) throw new Error('invalid worker claim');
                    const effects = applyCampaignEvent(lifecycle, { kind: 'Claim', stop: control.stop() });
                    if (effects.length === 0) { deniedCount++; worker.postMessage({ kind: 'stop' }); }
                    else { sender.active = effects[0].jobId; worker.postMessage({ kind: 'run', jobId: sender.active }); }
                    break;
                }
                case 'complete':
                    if (sender.active !== message.jobId || sender.completed) throw new Error('invalid worker completion');
                    applyCampaignEvent(lifecycle, { kind: 'Complete', jobId: message.jobId, summary: message.summary });
                    completedCount++;
                    sender.completed = true; worker.postMessage({ kind: 'completed' }); break;
                case 'send': {
                    if (sender.active !== message.job.jobId || !sender.completed || sender.blocked) throw new Error('invalid worker send');
                    sender.blocked = true;
                    sendCount++;
                    blocked.push({ sender, job: message.job, accepted: false });
                    pump(); break;
                }
                case 'done':
                    if (sender.active !== null) throw new Error('worker ended with an active send');
                    sender.done = true; break;
                default: throw new Error('invalid campaign worker message');
            }
            publishProbe();
        });
    }
    spawning = false; publishProbe(); finish();
}
