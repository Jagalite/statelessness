/** Installed public-package consumer; no imports from the repository or implementation internals. */
import {
    LogLevel, SnapshotPolicy, TextObserver, EncodedPayloads, NoPayloads, TextObservationError, ObservationError,
    bindModelCodec, customValueSchema, metadata, check, disposition,
    type DebugObservation, type CodecModel, type EncodeBuffer, type Event, type ObservationOptions,
    type ObservationWriter, type PayloadFormat, type PayloadWriter,
} from '@jagalite/statelessness';
// @ts-ignore Node runtime built-in; this consumer needs no Node development dependency.
import {readFileSync} from 'node:fs';
declare const process: {argv: string[]};

type NumericModel = CodecModel<number, number, number>;
const levels: Record<string, LogLevel> = {Off: LogLevel.Off, Error: LogLevel.Error, Warn: LogLevel.Warn, Info: LogLevel.Info, Debug: LogLevel.Debug, Trace: LogLevel.Trace};
function verbosity(value: string): LogLevel {
    if (!Object.hasOwn(levels, value)) throw Error('unknown level');
    return levels[value];
}
function policy(value: any): SnapshotPolicy {
    if (value === undefined || value === 'None') return SnapshotPolicy.None;
    if (value === 'EveryTransition') return SnapshotPolicy.EveryTransition;
    return SnapshotPolicy.Every(BigInt(value.every));
}
function byte(value: number): number {
    if (!Number.isInteger(value) || value < 0 || value > 255) throw Error('expected byte');
    return value;
}
function execute(row: any) {
    const callbacks: string[] = [], delivered: number[] = [];
    const declaration = row.binding === 'declaration';
    let fault: string | undefined, errorStage: string | null = null, unexpectedCallbacks = 0;
    let writeCalls = 0, flushCalls = 0;
    const forbidden = (): never => { unexpectedCallbacks++; throw Error('observation executed a forbidden model callback'); };
    const encode = (kind: string, value: number, out: EncodeBuffer): void => {
        callbacks.push(`encode_${kind}:${value}`);
        if (fault === `encode_${kind}`) throw Error('injected codec failure');
        out.extend(Uint8Array.of(byte(value)));
    };
    let model: NumericModel = {
        initialState: forbidden, step: forbidden, checkState: forbidden, checkTransition: forbidden,
        cloneState: x => x, cloneInput: x => x, cloneOutput: x => x,
        metadata: () => metadata('observation-fixture', 'source-derived'),
        // Into dispatch must win; no fallback silently reproduces the fixture bytes.
        encodeState: forbidden, encodeInput: forbidden, encodeOutput: forbidden,
        decodeState: forbidden, decodeInput: forbidden,
        // Declaration cases must fail unless the public binder replaces these hooks.
        encodeStateInto: declaration ? forbidden : (value, out) => encode('state', value, out),
        encodeInputInto: declaration ? forbidden : (value, out) => encode('input', value, out),
        encodeOutputInto: declaration ? forbidden : (value, out) => encode('output', value, out),
    };
    if (declaration) {
        const schema = (kind: string) => customValueSchema<number>({name: `Observation${kind}`,
            encodeInto: (value, out) => encode(kind, value, out), decodeFrom: forbidden});
        model = bindModelCodec(model, {state: schema('state'), input: schema('input'), output: schema('output')});
    }
    const writer: ObservationWriter = {
        write(bytes) {
            writeCalls++;
            const available = row.sink?.fail_after_bytes === undefined ? bytes.length : row.sink.fail_after_bytes - delivered.length;
            if (available <= 0) throw Error('injected writer failure');
            const length = Math.min(available, bytes.length);
            for (let i = 0; i < length; i++) delivered.push(bytes[i]);
            return length;
        },
        flush() { flushCalls++; if (row.sink?.fail_flush) throw Error('injected flush failure'); },
    };
    const format = (kind: string, value: number, out: PayloadWriter): void => {
        callbacks.push(`format_${kind}:${value}`);
        if (fault === `format_${kind}`) throw Error('injected formatter failure');
        if (kind === 'input' && fault === 'swallow_input') {
            try { out.writeAll('x'.repeat(1024)); } catch { /* Deliberately hostile formatter. */ }
            return;
        }
        out.writeAll(row.payloads.text);
    };
    let payloads: PayloadFormat<number, number, number, NumericModel>;
    switch (row.payloads.kind) {
        case 'encoded': payloads = new EncodedPayloads<number, number, number>(row.payloads.max_blob_bytes); break;
        case 'none': payloads = new NoPayloads(); break;
        case 'custom': payloads = {
            input: (_model, value, out) => format('input', value, out),
            output: (_model, value, out) => format('output', value, out),
            state: (_model, value, out) => format('state', value, out),
        }; break;
        default: throw Error('unknown payload format');
    }
    const options: Partial<ObservationOptions> = {
        level: row.options.level === undefined ? LogLevel.Off : verbosity(row.options.level),
        snapshots: policy(row.options.snapshots), maxRecordBytes: row.options.max_record_bytes,
    };
    const observer = new TextObserver<number, number, number, NumericModel>(writer, row.stream, options, payloads);
    const activity = () => [writeCalls, flushCalls, delivered.length, callbacks.length, unexpectedCallbacks];
    const assertInactive = (before: readonly number[], operation: string): void => {
        if (activity().some((count, index) => count !== before[index])) throw Error(`${operation} performed destination or payload work`);
    };
    let currentLevel = options.level ?? LogLevel.Off;
    const checks = (values: any[]) => values.map(c => check(c.id, c.status.toLowerCase(), c.message ?? ''));
    for (const e of row.events) {
        fault = e.fault;
        const before = activity(), wasPoisoned = observer.isPoisoned();
        const setting = e.kind === 'set_level' || e.kind === 'set_snapshots';
        let operationError: TextObservationError | undefined;
        try {
            let event: Event<number, number, number> | undefined;
            switch (e.kind) {
                case 'set_level': observer.setLevel(verbosity(e.level)); currentLevel = verbosity(e.level); break;
                case 'set_snapshots': observer.setSnapshots(policy(e.snapshots)); break;
                case 'flush': observer.flush(); break;
                case 'transition': event = {kind: 'transition', sequence: BigInt(e.sequence), before: e.before, input: e.input,
                    transition: {state: e.state, outputs: e.outputs, disposition: disposition(e.disposition.kind.toLowerCase(), e.disposition.reason ?? '')}, checks: checks(e.checks)}; break;
                case 'checkpoint': event = {kind: 'checkpoint', sequence: BigInt(e.sequence), state: e.state, checks: checks(e.checks)}; break;
                case 'message': event = {kind: 'message', level: verbosity(e.level), sequence: e.sequence === null ? null : BigInt(e.sequence), message: e.message}; break;
                case 'started': event = {kind: 'started', metadata: e.metadata,
                    config: {strategy: e.config.strategy, seed: e.config.seed === null ? null : BigInt(e.config.seed), parameters: e.config.parameters}}; break;
                case 'finished': event = {kind: 'finished', transitions: BigInt(e.transitions), reason: e.reason}; break;
                default: throw Error('unknown fixture event');
            }
            if (event !== undefined) observer.observe(model, event);
        } catch (error) {
            if (!(error instanceof TextObservationError)) throw error;
            operationError = error;
            errorStage ??= error.stage;
        }
        if (setting) {
            if (operationError) throw operationError;
            assertInactive(before, 'configuration change');
        } else if (wasPoisoned) {
            if (operationError?.stage !== 'sink_poisoned') throw Error('post-poison operation did not fail with sink_poisoned');
            assertInactive(before, 'post-poison operation');
        } else if (currentLevel === LogLevel.Off && e.kind !== 'flush') {
            if (operationError) throw operationError;
            assertInactive(before, 'Off observation');
        }
    }
    const beforeProbe = activity();
    observer.setLevel(LogLevel.Off);
    let poisoned = false;
    try { observer.observe(model, {kind: 'finished', transitions: 0n, reason: 'health probe'}); }
    catch (error) {
        if (!(error instanceof TextObservationError) || error.stage !== 'sink_poisoned') throw error;
        poisoned = true;
    }
    assertInactive(beforeProbe, 'Off health probe');
    if (observer.isPoisoned() !== poisoned) throw Error('writer health mismatch');
    if (poisoned) {
        // Probe an otherwise eligible payload and flush independently of the first-error receipt.
        observer.setLevel(LogLevel.Trace); observer.setSnapshots(SnapshotPolicy.EveryTransition);
        assertInactive(beforeProbe, 'post-poison configuration change');
        const event: Event<number, number, number> = {kind: 'transition', sequence: 1n, before: 0, input: 1,
            transition: {state: 1, outputs: [2], disposition: disposition('accepted')}, checks: []};
        for (const operation of [() => observer.observe(model, event), () => observer.flush()]) {
            let failure: unknown;
            try { operation(); } catch (error) { failure = error; }
            if (!(failure instanceof TextObservationError) || failure.stage !== 'sink_poisoned') throw Error('post-poison probe did not fail with sink_poisoned');
            assertInactive(beforeProbe, 'post-poison probe');
        }
    }
    const beforeExtraction = activity();
    if (observer.getRef() !== writer || observer.intoInner() !== writer) throw Error('writer ownership mismatch');
    assertInactive(beforeExtraction, 'writer extraction');
    if (unexpectedCallbacks !== 0) throw Error('observation executed initial/reducer/check/decode/legacy/unbound codec callback');
    return {id: row.id, record_hex: delivered.map(b => b.toString(16).padStart(2, '0')).join(''), callbacks, error_stage: errorStage, sink_poisoned: poisoned};
}

