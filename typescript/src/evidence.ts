/** Canonical causal envelopes. Host provenance is asserted data, never authentication. */
import { type CodecModel, type Check, type ReplayReport, ModelError, natural, scalar, validateRecording, validateMetadata } from './core.js';
import { consumeWriterPromiseRejection } from './binary.js';
import { type AuditTrace, type AuditReader, type AuditWriter, type BinaryLimits, resolveBinaryLimits, BinaryError, snapshotAuditTrace as snapshotAuditTraceRaw, captureAuditTraceForEvidence as captureAuditTraceRaw, auditTraceWireLength as auditTraceWireLengthRaw, writeAuditTrace as writeAuditTraceRaw, readAuditTrace as readAuditTraceRaw, auditCRC32, preflightRejectedEvidenceTrace } from './binary.js';
import { type ReplayOptions, replayWithObservationsBounded, ReplayError } from './replay.js';
function binary<T>(fn: () => T): T {
    try {
        return fn();
    }
    catch (e) {
        if (e instanceof BinaryError)
            throw new ModelError(e.kind === 'limit_exceeded' ? `trace exceeds ${e.detail} limit` : e.message);
        throw e;
    }
}
const readAuditTrace = (...args: Parameters<typeof readAuditTraceRaw>) => binary(() => readAuditTraceRaw(...args));
const writeAuditTrace = (...args: Parameters<typeof writeAuditTraceRaw>) => binary(() => writeAuditTraceRaw(...args));
const snapshotAuditTrace = (...args: Parameters<typeof snapshotAuditTraceRaw>) => binary(() => snapshotAuditTraceRaw(...args));
const captureAuditTraceForEvidence = (...args: Parameters<typeof captureAuditTraceRaw>) => binary(() => captureAuditTraceRaw(...args));
const auditTraceWireLength = (...args: Parameters<typeof auditTraceWireLengthRaw>) => binary(() => auditTraceWireLengthRaw(...args));
export interface Provenance {
    readonly run: string;
    readonly epoch: bigint;
    readonly manifest: string | null;
    readonly source: string | null;
    readonly environment: readonly (readonly [
        string,
        string
    ])[];
}
export interface OutputOrigin {
    readonly sequence: bigint;
    readonly output: number;
}
export interface CausalContext {
    readonly machine: string | null;
    readonly generation: bigint | null;
    readonly correlation: string | null;
    readonly logicalTime: bigint | null;
    readonly causes: readonly OutputOrigin[];
}
export interface StepEvidence {
    readonly sequence: bigint;
    readonly stateCheckCount: number;
    readonly causal: CausalContext;
}
export interface TraceV2 {
    readonly trace: AuditTrace;
    readonly checkpointAfter: bigint;
    readonly provenance: Provenance;
    readonly steps: readonly StepEvidence[];
}
export type Artifact = {
    readonly kind: 'v1';
    readonly trace: AuditTrace;
} | {
    readonly kind: 'v2';
    readonly evidence: TraceV2;
};
export interface EvidenceLimits {
    readonly trace: BinaryLimits;
    readonly maxMetadataBytes: number;
    readonly maxLabels: number;
    readonly maxLabelBytes: number;
    readonly maxCausesPerStep: number;
    readonly maxTotalCauses: number;
}
export type EvidenceLimitOptions = Partial<Omit<EvidenceLimits, 'trace'>> & {
    readonly trace?: Partial<BinaryLimits>;
};
export const DEFAULT_EVIDENCE_LIMITS: EvidenceLimits = Object.freeze({ trace: Object.freeze(resolveBinaryLimits()), maxMetadataBytes: 4194304, maxLabels: 1024, maxLabelBytes: 4096, maxCausesPerStep: 32, maxTotalCauses: 100000 });
const U64 = (1n << 64n) - 1n, U32 = 0xffffffff;
const fail = (s: string): never => { throw new ModelError(s); };
export function resolveEvidenceLimits(source: EvidenceLimitOptions = {}): EvidenceLimits {
    const { trace = {}, maxMetadataBytes = 4194304, maxLabels = 1024, maxLabelBytes = 4096, maxCausesPerStep = 32, maxTotalCauses = 100000 } = source;
    for (const [k, v] of Object.entries({ maxMetadataBytes, maxLabels, maxLabelBytes, maxCausesPerStep, maxTotalCauses }))
        natural(v, k);
    return Object.freeze({ trace: Object.freeze(resolveBinaryLimits(trace)), maxMetadataBytes, maxLabels, maxLabelBytes, maxCausesPerStep, maxTotalCauses });
}
function u64(v: bigint, name = 'u64'): bigint {
    if (typeof v !== 'bigint' || v < 0n || v > U64)
        fail(`invalid ${name}`);
    return v;
}
function utf8Length(value: string, max: number, message: string): number {
    if (typeof value !== 'string')
        return fail('expected string');
    if (value.length > max)
        fail(message);
    scalar(value);
    let n = 0;
    for (const c of value) {
        const cp = c.codePointAt(0)!;
        n += cp < 128 ? 1 : cp < 2048 ? 2 : cp < 65536 ? 3 : 4;
        if (n > max)
            fail(message);
    }
    return n;
}
function text(value: string, l: EvidenceLimits): string { utf8Length(value, Math.min(U32, l.maxLabelBytes), 'evidence label byte limit'); return value; }
function optionalText(value: string | null, l: EvidenceLimits): string | null { return value === null ? null : text(value, l); }
function arrayLength(value: unknown, max: number, message: string): number {
    if (!Array.isArray(value))
        return fail('expected evidence array');
    const n = value.length;
    natural(n, 'collection length', 0, U32);
    if (n > max)
        fail(message);
    return n;
}
export function captureProvenance(value: Provenance, limits: EvidenceLimits): Provenance {
    const { run, epoch, manifest, source, environment } = value;
    const n = arrayLength(environment, limits.maxLabels, 'evidence environment label limit');
    const out: ([
        string,
        string
    ])[] = [], names = new Set<string>();
    text(run, limits);
    u64(epoch);
    optionalText(manifest, limits);
    optionalText(source, limits);
    let size = 4 + utf8Length(run, U32, 'length overflow') + 8 + 1 + (manifest === null ? 0 : 4 + utf8Length(manifest, U32, 'length overflow')) + 1 + (source === null ? 0 : 4 + utf8Length(source, U32, 'length overflow')) + 4;
    if (size > limits.maxMetadataBytes)
        fail('encoded byte limit');
    for (let i = 0; i < n; i++) {
        const p = environment[i];
        if (!Array.isArray(p) || p.length !== 2)
            fail('invalid environment pair');
        const a = p[0], b = p[1];
        text(a, limits);
        text(b, limits);
        if (!a || names.has(a))
            fail('empty or duplicate evidence environment name');
        names.add(a);
        size += 8 + utf8Length(a, U32, 'length overflow') + utf8Length(b, U32, 'length overflow');
        if (size > limits.maxMetadataBytes)
            fail('encoded byte limit');
        out.push(Object.freeze([a, b]) as [
            string,
            string
        ]);
    }
    return Object.freeze({ run, epoch, manifest, source, environment: Object.freeze(out) });
}
export function captureContext(value: CausalContext, sequence: bigint, limits: EvidenceLimits, validateOrigins = true): CausalContext {
    const { machine, generation, correlation, logicalTime, causes } = value;
    u64(sequence);
    optionalText(machine, limits);
    optionalText(correlation, limits);
    if (generation !== null)
        u64(generation);
    if (logicalTime !== null)
        u64(logicalTime);
    const n = arrayLength(causes, limits.maxCausesPerStep, 'causal links per step limit'), out: OutputOrigin[] = [], seen = new Set<string>();
    let size = 4 + (machine === null ? 0 : 4 + utf8Length(machine, U32, 'length overflow')) + (generation === null ? 0 : 8) + (correlation === null ? 0 : 4 + utf8Length(correlation, U32, 'length overflow')) + (logicalTime === null ? 0 : 8) + 4;
    if (size + 12 > limits.maxMetadataBytes)
        fail('encoded byte limit');
    for (let i = 0; i < n; i++) {
        const { sequence: s, output } = causes[i];
        u64(s);
        natural(output, 'output index', 0, U32);
        const key = `${s}:${output}`;
        if (validateOrigins && (s === 0n || s >= sequence || seen.has(key)))
            fail('causal origin must be unique and precede its input');
        seen.add(key);
        size += 12;
        if (size + 12 > limits.maxMetadataBytes)
            fail('encoded byte limit');
        out.push(Object.freeze({ sequence: s, output }));
    }
    return Object.freeze({ machine, generation, correlation, logicalTime, causes: Object.freeze(out) });
}
export function emptyCausalContext(): CausalContext { return Object.freeze({ machine: null, generation: null, correlation: null, logicalTime: null, causes: Object.freeze([]) }); }
/** Measure before allocating encoded bytes; optional labels have one-byte tags. */
export function measureProvenance(p: Provenance, l: EvidenceLimits): number {
    let n = 4 + utf8Length(p.run, l.maxLabelBytes, 'evidence label byte limit') + 8 + 1 + 1 + 4;
    for (const s of [p.manifest, p.source])
        if (s !== null)
            n += 4 + utf8Length(s, l.maxLabelBytes, 'evidence label byte limit');
    const count = arrayLength(p.environment, l.maxLabels, 'evidence environment label limit');
    for (let i = 0; i < count; i++) {
        const pair = p.environment[i];
        n += 8 + utf8Length(pair[0], l.maxLabelBytes, 'evidence label byte limit') + utf8Length(pair[1], l.maxLabelBytes, 'evidence label byte limit');
        if (n > l.maxMetadataBytes)
            fail('encoded byte limit');
    }
    if (n > l.maxMetadataBytes)
        fail('encoded byte limit');
    return n;
}
export function measureStepEvidence(s: StepEvidence, l: EvidenceLimits): number {
    const c = s.causal;
    let n = 12 + 4 + 4;
    for (const v of [c.machine, c.correlation])
        if (v !== null)
            n += 4 + utf8Length(v, l.maxLabelBytes, 'evidence label byte limit');
    if (c.generation !== null)
        n += 8;
    if (c.logicalTime !== null)
        n += 8;
    const count = arrayLength(c.causes, l.maxCausesPerStep, 'causal links per step limit');
    n += count * 12;
    if (n > l.maxMetadataBytes)
        fail('encoded byte limit');
    return n;
}
function captureMetadata(value: TraceV2, l: EvidenceLimits): Omit<TraceV2, 'trace'> {
    const { checkpointAfter, provenance, steps } = value;
    u64(checkpointAfter);
    const p = captureProvenance(provenance, l), n = arrayLength(steps, l.trace.max_steps, 'phase evidence step limit');
    let bytes = 12 + measureProvenance(p, l), causes = 0;
    const out: StepEvidence[] = [];
    if (bytes > l.maxMetadataBytes)
        fail('encoded byte limit');
    for (let i = 0; i < n; i++) {
        const { sequence, stateCheckCount, causal } = steps[i];
        u64(sequence);
        natural(stateCheckCount, 'state check count', 0, U32);
        const c = captureContext(causal, sequence, l, false);
        causes += c.causes.length;
        if (causes > l.maxTotalCauses)
            fail('total causal links limit');
        const row = Object.freeze({ sequence, stateCheckCount, causal: c });
        bytes += measureStepEvidence(row, l);
        if (bytes > l.maxMetadataBytes)
            fail('encoded byte limit');
        out.push(row);
    }
    return Object.freeze({ checkpointAfter, provenance: p, steps: Object.freeze(out) });
}
/** Lazy borrowed field cache: reads native accessors once, never copies a payload buffer.
 * Array facades retain only inspected fields; ownership is acquired after all admission checks.
 */
