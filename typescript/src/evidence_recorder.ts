/** Causal phase journal coordinated atomically with the exact runtime recorder. */
import { ModelError } from './errors.js';
import { natural } from './core.js';
import { type RunConfig, DEFAULT_BINARY_LIMITS } from './binary.js';
import { Recorder, observeCheckedRetaining, retainedOutputCount, type RecorderOptions } from './runtime_recorder.js';
import { requireCheckedTurnHeader, type CheckedInitial, type CheckedTurn } from './checked.js';
import { resolveEvidenceLimits, captureProvenanceForRecorder, captureProvenance, captureContext, measureProvenance, measureStepEvidence, type EvidenceLimits, type EvidenceLimitOptions, type Provenance, type CausalContext, type StepEvidence, type TraceV2 } from './evidence.js';
const U64 = (1n << 64n) - 1n;
function u64(v: bigint, label: string): bigint { if (typeof v !== 'bigint' || v < 0n || v > U64)
    throw new ModelError(`${label} must be u64 bigint`); return v; }
function modelError(e: unknown): ModelError { if (e instanceof ModelError)
    return e; let text = 'evidence preparation failed'; try {
    text = e instanceof Error ? e.message : String(e);
}
catch { } return new ModelError(text); }
interface Entry {
    step: StepEvidence;
    bytes: number;
}
/** Host labels assert provenance; they are not authenticated or executed. */
export class EvidenceRecorder<S, I, O> {
    #recorder: Recorder<S, I, O>;
    #provenance: Provenance;
    #origin: bigint;
    #entries: Entry[] = [];
    #start = 0;
    #metadataBytes: bigint;
    #links = 0n;
    #limits: EvidenceLimits;
    #busy = false;
    #consumed = false;
    constructor(initial: CheckedInitial<S, I, O>, origin: bigint, config: RunConfig, options: RecorderOptions, provenance: Provenance, overrides: EvidenceLimitOptions = {}) {
        this.#origin = u64(origin, 'sequence origin');
        const { maxSteps = 1024, maxRetainedBytes = BigInt(DEFAULT_BINARY_LIMITS.max_total_bytes) } = options;
        u64(maxRetainedBytes, 'maxRetainedBytes');
        natural(maxSteps, 'maxSteps');
        this.#limits = resolveEvidenceLimits(overrides);
        const overhead = 32n + BigInt(this.#limits.maxMetadataBytes);
        if (overhead > U64)
            throw new ModelError('evidence reservation overflow');
        const budget = maxRetainedBytes < BigInt(this.#limits.trace.max_total_bytes) ? maxRetainedBytes : BigInt(this.#limits.trace.max_total_bytes);
        if (budget < overhead)
            throw new ModelError('evidence metadata reservation exceeds total limit');
        this.#provenance = captureProvenanceForRecorder(provenance, this.#limits);
        this.#metadataBytes = 12n + BigInt(measureProvenance(this.#provenance, this.#limits));
        if (this.#metadataBytes > BigInt(this.#limits.maxMetadataBytes))
            throw new ModelError('metadata budget exceeded');
        this.#recorder = Recorder.withCheckedInitialAt(initial, origin, config, { maxSteps, maxRetainedBytes: budget - overhead, limits: this.#limits.trace });
    }
    #available(): void { if (this.#consumed)
        throw new ModelError('runtime evidence recorder ownership was transferred'); }
    #checkpoint(): bigint { const checkpoint = this.#origin + this.#recorder.evictedSteps(); if (checkpoint > U64)
        throw new ModelError('checkpoint overflow'); return checkpoint; }
    observe<A, B, C>(token: CheckedTurn<A, B, C>, causal: CausalContext): void {
        this.#available();
        if (this.#recorder.isFrozen())
            throw new ModelError('runtime evidence recorder is frozen; export its retained trace');
        if (this.#busy)
            throw new ModelError('runtime evidence recorder observation is already in progress');
        this.#busy = true;
        try {
            const header = requireCheckedTurnHeader(token), context = captureContext(causal, header.sequence, this.#limits);
            natural(header.stateCheckCount, 'stateCheckCount', 0, 0xffffffff);
            const step: StepEvidence = { sequence: header.sequence, stateCheckCount: header.stateCheckCount, causal: context }, size = measureStepEvidence(step, this.#limits);
            let bytes = this.#metadataBytes + BigInt(size), links = this.#links + BigInt(context.causes.length), minimum = 0;
            for (let index = this.#start; index < this.#entries.length && (bytes > BigInt(this.#limits.maxMetadataBytes) || links > BigInt(this.#limits.maxTotalCauses)); index++) {
                const old = this.#entries[index];
                bytes -= BigInt(old.bytes);
                links -= BigInt(old.step.causal.causes.length);
                minimum++;
            }
            if (bytes > BigInt(this.#limits.maxMetadataBytes) || links > BigInt(this.#limits.maxTotalCauses))
                throw new ModelError('candidate phase metadata exceeds retention limits');
            const checkpoint = this.#checkpoint();
            for (const origin of context.causes) {
                if (origin.sequence > checkpoint) {
                    const count = retainedOutputCount(this.#recorder, origin.sequence);
                    if (count === null || origin.output >= count)
                        throw new ModelError('causal origin outside retained output range');
                }
            }
            // Allocate the whole prospective journal before trace commit. Advancing its
            // head after the actual trace evictions requires no additional allocation.
            const journal = this.#entries.slice(this.#start).concat({ step, bytes: size }), previous = this.#recorder.evictedSteps();
            observeCheckedRetaining(this.#recorder, token, minimum);
            const actual = this.#recorder.evictedSteps() - previous;
            // Recorder fit evicts only its old retained entries, whose count equals
            // journal.length - 1; conversion and the following head move are exact.
            const dropped = Number(actual);
            let committedBytes = this.#metadataBytes + BigInt(size), committedLinks = this.#links + BigInt(context.causes.length);
            for (let index = 0; index < dropped; index++) {
                committedBytes -= BigInt(journal[index].bytes);
                committedLinks -= BigInt(journal[index].step.causal.causes.length);
            }
            this.#entries = journal;
            this.#start = dropped;
            this.#metadataBytes = committedBytes;
            this.#links = committedLinks;
        }
        catch (e) {
            throw this.#recorder.stopWithError(modelError(e));
        }
        finally {
            this.#busy = false;
        }
    }
    isFrozen(): boolean { this.#available(); return this.#recorder.isFrozen(); }
    snapshot(): TraceV2 {
        this.#available();
        const trace = this.#recorder.snapshot(), checkpointAfter = this.#checkpoint();
        return { trace, checkpointAfter, provenance: captureProvenance(this.#provenance, this.#limits), steps: this.#entries.slice(this.#start).map(({ step }) => ({ sequence: step.sequence, stateCheckCount: step.stateCheckCount, causal: captureContext(step.causal, step.sequence, this.#limits) })) };
    }
    intoTrace(): TraceV2 {
        this.#available();
        if (this.#busy)
            throw new ModelError('runtime evidence recorder observation is already in progress');
        const checkpointAfter = this.#checkpoint(), steps = this.#entries.slice(this.#start).map(e => e.step), provenance = this.#provenance, trace = this.#recorder.intoTrace();
        this.#consumed = true;
        this.#entries = [];
        this.#provenance = { run: '', epoch: 0n, manifest: null, source: null, environment: [] };
        return { trace, checkpointAfter, provenance, steps };
    }
}
