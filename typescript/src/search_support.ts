/** Shared cooperative controls and checked search observations; not a package entrypoint. */
import { type Model, type Transition, type Check, type Violation, ModelError, ConfigError, check, disposition } from './core.js';
export interface RunLimits {
    readonly maxDurationMs?: number;
    readonly cancellation?: () => boolean;
}
type ControlStop = 'cancelled' | 'deadline';
function boolean(value: boolean, name: string): boolean { if (typeof value !== 'boolean')
    throw new TypeError(`${name} must return boolean`); return value; }
function call<T>(stage: string, fn: () => T): T { try {
    return fn();
}
catch (e) {
    throw new ModelError(`${stage}: ${e instanceof Error ? e.message : String(e)}`);
} }
const state = <S, I, O>(m: Model<S, I, O>, s: S): S => call('snapshot state', () => m.cloneState(s));
const input = <S, I, O>(m: Model<S, I, O>, i: I): I => call('snapshot input', () => m.cloneInput(i));
function copyTransition<S, I, O>(m: Model<S, I, O>, t: Transition<S, O>): Transition<S, O> { return call('snapshot transition', () => ({ state: m.cloneState(t.state), outputs: Array.from(t.outputs, o => m.cloneOutput(o)), disposition: disposition(t.disposition.kind, t.disposition.reason) })); }
/** Observation cannot alter search; all supplied values are independent snapshots. */
export interface SearchObserver<S, I, O> {
    /** BFS may omit history unless requested; fuzz always supplies the full prefix. */
    needsHistory?(): boolean;
    initial?(state: S, checks: readonly Check[]): void;
    transition?(before: S, input: I, transition: Transition<S, O>, stateChecks: readonly Check[], transitionChecks: readonly Check[], history: readonly I[]): void;
    checkError?(phase: Violation['phase'], checks: readonly Check[], complete: boolean): void;
}
const copyChecks = (checks: readonly Check[]): Check[] => checks.map(c => check(c.id, c.status, c.details));
export function searchChecks<S, I, O>(m: Model<S, I, O>, s: S, before?: S, i?: I, t?: Transition<S, O>, observer?: SearchObserver<S, I, O>, history: readonly I[] = []): {
    violations: Violation[];
    skipped: bigint;
} {
    const sc: Check[] = [], ec: Check[] = [];
    const notify = (phase: Violation['phase'], values: Check[], complete: boolean) => {
        if (observer?.checkError)
            call('observer check_error', () => observer.checkError!(phase, copyChecks(values), complete));
    };
    const reportCheckError = (error: unknown, batches: [
        Violation['phase'],
        Check[],
        boolean
    ][]): never => {
        const secondary: string[] = [];
        for (const [phase, values, complete] of batches) {
            try {
                notify(phase, values, complete);
            }
            catch (observationError) {
                secondary.push(observationError instanceof Error ? observationError.message : String(observationError));
            }
        }
        if (secondary.length)
            throw new ModelError(`${error instanceof Error ? error.message : String(error)}; ${secondary.join('; ')}`);
        throw error;
    };
    const collect = (stage: string, fn: () => Iterable<Check>, out: Check[]) => call(stage, () => {
        for (const c of fn())
            out.push(check(c.id, c.status, c.details));
    });
    try {
        collect('state check', () => m.checkState(state(m, s)), sc);
    }
    catch (error) {
        reportCheckError(error, [[t ? 'state' : 'initial_state', sc, false]]);
    }
    if (t) {
        try {
            if (m.checkTransition)
                collect('transition check', () => m.checkTransition!(state(m, before as S), input(m, i as I), copyTransition(m, t)), ec);
        }
        catch (error) {
            reportCheckError(error, [['state', sc, true], ['transition', ec, false]]);
        }
        if (observer?.transition)
            call('observer transition', () => observer.transition!(state(m, before as S), input(m, i as I), copyTransition(m, t), copyChecks(sc), copyChecks(ec), history.map(v => input(m, v))));
    }
    else if (observer?.initial)
        call('observer initial', () => observer.initial!(state(m, s), copyChecks(sc)));
    return { violations: [...sc.filter(c => c.status === 'failed').map(c => ({ phase: t ? 'state' as const : 'initial_state' as const, check: c })), ...ec.filter(c => c.status === 'failed').map(c => ({ phase: 'transition' as const, check: c }))], skipped: BigInt([...sc, ...ec].filter(c => c.status === 'skipped').length) };
}
/** Snapshot copied run settings; only the captured cancellation function's external state stays live. */
export function runControl(limits: RunLimits): () => ControlStop | null {
    const duration = limits.maxDurationMs;
    const cancellation = limits.cancellation;
    if (duration !== undefined && (!Number.isFinite(duration) || duration < 0))
        throw new ConfigError('maxDurationMs must be finite and nonnegative');
    const started = duration === undefined ? 0 : performance.now();
    return () => {
        // Preserve a method callback's original receiver without rereading its property.
        if (cancellation && call('cancellation', () => boolean(cancellation.call(limits), 'cancellation')))
            return 'cancelled';
        if (duration !== undefined && performance.now() - started >= duration)
            return 'deadline';
        return null;
    };
}

/** Rust check accounting saturates at u64::MAX rather than wrapping. */
export function addSkippedChecks(left:bigint,right:bigint):bigint {
    const total=left+right,maximum=(1n<<64n)-1n;
    return total>maximum?maximum:total;
}