function borrowedView<T>(source: T): {
    view: T;
    discardCache: () => void;
} {
    let views = new WeakMap<object, unknown>(), enabled = true;
    const allFields = new Set<Map<PropertyKey, {
        ok: boolean;
        value: unknown;
    }>>();
    const wrap = (value: unknown): unknown => {
        if (!enabled || value === null || typeof value !== 'object' || value instanceof Uint8Array)
            return value;
        const previous = views.get(value);
        if (previous !== undefined)
            return previous;
        const fields = new Map<PropertyKey, {
            ok: boolean;
            value: unknown;
        }>();
        allFields.add(fields);
        const view = new Proxy(Array.isArray(value) ? [] : {}, { get(_target, key) {
                if (!enabled)
                    return Reflect.get(value, key, value);
                const cached = fields.get(key);
                if (cached) {
                    if (!cached.ok)
                        throw cached.value;
                    return cached.value;
                }
                try {
                    const result = wrap(Reflect.get(value, key, value));
                    fields.set(key, { ok: true, value: result });
                    return result;
                }
                catch (error) {
                    fields.set(key, { ok: false, value: error });
                    throw error;
                }
            } });
        views.set(value, view);
        return view;
    };
    return { view: wrap(source) as T, discardCache() {
            enabled = false;
            for (const fields of allFields)
                fields.clear();
            allFields.clear();
            views = new WeakMap();
        } };
}
/** Recorder construction encodes provenance before semantic label validation in Rust. */
export function captureProvenanceForRecorder(value: Provenance, l: EvidenceLimits): Provenance {
    const p = borrowedView(value).view;
    let size = 0;
    const add = (n: number) => {
        if (n > l.maxMetadataBytes - size)
            fail('encoded byte limit');
        size += n;
    };
    const string = (v: string) => { add(4); const remaining = l.maxMetadataBytes - size; add(utf8Length(v, Math.min(U32, remaining), 'encoded byte limit')); };
    const optional = (v: string | null) => {
        add(1);
        if (v !== null)
            string(v);
    };
    string(p.run);
    u64(p.epoch);
    add(8);
    optional(p.manifest);
    optional(p.source);
    const count = arrayLength(p.environment, U32, 'environment count');
    add(4);
    if (BigInt(count) * 8n > BigInt(l.maxMetadataBytes - size))
        fail('encoded byte limit');
    for (let i = 0; i < count; i++) {
        const pair = p.environment[i];
        if (!Array.isArray(pair) || pair.length !== 2)
            fail('invalid environment pair');
        string(pair[0]);
        string(pair[1]);
    }
    if (size + 12 > l.maxMetadataBytes)
        fail('metadata budget exceeded');
    validateProvenanceBorrowed(p, l);
    return captureProvenance(p, l);
}
function validateProvenanceBorrowed(p: Provenance, l: EvidenceLimits): void {
    text(p.run, l);
    u64(p.epoch);
    optionalText(p.manifest, l);
    optionalText(p.source, l);
    const length = arrayLength(p.environment, l.maxLabels, 'evidence environment label limit'), names = new Set<string>();
    for (let i = 0; i < length; i++) {
        const pair = p.environment[i];
        if (!Array.isArray(pair) || pair.length !== 2)
            fail('invalid environment pair');
        const name = pair[0], value = pair[1];
        text(name, l);
        text(value, l);
        if (!name || names.has(name))
            fail('empty or duplicate evidence environment name');
        names.add(name);
    }
}
/** Semantic scan deliberately allocates no temporary check or step collections. */
function validateRecordingBorrowed(t: AuditTrace, l: BinaryLimits, discardCache: () => void): boolean {
    const initial = t.initialChecks, steps = t.steps;
    const length = arrayLength(steps, U32, 'step count'), initialCount = arrayLength(initial, U32, 'check count');
    let quota: string | null = initialCount > l.max_checks_per_step ? 'checks per step' : initialCount > l.max_items ? 'aggregate items' : length > l.max_steps ? 'steps' : initialCount + length > l.max_items ? 'aggregate items' : null;
    let items = initialCount + length;
    if (quota !== null)
        discardCache();
    const hasFailure = (checks: readonly Check[], initialBatch = false) => {
        const n = initialBatch ? initialCount : arrayLength(checks, U32, 'check count');
        if (!initialBatch && quota === null) {
            items += n;
            if (n > l.max_checks_per_step)
                quota = 'checks per step';
            else if (items > l.max_items)
                quota = 'aggregate items';
            if (quota !== null)
                discardCache();
        }
        for (let i = 0; i < n; i++)
            if (checks[i].status === 'failed')
                return true;
        return false;
    };
    const initialFailed = hasFailure(initial, true);
    if (initialFailed && length)
        fail('trace continues after initial property failure');
    let lastFailed = false;
    for (let i = 0; i < length; i++) {
        const bad = hasFailure(steps[i].checks);
        if (i < length - 1 && bad)
            fail('trace continues after property failure');
        lastFailed = bad;
    }
    if ((initialFailed || lastFailed) !== (t.termination === 'property_failed'))
        fail('trace termination disagrees with recorded checks');
    return quota !== null;
}
/** Exact structural byte measurement, independent of causal semantic quotas. */
function rawMetadataSize(v: TraceV2, l: EvidenceLimits): number {
    let size = 0;
    const add = (n: number) => {
        if (n > l.maxMetadataBytes - size)
            fail('encoded byte limit');
        size += n;
    };
    const string = (s: string) => {
        add(4);
        if (typeof s !== 'string')
            return fail('expected string');
        const remaining = l.maxMetadataBytes - size;
        if (s.length > remaining)
            fail('encoded byte limit');
        const n = utf8Length(s, Math.min(U32, remaining), 'encoded byte limit');
        add(n);
    };
    const optionString = (s: string | null) => {
        add(1);
        if (s !== null)
            string(s);
    };
    const optionU64 = (n: bigint | null) => {
        add(1);
        if (n !== null) {
            u64(n);
            add(8);
        }
    };
    u64(v.checkpointAfter);
    add(8);
    const p = v.provenance;
    string(p.run);
    u64(p.epoch);
    add(8);
    optionString(p.manifest);
    optionString(p.source);
    const labels = arrayLength(p.environment, U32, 'environment count');
    add(4);
    for (let i = 0; i < labels; i++) {
        const pair = p.environment[i];
        string(pair[0]);
        string(pair[1]);
    }
    const steps = arrayLength(v.steps, U32, 'phase evidence count');
    add(4);
    // Every metadata row has at least twenty bytes, even with all options absent.
    if (BigInt(steps) * 20n > BigInt(l.maxMetadataBytes - size))
        fail('encoded byte limit');
    for (let i = 0; i < steps; i++) {
        const row = v.steps[i];
        u64(row.sequence);
        natural(row.stateCheckCount, 'state check count', 0, U32);
        add(12);
        const c = row.causal;
        optionString(c.machine);
        optionU64(c.generation);
        optionString(c.correlation);
        optionU64(c.logicalTime);
        const count = arrayLength(c.causes, U32, 'cause count');
        add(4);
        add(count * 12);
    }
    return size;
}
function validateContextBorrowed(c: CausalContext, sequence: bigint, l: EvidenceLimits): number {
    optionalText(c.machine, l);
    optionalText(c.correlation, l);
    if (c.generation !== null)
        u64(c.generation);
    if (c.logicalTime !== null)
        u64(c.logicalTime);
    const count = arrayLength(c.causes, l.maxCausesPerStep, 'causal links per step limit'), seen = new Set<string>();
    for (let i = 0; i < count; i++) {
        const origin = c.causes[i], s = u64(origin.sequence), output = origin.output;
        natural(output, 'output index', 0, U32);
        const key = `${s}:${output}`;
        if (s === 0n || s >= sequence || seen.has(key))
            fail('causal origin must be unique and precede its input');
        seen.add(key);
    }
    return count;
}
export function snapshotTraceV2(value: TraceV2, overrides: EvidenceLimitOptions = {}): TraceV2 {
    const l = resolveEvidenceLimits(overrides), borrowed = borrowedView(value), view = borrowed.view;
    // Match Rust's admission order without copying unadmitted blobs or phase journals.
    validateProvenanceBorrowed(view.provenance, l);
    const knownUnfit = validateRecordingBorrowed(view.trace, l.trace, borrowed.discardCache);
    if (knownUnfit) {
        binary(() => preflightRejectedEvidenceTrace(view.trace, l.trace));
        fail('unfit trace changed during validation');
    }
    const trace = captureAuditTraceForEvidence(view.trace, l.trace);
    const metadataBytes = rawMetadataSize(view, l), wireBytes = auditTraceWireLength(trace, l.trace);
    if (BigInt(wireBytes) + BigInt(metadataBytes) + 32n > BigInt(l.trace.max_total_bytes))
        fail('evidence total byte limit');
    const count = arrayLength(view.steps, U32, 'phase evidence count');
    if (count !== trace.steps.length)
        fail('phase evidence does not cover every recorded step');
    const checkpoint = u64(view.checkpointAfter);
    if (checkpoint + BigInt(count) > U64)
        fail('evidence sequence overflow');
    let causes = 0;
    for (let i = 0; i < count; i++) {
        const row = view.steps[i];
        if (row.sequence !== checkpoint + BigInt(i) + 1n || row.stateCheckCount > trace.steps[i].checks.length)
            fail('invalid phase evidence sequence or check boundary');
        causes += validateContextBorrowed(row.causal, row.sequence, l);
        if (causes > l.maxTotalCauses)
            fail('total causal links limit');
        const n = row.causal.causes.length;
        for (let j = 0; j < n; j++) {
            const origin = row.causal.causes[j];
            if (origin.sequence > checkpoint) {
                const source = trace.steps[Number(origin.sequence - checkpoint - 1n)];
                if (!source || origin.output >= source.outputs.length)
                    fail('causal origin output index outside retained evidence');
            }
        }
    }
    const metadata = captureMetadata(view, l);
    return { trace: snapshotAuditTrace(trace, l.trace), ...metadata };
}
export function validateTraceV2(value: TraceV2, overrides: EvidenceLimitOptions = {}): void { snapshotTraceV2(value, overrides); }
class MetadataWriter {
    p = 0;
    readonly bytes: Uint8Array;
    readonly view: DataView;
    constructor(size: number) { this.bytes = new Uint8Array(size); this.view = new DataView(this.bytes.buffer); }
    u8(n: number) { this.bytes[this.p++] = n; }
    u32(n: number) { this.view.setUint32(this.p, n, true); this.p += 4; }
    u64(n: bigint) { this.view.setBigUint64(this.p, n, true); this.p += 8; }
    str(s: string) { const b = new TextEncoder().encode(s); this.u32(b.length); this.bytes.set(b, this.p); this.p += b.length; }
    option<T>(v: T | null, write: (v: T) => void) {
        this.u8(v === null ? 0 : 1);
        if (v !== null)
            write(v);
    }
    provenance(p: Provenance) {
        this.str(p.run);
        this.u64(p.epoch);
        this.option(p.manifest, v => this.str(v));
        this.option(p.source, v => this.str(v));
        this.u32(p.environment.length);
        for (const [k, v] of p.environment) {
            this.str(k);
            this.str(v);
        }
    }
    context(c: CausalContext) {
        this.option(c.machine, v => this.str(v));
        this.option(c.generation, v => this.u64(v));
        this.option(c.correlation, v => this.str(v));
        this.option(c.logicalTime, v => this.u64(v));
        this.u32(c.causes.length);
        for (const o of c.causes) {
            this.u64(o.sequence);
            this.u32(o.output);
        }
    }
}
export function encodeEvidenceMetadata(v: TraceV2, l: EvidenceLimits): Uint8Array {
    const size = 12 + measureProvenance(v.provenance, l) + v.steps.reduce((n, s) => n + measureStepEvidence(s, l), 0);
    if (size > l.maxMetadataBytes)
        fail('encoded byte limit');
    const w = new MetadataWriter(size);
    w.u64(v.checkpointAfter);
    w.provenance(v.provenance);
    w.u32(v.steps.length);
    for (const s of v.steps) {
        w.u64(s.sequence);
        w.u32(s.stateCheckCount);
        w.context(s.causal);
    }
    if (w.p !== size)
        fail('metadata size changed');
    return w.bytes;
}
export function writeTraceV2(value: TraceV2, overrides: EvidenceLimitOptions = {}): Uint8Array {
    const l = resolveEvidenceLimits(overrides), v = snapshotTraceV2(value, l), metadata = encodeEvidenceMetadata(v, l), trace = writeAuditTrace(v.trace, l.trace), out = new Uint8Array(trace.length + metadata.length + 32), d = new DataView(out.buffer);
    out.set(Uint8Array.of(83, 84, 76, 69, 83, 83, 26, 10, 2, 0, 0, 0));
    d.setBigUint64(12, BigInt(trace.length), true);
    d.setBigUint64(20, BigInt(metadata.length), true);
    out.set(trace, 28);
    out.set(metadata, 28 + trace.length);
    d.setUint32(out.length - 4, auditCRC32(out.subarray(0, out.length - 4)), true);
    return out;
}
/** A bad writer return is detected after the call, which may already publish bytes.
 * A returned native Promise with a throwing species can leave its rejection unhandled. */
