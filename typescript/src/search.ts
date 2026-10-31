import { count64, countLike, arrayGrowth, increment64, type Count, type CountValue } from './counts.js';
import { runFuzz, snapshotFuzzConfig } from './fuzz_kernel.js';
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
export interface AutoOptions<C extends Count = number> {
    readonly maxCandidates: C;
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
function closeSimplifier<I>(iterator: Iterator<I>): void {
    try { iterator.return?.(); }
    catch (error) {
        let detail = 'unknown error';
        try { const text: unknown = error instanceof Error ? error.message : String(error); if (typeof text === 'string') detail = text; } catch { /* Hostile thrown value. */ }
        throw new ModelError(`simpler_inputs close: ${detail}`);
    }
}
const state = <S, I, O>(m: Model<S, I, O>, s: S): S => call('snapshot state', () => m.cloneState(s));
const input = <S, I, O>(m: Model<S, I, O>, i: I): I => call('snapshot input', () => m.cloneInput(i));
function copyTransition<S, I, O>(m: Model<S, I, O>, t: Transition<S, O>): Transition<S, O> { return call('snapshot transition', () => ({ state: m.cloneState(t.state), outputs: Array.from(t.outputs, o => m.cloneOutput(o)), disposition: disposition(t.disposition.kind, t.disposition.reason) })); }
function step<S, I, O>(m: Model<S, I, O>, s: S, i: I): Transition<S, O> {
    const cloneInput = m.cloneInput, cloneState = m.cloneState, reduce = m.step;
    const capturedInput = call('snapshot input', () => cloneInput.call(m, i));
    const capturedState = call('snapshot state', () => cloneState.call(m, s));
    return copyTransition(m, call('step', () => reduce.call(m, capturedState, capturedInput)));
}
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
/** Transactional reservoir sampling: an oversized/erroring domain never advances rng. */
export function sampleEnumerated<S, I, O>(m: EnumerateModel<S, I, O>, s: S, rng: Rng, options: AutoOptions<Count> = DEFAULT_AUTO, poll?: () => boolean): I | typeof NO_INPUT {
    const maxCandidates=options.maxCandidates;
    if (count64(maxCandidates, 'maxCandidates') === 0n) throw new ConfigError('maxCandidates must be positive');
    const candidate = rng.clone();
    let selected: I | typeof NO_INPUT = NO_INPUT, count = 0n;
    const iterator = call('enumerate inputs', () => m.inputs(state(m, s))[Symbol.iterator]());
    let exhausted = false, failed = false;
    try {
        for (;;) {
            if (poll?.()) { failed = true; return NO_INPUT; }
            const item = call('enumerate inputs next', () => iterator.next());
            if (poll?.()) { failed = true; return NO_INPUT; }
            if (item.done) { exhausted = true; break; }
            if (count === BigInt(maxCandidates))
                throw new ModelError('automatic input domain exceeds max_candidates');
            count++;
            if (count === 1n || candidate.index(BigInt(count)) === 0n)
                selected = input(m, item.value);
        }
    } catch (error) { failed = true; throw error; }
    finally { if (!exhausted) { if (failed) { try { iterator.return?.(); } catch {} } else iterator.return?.(); } }
    if (poll?.()) return NO_INPUT;
    rng.copyFrom(candidate);
    return selected;
}
export function containsEnumerated<S, I, O>(m: EnumerateModel<S, I, O> & {
    equalInputs(left: I, right: I): boolean;
}, s: S, i: I, options: AutoOptions<Count> = DEFAULT_AUTO, poll?: () => boolean): boolean {
    const maxCandidates=options.maxCandidates;
    if (count64(maxCandidates, 'maxCandidates') === 0n) throw new ConfigError('maxCandidates must be positive');
    const cloneInput = m.cloneInput, cloneState = m.cloneState, inputs = m.inputs, equalInputs = m.equalInputs;
    const copyInput = (value: I): I => call('snapshot input', () => cloneInput.call(m, value));
    // Preserve the query before application state/domain callbacks can mutate an
    // external alias. Defer a failed copy until a candidate needs comparison so
    // an empty or failing domain keeps its normal outcome.
    let query: { ok: true; value: I } | { ok: false; error: unknown };
    try { query = { ok: true, value: copyInput(i) }; }
    catch (error) { query = { ok: false, error }; }
    let count = 0n;
    const iterator = call('enumerate inputs', () => inputs.call(m, call('snapshot state', () => cloneState.call(m, s)))[Symbol.iterator]());
    let exhausted = false, failed = false;
    try {
        for (;;) {
            if (poll?.()) { failed = true; return false; }
            const item = call('enumerate inputs next', () => iterator.next());
            if (poll?.()) { failed = true; return false; }
            if (item.done) { exhausted = true; return false; }
            if (count === BigInt(maxCandidates))
                throw new ModelError('automatic input domain exceeds max_candidates');
            count++;
            if (!query.ok) throw query.error;
            const equal = call('input equality', () => boolean(equalInputs.call(m, copyInput(item.value), copyInput(query.value)), 'equalInputs'));
            if (poll?.()) { failed = true; return false; }
            if (equal) return true;
        }
    } catch (error) { failed = true; throw error; }
    finally { if (!exhausted) { if (failed) { try { iterator.return?.(); } catch {} } else iterator.return?.(); } }
}
/** Preserve every model capability and bind delegated methods to their original receiver. */
export function automatic<M extends EnumerateModel<any, any, any> & {
    equalInputs(left: any, right: any): boolean;
}>(m: M, options: AutoOptions<Count> = DEFAULT_AUTO): M & (M extends Model<infer S, infer I, infer O> ? GenerateModel<S, I, O> : never) {
    return automaticControlled(m, options);
}
export function automaticControlled<M extends EnumerateModel<any, any, any> & { equalInputs(left: any, right: any): boolean }>(m: M, options: AutoOptions<Count> = DEFAULT_AUTO, poll?: () => boolean): M & (M extends Model<infer S, infer I, infer O> ? GenerateModel<S, I, O> : never) {
    const maxCandidates=options.maxCandidates;
    if (count64(maxCandidates, 'maxCandidates') === 0n) throw new ConfigError('maxCandidates must be positive');
    const opts = { maxCandidates };
    const generate = (s: any, r: Rng) => sampleEnumerated(m, s, r, opts, poll);
    const isEnabled = (s: any, i: any) => containsEnumerated(m, s, i, opts, poll);
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
export interface FuzzConfig<C extends Count = number> {
    readonly seed: bigint;
    readonly cases: C;
    readonly maxSteps: C;
    readonly maxTransitions: bigint;
    readonly mutationPercent: number;
}
export const DEFAULT_FUZZ: FuzzConfig = Object.freeze({ seed: 0n, cases: 1000, maxSteps: 100, maxTransitions: 100000n, mutationPercent: 50 });
export interface FuzzReport<I, C extends Count = number> {
    termination: 'cases_completed' | 'transition_limit' | 'failure_found' | ControlStop;
    seed: bigint;
    cases: CountValue<C>;
    transitions: bigint;
    skipped_checks: bigint;
    failure: Failure<I> | null;
}
export function fuzz<S, I, O>(m: GenerateModel<S, I, O>, config?: FuzzConfig, limits?: RunLimits, observer?: SearchObserver<S, I, O>): FuzzReport<I>;
export function fuzz<S, I, O, C extends Count = number>(m: GenerateModel<S, I, O>, config: FuzzConfig<C>, limits?: RunLimits, observer?: SearchObserver<S, I, O>): FuzzReport<I, C>;
export function fuzz<S, I, O, C extends Count = number>(m: GenerateModel<S, I, O>, config: FuzzConfig<C> = DEFAULT_FUZZ as FuzzConfig<C>, limits: RunLimits = {}, observer?: SearchObserver<S, I, O>): FuzzReport<I, C> {
    return runFuzz(m, snapshotFuzzConfig(config), runControl(limits), observer);
}
/** Cooperative finite-domain fuzzing; callback execution itself is not preempted. */
export function fuzzAuto<S, I, O, C extends Count = number>(model: EnumerateModel<S, I, O> & { equalInputs(left: I, right: I): boolean }, options: AutoOptions<Count>, config: FuzzConfig<C>, limits: RunLimits = {}, observer?: SearchObserver<S, I, O>): FuzzReport<I, C> {
    const maxCandidates = options.maxCandidates;
    if (count64(maxCandidates, 'maxCandidates') === 0n) throw new ConfigError('maxCandidates must be positive');
    const owned = snapshotFuzzConfig(config);
    const base = runControl(limits);
    let reason: ControlStop | null = null;
    const stop = () => reason ?? (reason = base());
    const modelAuto = automaticControlled(model, { maxCandidates }, () => stop() !== null);
    return runFuzz(modelAuto, owned, stop, observer);
}
/** Observe every fully checked edge, including the first failing edge. */
export function fuzzObserved<S, I, O, C extends Count = number>(m: GenerateModel<S, I, O>, config: FuzzConfig<C>, observer: SearchObserver<S, I, O>, limits: RunLimits = {}): FuzzReport<I, C> { return fuzz(m, config, limits, observer); }
export interface ShrinkConfig<C extends Count = number> {
    /** Bounds replay attempts and, separately, skipped duplicate simplifier hints. */
    readonly maxAttempts: C;
}
export const DEFAULT_SHRINK: ShrinkConfig = Object.freeze({ maxAttempts: 10000 });
export type ShrinkTermination = 'search_complete' | 'attempt_limit' | 'transition_limit' | ControlStop;
export type ShrinkOperation<C extends Count = number> = {
    kind: 'validate_original';
} | {
    kind: 'remove';
    start: CountValue<C>;
    end: CountValue<C>;
} | {
    kind: 'simplify';
    index: CountValue<C>;
    alternative: CountValue<C>;
};
export type ShrinkOutcome = 'retained' | 'invalid_causality' | 'different_failure' | 'no_failure' | 'already_seen' | 'resource_limit';
export interface ShrinkAttempt<C extends Count = number> {
    readonly attempt: CountValue<C>;
    readonly candidate_length: CountValue<C>;
    readonly operation: ShrinkOperation<C>;
    readonly outcome: ShrinkOutcome;
}
export interface ShrinkReport<I, C extends Count = number> {
    original: Failure<I>;
    minimized: Failure<I>;
    attempts: CountValue<C>;
    termination: ShrinkTermination;
    history: ShrinkAttempt<C>[];
    replayed_transitions: bigint;
    validated_original: boolean;
}
export function shrink<S, I, O>(m: GenerateModel<S, I, O>, original: Failure<I>, config?: ShrinkConfig, limits?: ShrinkLimits): ShrinkReport<I>;
export function shrink<S, I, O, C extends Count = number>(m: GenerateModel<S, I, O>, original: Failure<I>, config: ShrinkConfig<C>, limits?: ShrinkLimits): ShrinkReport<I, C>;
export function shrink<S, I, O, C extends Count = number>(m: GenerateModel<S, I, O>, original: Failure<I>, config: ShrinkConfig<C> = DEFAULT_SHRINK as ShrinkConfig<C>, limits: ShrinkLimits = {}): ShrinkReport<I, C> {
    const maxAttempts=config.maxAttempts;
    const maxReplayedTransitions=limits.maxReplayedTransitions;
    if (count64(maxAttempts, 'maxAttempts') === 0n) throw new ConfigError('maxAttempts must be positive');
    if (maxReplayedTransitions !== undefined)
        u64(maxReplayedTransitions, 'maxReplayedTransitions');
    const stop = runControl(limits);
    const checkStorage:Check[]=[];
    const copy = (f: Failure<I>): Failure<I> => ({ inputs: f.inputs.map(i => input(m, i)), violations: f.violations.map(v => ({ phase: v.phase, check: check(v.check.id, v.check.status, v.check.details) })) });
    const supplied = copy(original), target = supplied.violations[0];
    if (!target || target.check.status !== 'failed')
        throw new ModelError('cannot shrink a sequence without a target violation');
    const r: ShrinkReport<I, C> = { original: copy(supplied), minimized: copy(supplied), attempts: countLike(0n, maxAttempts), termination: 'search_complete', history: [], replayed_transitions: 0n, validated_original: false };
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
        type Captured = { ok: true; value: I } | { ok: false; error: unknown };
        const capture = (value: I): Captured => {
            try { return { ok: true, value: input(m, value) }; }
            catch (error) { return { ok: false, error }; }
        };
        const unwrap = (value: Captured): I => { if (!value.ok) throw value.error; return value.value; };
        const retained: Captured[] = [];
        let reason = stop();
        if (reason)
            return { kind: 'stopped', reason };
        // Freeze the replay prefix before initial-state/check callbacks can
        // change later candidate slots. Defer individual copy failures until
        // their transition is reached, and do not capture beyond the budget.
        const candidateLength = inputs.length;
        const remaining = maxReplayedTransitions === undefined ? BigInt(candidateLength)
            : maxReplayedTransitions > r.replayed_transitions
                ? maxReplayedTransitions - r.replayed_transitions : 0n;
        const captureCount = Number(remaining < BigInt(candidateLength) ? remaining : BigInt(candidateLength));
        const slots = inputs.slice(0, captureCount);
        const candidates = slots.map(capture);
        let s = state(m, call('shrink initial_state', () => m.initialState()));
        const initial = searchChecks(m,s,undefined,undefined,undefined,undefined,[],checkStorage);
        if (initial.violations.length)
            return { kind: 'failure', failure: { inputs: [], violations: initial.violations } };
        reason = stop();
        if (reason)
            return { kind: 'stopped', reason };
        for (let n = 0; n < candidateLength; n++) {
            reason = stop();
            if (reason)
                return { kind: 'stopped', reason };
            if (maxReplayedTransitions !== undefined && r.replayed_transitions >= maxReplayedTransitions)
                return { kind: 'stopped', reason: 'transition_limit' };
            // Preserve the witness before enabled/step callbacks can change an
            // external alias to the candidate. The reducer receives its own copy.
            const source = unwrap(candidates[n]);
            const current = input(m, source);
            const witnessInput = capture(current);
            const stepInput = capture(current);
            const observedInput = capture(current);
            retained.push(witnessInput);
            const valid = enabled(m, s, current);
            reason = stop();
            if (reason)
                return { kind: 'stopped', reason };
            if (!valid)
                return { kind: 'invalid' };
            const t = step(m, s, unwrap(stepInput));
            r.replayed_transitions = increment64(r.replayed_transitions, 'replayed transition count overflow');
            const observed = searchChecks(m,t.state,s,undefined,t,undefined,[],checkStorage,()=>unwrap(observedInput));
            if (observed.violations.length)
                return { kind: 'failure', failure: { inputs: retained.map(i => input(m, unwrap(i))), violations: observed.violations } };
            reason = stop();
            if (reason)
                return { kind: 'stopped', reason };
            s = t.state;
        }
        return { kind: 'pass' };
    };
    const hasTarget = (f: Failure<I>) => f.violations.some(v => v.phase === target.phase && v.check.id === target.check.id && v.check.status === 'failed');
    const validation = replay(supplied.inputs);
    r.attempts = countLike(1n, maxAttempts);
    if (validation.kind === 'stopped') {
        r.termination = validation.reason;
        r.history.push({ attempt: countLike(1n, maxAttempts), candidate_length: countLike(BigInt(supplied.inputs.length), maxAttempts), operation: { kind: 'validate_original' }, outcome: 'resource_limit' });
        return r;
    }
    if (validation.kind !== 'failure')
        throw new ModelError('original failure does not reproduce with valid causality');
    if (!hasTarget(validation.failure))
        throw new ModelError('original failure does not reproduce the target check and phase');
    r.minimized = copy(validation.failure);
    r.validated_original = true;
    r.history.push({ attempt: countLike(1n, maxAttempts), candidate_length: countLike(BigInt(supplied.inputs.length), maxAttempts), operation: { kind: 'validate_original' }, outcome: 'retained' });
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
    const attempt = (values: I[], operation: ShrinkOperation<C>): boolean => {
        if (r.attempts === maxAttempts) {
            r.termination = 'attempt_limit';
            return false;
        }
        r.attempts = countLike(BigInt(r.attempts) + 1n, maxAttempts);
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
                arrayGrowth(seen.length);
                seen.push(candidate.failure.inputs.map(i => input(m, i)));
                r.minimized = copy(candidate.failure);
                outcome = 'retained';
            }
        }
        arrayGrowth(r.history.length);
        r.history.push({ attempt: r.attempts, candidate_length: countLike(BigInt(values.length), maxAttempts), operation, outcome });
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
                if (!attempt(candidate, { kind: 'remove', start: countLike(BigInt(start), maxAttempts), end: countLike(BigInt(end), maxAttempts) }))
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
            let exhausted = false, failed = false;
            try {
                if (interrupted())
                    return r;
                let alternative = 0n;
                // Already-seen hints do not create replay history, but a lazy
                // simplifier can yield them forever. Bound consecutive skips per
                // iterator (reset after every replayed hint), matching Rust
                // shrink_ops, so finite multi-position runs are unaffected.
                let duplicateHints = 0n;
                for (;;) {
                    if (interrupted())
                        return r;
                    const item = call('simpler_inputs next', () => alternatives.next());
                    if (interrupted())
                        return r;
                    if (item.done) { exhausted = true; break; }
                    const alt = alternative;
                    alternative = increment64(alternative, 'simplification index overflow');
                    const candidate = r.minimized.inputs.map(i => input(m, i));
                    candidate[index] = input(m, item.value);
                    const wasSeen = known(candidate);
                    if (interrupted())
                        return r;
                    if (wasSeen) {
                        if (duplicateHints === BigInt(maxAttempts)) {
                            r.termination = 'attempt_limit';
                            return r;
                        }
                        duplicateHints++;
                        continue;
                    }
                    duplicateHints = 0n;
                    if (!attempt(candidate, { kind: 'simplify', index: countLike(BigInt(index), maxAttempts), alternative: countLike(alt, maxAttempts) }))
                        return r;
                    if (r.history.at(-1)!.outcome === 'retained') {
                        simplified = true;
                        break positions;
                    }
                }
            } catch (error) { failed = true; throw error; }
            finally { if (!exhausted) { if (failed) { try { alternatives.return?.(); } catch {} } else closeSimplifier(alternatives); } }
        }
        if (!simplified)
            return r;
    }
}

export type FuzzConfig64 = FuzzConfig<bigint>;
export type FuzzReport64<I> = FuzzReport<I, bigint>;

export type AutoOptions64 = AutoOptions<bigint>;
export type ShrinkConfig64 = ShrinkConfig<bigint>;
export type ShrinkReport64<I> = ShrinkReport<I, bigint>;
