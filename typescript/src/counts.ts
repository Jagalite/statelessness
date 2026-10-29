/** Exact Rust64 counts. Numbers remain a checked convenience representation. */
import { ConfigError, ModelError } from './errors.js';
export type Count = number | bigint;
export type CountValue<C extends Count> = C extends bigint ? bigint : number;
export const U64_MAX = (1n << 64n) - 1n;
export function count64(value: Count, name: string): bigint {
    if (typeof value === 'number' && (!Number.isSafeInteger(value) || value < 0)) throw new ConfigError(`${name} must be a nonnegative safe integer or u64 bigint`);
    if (typeof value !== 'number' && typeof value !== 'bigint') throw new ConfigError(`${name} must be a nonnegative safe integer or u64 bigint`);
    const wide = BigInt(value);
    if (wide < 0n || wide > U64_MAX) throw new ConfigError(`${name} must be u64`);
    return wide;
}
export function countLike<C extends Count>(value: bigint, representation: C): CountValue<C> {
    if (value < 0n || value > U64_MAX) throw new ModelError('count arithmetic exceeds u64');
    if (typeof representation === 'bigint') return value as CountValue<C>;
    if (value > BigInt(Number.MAX_SAFE_INTEGER)) throw new ModelError('count exceeds number precision; use bigint configuration');
    return Number(value) as CountValue<C>;
}
/** Check at the actual growth boundary; budgets themselves never allocate. */
export function arrayGrowth(length: number, additional = 1): void {
    if (!Number.isSafeInteger(length) || !Number.isSafeInteger(additional) || length < 0 || additional < 0 || length + additional > 0xffffffff) throw new ModelError('native array capacity exceeded');
}

export function increment64(value: bigint, message: string): bigint {
    if (value >= U64_MAX) throw new ModelError(message);
    return value + 1n;
}
