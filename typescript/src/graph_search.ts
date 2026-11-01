/** Experimental extension `graph-search-v1` (S1 admitted graph, S2 failure
 * collection); see spec/graph-search-v1.md. With default options the model
 * callback sequence is exactly bfs-controls-v1. `enumerateStates` is unchanged
 * and remains the portable bfs-v1 entry point. */
import { type EnumerateModel, type Model, type Transition, type Check, type Disposition, type Violation, type Failure, type SearchLimits, ModelError, ConfigError, natural, check, disposition, bytesEqual } from './core.js';
import { runControl, searchChecks, captureEdgeConsumers, addSkippedChecks, type SearchObserver } from './search_support.js';
import { U64_MAX } from './counts.js';
import type { EquivalenceReduction, ReductionDecision, RejectedEquivalence } from './reduction.js';

export type GraphNodeID = number;
/** Dense in evaluation order. Numbers stay exact: an ID past 2^53-1 is an error, never rounded. */
export type GraphEdgeID = number;
export type FailurePolicy =
  /** bfs-v1: the first failing check batch ends the search. */
  | { readonly kind: 'stopAtFirst' }
  /** Record every failing batch. `maxRetainedFindings` bounds retained witnesses only. */
  | { readonly kind: 'collect'; readonly maxRetainedFindings: number; readonly expandFailingSuccessors: boolean };
/** Exceeding either cap ends the search with `graph_retention_limit`. `maxGraphBytes`
 * bounds engine bookkeeping for nodes and retained edges, not model state. */
export interface GraphRetention { readonly retainEdges?: boolean; readonly maxRetainedEdges?: number; readonly maxGraphBytes?: bigint }
export type NodeExpansion =
  | { readonly kind: 'unexpanded' } | { readonly kind: 'complete' } | { readonly kind: 'depthFrontier' }
  | { readonly kind: 'failurePruned' } | { readonly kind: 'interrupted'; readonly reason: string };
export type EdgeTarget =
  /** Only observed in `edgeEvaluated` events; resolved before reports return. */
  | { readonly kind: 'pending' }
  | { readonly kind: 'admitted'; readonly node: GraphNodeID }
  | { readonly kind: 'duplicate'; readonly node: GraphNodeID }
  | { readonly kind: 'reduced'; readonly edge: GraphEdgeID }
  | { readonly kind: 'rejected'; readonly reason: string };
export interface GraphNode<S, I> {
  readonly id: GraphNodeID;
  readonly state: S;
  readonly parent: GraphNodeID | null;
  readonly input: I | null;
  readonly admittingEdge: GraphEdgeID | null;
  readonly depth: number;
  /** Initial checks for the root; otherwise the admitting edge's state checks. */
  readonly checks: readonly Check[];
  /** True when any check batch on the root path, including this node's, failed. */
  readonly pathFailed: boolean;
  expansion: NodeExpansion;
}
export interface GraphEdge<I, O> {
  readonly id: GraphEdgeID;
  readonly source: GraphNodeID;
  readonly input: I;
  readonly disposition: Disposition;
  readonly outputs: readonly O[];
  readonly stateChecks: readonly Check[];
  readonly transitionChecks: readonly Check[];
  readonly annotations: readonly string[];
  /** Reports are values; callers may rewrite a copy, e.g. for negative controls. */
  target: EdgeTarget;
}
export interface GraphFinding<I> {
  readonly index: bigint;
  /** Root-to-edge inputs; empty for an initial-state finding. */
  readonly witness: readonly I[];
  /** Every failed check of the batch, state checks before transition checks. */
  readonly violations: readonly Violation[];
  /** null for an initial-state finding. */
  readonly edge: GraphEdgeID | null;
  /** The witness prefix crosses an earlier failure (continued-failure-evidence-v1). */
  readonly requiresContinuedEvidence: boolean;
}
export interface PropertyFailureCount { readonly phase: Violation['phase']; readonly id: string; count: bigint; readonly firstFinding: bigint }
export type GraphCoverage =
  | { readonly kind: 'exhaustive' }
  | { readonly kind: 'bounded'; readonly depthCutoff: boolean; readonly failurePruned: boolean }
  | { readonly kind: 'incomplete'; readonly reason: string };
