/** Application-owned synchronous diagnostics. Events never confer checked authority. */
import {type Check, type CodecModel, type Metadata, type Model, type Transition, ConfigError, natural, scalar} from './core.js';
import {type RunConfig} from './binary.js';
import {EncodeStorage, type EncodeBuffer, encodeModel, preflightEncodeAppend, withEncodeBuffer} from './encoding.js';

export enum LogLevel { Off, Error, Warn, Info, Debug, Trace }
export type SnapshotPolicy = Readonly<{kind: 'none'} | {kind: 'every'; every: bigint} | {kind: 'every_transition'}>;
const NONE: SnapshotPolicy = Object.freeze({kind: 'none'});
const EVERY_TRANSITION: SnapshotPolicy = Object.freeze({kind: 'every_transition'});
const U64 = (1n << 64n) - 1n;
function u64(value: bigint, name: string, minimum = 0n): bigint {
    if (typeof value !== 'bigint' || value < minimum || value > U64) throw new ConfigError(`${name} must be ${minimum === 1n ? 'positive ' : ''}u64 bigint`);
    return value;
}
export const SnapshotPolicy = Object.freeze({
    None: NONE,
    EveryTransition: EVERY_TRANSITION,
    Every(every: bigint): SnapshotPolicy { return Object.freeze({kind: 'every', every: u64(every, 'snapshot interval', 1n)}); },
});
export interface ObservationOptions {
    readonly level: LogLevel;
    readonly snapshots: SnapshotPolicy;
    /** UTF-8 record bytes, including its final newline. Zero is a valid bound. */
    readonly maxRecordBytes: number;
}
export const DEFAULT_OBSERVATION_OPTIONS: Readonly<ObservationOptions> = Object.freeze({
    level: LogLevel.Off, snapshots: SnapshotPolicy.None, maxRecordBytes: 1024 * 1024,
});
function level(value: LogLevel): LogLevel { return natural(value, 'log level', LogLevel.Off, LogLevel.Trace); }
function snapshotPolicy(value: SnapshotPolicy): SnapshotPolicy {
    switch (value.kind) {
        case 'none': return NONE;
        case 'every_transition': return EVERY_TRANSITION;
        case 'every': return SnapshotPolicy.Every(value.every);
        default: throw new ConfigError('invalid snapshot policy');
    }
}

/** Borrowed for the duration of observe. Keep payloads and collections immutable during it. */
export type Event<S, I, O> =
    | {readonly kind: 'started'; readonly metadata: Metadata; readonly config: RunConfig}
    | {readonly kind: 'checkpoint'; readonly sequence: bigint; readonly state: S; readonly checks: readonly Check[]}
    | {readonly kind: 'transition'; readonly sequence: bigint; readonly before: S; readonly input: I; readonly transition: Transition<S, O>; readonly checks: readonly Check[]}
    | {readonly kind: 'message'; readonly level: LogLevel; readonly sequence: bigint | null; readonly message: string}
    | {readonly kind: 'finished'; readonly transitions: bigint; readonly reason: string};
export interface Observer<S, I, O, M extends Model<S, I, O> = Model<S, I, O>> {
    // Function properties retain model requirements under strictFunctionTypes.
    readonly observe: (model: M, event: Event<S, I, O>) => void;
}
/** A synchronous byte writer. Return the actual consumed prefix, never a Node stream boolean. */
export interface ObservationWriter {
    write(bytes: Uint8Array): number;
    flush(): void;
}
/** Callback-scoped record writer. Strings are UTF-8; raw bytes remain caller responsibility. */
export interface PayloadWriter {
    write(value: string | Uint8Array): number;
    writeAll(value: string | Uint8Array): void;
    flush(): void;
}
export interface PayloadFormat<S, I, O, M extends Model<S, I, O> = Model<S, I, O>> {
    readonly state: (model: M, state: S, out: PayloadWriter) => void;
    readonly input: (model: M, input: I, out: PayloadWriter) => void;
    readonly output: (model: M, output: O, out: PayloadWriter) => void;
}
export type TextObservationErrorStage = 'format' | 'record_limit' | 'format_state' | 'format_input' | 'format_output'
    | 'encode_state' | 'encode_input' | 'encode_output' | 'sink_write' | 'sink_flush' | 'sink_poisoned';
