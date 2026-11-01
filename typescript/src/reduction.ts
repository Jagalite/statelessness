/** Experimental extension `reduction-v1` (S4); see spec/reduction-v1.md.
 * Reduction collapses an evaluated edge into an earlier representative edge of
 * the same eligible class from the same node. It never skips executing or
 * checking an edge. Collapse requires equal disposition, every check, the
 * application projection, and full successor equality; a digest is not used.
 * `compareEquivalence` (research-equivalence-v1) evidence does not authorize reduction. */
import { type EnumerateModel, type Model, type Transition, type Check, type Violation, ModelError, disposition } from './core.js';
import { withCheckSink, appendStateChecks, appendTransitionChecks } from './checks.js';
import { runControl, type RunLimits } from './search_support.js';
import { type GraphSearchReport, type GraphEdge, type GraphEdgeID, type GraphNodeID, resolvedTarget, sameCoverage } from './graph_search.js';
import { ProgressGraph, analyzeProgress, type ProgressKind, type ProgressReport } from './progress.js';

export interface EquivalenceReduction<S, I, O> {
  /** The eligible equivalence class, or null when the edge is not a candidate. */
  eligibleClass(state: S, input: I): string | null;
  /** Deterministic bytes for ordered effects and progress annotations that
   * equivalence must preserve. Receives detached copies and edge labels. */
  projection(input: I, transition: Transition<S, O>, labels: readonly string[]): Uint8Array;
}
export interface RejectedEquivalence {
  readonly representative: GraphEdgeID;
  /** disposition_differs, checks_differ, projection_differs or successor_differs. */
  readonly reason: string;
}
export interface ReductionDecision {
  readonly edge: GraphEdgeID;
  readonly source: GraphNodeID;
  readonly equivalenceClass: string;
  /** null: the edge became a representative of its class. */
  readonly reducedTo: GraphEdgeID | null;
  /** Representatives compared before the decision, in comparison order. */
  readonly rejected: readonly RejectedEquivalence[];
}
function call<T>(stage: string, callback: () => T): T {
  try { return callback(); }
  catch (error) { throw new ModelError(`${stage}: ${error instanceof Error ? error.message : String(error)}`); }
}
const copyState = <S, I, O>(m: Model<S, I, O>, s: S): S => call('snapshot', () => m.cloneState(s));
const copyInput = <S, I, O>(m: Model<S, I, O>, i: I): I => call('snapshot', () => m.cloneInput(i));
function copyTransition<S, I, O>(m: Model<S, I, O>, t: Transition<S, O>): Transition<S, O> {
  return call('snapshot', () => ({ state: m.cloneState(t.state), outputs: Array.from(t.outputs, o => m.cloneOutput(o)), disposition: disposition(t.disposition.kind, t.disposition.reason) }));
}
function perform<S, I, O>(m: Model<S, I, O>, s: S, i: I): Transition<S, O> {
  return copyTransition(m, call('transition', () => m.step(copyState(m, s), copyInput(m, i))));
}
const boolean = (value: unknown, name: string): boolean => { if (typeof value !== 'boolean') throw new TypeError(`${name} must return boolean`); return value; };
const bytes = (value: unknown, name: string): Uint8Array => { if (!(value instanceof Uint8Array)) throw new TypeError(`${name} must return Uint8Array`); return Uint8Array.from(value); };
const compareBytes = (a: Uint8Array, b: Uint8Array): number => {
  for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i] - b[i];
  return a.length - b.length;
};
const sameChecks = (a: readonly Check[], b: readonly Check[]) => a.length === b.length && a.every((c, i) => c.id === b[i].id && c.status === b[i].status && c.details === b[i].details);

