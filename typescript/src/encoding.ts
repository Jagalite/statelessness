/** Reusable bounded encoding storage and callback-scoped capabilities. */
import { type CodecModel, ModelError, natural } from './core.js';
interface StorageState {
    bytes: Uint8Array;
    length: number;
}
const stores = new WeakMap<EncodeStorage, StorageState>();
export class EncodeStorage {
    constructor(capacity = 0) { natural(capacity, 'capacity'); stores.set(this, { bytes: new Uint8Array(capacity), length: 0 }); }
    get length(): number { return stores.get(this)!.length; }
    get capacity(): number { return stores.get(this)!.bytes.length; }
    snapshot(): Uint8Array { const s = stores.get(this)!; return new Uint8Array(s.bytes.subarray(0, s.length)); }
}
interface Root {
    state: StorageState;
    maximum: bigint;
    failed: boolean;
    error?: ModelError;
}
interface Lease {
    root: Root | null;
    parent?: Lease;
    active: boolean;
}
const leases = new WeakMap<EncodeBuffer, Lease>();
function active(buffer: EncodeBuffer): Lease & {
    root: Root;
} {
    const lease = leases.get(buffer);
    if (!lease?.active || !lease.root)
        throw new ModelError('encode buffer lease expired');
    for (let p: Lease | undefined = lease; p; p = p.parent)
        if (!p.active)
            throw new ModelError('encode buffer lease expired');
    if (lease.root.failed)
        throw lease.root.error;
    return lease as Lease & {
        root: Root;
    };
}
function failure(root: Root, error: unknown): ModelError {
    if (!root.failed) {
        root.failed = true;
        root.error = error instanceof ModelError ? error : new ModelError(error instanceof Error ? error.message : String(error));
    }
    return root.error!;
}
function operation<T>(buffer: EncodeBuffer, fn: (root: Root) => T): T { const l = active(buffer); try {
    return fn(l.root);
}
catch (e) {
    if (!l.root.failed)
        failure(l.root, e);
    throw e instanceof ModelError ? e : new ModelError(e instanceof Error ? e.message : String(e));
} }
function bytesView(value: Uint8Array): Uint8Array {
    if (!(value instanceof Uint8Array))
        throw new ModelError('codec must supply Uint8Array');
    const p = Object.getPrototypeOf(Uint8Array.prototype), get = (key: string) => Object.getOwnPropertyDescriptor(p, key)!.get!.call(value);
    return new Uint8Array(get('buffer'), get('byteOffset'), get('length'));
}
export function requireSynchronousCodecReturn(value: unknown): void {
    if (value === undefined)
        return;
    if (value instanceof Promise)
        Promise.prototype.then.call(value, undefined, () => { });
    throw new ModelError('codec hook must return undefined synchronously');
}
function invoke(root: Root, parent: Lease | undefined, callback: (buffer: EncodeBuffer) => void): void {
    const buffer = Object.create(EncodeBuffer.prototype) as EncodeBuffer, lease: Lease = { root, parent, active: true };
    leases.set(buffer, lease);
    try {
        requireSynchronousCodecReturn(callback(buffer));
        if (root.failed)
            throw root.error;
    }
    catch (e) {
        if (!root.failed)
            failure(root, e);
        throw e instanceof ModelError ? e : new ModelError(e instanceof Error ? e.message : String(e));
    }
    finally {
        lease.active = false;
        lease.root = null;
        lease.parent = undefined;
    }
}
/** Engine-issued synchronous lease; finish never overrides a previous rejected write. */
export class EncodeBuffer {
    constructor() { throw new ModelError('encode buffers are issued by withEncodeBuffer'); }
    get length(): number { return active(this).root.state.length; }
    extend(value: Uint8Array): void {
        operation(this, root => {
            const bytes = bytesView(value), state = root.state, required = state.length + bytes.length;
            if (!Number.isSafeInteger(required) || BigInt(required) > root.maximum)
                throw new ModelError('encoded blob exceeds byte limit');
            if (required > state.bytes.length) {
                const capacity = Math.min(Number(root.maximum > BigInt(Number.MAX_SAFE_INTEGER) ? BigInt(Number.MAX_SAFE_INTEGER) : root.maximum), Math.max(required, state.bytes.length * 2, 64));
                let next: Uint8Array;
                try {
                    next = new Uint8Array(capacity);
                }
                catch {
                    throw new ModelError('unable to allocate encoded blob');
                }
                next.set(state.bytes.subarray(0, state.length));
                state.bytes = next;
            }
            // Copy the entire accepted chunk; never retain mutable application views.
            state.bytes.set(bytes, state.length);
            state.length = required;
        });
    }
    extendFromSlice(value: Uint8Array): void { this.extend(value); }
    /** The legacy encoder's source allocation is outside this logical sink limit. */
    extendFromVec(value: Uint8Array): void { this.extend(value); }
    snapshot(): Uint8Array { return operation(this, root => new Uint8Array(root.state.bytes.subarray(0, root.state.length))); }
    finish(): void { active(this); }
    framed(callback: (buffer: EncodeBuffer) => void): void {
        operation(this, root => {
            const start = root.state.length;
            this.extend(new Uint8Array(8));
            invoke(root, active(this), callback);
            if (root.failed)
                throw root.error;
            const length = root.state.length - start - 8;
            new DataView(root.state.bytes.buffer, root.state.bytes.byteOffset, root.state.bytes.byteLength).setBigUint64(start, BigInt(length), true);
        });
    }
}
/** Clears logical destination while preserving capacity; a failed scope never publishes successful bytes. */
export function withEncodeBuffer(storage: EncodeStorage, maximum: number | bigint, callback: (buffer: EncodeBuffer) => void): void {
    const exact = typeof maximum === 'bigint' ? maximum : BigInt(natural(maximum, 'maximum'));
    if (exact < 0n || exact > ((1n << 64n) - 1n))
        throw new ModelError('maximum must be u64');
    const state = stores.get(storage);
    if (!state)
        throw new ModelError('invalid encode storage');
    state.length = 0;
    invoke({ state, maximum: exact, failed: false }, undefined, callback);
}
/** Internal forwarding creates a child lease over the same remaining root budget. */
export function withChildEncodeBuffer(buffer: EncodeBuffer, callback: (child: EncodeBuffer) => void): void { const l = active(buffer); invoke(l.root, l, callback); }
export type EncodingKind = 'state' | 'input' | 'output';
export function appendModelEncoding<S, I, O>(m: CodecModel<S, I, O>, kind: EncodingKind, value: S | I | O, buffer: EncodeBuffer): void {
    withChildEncodeBuffer(buffer, child => {
        const names = { state: ['encodeStateInto', 'encodeState', 'cloneState'], input: ['encodeInputInto', 'encodeInput', 'cloneInput'], output: ['encodeOutputInto', 'encodeOutput', 'cloneOutput'] } as const;
        const [into, legacy, clone] = names[kind], model = m as any, hook = model[into];
        const snapshot = model[clone].call(m, value);
        if (hook !== undefined) {
            if (typeof hook !== 'function')
                throw new ModelError('codec hook must be a function');
            requireSynchronousCodecReturn(hook.call(m, snapshot, child));
        }
        else {
            const encode = model[legacy];
            child.extendFromVec(encode.call(m, snapshot));
        }
    });
}
export function encodeModel<S, I, O>(m: CodecModel<S, I, O>, kind: EncodingKind, value: S | I | O, storage: EncodeStorage, maximum: number | bigint = Number.MAX_SAFE_INTEGER): Uint8Array {
    withEncodeBuffer(storage, maximum, buffer => appendModelEncoding(m, kind, value, buffer));
    return storage.snapshot();
}
