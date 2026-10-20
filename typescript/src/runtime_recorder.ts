/** Bounded contiguous recording of application-executed transitions. Never executes a reducer. */
import { ModelError } from './errors.js';
import { VERSION, check, natural, scalar, disposition, validateMetadata, bytesEqual, checkObservedCountIntoExact, type CodecModel, type Check, type Metadata, type Transition, type TraceStep } from './core.js';
import { type AuditTrace, type AuditWriter, type BinaryLimits, type RunConfig, writeAuditTraceTo, DEFAULT_BINARY_LIMITS, BinaryError } from './binary.js';
import { resolveBinaryLimits, replayRunSize, replayStepPreflight, errorFooterReserve, truncatedErrorReason } from './audit_budget.js';
import { EncodeStorage, encodeModel } from './encoding.js';
import { withCheckSink, appendStateChecks } from './checks.js';
import { requireCheckedInitialForRecorder, requireCheckedTurnForRecorder, requireCheckedTurnHeader, type CheckedInitial, type CheckedTurn } from './checked.js';
export interface RecorderOptions {
    readonly maxSteps?: number;
    readonly maxRetainedBytes?: bigint;
    readonly limits?: Partial<BinaryLimits>;
}
interface Options {
    maxSteps: number;
    maxRetainedBytes: bigint;
    limits: BinaryLimits;
}
interface Observation {
    step: TraceStep;
    boundary: number;
    bytes: number;
    items: number;
}
const U64 = (1n << 64n) - 1n, BOOT = Symbol('recorder checked initialization');
const u64 = (v: bigint, name: string) => { if (typeof v !== 'bigint' || v < 0n || v > U64)
    throw new ModelError(`${name} must be u64 bigint`); return v; };
