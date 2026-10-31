/** Sealed checking evidence. Clone hooks must preserve values and isolate mutable payloads. */
import { type Model, type Transition, type Check, type CheckPolicy, check, disposition, natural, checkObservedCountIntoExact } from './core.js';
import { ConfigError, ModelError } from './errors.js';
import { withCheckSink, appendStateChecks } from './checks.js';
export interface ExactCheckPolicy {
    readonly stateEvery: bigint;
    readonly transitionChecks: boolean;
}
export const FULL_CHECKS_EXACT: ExactCheckPolicy = Object.freeze({ stateEvery: 1n, transitionChecks: true });
const U64 = (1n << 64n) - 1n;
function sequenceValue(value: bigint, name: string, minimum = 1n): bigint { if (typeof value !== 'bigint' || value < minimum || value > U64)
    throw new ConfigError(`${name} must be a bigint in ${minimum}..${U64}`); return value; }
function stage<T>(name: string, fn: () => T): T { try {
    return fn();
}
catch (e) {
    throw new ModelError(`${name}: ${e instanceof Error ? e.message : String(e)}`);
} }
function copyChecks(values: readonly Check[]): readonly Check[] { return Object.freeze(values.map(c => Object.freeze(check(c.id, c.status, c.details)))); }
function copyTransition<S, I, O>(m: Model<S, I, O>, t: Transition<S, O>): Transition<S, O> { const { state, outputs, disposition: d } = t; return Object.freeze({ state: m.cloneState(state), outputs: Object.freeze(Array.from(outputs, o => m.cloneOutput(o))), disposition: Object.freeze(disposition(d.kind, d.reason)) }); }
export interface CheckedInitialContext<S, I, O> {
    readonly model: Model<S, I, O>;
    readonly state: S;
    readonly checks: readonly Check[];
}
export interface CheckedTurnContext<S, I, O> {
    readonly model: Model<S, I, O>;
    readonly before: S;
    readonly input: I;
    readonly transition: Transition<S, O>;
    readonly sequence: bigint;
    readonly checks: readonly Check[];
    readonly stateCheckCount: number;
    readonly fullChecking: boolean;
}
const initials = new WeakMap<object, CheckedInitialContext<unknown, unknown, unknown>>();
const turns = new WeakMap<object, CheckedTurnContext<unknown, unknown, unknown>>();
function initialContext<S, I, O>(token: CheckedInitial<S, I, O>): CheckedInitialContext<S, I, O> { const c = initials.get(token); if (!c)
    throw new ModelError('invalid checked initial token'); return c as CheckedInitialContext<S, I, O>; }
function turnContext<S, I, O>(token: CheckedTurn<S, I, O>): CheckedTurnContext<S, I, O> { const c = turns.get(token); if (!c)
    throw new ModelError('invalid checked turn token'); return c as CheckedTurnContext<S, I, O>; }