export class TextObservationError extends Error {
    constructor(readonly stage: TextObservationErrorStage, detail: string) {
        super(`${stage.replaceAll('_', ' ')}: ${detail}`);
        this.name = 'TextObservationError';
        Object.freeze(this);
    }
}
// Public errors carry diagnostics only. Internal attribution belongs to one
// record or one payload callback, never another observer or a previous call.
const errors = new WeakMap<object, {operation: object; error: TextObservationError}>();
const payloadOperations = new WeakMap<PayloadWriter, object>();
function attributed(error: TextObservationError, operation: object | undefined): TextObservationError {
    if (operation) errors.set(error, {operation, error});
    return error;
}
function detail(error: unknown): string {
    try { return error instanceof Error ? String(error.message) : String(error); }
    catch { return 'callback failed (diagnostic unavailable)'; }
}
function contextual(stage: TextObservationErrorStage, error: unknown): TextObservationError {
    return new TextObservationError(stage, detail(error));
}
function known(error: unknown, operation: object): TextObservationError | undefined {
    const origin = (typeof error === 'object' && error !== null) || typeof error === 'function' ? errors.get(error) : undefined;
    return origin && origin.operation === operation ? origin.error : undefined;
}
function rejectAsync(value: unknown): void {
    // Only genuine promises are observed, without reading arbitrary then getters.
    try { Promise.prototype.then.call(value, undefined, () => {}); } catch { /* Not a native promise. */ }
}
function synchronous(value: unknown): void {
    if (value !== undefined) { rejectAsync(value); throw new Error('callback must return undefined synchronously'); }
}

export class NoPayloads implements PayloadFormat<unknown, unknown, unknown> {
    readonly state = (_model: unknown, _state: unknown, out: PayloadWriter): void => { out.writeAll('unavailable'); };
    readonly input = (_model: unknown, _input: unknown, out: PayloadWriter): void => { out.writeAll('unavailable'); };
    readonly output = (_model: unknown, _output: unknown, out: PayloadWriter): void => { out.writeAll('unavailable'); };
}
/** Existing codec dispatch, including clone hooks and declaration-bound encodeInto hooks.
 * One bounded EncodeStorage is reused. Legacy encoders can allocate before admission.
 */
