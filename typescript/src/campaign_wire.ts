import type { Count } from './counts.js';
/** Internal host transport. Only application input values use the explicit input adapter. */
import { ModelError } from './errors.js';
import { campaignPanicMessage, type JobReport } from './campaign.js';
import { synchronous } from './guided_sync.js';

export function mapCampaignInputs<I, W, C extends Count = number>(job: JobReport<I, C>, map: (input: I) => W): JobReport<W, C> {
    if (job.outcome.kind !== 'Completed') return { ...job, outcome: job.outcome };
    const source = job.outcome.report;
    const failure = source.failure === null ? null : { ...source.failure, inputs: source.failure.inputs.map(input => synchronous(map(input))) };
    if ('guidance' in source) {
        const guidance = { ...source.guidance, corpus: source.guidance.corpus.map(entry => ({ ...entry, inputs: entry.inputs.map(input => synchronous(map(input))) })) };
        return { ...job, outcome: { kind: 'Completed', report: { ...source, failure, guidance } } };
    }
    return { ...job, outcome: { kind: 'Completed', report: { ...source, failure } } };
}
export function packCampaignJob<I, W, C extends Count = number>(job: JobReport<I, C>, encode: (input: I) => W): unknown {
    const mapped = mapCampaignInputs(job, encode);
    if (mapped.outcome.kind === 'FactoryError' || mapped.outcome.kind === 'ModelError') {
        return { ...mapped, outcome: { ...mapped.outcome, error: campaignPanicMessage(mapped.outcome.error) } };
    }
    return mapped;
}
export function unpackCampaignJob<I, W, C extends Count = number>(wire: any, decode: (wire: W) => I): JobReport<I, C> {
    const job = wire as JobReport<W, C>;
    if (job.outcome.kind === 'FactoryError' || job.outcome.kind === 'ModelError') job.outcome = { ...job.outcome, error: new ModelError(String((wire as any).outcome.error)) };
    return mapCampaignInputs(job, decode);
}
