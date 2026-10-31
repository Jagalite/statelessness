/** Strict portable JSON, separate from the native runtime. No Node dependencies. */
import { validateTraceLimits, check, disposition, scalar, type Check, type Disposition, type Metadata, type Trace, type TraceStep, type TraceLimits, DEFAULT_TRACE_LIMITS } from './core.js';
export class FormatError extends Error { constructor(message: string) { super(message); this.name = 'FormatError'; } }
export function fields(value: unknown, required: readonly string[], optional: readonly string[] = []): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new FormatError('expected object');
  const obj = value as Record<string, unknown>;
  if (required.some(k => !Object.hasOwn(obj, k)) || Object.keys(obj).some(k => !required.includes(k) && !optional.includes(k))) throw new FormatError('unknown or missing field');
  return obj;
}
export function string(value: unknown): string { try { scalar(value); return value; } catch { throw new FormatError('expected scalar string'); } }
export function integer(value: unknown, maximum = Number.MAX_SAFE_INTEGER): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0 || value > maximum) throw new FormatError('invalid integer');
  return value;
}
export function boolean(value: unknown): boolean { if (typeof value !== 'boolean') throw new FormatError('expected boolean'); return value; }
export function array(value: unknown, maximum = 250_000): unknown[] { if (!Array.isArray(value) || value.length > maximum) throw new FormatError('expected bounded array'); return value; }
export function fallback(obj: Record<string, unknown>, key: string, defaultValue: unknown): unknown { return Object.hasOwn(obj, key) ? obj[key] : defaultValue; }
export function strictParse(data: string | Uint8Array, maximum = 64 * 1024 * 1024, exactIntegers = false): unknown {
  let text: string;
  try {
    // Valid UTF-8 uses at least one byte per UTF-16 code unit, including
    // surrogate pairs. Reject an obvious overflow before scanning or encoding
    // an arbitrarily large string; the exact byte check still handles non-ASCII.
    if (typeof data === 'string') { if (data.length > maximum) throw new FormatError('JSON byte limit'); scalar(data); if (new TextEncoder().encode(data).length > maximum) throw new FormatError('JSON byte limit'); text = data; }
    else { if (!(data instanceof Uint8Array) || data.length > maximum) throw new FormatError('JSON byte limit'); text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(data); }
  } catch { throw new FormatError('invalid UTF-8 or JSON byte limit'); }
  let p = 0;
  const fail = (): never => { throw new FormatError(`invalid JSON at offset ${p}`); };
  const space = () => { while (p < text.length && ' \t\r\n'.includes(text[p])) p++; };
  const quoted = (): string => {
    const start = p;
    if (text[p++] !== '"') return fail();
    while (p < text.length) {
      const c = text[p++];
      if (c === '"') {
        try { return string(JSON.parse(text.slice(start, p))); } catch { return fail(); }
      }
      if (c.charCodeAt(0) < 32) return fail();
      if (c === '\\') {
        const e = text[p++];
        if (e === 'u') { if (!/^[0-9a-fA-F]{4}$/.test(text.slice(p, p + 4))) return fail(); p += 4; }
        else if (!['"', '\\', '/', 'b', 'f', 'n', 'r', 't'].includes(e)) return fail();
      }
    }
    return fail();
  };
  const value = (depth: number): unknown => {
    if (depth > 64) throw new FormatError('JSON nesting limit');
    space();
    if (text[p] === '"') return quoted();
    if (text[p] === '{') {
      p++; space(); const result: Record<string, unknown> = Object.create(null);
      if (text[p] === '}') { p++; return result; }
      while (true) {
        space(); const key = quoted();
        if (Object.hasOwn(result, key)) throw new FormatError('duplicate object key');
        space(); if (text[p++] !== ':') return fail(); result[key] = value(depth + 1); space();
        const c = text[p++]; if (c === '}') return result; if (c !== ',') return fail();
      }
    }
    if (text[p] === '[') {
      p++; space(); const result: unknown[] = [];
      if (text[p] === ']') { p++; return result; }
      while (true) { result.push(value(depth + 1)); space(); const c = text[p++]; if (c === ']') return result; if (c !== ',') return fail(); }
    }
    for (const [token, v] of [['null', null], ['true', true], ['false', false]] as const) {
      if (text.startsWith(token, p)) { p += token.length; return v; }
    }
    const match = /^-?(?:0|[1-9][0-9]*)/.exec(text.slice(p));
    if (!match) return fail();
    p += match[0].length;
    // No fractional/exponent spelling is permitted, even when mathematically integral.
    if (text[p] === '.' || text[p] === 'e' || text[p] === 'E') return fail();
    const n = BigInt(match[0]);
    if (n < -(1n << 63n) || n > (1n << 64n) - 1n) return fail();
    // All meaningful numeric fields are separately bounded to <= 2^53-1.
    // Full-width seed/bound values are strings and never pass through Number.
    return exactIntegers ? n : Number(match[0]);
  };
  const result = value(0); space(); if (p !== text.length) return fail(); return result;
}
export function checkFromData(value: unknown): Check {
  const o = fields(value, ['id', 'status'], ['details']);
  return check(string(o.id), string(o.status) as Check['status'], string(fallback(o, 'details', '')));
}
export function dispositionFromData(value: unknown): Disposition {
  const o = fields(value, ['kind'], ['reason']);
  return disposition(string(o.kind) as Disposition['kind'], string(fallback(o, 'reason', '')));
}
export function metadataFromData(value: unknown): Metadata {
  const o = fields(value, ['name', 'model_version', 'properties_version', 'codec_version', 'build']);
  return { name: string(o.name), build: string(o.build), model_version: integer(o.model_version, 0xffffffff), properties_version: integer(o.properties_version, 0xffffffff), codec_version: integer(o.codec_version, 0xffffffff) };
}
export function hex(bytes: Uint8Array): string {
  if (!(bytes instanceof Uint8Array)) throw new FormatError('expected bytes');
  // Do not dispatch a subclass iterator: it can yield data unrelated to the
  // byte view and bypass the limits checked by stringifyTrace.
  const size = typedArrayLength.call(bytes) as number;
  const parts = new Array<string>(size);
  for (let i = 0; i < size; i++) parts[i] = bytes[i].toString(16).padStart(2, '0');
  return parts.join('');
}
const typedArrayLength = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(Uint8Array.prototype), 'length')!.get!;
export function traceToData(t: Trace): Record<string, unknown> {
  // Array subclass iterators, map overrides and species constructors are not
  // part of the wire representation. Capture indexed slots into plain arrays.
  const slots = <T, U>(source: readonly T[], project: (value: T) => U): U[] => {
    const result: U[] = [];
    const length = source.length;
    for (let i = 0; i < length; i++) result.push(project(source[i]));
    return result;
  };
  return { format: 'stateless.trace-json', version: 1, metadata: t.metadata,
    initial_state: hex(t.initialState), initial_checks: slots(t.initialChecks, c => c),
    steps: slots(t.steps, s => ({ input: hex(s.input), disposition: s.disposition, outputs: slots(s.outputs, hex), post_state: hex(s.postState), checks: slots(s.checks, c => c) })),
    termination: t.termination, error: t.error };
}
export function traceFromData(value: unknown, limits: TraceLimits = DEFAULT_TRACE_LIMITS): Trace {
  validateTraceLimits(limits);
  const o = fields(value, ['format', 'version', 'metadata', 'initial_state', 'initial_checks', 'steps', 'termination', 'error']);
  if (o.format !== 'stateless.trace-json' || o.version !== 1) throw new FormatError('unsupported trace format');
  let items = 0, payload = 0;
  const count = (n: number) => { items += n; if (items > limits.maxItems) throw new FormatError('trace item limit'); };
  // Direct callers may supply Array subclasses. Never delegate validation to
  // their map/iterator/species hooks: they can omit or substitute entries.
  const bounded = (value: unknown, maximum: number): [unknown[], number] => {
    if (!Array.isArray(value)) throw new FormatError('expected bounded array');
    const length = value.length;
    if (!Number.isSafeInteger(length) || length < 0 || length > maximum) throw new FormatError('expected bounded array');
    return [value, length];
  };
  const indexed = <T>(a: unknown[], length: number, convert: (entry: unknown) => T): T[] => {
    const result: T[] = [];
    for (let i = 0; i < length; i++) result.push(convert(a[i]));
    return result;
  };
  const checks = (v: unknown): Check[] => { const [a, length] = bounded(v, limits.maxItems); count(length); return indexed(a, length, checkFromData); };
  const blob = (v: unknown): Uint8Array => {
    const s = string(v);
    if (s.length > 2 * limits.maxBlobBytes || s.length % 2 !== 0 || !/^[0-9a-f]*$/.test(s)) throw new FormatError('invalid hexadecimal bytes');
    payload += s.length / 2; if (payload > limits.maxPayloadBytes) throw new FormatError('trace payload limit');
    const result = new Uint8Array(s.length / 2);
    for (let i = 0; i < result.length; i++) result[i] = Number.parseInt(s.slice(i * 2, i * 2 + 2), 16);
    return result;
  };
  const initialState = blob(o.initial_state), initialChecks = checks(o.initial_checks);
  const [stepArray, stepCount] = bounded(o.steps, limits.maxSteps);
  const steps: TraceStep[] = indexed(stepArray, stepCount, v => {
    const s = fields(v, ['input', 'disposition', 'outputs', 'post_state', 'checks']);
    const [a, length] = bounded(s.outputs, limits.maxItems); count(1 + length);
    return { input: blob(s.input), disposition: dispositionFromData(s.disposition), outputs: indexed(a, length, blob), postState: blob(s.post_state), checks: checks(s.checks) };
  });
  const termination = string(o.termination) as Trace['termination'], error = string(o.error);
  if (!['completed', 'property_failed', 'step_limit', 'interrupted', 'model_error'].includes(termination) || (error !== '' && termination !== 'model_error')) throw new FormatError('invalid trace termination');
  return { metadata: metadataFromData(o.metadata), initialState, initialChecks, steps, termination, error };
}
export function stringifyTrace(trace: Trace, limits: TraceLimits = DEFAULT_TRACE_LIMITS): string {
  validateTraceLimits(limits);
  // Reject oversized payloads and an unavoidable lower bound on JSON size
  // before expanding bytes to hexadecimal or building the intermediate tree.
  // UTF-8 uses at least one byte per UTF-16 code unit for valid scalar text;
  // JSON escaping can only increase that contribution. The normal parser and
  // final byte check still validate the complete shape and exact escaped size.
  // As with traceToData, callers must keep mutable trace inputs stable.
  let items = 0, payload = 0, lower = 0;
  const add = (n: number) => { if (n > limits.maxJSONBytes - lower) throw new FormatError('trace JSON limit'); lower += n; };
  const text = (v: unknown) => { if (typeof v === 'string') add(v.length); };
  const blob = (v: unknown) => {
    if (!(v instanceof Uint8Array)) throw new FormatError('expected bytes');
    const size = typedArrayLength.call(v) as number;
    if (size > limits.maxBlobBytes) throw new FormatError('invalid hexadecimal bytes');
    if (size > limits.maxPayloadBytes - payload) throw new FormatError('trace payload limit');
    payload += size;
    if (size > Math.floor((limits.maxJSONBytes - lower) / 2)) throw new FormatError('trace JSON limit');
    add(size * 2);
  };
  const count = (n: number) => { if (n > limits.maxItems - items) throw new FormatError('trace item limit'); items += n; add(n); };
  const checks = (v: readonly Check[]) => { const length = v.length; count(length); for (let i = 0; i < length; i++) { const c = v[i]; text(c.id); text(c.status); text(c.details); } };
  if (!trace || !trace.metadata || !Array.isArray(trace.initialChecks) || !Array.isArray(trace.steps)) throw new FormatError('expected bounded array');
  {
    if (trace.steps.length > limits.maxSteps) throw new FormatError('expected bounded array');
    text(trace.metadata.name); text(trace.metadata.build); text(trace.termination); text(trace.error);
    blob(trace.initialState); checks(trace.initialChecks);
    const stepCount = trace.steps.length;
    for (let i = 0; i < stepCount; i++) {
      const step = trace.steps[i];
      if (!step || !Array.isArray(step.outputs) || !Array.isArray(step.checks)) throw new FormatError('expected bounded array');
      const outputCount = step.outputs.length;
      count(1); count(outputCount); blob(step.input); blob(step.postState);
      for (let j = 0; j < outputCount; j++) blob(step.outputs[j]);
      text(step.disposition?.kind); text(step.disposition?.reason); checks(step.checks);
    }
  }
  const data = traceToData(trace);
  // Validate and reconstruct plain records before JSON.stringify: otherwise a
  // non-enumerable user toJSON on a check, disposition or metadata object can
  // substitute an unbounded value after the preflight.
  const validated = traceFromData(data, limits);
  const clean = traceToData(validated);
  // Keep the original enumerable field order and optional-field spelling while
  // taking values from the validated, hook-free records.
  const ordered = (original: unknown, validatedRecord: unknown): Record<string, unknown> => {
    const result: Record<string, unknown> = Object.create(null);
    const source = validatedRecord as Record<string, unknown>;
    for (const key of Object.keys(original as object)) result[key] = source[key];
    return result;
  };
  clean.metadata = ordered(data.metadata, clean.metadata);
  const restoreChecks = (original: unknown, target: unknown) => {
    const a = original as Record<string, unknown>[], b = target as Record<string, unknown>[];
    for (let i = 0; i < a.length; i++) b[i] = ordered(a[i], b[i]);
  };
  restoreChecks(data.initial_checks, clean.initial_checks);
  const oldSteps = data.steps as Record<string, unknown>[], newSteps = clean.steps as Record<string, unknown>[];
  for (let i = 0; i < oldSteps.length; i++) {
    restoreChecks(oldSteps[i].checks, newSteps[i].checks);
    newSteps[i].disposition = ordered(oldSteps[i].disposition, newSteps[i].disposition);
  }
  const result = JSON.stringify(clean);
  if (new TextEncoder().encode(result).length > limits.maxJSONBytes) throw new FormatError('trace JSON limit');
  return result;
}
export function parseTrace(data: string | Uint8Array, limits: TraceLimits = DEFAULT_TRACE_LIMITS): Trace { validateTraceLimits(limits); return traceFromData(strictParse(data, limits.maxJSONBytes), limits); }
