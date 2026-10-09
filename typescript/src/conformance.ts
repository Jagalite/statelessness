/** Finite table adapter. Every operation calls the public native engine. */
import { type Check, type Disposition, type CodecModel, type EnumerateModel, type Metadata, type Transition, metadata, ModelError, Rng, VERSION, SPEC_VERSION, PROFILES, enumerateStates, checkObserved, record, replay } from './core.js';
import { fields, string, integer, array, boolean, fallback, strictParse, FormatError, checkFromData, dispositionFromData, metadataFromData, traceToData, traceFromData } from './json.js';
export const MAX_LINE = 4 * 1024 * 1024;
interface Edge { input: string; to: string; outputs: string[]; disposition: Disposition; checks: Check[]; stepError: boolean; checkError: boolean }
interface Row { checks: Check[]; edges: Edge[]; checkError: boolean; inputsError: boolean }
export class TableModel implements CodecModel<string, string, string>, EnumerateModel<string, string, string> {
  private readonly rows = new Map<string, Row>();
  private readonly initial: string;
  private readonly identity: Metadata;
  private readonly initialError: boolean;
  stepCalls = 0;
  constructor(value: unknown) {
    const o = fields(value, ['id', 'initial', 'states'], ['metadata', 'initial_error']);
    const id = string(o.id); this.initial = string(o.initial);
    this.identity = Object.hasOwn(o, 'metadata') ? metadataFromData(o.metadata) : metadata(id, 'fixture-v1');
    this.initialError = boolean(fallback(o, 'initial_error', false));
    let edgeCount = 0;
    for (const value of array(o.states, 1000)) {
      const s = fields(value, ['id'], ['checks', 'edges', 'check_error', 'inputs_error']), name = string(s.id);
      if (this.rows.has(name)) throw new FormatError('duplicate state');
      const checks = array(fallback(s, 'checks', []), 4096).map(checkFromData), seen = new Map<string, string>();
      const edges = array(fallback(s, 'edges', []), 10_000).map(value => {
        const e = fields(value, ['input', 'to'], ['outputs', 'disposition', 'checks', 'step_error', 'check_error']);
        const edge: Edge = { input: string(e.input), to: string(e.to), outputs: array(fallback(e, 'outputs', []), 4096).map(string), disposition: dispositionFromData(fallback(e, 'disposition', { kind: 'accepted' })), checks: array(fallback(e, 'checks', []), 4096).map(checkFromData), stepError: boolean(fallback(e, 'step_error', false)), checkError: boolean(fallback(e, 'check_error', false)) };
        const semantic = JSON.stringify(edge);
        if (seen.has(edge.input) && seen.get(edge.input) !== semantic) throw new FormatError('conflicting transitions');
        seen.set(edge.input, semantic); edgeCount++; return edge;
      });
      this.rows.set(name, { checks, edges, checkError: boolean(fallback(s, 'check_error', false)), inputsError: boolean(fallback(s, 'inputs_error', false)) });
    }
    if (edgeCount > 10_000 || !this.rows.has(this.initial)) throw new FormatError('invalid initial state or model size');
    for (const row of this.rows.values()) for (const edge of row.edges) if (!this.rows.has(edge.to)) throw new FormatError('unknown target');
  }
  cloneState(s: string): string { return s; }
  cloneInput(s: string): string { return s; }
  cloneOutput(s: string): string { return s; }
  equalStates(a: string, b: string): boolean { return a === b; }
  hashState(): number { return 0; } // Force collisions to qualify equality handling.
  metadata(): Metadata { return this.identity; }
  initialState(): string { if (this.initialError) throw new ModelError('injected initial_state'); return this.initial; }
  private row(s: string): Row { const r = this.rows.get(s); if (!r) throw new ModelError('unknown state'); return r; }
  private edge(s: string, i: string): Edge { const e = this.row(s).edges.find(e => e.input === i); if (!e) throw new ModelError("input is not in this state's domain"); return e; }
  inputs(s: string): string[] { const r = this.row(s); if (r.inputsError) throw new ModelError('injected inputs'); return r.edges.map(e => e.input); }
  step(s: string, i: string): Transition<string, string> { this.stepCalls++; const e = this.edge(s, i); if (e.stepError) throw new ModelError('injected step'); return { state: e.to, outputs: e.outputs, disposition: e.disposition }; }
  checkState(s: string): Check[] { const r = this.row(s); if (r.checkError) throw new ModelError('injected check_state'); return r.checks; }
  checkTransition(s: string, i: string): Check[] { const e = this.edge(s, i); if (e.checkError) throw new ModelError('injected check_transition'); return e.checks; }
  encodeState(s: string): Uint8Array { this.row(s); return new TextEncoder().encode(s); }
  decodeState(b: Uint8Array): string { const s = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(b); this.row(s); return s; }
  encodeInput(s: string): Uint8Array { return new TextEncoder().encode(s); }
  decodeInput(b: Uint8Array): string { return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(b); }
  encodeOutput(s: string): Uint8Array { return new TextEncoder().encode(s); }
}
function u64(v: unknown): bigint { const s = string(v); if (!/^(0|[1-9][0-9]{0,19})$/.test(s) || BigInt(s) > (1n << 64n) - 1n) throw new FormatError('invalid decimal u64'); return BigInt(s); }
export function execute(value: unknown): unknown {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new FormatError('expected request');
  const request = value as Record<string, unknown>;
  if (integer(request.version) !== 1) return { error: 'unsupported_version' };
  const op = string(request.operation);
  if (op === 'hello') { fields(value, ['version', 'operation']); return { implementation: 'typescript', package_version: VERSION, spec_version: SPEC_VERSION, profiles: PROFILES, protocol_version: 1 }; }
  if (op === 'rng') {
    fields(value, ['version', 'operation', 'seed', 'draws', 'bounds']); const rng = new Rng(u64(request.seed));
    const draws = integer(request.draws, 10_000), bounds = array(request.bounds, 10_000).map(u64);
    return { raw: Array.from({ length: draws }, () => rng.nextU64().toString()), indices: bounds.map(b => rng.index(b)?.toString() ?? null), next: rng.nextU64().toString() };
  }
  const extras: Record<string, string[]> = { enumerate: ['config'], record: ['inputs', 'max_steps'], observe: ['before', 'input', 'transition', 'sequence', 'policy'], replay: ['trace', 'allow_build_mismatch'] };
  if (!Object.hasOwn(extras, op)) return { error: 'unsupported_operation' };
  const o = fields(value, ['version', 'operation', 'model', ...extras[op]]), model = new TableModel(o.model);
  if (op === 'enumerate') {
    const c = fields(o.config, ['max_states', 'max_transitions', 'max_depth']);
    const maxStates = integer(c.max_states, 100_000), maxTransitions = integer(c.max_transitions, 100_000), maxDepth = integer(c.max_depth, 100_000);
    if (!maxStates) return { error: 'invalid_config' };
    return enumerateStates(model, { maxStates, maxTransitions, maxDepth });
  }
  if (op === 'record') return { trace: traceToData(record(model, array(o.inputs, 100_000).map(string), integer(o.max_steps, 100_000))) };
  if (op === 'observe') {
    const before = string(o.before), input = string(o.input), t = fields(o.transition, ['state', 'outputs', 'disposition']);
    const transition = { state: string(t.state), outputs: array(t.outputs, 4096).map(string), disposition: dispositionFromData(t.disposition) };
    const p = fields(o.policy, ['state_every', 'transition_checks']), sequence = integer(o.sequence, 100_000), stateEvery = integer(p.state_every, 100_000), transitionChecks = boolean(p.transition_checks);
    if (!sequence || !stateEvery) return { error: 'invalid_config' };
    return { checks: checkObserved(model, before, input, transition, sequence, { stateEvery, transitionChecks }), step_calls: model.stepCalls };
  }
  return replay(model, traceFromData(o.trace), boolean(o.allow_build_mismatch));
}
export function rawResponse(raw: Uint8Array): unknown {
  try { return execute(strictParse(raw, MAX_LINE)); }
  catch (error) {
    if (error instanceof ModelError) return { error: 'model_error' };
    if (error instanceof FormatError || error instanceof TypeError) return { error: 'invalid_request' };
    throw error; // Programming defects are process failures, not successful observations.
  }
}