export function writeTraceV2To(value: TraceV2, writer: AuditWriter, limits: EvidenceLimitOptions = {}): void {
    const write = writer.write;
    if (typeof write !== 'function')
        return fail('invalid evidence writer');
    const bytes = writeTraceV2(value, limits), result: unknown = write.call(writer, bytes);
    if (result !== undefined) {
        consumeWriterPromiseRejection(result);
        fail('evidence writer must return undefined synchronously');
    }
}
/** Decoder accounting is independent of the embedded trace's item quota. */
class MetadataReader {
    p = 0;
    depth = 0;
    allocation: bigint;
    elements: bigint;
    work: bigint;
    readonly view: DataView;
    constructor(readonly bytes: Uint8Array, maximum: number) {
        if (bytes.length > maximum)
            fail('decode byte limit');
        this.allocation = BigInt(maximum) * 16n;
        this.elements = BigInt(maximum);
        this.work = BigInt(maximum) * 16n;
        this.view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    }
    value<T>(read: () => T): T {
        if (this.work === 0n)
            fail('decode work limit');
        this.work--;
        if (this.depth >= 16)
            fail('decode nesting limit');
        this.depth++;
        try {
            return read();
        }
        finally {
            this.depth--;
        }
    }
    take(n: number): Uint8Array {
        if (n > this.bytes.length - this.p)
            fail('truncated value');
        const b = this.bytes.subarray(this.p, this.p + n);
        this.p += n;
        return b;
    }
    collection(n: number, size: number): void {
        if (BigInt(n) > this.elements)
            fail('decode element limit');
        this.elements -= BigInt(n);
        const a = BigInt(n) * BigInt(size);
        if (a > this.allocation)
            fail('decode allocation limit');
        this.allocation -= a;
        if (BigInt(n) > this.work)
            fail('decode work limit');
    }
    u8(): number { return this.value(() => this.take(1)[0]); }
    u32(): number { return this.value(() => { this.take(4); return this.view.getUint32(this.p - 4, true); }); }
    u64(): bigint { return this.value(() => { this.take(8); return this.view.getBigUint64(this.p - 8, true); }); }
    str(): string {
        return this.value(() => {
            const n = this.u32();
            this.collection(n, 1);
            this.work -= BigInt(n);
            const bytes = this.take(n);
            try {
                return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
            }
            catch {
                return fail('invalid UTF-8');
            }
        });
    }
    option<T>(read: () => T): T | null {
        return this.value(() => {
            const tag = this.u8();
            if (tag === 0)
                return null;
            if (tag === 1)
                return read();
            return fail('invalid option tag');
        });
    }
    vector<T>(size: number, read: () => T): T[] {
        return this.value(() => {
            const n = this.u32();
            this.collection(n, size);
            const out: T[] = [];
            for (let i = 0; i < n; i++)
                out.push(read());
            return out;
        });
    }
    provenance(): Provenance {
        return this.value(() => ({ run: this.str(), epoch: this.u64(), manifest: this.option(() => this.str()), source: this.option(() => this.str()), environment: this.vector(48, () => this.value(() => [this.str(), this.str()] as [
                string,
                string
            ])) }));
    }
    context(): CausalContext { return this.value(() => ({ machine: this.option(() => this.str()), generation: this.option(() => this.u64()), correlation: this.option(() => this.str()), logicalTime: this.option(() => this.u64()), causes: this.vector(16, () => this.value(() => ({ sequence: this.u64(), output: this.u32() }))) })); }
    step(): StepEvidence { return this.value(() => ({ sequence: this.u64(), stateCheckCount: this.u32(), causal: this.context() })); }
}
function nativeView(value: Uint8Array): Uint8Array {
    if (!(value instanceof Uint8Array))
        fail('expected bytes');
    const p = Object.getPrototypeOf(Uint8Array.prototype), get = (k: string) => Object.getOwnPropertyDescriptor(p, k)!.get!.call(value);
    return new Uint8Array(get('buffer'), get('byteOffset'), get('length'));
}
function readBounded(source: Uint8Array | AuditReader, maximum: number): Uint8Array {
    if (source instanceof Uint8Array) {
        const view = nativeView(source);
        if (view.length > maximum)
            fail('evidence byte limit');
        return new Uint8Array(view);
    }
    const read = source.read;
    if (typeof read !== 'function')
        fail('invalid evidence reader');
    // Keep retained storage proportional to the bytes read, not to the number
    // of read calls: a reader is allowed to return one byte at a time.
    let bytes = new Uint8Array(0);
    let total = 0;
    while (true) {
        const target = new Uint8Array(Math.min(8192, maximum - total + 1)), n = read.call(source, target);
        natural(n, 'reader count', 0, target.length);
        if (n === 0)
            break;
        if (n > maximum - total)
            fail('evidence byte limit');
        const required = total + n;
        if (required > bytes.length) {
            const capacity = Math.max(required, Math.min(maximum, Math.max(8192, bytes.length * 2)));
            const grown = new Uint8Array(capacity);
            grown.set(bytes.subarray(0, total));
            bytes = grown;
        }
        bytes.set(target.subarray(0, n), total);
        total += n;
    }
    return bytes.length === total ? bytes : bytes.slice(0, total);
}
export function readArtifact(source: Uint8Array | AuditReader, overrides: EvidenceLimitOptions = {}): Artifact {
    const l = resolveEvidenceLimits(overrides), bytes = readBounded(source, l.trace.max_total_bytes), d = new DataView(bytes.buffer);
    if (bytes.length < 12 || ![83, 84, 76, 69, 83, 83, 26, 10].every((b, i) => bytes[i] === b))
        fail('truncated or invalid evidence header');
    const version = d.getUint16(8, true);
    if (version === 1)
        return { kind: 'v1', trace: readAuditTrace(bytes, l.trace) };
    if (version !== 2)
        fail('unsupported evidence version');
    if (bytes[10] !== 0 || bytes[11] !== 0 || bytes.length < 32)
        fail('invalid or truncated v2 header');
    const traceLength = d.getBigUint64(12, true), metaLength = d.getBigUint64(20, true);
    if (traceLength + metaLength + 32n !== BigInt(bytes.length) || metaLength > BigInt(l.maxMetadataBytes))
        fail('invalid evidence lengths or metadata limit');
    const end = bytes.length - 4;
    if (auditCRC32(bytes.subarray(0, end)) !== d.getUint32(end, true))
        fail('evidence checksum mismatch');
    const split = 28 + Number(traceLength), trace = readAuditTrace(bytes.subarray(28, split), l.trace), r = new MetadataReader(bytes.subarray(split, end), l.maxMetadataBytes);
    const v: TraceV2 = { trace, checkpointAfter: r.u64(), provenance: r.provenance(), steps: r.vector(120, () => r.step()) };
    if (r.p !== r.bytes.length)
        fail('trailing value bytes');
    return { kind: 'v2', evidence: snapshotTraceV2(v, l) };
}
export type EvidenceCheckPhase = 'initial_state' | 'state' | 'transition';
export function artifactTrace(artifact: Artifact): AuditTrace { return artifact.kind === 'v1' ? artifact.trace : artifact.evidence.trace; }
export function checkPhase(artifact: Artifact, sequence: number, index: number): EvidenceCheckPhase | null {
    if (!Number.isSafeInteger(sequence) || !Number.isSafeInteger(index) || sequence < 0 || index < 0)
        return null;
    const t = artifactTrace(artifact);
    if (sequence === 0)
        return index < t.initialChecks.length ? 'initial_state' : null;
    if (artifact.kind === 'v1' || sequence > t.steps.length || index >= t.steps[sequence - 1].checks.length)
        return null;
    const row = artifact.evidence.steps[sequence - 1];
    return row ? index < row.stateCheckCount ? 'state' : 'transition' : null;
}
export function replayTraceV2<S, I, O>(value: TraceV2, model: CodecModel<S, I, O>, options: ReplayOptions = {}, overrides: EvidenceLimitOptions = {}): ReplayReport {
    const capturedOptions = { allowBuildMismatch: options.allowBuildMismatch }, l = resolveEvidenceLimits(overrides), v = snapshotTraceV2(value, l);
    let mismatch: number | null = null;
    try {
        return replayWithObservationsBounded(model, v.trace, capturedOptions, l.trace, (observation, report) => {
            if (observation.kind === 'turn' && report.outcome === 'exact') {
                const sequence = observation.actual.sequence;
                if (v.steps[sequence - 1].stateCheckCount !== observation.actual.state_check_count) {
                    mismatch = sequence;
                    throw new ModelError('persisted check phase differs from replay');
                }
            }
        });
    }
    catch (e) {
        if (e instanceof ReplayError) {
            if (mismatch !== null)
                return { ...e.report, outcome: 'diverged', step: mismatch, field: 'check_phase', steps_verified: mismatch - 1, failure_reproduced: false };
            throw e.error;
        }
        throw e;
    }
}
/** Own admitted input before callbacks, but let the shared kernel enforce identity-first errors.
 * An unfit artifact retains only its metadata and a cached failure; no payload copy is attempted.
 */
