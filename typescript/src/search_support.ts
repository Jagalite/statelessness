import { withCheckSink, appendStateChecks, appendTransitionChecks } from './checks.js';
/** Shared cooperative controls and checked search observations; not a package entrypoint. */
import { type Model, type Transition, type Check, type Violation, ModelError, ConfigError, check, disposition } from './core.js';
export interface RunLimits {
    readonly maxDurationMs?: number;
    readonly cancellation?: () => boolean;
}
type ControlStop = 'cancelled' | 'deadline';
function boolean(value: boolean, name: string): boolean {
    if (typeof value !== 'boolean')
        throw new TypeError(`${name} must return boolean`);
    return value;
}
function call<T>(stage: string, fn: () => T): T {
    try {
        return fn();
    }
    catch (e) {
        throw new ModelError(`${stage}: ${e instanceof Error ? e.message : String(e)}`);
    }
}
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
type EdgeCapture<T> = { ok: true; value: T } | { ok: false; error: unknown };
/** Pin consumers before callers invoke history callbacks; failures remain staged. */
export function captureEdgeConsumers<S,I,O>(m: Model<S,I,O>, observer?: SearchObserver<S,I,O>) {
    const capture = <T>(fn: () => T): EdgeCapture<T> => { try { return {ok:true,value:fn()}; } catch (error) { return {ok:false,error}; } };
    const transition = capture(() => {
        const hook = m.checkTransitionInto;
        if (hook !== undefined && typeof hook !== 'function') throw new ModelError('append check hook must be a function');
        const legacy = hook === undefined ? m.checkTransition : undefined;
        if (hook === undefined && legacy === undefined) return undefined;
        return {checkTransitionInto: typeof hook === 'function' ? hook.bind(m) : hook, checkTransition: typeof legacy === 'function' ? legacy.bind(m) : legacy};
    });
    const observe = capture(() => observer?.transition);
    return {transition, observe};
}
export function searchChecks<S, I, O>(m: Model<S, I, O>, s: S, before?: S, i?: I, t?: Transition<S, O>, observer?: SearchObserver<S, I, O>, history: readonly I[] = [], storage: Check[] = [], resolveInput?: () => I, resolveState?: () => S, resolveTransition?: () => Transition<S,O>, resolveBefore?: () => S, consumers?: ReturnType<typeof captureEdgeConsumers<S,I,O>>): {
    violations: Violation[];
    skipped: bigint;
    /** Leading entries of `storage` that are state checks; the rest are transition checks. */
    stateCount: number;
} {
    storage.length = 0;
    const phase = t ? 'state' : 'initial_state';
    // State check callbacks may mutate application aliases. Capture the later
    // edge and observer history first, but defer copy errors to the stage that
    // consumes each snapshot so state-check errors keep precedence.
    type Capture<T> = { ok: true; value: T } | { ok: false; error: unknown };
    const capture = <T>(fn: () => T): Capture<T> => { try { return {ok:true,value:fn()}; } catch (error) { return {ok:false,error}; } };
    const get = <T>(value: Capture<T>): T => { if (!value.ok) throw value.error; return value.value; };
    // Initial observers must see the same observation as state checks even if
    // a check mutates an external alias. Defer lookup/copy errors until the
    // observer stage, after any state-check failure has been reported.
    const initialSnapshot = !t && observer ? capture(() => state(m, s)) : undefined;
    const initialSelection = !t ? capture(() => observer?.initial) : undefined;
    const initialObserver = initialSelection?.ok ? initialSelection.value : undefined;
    const initialClone = initialObserver ? capture(() => { const clone = m.cloneState; return (value: S) => call('snapshot state', () => clone.call(m, value)); }) : undefined;
    const initialState = initialObserver ? capture(() => get(initialClone!)(get(initialSnapshot!))) : undefined;
    const transitionSelection = t ? consumers?.transition ?? capture(() => {
        const hook = m.checkTransitionInto;
        if (hook !== undefined && typeof hook !== 'function') throw new ModelError('append check hook must be a function');
        const legacy = hook === undefined ? m.checkTransition : undefined;
        if (hook === undefined && legacy === undefined) return undefined;
        return {
            checkTransitionInto: typeof hook === 'function' ? hook.bind(m) : hook,
            checkTransition: typeof legacy === 'function' ? legacy.bind(m) : legacy,
        };
    }) : undefined;
    const observerSelection = t ? consumers?.observe ?? capture(() => observer?.transition) : undefined;
    const hasTransitionCheck = transitionSelection?.ok && transitionSelection.value !== undefined;
    const observeTransition = observerSelection?.ok ? observerSelection.value : undefined;
    const copies = t && (hasTransitionCheck || observeTransition) ? capture(() => {
        const cs=m.cloneState, ci=m.cloneInput, co=m.cloneOutput;
        return {cloneState:(v:S)=>cs.call(m,v), cloneInput:(v:I)=>ci.call(m,v), cloneOutput:(v:O)=>co.call(m,v)} as Model<S,I,O>;
    }) : undefined;
    const edge = t && (hasTransitionCheck || observeTransition) ? capture(() => {
        const snapshotModel=get(copies!);
        const capturedInput = input(snapshotModel, resolveInput ? resolveInput() : i as I);
        return {before:state(snapshotModel,resolveBefore ? resolveBefore() : before as S), input:capturedInput, transition:copyTransition(snapshotModel,resolveTransition ? resolveTransition() : t)};
    }) : undefined;
    const prefix = observeTransition ? capture(() => history.map(v => input(get(copies!),v))) : undefined;
    const notify = (error: unknown, batches: [
        Violation['phase'],
        number,
        number,
        boolean
    ][]): never => {
        const secondary: string[] = [];
        if (observer?.checkError)
            for (const [phase, start, end, complete] of batches) {
                try {
                    call('observer check_error', () => observer.checkError!(phase, copyChecks(storage.slice(start, end)), complete));
                }
                catch (e) {
                    secondary.push(e instanceof Error ? e.message : String(e));
                }
            }
        if (secondary.length)
            throw new ModelError(`${error instanceof Error ? error.message : String(error)}; ${secondary.join('; ')}`);
        throw error;
    };
    try {
        withCheckSink(storage, sink => appendStateChecks(m, resolveState ? resolveState() : s, sink, t ? 'check_state' : 'initial check_state'));
    }
    catch (error) {
        notify(error, [[phase, 0, storage.length, false]]);
    }
    const stateCount = storage.length;
    if (t) {
        try {
            if (transitionSelection && !transitionSelection.ok) throw transitionSelection.error;
            if (hasTransitionCheck && edge) {
                const e = get(edge);
                const hooks=get(transitionSelection!)!, snapshotModel=get(copies!);
                const facade={...snapshotModel,...hooks} as Model<S,I,O>;
                call('transition check', () => withCheckSink(storage, sink => appendTransitionChecks(facade, e.before, e.input, e.transition, sink)));
            }
        }
        catch (error) {
            notify(error, [['state', 0, stateCount, true], ['transition', stateCount, storage.length, false]]);
        }
        if (observerSelection && !observerSelection.ok)
            call('observer transition', () => { throw observerSelection.error; });
        if (observeTransition && edge && prefix)
            call('observer transition', () => { const e = get(edge), snapshotModel=get(copies!); observeTransition.call(observer, state(snapshotModel,e.before), input(snapshotModel,e.input), copyTransition(snapshotModel,e.transition), copyChecks(storage.slice(0, stateCount)), copyChecks(storage.slice(stateCount)), get(prefix).map(v => input(snapshotModel,v))); });
    }
    else {
        if (initialSelection && !initialSelection.ok)
            call('observer initial', () => { throw initialSelection.error; });
        if (initialObserver && initialState)
            call('observer initial', () => initialObserver.call(observer, get(initialClone!)(get(initialState)), copyChecks(storage)));
    }
    const violations: Violation[] = [];
    let skipped = 0n;
    // A single reusable buffer stores both batches. Boundary offsets avoid temporary batch arrays when unobserved.
    for (let index = 0; index < storage.length; index++) {
        const c = storage[index];
        if (c.status === 'failed')
            violations.push({ phase: t ? (index < stateCount ? 'state' : 'transition') : 'initial_state', check: c });
        else if (c.status === 'skipped')
            skipped++;
    }
    return { violations, skipped, stateCount };
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
export function addSkippedChecks(left: bigint, right: bigint): bigint {
    const total = left + right, maximum = (1n << 64n) - 1n;
    return total > maximum ? maximum : total;
}