export type GraphEvent<I, O> =
  | { readonly kind: 'rootAdmitted'; readonly node: GraphNodeID }
  | { readonly kind: 'expansionStarted'; readonly node: GraphNodeID }
  | { readonly kind: 'edgeEvaluated'; readonly edge: GraphEdge<I, O> }
  | { readonly kind: 'finding'; readonly index: bigint; readonly edge: GraphEdgeID | null; readonly violations: readonly Violation[] }
  | { readonly kind: 'successorAdmitted'; readonly edge: GraphEdgeID; readonly node: GraphNodeID }
  | { readonly kind: 'duplicateEdge'; readonly edge: GraphEdgeID; readonly existing: GraphNodeID }
  | { readonly kind: 'edgeReduced'; readonly edge: GraphEdgeID; readonly representative: GraphEdgeID }
  | { readonly kind: 'successorRejected'; readonly edge: GraphEdgeID; readonly reason: string }
  | { readonly kind: 'expansionFinished'; readonly node: GraphNodeID; readonly status: NodeExpansion }
  | { readonly kind: 'layerCompleted'; readonly depth: number }
  | { readonly kind: 'searchFinished'; readonly termination: string };
export interface GraphSearchOptions<S, I, O> {
  readonly failurePolicy?: FailurePolicy;
  readonly retention?: GraphRetention;
  readonly observer?: SearchObserver<S, I, O>;
  /** Synchronous event sink. Payloads are detached copies; errors abort. */
  readonly events?: (event: GraphEvent<I, O>) => void;
  /** User edge labels, called with detached copies after all checks. */
  readonly annotateEdge?: (before: S, input: I, transition: Transition<S, O>) => readonly string[];
  readonly reduction?: EquivalenceReduction<S, I, O>;
}
/** `maxTransitions` accepts a safe Number or a u64 bigint; reports use bigint counters. */
export interface GraphSearchConfig { readonly maxStates: number; readonly maxTransitions: number | bigint; readonly maxDepth: number }
export const DEFAULT_GRAPH_SEARCH: GraphSearchConfig = Object.freeze({ maxStates: 100_000, maxTransitions: 1_000_000n, maxDepth: 100 });
export interface GraphSearchReport<S, I, O> {
  termination: string;
  coverage: GraphCoverage;
  /** bfs-v1 meaning: admitted states, or 1 once the initial state exists. */
  states: number;
  /** Evaluated transitions, including duplicate, reduced and rejected edges. */
  transitions: bigint;
  maxDepthReached: number;
  skippedChecks: bigint;
  estimatedRetainedStateBytes: bigint | null;
  nodes: GraphNode<S, I>[];
  /** Indexed by edge ID while `edgesRetained`. */
  edges: GraphEdge<I, O>[];
  edgesRetained: boolean;
  /** Edges between admitted nodes (admitted plus duplicate targets). */
  graphEdges: bigint;
  duplicateEdges: bigint;
  reducedEdges: bigint;
  rejectedSuccessors: bigint;
  /** Admitted nodes whose expansion is not `complete`. */
  frontierSize: number;
  completedLayers: number;
  depthCutoff: boolean;
  failurePruned: boolean;
  /** Engine bookkeeping for nodes and retained edges (saturating). */
  estimatedGraphBytes: bigint;
  findings: GraphFinding<I>[];
  findingCount: bigint;
  failedProperties: PropertyFailureCount[];
  reductionDecisions: ReductionDecision[];
}
export function findingsTruncated(r: GraphSearchReport<unknown, unknown, unknown>): boolean { return r.findingCount > BigInt(r.findings.length); }
/** The first retained finding as a bfs-v1 failure. */
export function graphFailure<I>(r: GraphSearchReport<unknown, I, unknown>): Failure<I> | null {
  const f = r.findings[0];
  return f ? { inputs: f.witness, violations: f.violations } : null;
}
/** A model, checker, observer or sink error after the search started. The
 * report holds the evidence gathered before the error and is incomplete. */
