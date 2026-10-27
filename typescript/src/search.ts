import { runControl, searchChecks, addSkippedChecks, type RunLimits, type SearchObserver } from './search_support.js';
export type { RunLimits, SearchObserver } from './search_support.js';
/** Independent generation and causal search. All control is cooperative between callbacks. */
import { Model, EnumerateModel, Transition, Check, Violation, Failure, Rng, ModelError, ConfigError, natural, check, disposition } from './core.js';
/** Distinct exhaustion marker; null and undefined remain ordinary model inputs. */
export const NO_INPUT: unique symbol = Symbol('stateless.no_input');
export interface GenerateModel<S, I, O> extends Model<S, I, O> {
    generate(state: S, rng: Rng): I | typeof NO_INPUT;
    isEnabled?(state: S, input: I): boolean;
    simplerInputs?(input: I): Iterable<I>;
    equalInputs(left: I, right: I): boolean;
}
export interface AutoOptions {
    readonly maxCandidates: number;
}
export const DEFAULT_AUTO: AutoOptions = Object.freeze({ maxCandidates: 100000 });
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
function step<S, I, O>(m: Model<S, I, O>, s: S, i: I): Transition<S, O> { return copyTransition(m, call('step', () => m.step(state(m, s), input(m, i)))); }
function enabled<S, I, O>(m: GenerateModel<S, I, O>, s: S, i: I): boolean {
    return call('is_enabled', () => {
        const v = m.isEnabled ? m.isEnabled(state(m, s), input(m, i)) : true;
        if (typeof v !== 'boolean')
            throw new TypeError('isEnabled must return boolean');
        return v;
    });
}
/** Transactional reservoir sampling: an oversized/erroring domain never advances rng. */
export function sampleEnumerated<S, I, O>(m: EnumerateModel<S, I, O>, s: S, rng: Rng, options: AutoOptions = DEFAULT_AUTO): I | typeof NO_INPUT {
    const maxCandidates=options.maxCandidates;
    natural(maxCandidates, 'maxCandidates', 1);
    const candidate = rng.clone();
    let selected: I | typeof NO_INPUT = NO_INPUT, count = 0;
    const iterator = call('enumerate inputs', () => m.inputs(state(m, s))[Symbol.iterator]());
    for (;;) {
        const item = call('enumerate inputs next', () => iterator.next());
        if (item.done)
            break;
        if (count === maxCandidates)
            throw new ModelError('automatic input domain exceeds max_candidates');
        count++;
        if (count === 1 || candidate.index(BigInt(count)) === 0n)
            selected = input(m, item.value);
    }
    rng.copyFrom(candidate);
    return selected;
}
export function containsEnumerated<S, I, O>(m: EnumerateModel<S, I, O> & {
    equalInputs(left: I, right: I): boolean;
}, s: S, i: I, options: AutoOptions = DEFAULT_AUTO): boolean {
    const maxCandidates=options.maxCandidates;
    natural(maxCandidates, 'maxCandidates', 1);
    let count = 0;
    const iterator = call('enumerate inputs', () => m.inputs(state(m, s))[Symbol.iterator]());
    for (;;) {
        const item = call('enumerate inputs next', () => iterator.next());
        if (item.done)
            return false;
        if (count === maxCandidates)
            throw new ModelError('automatic input domain exceeds max_candidates');
        count++;
        if (call('input equality', () => boolean(m.equalInputs(input(m, item.value), input(m, i)), 'equalInputs')))
            return true;
    }
}
/** Preserve every model capability and bind delegated methods to their original receiver. */
export function automatic<M extends EnumerateModel<any, any, any> & {
    equalInputs(left: any, right: any): boolean;
}>(m: M, options: AutoOptions = DEFAULT_AUTO): M & (M extends Model<infer S, infer I, infer O> ? GenerateModel<S, I, O> : never) {
    const maxCandidates=options.maxCandidates;
    natural(maxCandidates, 'maxCandidates', 1);
    const opts = { maxCandidates };
    const generate = (s: any, r: Rng) => sampleEnumerated(m, s, r, opts);
    const isEnabled = (s: any, i: any) => containsEnumerated(m, s, i, opts);
    const bound = new Map<PropertyKey, unknown>();
    const facade = Object.assign(Object.create(Object.getPrototypeOf(m)), { generate, isEnabled }) as M;
    return new Proxy(facade, { get(target, key) {
            if (Object.hasOwn(target, key))
                return Reflect.get(target, key);
            const value = Reflect.get(m, key, m);
            if (typeof value !== 'function')
                return value;
            // Cache only while the underlying method is unchanged.
            const prior = bound.get(key) as {
                source: unknown;
                method: unknown;
            } | undefined;
            if (prior?.source === value)
                return prior.method;
            const method = value.bind(m);
            bound.set(key, { source: value, method });
            return method;
        } }) as M & (M extends Model<infer S, infer I, infer O> ? GenerateModel<S, I, O> : never);
}
export interface ShrinkLimits extends RunLimits {
    readonly maxReplayedTransitions?: bigint;
}
type ControlStop = 'cancelled' | 'deadline';
const MASK = (1n << 64n) - 1n;
function u64(n: bigint, name: string): void {
    if (typeof n !== 'bigint' || n < 0n || n > MASK)
        throw new ConfigError(`${name} must be u64 bigint`);
}
export interface FuzzConfig {
    readonly seed: bigint;
    readonly cases: number;
    readonly maxSteps: number;
    readonly maxTransitions: bigint;
    readonly mutationPercent: number;
}
export const DEFAULT_FUZZ: FuzzConfig = Object.freeze({ seed: 0n, cases: 1000, maxSteps: 100, maxTransitions: 100000n, mutationPercent: 50 });
export interface FuzzReport<I> {
    termination: 'cases_completed' | 'transition_limit' | 'failure_found' | ControlStop;
    seed: bigint;
    cases: number;
    transitions: bigint;
    skipped_checks: bigint;
    failure: Failure<I> | null;
}
export function fuzz<S, I, O>(m: GenerateModel<S, I, O>, config: FuzzConfig = DEFAULT_FUZZ, limits: RunLimits = {}, observer?: SearchObserver<S, I, O>): FuzzReport<I> {
    // Rust receives copied configs; callback-side mutation cannot change validated budgets.
    const {seed,cases,maxSteps,maxTransitions,mutationPercent}=config;
    config={seed,cases,maxSteps,maxTransitions,mutationPercent};
    u64(config.seed, 'seed');
    u64(config.maxTransitions, 'maxTransitions');
    natural(config.cases, 'cases');
    natural(config.maxSteps, 'maxSteps');
    natural(config.mutationPercent, 'mutationPercent', 0, 100);
    const stop = runControl(limits), rng = new Rng(config.seed);
    let previous: I[] = [];
    const checkStorage:Check[]=[];
    const r: FuzzReport<I> = { termination: 'cases_completed', seed: config.seed, cases: 0, transitions: 0n, skipped_checks: 0n, failure: null };
    const stopped = () => {
        const reason = stop();
        if (reason)
            r.termination = reason;
        return reason !== null;
    };
    if (stopped())
        return r;
    for (let c = 0; c < config.cases; c++) {
        if (stopped())
            return r;
        let s = state(m, call('initial_state', () => m.initialState()));
        r.cases++;
        const initial = searchChecks(m, s, undefined, undefined, undefined, observer,[],checkStorage);
        r.skipped_checks = addSkippedChecks(r.skipped_checks,initial.skipped);
        if (initial.violations.length) {
            r.termination = 'failure_found';
            r.failure = { inputs: [], violations: initial.violations };
            return r;
        }
        if (stopped())
            return r;
        let mutated: I[] = [], replace: number | null = null;
        if (previous.length && (rng.index(100n) as bigint) < BigInt(config.mutationPercent)) {
            mutated = previous.map(i => input(m, i));
            const start = Number(rng.index(BigInt(mutated.length)));
            const length = 1 + Number(rng.index(BigInt(mutated.length - start)));
            mutated.splice(start, length);
            replace = Number(rng.index(BigInt(mutated.length + 1)));
        }
        const inputs: I[] = [];
        for (let n = 0; n < config.maxSteps; n++) {
            if (r.transitions === config.maxTransitions) {
                r.termination = 'transition_limit';
                return r;
            }
            if (stopped())
                return r;
            let next: I | typeof NO_INPUT = NO_INPUT, reused = false;
            if (n !== replace && n < mutated.length && enabled(m, s, mutated[n])) {
                next = input(m, mutated[n]);
                reused = true;
            }
            if (stopped())
                return r;
            if (!reused)
                next = call('generate', () => { const v = m.generate(state(m, s), rng); return v === NO_INPUT ? NO_INPUT : input(m, v); });
            if (stopped())
                return r;
            if (next === NO_INPUT)
                break;
            const valid = reused || enabled(m, s, next);
            if (stopped())
                return r;
            if (!valid)
                throw new ModelError('generate returned a causally disabled input');
            const t = step(m, s, next);
            r.transitions++;
            inputs.push(input(m, next));
            const observed = searchChecks(m, t.state, s, next, t, observer, inputs,checkStorage);
            r.skipped_checks = addSkippedChecks(r.skipped_checks,observed.skipped);
            if (observed.violations.length) {
                r.termination = 'failure_found';
                r.failure = { inputs: inputs.map(i => input(m, i)), violations: observed.violations };
                return r;
            }
            if (stopped())
                return r;
            s = t.state;
        }
        previous = inputs;
    }
    return r;
}
/** Observe every fully checked edge, including the first failing edge. */
export function fuzzObserved<S, I, O>(m: GenerateModel<S, I, O>, config: FuzzConfig, observer: SearchObserver<S, I, O>, limits: RunLimits = {}): FuzzReport<I> { return fuzz(m, config, limits, observer); }
export interface ShrinkConfig {
    readonly maxAttempts: number;
}
export const DEFAULT_SHRINK: ShrinkConfig = Object.freeze({ maxAttempts: 10000 });
export type ShrinkTermination = 'search_complete' | 'attempt_limit' | 'transition_limit' | ControlStop;
export type ShrinkOperation = {
    kind: 'validate_original';
} | {
    kind: 'remove';
    start: number;
    end: number;
} | {
    kind: 'simplify';
    index: number;
    alternative: number;
};
export type ShrinkOutcome = 'retained' | 'invalid_causality' | 'different_failure' | 'no_failure' | 'already_seen' | 'resource_limit';
export interface ShrinkAttempt {
    readonly attempt: number;
    readonly candidate_length: number;
    readonly operation: ShrinkOperation;
    readonly outcome: ShrinkOutcome;
}
export interface ShrinkReport<I> {
    original: Failure<I>;
    minimized: Failure<I>;
    attempts: number;
    termination: ShrinkTermination;
    history: ShrinkAttempt[];
    replayed_transitions: bigint;
    validated_original: boolean;
}
export function shrink<S, I, O>(m: GenerateModel<S, I, O>, original: Failure<I>, config: ShrinkConfig = DEFAULT_SHRINK, limits: ShrinkLimits = {}): ShrinkReport<I> {
    const maxAttempts=config.maxAttempts;
    const maxReplayedTransitions=limits.maxReplayedTransitions;
    natural(maxAttempts, 'maxAttempts', 1);
    if (maxReplayedTransitions !== undefined)
        u64(maxReplayedTransitions, 'maxReplayedTransitions');
    const stop = runControl(limits);
    const checkStorage:Check[]=[];
    const copy = (f: Failure<I>): Failure<I> => ({ inputs: f.inputs.map(i => input(m, i)), violations: f.violations.map(v => ({ phase: v.phase, check: check(v.check.id, v.check.status, v.check.details) })) });
    const supplied = copy(original), target = supplied.violations[0];
    if (!target || target.check.status !== 'failed')
        throw new ModelError('cannot shrink a sequence without a target violation');
    const r: ShrinkReport<I> = { original: copy(supplied), minimized: copy(supplied), attempts: 0, termination: 'search_complete', history: [], replayed_transitions: 0n, validated_original: false };
    type Candidate = {
        kind: 'failure';
        failure: Failure<I>;
    } | {
        kind: 'invalid';
    } | {
        kind: 'pass';
    } | {
        kind: 'stopped';
        reason: ShrinkTermination;
    };
    const replay = (inputs: readonly I[]): Candidate => {
        let reason = stop();
        if (reason)
            return { kind: 'stopped', reason };
        let s = state(m, call('shrink initial_state', () => m.initialState()));
        const initial = searchChecks(m,s,undefined,undefined,undefined,undefined,[],checkStorage);
        if (initial.violations.length)
            return { kind: 'failure', failure: { inputs: [], violations: initial.violations } };
        reason = stop();
        if (reason)
            return { kind: 'stopped', reason };
        for (let n = 0; n < inputs.length; n++) {
            reason = stop();
            if (reason)
                return { kind: 'stopped', reason };
            if (maxReplayedTransitions !== undefined && r.replayed_transitions >= maxReplayedTransitions)
                return { kind: 'stopped', reason: 'transition_limit' };
            const valid = enabled(m, s, inputs[n]);
            reason = stop();
            if (reason)
                return { kind: 'stopped', reason };
            if (!valid)
                return { kind: 'invalid' };
            const t = step(m, s, inputs[n]);
            r.replayed_transitions++;
            const observed = searchChecks(m,t.state,s,inputs[n],t,undefined,[],checkStorage);
            if (observed.violations.length)
                return { kind: 'failure', failure: { inputs: inputs.slice(0, n + 1).map(i => input(m, i)), violations: observed.violations } };
            reason = stop();
            if (reason)
                return { kind: 'stopped', reason };
            s = t.state;
        }
        return { kind: 'pass' };
    };
    const hasTarget = (f: Failure<I>) => f.violations.some(v => v.phase === target.phase && v.check.id === target.check.id && v.check.status === 'failed');
    const validation = replay(supplied.inputs);
    r.attempts = 1;
    if (validation.kind === 'stopped') {
        r.termination = validation.reason;
        r.history.push({ attempt: 1, candidate_length: supplied.inputs.length, operation: { kind: 'validate_original' }, outcome: 'resource_limit' });
        return r;
    }
    if (validation.kind !== 'failure')
        throw new ModelError('original failure does not reproduce with valid causality');
    if (!hasTarget(validation.failure))
        throw new ModelError('original failure does not reproduce the target check and phase');
    r.minimized = copy(validation.failure);
    r.validated_original = true;
    r.history.push({ attempt: 1, candidate_length: supplied.inputs.length, operation: { kind: 'validate_original' }, outcome: 'retained' });
    const seen: I[][] = [r.minimized.inputs.map(i => input(m, i))];
    const known = (values: readonly I[]) => {
        for (const prior of seen) {
            if (prior.length !== values.length)
                continue;
            let equal = true;
            for (let n = 0; n < prior.length; n++) {
                if (stop())
                    return false;
                const same = call('input_equal', () => boolean(m.equalInputs(input(m, prior[n]), input(m, values[n])), 'equalInputs'));
                if (stop())
                    return false;
                if (!same) {
                    equal = false;
                    break;
                }
            }
            if (equal)
                return true;
        }
        return false;
    };
    const interrupted = () => {
        const reason = stop();
        if (reason)
            r.termination = reason;
        return reason !== null;
    };
    const attempt = (values: I[], operation: ShrinkOperation): boolean => {
        if (r.attempts === maxAttempts) {
            r.termination = 'attempt_limit';
            return false;
        }
        r.attempts++;
        const candidate = replay(values);
        let outcome: ShrinkOutcome;
        if (candidate.kind === 'stopped') {
            r.termination = candidate.reason;
            outcome = 'resource_limit';
        }
        else if (candidate.kind === 'invalid')
            outcome = 'invalid_causality';
        else if (candidate.kind === 'pass')
            outcome = 'no_failure';
        else if (!hasTarget(candidate.failure))
            outcome = 'different_failure';
        else {
            const wasSeen = known(candidate.failure.inputs);
            if (interrupted())
                outcome = 'resource_limit';
            else if (wasSeen)
                outcome = 'already_seen';
            else {
                seen.push(candidate.failure.inputs.map(i => input(m, i)));
                r.minimized = copy(candidate.failure);
                outcome = 'retained';
            }
        }
        r.history.push({ attempt: r.attempts, candidate_length: values.length, operation, outcome });
        return outcome !== 'resource_limit';
    };
    for (;;) {
        let groups = 2;
        while (r.minimized.inputs.length) {
            const length = r.minimized.inputs.length, chunk = Math.ceil(length / groups);
            let accepted = false;
            for (let start = 0; start < length; start += chunk) {
                if (interrupted())
                    return r;
                const end = Math.min(start + chunk, length), candidate = r.minimized.inputs.map(i => input(m, i));
                candidate.splice(start, end - start);
                const wasSeen = known(candidate);
                if (interrupted())
                    return r;
                if (wasSeen)
                    continue;
                if (!attempt(candidate, { kind: 'remove', start, end }))
                    return r;
                if (r.history.at(-1)!.outcome === 'retained') {
                    accepted = true;
                    groups = Math.max(groups - 1, 2);
                    break;
                }
            }
            if (accepted)
                continue;
            if (groups >= length)
                break;
            groups = Math.min(groups * 2, length);
        }
        let simplified = false;
        positions: for (let index = 0; index < r.minimized.inputs.length; index++) {
            if (interrupted())
                return r;
            const alternatives = call('simpler_inputs', () => (m.simplerInputs?.(input(m, r.minimized.inputs[index])) ?? [])[Symbol.iterator]());
            if (interrupted())
                return r;
            let alternative = 0;
            for (;;) {
                if (interrupted())
                    return r;
                const item = call('simpler_inputs next', () => alternatives.next());
                if (interrupted())
                    return r;
                if (item.done)
                    break;
                const candidate = r.minimized.inputs.map(i => input(m, i));
                candidate[index] = input(m, item.value);
                const alt = alternative++;
                natural(alternative, 'simplification index');
                const wasSeen = known(candidate);
                if (interrupted())
                    return r;
                if (wasSeen)
                    continue;
                if (!attempt(candidate, { kind: 'simplify', index, alternative: alt }))
                    return r;
                if (r.history.at(-1)!.outcome === 'retained') {
                    simplified = true;
                    break positions;
                }
            }
        }
        if (!simplified)
            return r;
    }
}