// Public declaration and runtime variant control, independent of logging Event.
const initialDebug: DebugObservation<number, number, number> = {kind: 'initial', state: 7, checks: [check('state')]};
const turnDebug: DebugObservation<number, number, number> = {kind: 'turn', observation: {
    sequence: (1n << 64n) - 1n, before: 7, input: 1,
    transition: {state: 8, outputs: [9], disposition: disposition('accepted')},
    checks: [check('state'), check('edge')], stateCheckCount: 1,
}};
if (initialDebug.state !== 7 || initialDebug.checks.length !== 1 || turnDebug.observation.stateCheckCount !== 1)
    throw Error('DebugObservation public variants lost data');

const [mode, fixturePath] = process.argv.slice(2);
if (!['observe', 'self-test'].includes(mode) || !fixturePath) throw Error('usage: consumer observe|self-test fixturePath');
if (mode === 'self-test') {
    // Exercise both installed error declarations without executing a model.
    const actual = {state: 3, outputs: [2], disposition: disposition('accepted')}, cause = Error('oracle advance');
    const oracle = new ObservationError<number, number>(cause, actual);
    const diagnostic = new TextObservationError('format_input', 'formatter');
    if (oracle.cause !== cause || oracle.transition !== actual || oracle instanceof TextObservationError
        || diagnostic instanceof ObservationError || diagnostic.stage !== 'format_input') throw Error('public error exports conflict');
}
const fixture = JSON.parse(readFileSync(fixturePath, 'utf8'));
if (fixture.schema !== 'statelessness.observation.v1' || !Array.isArray(fixture.cases) || fixture.cases.length === 0) throw Error('invalid or empty observation fixture');
const results = fixture.cases.map((row: any) => {
    const result = execute(row);
    if (mode === 'self-test') {
        const {record_hex, callbacks, error_stage, sink_poisoned} = row.expected;
        const expected = {id: row.id, record_hex, callbacks, error_stage, sink_poisoned};
        if (JSON.stringify(result) !== JSON.stringify(expected)) throw Error(`case ${row.id} mismatch\nactual: ${JSON.stringify(result)}\nexpected: ${JSON.stringify(expected)}`);
    }
    return result;
});
console.log(JSON.stringify({schema: 'statelessness.observation-results.v1', language: 'typescript', cases: results}));