export class GraphSearchError<S = unknown, I = unknown, O = unknown> extends ModelError {
  constructor(readonly error: unknown, readonly report: GraphSearchReport<S, I, O>) {
    super(error instanceof Error ? error.message : String(error));
    this.name = 'GraphSearchError';
  }
}
export function sameExpansion(a: NodeExpansion, b: NodeExpansion): boolean {
  return a.kind === b.kind && (a.kind !== 'interrupted' || a.reason === (b as { reason: string }).reason);
}
export function sameCoverage(a: GraphCoverage, b: GraphCoverage): boolean {
  if (a.kind !== b.kind) return false;
  if (a.kind === 'bounded') { const o = b as typeof a; return a.depthCutoff === o.depthCutoff && a.failurePruned === o.failurePruned; }
  if (a.kind === 'incomplete') return a.reason === (b as typeof a).reason;
  return true;
}
/** Follows reduced edges to their representative's resolved target. */
export function resolvedTarget(report: GraphSearchReport<unknown, unknown, unknown>, edge: GraphEdge<unknown, unknown>): GraphNodeID | null {
  let target = edge.target, hops = 0;
  while (target.kind === 'reduced' && target.edge >= 0 && target.edge < report.edges.length && hops <= report.edges.length) { target = report.edges[target.edge].target; hops++; }
  return target.kind === 'admitted' || target.kind === 'duplicate' ? target.node : null;
}

// Engine bookkeeping estimate. JavaScript exposes no object strides, so these are
// fixed nominal record sizes; text is charged by UTF-8 length. Not process RSS.
const NODE_BYTES = 64n, EDGE_BYTES = 96n, CHECK_BYTES = 32n, OUTPUT_SLOT_BYTES = 8n, LABEL_BYTES = 16n;
const encoder = new TextEncoder();
const saturating = (a: bigint, b: bigint): bigint => { const s = a + b; return s > U64_MAX ? U64_MAX : s; };
const utf8 = (s: string): bigint => BigInt(encoder.encode(s).length);
const checkBytes = (checks: readonly Check[]): bigint => checks.reduce((n, c) => saturating(n, CHECK_BYTES + utf8(c.id) + utf8(c.details)), 0n);
function call<T>(stage: string, callback: () => T): T {
  try { return callback(); }
  catch (error) { throw new ModelError(`${stage}: ${error instanceof Error ? error.message : String(error)}`); }
}
const copyState = <S, I, O>(m: Model<S, I, O>, s: S): S => call('snapshot', () => m.cloneState(s));
const copyInput = <S, I, O>(m: Model<S, I, O>, i: I): I => call('snapshot', () => m.cloneInput(i));
const copyOutputs = <S, I, O>(m: Model<S, I, O>, outputs: readonly O[]): O[] => call('snapshot', () => Array.from(outputs, o => m.cloneOutput(o)));
function copyTransition<S, I, O>(m: Model<S, I, O>, t: Transition<S, O>): Transition<S, O> {
  return call('snapshot', () => ({ state: m.cloneState(t.state), outputs: Array.from(t.outputs, o => m.cloneOutput(o)), disposition: disposition(t.disposition.kind, t.disposition.reason) }));
}
function step<S, I, O>(m: Model<S, I, O>, s: S, i: I): Transition<S, O> {
  return copyTransition(m, call('transition', () => m.step(copyState(m, s), copyInput(m, i))));
}
type Captured<T> = { readonly value: T } | { readonly error: unknown };
const capture = <T>(fn: () => T): Captured<T> => { try { return { value: fn() }; } catch (error) { return { error }; } };
const get = <T>(c: Captured<T>): T => { if ('error' in c) throw c.error; return c.value; };
const copyChecks = (checks: readonly Check[]): Check[] => checks.map(c => check(c.id, c.status, c.details));
const copyViolations = (v: readonly Violation[]): Violation[] => v.map(x => ({ phase: x.phase, check: check(x.check.id, x.check.status, x.check.details) }));
const sameChecks = (a: readonly Check[], b: readonly Check[]) => a.length === b.length && a.every((c, i) => c.id === b[i].id && c.status === b[i].status && c.details === b[i].details);
const COMPLETE: NodeExpansion = Object.freeze({ kind: 'complete' });
const UNEXPANDED: NodeExpansion = Object.freeze({ kind: 'unexpanded' });