// MARK: Diamond validation
export interface DiamondWitness<I> {
  readonly node: GraphNodeID;
  readonly first: I;
  readonly second: I;
  /** state_differs or observations_differ. */
  readonly reason: string;
}
export interface DiamondReport<I> {
  /** search_complete, or the RunLimits stop reason. */
  termination: string;
  checkedNodes: number;
  candidatePairs: bigint;
  /** Pairs where each input stayed enabled after the other. */
  validated: bigint;
  nonCommuting: DiamondWitness<I>[];
}
/** For each completely expanded node, executes both orders of every pair of
 * candidate inputs that remain enabled after each other, and compares the
 * final states by model equality and the two steps' observations: the set of
 * failed check (phase, ID) pairs and the multiset of projections. This is
 * evidence about independence, never permission to prune. */
export function validateDiamonds<S, I, O>(m: EnumerateModel<S, I, O>, report: GraphSearchReport<S, I, O>, candidate: (state: S, input: I) => boolean, equalInputs: (left: I, right: I) => boolean,
  projection: (input: I, transition: Transition<S, O>) => Uint8Array, limits: RunLimits = {}): DiamondReport<I> {
  const stop = runControl(limits);
  const out: DiamondReport<I> = { termination: 'search_complete', checkedNodes: 0, candidatePairs: 0n, validated: 0n, nonCommuting: [] };
  const enumerate = (state: S): I[] => {
    const iterator = call('enumerate inputs', () => m.inputs(copyState(m, state))[Symbol.iterator]());
    const values: I[] = [];
    for (;;) { const item = call('enumerate inputs', () => iterator.next()); if (item.done) return values; values.push(copyInput(m, item.value)); }
  };
  const enabled = (input: I, state: S): boolean => enumerate(state).some(other => call('input_equal', () => boolean(equalInputs(copyInput(m, other), copyInput(m, input)), 'equalInputs')));
  const observe = (before: S, input: I, t: Transition<S, O>): { failed: string[]; projection: Uint8Array } => {
    const stateChecks: Check[] = [], edgeChecks: Check[] = [];
    call('state check', () => withCheckSink(stateChecks, sink => appendStateChecks(m, t.state, sink)));
    call('transition check', () => withCheckSink(edgeChecks, sink => appendTransitionChecks(m, before, input, t, sink)));
    const violations: Violation[] = [...stateChecks.map(check => ({ phase: 'state' as const, check })), ...edgeChecks.map(check => ({ phase: 'transition' as const, check }))];
    const failed = violations.filter(v => v.check.status === 'failed').map(v => `${v.phase}\u0000${v.check.id}`);
    return { failed, projection: call('diamond projection', () => bytes(projection(copyInput(m, input), copyTransition(m, t)), 'projection')) };
  };
  const equalStates = (a: S, b: S) => call('state equality', () => boolean(m.equalStates(copyState(m, a), copyState(m, b)), 'equalStates'));
  for (const node of report.nodes) {
    if (node.expansion.kind !== 'complete') continue;
    let reason = stop();
    if (reason) { out.termination = reason; return out; }
    out.checkedNodes++;
    const candidates = enumerate(node.state).filter(input => call('diamond candidate', () => boolean(candidate(copyState(m, node.state), copyInput(m, input)), 'candidate')));
    for (let i = 0; i < candidates.length; i++) for (let j = i + 1; j < candidates.length; j++) {
      reason = stop();
      if (reason) { out.termination = reason; return out; }
      out.candidatePairs++;
      const a = candidates[i], b = candidates[j];
      const ta = perform(m, node.state, a), tb = perform(m, node.state, b);
      if (!enabled(b, ta.state) || !enabled(a, tb.state)) continue;
      const tab = perform(m, ta.state, b), tba = perform(m, tb.state, a);
      out.validated++;
      const same = equalStates(tab.state, tba.state);
      const left = [observe(node.state, a, ta), observe(ta.state, b, tab)], right = [observe(node.state, b, tb), observe(tb.state, a, tba)];
      const failures = (side: typeof left) => [...new Set(side.flatMap(o => o.failed))].sort();
      const sameFailures = JSON.stringify(failures(left)) === JSON.stringify(failures(right));
      const sorted = (side: typeof left) => side.map(o => o.projection).sort(compareBytes);
      const l = sorted(left), r = sorted(right);
      const sameProjections = l.length === r.length && l.every((p, k) => compareBytes(p, r[k]) === 0);
      if (!same || !sameFailures || !sameProjections) out.nonCommuting.push({ node: node.id, first: copyInput(m, a), second: copyInput(m, b), reason: same ? 'observations_differ' : 'state_differs' });
    }
  }
  return out;
}

