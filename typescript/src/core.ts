import {ModelError,ConfigError} from './errors.js';
export {ModelError,ConfigError} from './errors.js';
import {replayBasic} from './replay.js';
import {EncodeStorage,encodeModel,type EncodeBuffer} from './encoding.js';
export {EncodeStorage,EncodeBuffer,withEncodeBuffer} from './encoding.js';
import {CheckSink,withCheckSink,appendStateChecks,appendTransitionChecks} from './checks.js';
export {CheckSink,withCheckSink} from './checks.js';
import {runControl, searchChecks, addSkippedChecks, type RunLimits, type SearchObserver} from './search_support.js';
/** Independent native engine. Callbacks describe effects; the engine never executes I/O.
 * Models must be deterministic. Explicit copy hooks preserve application-owned
 * classes and reference graphs without guessing that structuredClone is suitable.
 */
export const VERSION = '0.1.0';
export const SPEC_VERSION = '1.0.0';
export const PROFILES = ['core-v1', 'bfs-v1', 'rng-splitmix64-v1', 'trace-json-v1'] as const;
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
  const {name,build,model_version,properties_version,codec_version}=m;
  scalar(name);scalar(build);
  for(const [key,value]of [['model_version',model_version],['properties_version',properties_version],['codec_version',codec_version]] as const)natural(value,key,0,0xffffffff);
  return {name,build,model_version,properties_version,codec_version};
}
export interface Model<S, I, O> {
  /** Logical retained-state estimate, excluding engine storage; null means unknown. */
  estimatedStateBytes?(state: S): bigint | null;
  initialState(): S;
  step(state: S, input: I): Transition<S, O>;
  checkState(state: S): Iterable<Check>;
  checkStateInto?(state:S,sink:CheckSink):void;
  checkTransitionInto?(before:S,input:I,transition:Transition<S,O>,sink:CheckSink):void;
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
  encodeStateInto?(state:S,buffer:EncodeBuffer):void;
  encodeInputInto?(input:I,buffer:EncodeBuffer):void;
  encodeOutputInto?(output:O,buffer:EncodeBuffer):void;
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
function stateChecks<S,I,O>(m:Model<S,I,O>,s:S,stage='state check'):Check[]{
  const output:Check[]=[];call(stage,()=>withCheckSink(output,sink=>appendStateChecks(m,s,sink)));return output;
}
function step<S, I, O>(m: Model<S, I, O>, s: S, i: I): Transition<S, O> {
  return copyTransition(m, call('transition', () => m.step(copyState(m, s), copyInput(m, i))));
}
export interface CheckPolicy { readonly stateEvery: number; readonly transitionChecks: boolean }
export const FULL_CHECKS: CheckPolicy = Object.freeze({ stateEvery: 1, transitionChecks: true });
/** Clear caller storage at entry and on every error; checks an already executed turn without step(). */
export interface ExactObservedCheckPolicy { readonly stateEvery: bigint; readonly transitionChecks: boolean }
export function checkObservedCountIntoExact<S,I,O>(m:Model<S,I,O>,before:S,input:I,transition:Transition<S,O>,sequence:bigint,output:Check[],policy:ExactObservedCheckPolicy={stateEvery:1n,transitionChecks:true}):number {
  output.length=0;
  try {
    const {stateEvery,transitionChecks}=policy;
    const maximum=(1n<<64n)-1n;
    if(typeof sequence!=='bigint'||sequence<1n||sequence>maximum)throw new ConfigError('transition sequence must be positive u64 bigint');
    if(typeof stateEvery!=='bigint'||stateEvery<1n||stateEvery>maximum)throw new ConfigError('stateEvery must be positive u64 bigint');
    if(typeof transitionChecks!=='boolean')throw new ConfigError('transitionChecks must be boolean');
    let boundary=0;
    withCheckSink(output,sink=>{
      if(sequence%stateEvery===0n)call('state check',()=>appendStateChecks(m,transition.state,sink));
      else sink.push(check('stateless.state_checks','skipped','periodic checking policy'));
      boundary=output.length;
      if(transitionChecks)call('transition check',()=>appendTransitionChecks(m,before,input,transition,sink));
      else sink.push(check('stateless.transition_checks','skipped','disabled by policy'));
    });
    return boundary;
  } catch(error){output.length=0;throw error;}
}
export function checkObservedCountInto<S,I,O>(m:Model<S,I,O>,before:S,input:I,transition:Transition<S,O>,sequence:number,output:Check[],policy:CheckPolicy=FULL_CHECKS):number {
  output.length=0;
  try {
    const {stateEvery,transitionChecks}=policy;
    natural(sequence,'sequence',1);natural(stateEvery,'stateEvery',1);
    return checkObservedCountIntoExact(m,before,input,transition,BigInt(sequence),output,{stateEvery:BigInt(stateEvery),transitionChecks});
  }catch(error){output.length=0;throw error;}
}
export function checkObservedInto<S,I,O>(m:Model<S,I,O>,before:S,input:I,transition:Transition<S,O>,sequence:number,output:Check[],policy:CheckPolicy=FULL_CHECKS):void {checkObservedCountInto(m,before,input,transition,sequence,output,policy);}
/** Collection convenience shares the append dispatcher and never calls step(). */
export function checkObserved<S,I,O>(m:Model<S,I,O>,before:S,input:I,transition:Transition<S,O>,sequence:number,policy:CheckPolicy=FULL_CHECKS):Check[]{
  const output:Check[]=[];checkObservedInto(m,before,input,transition,sequence,output,policy);return output;
}
function failed(checks: readonly Check[]): boolean { return checks.some(c => c.status === 'failed'); }
export interface SearchConfig { readonly maxStates: number; readonly maxTransitions: number; readonly maxDepth: number }
export const DEFAULT_SEARCH: SearchConfig = Object.freeze({ maxStates: 100_000, maxTransitions: 1_000_000, maxDepth: 100 });
export interface Violation { readonly phase: 'initial_state' | 'state' | 'transition'; readonly check: Check }
export interface Failure<I> { readonly inputs: readonly I[]; readonly violations: readonly Violation[] }
export interface SearchLimits {
    readonly control?: RunLimits;
    readonly maxEstimatedStateBytes?: bigint;
}
export interface SearchReport<I> {
    readonly termination: 'graph_exhausted' | 'depth_bound' | 'state_limit' | 'transition_limit' | 'failure_found' | 'estimated_state_byte_limit' | 'cancelled' | 'deadline';
    readonly states: number;
    readonly transitions: number;
    readonly max_depth_reached: number;
    readonly skipped_checks: number;
    readonly estimated_retained_state_bytes: bigint | null;
    readonly failure: Failure<I> | null;
}
export interface ExactSearchConfig { readonly maxStates:number; readonly maxTransitions:bigint; readonly maxDepth:number }
export const DEFAULT_EXACT_SEARCH:ExactSearchConfig=Object.freeze({maxStates:100000,maxTransitions:1000000n,maxDepth:100});
export type ExactSearchReport<I> = Omit<SearchReport<I>,'transitions'|'skipped_checks'> & {readonly transitions:bigint;readonly skipped_checks:bigint};
/** Compatibility entrypoint: safe Number budgets/reports; the shared kernel uses exact bigint counters. */
export function enumerateStates<S,I,O>(m:EnumerateModel<S,I,O>,config:SearchConfig=DEFAULT_SEARCH,limits:SearchLimits={},observer?:SearchObserver<S,I,O>):SearchReport<I> {
  const {maxStates,maxTransitions,maxDepth}=config;
  natural(maxStates,'maxStates',1);natural(maxTransitions,'maxTransitions');natural(maxDepth,'maxDepth');
  const r=enumerateStatesExact(m,{maxStates,maxTransitions:BigInt(maxTransitions),maxDepth},limits,observer);
  if(r.transitions>BigInt(Number.MAX_SAFE_INTEGER)||r.skipped_checks>BigInt(Number.MAX_SAFE_INTEGER))throw new ModelError('BFS counters exceed safe Number range; use enumerateStatesExact');
  return {...r,transitions:Number(r.transitions),skipped_checks:Number(r.skipped_checks)};
}
/** FIFO search with exact equality, checked revisits, cooperative controls and admission estimates. */
export function enumerateStatesExact<S, I, O>(m: EnumerateModel<S, I, O>, config: ExactSearchConfig = DEFAULT_EXACT_SEARCH, limits: SearchLimits = {}, observer?: SearchObserver<S, I, O>): ExactSearchReport<I> {
    const {maxStates,maxTransitions,maxDepth}=config;
    natural(maxStates, 'maxStates', 1);
    if(typeof maxTransitions!=='bigint'||maxTransitions<0n||maxTransitions>((1n<<64n)-1n))throw new ConfigError('maxTransitions must be u64 bigint');
    natural(maxDepth, 'maxDepth');
    const cap = limits.maxEstimatedStateBytes;
    if (cap !== undefined && (typeof cap !== 'bigint' || cap < 0n || cap > ((1n << 64n) - 1n)))
        throw new ConfigError('maxEstimatedStateBytes must be u64 bigint');
    const stop = runControl(limits.control ?? {});
    type Node = {
        state: S;
        parent: number;
        input?: I;
        depth: number;
    };
    const nodes: Node[] = [];
    const checkStorage:Check[]=[];
    let states = 0, transitions = 0n, reached = 0, skipped = 0n, retained: bigint | null = null, cutoff = false;
    const report = (termination: SearchReport<I>['termination'], failure: Failure<I> | null = null): ExactSearchReport<I> => ({ termination, states, transitions, max_depth_reached: reached, skipped_checks: skipped, estimated_retained_state_bytes: retained, failure });
    const stopped = (): ExactSearchReport<I> | null => { const reason = stop(); return reason ? report(reason) : null; };
    const estimate = (s: S): bigint | null => call('estimated_state_bytes', () => {
        const n = m.estimatedStateBytes ? m.estimatedStateBytes(copyState(m, s)) : null;
        if (n !== null && (typeof n !== 'bigint' || n < 0n || n > ((1n << 64n) - 1n)))
            throw new TypeError('estimatedStateBytes must return u64 bigint or null');
        return n;
    });
    const hash = (s: S): string | number | bigint => call('state hash', () => m.hashState ? m.hashState(copyState(m, s)) : 0);
    const path = (index: number): I[] => { const out: I[] = []; for (let k = index; nodes[k].parent >= 0; k = nodes[k].parent)
        out.push(copyInput(m, nodes[k].input as I)); out.reverse(); return out; };
    let halted = stopped();
    if (halted)
        return halted;
    const initial = copyState(m, call('initial_state', () => m.initialState()));
    states = 1;
    const checked = searchChecks(m, initial, undefined, undefined, undefined, observer,[],checkStorage);
    skipped = addSkippedChecks(skipped,checked.skipped);
    if (checked.violations.length)
        return report('failure_found', { inputs: [], violations: checked.violations });
    halted = stopped();
    if (halted)
        return halted;
    retained = estimate(initial);
    halted = stopped();
    if (halted)
        return halted;
    if (cap !== undefined) {
        if (retained === null)
            throw new ModelError('state byte budget requires estimated_state_bytes');
        if (retained > cap) {
            states = 0;
            retained = 0n;
            return report('estimated_state_byte_limit');
        }
    }
    const initialHash = hash(initial);
    halted = stopped();
    if (halted)
        return halted;
    const visited = new Map<string | number | bigint, number[]>([[initialHash, [0]]]);
    nodes.push({ state: initial, parent: -1, depth: 0 });
    for (let cursor = 0; cursor < nodes.length; cursor++) {
        halted = stopped();
        if (halted)
            return halted;
        const node = nodes[cursor];
        const iterator = call('enumerate inputs', () => m.inputs(copyState(m, node.state))[Symbol.iterator]());
        halted = stopped();
        if (halted)
            return halted;
        const next = () => call('enumerate inputs', () => iterator.next());
        if (node.depth === maxDepth) {
            const item = next();
            halted = stopped();
            if (halted)
                return halted;
            cutoff = !item.done || cutoff;
            continue;
        }
        for (;;) {
            halted = stopped();
            if (halted)
                return halted;
            const item = next();
            halted = stopped();
            if (halted)
                return halted;
            if (item.done)
                break;
            if (transitions === maxTransitions)
                return report('transition_limit');
            const t = step(m, node.state, item.value);
            transitions++;
            reached = Math.max(reached, node.depth + 1);
            const wantHistory = observer?.needsHistory ? call('observer needs_history', () => { const v = observer.needsHistory!(); if (typeof v !== 'boolean')
                throw new TypeError('needsHistory must return boolean'); return v; }) : false;
            const history = wantHistory ? [...path(cursor), copyInput(m, item.value)] : [];
            const checked = searchChecks(m, t.state, node.state, item.value, t, observer, history,checkStorage);
            skipped = addSkippedChecks(skipped,checked.skipped);
            if (checked.violations.length)
                return report('failure_found', { inputs: [...path(cursor), copyInput(m, item.value)], violations: checked.violations });
            halted = stopped();
            if (halted)
                return halted;
            const key = hash(t.state);
            halted = stopped();
            if (halted)
                return halted;
            const bucket = visited.get(key) ?? [];
            let duplicate = false;
            // Rust's same-hash collision chain visits the most recently admitted state first.
            for (const other of bucket) {
                halted = stopped();
                if (halted)
                    return halted;
                const equal = call('state equality', () => { const v = m.equalStates(copyState(m, nodes[other].state), copyState(m, t.state)); if (typeof v !== 'boolean')
                    throw new TypeError('equalStates must return boolean'); return v; });
                halted = stopped();
                if (halted)
                    return halted;
                if (equal) {
                    duplicate = true;
                    break;
                }
            }
            if (duplicate)
                continue;
            if (nodes.length === maxStates)
                return report('state_limit');
            const bytes = estimate(t.state);
            halted = stopped();
            if (halted)
                return halted;
            const sum = retained === null || bytes === null ? null : retained + bytes;
            const total = sum !== null && sum > ((1n << 64n) - 1n) ? null : sum;
            if (cap !== undefined) {
                if (bytes === null)
                    throw new ModelError('state byte budget requires estimated_state_bytes');
                if (total === null || total > cap)
                    return report('estimated_state_byte_limit');
            }
            bucket.unshift(nodes.length);
            visited.set(key, bucket);
            nodes.push({ state: t.state, parent: cursor, input: copyInput(m, item.value), depth: node.depth + 1 });
            states = nodes.length;
            retained = total;
        }
    }
    return report(cutoff ? 'depth_bound' : 'graph_exhausted');
}
export function enumerateStatesObserved<S, I, O>(m: EnumerateModel<S, I, O>, config: SearchConfig, observer: SearchObserver<S, I, O>, limits: SearchLimits = {}): SearchReport<I> { return enumerateStates(m, config, limits, observer); }
export interface TraceStep { readonly input: Uint8Array; readonly disposition: Disposition; readonly outputs: readonly Uint8Array[]; readonly postState: Uint8Array; readonly checks: readonly Check[] }
export type Termination = 'completed' | 'property_failed' | 'step_limit' | 'interrupted' | 'model_error';
export interface Trace { readonly metadata: Metadata; readonly initialState: Uint8Array; readonly initialChecks: readonly Check[]; readonly steps: readonly TraceStep[]; readonly termination: Termination; readonly error: string }
export interface TraceLimits { readonly maxSteps: number; readonly maxBlobBytes: number; readonly maxItems: number; readonly maxPayloadBytes: number; readonly maxJSONBytes: number }
export const DEFAULT_TRACE_LIMITS: TraceLimits = Object.freeze({ maxSteps: 100_000, maxBlobBytes: 4 * 1024 * 1024, maxItems: 250_000, maxPayloadBytes: 32 * 1024 * 1024, maxJSONBytes: 64 * 1024 * 1024 });
export function validateTraceLimits(limits: TraceLimits): void {
  for (const key of ['maxSteps', 'maxBlobBytes', 'maxItems', 'maxPayloadBytes', 'maxJSONBytes'] as const) natural(limits[key], key);
}
export function record<S, I, O>(m: CodecModel<S, I, O>, inputs: Iterable<I>, maxSteps = 100_000, limits: TraceLimits = DEFAULT_TRACE_LIMITS): Trace {
  limits={maxSteps:limits.maxSteps,maxBlobBytes:limits.maxBlobBytes,maxItems:limits.maxItems,maxPayloadBytes:limits.maxPayloadBytes,maxJSONBytes:limits.maxJSONBytes};
  natural(maxSteps, 'maxSteps'); validateTraceLimits(limits);
  const encodingStorage=new EncodeStorage();
  let state = copyState(m, call('initial state', () => m.initialState()));
  const initialChecks = stateChecks(m,state,'initial check');
  const initialState = call('encode initial state',()=>encodeModel(m,'state',state,encodingStorage,limits.maxBlobBytes));
  const identity = call('metadata', () => validateMetadata(m.metadata()));
  let items = initialChecks.length, payload = initialState.length;
  if (items > limits.maxItems || payload > limits.maxPayloadBytes) throw new ModelError('recording limit: initial checkpoint');
  const steps: TraceStep[] = [];
  const checkStorage:Check[]=[];
  const result = (termination: Termination, error = ''): Trace => ({ metadata: identity, initialState, initialChecks, steps, termination, error });
  if (failed(initialChecks)) return result('property_failed');
  try {
    const iterator = call('inputs', () => inputs[Symbol.iterator]());
    while (true) {
      const item = call('inputs', () => iterator.next());
      if (item.done) return result('completed');
      if (steps.length >= Math.min(maxSteps, limits.maxSteps)) return result('step_limit');
      const input = call('encode input',()=>encodeModel(m,'input',item.value,encodingStorage,limits.maxBlobBytes));
      const t=step(m,state,item.value);
      checkObservedInto(m,state,item.value,t,steps.length+1,checkStorage);
      const checks=checkStorage.map(c=>({...c}));
      const nextItems = items + 1 + checks.length + t.outputs.length;
      if (nextItems > limits.maxItems) throw new ModelError('recording limit: aggregate items');
      const outputs = t.outputs.map(o => call('encode output',()=>encodeModel(m,'output',o,encodingStorage,limits.maxBlobBytes)));
      const postState = call('encode state',()=>encodeModel(m,'state',t.state,encodingStorage,limits.maxBlobBytes));
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
export function validateRecording(t: Trace): void {
  if (!['completed', 'property_failed', 'step_limit', 'interrupted', 'model_error'].includes(t.termination)) throw new ModelError('unknown trace termination');
  if (t.error && t.termination !== 'model_error') throw new ModelError('error text requires model_error termination');
  if (failed(t.initialChecks) && t.steps.length) throw new ModelError('trace continues after initial property failure');
  if (t.steps.slice(0, -1).some(s => failed(s.checks))) throw new ModelError('trace continues after property failure');
  const bad = failed(t.initialChecks) || (t.steps.length > 0 && failed(t.steps[t.steps.length - 1].checks));
  if (bad !== (t.termination === 'property_failed')) throw new ModelError('trace termination disagrees with recorded checks');
}
/** Exact means the stored prefix matched; it does not verify any terminal callback error. */
export function replay<S,I,O>(m:CodecModel<S,I,O>,trace:Trace,allowBuildMismatch=false):ReplayReport {return replayBasic(m,trace,allowBuildMismatch);}
const MASK = (1n << 64n) - 1n;
/** SplitMix64 with exact modulo arithmetic; not a cryptographic generator. */
export class Rng {
  private state: bigint;
  constructor(seed = 0n) { this.state = this.u64(seed); }
  private u64(n: bigint): bigint { if (typeof n !== 'bigint' || n < 0n || n > MASK) throw new ConfigError('expected u64 bigint'); return n; }
  /** Independent snapshot for transactional finite-domain sampling. */
  clone(): Rng { return new Rng(this.state); }
  /** Adopt the state of a successful sampling transaction. */
  copyFrom(other: Rng): void { this.state = other.state; }
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
