/** Experimental extension `progress-analysis-v1` (S3); see
 * spec/progress-analysis-v1.md. Applications supply node kinds, ranks and edge
 * fairness; this module owns only graph algorithms. Fair-path reachability and
 * rank checks are bounded evidence, not a proof that every scheduler execution
 * eventually completes. */
import { ModelError, ConfigError } from './errors.js';
import { U64_MAX } from './counts.js';
import { type GraphSearchReport, type GraphEdge, resolvedTarget } from './graph_search.js';

export interface ProgressEdge { readonly source: number; readonly target: number; readonly fair: boolean }
function call<T>(stage: string, callback: () => T): T {
  try { return callback(); }
  catch (error) { throw new ModelError(`${stage}: ${error instanceof Error ? error.message : String(error)}`); }
}
export class ProgressGraph {
  readonly nodeCount: number;
  readonly edges: readonly ProgressEdge[];
  /** False for a node whose outgoing edges are not all known: a depth frontier,
   * an interrupted or unexpanded node, or a node with a rejected successor. */
  readonly measured: readonly boolean[];
  constructor(nodeCount: number, edges: readonly ProgressEdge[], measured: readonly boolean[]) {
    if (!Number.isSafeInteger(nodeCount) || nodeCount < 0 || measured.length !== nodeCount) throw new ConfigError('progress graph needs one measured flag per node');
    const inRange = (n: number) => Number.isSafeInteger(n) && n >= 0 && n < nodeCount;
    for (const e of edges) if (!inRange(e.source) || !inRange(e.target)) throw new ConfigError('progress edge endpoint out of range');
    for (const flag of measured) if (typeof flag !== 'boolean') throw new ConfigError('measured flags must be boolean');
    for (const e of edges) if (typeof e.fair !== 'boolean') throw new ConfigError('progress edge fairness must be boolean');
    this.nodeCount = nodeCount;
    this.edges = Object.freeze(edges.map(e => Object.freeze({ source: e.source, target: e.target, fair: e.fair })));
    this.measured = Object.freeze([...measured]);
  }
  /** Admitted and duplicate edges keep their targets; reduced edges use their
   * representative's target. Requires retained edges. */
  static fromReport<S, I, O>(report: GraphSearchReport<S, I, O>, fair: (edge: GraphEdge<I, O>) => boolean): ProgressGraph {
    if (!report.edgesRetained) throw new ModelError('progress analysis requires retained edges');
    const measured = report.nodes.map(n => n.expansion.kind === 'complete'), edges: ProgressEdge[] = [];
    for (const edge of report.edges) {
      const target = resolvedTarget(report, edge);
      if (target === null) { measured[edge.source] = false; continue; }
      const isFair = call('progress fairness', () => { const v = fair(edge); if (typeof v !== 'boolean') throw new TypeError('fair must return boolean'); return v; });
      edges.push({ source: edge.source, target, fair: isFair });
    }
    return new ProgressGraph(report.nodes.length, edges, measured);
  }
}
export type ProgressKind = 'terminal' | 'intentionalWait' | 'externalWait' | 'transaction' | 'other';
export type ProgressClass = 'terminal' | 'stableIntentionalWait' | 'declaredExternalWait' | 'transactionProgress' | 'stuck' | 'unmeasuredCutoff';
export interface ProgressReport {
  readonly classes: readonly ProgressClass[];
  readonly terminalStates: number;
  readonly stableIntentionalWaitStates: number;
  readonly externalWaitStates: number;
  readonly unmeasuredStates: number;
  readonly transactionStates: number;
  readonly settledTransactionStates: number;
  /** Transaction nodes with no fair path to a settled node. */
  readonly unsettledTransactionStates: readonly number[];
  readonly stuckStates: readonly number[];
  /** Members sorted ascending; components sorted by first member. */
  readonly closedNonterminalComponents: readonly (readonly number[])[];
  /** Closed candidates excluded because a member is unmeasured. */
  readonly unmeasuredComponents: number;
  readonly maximumRank: bigint;
  /** Every node is measured; otherwise absence of a finding is not evidence. */
  readonly graphComplete: boolean;
}
const ascending = (a: number, b: number) => a - b;
/** Tarjan's algorithm with an explicit stack. Members are sorted ascending and
 * components are ordered by their smallest member. */