export class EncodedPayloads<S = unknown, I = unknown, O = unknown> implements PayloadFormat<S, I, O, CodecModel<S, I, O>> {
    #storage = new EncodeStorage();
    #maximum: number | bigint;
    #busy = false;
    constructor(maxBlobBytes: number | bigint) {
        this.#maximum = typeof maxBlobBytes === 'bigint' ? u64(maxBlobBytes, 'maxBlobBytes') : natural(maxBlobBytes, 'maxBlobBytes');
    }
    #encode(kind: 'state' | 'input' | 'output', model: CodecModel<S, I, O>, value: S | I | O, out: PayloadWriter): void {
        const operation = payloadOperations.get(out);
        if (this.#busy) throw attributed(new TextObservationError(`encode_${kind}`, 'payload encoding already in progress'), operation);
        this.#busy = true;
        try {
            let bytes: Uint8Array;
            try { bytes = encodeModel(model, kind, value, this.#storage, this.#maximum); }
            catch (error) { throw attributed(contextual(`encode_${kind}`, error), operation); }
            // Output failures belong to the record, not to codec attribution.
            out.writeAll('hex:');
            for (const byte of bytes) out.writeAll(byte.toString(16).padStart(2, '0'));
        } finally { this.#busy = false; }
    }
    state(model: CodecModel<S, I, O>, state: S, out: PayloadWriter): void { this.#encode('state', model, state, out); }
    input(model: CodecModel<S, I, O>, input: I, out: PayloadWriter): void { this.#encode('input', model, input, out); }
    output(model: CodecModel<S, I, O>, output: O, out: PayloadWriter): void { this.#encode('output', model, output, out); }
}

/** One record lease over existing sticky encoding storage; never exposes the buffer itself. */
class RecordWriter implements PayloadWriter {
    #error: TextObservationError | undefined;
    #active = true;
    #scratch = new Uint8Array(1024);
    #encoder = new TextEncoder();
    constructor(private readonly buffer: EncodeBuffer, private readonly operation: object) {}
    #healthy(): void {
        if (!this.#active) throw new TextObservationError('format', 'record writer lease expired');
        if (this.#error) throw this.#error;
    }
    write(value: string | Uint8Array): number {
        this.#healthy();
        try {
            if (typeof value !== 'string') {
                const before = this.buffer.length;
                this.buffer.extend(value);
                return this.buffer.length - before;
            }
            scalar(value);
            let length = 0;
            for (const c of value) { const cp = c.codePointAt(0)!; length += cp < 128 ? 1 : cp < 2048 ? 2 : cp < 65536 ? 3 : 4; }
            // Admit the whole write before UTF-8 scratch/copy, including multibyte text.
            preflightEncodeAppend(this.buffer, BigInt(length));
            let offset = 0;
            while (offset < value.length) {
                const encoded = this.#encoder.encodeInto(value.slice(offset, offset + 512 + (isHighSurrogate(value.charCodeAt(offset + 511)) ? 1 : 0)), this.#scratch);
                this.buffer.extend(this.#scratch.subarray(0, encoded.written));
                offset += encoded.read;
            }
            return length;
        } catch (error) {
            // Publish sticky state before trying to render an application exception.
            this.#error = attributed(new TextObservationError('record_limit', 'observation record write failed'), this.operation);
            this.#error = attributed(contextual('record_limit', error), this.operation);
            throw this.#error;
        }
    }
    writeAll(value: string | Uint8Array): void { this.write(value); }
    flush(): void { this.#healthy(); }
    finish(): void { this.#healthy(); this.buffer.finish(); }
    close(): void { this.#active = false; }
    callback(kind: 'state' | 'input' | 'output', run: (out: PayloadWriter) => void): void {
        let active = true;
        const operation = {};
        const guard = () => { if (!active) throw new TextObservationError('format', 'payload writer lease expired'); };
        const lease: PayloadWriter = Object.freeze({
            write: (value: string | Uint8Array) => { guard(); return this.write(value); },
            writeAll: (value: string | Uint8Array) => { guard(); this.writeAll(value); },
            flush: () => { guard(); this.flush(); },
        });
        payloadOperations.set(lease, operation);
        try {
            try { synchronous(run(lease)); }
            finally { active = false; payloadOperations.delete(lease); }
        } catch (error) {
            // A caught failed write stays primary even if the callback then
            // throws a foreign staged error. It never poisons the destination.
            this.#healthy();
            throw attributed(known(error, operation) ?? contextual(`format_${kind}`, error), this.operation);
        }
    }
}
function isHighSurrogate(code: number): boolean { return code >= 0xd800 && code <= 0xdbff; }

// Rust-style quoted strings for the shared text profile. Unicode category versions
// can differ from Rust's Debug tables; this is not a universal Debug wire format.
const NONPRINTING = /[\p{C}\p{Z}\p{Grapheme_Extend}]/u;
function quoted(out: PayloadWriter, value: string): void {
    scalar(value);
    out.writeAll('"');
    for (const c of value) {
        switch (c) {
            case '\0': out.writeAll('\\0'); break;
            case '\t': out.writeAll('\\t'); break;
            case '\r': out.writeAll('\\r'); break;
            case '\n': out.writeAll('\\n'); break;
            case '\\': out.writeAll('\\\\'); break;
            case '"': out.writeAll('\\"'); break;
            default: out.writeAll(c !== ' ' && NONPRINTING.test(c) ? `\\u{${c.codePointAt(0)!.toString(16)}}` : c);
        }
    }
    out.writeAll('"');
}
function checkLevel(checks: readonly Check[], ordinary: LogLevel): LogLevel {
    if (checks.some(c => c.status === 'failed')) return LogLevel.Error;
    return checks.some(c => c.status === 'skipped') ? LogLevel.Warn : ordinary;
}
function eventLevel<S, I, O>(event: Event<S, I, O>): LogLevel {
    switch (event.kind) {
        case 'started': case 'finished': return LogLevel.Info;
        case 'checkpoint': return checkLevel(event.checks, LogLevel.Trace);
        case 'transition': return checkLevel(event.checks, LogLevel.Debug);
        case 'message': return level(event.level);
        default: throw new Error('invalid observation event');
    }
}
function writeChecks(out: PayloadWriter, checks: readonly Check[], verbosity: LogLevel): void {
    out.writeAll(` checks=${natural(checks.length, 'check count')}`);
    for (const check of checks) {
        if (verbosity < LogLevel.Trace && check.status === 'passed') continue;
        out.writeAll(' check='); quoted(out, check.id); out.writeAll(':');
        switch (check.status) {
            case 'passed': out.writeAll('Passed'); break;
            case 'failed': case 'skipped':
                out.writeAll(check.status === 'failed' ? 'Failed(' : 'Skipped(');
                quoted(out, check.details); out.writeAll(')'); break;
            default: throw new Error('invalid check status');
        }
    }
}
function started(out: PayloadWriter, metadata: Metadata, config: RunConfig): void {
    out.writeAll(' event=started metadata=ModelMetadata { name: '); quoted(out, metadata.name);
    out.writeAll(`, model_version: ${natural(metadata.model_version, 'model version', 0, 0xffffffff)}, properties_version: ${natural(metadata.properties_version, 'properties version', 0, 0xffffffff)}, codec_version: ${natural(metadata.codec_version, 'codec version', 0, 0xffffffff)}, build: `);
    quoted(out, metadata.build); out.writeAll(' } config=RunConfig { strategy: '); quoted(out, config.strategy);
    out.writeAll(`, seed: ${config.seed === null ? 'None' : `Some(${u64(config.seed, 'seed')})`}, parameters: [`);
    for (let i = 0; i < config.parameters.length; i++) {
        if (i) out.writeAll(', ');
        const [key, value] = config.parameters[i];
        out.writeAll('('); quoted(out, key); out.writeAll(', '); quoted(out, value); out.writeAll(')');
    }
    out.writeAll('] }');
}
function interrupted(error: unknown): boolean {
    try { return error !== null && typeof error === 'object' && (error as {code?: unknown}).code === 'EINTR'; }
    catch { return false; }
}

/** Synchronous, reusable staging followed by write-all. No model execution/checking.
 * Writer failures poison permanently; formatter/codec failures discard just this event.
 */
export class TextObserver<S, I, O, M extends Model<S, I, O> = Model<S, I, O>, W extends ObservationWriter = ObservationWriter> implements Observer<S, I, O, M> {
    #writer: W;
    #payloads: PayloadFormat<S, I, O, M>;
    #stream: string;
    #level: LogLevel;
    #snapshots: SnapshotPolicy;
    #maximum: number;
    #storage = new EncodeStorage();
    #failed = false;
    #busy = false;
    #consumed = false;
    constructor(writer: W, streamId: string, options: Partial<ObservationOptions> = {}, payloads: PayloadFormat<S, I, O, M> = new NoPayloads()) {
        scalar(streamId);
        this.#writer = writer; this.#stream = streamId; this.#payloads = payloads;
        this.#level = level(options.level ?? DEFAULT_OBSERVATION_OPTIONS.level);
        this.#snapshots = snapshotPolicy(options.snapshots ?? DEFAULT_OBSERVATION_OPTIONS.snapshots);
        this.#maximum = natural(options.maxRecordBytes ?? DEFAULT_OBSERVATION_OPTIONS.maxRecordBytes, 'maxRecordBytes');
    }
    #available(): void {
        if (this.#consumed) throw new TextObservationError('format', 'observation writer ownership transferred');
        if (this.#busy) throw new TextObservationError('format', 'observation already in progress');
    }
    #healthy(): void {
        if (this.#failed) throw new TextObservationError('sink_poisoned', 'observation sink failed; output may be incomplete');
        this.#available();
    }
    setLevel(value: LogLevel): void { this.#available(); this.#level = level(value); }
    setSnapshots(value: SnapshotPolicy): void { this.#available(); this.#snapshots = snapshotPolicy(value); }
    isPoisoned(): boolean { return this.#failed; }
    getRef(): W {
        if (this.#consumed) throw new TextObservationError('format', 'observation writer ownership transferred');
        return this.#writer;
    }
    /** Transfers this observer's writer handle. Never flushes, closes or disposes it. */
    intoInner(): W { this.#available(); this.#consumed = true; return this.#writer; }
    flush(): void {
        this.#healthy(); this.#busy = true;
        try { synchronous(this.#writer.flush()); }
        catch (error) { this.#failed = true; throw contextual('sink_flush', error); }
        finally { this.#busy = false; }
    }
    readonly observe = (model: M, event: Event<S, I, O>): void => {
        this.#healthy();
        if (this.#level === LogLevel.Off) return;
        this.#busy = true;
        try {
            let record: Uint8Array;
            let formatError: TextObservationError | undefined;
            const operation = {};
            try {
                const severity = eventLevel(event);
                if (severity === LogLevel.Off || severity > this.#level) return;
                withEncodeBuffer(this.#storage, this.#maximum, buffer => {
                    const out = new RecordWriter(buffer, operation);
                    try {
                        out.writeAll(`${LogLevel[severity]} stream=`); quoted(out, this.#stream);
                        this.#format(model, event, out);
                        out.writeAll('\n'); out.finish();
                    } catch (error) {
                        // withEncodeBuffer owns callback diagnostics; retain our public stage
                        // separately so its ModelError normalization cannot erase attribution.
                        formatError = known(error, operation) ?? contextual('format', error);
                        throw formatError;
                    } finally { out.close(); }
                });
                record = this.#storage.snapshot();
            } catch (error) { throw formatError ?? known(error, operation) ?? contextual('format', error); }
            let offset = 0;
            while (offset < record.length) {
                let written: number;
                try { written = this.#writer.write(record.subarray(offset)); }
                catch (error) {
                    if (interrupted(error)) continue;
                    this.#failed = true; throw contextual('sink_write', error);
                }
                if (!Number.isSafeInteger(written) || written <= 0 || written > record.length - offset) {
                    this.#failed = true; rejectAsync(written);
                    throw new TextObservationError('sink_write', 'writer must synchronously return a positive consumed byte count within the supplied slice');
                }
                offset += written;
            }
        } finally { this.#busy = false; }
    };
    #payload(kind: 'state' | 'input' | 'output', model: M, value: S | I | O, out: RecordWriter): void {
        out.callback(kind, writer => {
            switch (kind) {
                case 'state': return this.#payloads.state(model, value as S, writer);
                case 'input': return this.#payloads.input(model, value as I, writer);
                case 'output': return this.#payloads.output(model, value as O, writer);
            }
        });
    }
    #format(model: M, event: Event<S, I, O>, out: RecordWriter): void {
        switch (event.kind) {
            case 'started': started(out, event.metadata, event.config); break;
            case 'finished':
                out.writeAll(` event=finished transitions=${u64(event.transitions, 'transitions')} reason=`); quoted(out, event.reason); break;
            case 'message':
                out.writeAll(` event=message sequence=${event.sequence === null ? 'None' : `Some(${u64(event.sequence, 'message sequence')})`} message=`); quoted(out, event.message); break;
            case 'checkpoint':
                out.writeAll(` event=checkpoint sequence=${u64(event.sequence, 'checkpoint sequence')}`); writeChecks(out, event.checks, this.#level);
                if (this.#level === LogLevel.Trace && this.#snapshots.kind !== 'none') {
                    out.writeAll(' state='); this.#payload('state', model, event.state, out);
                }
                break;
            case 'transition': {
                const sequence = u64(event.sequence, 'transition sequence', 1n), transition = event.transition;
                out.writeAll(` event=transition sequence=${sequence} disposition=`);
                switch (transition.disposition.kind) {
                    case 'accepted': out.writeAll('Accepted'); break;
                    case 'rejected': case 'ignored':
                        out.writeAll(transition.disposition.kind === 'rejected' ? 'Rejected(' : 'Ignored(');
                        quoted(out, transition.disposition.reason); out.writeAll(')'); break;
                    default: throw new Error('invalid disposition');
                }
                const outputs = transition.outputs, outputCount = natural(outputs.length, 'output count');
                out.writeAll(` outputs=${outputCount}`); writeChecks(out, event.checks, this.#level);
                if (this.#level === LogLevel.Trace) {
                    out.writeAll(' input='); this.#payload('input', model, event.input, out);
                    for (let i = 0; i < outputCount; i++) {
                        out.writeAll(` output[${i}]=`); this.#payload('output', model, outputs[i], out);
                    }
                    if (this.#snapshots.kind === 'every_transition' || (this.#snapshots.kind === 'every' && sequence % this.#snapshots.every === 0n)) {
                        out.writeAll(' post_state='); this.#payload('state', model, transition.state, out);
                    }
                }
                break;
            }
        }
    }
}
