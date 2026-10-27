/** Binary-equivalent bounded recording keeps a coherent admitted prefix on later errors. */
import { type CodecModel, type Check, type Transition, ModelError, natural, validateMetadata, disposition, checkObservedCountInto, VERSION, scalar } from './core.js';
import { type AuditTrace, type RunConfig, type BinaryLimits } from './binary.js';
import { EncodeStorage, encodeModel } from './encoding.js';
import { withCheckSink, appendStateChecks } from './checks.js';
import { AuditBudget, resolveBinaryLimits, effectiveBlobLimit, errorFooterReserve, truncatedErrorReason } from './audit_budget.js';
function call<T>(stage: string, callback: () => T): T { try {
    return callback();
}
catch (e) {
    throw new ModelError(`${stage}: ${e instanceof Error ? e.message : String(e)}`);
} }
function captureConfig(config: RunConfig): RunConfig {
    const { strategy, seed } = config, source = config.parameters, length = source.length;
    natural(length, 'parameters', 0, 0xffffffff);
    const parameters: [
        string,
        string
    ][] = [];
    for (let i = 0; i < length; i++) {
        const pair = source[i];
        if (!Array.isArray(pair) || pair.length !== 2)
            throw new ModelError('invalid parameter pair');
        parameters.push([pair[0], pair[1]]);
    }
    scalar(strategy);
    if (seed !== null && (typeof seed !== 'bigint' || seed < 0n || seed > ((1n << 64n) - 1n)))
        throw new ModelError('seed must be u64 bigint or null');
    for (const [key, value] of parameters) {
        scalar(key);
        scalar(value);
        if (key.startsWith('stateless.engine.'))
            throw new ModelError('stateless.engine. parameters are reserved');
    }
    parameters.push(['stateless.engine.version', VERSION], ['stateless.engine.build', 'native-typescript-v1']);
    return { strategy, seed, parameters };
}
export function recordAudit<S, I, O>(m: CodecModel<S, I, O>, inputs: Iterable<I>, config: RunConfig, maxSteps = 100000, overrides: Partial<BinaryLimits> = {}): AuditTrace {
    natural(maxSteps, 'maxSteps');
    const limits = resolveBinaryLimits(overrides), runConfig = captureConfig(config), encoding = new EncodeStorage(), checks: Check[] = [];
    let state = call('initial state', () => m.cloneState(m.initialState()));
    call('initial check', () => withCheckSink(checks, sink => appendStateChecks(m, state, sink)));
    const initialChecks = checks.map(c => ({ ...c })), initialState = call('encode initial state', () => encodeModel(m, 'state', state, encoding, effectiveBlobLimit(limits)));
    const metadata = call('metadata', () => validateMetadata(m.metadata()));
    const trace: AuditTrace = { metadata, config: runConfig, initialState, initialChecks, steps: [], termination: 'completed', error: '' };
    const failed = (cs: readonly Check[]) => cs.some(c => c.status === 'failed'), initialFailed = failed(checks), reserve = errorFooterReserve(limits);
    const budget = new AuditBudget(metadata, runConfig, initialState, checks, limits, initialFailed ? 18 : reserve);
    if (initialFailed)
        return { ...trace, termination: 'property_failed' };
    const steps: import('./core.js').TraceStep[] = [];
    const result = (termination: AuditTrace['termination'], error = ''): AuditTrace => ({ ...trace, steps, termination, error });
    try {
        const iterator = call('record input', () => inputs[Symbol.iterator]());
        for (;;) {
            const next = call('record input', () => iterator.next());
            if (next.done)
                return result('completed');
            if (steps.length >= Math.min(maxSteps, limits.max_steps))
                return result('step_limit');
            const frameBudget = Math.max(0, Math.min(limits.max_total_bytes - budget.bytes - 18, Math.min(0xffffffff, limits.max_frame_bytes) + 9));
            const input = call('encode input', () => encodeModel(m, 'input', next.value, encoding, Math.min(budget.blobLimit, frameBudget)));
            const t = call('transition', () => { const raw = m.step(m.cloneState(state), m.cloneInput(next.value)); return { state: m.cloneState(raw.state), outputs: Array.from(raw.outputs, o => m.cloneOutput(o)), disposition: disposition(raw.disposition.kind, raw.disposition.reason) }; });
            checkObservedCountInto(m, state, next.value, t, steps.length + 1, checks);
            const plan = budget.planRecord(input, t.disposition, t.outputs.length, checks, 18), outputs: Uint8Array[] = [];
            for (const output of t.outputs) {
                plan.reserveOutput();
                const bytes = call('encode output', () => encodeModel(m, 'output', output, encoding, plan.maximum));
                plan.accept(bytes);
                outputs.push(bytes);
            }
            const postState = call('encode state', () => encodeModel(m, 'state', t.state, encoding, plan.maximum));
            plan.accept(postState);
            const terminal = failed(checks);
            plan.commit(terminal ? 18 : reserve);
            steps.push({ input, disposition: { ...t.disposition }, outputs, postState, checks: checks.map(c => ({ ...c })) });
            state = t.state;
            if (terminal)
                return result('property_failed');
        }
    }
    catch (e) {
        return result('model_error', truncatedErrorReason(e, limits));
    }
}
