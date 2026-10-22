/** Internal synchronous callback boundary. Never invoke an arbitrary thenable. */
import { ModelError } from './errors.js';

export function thrownText(error: unknown): string {
    try { return String(error instanceof Error ? error.message : error); }
    catch { return '<unprintable thrown value>'; }
}

export function synchronous<T>(value: T): T {
    if (value !== null && (typeof value === 'object' || typeof value === 'function')) {
        if (value instanceof Promise) {
            // Handle native rejections without trusting an overridden .then/.catch.
            Promise.prototype.then.call(value, undefined, () => {});
            throw new ModelError('guided callbacks must return synchronously');
        }
        if (typeof (value as { then?: unknown }).then === 'function')
            throw new ModelError('guided callbacks must return synchronously');
    }
    return value;
}

export function synchronousVoid(value: unknown): void {
    synchronous(value);
    if (value !== undefined) throw new ModelError('guided callback must return undefined synchronously');
}

export function guidedCall<T>(stage: string, callback: () => T): T {
    try { return synchronous(callback()); }
    catch (error) { throw new ModelError(`${stage}: ${thrownText(error)}`); }
}