/** Not publicly constructible; runtime authority lives in a private WeakMap. */
export class CheckedInitial<S, I, O> {
    declare private readonly checkedInitialBrand: void;
    private constructor() { throw new ModelError('checked initial tokens are issued by checkInitial'); }
    get model(): Model<S, I, O> { return initialContext(this).model; }
    get state(): S { const c = initialContext(this); return stage('snapshot', () => c.model.cloneState(c.state)); }
    get checks(): readonly Check[] { return copyChecks(initialContext(this).checks); }
    get stateCheckCount(): number { return initialContext(this).checks.length; }
    intoChecks(): readonly Check[] { return this.checks; }
}
/** Diagnostic views never carry token authority. Accessors detach mutable context. */
export interface TurnObservation<S, I, O> {
    readonly sequence: bigint;
    readonly before: S;
    readonly input: I;
    readonly transition: Transition<S, O>;
    readonly checks: readonly Check[];
    readonly stateCheckCount: number;
}
export class CheckedTurn<S, I, O> {
    declare private readonly checkedTurnBrand: void;
    private constructor() { throw new ModelError('checked turn tokens are issued by checkTurn'); }
    get model(): Model<S, I, O> { return turnContext(this).model; }
    get before(): S { const c = turnContext(this); return stage('snapshot', () => c.model.cloneState(c.before)); }
    get input(): I { const c = turnContext(this); return stage('snapshot', () => c.model.cloneInput(c.input)); }
    get transition(): Transition<S, O> { const c = turnContext(this); return stage('snapshot', () => copyTransition(c.model, c.transition)); }
    get sequence(): bigint { return turnContext(this).sequence; }
    get checks(): readonly Check[] { return copyChecks(turnContext(this).checks); }
    get stateCheckCount(): number { return turnContext(this).stateCheckCount; }
    get fullChecking(): boolean { return turnContext(this).fullChecking; }
    intoChecks(): readonly Check[] { return this.checks; }
    observation(): TurnObservation<S, I, O> { const c = requireCheckedTurn(this); return Object.freeze({ sequence: c.sequence, before: c.before, input: c.input, transition: c.transition, checks: c.checks, stateCheckCount: c.stateCheckCount }); }
}
/** Internal consumer bridge; not exported from the package entrypoint. Always authenticates and detaches. */
export function requireCheckedInitial<S, I, O>(token: CheckedInitial<S, I, O>): CheckedInitialContext<S, I, O> { const c = initialContext(token); return { model: c.model, state: stage('snapshot', () => c.model.cloneState(c.state)), checks: copyChecks(c.checks) }; }
/** Recorder borrows the immutable batch until quota admission, avoiding an unbounded early copy. */
export function requireCheckedInitialForRecorder<S, I, O>(token: CheckedInitial<S, I, O>): CheckedInitialContext<S, I, O> {
    const c = initialContext(token);
    return {model: c.model, state: stage('snapshot', () => c.model.cloneState(c.state)), checks: c.checks};
}
export function requireCheckedTurnHeader<S, I, O>(token: CheckedTurn<S, I, O>): {
    sequence: bigint;
    fullChecking: boolean;
    stateCheckCount: number;
} { const c = turnContext(token); return { sequence: c.sequence, fullChecking: c.fullChecking, stateCheckCount: c.stateCheckCount }; }
export function requireCheckedTurn<S, I, O>(token: CheckedTurn<S, I, O>): CheckedTurnContext<S, I, O> { const c = turnContext(token); return { ...c, before: stage('snapshot', () => c.model.cloneState(c.before)), input: stage('snapshot', () => c.model.cloneInput(c.input)), transition: stage('snapshot', () => copyTransition(c.model, c.transition)), checks: copyChecks(c.checks) }; }
export function requireCheckedTurnForRecorder<S, I, O>(token: CheckedTurn<S, I, O>): CheckedTurnContext<S, I, O> {
    const c = turnContext(token);
    return {...c, before: stage('snapshot', () => c.model.cloneState(c.before)), input: stage('snapshot', () => c.model.cloneInput(c.input)), transition: stage('snapshot', () => copyTransition(c.model, c.transition)), checks: c.checks};
}
export function checkInitial<S, I, O>(model: Model<S, I, O>, state: S): CheckedInitial<S, I, O> {
    const owned = stage('snapshot', () => model.cloneState(state)), checks: Check[] = [];
    withCheckSink(checks, sink => appendStateChecks(model, owned, sink, 'initial check'));
    const token = Object.create(CheckedInitial.prototype) as CheckedInitial<S, I, O>;
    initials.set(token, { model, state: stage('snapshot', () => model.cloneState(owned)), checks: copyChecks(checks) } as CheckedInitialContext<unknown, unknown, unknown>);
    return Object.freeze(token) as CheckedInitial<S, I, O>;
}
export function checkTurnExact<S, I, O>(model: Model<S, I, O>, before: S, input: I, transition: Transition<S, O>, sequence: bigint, policy: ExactCheckPolicy = FULL_CHECKS_EXACT): CheckedTurn<S, I, O> {
    const { stateEvery, transitionChecks } = policy;
    if (sequence === 0n)
        throw new ConfigError('transition sequence must be positive');
    sequenceValue(sequence, 'sequence');
    sequenceValue(stateEvery, 'stateEvery');
    if (typeof transitionChecks !== 'boolean')
        throw new ConfigError('transitionChecks must be boolean');
    const owned = stage('snapshot', () => ({ before: model.cloneState(before), input: model.cloneInput(input), transition: copyTransition(model, transition) }));
    const checks: Check[] = [];
    const stateCheckCount = checkObservedCountIntoExact(model, owned.before, owned.input, owned.transition, sequence, checks, {stateEvery, transitionChecks});
    const token = Object.create(CheckedTurn.prototype) as CheckedTurn<S, I, O>;
    const sealed = stage('snapshot', () => ({ before: model.cloneState(owned.before), input: model.cloneInput(owned.input), transition: copyTransition(model, owned.transition) }));
    turns.set(token, { model, ...sealed, sequence, checks: copyChecks(checks), stateCheckCount, fullChecking: stateEvery === 1n && transitionChecks } as CheckedTurnContext<unknown, unknown, unknown>);
    return Object.freeze(token) as CheckedTurn<S, I, O>;
}
export function checkTurn<S, I, O>(model: Model<S, I, O>, before: S, input: I, transition: Transition<S, O>, sequence: number, policy: CheckPolicy = { stateEvery: 1, transitionChecks: true }): CheckedTurn<S, I, O> { const { stateEvery, transitionChecks } = policy; natural(sequence, 'sequence', 1); natural(stateEvery, 'stateEvery', 1); return checkTurnExact(model, before, input, transition, BigInt(sequence), { stateEvery: BigInt(stateEvery), transitionChecks }); }
