/** Explicit canonical owned values. Layout weights describe logical Rust64 requests, not JS heap. */
import { ModelError } from './errors.js';
import { EncodeStorage, withEncodeBuffer, withChildEncodeBuffer, withRawChildEncodeBuffer, preflightEncodeAppend, type EncodeBuffer } from './encoding.js';
const U64 = (1n << 64n) - 1n;
function error(message: string): never { throw new ModelError(message); }
function exact(value: number | bigint, name: string): bigint {
    if (typeof value === 'bigint') {
        if (value < 0n || value > U64)
            return error(`${name} must be u64`);
        return value;
    }
    if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0)
        return error(`${name} must be exact u64`);
    return BigInt(value);
}
function modelError(cause: unknown): ModelError {
    try {
        const message = cause instanceof Error ? cause.message : String(cause);
        return new ModelError(typeof message === 'string' ? message : String(message));
    }
    catch {
        return new ModelError('value codec callback failed');
    }
}
function noPromise<T>(value: T): T {
    if (value instanceof Promise) {
        Promise.prototype.then.call(value, undefined, () => { });
        return error('value codec callback must be synchronous');
    }
    return value;
}
function synchronousVoid(value: unknown): void {
    noPromise(value);
    if (value !== undefined)
        error('value encoder must return undefined synchronously');
}
function byteView(value: Uint8Array): Uint8Array {
    if (!(value instanceof Uint8Array))
        return error('expected Uint8Array');
    const proto = Object.getPrototypeOf(Uint8Array.prototype), get = (key: string) => Object.getOwnPropertyDescriptor(proto, key)!.get!.call(value);
    return new Uint8Array(get('buffer'), get('byteOffset'), get('length'));
}
export interface DecodeLimits {
    readonly bytes: number | bigint;
    readonly allocation: number | bigint;
    readonly elements: number | bigint;
    readonly depth: number | bigint;
    readonly work: number | bigint;
}
export interface ResolvedDecodeLimits {
    readonly bytes: bigint;
    readonly allocation: bigint;
    readonly elements: bigint;
    readonly depth: bigint;
    readonly work: bigint;
}
export const DEFAULT_DECODE_LIMITS: ResolvedDecodeLimits = Object.freeze({ bytes: 16777216n, allocation: 33554432n, elements: 1000000n, depth: 64n, work: 2000000n });
export function resolveDecodeLimits(source: Partial<DecodeLimits> = {}): ResolvedDecodeLimits {
    const { bytes = 16777216n, allocation = 33554432n, elements = 1000000n, depth = 64n, work = 2000000n } = source;
    return Object.freeze({ bytes: exact(bytes, 'bytes'), allocation: exact(allocation, 'allocation'), elements: exact(elements, 'elements'), depth: exact(depth, 'depth'), work: exact(work, 'work') });
}
interface ReaderState {
    bytes: Uint8Array;
    offset: number;
    limits: {
        bytes: bigint;
        allocation: bigint;
        elements: bigint;
        depth: bigint;
        work: bigint;
    };
    depth: bigint;
    failure: string | null;
}
const readers = new WeakMap<Decoder, ReaderState>();
function state(reader: Decoder): ReaderState {
    const s = readers.get(reader);
    if (!s)
        return error('invalid decoder');
    return s;
}
function prior(s: ReaderState): void {
    if (s.failure !== null)
        throw new ModelError(s.failure);
}
function reject(s: ReaderState, cause: unknown): never {
    if (s.failure === null) {
        s.failure = 'value codec callback failed';
        s.failure = modelError(cause).message;
    }
    throw new ModelError(s.failure);
}
export class Decoder {
    constructor(input: Uint8Array, limits: Partial<DecodeLimits> = {}) {
        const resolved = resolveDecodeLimits(limits), view = byteView(input);
        if (BigInt(view.length) > resolved.bytes)
            error('decode byte limit');
        let owned: Uint8Array;
        try {
            owned = new Uint8Array(view);
        }
        catch {
            return error('decode allocation failed');
        }
        readers.set(this, { bytes: owned, offset: 0, limits: { ...resolved }, depth: 0n, failure: null });
    }
    get remaining(): bigint { const s = state(this); return BigInt(s.bytes.length - s.offset); }
    get limits(): ResolvedDecodeLimits { return Object.freeze({ ...state(this).limits }); }
    get depth(): bigint { return state(this).depth; }
    /** Returns owned bytes; never exposes the unread buffer through a shared ArrayBuffer. */
    take(count: number | bigint): Uint8Array {
        const s = state(this);
        prior(s);
        let n: bigint;
        try {
            n = exact(count, 'take count');
        }
        catch (e) {
            return reject(s, e);
        }
        if (n > BigInt(s.bytes.length - s.offset))
            return reject(s, new ModelError('truncated value'));
        let out: Uint8Array;
        try {
            out = new Uint8Array(s.bytes.subarray(s.offset, s.offset + Number(n)));
        }
        catch {
            return reject(s, new ModelError('decode allocation failed'));
        }
        s.offset += Number(n);
        return out;
    }
    value<T>(schema: ValueSchema<T>): T {
        const s = state(this);
        prior(s);
        if (s.limits.work === 0n)
            return reject(s, new ModelError('decode work limit'));
        s.limits.work--;
        if (s.depth >= s.limits.depth)
            return reject(s, new ModelError('decode nesting limit'));
        s.depth++;
        try {
            const decode = schema.decodeFrom;
            if (typeof decode !== 'function')
                return error('invalid value decoder');
            const result = noPromise(decode.call(schema, this));
            prior(s);
            return result;
        }
        catch (e) {
            return reject(s, e);
        }
        finally {
            s.depth--;
        }
    }
    collection(count: number | bigint, elementSize: number | bigint): void {
        const s = state(this);
        prior(s);
        let n: bigint, size: bigint;
        try {
            n = exact(count, 'collection count');
            size = exact(elementSize, 'element size');
        }
        catch (e) {
            return reject(s, e);
        }
        if (n > s.limits.elements)
            return reject(s, new ModelError('decode element limit'));
        s.limits.elements -= n;
        const allocation = n * size;
        if (allocation > U64)
            return reject(s, new ModelError('decode allocation overflow'));
        if (allocation > s.limits.allocation)
            return reject(s, new ModelError('decode allocation limit'));
        s.limits.allocation -= allocation;
        if (n > s.limits.work)
            return reject(s, new ModelError('decode work limit'));
    }
    /** Trailing bytes are a terminal check error, not a new sticky structural failure. */
    finish(): void {
        const s = state(this);
        prior(s);
        if (s.offset !== s.bytes.length)
            error('trailing value bytes');
    }
}
export interface LogicalLayout {
    readonly bytes: bigint;
    readonly convention: 'logical-rust64';
    readonly name: string;
}
export function logicalLayout(bytes: number | bigint, name: string): LogicalLayout {
    if (typeof name !== 'string' || name.length === 0)
        return error('layout name must be nonempty');
    strictTextLength(name);
    return Object.freeze({ bytes: exact(bytes, 'layout bytes'), convention: 'logical-rust64', name });
}
function layoutCopy(layout: LogicalLayout | null | undefined): LogicalLayout | null {
    if (layout == null)
        return null;
    const { bytes, convention, name } = layout;
    if (convention !== 'logical-rust64')
        return error('unsupported allocation layout convention');
    return logicalLayout(bytes, name);
}
export interface ValueSchema<T> {
    readonly layout: LogicalLayout | null;
    encodeInto(value: T, out: EncodeBuffer): void;
    encodeRaw(value: T, out: EncodeBuffer): void;
    /** Concrete raw decode: only enclosing Decoder.value guards record returned errors. */
    decodeFrom(reader: Decoder): T;
    schema(): string;
    variant(value: T): string | null;
}
export interface LengthSchema<T> extends ValueSchema<T> {
    encodeLength(value: T, width: number, out: EncodeBuffer): void;
    decodeLength(width: number, reader: Decoder): T;
    encodeLengthRaw(value: T, width: number, out: EncodeBuffer): void;
}
export interface CustomValueSchema<T> {
    readonly name: string;
    readonly layout?: LogicalLayout | null;
    encodeInto?(value: T, out: EncodeBuffer): void;
    decodeFrom?(reader: Decoder): T;
    encodeLength?(value: T, width: number, out: EncodeBuffer): void;
    decodeLength?(width: number, reader: Decoder): T;
    variant?(value: T): string | null;
}
const shapes = new WeakMap<object, string>();
type Directions = {
    encode: boolean;
    decode: boolean;
};
const directions = new WeakMap<object, Directions>();
const lengthDirections = new WeakMap<object, Directions>();
function direction(schema: ValueSchema<unknown>): Directions { return directions.get(schema) ?? { encode: typeof schema.encodeRaw === 'function', decode: typeof schema.decodeFrom === 'function' }; }
function combined(schemas: readonly ValueSchema<any>[]): Directions { return { encode: schemas.every(s => direction(s).encode), decode: schemas.every(s => direction(s).decode) }; }
const rawScopes = new WeakSet<EncodeBuffer>();
function emit<T>(schema: ValueSchema<T>, value: T, out: EncodeBuffer): void {
    if (rawScopes.has(out))
        schema.encodeRaw(value, out);
    else
        schema.encodeInto(value, out);
}
function nativeLengthWidth(width: number): void {
    if (typeof width !== 'number' || !Number.isInteger(width) || width < 0 || width > 255)
        error('length width must be a u8');
}
function makeSchema<T>(spec: CustomValueSchema<T>, shape?: string, length?: {
    encode?: (v: T, width: number, out: EncodeBuffer) => void;
    decode?: (width: number, r: Decoder) => T;
}, capabilities?: Directions): ValueSchema<T> {
    const { name, layout } = spec, encode = spec.encodeInto, decode = spec.decodeFrom, variant = spec.variant;
    strictTextLength(name);
    if ((encode !== undefined && typeof encode !== 'function') || (decode !== undefined && typeof decode !== 'function') || (!encode && !decode && !length?.encode && !length?.decode) || (variant !== undefined && typeof variant !== 'function'))
        return error('invalid value schema callbacks');
    const available = capabilities ?? { encode: !!encode, decode: !!decode };
    const requireEncode = () => {
        if (!available.encode)
            error('value schema has no encoder');
    };
    const requireDecode = () => {
        if (!available.decode)
            error('value schema has no decoder');
    };
    const result: ValueSchema<T> = { layout: layoutCopy(layout), schema: () => name, variant(value) {
            if (!variant)
                return null;
            const label = noPromise(variant.call(spec, value));
            if (label !== null)
                strictTextLength(label);
            return label;
        },
        encodeInto(value, out) { requireEncode(); withChildEncodeBuffer(out, child => synchronousVoid(encode!.call(spec, value, child))); },
        encodeRaw(value, out) {
            requireEncode();
            withRawChildEncodeBuffer(out, child => {
                rawScopes.add(child);
                try {
                    synchronousVoid(encode!.call(spec, value, child));
                }
                finally {
                    rawScopes.delete(child);
                }
            });
        },
        decodeFrom(reader) { requireDecode(); return noPromise(decode!.call(spec, reader)); }
    };
    if (length) {
        if ((length.encode !== undefined && typeof length.encode !== 'function') || (length.decode !== undefined && typeof length.decode !== 'function'))
            error('invalid length schema callbacks');
        const availableLength = capabilities ?? { encode: !!length.encode, decode: !!length.decode };
        lengthDirections.set(result, availableLength);
        const requireLengthEncode = () => {
            if (!availableLength.encode)
                error('value schema has no length encoder');
        };
        const requireLengthDecode = () => {
            if (!availableLength.decode)
                error('value schema has no length decoder');
        };
        Object.assign(result, { encodeLength(value: T, width: number, out: EncodeBuffer) { nativeLengthWidth(width); requireLengthEncode(); withChildEncodeBuffer(out, child => synchronousVoid(length.encode!(value, width, child))); }, decodeLength(width: number, reader: Decoder) { nativeLengthWidth(width); requireLengthDecode(); return noPromise(length.decode!(width, reader)); }, encodeLengthRaw(value: T, width: number, out: EncodeBuffer) {
                nativeLengthWidth(width);
                requireLengthEncode();
                withRawChildEncodeBuffer(out, child => {
                    rawScopes.add(child);
                    try {
                        synchronousVoid(length.encode!(value, width, child));
                    }
                    finally {
                        rawScopes.delete(child);
                    }
                });
            } });
    }
    directions.set(result, available);
    if (shape)
        shapes.set(result, shape);
    return Object.freeze(result);
}
export function customValueSchema<T>(spec: CustomValueSchema<T>): LengthSchema<T> {
    const encode = spec.encodeLength, decode = spec.decodeLength;
    if ((encode !== undefined && typeof encode !== 'function') || (decode !== undefined && typeof decode !== 'function'))
        error('invalid length schema callbacks');
    return makeSchema(spec, undefined, { encode: encode === undefined ? undefined : (v, width, out) => encode.call(spec, v, width, out), decode: decode === undefined ? undefined : (width, reader) => decode.call(spec, width, reader) }) as LengthSchema<T>;
}
export function encodeValue<T>(schema: ValueSchema<T>, value: T, maximum: number | bigint): Uint8Array {
    const storage = new EncodeStorage();
    withEncodeBuffer(storage, maximum, out => {
        const encode = schema.encodeRaw;
        if (typeof encode !== 'function')
            return error('invalid value encoder');
        synchronousVoid(encode.call(schema, value, out));
    });
    return storage.snapshot();
}
export function decodeValue<T>(schema: ValueSchema<T>, bytes: Uint8Array, limits: Partial<DecodeLimits> = {}): T { const reader = new Decoder(bytes, limits), result = reader.value(schema); reader.finish(); return result; }
export const UNIT: unique symbol = Symbol('canonical unit');
export type Unit = typeof UNIT;
export type Option<T> = {
    readonly some: false;
} | {
    readonly some: true;
    readonly value: T;
};
export const NONE: Option<never> = Object.freeze({ some: false });
export function some<T>(value: T): Option<T> { return Object.freeze({ some: true, value }); }
function strictTextLength(value: string): number {
    if (typeof value !== 'string')
        return error('expected string');
    let count = 0;
    for (let i = 0; i < value.length; i++) {
        const c = value.charCodeAt(i);
        if (c >= 0xd800 && c <= 0xdbff) {
            const next = value.charCodeAt(++i);
            if (!(next >= 0xdc00 && next <= 0xdfff))
                return error('invalid Unicode scalar');
            count += 4;
        }
        else if (c >= 0xdc00 && c <= 0xdfff)
            return error('invalid Unicode scalar');
        else
            count += c < 128 ? 1 : c < 2048 ? 2 : 3;
        if (count > 0xffffffff)
            return error('length overflow');
    }
    return count;
}
function numberInteger(value: number, bits: number, signed: boolean): bigint {
    const min = signed ? -(2 ** (bits - 1)) : 0, max = signed ? 2 ** (bits - 1) - 1 : 2 ** bits - 1;
    if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < min || value > max)
        return error('integer out of range');
    return BigInt(value);
}
function bigInteger(value: bigint, bits: number, signed: boolean): bigint {
    const b = BigInt(bits), min = signed ? -(1n << (b - 1n)) : 0n, max = signed ? (1n << (b - 1n)) - 1n : (1n << b) - 1n;
    if (typeof value !== 'bigint' || value < min || value > max)
        return error('integer out of range');
    return value;
}
function integer<T extends number | bigint>(bits: number, signed: boolean): ValueSchema<T> {
    const name = `${signed ? 'i' : 'u'}${bits}`, width = bits / 8;
    return makeSchema<T>({ name, layout: logicalLayout(width, name), encodeInto(value, out) {
            let n = bits <= 32 ? numberInteger(value as number, bits, signed) : bigInteger(value as bigint, bits, signed);
            if (n < 0n)
                n += 1n << BigInt(bits);
            const bytes = new Uint8Array(width);
            for (let i = 0; i < width; i++) {
                bytes[i] = Number(n & 255n);
                n >>= 8n;
            }
            out.extend(bytes);
        }, decodeFrom(reader) {
            const bytes = reader.take(width);
            let n = 0n;
            for (let i = width - 1; i >= 0; i--)
                n = (n << 8n) | BigInt(bytes[i]);
            if (signed && (n & (1n << BigInt(bits - 1))) !== 0n)
                n -= 1n << BigInt(bits);
            return (bits <= 32 ? Number(n) : n) as T;
        } }, name);
}
const u8 = integer<number>(8, false), u16 = integer<number>(16, false), u32 = integer<number>(32, false), u64 = integer<bigint>(64, false), u128 = integer<bigint>(128, false), i8 = integer<number>(8, true), i16 = integer<number>(16, true), i32 = integer<number>(32, true), i64 = integer<bigint>(64, true), i128 = integer<bigint>(128, true);
const bool = makeSchema<boolean>({ name: 'bool', layout: logicalLayout(1, 'bool'), encodeInto(v, out) {
        if (typeof v !== 'boolean')
            return error('expected boolean');
        emit(u8, v ? 1 : 0, out);
    }, decodeFrom(r) {
        const v = r.value(u8);
        if (v === 0)
            return false;
        if (v === 1)
            return true;
        return error('invalid boolean');
    } }, 'bool');
