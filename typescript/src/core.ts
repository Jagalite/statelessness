/** Independent native engine. Callbacks describe effects; the engine never executes I/O.
 * Models must be deterministic. Explicit copy hooks preserve application-owned
 * classes and reference graphs without guessing that structuredClone is suitable.
 */
export const VERSION = '0.1.0';
export const SPEC_VERSION = '1.0.0';
export const PROFILES = ['core-v1', 'bfs-v1', 'rng-splitmix64-v1', 'trace-json-v1'] as const;
export class ModelError extends Error {
  constructor(message: string) { super(message); this.name = 'ModelError'; }
}
export class ConfigError extends Error {
  constructor(message: string) { super(message); this.name = 'ConfigError'; }
}
export type CheckStatus = 'passed' | 'failed' | 'skipped';
export interface Check { readonly id: string; readonly status: CheckStatus; readonly details: string }
export interface Disposition { readonly kind: 'accepted' | 'rejected' | 'ignored'; readonly reason: string }
export interface Transition<S, O> { readonly state: S; readonly outputs: readonly O[]; readonly disposition: Disposition }
export interface Metadata {
  readonly name: string; readonly model_version: number; readonly properties_version: number;
  readonly codec_version: number; readonly build: string;
}
export function scalar(value: unknown): asserts value is string {
  if (typeof value !== 'string') throw new TypeError('expected string');
  for (let i = 0; i < value.length; i++) {
    const c = value.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const next = value.charCodeAt(++i);
      if (!(next >= 0xdc00 && next <= 0xdfff)) throw new TypeError('invalid Unicode scalar');
    } else if (c >= 0xdc00 && c <= 0xdfff) throw new TypeError('invalid Unicode scalar');
  }
}
export function natural(value: number, name: string, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): number {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) throw new ConfigError(`${name} must be an integer in ${minimum}..${maximum}`);
  return value;
}
export function check(id: string, status: CheckStatus = 'passed', details = ''): Check {
  scalar(id); scalar(details);
  if (!['passed', 'failed', 'skipped'].includes(status) || (status === 'passed' && details !== '')) throw new TypeError('invalid check');
  return { id, status, details };
}
export function disposition(kind: Disposition['kind'] = 'accepted', reason = ''): Disposition {
  scalar(reason);
  if (!['accepted', 'rejected', 'ignored'].includes(kind) || (kind === 'accepted' && reason !== '')) throw new TypeError('invalid disposition');
  return { kind, reason };
}
export function accepted<S, O>(state: S, outputs: readonly O[] = []): Transition<S, O> {
  return { state, outputs, disposition: disposition() };
}
export function metadata(name: string, build = 'unqualified'): Metadata {
  scalar(name); scalar(build);
  return { name, build, model_version: 1, properties_version: 1, codec_version: 1 };
}
export function validateMetadata(m: Metadata): Metadata {
  scalar(m.name); scalar(m.build);
  for (const key of ['model_version', 'properties_version', 'codec_version'] as const) natural(m[key], key, 0, 0xffffffff);
  return { ...m };
}
export interface Model<S, I, O> {
  initialState(): S;
  step(state: S, input: I): Transition<S, O>;
  checkState(state: S): Iterable<Check>;
  checkTransition?(before: S, input: I, transition: Transition<S, O>): Iterable<Check>;
  /** Identity is appropriate only for immutable values; clone nested mutable data. */
  cloneState(state: S): S;
  cloneInput(input: I): I;
  cloneOutput(output: O): O;
}
export interface EnumerateModel<S, I, O> extends Model<S, I, O> {
  inputs(state: S): Iterable<I>;
  equalStates(left: S, right: S): boolean;
  /** Optional acceleration. Equal states MUST hash equally. Collisions use equalStates. */
  hashState?(state: S): string | number | bigint;
}
export interface CodecModel<S, I, O> extends Model<S, I, O> {
  metadata(): Metadata;
  encodeState(state: S): Uint8Array;
  decodeState(bytes: Uint8Array): S;
  encodeInput(input: I): Uint8Array;
  decodeInput(bytes: Uint8Array): I;
  encodeOutput(output: O): Uint8Array;
}
function call<T>(stage: string, callback: () => T): T {
  try { return callback(); }
  catch (error) { throw new ModelError(`${stage}: ${error instanceof Error ? error.message : String(error)}`); }
}
function copyState<S, I, O>(m: Model<S, I, O>, s: S): S { return call('snapshot', () => m.cloneState(s)); }
function copyInput<S, I, O>(m: Model<S, I, O>, i: I): I { return call('snapshot', () => m.cloneInput(i)); }
function copyTransition<S, I, O>(m: Model<S, I, O>, t: Transition<S, O>): Transition<S, O> {
  return call('snapshot', () => ({ state: m.cloneState(t.state), outputs: Array.from(t.outputs, o => m.cloneOutput(o)), disposition: disposition(t.disposition.kind, t.disposition.reason) }));
}
function collect(stage: string, callback: () => Iterable<Check>): Check[] {
  return call(stage, () => Array.from(callback(), c => check(c.id, c.status, c.details)));
}
function stateChecks<S, I, O>(m: Model<S, I, O>, s: S): Check[] {
  return collect('state check', () => m.checkState(copyState(m, s)));
}
function edgeChecks<S, I, O>(m: Model<S, I, O>, b: S, i: I, t: Transition<S, O>): Check[] {
  return m.checkTransition ? collect('transition check', () => m.checkTransition!(copyState(m, b), copyInput(m, i), copyTransition(m, t))) : [];
}
function step<S, I, O>(m: Model<S, I, O>, s: S, i: I): Transition<S, O> {
  return copyTransition(m, call('transition', () => m.step(copyState(m, s), copyInput(m, i))));
}
export interface CheckPolicy { readonly stateEvery: number; readonly transitionChecks: boolean }
export const FULL_CHECKS: CheckPolicy = Object.freeze({ stateEvery: 1, transitionChecks: true });
/** Observe exactly once: never calls step(). Initial state checking is separate. */
export function checkObserved<S, I, O>(m: Model<S, I, O>, before: S, input: I, transition: Transition<S, O>, sequence: number, policy: CheckPolicy = FULL_CHECKS): Check[] {
  natural(sequence, 'sequence', 1); natural(policy.stateEvery, 'stateEvery', 1);
  if (typeof policy.transitionChecks !== 'boolean') throw new ConfigError('transitionChecks must be boolean');
  const first = sequence % policy.stateEvery === 0 ? stateChecks(m, transition.state) : [check('stateless.state_checks', 'skipped', 'periodic checking policy')];
  const second = policy.transitionChecks ? edgeChecks(m, before, input, transition) : [check('stateless.transition_checks', 'skipped', 'disabled by policy')];
  return [...first, ...second];
}
function failed(checks: readonly Check[]): boolean { return checks.some(c => c.status === 'failed'); }
export interface SearchConfig { readonly maxStates: number; readonly maxTransitions: number; readonly maxDepth: number }
export const DEFAULT_SEARCH: SearchConfig = Object.freeze({ maxStates: 100_000, maxTransitions: 1_000_000, maxDepth: 100 });
export interface Violation { readonly phase: 'initial_state' | 'state' | 'transition'; readonly check: Check }
export interface Failure<I> { readonly inputs: readonly I[]; readonly violations: readonly Violation[] }
export interface SearchReport<I> {
  readonly termination: 'graph_exhausted' | 'depth_bound' | 'state_limit' | 'transition_limit' | 'failure_found';
  readonly states: number; readonly transitions: number; readonly max_depth_reached: number;
  readonly skipped_checks: number; readonly failure: Failure<I> | null;
}
/** FIFO search with exact equality, checked revisits, and explicit incomplete results. */
export function enumerateStates<S, I, O>(m: EnumerateModel<S, I, O>, config: SearchConfig = DEFAULT_SEARCH): SearchReport<I> {
  natural(config.maxStates, 'maxStates', 1); natural(config.maxTransitions, 'maxTransitions'); natural(config.maxDepth, 'maxDepth');
  const initial = copyState(m, call('initial_state', () => m.initialState()));
  const checks = stateChecks(m, initial);
  let skipped = checks.filter(c => c.status === 'skipped').length;
  const violations: Violation[] = checks.filter(c => c.status === 'failed').map(c => ({ phase: 'initial_state', check: c }));
  type Node = { state: S; parent: number; input?: I; depth: number };
  const nodes: Node[] = [{ state: initial, parent: -1, depth: 0 }];
  let transitions = 0, reached = 0, cutoff = false;
  const report = (termination: SearchReport<I>['termination'], failure: Failure<I> | null = null): SearchReport<I> => ({ termination, states: nodes.length, transitions, max_depth_reached: reached, skipped_checks: skipped, failure });
  if (violations.length) return report('failure_found', { inputs: [], violations });
  const hash = (s: S): string | number | bigint => call('state hash', () => m.hashState ? m.hashState(copyState(m, s)) : 0);
  const visited = new Map<string | number | bigint, number[]>([[hash(initial), [0]]]);
  // Admission order is FIFO, so the node array is also the queue. No array.shift().
  for (let cursor = 0; cursor < nodes.length; cursor++) {
    const node = nodes[cursor];
    const iterator = call('enumerate inputs', () => m.inputs(copyState(m, node.state))[Symbol.iterator]());
    const next = () => call('enumerate inputs', () => iterator.next());
    if (node.depth === config.maxDepth) { cutoff = !next().done || cutoff; continue; }
    for (let item = next(); !item.done; item = next()) {
      if (transitions === config.maxTransitions) return report('transition_limit');
      const t = step(m, node.state, item.value);
      transitions++; reached = Math.max(reached, node.depth + 1);
      const sc = stateChecks(m, t.state), ec = edgeChecks(m, node.state, item.value, t);
      skipped += [...sc, ...ec].filter(c => c.status === 'skipped').length;
      const bad: Violation[] = [
        ...sc.filter(c => c.status === 'failed').map(c => ({ phase: 'state' as const, check: c })),
        ...ec.filter(c => c.status === 'failed').map(c => ({ phase: 'transition' as const, check: c }))
      ];
      if (bad.length) {
        const inputs = [copyInput(m, item.value)];
        for (let k = cursor; nodes[k].parent >= 0; k = nodes[k].parent) inputs.push(copyInput(m, nodes[k].input as I));
        inputs.reverse(); return report('failure_found', { inputs, violations: bad });
      }
      const key = hash(t.state), bucket = visited.get(key) ?? [];
      if (bucket.some(k => call('state equality', () => m.equalStates(copyState(m, nodes[k].state), copyState(m, t.state))))) continue;
      if (nodes.length === config.maxStates) return report('state_limit');
      bucket.push(nodes.length); visited.set(key, bucket);
      nodes.push({ state: t.state, parent: cursor, input: copyInput(m, item.value), depth: node.depth + 1 });
    }
  }
  return report(cutoff ? 'depth_bound' : 'graph_exhausted');
}
export interface TraceStep { readonly input: Uint8Array; readonly disposition: Disposition; readonly outputs: readonly Uint8Array[]; readonly postState: Uint8Array; readonly checks: readonly Check[] }
export type Termination = 'completed' | 'property_failed' | 'step_limit' | 'interrupted' | 'model_error';
export interface Trace { readonly metadata: Metadata; readonly initialState: Uint8Array; readonly initialChecks: readonly Check[]; readonly steps: readonly TraceStep[]; readonly termination: Termination; readonly error: string }
export interface TraceLimits { readonly maxSteps: number; readonly maxBlobBytes: number; readonly maxItems: number; readonly maxPayloadBytes: number; readonly maxJSONBytes: number }
export const DEFAULT_TRACE_LIMITS: TraceLimits = Object.freeze({ maxSteps: 100_000, maxBlobBytes: 4 * 1024 * 1024, maxItems: 250_000, maxPayloadBytes: 32 * 1024 * 1024, maxJSONBytes: 64 * 1024 * 1024 });
export function validateTraceLimits(limits: TraceLimits): void {
  for (const key of ['maxSteps', 'maxBlobBytes', 'maxItems', 'maxPayloadBytes', 'maxJSONBytes'] as const) natural(limits[key], key);
}
function encode(stage: string, callback: () => Uint8Array, maximum = Number.MAX_SAFE_INTEGER): Uint8Array {
  return call(stage, () => {
    const value = callback();
    if (!(value instanceof Uint8Array)) throw new TypeError('codec must return Uint8Array');
    if (value.length > maximum) throw new Error('encoded blob exceeds byte limit');
    return new Uint8Array(value);
  });
}
export function record<S, I, O>(m: CodecModel<S, I, O>, inputs: Iterable<I>, maxSteps = 100_000, limits: TraceLimits = DEFAULT_TRACE_LIMITS): Trace {
  natural(maxSteps, 'maxSteps'); validateTraceLimits(limits);
  let state = copyState(m, call('initial state', () => m.initialState()));
  const initialChecks = collect('initial check', () => m.checkState(copyState(m, state)));
  const initialState = encode('encode initial state', () => m.encodeState(copyState(m, state)), limits.maxBlobBytes);
  const identity = call('metadata', () => validateMetadata(m.metadata()));
  let items = initialChecks.length, payload = initialState.length;
  if (items > limits.maxItems || payload > limits.maxPayloadBytes) throw new ModelError('recording limit: initial checkpoint');
  const steps: TraceStep[] = [];
  const result = (termination: Termination, error = ''): Trace => ({ metadata: identity, initialState, initialChecks, steps, termination, error });
  if (failed(initialChecks)) return result('property_failed');
  try {
    const iterator = call('inputs', () => inputs[Symbol.iterator]());
    while (true) {
      const item = call('inputs', () => iterator.next());
      if (item.done) return result('completed');
      if (steps.length >= Math.min(maxSteps, limits.maxSteps)) return result('step_limit');
      const input = encode('encode input', () => m.encodeInput(copyInput(m, item.value)), limits.maxBlobBytes);
      const t = step(m, state, item.value), checks = checkObserved(m, state, item.value, t, steps.length + 1);
      const nextItems = items + 1 + checks.length + t.outputs.length;
      if (nextItems > limits.maxItems) throw new ModelError('recording limit: aggregate items');
      const outputs = t.outputs.map(o => encode('encode output', () => m.encodeOutput(call('snapshot', () => m.cloneOutput(o))), limits.maxBlobBytes));
      const postState = encode('encode state', () => m.encodeState(copyState(m, t.state)), limits.maxBlobBytes);
      const nextPayload = payload + input.length + postState.length + outputs.reduce((n, o) => n + o.length, 0);
      if (nextPayload > limits.maxPayloadBytes) throw new ModelError('recording limit: aggregate payload bytes');
      steps.push({ input, disposition: t.disposition, outputs, postState, checks });
      state = t.state; items = nextItems; payload = nextPayload;
      if (failed(checks)) return result('property_failed');
    }
  } catch (error) {
    if (!(error instanceof ModelError)) throw error;
    return result('model_error', error.message);
  }
}
export interface ReplayReport { outcome: 'exact' | 'diverged' | 'incompatible'; steps_verified: number; failure_reproduced: boolean; build_matches: boolean; step: number | null; field: string | null }
export function bytesEqual(a: Uint8Array, b: Uint8Array): boolean { return a.length === b.length && a.every((v, i) => v === b[i]); }
function checksEqual(a: readonly Check[], b: readonly Check[]): boolean { return a.length === b.length && a.every((c, i) => c.id === b[i].id && c.status === b[i].status && c.details === b[i].details); }
function sameFailure(a: readonly Check[], b: readonly Check[]): boolean { return a.some(x => x.status === 'failed' && b.some(y => y.id === x.id && y.status === 'failed')); }
export function validateRecording(t: Trace): void {
  if (!['completed', 'property_failed', 'step_limit', 'interrupted', 'model_error'].includes(t.termination)) throw new ModelError('unknown trace termination');
  if (t.error && t.termination !== 'model_error') throw new ModelError('error text requires model_error termination');
  if (failed(t.initialChecks) && t.steps.length) throw new ModelError('trace continues after initial property failure');
  if (t.steps.slice(0, -1).some(s => failed(s.checks))) throw new ModelError('trace continues after property failure');
  const bad = failed(t.initialChecks) || (t.steps.length > 0 && failed(t.steps[t.steps.length - 1].checks));
  if (bad !== (t.termination === 'property_failed')) throw new ModelError('trace termination disagrees with recorded checks');
}
/** Exact means the stored prefix matched; it does not verify any terminal callback error. */
export function replay<S, I, O>(m: CodecModel<S, I, O>, trace: Trace, allowBuildMismatch = false): ReplayReport {
  if (typeof allowBuildMismatch !== 'boolean') throw new ConfigError('allowBuildMismatch must be boolean');
  const identity = call('metadata', () => validateMetadata(m.metadata()));
  const r: ReplayReport = { outcome: 'exact', steps_verified: 0, failure_reproduced: false, build_matches: identity.build === trace.metadata.build, step: null, field: null };
  if (['name', 'model_version', 'properties_version', 'codec_version'].some(k => identity[k as keyof Metadata] !== trace.metadata[k as keyof Metadata]) || (!r.build_matches && !allowBuildMismatch)) { r.outcome = 'incompatible'; return r; }
  validateRecording(trace);
  let state = call('decode initial state', () => m.decodeState(new Uint8Array(trace.initialState)));
  if (!bytesEqual(encode('encode initial state', () => m.encodeState(copyState(m, state))), trace.initialState)) throw new ModelError('initial state encoding is not canonical');
  const initial = collect('initial check', () => m.checkState(copyState(m, state)));
  r.failure_reproduced = sameFailure(trace.initialChecks, initial);
  if (!checksEqual(initial, trace.initialChecks)) { r.outcome = 'diverged'; r.field = 'initial checks'; return r; }
  for (let index = 0; index < trace.steps.length; index++) {
    const expected = trace.steps[index];
    const input = call('decode input', () => m.decodeInput(new Uint8Array(expected.input)));
    if (!bytesEqual(encode('encode input', () => m.encodeInput(copyInput(m, input))), expected.input)) throw new ModelError(`input ${index + 1} encoding is not canonical`);
    const actual = step(m, state, input), checks = checkObserved(m, state, input, actual, index + 1);
    r.failure_reproduced ||= sameFailure(expected.checks, checks);
    const outputs = actual.outputs.map(o => encode('encode output', () => m.encodeOutput(call('snapshot', () => m.cloneOutput(o)))));
    const postState = encode('encode state', () => m.encodeState(copyState(m, actual.state)));
    const mismatch = actual.disposition.kind !== expected.disposition.kind || actual.disposition.reason !== expected.disposition.reason ? 'disposition'
      : outputs.length !== expected.outputs.length || outputs.some((o, i) => !bytesEqual(o, expected.outputs[i])) ? 'outputs'
      : !bytesEqual(postState, expected.postState) ? 'state' : !checksEqual(checks, expected.checks) ? 'checks' : null;
    if (mismatch) { r.outcome = 'diverged'; r.step = index + 1; r.field = mismatch; return r; }
    r.steps_verified++; state = actual.state;
  }
  return r;
}
const MASK = (1n << 64n) - 1n;
/** SplitMix64 with exact modulo arithmetic; not a cryptographic generator. */
export class Rng {
  private state: bigint;
  constructor(seed = 0n) { this.state = this.u64(seed); }
  private u64(n: bigint): bigint { if (typeof n !== 'bigint' || n < 0n || n > MASK) throw new ConfigError('expected u64 bigint'); return n; }
  nextU64(): bigint {
    this.state = (this.state + 0x9e3779b97f4a7c15n) & MASK;
    let z = this.state;
    z = ((z ^ (z >> 30n)) * 0xbf58476d1ce4e5b9n) & MASK;
    z = ((z ^ (z >> 27n)) * 0x94d049bb133111ebn) & MASK;
    return z ^ (z >> 31n);
  }
  index(upper: bigint): bigint | null {
    this.u64(upper); if (upper === 0n) return null;
    const threshold = (1n << 64n) % upper;
    while (true) { const value = this.nextU64(); if (value >= threshold) return value % upper; }
  }
}
