/** Candidate Rust audit binary v1. Structural decoding never establishes replay or check phases. */
import { type Metadata, type Check, type Disposition, type Trace, type TraceStep, type Termination, scalar } from './core.js';
export interface RunConfig {
    readonly strategy: string;
    readonly seed: bigint | null;
    readonly parameters: readonly (readonly [
        string,
        string
    ])[];
}
export interface AuditTrace extends Trace {
    readonly config: RunConfig;
}
export interface BinaryLimits {
    readonly max_total_bytes: number;
    readonly max_frame_bytes: number;
    readonly max_blob_bytes: number;
    readonly max_string_bytes: number;
    readonly max_steps: number;
    readonly max_checks_per_step: number;
    readonly max_outputs_per_step: number;
    readonly max_parameters: number;
    readonly max_items: number;
}
export const DEFAULT_BINARY_LIMITS: BinaryLimits = Object.freeze({ max_total_bytes: 67108864, max_frame_bytes: 8388608,
    max_blob_bytes: 4194304, max_string_bytes: 65536, max_steps: 100000, max_checks_per_step: 4096,
    max_outputs_per_step: 4096, max_parameters: 1024, max_items: 250000 });
export type BinaryErrorKind = 'invalid_data' | 'truncated' | 'unsupported_version' | 'checksum_mismatch' | 'limit_exceeded' | 'io';
export class BinaryError extends Error {
    constructor(readonly kind: BinaryErrorKind, readonly detail: string | null = null) { super(`${kind}${detail === null ? '' : ': ' + detail}`); this.name = 'BinaryError'; }
}
/** read fills any nonempty prefix of target, returns zero only at EOF; errors propagate with io context. */
export interface AuditReader {
    read(target: Uint8Array): number;
}
/** write consumes the supplied bytes synchronously; no flush/close/fsync is implied. */
export interface AuditWriter {
    write(bytes: Uint8Array): void;
}
const HEADER = Uint8Array.of(83, 84, 76, 69, 83, 83, 26, 10, 1, 0, 0, 0), U32 = 0xffffffff, U64 = (1n << 64n) - 1n;
const fail = (kind: BinaryErrorKind, detail: string | null = null): never => { throw new BinaryError(kind, detail); };
function integer(n: number, label: string, max = Number.MAX_SAFE_INTEGER): number {
    if (typeof n !== 'number' || !Number.isSafeInteger(n) || n < 0 || n > max)
        fail('invalid_data', label);
    return n;
}
function limits(value: Partial<BinaryLimits>): BinaryLimits {
    for (const key of Object.keys(value))
        if (!Object.hasOwn(DEFAULT_BINARY_LIMITS, key))
            fail('invalid_data', 'unknown binary limit');
    const l = { ...DEFAULT_BINARY_LIMITS, ...value };
    for (const [name, n] of Object.entries(l))
        integer(n, name);
    return l;
}
function bound(n: number, maximum: number, label: string): number {
    if (n > maximum)
        fail('limit_exceeded', label);
    return n;
}
function add(a: number, b: number): number { return integer(a + b, 'length overflow'); }
function textLength(value: string): number {
    try {
        scalar(value);
    }
    catch {
        return fail('invalid_data', 'invalid Unicode scalar');
    }
    let n = 0;
    for (const c of value) {
        const cp = c.codePointAt(0)!;
        n = add(n, cp < 128 ? 1 : cp < 2048 ? 2 : cp < 65536 ? 3 : 4);
    }
    return n;
}
export function auditCRC32(bytes: Uint8Array): number {
    let crc = 0xffffffff;
    for (const byte of bytes) {
        crc ^= byte;
        for (let n = 0; n < 8; n++)
            crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
    return (crc ^ 0xffffffff) >>> 0;
}
/** Capture fields once before validation/encoding, so getters cannot change wire variants between passes.
 * Blob storage is read synchronously; conversions separately copy every byte, including Node Buffers.
 * Collection quotas are applied before allocating the captured arrays. */
function capture(t: AuditTrace, l: BinaryLimits): AuditTrace {
    let items = 0;
    const object = (x: unknown): Record<string, any> => {
        if (x === null || typeof x !== 'object')
            return fail('invalid_data', 'expected record');
        return x as Record<string, any>;
    };
    const arrayMap = <T>(x: unknown, max: number, label: string, map: (value: any) => T): T[] => {
        if (!Array.isArray(x))
            return fail('invalid_data', 'expected array');
        const length = bound(integer(x.length, 'collection count', label === 'steps' ? Number.MAX_SAFE_INTEGER : U32), max, label);
        items = bound(add(items, length), l.max_items, 'aggregate items');
        const out: T[] = [];
        // Do not invoke a caller's overridden iterator or reread a Proxy length.
        for (let i = 0; i < length; i++)
            out.push(map(x[i]));
        return out;
    };
    const blob = (x: unknown): Uint8Array => {
        if (!(x instanceof Uint8Array))
            return fail('invalid_data', 'expected bytes');
        const proto = Object.getPrototypeOf(Uint8Array.prototype);
        const get = (key: string) => Object.getOwnPropertyDescriptor(proto, key)!.get!.call(x);
        const length = bound(integer(get('length'), 'blob wire length', U32), l.max_blob_bytes, 'blob bytes');
        // Intrinsic getters avoid Buffer/subclass accessors reporting a false size.
        return new Uint8Array(get('buffer'), get('byteOffset'), length);
    };
    const cs = (x: unknown): Check[] => arrayMap(x, l.max_checks_per_step, 'checks per step', v => {
        const c = object(v);
        return { id: c.id, status: c.status, details: c.details };
    });
    try {
        const a = object(t), m = object(a.metadata), c = object(a.config);
        const metadata: Metadata = { name: m.name, model_version: m.model_version, properties_version: m.properties_version, codec_version: m.codec_version, build: m.build };
        const config: RunConfig = { strategy: c.strategy, seed: c.seed, parameters: arrayMap(c.parameters, l.max_parameters, 'parameters', v => {
                if (!Array.isArray(v) || v.length !== 2)
                    return fail('invalid_data', 'parameter pair');
                return [v[0], v[1]] as const;
            }) };
        const initialState = blob(a.initialState), initialChecks = cs(a.initialChecks);
        const steps: TraceStep[] = arrayMap(a.steps, l.max_steps, 'steps', v => { const s = object(v), d = object(s.disposition); return { input: blob(s.input), disposition: { kind: d.kind, reason: d.reason }, outputs: arrayMap(s.outputs, l.max_outputs_per_step, 'outputs per step', o => blob(o)), postState: blob(s.postState), checks: cs(s.checks) }; });
        return { metadata, config, initialState, initialChecks, steps, termination: a.termination, error: a.error };
    }
    catch (e) {
        if (e instanceof BinaryError)
            throw e;
        return fail('invalid_data', e instanceof Error ? e.message : String(e));
    }
}
function copyConfig(c: RunConfig): RunConfig { return { strategy: c.strategy, seed: c.seed, parameters: c.parameters.map(p => [p[0], p[1]] as const) }; }
function copyTrace(t: Trace): Trace { return { metadata: { ...t.metadata }, initialState: new Uint8Array(t.initialState), initialChecks: t.initialChecks.map(c => ({ ...c })), steps: t.steps.map(s => ({ input: new Uint8Array(s.input), disposition: { ...s.disposition }, outputs: s.outputs.map(o => new Uint8Array(o)), postState: new Uint8Array(s.postState), checks: s.checks.map(c => ({ ...c })) })), termination: t.termination, error: t.error }; }
/** Configuration is mandatory; portable evidence cannot supply or infer it. */
export function auditTraceFromPortable(trace: Trace, config: RunConfig, overrides: Partial<BinaryLimits> = {}): AuditTrace {
    const l = limits(overrides), t = capture({ ...trace, config }, l);
    preflight(t, l);
    return { ...copyTrace(t), config: copyConfig(t.config) };
}
/** Returning configuration alongside the portable shape makes the narrowing explicit and lossless. */
export function auditTraceToPortable(trace: AuditTrace, overrides: Partial<BinaryLimits> = {}): {
    trace: Trace;
    config: RunConfig;
} {
    const l = limits(overrides), t = capture(trace, l);
    preflight(t, l);
    return { trace: copyTrace(t), config: copyConfig(t.config) };
}
function preflight(t: AuditTrace, l: BinaryLimits): number[] {
    let items = 0;
    const sizes: number[] = [];
    const item = (n: number) => { items = bound(add(items, n), l.max_items, 'aggregate items'); };
    const string = (s: string) => add(4, bound(integer(textLength(s), 'string wire length', U32), l.max_string_bytes, 'string bytes'));
    const blob = (b: Uint8Array) => {
        if (!(b instanceof Uint8Array))
            return fail('invalid_data', 'expected bytes');
        return add(4, bound(integer(b.length, 'blob wire length', U32), l.max_blob_bytes, 'blob bytes'));
    };
    const count = (a: readonly unknown[], maximum: number, label: string) => {
        if (!Array.isArray(a))
            return fail('invalid_data', 'expected array');
        const n = bound(integer(a.length, 'collection wire count', U32), maximum, label);
        item(n);
        return n;
    };
    const checks = (cs: readonly Check[]) => {
        count(cs, l.max_checks_per_step, 'checks per step');
        let n = 4;
        for (const c of cs) {
            n = add(n, add(string(c.id), 1));
            if (c.status === 'passed') {
                if (c.details !== '')
                    fail('invalid_data', 'passed check reason');
            }
            else if (c.status === 'failed' || c.status === 'skipped')
                n = add(n, string(c.details));
            else
                fail('invalid_data', 'check status tag');
        }
        return n;
    };
    const m = t.metadata;
    let run = string(m.name);
    for (const v of [m.model_version, m.properties_version, m.codec_version]) {
        integer(v, 'metadata version', U32);
        run = add(run, 4);
    }
    run = add(run, string(m.build));
    run = add(run, add(string(t.config.strategy), 1));
    if (t.config.seed !== null) {
        if (typeof t.config.seed !== 'bigint' || t.config.seed < 0n || t.config.seed > U64)
            fail('invalid_data', 'seed must be u64 bigint or null');
        run = add(run, 8);
    }
    count(t.config.parameters, l.max_parameters, 'parameters');
    run = add(run, 4);
    for (const p of t.config.parameters) {
        if (!Array.isArray(p) || p.length !== 2)
            fail('invalid_data', 'parameter pair');
        run = add(run, add(string(p[0]), string(p[1])));
    }
    run = add(run, add(blob(t.initialState), checks(t.initialChecks)));
    sizes.push(run);
    if (!Array.isArray(t.steps))
        fail('invalid_data', 'expected steps');
    bound(integer(t.steps.length, 'step count'), l.max_steps, 'steps');
    for (const s of t.steps) {
        item(1);
        let n = add(8, add(blob(s.input), 1));
        if (s.disposition.kind === 'accepted') {
            if (s.disposition.reason !== '')
                fail('invalid_data', 'accepted disposition reason');
        }
        else if (s.disposition.kind === 'rejected' || s.disposition.kind === 'ignored')
            n = add(n, string(s.disposition.reason));
        else
            fail('invalid_data', 'disposition tag');
        count(s.outputs, l.max_outputs_per_step, 'outputs per step');
        n = add(n, 4);
        for (const o of s.outputs)
            n = add(n, blob(o));
        n = add(n, add(blob(s.postState), checks(s.checks)));
        sizes.push(n);
    }
    let end = 9;
    if (t.termination === 'model_error')
        end = add(end, string(t.error));
    else {
        if (!['completed', 'property_failed', 'step_limit', 'interrupted'].includes(t.termination))
            fail('invalid_data', 'termination tag');
        if (t.error !== '')
            fail('invalid_data', 'error text requires model_error');
    }
    sizes.push(end);
    let total = HEADER.length;
    for (const n of sizes) {
        bound(integer(n, 'frame wire length', U32), l.max_frame_bytes, 'frame bytes');
        total = add(total, add(9, n));
        bound(total, l.max_total_bytes, 'total bytes');
    }
    return sizes;
}
class Encoder {
    p = 0;
    readonly view: DataView;
    constructor(readonly bytes: Uint8Array) { this.view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength); }
    u8(n: number) { this.bytes[this.p++] = n; }
    u32(n: number) { this.view.setUint32(this.p, n, true); this.p += 4; }
    u64(n: bigint) { this.view.setBigUint64(this.p, n, true); this.p += 8; }
    blob(b: Uint8Array) { this.u32(b.length); this.bytes.set(b, this.p); this.p += b.length; }
    str(s: string) { this.blob(new TextEncoder().encode(s)); }
    checks(cs: readonly Check[]) {
        this.u32(cs.length);
        for (const c of cs) {
            this.str(c.id);
            this.u8(c.status === 'passed' ? 0 : c.status === 'failed' ? 1 : 2);
            if (c.status !== 'passed')
                this.str(c.details);
        }
    }
}
/** Entire structure/length/item validation precedes allocation and publication. */
export function writeAuditTrace(t: AuditTrace, overrides: Partial<BinaryLimits> = {}): Uint8Array {
    const l = limits(overrides);
    t = capture(t, l);
    const sizes = preflight(t, l), total = sizes.reduce((n, s) => n + s + 9, HEADER.length), out = new Uint8Array(total), e = new Encoder(out);
    out.set(HEADER);
    e.p = HEADER.length;
    const frame = (tag: number, size: number, body: () => void) => {
        const start = e.p;
        e.u8(tag);
        e.u32(size);
        body();
        if (e.p !== start + 5 + size)
            fail('invalid_data', 'trace changed during encoding');
        e.u32(auditCRC32(out.subarray(start, e.p)));
    };
    frame(1, sizes[0], () => {
        const m = t.metadata;
        e.str(m.name);
        e.u32(m.model_version);
        e.u32(m.properties_version);
        e.u32(m.codec_version);
        e.str(m.build);
        e.str(t.config.strategy);
        e.u8(t.config.seed === null ? 0 : 1);
        if (t.config.seed !== null)
            e.u64(t.config.seed);
        e.u32(t.config.parameters.length);
        for (const [k, v] of t.config.parameters) {
            e.str(k);
            e.str(v);
        }
        e.blob(t.initialState);
        e.checks(t.initialChecks);
    });
    t.steps.forEach((s, i) => frame(2, sizes[i + 1], () => {
        e.u64(BigInt(i));
        e.blob(s.input);
        e.u8(s.disposition.kind === 'accepted' ? 0 : s.disposition.kind === 'rejected' ? 1 : 2);
        if (s.disposition.kind !== 'accepted')
            e.str(s.disposition.reason);
        e.u32(s.outputs.length);
        for (const o of s.outputs)
            e.blob(o);
        e.blob(s.postState);
        e.checks(s.checks);
    }));
    frame(3, sizes.at(-1)!, () => {
        e.u64(BigInt(t.steps.length));
        e.u8(['completed', 'property_failed', 'step_limit', 'interrupted', 'model_error'].indexOf(t.termination));
        if (t.termination === 'model_error')
            e.str(t.error);
    });
    return out;
}
export function writeAuditTraceTo(t: AuditTrace, writer: AuditWriter, overrides: Partial<BinaryLimits> = {}): void {
    const bytes = writeAuditTrace(t, overrides);
    try {
        writer.write(bytes);
    }
    catch (e) {
        fail('io', e instanceof Error ? e.message : String(e));
    }
}
class Stream {
    total = 0;
    private offset = 0;
    constructor(readonly source: Uint8Array | AuditReader, readonly l: BinaryLimits) { }
    read(target: Uint8Array): number {
        if (this.source instanceof Uint8Array) {
            const n = Math.min(target.length, this.source.length - this.offset);
            target.set(this.source.subarray(this.offset, this.offset + n));
            this.offset += n;
            return n;
        }
        try {
            const n = this.source.read(target);
            if (!Number.isSafeInteger(n) || n < 0 || n > target.length)
                fail('invalid_data', 'reader byte count');
            return n;
        }
        catch (e) {
            if (e instanceof BinaryError)
                throw e;
            return fail('io', e instanceof Error ? e.message : String(e));
        }
    }
    exact(n: number): Uint8Array {
        bound(add(this.total, n), this.l.max_total_bytes, 'total bytes');
        const b = new Uint8Array(n);
        let p = 0;
        while (p < n) {
            const got = this.read(b.subarray(p));
            if (got === 0)
                fail('truncated');
            p += got;
        }
        this.total += n;
        return b;
    }
}
class Payload {
    p = 0;
    readonly view: DataView;
    constructor(readonly bytes: Uint8Array, readonly l: BinaryLimits, readonly items: (n: number) => void) { this.view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength); }
    need(n: number) {
        if (n > this.bytes.length - this.p)
            fail('invalid_data', 'field exceeds frame');
    }
    u8() { this.need(1); return this.bytes[this.p++]; }
    u32() { this.need(4); const n = this.view.getUint32(this.p, true); this.p += 4; return n; }
    u64() { this.need(8); const n = this.view.getBigUint64(this.p, true); this.p += 8; return n; }
    blob(maximum = this.l.max_blob_bytes, label = 'blob bytes') { const n = bound(this.u32(), maximum, label); this.need(n); const b = this.bytes.slice(this.p, this.p + n); this.p += n; return b; }
    str() {
        const b = this.blob(this.l.max_string_bytes, 'string bytes');
        try {
            return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(b);
        }
        catch {
            return fail('invalid_data', 'invalid UTF-8');
        }
    }
    count(maximum: number, label: string, width: number) {
        const n = bound(this.u32(), maximum, label);
        this.items(n);
        if (n > Math.floor((this.bytes.length - this.p) / width))
            fail('invalid_data', 'collection count exceeds frame');
        return n;
    }
    checks(): Check[] {
        const n = this.count(this.l.max_checks_per_step, 'checks per step', 5), out: Check[] = [];
        for (let i = 0; i < n; i++) {
            const id = this.str(), tag = this.u8();
            if (tag > 2)
                fail('invalid_data', 'check status tag');
            out.push({ id, status: tag === 0 ? 'passed' : tag === 1 ? 'failed' : 'skipped', details: tag === 0 ? '' : this.str() });
        }
        return out;
    }
    done() {
        if (this.p !== this.bytes.length)
            fail('invalid_data', 'trailing record fields');
    }
}
export function readAuditTrace(source: Uint8Array | AuditReader, overrides: Partial<BinaryLimits> = {}): AuditTrace {
    if (!(source instanceof Uint8Array) && (!source || typeof source.read !== 'function'))
        fail('invalid_data', 'expected bytes or reader');
    const l = limits(overrides), stream = new Stream(source, l), header = stream.exact(12);
    if (!header.subarray(0, 8).every((b, i) => b === HEADER[i]))
        fail('invalid_data', 'file magic');
    const hv = new DataView(header.buffer);
    if (hv.getUint16(8, true) !== 1)
        fail('unsupported_version', String(hv.getUint16(8, true)));
    if (hv.getUint16(10, true) !== 0)
        fail('invalid_data', 'unsupported header flags');
    let totalItems = 0;
    const items = (n: number) => { totalItems = bound(add(totalItems, n), l.max_items, 'aggregate items'); };
    let metadata: Metadata | undefined, config: RunConfig | undefined, initialState: Uint8Array | undefined, initialChecks: Check[] | undefined;
    const steps: TraceStep[] = [];
    for (;;) {
        const fh = stream.exact(5), tag = fh[0], size = new DataView(fh.buffer).getUint32(1, true);
        bound(size, l.max_frame_bytes, 'frame bytes');
        bound(add(stream.total, add(size, 4)), l.max_total_bytes, 'total bytes');
        const bytes = stream.exact(size), crc = stream.exact(4), frame = new Uint8Array(5 + size);
        frame.set(fh);
        frame.set(bytes, 5);
        if (auditCRC32(frame) !== new DataView(crc.buffer).getUint32(0, true))
            fail('checksum_mismatch');
        const p = new Payload(bytes, l, items);
        if (metadata === undefined && tag !== 1)
            fail('invalid_data', 'first record must be run metadata');
        if (tag === 1 && metadata === undefined) {
            metadata = { name: p.str(), model_version: p.u32(), properties_version: p.u32(), codec_version: p.u32(), build: p.str() };
            const strategy = p.str(), present = p.u8();
            if (present > 1)
                fail('invalid_data', 'seed tag');
            const seed = present === 1 ? p.u64() : null, n = p.count(l.max_parameters, 'parameters', 8), parameters: [
                string,
                string
            ][] = [];
            for (let i = 0; i < n; i++)
                parameters.push([p.str(), p.str()]);
            config = { strategy, seed, parameters };
            initialState = p.blob();
            initialChecks = p.checks();
            p.done();
        }
        else if (tag === 2) {
            bound(steps.length + 1, l.max_steps, 'steps');
            items(1);
            if (p.u64() !== BigInt(steps.length))
                fail('invalid_data', 'transition index');
            const input = p.blob(), dt = p.u8();
            if (dt > 2)
                fail('invalid_data', 'disposition tag');
            const disposition: Disposition = { kind: dt === 0 ? 'accepted' : dt === 1 ? 'rejected' : 'ignored', reason: dt === 0 ? '' : p.str() }, n = p.count(l.max_outputs_per_step, 'outputs per step', 4), outputs: Uint8Array[] = [];
            for (let i = 0; i < n; i++)
                outputs.push(p.blob());
            const postState = p.blob(), checks = p.checks();
            p.done();
            steps.push({ input, disposition, outputs, postState, checks });
        }
        else if (tag === 3) {
            if (p.u64() !== BigInt(steps.length))
                fail('invalid_data', 'footer transition count');
            const tt = p.u8();
            if (tt > 4)
                fail('invalid_data', 'termination tag');
            const termination: Termination = (['completed', 'property_failed', 'step_limit', 'interrupted', 'model_error'] as const)[tt], error = tt === 4 ? p.str() : '';
            p.done();
            if (stream.read(new Uint8Array(1)) !== 0)
                fail('invalid_data', 'trailing data after footer');
            return { metadata: metadata!, config: config!, initialState: initialState!, initialChecks: initialChecks!, steps, termination, error };
        }
        else
            fail('invalid_data', 'unknown or misplaced record');
    }
}
/** Internal shared structural accounting; no wire output allocation or model callbacks. */
export function resolveBinaryLimits(overrides: Partial<BinaryLimits> = {}): BinaryLimits { return limits(overrides); }
export function validateAuditTrace(trace: AuditTrace, overrides: Partial<BinaryLimits> = {}): void { const l = limits(overrides); preflight(capture(trace, l), l); }
/** Validate then detach retained fields and bytes for replay ownership. */
export function snapshotAuditTrace(trace: AuditTrace, overrides: Partial<BinaryLimits> = {}): AuditTrace {
    const l = limits(overrides), t = capture(trace, l);
    preflight(t, l);
    return { ...copyTrace(t), config: copyConfig(t.config) };
}
export function auditRunLayout(metadata: Metadata, config: RunConfig, initialState: Uint8Array, initialChecks: readonly Check[], overrides: Partial<BinaryLimits>): {
    bytes: number;
    items: number;
} {
    const l = limits(overrides), t = capture({ metadata, config, initialState, initialChecks, steps: [], termination: 'completed', error: '' }, l), sizes = preflight(t, l);
    return { bytes: sizes[0] + 9, items: t.config.parameters.length + t.initialChecks.length };
}
/** Internal exact validated wire measurement without constructing encoded payload bytes. */
export function auditTraceWireLength(trace: AuditTrace, overrides: Partial<BinaryLimits> = {}): number {
    const l = limits(overrides), t = capture(trace, l);
    return preflight(t, l).reduce((n, s) => n + s + 9, HEADER.length);
}
/** Internal stable field capture, retaining byte views until a consumer completes envelope preflight. */
export function captureAuditTraceForEvidence(trace: AuditTrace, overrides: Partial<BinaryLimits> = {}): AuditTrace {
    const l = limits(overrides), t = capture(trace, l);
    preflight(t, l);
    return t;
}
/** Internal ordered v1 sizing for known-rejected evidence inputs. Indexed lazy arrays
 * prevent an unbounded capture journal; no payload bytes or source collections are copied.
 * Rejected-source accessors may be read again after the separate semantic scan.
 */
