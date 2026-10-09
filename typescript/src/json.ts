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
export function strictParse(data: string | Uint8Array, maximum = 64 * 1024 * 1024): unknown {
  let text: string;
  try {
    if (typeof data === 'string') { scalar(data); if (new TextEncoder().encode(data).length > maximum) throw new FormatError('JSON byte limit'); text = data; }
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
    return Number(match[0]);
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
  return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
}
export function traceToData(t: Trace): Record<string, unknown> {
  return { format: 'stateless.trace-json', version: 1, metadata: t.metadata,
    initial_state: hex(t.initialState), initial_checks: t.initialChecks,
    steps: t.steps.map(s => ({ input: hex(s.input), disposition: s.disposition, outputs: s.outputs.map(hex), post_state: hex(s.postState), checks: s.checks })),
    termination: t.termination, error: t.error };
}
export function traceFromData(value: unknown, limits: TraceLimits = DEFAULT_TRACE_LIMITS): Trace {
  validateTraceLimits(limits);
  const o = fields(value, ['format', 'version', 'metadata', 'initial_state', 'initial_checks', 'steps', 'termination', 'error']);
  if (o.format !== 'stateless.trace-json' || o.version !== 1) throw new FormatError('unsupported trace format');
  let items = 0, payload = 0;
  const count = (n: number) => { items += n; if (items > limits.maxItems) throw new FormatError('trace item limit'); };
  const checks = (v: unknown): Check[] => { const a = array(v, limits.maxItems); count(a.length); return a.map(checkFromData); };
  const blob = (v: unknown): Uint8Array => {
    const s = string(v);
    if (s.length > 2 * limits.maxBlobBytes || s.length % 2 !== 0 || !/^[0-9a-f]*$/.test(s)) throw new FormatError('invalid hexadecimal bytes');
    payload += s.length / 2; if (payload > limits.maxPayloadBytes) throw new FormatError('trace payload limit');
    const result = new Uint8Array(s.length / 2);
    for (let i = 0; i < result.length; i++) result[i] = Number.parseInt(s.slice(i * 2, i * 2 + 2), 16);
    return result;
  };
  const initialState = blob(o.initial_state), initialChecks = checks(o.initial_checks);
  const steps: TraceStep[] = array(o.steps, limits.maxSteps).map(v => {
    const s = fields(v, ['input', 'disposition', 'outputs', 'post_state', 'checks']);
    const a = array(s.outputs, limits.maxItems); count(1 + a.length);
    return { input: blob(s.input), disposition: dispositionFromData(s.disposition), outputs: a.map(blob), postState: blob(s.post_state), checks: checks(s.checks) };
  });
  const termination = string(o.termination) as Trace['termination'], error = string(o.error);
  if (!['completed', 'property_failed', 'step_limit', 'interrupted', 'model_error'].includes(termination) || (error !== '' && termination !== 'model_error')) throw new FormatError('invalid trace termination');
  return { metadata: metadataFromData(o.metadata), initialState, initialChecks, steps, termination, error };
}
export function stringifyTrace(trace: Trace, limits: TraceLimits = DEFAULT_TRACE_LIMITS): string {
  validateTraceLimits(limits);
  const data = traceToData(trace); traceFromData(data, limits);
  const result = JSON.stringify(data);
  if (new TextEncoder().encode(result).length > limits.maxJSONBytes) throw new FormatError('trace JSON limit');
  return result;
}
export function parseTrace(data: string | Uint8Array, limits: TraceLimits = DEFAULT_TRACE_LIMITS): Trace { validateTraceLimits(limits); return traceFromData(strictParse(data, limits.maxJSONBytes), limits); }