export function stronglyConnectedComponents(nodeCount: number, edges: readonly (readonly [number, number])[]): number[][] {
  const adjacency: number[][] = Array.from({ length: nodeCount }, () => []);
  for (const [source, target] of edges) adjacency[source].push(target);
  const index = new Array<number>(nodeCount).fill(-1), low = new Array<number>(nodeCount).fill(0), onStack = new Array<boolean>(nodeCount).fill(false);
  const stack: number[] = [], components: number[][] = [];
  const work: { node: number; next: number }[] = [];
  let counter = 0;
  for (let root = 0; root < nodeCount; root++) {
    if (index[root] >= 0) continue;
    work.push({ node: root, next: 0 });
    while (work.length) {
      const frame = work[work.length - 1], v = frame.node;
      if (frame.next === 0 && index[v] < 0) { index[v] = counter; low[v] = counter; counter++; stack.push(v); onStack[v] = true; }
      if (frame.next < adjacency[v].length) {
        const w = adjacency[v][frame.next]; frame.next++;
        if (index[w] < 0) work.push({ node: w, next: 0 }); else if (onStack[w]) low[v] = Math.min(low[v], index[w]);
        continue;
      }
      work.pop();
      if (work.length) { const parent = work[work.length - 1].node; low[parent] = Math.min(low[parent], low[v]); }
      if (low[v] === index[v]) {
        const component: number[] = [];
        for (;;) { const w = stack.pop()!; onStack[w] = false; component.push(w); if (w === v) break; }
        components.push(component.sort(ascending));
      }
    }
  }
  return components.sort((a, b) => a[0] - b[0]);
}
/** Nodes with a path to any target using edges accepted by `include`. */
export function reverseReachable(nodeCount: number, edges: readonly ProgressEdge[], targets: readonly number[], include: (edge: ProgressEdge) => boolean = () => true): boolean[] {
  const reverse: number[][] = Array.from({ length: nodeCount }, () => []);
  for (const e of edges) if (include(e)) reverse[e.target].push(e.source);
  const reached = new Array<boolean>(nodeCount).fill(false), queue: number[] = [];
  for (const t of targets) if (!reached[t]) { reached[t] = true; queue.push(t); }
  for (let head = 0; head < queue.length; head++) for (const u of reverse[queue[head]]) if (!reached[u]) { reached[u] = true; queue.push(u); }
  return reached;
}
const KINDS: readonly ProgressKind[] = ['terminal', 'intentionalWait', 'externalWait', 'transaction', 'other'];
/** Classification order: unmeasured, terminal, external wait, intentional wait,
 * transaction with a fair outgoing edge, otherwise stuck. A closed nonterminal
 * component contains a transaction or stuck node, has no exit edge, no fair
 * rank-decreasing edge, and no unmeasured member. */
export function analyzeProgress(graph: ProgressGraph, kind: (node: number) => ProgressKind, rank?: (node: number) => bigint): ProgressReport {
  const n = graph.nodeCount;
  const outgoing: ProgressEdge[][] = Array.from({ length: n }, () => []);
  for (const e of graph.edges) outgoing[e.source].push(e);
  const classes: ProgressClass[] = [], ranks: bigint[] = [];
  for (let v = 0; v < n; v++) {
    ranks.push(rank ? call('progress rank', () => { const r = rank(v); if (typeof r !== 'bigint' || r < 0n || r > U64_MAX) throw new TypeError('rank must return u64 bigint'); return r; }) : 0n);
    if (!graph.measured[v]) { classes.push('unmeasuredCutoff'); continue; }
    const k = call('progress kind', () => { const value = kind(v); if (!KINDS.includes(value)) throw new TypeError('unknown progress kind'); return value; });
    switch (k) {
      case 'terminal': classes.push('terminal'); break;
      case 'externalWait': classes.push('declaredExternalWait'); break;
      case 'intentionalWait': classes.push('stableIntentionalWait'); break;
      case 'transaction': classes.push(outgoing[v].some(e => e.fair) ? 'transactionProgress' : 'stuck'); break;
      default: classes.push('stuck');
    }
  }
  const nodes = Array.from({ length: n }, (_, v) => v);
  const settled = nodes.filter(v => classes[v] === 'terminal' || classes[v] === 'stableIntentionalWait' || classes[v] === 'declaredExternalWait');
  const canSettle = reverseReachable(n, graph.edges, settled, e => e.fair);
  const closed: number[][] = [];
  let unmeasuredComponents = 0;
  for (const component of stronglyConnectedComponents(n, graph.edges.map(e => [e.source, e.target] as const))) {
    if (!component.some(v => classes[v] === 'transactionProgress' || classes[v] === 'stuck')) continue;
    const members = new Set(component), exits = component.flatMap(v => outgoing[v]);
    if (exits.some(e => !members.has(e.target)) || exits.some(e => e.fair && ranks[e.target] < ranks[e.source])) continue;
    if (component.some(v => !graph.measured[v])) { unmeasuredComponents++; continue; }
    closed.push(component);
  }
  const transactions = nodes.filter(v => classes[v] === 'transactionProgress');
  const count = (c: ProgressClass) => classes.reduce((total, x) => total + (x === c ? 1 : 0), 0);
  return {
    classes, terminalStates: count('terminal'), stableIntentionalWaitStates: count('stableIntentionalWait'), externalWaitStates: count('declaredExternalWait'),
    unmeasuredStates: count('unmeasuredCutoff'), transactionStates: transactions.length, settledTransactionStates: transactions.filter(v => canSettle[v]).length,
    unsettledTransactionStates: transactions.filter(v => !canSettle[v]), stuckStates: nodes.filter(v => classes[v] === 'stuck'),
    closedNonterminalComponents: closed, unmeasuredComponents, maximumRank: ranks.reduce((a, b) => (b > a ? b : a), 0n), graphComplete: !graph.measured.includes(false),
  };
}