export function preflightRejectedEvidenceTrace(source: AuditTrace, overrides: Partial<BinaryLimits> = {}): number {
    const l = limits(overrides);
    const record = (v: unknown): Record<string, any> => { if (v === null || typeof v !== 'object')
        return fail('invalid_data', 'expected record'); return v as Record<string, any>; };
    const bytes = (v: unknown): Uint8Array => {
        if (!(v instanceof Uint8Array))
            return fail('invalid_data', 'expected bytes');
        const p = Object.getPrototypeOf(Uint8Array.prototype), get = (key: string) => Object.getOwnPropertyDescriptor(p, key)!.get!.call(v);
        return new Uint8Array(get('buffer'), get('byteOffset'), get('length'));
    };
    const array = <T>(value: unknown, convert: (v: unknown) => T): T[] => {
        if (!Array.isArray(value))
            return fail('invalid_data', 'expected array');
        const length = integer(value.length, 'collection wire count', U32);
        return new Proxy([] as T[], { get(_target, key) {
                if (key === 'length')
                    return length;
                if (key === Symbol.iterator)
                    return function* () { for (let i = 0; i < length; i++)
                        yield convert(value[i]); };
                return Reflect.get(value, key, value);
            } });
    };
    const checks = (value: unknown) => array(value, v => { const c = record(v); return { id: c.id, status: c.status, details: c.details } as Check; });
    const t = record(source), m = record(t.metadata), c = record(t.config);
    const trace: AuditTrace = {
        metadata: { name: m.name, model_version: m.model_version, properties_version: m.properties_version, codec_version: m.codec_version, build: m.build },
        config: { strategy: c.strategy, seed: c.seed, parameters: array(c.parameters, v => { if (!Array.isArray(v) || v.length !== 2)
                return fail('invalid_data', 'parameter pair'); return [v[0], v[1]] as const; }) },
        initialState: bytes(t.initialState), initialChecks: checks(t.initialChecks),
        steps: array(t.steps, v => { const s = record(v), d = record(s.disposition); return { input: bytes(s.input), disposition: { kind: d.kind, reason: d.reason }, outputs: array(s.outputs, bytes), postState: bytes(s.postState), checks: checks(s.checks) }; }),
        termination: t.termination, error: t.error
    };
    return preflight(trace, l).reduce((n, s) => n + s + 9, HEADER.length);
}