// MARK: Reduced versus unreduced comparison
/** Application progress predicates (progress-analysis-v1) applied to both
 * graphs. Kind and rank take states so they agree across the two reports. */
export interface ReductionProgressPredicates<S, I, O> {
  fair(edge: GraphEdge<I, O>): boolean;
  kind(state: S): ProgressKind;
  rank?(state: S): bigint;
}
export interface ReductionComparison {
  /** Unreduced node IDs with no equal reduced node, and the reverse. */
  missingInReduced: GraphNodeID[];
  extraInReduced: GraphNodeID[];
  /** Unreduced edges whose (source, target, disposition, checks) class has no
   * reduced representative, and the reverse. */
  unrepresentedEdges: GraphEdgeID[];
  extraReducedEdges: GraphEdgeID[];
  findingsMatch: boolean;
  coverageMatch: boolean;
  /** null when no progress predicates were supplied: progress was not compared. */
  progressMatch: boolean | null;
  /** Unreduced nodes whose progress class, settlement or closed-component
   * membership differs from their reduced counterpart. */
  progressMismatches: GraphNodeID[];
}
/** Not compared progress (`progressMatch === null`) does not block
 * equivalence; it is simply absent evidence and is reported as such. */
export function reductionEquivalent(c: ReductionComparison): boolean {
  return !c.missingInReduced.length && !c.extraInReduced.length && !c.unrepresentedEdges.length && !c.extraReducedEdges.length && c.findingsMatch && c.coverageMatch && c.progressMatch !== false;
}
/** Compares reachable states by model equality, represented edge classes (raw
 * edge counts may differ), failed property counts, coverage and, when
 * predicates are supplied, progress results. Both reports must retain edges
 * and must not be incomplete. */