const failed = (cs: readonly Check[]) => cs.some(c => c.status === 'failed');
const copies = (cs: readonly Check[]) => cs.map(c => check(c.id, c.status, c.details));
const contextual = (stage: string, e: unknown) => { let text = 'callback failed'; try {
    text = e instanceof BinaryError && e.kind === 'limit_exceeded' ? `trace exceeds ${e.detail} limit` : e instanceof Error ? e.message : String(e);
}
catch { } return new ModelError(`runtime ${stage}: ${text}`); };
function call<T>(stage: string, f: () => T): T { try {
    return f();
}
catch (e) {
    throw contextual(stage, e);
} }
function captureOptions(source: RecorderOptions): Options {
    const { maxSteps = 1024, maxRetainedBytes, limits: overrides = {} } = source, limits = resolveBinaryLimits(overrides);
    natural(maxSteps, 'maxSteps');
    if (maxSteps === 0 || maxSteps > limits.max_steps)
        throw new ModelError('runtime recorder max_steps must be positive and within trace limits');
    const maximum = maxRetainedBytes === undefined ? BigInt(DEFAULT_BINARY_LIMITS.max_total_bytes) : u64(maxRetainedBytes, 'maxRetainedBytes');
    if (maximum === 0n)
        throw new ModelError('runtime recorder byte budget must be positive');
    return { maxSteps, maxRetainedBytes: maximum, limits };
}
function configCopy(source: RunConfig, options: Options, origin: bigint, stamp = true): RunConfig {
    const { strategy, seed } = source, values = source.parameters, length = values.length;
    natural(length, 'parameter count', 0, 0xffffffff);
    scalar(strategy);
    if (seed !== null)
        u64(seed, 'seed');
    const parameters: [
        string,
        string
    ][] = [];
    for (let i = 0; i < length; i++) {
        const p = values[i];
        if (!Array.isArray(p) || p.length !== 2)
            throw new ModelError('invalid runtime parameter pair');
        const [k, v] = p;
        scalar(k);
        scalar(v);
        if (k.startsWith('stateless.monitor.') || k.startsWith('stateless.engine.'))
            throw new ModelError(`${k.startsWith('stateless.monitor.') ? 'stateless.monitor.' : 'stateless.engine.'} parameters are reserved`);
        parameters.push([k, v]);
    }
    if (!stamp)
        return { strategy, seed, parameters };
    parameters.push(['stateless.engine.version', VERSION], ['stateless.engine.build', 'native-typescript-v1']);
    if (origin !== 0n)
        parameters.push(['stateless.monitor.sequence_origin', origin.toString()]);
    for (const [k, v] of [['max_steps', options.maxSteps.toString()], ['max_retained_bytes', options.maxRetainedBytes.toString()], ['checking', 'full'], ['observed_steps', '0'], ['evicted_steps', '0'], ['checkpoint_after_sequence', '0']])
        parameters.push([`stateless.monitor.${k}`, v]);
    return { strategy, seed, parameters };
}
function stringBytes(s: string, maximum: number): number { scalar(s); let n = 0; for (const c of s) {
    const cp = c.codePointAt(0)!;
    n += cp < 128 ? 1 : cp < 2048 ? 2 : cp < 65536 ? 3 : 4;
    if (n > maximum)
        throw new ModelError('runtime recording limit: string bytes');
} return n; }
/** Native ownership: snapshots detach bytes/checks; intoTrace consumes this recorder. */
export class Recorder<S, I, O> {
    #metadata!: Metadata;
    #config!: RunConfig;
    #initial!: Uint8Array;
    #checks!: Check[];
    #steps: Observation[] = [];
    #options!: Options;
    #observed = 0n;
    #origin = 0n;
    #evicted = 0n;
    #termination: AuditTrace['termination'] = 'interrupted';
    #error = '';
    #runBytes = 0;
    #stepBytes = 0n;
    #stepItems = 0n;
    #runItems = 0;
    #reserved = 0;
    #consumed = false;
    #busy = false;
    #encoding = new EncodeStorage();
    #checkStorage: Check[] = [];
    constructor(model: CodecModel<S, I, O>, state: S, config: RunConfig, options?: RecorderOptions);
    constructor(model: CodecModel<S, I, O>, state: S, config: RunConfig, options: RecorderOptions = {}, bootstrap?: {
        seal: symbol;
        checks: readonly Check[];
        origin: bigint;
    }) {
        this.#options = captureOptions(options);
        const checked = bootstrap?.seal === BOOT ? bootstrap : undefined;
        this.#origin = checked ? u64(checked.origin, 'sequence origin') : 0n;
        this.#config = configCopy(config, this.#options, this.#origin);
        const owned = call('snapshot checkpoint', () => model.cloneState(state));
        this.#metadata = call('metadata', () => validateMetadata(model.metadata()));
        this.#reserved = errorFooterReserve(this.#options.limits);
        // Reserve footer framing before invoking a codec, as the Rust recorder does.
        if (this.#reserved - 9 > this.#options.limits.max_frame_bytes)
            throw new ModelError('runtime recording limit: frame bytes');
        this.#initial = call('encode checkpoint', () => encodeModel(model, 'state', owned, this.#encoding, this.#blobLimit()));
        if (checked) {
            this.#checkedBatch(checked.checks, this.#options.maxRetainedBytes);
            this.#checks = copies(checked.checks);
        }
        else {
            call('check checkpoint', () => withCheckSink(this.#checkStorage, sink => appendStateChecks(model, owned, sink)));
            this.#checks = copies(this.#checkStorage);
        }
        const run = call('recording limit', () => replayRunSize(this.#metadata, this.#config, this.#initial, this.#checks, this.#options.limits));
        this.#runBytes = run.bytes;
        this.#runItems = run.items;
        const terminal = failed(this.#checks);
        this.#validate(run.bytes, BigInt(run.items), 0n, 0, terminal ? 18 : this.#reserved);
        if (terminal)
            this.#termination = 'property_failed';
    }
    static withCheckedInitial<S, I, O>(token: CheckedInitial<S, I, O>, config: RunConfig, options: RecorderOptions = {}): Recorder<S, I, O> { return Recorder.withCheckedInitialAt(token, 0n, config, options); }
    static withCheckedInitialAt<S, I, O>(token: CheckedInitial<S, I, O>, origin: bigint, config: RunConfig, options: RecorderOptions = {}): Recorder<S, I, O> {
        u64(origin, 'sequence origin');
        const selected = captureOptions(options), callerConfig = configCopy(config, selected, origin, false);
        const c = requireCheckedInitialForRecorder(token);
        return Reflect.construct(Recorder, [c.model, c.state, callerConfig, selected, { seal: BOOT, checks: c.checks, origin }]) as Recorder<S, I, O>;
    }
    #available(): void { if (this.#consumed)
        throw new ModelError('runtime recorder ownership was transferred'); }
    #active(): void { this.#available(); if (this.isFrozen())
        throw new ModelError('runtime recorder is frozen; export its retained trace'); if (this.#busy)
        throw new ModelError('runtime recorder observation is already in progress'); }
    #blobLimit(): number { return Number([BigInt(this.#options.limits.max_blob_bytes), 0xffffffffn, BigInt(this.#options.limits.max_frame_bytes), this.#options.maxRetainedBytes, BigInt(this.#options.limits.max_total_bytes)].reduce((a, b) => a < b ? a : b)); }
    #counts(config: RunConfig, observed: bigint, evicted: bigint): RunConfig { const parameters = config.parameters.map(([k, v]) => [k, v] as [
        string,
        string
    ]), offset = parameters.length - 3; parameters[offset][1] = observed.toString(); parameters[offset + 1][1] = evicted.toString(); parameters[offset + 2][1] = evicted.toString(); return { ...config, parameters }; }
    #validate(runBytes: number, items: bigint, stepBytes: bigint, count: number, footer: number): void {
        const l = this.#options.limits, total = 12n + BigInt(runBytes) + stepBytes + BigInt(footer);
        if (total > BigInt(l.max_total_bytes))
            throw new ModelError('runtime recording limit: trace exceeds total bytes limit');
        if (items > BigInt(l.max_items))
            throw new ModelError('runtime recording limit: trace exceeds aggregate items limit');
        if (count > l.max_steps)
            throw new ModelError('runtime recording limit: trace exceeds steps limit');
        if (total > this.#options.maxRetainedBytes)
            throw new ModelError('runtime recording exceeds retained byte budget');
        if (count > this.#options.maxSteps)
            throw new ModelError('runtime recording exceeds retained step budget');
        if (footer - 9 > l.max_frame_bytes)
            throw new ModelError('runtime recording limit: frame bytes');
    }
    #checkedBatch(checks: readonly Check[], maximum: bigint): void {
        const l = this.#options.limits, cap = BigInt(Math.min(0xffffffff, l.max_frame_bytes));
        if (checks.length > Math.min(0xffffffff, l.max_checks_per_step))
            throw new ModelError('runtime recording limit: checks per step');
        if (checks.length > l.max_items)
            throw new ModelError('runtime recording limit: trace exceeds aggregate items limit');
        let size = 4n;
        const add = (n: number) => { size += BigInt(n); if (size > cap)
            throw new ModelError('runtime recording limit: frame bytes'); };
        add(0);
        for (const c of checks) {
            add(4 + stringBytes(c.id, Math.min(0xffffffff, l.max_string_bytes)));
            add(1);
            if (c.status === 'passed') {
                if (c.details !== '')
                    throw new ModelError('runtime recording limit: passed check reason');
            }
            else if (c.status === 'failed' || c.status === 'skipped')
                add(4 + stringBytes(c.details, Math.min(0xffffffff, l.max_string_bytes)));
            else
                throw new ModelError('runtime recording limit: check status');
        }
        if (size > maximum)
            throw new ModelError('runtime recording limit: retained bytes');
    }
    #freeze(e: unknown): ModelError { const previous = this.#busy; this.#busy = true; try {
        const error = e instanceof ModelError ? e : contextual(e instanceof BinaryError ? 'recording limit' : 'observation', e);
        if (this.#termination !== 'interrupted')
            return error;
        this.#termination = 'model_error';
        try {
            this.#error = truncatedErrorReason(error, this.#options.limits);
        }
        catch {
            this.#error = '';
        }
        return error;
    }
    finally {
        this.#busy = previous;
    } }
    stopWithError(error: ModelError): ModelError { this.#available(); if (this.isFrozen())
        return error; return this.#freeze(error); }
    observe<A, B, C>(model: CodecModel<A, B, C>, before: A, input: B, transition: Transition<A, C>): readonly Check[] {
        this.#active();
        this.#busy = true;
        try {
            return this.#commit(this.#prepare(model, before, input, transition));
        }
        catch (e) {
            throw this.#freeze(e);
        }
        finally {
            this.#busy = false;
        }
    }
    observeChecked<A, B, C>(token: CheckedTurn<A, B, C>): readonly Check[] {
        this.#active();
        this.#busy = true;
        try {
            const header = requireCheckedTurnHeader(token);
            if (!header.fullChecking)
                throw new ModelError('runtime recorder requires full checking');
            const next = this.#origin + this.#observed + 1n;
            if (next > U64 || header.sequence !== next)
                throw new ModelError('runtime checked observation sequence is missing or reordered');
            const c = requireCheckedTurnForRecorder(token);
            return this.#commit(this.#prepare(c.model as CodecModel<A, B, C>, c.before, c.input, c.transition, { checks: c.checks, boundary: c.stateCheckCount }));
        }
        catch (e) {
            throw this.#freeze(e);
        }
        finally {
            this.#busy = false;
        }
    }
    #prepare<A, B, C>(model: CodecModel<A, B, C>, before: A, input: B, t: Transition<A, C>, checked?: {
        checks: readonly Check[];
        boundary: number;
    }): Observation {
        const metadata = call('metadata', () => validateMetadata(model.metadata()));
        if ((Object.keys(this.#metadata) as (keyof Metadata)[]).some(k => metadata[k] !== this.#metadata[k]))
            throw new ModelError('runtime recorder model identity changed');
        if (this.#observed === U64)
            throw new ModelError('runtime observation sequence overflow');
        const maximum = this.#blobLimit(), beforeBytes = call('encode before state', () => encodeModel(model, 'state', before, this.#encoding, maximum));
        const expected = this.#steps.length ? this.#steps[this.#steps.length - 1].step.postState : this.#initial;
        if (!bytesEqual(beforeBytes, expected))
            throw new ModelError('runtime before state is discontinuous; observations are missing or reordered');
        const l = this.#options.limits, outputs = t.outputs, count = outputs.length, rawDisposition = t.disposition, d = disposition(rawDisposition.kind, rawDisposition.reason);
        natural(count, 'output count', 0, 0xffffffff);
        if (count > l.max_outputs_per_step)
            throw new ModelError('runtime recording limit: outputs per step');
        if (count > l.max_items)
            throw new ModelError('runtime recording limit: trace exceeds aggregate items limit');
        const payloadBudget = Math.min(l.max_frame_bytes, 0xffffffff, Number(this.#options.maxRetainedBytes > BigInt(Number.MAX_SAFE_INTEGER) ? BigInt(Number.MAX_SAFE_INTEGER) : this.#options.maxRetainedBytes), l.max_total_bytes);
        if (count > Math.floor(payloadBudget / 4))
            throw new ModelError('runtime recording limit: frame bytes');
        if (d.kind !== 'accepted' && stringBytes(d.reason, Math.min(0xffffffff, l.max_string_bytes)) > payloadBudget)
            throw new ModelError('runtime recording limit: frame bytes');
        const encodedInput = call('encode input', () => encodeModel(model, 'input', input, this.#encoding, maximum));
        let checks: Check[], boundary: number;
        if (checked) {
            this.#checkedBatch(checked.checks, BigInt(payloadBudget));
            checks = copies(checked.checks);
            boundary = checked.boundary;
        }
        else {
            boundary = checkObservedCountIntoExact(model, before, input, t, this.#observed + 1n, this.#checkStorage);
            checks = copies(this.#checkStorage);
        }
        const fixed = replayStepPreflight(encodedInput.length, d, count, checks, l), frameBudget = Math.min(Math.min(0xffffffff, l.max_frame_bytes) + 9, Number(this.#options.maxRetainedBytes > BigInt(Number.MAX_SAFE_INTEGER) ? BigInt(Number.MAX_SAFE_INTEGER) : this.#options.maxRetainedBytes), l.max_total_bytes);
        let remaining = frameBudget - 9 - fixed.payloadBytes;
        if (remaining < 0)
            throw new ModelError('runtime recording limit: frame bytes');
        const encodedOutputs: Uint8Array[] = [];
        for (let i = 0; i < count; i++) {
            const encoded = call('encode output', () => encodeModel(model, 'output', outputs[i], this.#encoding, Math.min(maximum, remaining)));
            remaining -= encoded.length;
            encodedOutputs.push(encoded);
        }
        const postState = call('encode post state', () => encodeModel(model, 'state', t.state, this.#encoding, Math.min(maximum, remaining)));
        const bytes = 9 + fixed.payloadBytes + encodedOutputs.reduce((n, b) => n + b.length, 0) + postState.length;
        return { step: { input: encodedInput, disposition: d, outputs: encodedOutputs, postState, checks }, boundary, bytes, items: fixed.items };
    }
    #commit(candidate: Observation): readonly Check[] {
        if (this.#termination !== 'interrupted')
            throw new ModelError('runtime recorder is frozen; export its retained trace');
        let bytes = this.#stepBytes + BigInt(candidate.bytes), items = this.#stepItems + BigInt(candidate.items), evictions = this.#steps.length === this.#options.maxSteps ? 1 : 0;
        for (let i = 0; i < evictions; i++) {
            bytes -= BigInt(this.#steps[i].bytes);
            items -= BigInt(this.#steps[i].items);
        }
        let last: unknown = new ModelError('runtime recording cannot retain observation');
        for (; evictions <= this.#steps.length; evictions++) {
            const config = this.#counts(this.#config, this.#observed + 1n, this.#evicted + BigInt(evictions)), checkpoint = evictions ? this.#steps[evictions - 1] : undefined;
            const initial = checkpoint ? checkpoint.step.postState : this.#initial, checks = checkpoint ? checkpoint.step.checks.slice(0, checkpoint.boundary) : this.#checks;
            try {
                const run = replayRunSize(this.#metadata, config, initial, checks, this.#options.limits);
                this.#validate(run.bytes, BigInt(run.items) + items, bytes, this.#steps.length + 1 - evictions, failed(candidate.step.checks) ? 18 : this.#reserved);
                // Nothing retained changes until candidate encoding and the whole fitting plan succeed.
                const retainedChecks = copies(checks), retainedSteps = this.#steps.slice(evictions).concat(candidate), result = Object.freeze(copies(candidate.step.checks).map(c => Object.freeze(c)));
                this.#config = config;
                this.#initial = initial;
                this.#checks = retainedChecks;
                this.#steps = retainedSteps;
                this.#observed++;
                this.#evicted += BigInt(evictions);
                this.#runBytes = run.bytes;
                this.#runItems = run.items;
                this.#stepBytes = bytes;
                this.#stepItems = items;
                if (failed(candidate.step.checks))
                    this.#termination = 'property_failed';
                return result;
            }
            catch (e) {
                last = e;
            }
            if (evictions < this.#steps.length) {
                bytes -= BigInt(this.#steps[evictions].bytes);
                items -= BigInt(this.#steps[evictions].items);
            }
        }
        throw last;
    }
    #view(): AuditTrace { this.#available(); return { metadata: this.#metadata, config: this.#config, initialState: this.#initial, initialChecks: this.#checks, steps: this.#steps.map(s => s.step), termination: this.#termination, error: this.#error }; }
    snapshot(): AuditTrace { const t = this.#view(); return { metadata: { ...t.metadata }, config: { ...t.config, parameters: t.config.parameters.map(([k, v]) => [k, v]) }, initialState: new Uint8Array(t.initialState), initialChecks: copies(t.initialChecks), steps: t.steps.map(s => ({ input: new Uint8Array(s.input), disposition: { ...s.disposition }, outputs: s.outputs.map(b => new Uint8Array(b)), postState: new Uint8Array(s.postState), checks: copies(s.checks) })), termination: t.termination, error: t.error }; }
    intoTrace(): AuditTrace { this.#available(); if (this.#busy)
        throw new ModelError('runtime recorder observation is already in progress'); const trace = this.#view(); this.#consumed = true; this.#steps = []; this.#checks = []; this.#initial = new Uint8Array(); return trace; }
    writeTo(writer: AuditWriter): void { this.writeWithLimits(writer, this.#options.limits); }
    writeWithLimits(writer: AuditWriter, limits: Partial<BinaryLimits>): void { writeAuditTraceTo(this.#view(), writer, limits); }
    retainedBytes(): bigint { this.#available(); return 12n + BigInt(this.#runBytes) + this.#stepBytes + BigInt(this.#termination === 'model_error' ? 22 + new TextEncoder().encode(this.#error).length : 18); }
    retainedSteps(): number { this.#available(); return this.#steps.length; }
    observedSteps(): bigint { this.#available(); return this.#observed; }
    evictedSteps(): bigint { this.#available(); return this.#evicted; }
    isFrozen(): boolean { this.#available(); return this.#termination !== 'interrupted'; }
}