const unit = makeSchema<Unit>({ name: '()', layout: logicalLayout(0, '()'), encodeInto(v) {
        if (v !== UNIT)
            error('expected UNIT');
    }, decodeFrom() { return UNIT; } }, 'unit');
function widthSchema(width: number): ValueSchema<number> {
    switch (width) {
        case 1: return u8;
        case 2: return u16;
        case 4: return u32;
        default: return error('unsupported length width');
    }
}
export function encodeLengthValue(length: number | bigint, width: number, out: EncodeBuffer): void {
    const schema = widthSchema(width), n = exact(length, 'length');
    if (n > ((1n << BigInt(width * 8)) - 1n))
        return error('length overflow');
    emit(schema, Number(n), out);
}
function readLength(width: number, r: Decoder): number { return r.value(widthSchema(width)); }
function encodeString(value: string, width: number, out: EncodeBuffer): void {
    const n = strictTextLength(value);
    encodeLengthValue(n, width, out);
    preflightEncodeAppend(out, BigInt(n));
    // Fixed scratch avoids an allocating UTF8 encoding proportional to an unadmitted string.
    const scratch = new Uint8Array(4096);
    let used = 0;
    const push = (byte: number) => {
        if (used === scratch.length) {
            out.extend(scratch);
            used = 0;
        }
        scratch[used++] = byte;
    };
    for (const c of value) {
        const cp = c.codePointAt(0)!;
        if (cp < 128)
            push(cp);
        else if (cp < 2048) {
            push(0xc0 | (cp >> 6));
            push(0x80 | (cp & 63));
        }
        else if (cp < 65536) {
            push(0xe0 | (cp >> 12));
            push(0x80 | ((cp >> 6) & 63));
            push(0x80 | (cp & 63));
        }
        else {
            push(0xf0 | (cp >> 18));
            push(0x80 | ((cp >> 12) & 63));
            push(0x80 | ((cp >> 6) & 63));
            push(0x80 | (cp & 63));
        }
    }
    if (used)
        out.extend(scratch.subarray(0, used));
}
function decodeString(width: number, r: Decoder): string {
    const n = readLength(width, r);
    r.collection(n, 1);
    state(r).limits.work -= BigInt(n);
    const bytes = r.take(n);
    try {
        return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
    }
    catch {
        return error('invalid UTF-8');
    }
}
const string = makeSchema<string>({ name: 'alloc::string::String', layout: logicalLayout(24, 'String'), encodeInto(v, out) { encodeString(v, 4, out); }, decodeFrom(r) { return decodeString(4, r); } }, 'string', { encode: encodeString, decode: decodeString }) as LengthSchema<string>;
function arrayCount(value: readonly unknown[], expected?: number): number {
    if (!Array.isArray(value))
        return error('expected array');
    const n = value.length;
    if (!Number.isSafeInteger(n) || n < 0 || n > 0xffffffff)
        return error('length overflow');
    if (expected !== undefined && n !== expected)
        return error('array length');
    return n;
}
function option<T>(element: ValueSchema<T>, layout?: LogicalLayout): ValueSchema<Option<T>> {
    const tag = shapes.get(element), known: Record<string, number> = { unit: 1, bool: 1, u8: 2, u64: 16, string: 24 };
    const selected = layout ?? (tag !== undefined && Object.prototype.hasOwnProperty.call(known, tag) ? logicalLayout(known[tag], `Option<${tag}>`) : undefined);
    return makeSchema<Option<T>>({ name: `Option<${element.schema()}>`, layout: selected, encodeInto(v, out) {
            if (v === null || typeof v !== 'object')
                return error('expected tagged option');
            const present = v.some;
            if (typeof present !== 'boolean')
                return error('expected tagged option');
            emit(u8, present ? 1 : 0, out);
            if (present)
                emit(element, v.value, out);
        }, decodeFrom(r) {
            const tag = r.value(u8);
            if (tag === 0)
                return NONE;
            if (tag === 1)
                return some(r.value(element));
            return error('invalid option tag');
        } }, undefined, undefined, combined([element]));
}
function vector<T>(element: ValueSchema<T>): LengthSchema<T[]> {
    const weight = layoutCopy(element.layout);
    const encode = (value: T[], width: number, out: EncodeBuffer) => {
        const n = arrayCount(value);
        encodeLengthValue(n, width, out);
        for (let i = 0; i < n; i++)
            emit(element, value[i], out);
    };
    const decode = (width: number, r: Decoder) => {
        const n = readLength(width, r);
        r.collection(n, (weight ?? error('collection element requires explicit allocation layout')).bytes);
        const out: T[] = [];
        try {
            for (let i = 0; i < n; i++)
                out.push(r.value(element));
        }
        catch (e) {
            throw e;
        }
        return out;
    };
    return makeSchema<T[]>({ name: `Vec<${element.schema()}>`, layout: logicalLayout(24, 'Vec header'), encodeInto(v, out) { encode(v, 4, out); }, decodeFrom(r) { return decode(4, r); } }, 'vector', { encode, decode }, combined([element])) as LengthSchema<T[]>;
}
function array<T>(element: ValueSchema<T>, length: number, layout?: LogicalLayout): ValueSchema<T[]> {
    if (typeof length !== 'number' || !Number.isSafeInteger(length) || length < 0 || length > 0xffffffff)
        return error('array length');
    const weight = layoutCopy(element.layout);
    const selected = layout ?? (shapes.get(element) === 'u16' && length === 3 ? logicalLayout(6, '[u16;3]') : undefined);
    return makeSchema<T[]>({ name: `[${element.schema()};${length}]`, layout: selected, encodeInto(v, out) {
            arrayCount(v, length);
            for (let i = 0; i < length; i++)
                emit(element, v[i], out);
        }, decodeFrom(r) {
            r.collection(length, (weight ?? error('collection element requires explicit allocation layout')).bytes);
            const out: T[] = [];
            for (let i = 0; i < length; i++)
                out.push(r.value(element));
            return out;
        } }, undefined, undefined, combined([element]));
}
export type SchemaValue<S> = S extends ValueSchema<infer T> ? T : never;
export type TupleValue<S extends readonly ValueSchema<any>[]> = {
    -readonly [K in keyof S]: SchemaValue<S[K]>;
};
function tuple<const S extends readonly [
    ValueSchema<any>,
    ...ValueSchema<any>[]
]>(fields: S, layout?: LogicalLayout): ValueSchema<TupleValue<S>> {
    const n = arrayCount(fields);
    if (n < 1 || n > 4)
        return error('tuple arity must be 1..4');
    const owned = Array.from({ length: n }, (_, i) => fields[i]);
    const selected = layout ?? (n === 2 && shapes.get(owned[0]) === 'u8' && shapes.get(owned[1]) === 'u64' ? logicalLayout(16, '(u8,u64)') : undefined);
    return makeSchema<TupleValue<S>>({ name: `(${owned.map(s => s.schema()).join(',')})`, layout: selected, encodeInto(v, out) {
            arrayCount(v, n);
            for (let i = 0; i < n; i++)
                emit(owned[i], v[i], out);
        }, decodeFrom(r) { return owned.map(s => r.value(s)) as TupleValue<S>; } }, undefined, undefined, combined(owned));
}
function oracleState<S, H>(model: ValueSchema<S>, oracle: ValueSchema<H>, layout?: LogicalLayout): ValueSchema<{
    model: S;
    oracle: H;
}> {
    return makeSchema({ name: `OracleState<${model.schema()},${oracle.schema()}>`, layout, encodeInto(value, out) {
            if (value === null || typeof value !== 'object')
                return error('expected OracleState');
            const m = value.model, h = value.oracle;
            emit(model, m, out);
            emit(oracle, h, out);
        }, decodeFrom(r) { return { model: r.value(model), oracle: r.value(oracle) }; } }, undefined, undefined, combined([model, oracle]));
}
export function withLength<T>(schema: LengthSchema<T>, width: number): ValueSchema<T> {
    nativeLengthWidth(width);
    const encode = schema.encodeLength, raw = schema.encodeLengthRaw, decode = schema.decodeLength;
    if (typeof encode !== 'function' || typeof decode !== 'function')
        return error('schema has no length override');
    return makeSchema({ name: `${schema.schema()} length${width}`, layout: schema.layout, encodeInto(v, out) { (rawScopes.has(out) ? raw : encode).call(schema, v, width, out); }, decodeFrom(r) { return decode.call(schema, width, r); } }, shapes.get(schema), undefined, lengthDirections.get(schema) ?? { encode: typeof schema.encodeLengthRaw === 'function', decode: typeof schema.decodeLength === 'function' });
}
export const valueCodecs = Object.freeze({ u8, u16, u32, u64, u128, i8, i16, i32, i64, i128, bool, unit, string, option, vector, array, tuple, oracleState });

