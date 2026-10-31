import { count64, countLike, arrayGrowth, type Count } from './counts.js';
/** Internal shared fuzz kernel. Drivers choose candidates and learn only after complete passing observations. */
import { Rng, ModelError, ConfigError, natural, disposition, type Model, type Transition, type Check } from './core.js';
import { NO_INPUT, type GenerateModel, type FuzzConfig, type FuzzReport } from './search.js';
import { searchChecks, addSkippedChecks, type SearchObserver } from './search_support.js';
import { guidedCall } from './guided_sync.js';
export interface FuzzDriver<S, I, O> {
    candidate(previous: readonly I[], rng: Rng, percent: number): { inputs: I[]; replace: number | null };
    initial(state: S, caseNumber: bigint): void;
    transition(before: S, input: I, after: Transition<S, O>, inputs: readonly I[], caseNumber: bigint, transition: bigint): void;
}
const MASK = (1n << 64n) - 1n;
function u64(value: bigint, name: string): void {
    if (typeof value !== 'bigint' || value < 0n || value > MASK) throw new ConfigError(`${name} must be u64 bigint`);
}
export function snapshotFuzzConfig<C extends Count>(config: FuzzConfig<C>, guided = false): FuzzConfig<C> {
    // Rust receives copied configs; callback-side mutation cannot change validated budgets.
    const {seed,cases,maxSteps,maxTransitions,mutationPercent}=config;
    config={seed,cases,maxSteps,maxTransitions,mutationPercent};
    u64(config.seed, 'seed');
    u64(config.maxTransitions, 'maxTransitions');
    count64(config.cases, 'cases');
    count64(config.maxSteps, 'maxSteps');
    if (guided && config.mutationPercent > 100) throw new ConfigError('mutation_percent must be in 0..=100');
    natural(config.mutationPercent, 'mutationPercent', 0, 100);
    return config;
}
function call<T>(stage: string, fn: () => T): T {
    try {
        return fn();
    }
    catch (e) {
        // Preserve ordinary fuzz's exception formatting, including a secondary
        // exception thrown by an Error.message getter or String conversion.
        throw new ModelError(`${stage}: ${e instanceof Error ? e.message : String(e)}`);
    }
}
const state = <S, I, O>(m: Model<S, I, O>, s: S): S => call('snapshot state', () => m.cloneState(s));
const input = <S, I, O>(m: Model<S, I, O>, i: I): I => call('snapshot input', () => m.cloneInput(i));
function copyTransition<S, I, O>(m: Model<S, I, O>, t: Transition<S, O>): Transition<S, O> { return call('snapshot transition', () => ({ state: m.cloneState(t.state), outputs: Array.from(t.outputs, o => m.cloneOutput(o)), disposition: disposition(t.disposition.kind, t.disposition.reason) })); }
type Captured<T> = { ok: true; value: T } | { ok: false; error: unknown };
function capture<T>(fn: () => T): Captured<T> { try { return {ok:true,value:fn()}; } catch (error) { return {ok:false,error}; } }
function take<T>(value: Captured<T>): T { if (!value.ok) throw value.error; return value.value; }
function step<S, I, O>(m: Model<S, I, O>, s: S, i: I): Transition<S, O> { return copyTransition(m, call('step', () => m.step(state(m, s), input(m, i)))); }
function enabled<S, I, O>(m: GenerateModel<S, I, O>, s: S, i: I): boolean {
    return call('is_enabled', () => {
        const predicate = m.isEnabled;
        if (!predicate) return true;
        const cloneInput = m.cloneInput, cloneState = m.cloneState;
        const capturedInput = call('snapshot input', () => cloneInput.call(m, i));
        const capturedState = call('snapshot state', () => cloneState.call(m, s));
        const v = predicate.call(m, capturedState, capturedInput);
        if (typeof v !== 'boolean')
            throw new TypeError('isEnabled must return boolean');
        return v;
    });
}
export function runFuzz<S, I, O, C extends Count>(m: GenerateModel<S, I, O>, config: FuzzConfig<C>, stop: () => 'cancelled' | 'deadline' | null, observer?: SearchObserver<S, I, O>, driver?: FuzzDriver<S, I, O>): FuzzReport<I, C> {
    const rng = new Rng(config.seed);
    let previous: I[] = [];
    const checkStorage:Check[]=[];
    const r: FuzzReport<I, C> = { termination: 'cases_completed', seed: config.seed, cases: countLike(0n, config.cases), transitions: 0n, skipped_checks: 0n, failure: null };
    const stopped = () => {
        const reason = stop();
        if (reason)
            r.termination = reason;
        return reason !== null;
    };
    if (stopped())
        return r;
    for (let c = 0n; c < BigInt(config.cases); c++) {
        if (stopped())
            return r;
        let s = state(m, call('initial_state', () => m.initialState()));
        // Keep the check observation separate from continuation: cloning the
        // latter can invoke application code that mutates an external alias.
        const initialCheck = capture(() => state(m, s));
        const initialState = capture(() => state(m, s));
        r.cases = countLike(BigInt(r.cases) + 1n, config.cases);
        const initial = searchChecks(m, initialCheck.ok ? initialCheck.value : s, undefined, undefined, undefined, observer,[],checkStorage,undefined,()=>take(initialCheck));
        r.skipped_checks = addSkippedChecks(r.skipped_checks,initial.skipped);
        if (initial.violations.length) {
            r.termination = 'failure_found';
            r.failure = { inputs: [], violations: initial.violations };
            return r;
        }
        if (stopped())
            return r;
        if (driver || BigInt(config.maxSteps) > 0n) s = take(initialState);
        if (driver) {
            const continuation = capture(() => state(m, s));
            guidedCall('initial feedback', () => driver.initial(s, BigInt(r.cases)));
            if (stopped()) return r;
            if (BigInt(config.maxSteps) > 0n) s = take(continuation);
        }
        let mutated: I[] = [], replace: number | null = null;
        if (driver) {
            const candidate = driver.candidate(previous, rng, config.mutationPercent);
            mutated = candidate.inputs;
            replace = candidate.replace;
        } else if (previous.length && (rng.index(100n) as bigint) < BigInt(config.mutationPercent)) {
            mutated = previous.map(i => input(m, i));
            const start = Number(rng.index(BigInt(mutated.length)));
            const length = 1 + Number(rng.index(BigInt(mutated.length - start)));
            mutated.splice(start, length);
            replace = Number(rng.index(BigInt(mutated.length + 1)));
        }
        const inputs: I[] = [];
        for (let n = 0n; n < BigInt(config.maxSteps); n++) {
            if (r.transitions === config.maxTransitions) {
                r.termination = 'transition_limit';
                return r;
            }
            if (stopped())
                return r;
            const executionState = capture(() => state(m, s));
            const current = executionState.ok ? executionState.value : s;
            const admissionState = capture(() => state(m, current));
            const admission = admissionState.ok ? admissionState.value : current;
            // Feedback's before-state must predate enabled/generation callbacks.
            const feedbackBefore = driver ? capture(() => state(m, current)) : undefined;
            let next: I | typeof NO_INPUT = NO_INPUT, reused = false;
            let admitted: Captured<I> | undefined;
            if ((replace === null || n !== BigInt(replace)) && n < BigInt(mutated.length)) {
                const candidate = mutated[Number(n)];
                const captured = capture(() => input(m, candidate));
                if (enabled(m, admission, captured.ok ? captured.value : candidate)) {
                    admitted = captured;
                    next = captured.ok ? captured.value : candidate;
                    reused = true;
                }
            }
            if (stopped())
                return r;
            if (!reused)
                next = call('generate', () => { const v = m.generate(state(m, admission), rng); return v === NO_INPUT ? NO_INPUT : input(m, v); });
            if (stopped())
                return r;
            if (next === NO_INPUT)
                break;
            if (!admitted) admitted = capture(() => input(m, next as I));
            const valid = reused || enabled(m, admission, admitted.ok ? admitted.value : next);
            if (stopped())
                return r;
            if (!valid)
                throw new ModelError('generate returned a causally disabled input');
            take(admissionState);
            const beforeState = take(executionState);
            // Retain the admitted input before reducer callbacks can mutate an
            // external alias. Clone hooks must still provide detached values.
            const observedInput = take(admitted);
            const feedbackInput = driver ? capture(() => input(m, observedInput)) : undefined;
            const edgeBefore = capture(() => state(m, beforeState));
            const edgeInput = capture(() => input(m, observedInput));
            const stepInput = input(m, observedInput);
            const t = step(m, beforeState, stepInput);
            r.transitions++;
            const edgeState = capture(() => state(m, t.state));
            const edgeTransition = capture(() => copyTransition(m, t));
            const successor = capture(() => state(m, t.state));
            const feedbackTransition = driver ? capture(() => copyTransition(m, t)) : undefined;
            arrayGrowth(inputs.length);
            inputs.push(input(m, observedInput));
            const feedbackHistory = driver ? capture(() => inputs.map(i => input(m, i))) : undefined;
            const observed = searchChecks(m, t.state, beforeState, observedInput, t, observer, inputs,checkStorage,
                () => take(edgeInput), () => take(edgeState), () => take(edgeTransition), () => take(edgeBefore));
            r.skipped_checks = addSkippedChecks(r.skipped_checks,observed.skipped);
            if (observed.violations.length) {
                r.termination = 'failure_found';
                r.failure = { inputs: inputs.map(i => input(m, i)), violations: observed.violations };
                return r;
            }
            if (stopped())
                return r;
            if (driver) {
                guidedCall('transition feedback', () => driver.transition(take(feedbackBefore!), take(feedbackInput!), take(feedbackTransition!), take(feedbackHistory!), BigInt(r.cases), r.transitions));
                if (stopped()) return r;
            }
            if (n + 1n < BigInt(config.maxSteps) && r.transitions < config.maxTransitions) s = take(successor);
        }
        previous = inputs;
    }
    return r;
}