/** Breadth-first exploration that retains the admitted graph and its evidence. */
export function exploreGraph<S, I, O>(m: EnumerateModel<S, I, O>, config: GraphSearchConfig = DEFAULT_GRAPH_SEARCH, limits: SearchLimits = {}, options: GraphSearchOptions<S, I, O> = {}): GraphSearchReport<S, I, O> {
  const { maxStates, maxDepth } = config, maxTransitionsValue = config.maxTransitions;
  natural(maxStates, 'maxStates', 1);
  let maxTransitions: bigint;
  if (typeof maxTransitionsValue === 'bigint') {
    if (maxTransitionsValue < 0n || maxTransitionsValue > U64_MAX) throw new ConfigError('maxTransitions must be u64 bigint');
    maxTransitions = maxTransitionsValue;
  } else maxTransitions = BigInt(natural(maxTransitionsValue, 'maxTransitions'));
  natural(maxDepth, 'maxDepth');
  const cap = limits.maxEstimatedStateBytes;
  if (cap !== undefined && (typeof cap !== 'bigint' || cap < 0n || cap > U64_MAX)) throw new ConfigError('maxEstimatedStateBytes must be u64 bigint');
  const policy = options.failurePolicy ?? { kind: 'stopAtFirst' };
  if (policy.kind !== 'stopAtFirst' && policy.kind !== 'collect') throw new ConfigError('unknown failure policy');
  const collect = policy.kind === 'collect';
  const expandFailing = policy.kind === 'collect' ? policy.expandFailingSuccessors : false;
  const maxFindings = policy.kind === 'collect' ? policy.maxRetainedFindings : 1;
  if (collect) {
    natural(maxFindings, 'maxRetainedFindings');
    if (typeof expandFailing !== 'boolean') throw new ConfigError('expandFailingSuccessors must be boolean');
  }
  const retainEdges = options.retention?.retainEdges ?? true, maxRetainedEdges = options.retention?.maxRetainedEdges, maxGraphBytes = options.retention?.maxGraphBytes;
  if (typeof retainEdges !== 'boolean') throw new ConfigError('retainEdges must be boolean');
  if (maxRetainedEdges !== undefined) natural(maxRetainedEdges, 'maxRetainedEdges');
  if (maxGraphBytes !== undefined && (typeof maxGraphBytes !== 'bigint' || maxGraphBytes < 0n || maxGraphBytes > U64_MAX)) throw new ConfigError('maxGraphBytes must be u64 bigint');
  const { observer, events: sink, annotateEdge, reduction } = options;
  const stop = runControl(limits.control ?? {});
  const needsEdgeRecord = retainEdges || sink !== undefined;
  const r: GraphSearchReport<S, I, O> = {
    termination: 'graph_exhausted', coverage: { kind: 'exhaustive' }, states: 0, transitions: 0n, maxDepthReached: 0, skippedChecks: 0n, estimatedRetainedStateBytes: null,
    nodes: [], edges: [], edgesRetained: retainEdges, graphEdges: 0n, duplicateEdges: 0n, reducedEdges: 0n, rejectedSuccessors: 0n,
    frontierSize: 0, completedLayers: 0, depthCutoff: false, failurePruned: false, estimatedGraphBytes: 0n, findings: [], findingCount: 0n, failedProperties: [], reductionDecisions: [],
  };
  const checkStorage: Check[] = [];
  const visited = new Map<string | number | bigint, number[]>();
  let nextEdge = 0, currentLayer = 0, activeNode: number | null = null;
  /** The evaluated edge whose successor is not yet resolved, retained or not. */
  let pendingEdge: GraphEdgeID | null = null;

  const stopped = (): boolean => { const reason = stop(); if (reason) { r.termination = reason; return true; } return false; };
  const emit = (event: () => GraphEvent<I, O>): void => {
    if (!sink) return;
    const value = event();
    call('graph event sink', () => { const result: unknown = sink(value); if (result !== undefined) throw new TypeError('event sink must return undefined synchronously'); });
  };
  const detached = (e: GraphEdge<I, O>): GraphEdge<I, O> => ({ id: e.id, source: e.source, input: copyInput(m, e.input), disposition: disposition(e.disposition.kind, e.disposition.reason), outputs: copyOutputs(m, e.outputs),
    stateChecks: copyChecks(e.stateChecks), transitionChecks: copyChecks(e.transitionChecks), annotations: [...e.annotations], target: { ...e.target } });
  const estimate = (s: S): bigint | null => call('estimated_state_bytes', () => {
    const n = m.estimatedStateBytes ? m.estimatedStateBytes(copyState(m, s)) : null;
    if (n !== null && (typeof n !== 'bigint' || n < 0n || n > U64_MAX)) throw new TypeError('estimatedStateBytes must return u64 bigint or null');
    return n;
  });
  const hash = (s: S): string | number | bigint => call('state hash', () => m.hashState ? m.hashState(copyState(m, s)) : 0);
  const path = (index: number): I[] => {
    const out: I[] = [];
    for (let k = index; r.nodes[k].parent !== null; k = r.nodes[k].parent!) out.push(copyInput(m, r.nodes[k].input as I));
    return out.reverse();
  };
  const witness = (cursor: number, input: I): I[] => [...path(cursor), copyInput(m, input)];
  /** False when the bytes would exceed the declared graph byte cap. */
  const chargeGraph = (bytes: bigint): boolean => {
    const total = saturating(r.estimatedGraphBytes, bytes);
    if (maxGraphBytes !== undefined && total > maxGraphBytes) return false;
    r.estimatedGraphBytes = total; return true;
  };
  const edgeBytes = (e: GraphEdge<I, O>): bigint => {
    let bytes = saturating(EDGE_BYTES, checkBytes(e.stateChecks));
    bytes = saturating(bytes, checkBytes(e.transitionChecks));
    bytes = saturating(bytes, BigInt(e.outputs.length) * OUTPUT_SLOT_BYTES);
    for (const label of e.annotations) bytes = saturating(bytes, LABEL_BYTES + utf8(label));
    return bytes;
  };
  const setTarget = (edge: GraphEdgeID, target: EdgeTarget): void => {
    if (pendingEdge === edge) pendingEdge = null;
    if (r.edgesRetained && edge < r.edges.length) r.edges[edge].target = target;
  };
  const reject = (edge: GraphEdgeID, reason: string): void => {
    setTarget(edge, { kind: 'rejected', reason }); r.rejectedSuccessors = saturating(r.rejectedSuccessors, 1n);
    emit(() => ({ kind: 'successorRejected', edge, reason }));
  };
  const finishNode = (id: GraphNodeID, status: NodeExpansion): void => {
    r.nodes[id].expansion = status; activeNode = null;
    // The sink receives a detached status; the node keeps its own object.
    emit(() => ({ kind: 'expansionFinished', node: id, status: { ...status } }));
  };
  const recordFinding = (violations: readonly Violation[], edge: GraphEdgeID | null, earlier: boolean, makeWitness: () => I[]): void => {
    const index = r.findingCount;
    r.findingCount = saturating(r.findingCount, 1n);
    for (const v of violations) {
      const known = r.failedProperties.find(p => p.phase === v.phase && p.id === v.check.id);
      if (known) known.count = saturating(known.count, 1n);
      else r.failedProperties.push({ phase: v.phase, id: v.check.id, count: 1n, firstFinding: index });
    }
    if (r.findings.length < maxFindings) r.findings.push({ index, witness: makeWitness(), violations: copyViolations(violations), edge, requiresContinuedEvidence: earlier });
    emit(() => ({ kind: 'finding', index, edge, violations: copyViolations(violations) }));
  };
  const completeLayer = (): void => { r.completedLayers++; const depth = currentLayer; emit(() => ({ kind: 'layerCompleted', depth })); };
  /** Resolves interrupted state on every exit path, including errors. */
  const finalize = (): void => {
    if (activeNode !== null && activeNode < r.nodes.length && r.nodes[activeNode].expansion.kind === 'unexpanded') {
      r.nodes[activeNode].expansion = { kind: 'interrupted', reason: r.termination }; activeNode = null;
    }
    // An edge evaluated when the search ended (stop-at-first failure, error)
    // is a rejected successor whether or not its record was retained.
    if (pendingEdge !== null) { setTarget(pendingEdge, { kind: 'rejected', reason: r.termination }); r.rejectedSuccessors = saturating(r.rejectedSuccessors, 1n); }
    r.frontierSize = r.nodes.reduce((n, node) => n + (node.expansion.kind === 'complete' ? 0 : 1), 0);
    r.coverage = r.termination === 'graph_exhausted' || r.termination === 'depth_bound'
      ? (!r.depthCutoff && !r.failurePruned ? { kind: 'exhaustive' } : { kind: 'bounded', depthCutoff: r.depthCutoff, failurePruned: r.failurePruned })
      : { kind: 'incomplete', reason: r.termination };
  };
  type Representative = { edge: GraphEdgeID; state: S; disposition: Disposition; checks: Check[]; projection: Uint8Array };

  /** Evaluates and resolves one edge. False when the search ends. */
  const evaluate = (cursor: number, node: GraphNode<S, I>, input: I, representatives: Map<string, Representative[]>): boolean => {
    // Retain the enumerated input before reducer callbacks can mutate an external alias.
    const retainedInput = copyInput(m, input);
    const t = step(m, node.state, retainedInput);
    // Detach the successor, edge and graph payload before checks/observers can
    // mutate an external alias. Copy failures surface only when used.
    const successor = capture(() => copyState(m, t.state));
    const checkState = capture(() => copyState(m, t.state));
    const edge = capture(() => ({ before: copyState(m, node.state), input: copyInput(m, retainedInput), transition: copyTransition(m, t) }));
    const consumers = captureEdgeConsumers(m, observer);
    const edgeValue = () => get(edge);
    const edgePayload = needsEdgeRecord ? capture(() => ({ input: copyInput(m, retainedInput), outputs: copyOutputs(m, t.outputs) })) : null;
    r.transitions++;
    r.maxDepthReached = Math.max(r.maxDepthReached, node.depth + 1);
    const wantHistory = observer?.needsHistory ? call('observer needs_history', () => { const v = observer.needsHistory!(); if (typeof v !== 'boolean') throw new TypeError('needsHistory must return boolean'); return v; }) : false;
    const history = wantHistory ? witness(cursor, retainedInput) : [];
    const checked = searchChecks(m, t.state, node.state, retainedInput, t, observer, history, checkStorage, () => edgeValue().input, () => get(checkState), () => edgeValue().transition, () => edgeValue().before, consumers);
    r.skippedChecks = addSkippedChecks(r.skippedChecks, checked.skipped);
    const bad = checked.violations;
    const batch = checkStorage.slice(), stateChecks = batch.slice(0, checked.stateCount), transitionChecks = batch.slice(checked.stateCount);
    const labels = annotateEdge ? annotations(node.state, retainedInput, successor, t) : [];
    if (nextEdge === Number.MAX_SAFE_INTEGER) throw new ModelError('graph edge IDs exceed safe Number range');
    const edgeID = nextEdge++; pendingEdge = edgeID;
    let retained = false;
    if (edgePayload) {
      const payload = get(edgePayload);
      const record: GraphEdge<I, O> = { id: edgeID, source: cursor, input: payload.input, disposition: disposition(t.disposition.kind, t.disposition.reason), outputs: payload.outputs, stateChecks, transitionChecks, annotations: labels, target: { kind: 'pending' } };
      if (r.edgesRetained) {
        if (maxRetainedEdges !== undefined && r.edges.length >= maxRetainedEdges) r.edgesRetained = false;
        else if (!chargeGraph(edgeBytes(record))) r.edgesRetained = false;
        else { r.edges.push(record); retained = true; }
      }
      emit(() => ({ kind: 'edgeEvaluated', edge: detached(record) }));
    }
    if (bad.length) {
      recordFinding(bad, edgeID, node.pathFailed, () => witness(cursor, retainedInput));
      if (!collect) { r.termination = 'failure_found'; return false; }
    }
    if (retainEdges && !retained) {
      // The graph can no longer be retained completely.
      r.termination = 'graph_retention_limit'; reject(edgeID, 'graph_retention_limit'); return false;
    }
    if (stopped()) { reject(edgeID, r.termination); return false; }
    const retainedSuccessor = get(successor);
    if (reduction) {
      const label = call('reduction eligible_class', () => {
        const v = reduction.eligibleClass(copyState(m, node.state), copyInput(m, retainedInput));
        if (v !== null && typeof v !== 'string') throw new TypeError('eligibleClass must return string or null');
        return v;
      });
      if (stopped()) { reject(edgeID, r.termination); return false; }
      if (label !== null) {
        const projection = call('reduction projection', () => {
          const observed: Transition<S, O> = { state: copyState(m, retainedSuccessor), outputs: copyOutputs(m, t.outputs), disposition: disposition(t.disposition.kind, t.disposition.reason) };
          const v = reduction.projection(copyInput(m, retainedInput), observed, [...labels]);
          if (!(v instanceof Uint8Array)) throw new TypeError('projection must return Uint8Array');
          return Uint8Array.from(v);
        });
        if (stopped()) { reject(edgeID, r.termination); return false; }
        const rejected: RejectedEquivalence[] = [];
        let reducedTo: GraphEdgeID | null = null;
        for (const rep of representatives.get(label) ?? []) {
          let reason: string | null;
          if (rep.disposition.kind !== t.disposition.kind || rep.disposition.reason !== t.disposition.reason) reason = 'disposition_differs';
          else if (!sameChecks(rep.checks, batch)) reason = 'checks_differ';
          else if (!bytesEqual(rep.projection, projection)) reason = 'projection_differs';
          else {
            if (stopped()) { reject(edgeID, r.termination); return false; }
            const same = sameState(rep.state, retainedSuccessor);
            if (stopped()) { reject(edgeID, r.termination); return false; }
            reason = same ? null : 'successor_differs';
          }
          if (reason !== null) rejected.push({ representative: rep.edge, reason });
          else { reducedTo = rep.edge; break; }
        }
        r.reductionDecisions.push({ edge: edgeID, source: cursor, equivalenceClass: label, reducedTo, rejected });
        if (reducedTo !== null) {
          const representative = reducedTo;
          setTarget(edgeID, { kind: 'reduced', edge: representative }); r.reducedEdges = saturating(r.reducedEdges, 1n);
          emit(() => ({ kind: 'edgeReduced', edge: edgeID, representative }));
          return true;
        }
        const reps = representatives.get(label) ?? [];
        reps.push({ edge: edgeID, state: copyState(m, retainedSuccessor), disposition: disposition(t.disposition.kind, t.disposition.reason), checks: batch, projection });
        representatives.set(label, reps);
      }
    }
    const key = hash(retainedSuccessor);
    if (stopped()) { reject(edgeID, r.termination); return false; }
    const bucket = visited.get(key) ?? [];
    let duplicate: number | null = null;
    // Rust collision chains visit the most recently admitted state first.
    for (const index of bucket) {
      if (stopped()) { reject(edgeID, r.termination); return false; }
      const same = sameState(r.nodes[index].state, retainedSuccessor);
      if (stopped()) { reject(edgeID, r.termination); return false; }
      if (same) { duplicate = index; break; }
    }
    if (duplicate !== null) {
      const existing = duplicate;
      setTarget(edgeID, { kind: 'duplicate', node: existing }); r.duplicateEdges = saturating(r.duplicateEdges, 1n); r.graphEdges = saturating(r.graphEdges, 1n);
      emit(() => ({ kind: 'duplicateEdge', edge: edgeID, existing }));
      return true;
    }
    if (bad.length && !expandFailing) { r.failurePruned = true; reject(edgeID, 'failing_successor'); return true; }
    if (r.nodes.length === maxStates) { r.termination = 'state_limit'; reject(edgeID, 'state_limit'); return false; }
    const bytes = estimate(retainedSuccessor);
    if (stopped()) { reject(edgeID, r.termination); return false; }
    const sum = r.estimatedRetainedStateBytes === null || bytes === null ? null : r.estimatedRetainedStateBytes + bytes;
    const total = sum !== null && sum > U64_MAX ? null : sum;
    if (cap !== undefined) {
      if (bytes === null) throw new ModelError('state byte budget requires estimated_state_bytes');
      if (total === null || total > cap) { r.termination = 'estimated_state_byte_limit'; reject(edgeID, 'estimated_state_byte_limit'); return false; }
    }
    if (!chargeGraph(saturating(NODE_BYTES, checkBytes(stateChecks)))) { r.termination = 'graph_retention_limit'; reject(edgeID, 'graph_retention_limit'); return false; }
    const ownedInput = copyInput(m, retainedInput);
    const id = r.nodes.length;
    bucket.unshift(id);
    visited.set(key, bucket);
    r.nodes.push({ id, state: retainedSuccessor, parent: cursor, input: ownedInput, admittingEdge: edgeID, depth: node.depth + 1, checks: stateChecks, pathFailed: node.pathFailed || bad.length > 0, expansion: UNEXPANDED });
    r.states = r.nodes.length; r.estimatedRetainedStateBytes = total; r.graphEdges = saturating(r.graphEdges, 1n);
    setTarget(edgeID, { kind: 'admitted', node: id });
    emit(() => ({ kind: 'successorAdmitted', edge: edgeID, node: id }));
    return true;
  };
  const sameState = (admitted: S, candidate: S): boolean => call('state equality', () => {
    const v = m.equalStates(copyState(m, admitted), copyState(m, candidate));
    if (typeof v !== 'boolean') throw new TypeError('equalStates must return boolean');
    return v;
  });
  const annotations = (before: S, input: I, successor: Captured<S>, t: Transition<S, O>): string[] => {
    const snapshot: Transition<S, O> = { state: copyState(m, get(successor)), outputs: copyOutputs(m, t.outputs), disposition: disposition(t.disposition.kind, t.disposition.reason) };
    return call('annotate edge', () => {
      const labels = annotateEdge!(copyState(m, before), copyInput(m, input), snapshot);
      if (!Array.isArray(labels) || labels.some(l => typeof l !== 'string')) throw new TypeError('annotateEdge must return an array of strings');
      return [...labels];
    });
  };
  /** Expands one dequeued node. False when the search ends. */
  const expand = (cursor: number, node: GraphNode<S, I>): boolean => {
    if (stopped()) return false;
    activeNode = cursor;
    emit(() => ({ kind: 'expansionStarted', node: cursor }));
    const iterator = call('enumerate inputs', () => m.inputs(copyState(m, node.state))[Symbol.iterator]());
    if (stopped()) return false;
    const next = () => call('enumerate inputs', () => iterator.next());
    if (node.depth === maxDepth) {
      const item = next();
      if (stopped()) return false;
      if (!item.done) r.depthCutoff = true;
      finishNode(cursor, item.done ? COMPLETE : { kind: 'depthFrontier' });
      return true;
    }
    const representatives = new Map<string, Representative[]>();
    for (;;) {
      if (stopped()) return false;
      const item = next();
      if (stopped()) return false;
      if (item.done) break;
      if (r.transitions === maxTransitions) { r.termination = 'transition_limit'; return false; }
      if (!evaluate(cursor, node, item.value, representatives)) return false;
    }
    finishNode(cursor, COMPLETE);
    return true;
  };
  const execute = (): void => {
    if (stopped()) return;
    const initial = copyState(m, call('initial_state', () => m.initialState()));
    // Checks can invoke application code with aliases to the returned value.
    const initialNode = capture(() => copyState(m, initial));
    const initialCheck = capture(() => copyState(m, initial));
    r.states = 1;
    const checked = searchChecks(m, initial, undefined, undefined, undefined, observer, [], checkStorage, undefined, () => get(initialCheck));
    r.skippedChecks = addSkippedChecks(r.skippedChecks, checked.skipped);
    const initialChecks = checkStorage.slice(), initialBad = checked.violations;
    if (initialBad.length) {
      recordFinding(initialBad, null, false, () => []);
      if (!collect) { r.termination = 'failure_found'; return; }
    }
    if (stopped()) return;
    const retainedInitial = get(initialNode);
    const initialBytes = estimate(retainedInitial);
    r.estimatedRetainedStateBytes = initialBytes;
    if (stopped()) return;
    if (cap !== undefined) {
      if (initialBytes === null) throw new ModelError('state byte budget requires estimated_state_bytes');
      if (initialBytes > cap) { r.states = 0; r.estimatedRetainedStateBytes = 0n; r.termination = 'estimated_state_byte_limit'; return; }
    }
    const initialHash = hash(retainedInitial);
    if (stopped()) return;
    if (!chargeGraph(saturating(NODE_BYTES, checkBytes(initialChecks)))) { r.states = 0; r.estimatedRetainedStateBytes = 0n; r.termination = 'graph_retention_limit'; return; }
    r.nodes.push({ id: 0, state: retainedInitial, parent: null, input: null, admittingEdge: null, depth: 0, checks: initialChecks, pathFailed: initialBad.length > 0, expansion: UNEXPANDED });
    visited.set(initialHash, [0]);
    emit(() => ({ kind: 'rootAdmitted', node: 0 }));
    if (initialBad.length && !expandFailing) { r.failurePruned = true; finishNode(0, { kind: 'failurePruned' }); }
    for (let cursor = 0; cursor < r.nodes.length; cursor++) {
      const node = r.nodes[cursor];
      if (node.depth > currentLayer) { completeLayer(); currentLayer = node.depth; }
      if (node.expansion.kind === 'failurePruned') continue;
      if (!expand(cursor, node)) return;
    }
    completeLayer();
    if (r.depthCutoff) r.termination = 'depth_bound';
  };
  try { execute(); }
  catch (error) { r.termination = 'model_error'; finalize(); throw new GraphSearchError<S, I, O>(error, r); }
  finalize();
  try { const termination = r.termination; emit(() => ({ kind: 'searchFinished', termination })); }
  catch (error) { r.termination = 'model_error'; finalize(); throw new GraphSearchError<S, I, O>(error, r); }
  return r;
}
