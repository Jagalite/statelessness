/** Synchronous append-only check leases shared by execution and every search path. */
import { type Check, type Model, type Transition, ModelError, check } from './core.js';
interface Lease {
    output: Check[] | null;
    parent?: Lease;
    active: boolean;
    failed: boolean;
    failure?: unknown;
    context?: string;
}
const leases = new WeakMap<CheckSink, Lease>();
function active(sink: CheckSink): Lease & {
    output: Check[];
} {
    const lease = leases.get(sink);
    // An escaped expired child cannot poison an unrelated still-active lease.
    if (!lease?.active)
        throw new ModelError('check sink lease expired');
    for (let p: Lease | undefined = lease; p; p = p.parent) {
        if (!p.active)
            throw new ModelError('check sink lease expired');
        if (p.failed)
            throw p.failure;
    }
    return lease as Lease & {
        output: Check[];
    };
}
function contextual(lease: Lease, error: unknown): ModelError {
    const message = error instanceof Error ? error.message : String(error);
    return new ModelError(lease.context ? `${lease.context}: ${message}` : message);
}
function poison(lease: Lease, error: unknown): void {
    lease.active = false;
    for (let p: Lease | undefined = lease; p; p = p.parent)
        if (!p.failed) {
            p.failed = true;
            p.failure = error;
        }
}
function operate<T>(sink: CheckSink, fn: (lease: Lease & {
    output: Check[];
}) => T): T {
    const lease = active(sink);
    try {
        return fn(lease);
    }
    catch (error) {
        const primary = lease.failed ? lease.failure : contextual(lease, error);
        poison(lease, primary);
        throw primary;
    }
}
/** Engine-issued per-callback lease. No mutable storage escapes; every operation expires on return/error. */
export class CheckSink {
    constructor() { throw new ModelError('check sink leases are issued by checker dispatch'); }
    get length(): number { return active(this).output.length; }
    push(value: Check): void {
        operate(this, lease => { const { id, status, details } = value; lease.output.push(Object.freeze(check(id, status, details))); });
    }
    extend(values: Iterable<Check>): void {
        operate(this, () => {
            for (const value of values)
                this.push(value);
        });
    }
    snapshot(): readonly Check[] { return operate(this, lease => Object.freeze(lease.output.map(c => Object.freeze({ ...c })))); }
    /** Validates a reservation request; JavaScript does not expose portable array capacity reservation. */
    reserve(additional: number): void {
        operate(this, () => {
            if (!Number.isSafeInteger(additional) || additional < 0)
                throw new ModelError('check sink reserve must be a nonnegative safe integer');
        });
    }
}
function invoke<T>(output: Check[], parent: Lease | undefined, callback: (sink: CheckSink) => T, context?: string): T {
    const sink = Object.create(CheckSink.prototype) as CheckSink, lease: Lease = { output, parent, active: true, failed: false, context };
    leases.set(sink, lease);
    try {
        const result = callback(sink);
        if (lease.failed)
            throw lease.failure;
        return result;
    }
    catch (error) {
        poison(lease, lease.failed ? lease.failure : contextual(lease, error));
        throw lease.failure;
    }
    finally {
        lease.active = false;
        // Expired capabilities must not retain reusable storage, parent leases or callback errors.
        lease.output = null;
        lease.parent = undefined;
        lease.failure = undefined;
        lease.failed = false;
        lease.context = undefined;
    }
}
/** Wrap caller storage without clearing it for one synchronous callback. Never reopens an expired lease. */
export function withCheckSink(output: Check[], callback: (sink: CheckSink) => void): void {
    invoke(output, undefined, sink => requireSynchronousCheckReturn(callback(sink)));
}
/** Internal wrapper forwarding: child expiry never revokes an outer valid lease. */
export function withChildCheckSink<T>(parent: CheckSink, callback: (sink: CheckSink) => T, context?: string): T {
    const lease = active(parent);
    return invoke(lease.output, lease, callback, context ?? lease.context);
}
/** Into callbacks are synchronous void hooks; TypeScript's void type alone permits async functions. */
export function requireSynchronousCheckReturn(value: unknown): void {
    if (value === undefined)
        return;
    // Do not leave an async callback rejection unhandled after its lease is revoked.
    if (value instanceof Promise)
        Promise.prototype.then.call(value, undefined, () => { });
    throw new ModelError('append check hook must return undefined synchronously');
}
export function appendStateChecks<S, I, O>(m: Model<S, I, O>, state: S, sink: CheckSink, context = 'state check'): void {
    withChildCheckSink(sink, child => {
        const snapshot = m.cloneState(state), hook = m.checkStateInto;
        if (hook !== undefined) {
            if (typeof hook !== 'function')
                throw new ModelError('append check hook must be a function');
            requireSynchronousCheckReturn(hook.call(m, snapshot, child));
        }
        else {
            const legacy = m.checkState;
            child.extend(legacy.call(m, snapshot));
        }
    }, context);
}
export function appendTransitionChecks<S, I, O>(m: Model<S, I, O>, before: S, input: I, t: Transition<S, O>, sink: CheckSink): void {
    withChildCheckSink(sink, child => {
        const hook = m.checkTransitionInto;
        if (hook !== undefined && typeof hook !== 'function')
            throw new ModelError('append check hook must be a function');
        const legacy = hook === undefined ? m.checkTransition : undefined;
        if (hook === undefined && legacy === undefined)
            return;
        const b = m.cloneState(before), i = m.cloneInput(input), after = { state: m.cloneState(t.state), outputs: t.outputs.map(o => m.cloneOutput(o)), disposition: { ...t.disposition } };
        if (hook !== undefined)
            requireSynchronousCheckReturn(hook.call(m, b, i, after, child));
        else
            child.extend(legacy!.call(m, b, i, after));
    }, 'transition check');
}