export function compareReduction<S, I, O>(m: EnumerateModel<S, I, O>, unreduced: GraphSearchReport<S, I, O>, reduced: GraphSearchReport<S, I, O>, progress?: ReductionProgressPredicates<S, I, O>): ReductionComparison {
  for (const report of [unreduced, reduced]) {
    if (!report.edgesRetained) throw new ModelError('reduction comparison requires retained edges');
    if (report.coverage.kind === 'incomplete') throw new ModelError('reduction comparison requires finished searches');
  }
  const hash = (s: S) => call('state hash', () => m.hashState ? m.hashState(copyState(m, s)) : 0);
  const equal = (a: S, b: S) => call('state equality', () => boolean(m.equalStates(copyState(m, a), copyState(m, b)), 'equalStates'));
  const buckets = new Map<string | number | bigint, number[]>();
  for (const node of reduced.nodes) { const key = hash(node.state); const bucket = buckets.get(key) ?? []; bucket.push(node.id); buckets.set(key, bucket); }
  const mapping: (number | null)[] = new Array(unreduced.nodes.length).fill(null), matched = new Set<number>();
  const out: ReductionComparison = { missingInReduced: [], extraInReduced: [], unrepresentedEdges: [], extraReducedEdges: [], findingsMatch: true, coverageMatch: sameCoverage(unreduced.coverage, reduced.coverage), progressMatch: null, progressMismatches: [] };
  for (const node of unreduced.nodes) {
    for (const other of buckets.get(hash(node.state)) ?? []) if (equal(node.state, reduced.nodes[other].state)) { mapping[node.id] = other; matched.add(other); break; }
    if (mapping[node.id] === null) out.missingInReduced.push(node.id);
  }
  out.extraInReduced = reduced.nodes.map(n => n.id).filter(id => !matched.has(id));
  const same = (a: GraphEdge<I, O>, b: GraphEdge<I, O>) => a.disposition.kind === b.disposition.kind && a.disposition.reason === b.disposition.reason && sameChecks(a.stateChecks, b.stateChecks) && sameChecks(a.transitionChecks, b.transitionChecks);
  const reducedEdges = reduced.edges.filter(e => e.target.kind !== 'reduced');
  // An edge resolved to a node that has no counterpart is never represented.
  const mapped = (report: GraphSearchReport<S, I, O>, edge: GraphEdge<I, O>, map: (n: number) => number | null): { known: boolean; node: number | null } => {
    const node = resolvedTarget(report, edge);
    if (node === null) return { known: true, node: null };
    const other = map(node);
    return other === null ? { known: false, node: null } : { known: true, node: other };
  };
  const reverse: (number | null)[] = new Array(reduced.nodes.length).fill(null);
  mapping.forEach((r, u) => { if (r !== null && r >= 0 && r < reverse.length) reverse[r] = u; });
  const at = (table: readonly (number | null)[], index: number): number | null => index >= 0 && index < table.length ? table[index] : null;
  for (const edge of unreduced.edges) {
    const target = mapped(unreduced, edge, n => at(mapping, n));
    const source = at(mapping, edge.source);
    if (source === null || !target.known) { out.unrepresentedEdges.push(edge.id); continue; }
    if (!reducedEdges.some(e => e.source === source && resolvedTarget(reduced, e) === target.node && same(e, edge))) out.unrepresentedEdges.push(edge.id);
  }
  for (const edge of reducedEdges) {
    const target = mapped(reduced, edge, n => at(reverse, n));
    const source = at(reverse, edge.source);
    const represented = target.known && source !== null && unreduced.edges.some(other => other.source === source && resolvedTarget(unreduced, other) === target.node && same(other, edge));
    if (!represented) out.extraReducedEdges.push(edge.id);
  }
  out.findingsMatch = unreduced.findingCount === reduced.findingCount && unreduced.failedProperties.length === reduced.failedProperties.length
    && unreduced.failedProperties.every((p, i) => { const o = reduced.failedProperties[i]; return p.phase === o.phase && p.id === o.id && p.count === o.count && p.firstFinding === o.firstFinding; });
  if (progress) {
    const analyze = (report: GraphSearchReport<S, I, O>): ProgressReport => analyzeProgress(ProgressGraph.fromReport(report, e => progress.fair(e)),
      n => progress.kind(copyState(m, report.nodes[n].state)), progress.rank ? n => progress.rank!(copyState(m, report.nodes[n].state)) : undefined);
    const u = analyze(unreduced), r = analyze(reduced);
    const inClosed = (report: ProgressReport, n: number) => report.closedNonterminalComponents.some(c => c.includes(n));
    const uUnsettled = new Set(u.unsettledTransactionStates), rUnsettled = new Set(r.unsettledTransactionStates);
    // Closed components must correspond member for member under the mapping.
    const key = (members: readonly number[]) => [...new Set(members)].sort((a, b) => a - b).join(',');
    const uClosed = new Set(u.closedNonterminalComponents.map(c => key(c.map(n => at(mapping, n)).filter((n): n is number => n !== null))));
    const rClosed = new Set(r.closedNonterminalComponents.map(c => key(c)));
    for (let n = 0; n < unreduced.nodes.length; n++) {
      const k = at(mapping, n);
      if (k === null) continue;
      if (u.classes[n] !== r.classes[k] || uUnsettled.has(n) !== rUnsettled.has(k) || inClosed(u, n) !== inClosed(r, k)) out.progressMismatches.push(n);
    }
    out.progressMatch = !out.progressMismatches.length && uClosed.size === rClosed.size && [...uClosed].every(c => rClosed.has(c)) && u.graphComplete === r.graphComplete;
  }
  return out;
}