/** Directional views do not fabricate the other three capabilities. */
export interface ValueEncoder<T> { encodeRaw(value: T, out: EncodeBuffer): void; schema(): string; variant(value: T): string | null }
export interface ValueDecoder<T> { decodeFrom(reader: Decoder): T }
export interface LengthEncoder<T> { encodeLengthRaw(value: T, width: number, out: EncodeBuffer): void }
export interface LengthDecoder<T> { decodeLength(width: number, reader: Decoder): T }
type Requirement = 'encode' | 'decode' | 'both';
export interface CodecField<T> { readonly key: PropertyKey; readonly get: (value: T) => unknown; readonly encode?: (value: any, out: EncodeBuffer) => void; readonly decode?: (reader: Decoder) => unknown }
const bindings = new WeakSet<object>();
function keysOnly(value: object, keys: readonly string[]): void { for (const k of Reflect.ownKeys(value)) if (typeof k !== 'string' || !keys.includes(k)) error('unknown declaration option'); }
function callback(value: unknown): void { if (value !== undefined && typeof value !== 'function') error('invalid declaration callback'); }
function ownedArray<T>(array: readonly T[]): T[] { if (!Array.isArray(array)) return error('expected declaration array'); const n = array.length, out: T[] = []; for (let i = 0; i < n; i++) { if (!Object.prototype.hasOwnProperty.call(array, i)) error('sparse declaration array'); out.push(array[i]); } return out; }
function requirement(value: Requirement | undefined, d: Directions): void { if (value !== undefined && value !== 'encode' && value !== 'decode' && value !== 'both') error('invalid direction requirement'); if ((value === 'encode' || value === 'both') && !d.encode) error('declaration has no encoder'); if ((value === 'decode' || value === 'both') && !d.decode) error('declaration has no decoder'); }
export function codecField<T>() {
    function field(key: PropertyKey, codec: any, width?: number, get: (value: T) => unknown = value => (value as any)[key]): CodecField<T> {
        if (typeof key !== 'string' && typeof key !== 'number' && typeof key !== 'symbol') error('invalid field key');
        if (!codec || typeof codec !== 'object') error('invalid field codec');
        if (width !== undefined && width !== 1 && width !== 2 && width !== 4) error('field length width must be 1, 2 or 4');
        const raw = width === undefined ? codec.encodeRaw : codec.encodeLengthRaw, dec = width === undefined ? codec.decodeFrom : codec.decodeLength;
        callback(raw); callback(dec); callback(get);
        const capturedDirections = width === undefined ? directions.get(codec) : lengthDirections.get(codec);
        const d = capturedDirections ?? { encode: typeof raw === 'function', decode: typeof dec === 'function' };
        const captured = width === undefined ? { decodeFrom: (r: Decoder) => dec.call(codec, r) } : null;
        const result = Object.freeze({ key, get, encode: d.encode ? (v: unknown, out: EncodeBuffer) => synchronousVoid(width === undefined ? raw.call(codec, v, out) : raw.call(codec, v, width, out)) : undefined, decode: d.decode ? (r: Decoder) => width === undefined ? r.value(captured as ValueSchema<unknown>) : noPromise(dec.call(codec, width, r)) : undefined });
        bindings.add(result); return result;
    }
    return Object.freeze({
        value<K extends keyof T>(key: K, codec: ValueEncoder<T[K]> | ValueDecoder<T[K]>) { return field(key, codec); },
        length<K extends keyof T>(key: K, codec: LengthEncoder<T[K]> | LengthDecoder<T[K]>, width: number) { return field(key, codec, width); },
        access<V>(key: PropertyKey, codec: ValueEncoder<V> | ValueDecoder<V>, get: (value: T) => V) { if (typeof get !== 'function') error('invalid field accessor'); return field(key, codec, undefined, get); },
        adapter<K extends keyof T>(key: K, adapter: { encode?: (value: T[K], out: EncodeBuffer) => void; decode?: (reader: Decoder) => T[K] }) {
            if (typeof key !== 'string' && typeof key !== 'number' && typeof key !== 'symbol') error('invalid field key');
            keysOnly(adapter, ['encode', 'decode']); const { encode, decode } = adapter; callback(encode); callback(decode);
            const result = Object.freeze({ key, get: (v: T) => v[key], encode: encode ? (v: any, out: EncodeBuffer) => withRawChildEncodeBuffer(out, child => synchronousVoid(encode.call(adapter, v, child))) : undefined, decode: decode ? (r: Decoder) => noPromise(decode.call(adapter, r)) : undefined }); bindings.add(result); return result;
        }
    });
}
export interface RecordDeclaration<T> { name: string; fields: readonly CodecField<T>[]; construct?: (fields: any) => T; constructPositional?: (...fields: any[]) => T; require?: Requirement; layout?: LogicalLayout | null }
function recordParts<T>(source: RecordDeclaration<T>, tuple = false) {
    keysOnly(source, ['name', 'fields', 'construct', 'constructPositional', 'require', 'layout']);
    const { name, fields, construct, constructPositional, require: required, layout } = source;
    strictTextLength(name); if (!name.length) error('empty diagnostic name'); callback(construct); callback(constructPositional); if (construct && constructPositional) error('choose one record constructor');
    const owned = ownedArray(fields), seen = new Set<PropertyKey>();
    for (const f of owned) { if (!bindings.has(f)) error('invalid field binding'); if (seen.has(f.key)) error('duplicate field key'); seen.add(f.key); }
    const d = { encode: owned.every(f => !!f.encode), decode: !!(construct || constructPositional) && owned.every(f => !!f.decode) }; requirement(required, d);
    const copiedLayout = layoutCopy(layout);
    return { name, layout: copiedLayout, d, encode(v: T, out: EncodeBuffer) { for (const f of owned) f.encode!(f.get(v), out); }, decode(r: Decoder): T { const values: any = tuple || constructPositional ? [] : Object.create(null); for (const f of owned) { const value = f.decode!(r); if (tuple || constructPositional) values.push(value); else Object.defineProperty(values, f.key, { value, enumerable: true, writable: true, configurable: true }); } return noPromise(constructPositional ? constructPositional(...values) : construct!(values)); } };
}
export function orderedRecord<T>(source: RecordDeclaration<T>): ValueSchema<T> { const p = recordParts(source); return makeSchema({ name: p.name, layout: p.layout, encodeInto: p.encode, decodeFrom: p.decode }, undefined, undefined, p.d); }
export function tupleRecord<T>(source: RecordDeclaration<T>): ValueSchema<T> { const p = recordParts(source, true); return makeSchema({ name: p.name, layout: p.layout, encodeInto: p.encode, decodeFrom: p.decode }, undefined, undefined, p.d); }
export function unitRecord<T>(source: { name: string; construct?: () => T; require?: Requirement; layout?: LogicalLayout | null }): ValueSchema<T> { keysOnly(source, ['name', 'construct', 'require', 'layout']); const { name, construct, require: required, layout } = source; return orderedRecord({ name, construct, require: required, layout, fields: [] }); }
export interface EnumCase<T> { readonly label: string; readonly tag: number; readonly declaration: RecordDeclaration<T> }
const enumBindings = new WeakSet<object>();
export function enumCases<T, K extends keyof T>(discriminator: K) {
    function make(label: string, tag: number, declaration: RecordDeclaration<any>): EnumCase<T> { const result = Object.freeze({ label, tag, declaration }); enumBindings.add(result); return result; }
    return Object.freeze({ unit<V extends T[K] & string>(label: V, tag: number, construct?: () => Extract<T, Record<K, V>>) { return make(label, tag, { name: label, fields: [], construct }); }, record<V extends T[K] & string>(label: V, tag: number, declaration: Omit<RecordDeclaration<Extract<T, Record<K, V>>>, 'name'>) { const { fields, construct, constructPositional, require: required, layout } = declaration; keysOnly(declaration, ['fields', 'construct', 'constructPositional', 'require', 'layout']); return make(label, tag, { name: label, fields, construct, constructPositional, require: required, layout }); }, tuple<V extends T[K] & string>(label: V, tag: number, declaration: Omit<RecordDeclaration<Extract<T, Record<K, V>>>, 'name'>) { const { fields, construct, require: required, layout } = declaration; keysOnly(declaration, ['fields', 'construct', 'require', 'layout']); const capturedFields = ownedArray(fields), capturedKeys = capturedFields.map(f => f.key); const entry = make(label, tag, { name: label, fields: capturedFields, construct: construct ? values => construct(capturedKeys.map(key => values[key])) : undefined, require: required, layout }); return entry; } });
}
export function taggedEnum<T>(source: { name: string; discriminator: keyof T; cases: readonly EnumCase<T>[]; tagType?: 'u8' | 'u16' | 'u32'; require?: Requirement; layout?: LogicalLayout | null }): ValueSchema<T> {
    keysOnly(source, ['name', 'discriminator', 'cases', 'tagType', 'require', 'layout']); const { name, discriminator, cases, tagType = 'u8', require: required, layout } = source;
    strictTextLength(name); if (!name.length) error('empty diagnostic name'); if (typeof discriminator !== 'string' && typeof discriminator !== 'symbol' && typeof discriminator !== 'number') error('invalid discriminator');
    const tagCodec = tagType === 'u8' ? u8 : tagType === 'u16' ? u16 : tagType === 'u32' ? u32 : error('invalid tag type'), max = tagType === 'u8' ? 255 : tagType === 'u16' ? 65535 : 4294967295;
    const byLabel = new Map<string, any>(), byTag = new Map<number, any>(), parts = ownedArray(cases).map(c => { if (!enumBindings.has(c)) error('invalid enum case'); const { label, tag, declaration } = c; strictTextLength(label); if (!label.length || !Number.isSafeInteger(tag) || tag < 0 || tag > max) error('invalid enum tag or label'); if (byLabel.has(label) || byTag.has(tag)) error('duplicate enum tag or label'); const p = { ...recordParts(declaration), label, tag }; byLabel.set(label, p); byTag.set(tag, p); return p; });
    const d = { encode: parts.every(p => p.d.encode), decode: parts.every(p => p.d.decode) }; requirement(required, d);
    const select = (v: T) => byLabel.get((v as any)[discriminator]) ?? error('unknown enum discriminator');
    return makeSchema({ name, layout, variant: v => select(v).label, encodeInto(v, out) { const p = select(v); tagCodec.encodeRaw(p.tag, out); p.encode(v, out); }, decodeFrom(r) { const tag = r.value(tagCodec), p = byTag.get(tag); if (!p) error('unknown enum tag'); return p.decode(r); } }, undefined, undefined, d);
}