function captureUpgradeTrace(source: AuditTrace, limits: BinaryLimits): AuditTrace {
    let metadata: AuditTrace['metadata'];
    try {
        metadata = validateMetadata(source.metadata);
    }
    catch (error) {
        return { get metadata(): AuditTrace['metadata'] { throw error; } } as AuditTrace;
    }
    try {
        const candidate = { metadata,
            get config() { return source.config; },
            get initialState() { return source.initialState; },
            get initialChecks() { return source.initialChecks; },
            get steps() { return source.steps; },
            get termination() { return source.termination; },
            get error() { return source.error; }
        };
        return snapshotAuditTrace(candidate, limits);
    }
    catch (error) {
        return { metadata, get config(): AuditTrace['config'] { throw error; } } as AuditTrace;
    }
}
export function verifyUpgrade<S, I, O>(model: CodecModel<S, I, O>, trace: AuditTrace, provenance: Provenance, checkpointAfter: bigint, options: ReplayOptions = {}, overrides: EvidenceLimitOptions = {}): TraceV2 {
    const capturedOptions = { allowBuildMismatch: options.allowBuildMismatch }, l = resolveEvidenceLimits(overrides), capturedTrace = captureUpgradeTrace(trace, l.trace), steps: StepEvidence[] = [];
    u64(checkpointAfter);
    let p: Provenance | undefined, provenanceError: unknown;
    let provenanceFailed = false;
    try {
        p = captureProvenance(provenance, l);
    }
    catch (e) {
        provenanceError = e;
        provenanceFailed = true;
    }
    let report: ReplayReport;
    try {
        report = replayWithObservationsBounded(model, capturedTrace, capturedOptions, l.trace, (observation) => {
            if (observation.kind === 'turn') {
                const sequence = checkpointAfter + BigInt(observation.actual.sequence);
                if (sequence > U64)
                    fail('evidence sequence overflow');
                const stateCheckCount = observation.actual.state_check_count;
                if (stateCheckCount === null)
                    return fail('phase check count unavailable');
                natural(stateCheckCount, 'phase check count', 0, U32);
                steps.push({ sequence, stateCheckCount, causal: emptyCausalContext() });
            }
        });
    }
    catch (e) {
        if (e instanceof ReplayError)
            throw e.error;
        throw e;
    }
    if (report.outcome !== 'exact')
        fail('v1 upgrade requires exact compatible replay');
    if (provenanceFailed)
        throw provenanceError;
    return snapshotTraceV2({ trace: capturedTrace, checkpointAfter, provenance: p!, steps }, l);
}
export interface ExportPolicy {
    readonly maxSteps?: number;
    readonly maxBytes?: number;
    readonly propertyIds?: readonly string[];
}
/** Nonreplayable diagnostic bytes; no payload, details, model identity, or causal labels survive. */
export function diagnosticJSON(artifact: Artifact, policy: ExportPolicy = {}): Uint8Array {
    const { maxSteps = 1000, maxBytes = 1048576, propertyIds = [] } = policy;
    natural(maxSteps, 'maxSteps');
    natural(maxBytes, 'maxBytes');
    const count = arrayLength(propertyIds, U32, 'property ID count'), ids = new Set<string>();
    for (let i = 0; i < count; i++) {
        const id = propertyIds[i];
        scalar(id);
        ids.add(id);
    }
    const kind = artifact.kind;
    if (kind !== 'v1' && kind !== 'v2')
        return fail('invalid artifact kind');
    const evidence = kind === 'v2' ? artifact.evidence : null;
    const t = kind === 'v1' ? artifact.trace : evidence!.trace;
    const checkpoint = evidence === null ? null : u64(evidence.checkpointAfter, 'checkpoint');
    const { termination, steps, initialChecks } = t;
    const stepCount = arrayLength(steps, U32, 'step count');
    const names = { completed: 'completed_sequence', property_failed: 'property_failure', step_limit: 'step_limit', interrupted: 'interrupted', model_error: 'model_error' };
    if (typeof termination !== 'string' || !Object.prototype.hasOwnProperty.call(names, termination))
        return fail('invalid trace termination');
    const terminalName = names[termination as keyof typeof names];
    const phaseRows = evidence === null ? null : evidence.steps;
    if (phaseRows !== null && !Array.isArray(phaseRows))
        return fail('invalid phase evidence array');
    const chunks: Uint8Array[] = [];
    let total = 0;
    const append = (text: string) => { const n = utf8Length(text, maxBytes - total, 'diagnostic export byte limit'); const bytes = new TextEncoder().encode(text); total += n; chunks.push(bytes); };
    const quote = (text: string) => {
        append('"');
        for (const c of text) {
            append(c === '"' ? '\\"' : c === '\\' ? '\\\\' : c.charCodeAt(0) <= 31 ? `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}` : c);
        }
        append('"');
    };
    append(`{"schema":"statelessness.diagnostic-export/1","replayable":false,"redaction_policy":"omit-payloads-v1","raw_payloads_included":false,"checkpoint_after":${checkpoint === null ? 'null' : checkpoint},"omitted_prefix":${checkpoint === null ? 'null' : checkpoint !== 0n},"termination":"${terminalName}","truncated":${stepCount > maxSteps},"observations":[`);
    for (let sequence = 0; sequence <= Math.min(stepCount, maxSteps); sequence++) {
        if (sequence)
            append(',');
        append(`{"sequence":${sequence},"checks":[`);
        const checks = sequence === 0 ? initialChecks : steps[sequence - 1].checks;
        const length = arrayLength(checks, U32, 'check count');
        let boundary: number | null = null;
        if (sequence !== 0 && phaseRows !== null && sequence <= phaseRows.length) {
            const row = phaseRows[sequence - 1];
            if (row !== undefined) {
                const value = row.stateCheckCount;
                natural(value, 'state check count', 0, U32);
                boundary = value;
            }
        }
        for (let i = 0; i < length; i++) {
            const { id, status } = checks[i];
            scalar(id);
            if (status !== 'passed' && status !== 'failed' && status !== 'skipped')
                return fail('invalid check status');
            const phase = sequence === 0 ? 'initial_state' : boundary === null ? 'unknown_legacy' : i < boundary ? 'state' : 'transition';
            if (i)
                append(',');
            append(`{"status":"${status}","phase":"${phase}","id":`);
            if (ids.has(id))
                quote(id);
            else
                append('null');
            append('}');
        }
        append(']}');
    }
    append(']}');
    const out = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
        out.set(chunk, offset);
        offset += chunk.length;
    }
    return out;
}