/** Bind eight version-gated hooks while keeping original model receivers intact. */
export function bindModelCodec<M extends { metadata(): { codec_version: number } }, S, I, O>(model: M, declaration: { version?: number; state: ValueSchema<S>; input: ValueSchema<I>; output: ValueEncoder<O>; decodeLimits?: Partial<DecodeLimits> | (() => Partial<DecodeLimits>) }): M & import('./core.js').CodecModel<S, I, O> {
    keysOnly(declaration, ['version', 'state', 'input', 'output', 'decodeLimits']);
    const { version = 1, state: stateCodec, input, output, decodeLimits } = declaration;
    if (!Number.isSafeInteger(version) || version <= 0 || version > 0xffffffff) error('invalid codec version');
    const capture = (s: any, decode: boolean) => { if (!s || typeof s !== 'object') error('invalid model codec'); const encode = s.encodeRaw, from = decode ? s.decodeFrom : undefined, d = directions.get(s) ?? { encode: typeof encode === 'function', decode: typeof from === 'function' }; if (!d.encode || (decode && !d.decode) || typeof encode !== 'function' || (decode && typeof from !== 'function')) error('missing model codec direction'); return { encodeRaw: (v: any, out: EncodeBuffer) => synchronousVoid(encode.call(s, v, out)), ...(decode ? { decodeFrom: (r: Decoder) => noPromise(from.call(s, r)) } : {}) } as ValueSchema<any>; };
    const s = capture(stateCodec, true), i = capture(input, true), o = capture(output, false), limits = typeof decodeLimits === 'function' ? decodeLimits : resolveDecodeLimits(decodeLimits);
    if (typeof model.metadata !== 'function') error('model metadata required');
    const gate = () => { const metadata = model.metadata; if (typeof metadata !== 'function') error('model metadata required'); const current = noPromise(metadata.call(model)).codec_version; if (!Number.isSafeInteger(current) || current < 0 || current > 0xffffffff || current !== version) error('ModelCodec derive version does not match Model metadata'); };
    const hooks: Record<string, Function> = Object.create(null);
    for (const [name, codec] of [['State', s], ['Input', i], ['Output', o]] as const) { hooks[`encode${name}`] = (v: any) => { gate(); return encodeValue(codec, v, U64); }; hooks[`encode${name}Into`] = (v: any, out: EncodeBuffer) => { gate(); codec.encodeRaw(v, out); }; }
    for (const [name, codec] of [['State', s], ['Input', i]] as const) hooks[`decode${name}`] = (bytes: Uint8Array) => { gate(); const resolved = typeof limits === 'function' ? noPromise(limits.call(undefined)) : limits; return decodeValue(codec, bytes, resolved); };
    return new Proxy(Object.create(null), { get(_target, key) { if (typeof key === 'string' && Object.hasOwn(hooks, key)) return hooks[key]; const value = Reflect.get(model, key, model); return typeof value === 'function' ? value.bind(model) : value; }, has(_target, key) { return Object.hasOwn(hooks, key) || key in model; }, ownKeys() { return [...new Set([...Reflect.ownKeys(model), ...Reflect.ownKeys(hooks)])]; }, getOwnPropertyDescriptor(_target, key) { if (Object.hasOwn(hooks, key)) return { value: hooks[key as string], enumerable: true, configurable: true, writable: false }; const desc = Reflect.getOwnPropertyDescriptor(model, key); return desc ? { ...desc, configurable: true } : undefined; } }) as M & import('./core.js').CodecModel<S, I, O>;
}
