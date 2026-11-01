# progress-analysis-v1 (candidate, Swift-only extension)

Status: experimental Swift-only extension implementing migration proposal S3.
The library owns graph algorithms; applications own every predicate (terminal,
intentional wait, declared external wait, transaction, rank, fair edge). General
liveness remains deferred: fair-path reachability and rank checks over a bounded
graph are evidence, not a proof that every scheduler execution completes.

API: `ProgressGraph`, `analyzeProgress(graph, kind:, rank:)`,
`stronglyConnectedComponents(nodeCount:edges:)`,
`reverseReachable(nodeCount:edges:targets:include:)`.

## Graph and completeness

**PROGRESS-001.** A `ProgressGraph` has dense node IDs, directed edges with a
fairness flag, and one `measured` flag per node. Out-of-range endpoints are
configuration errors. Built from a `graph-search-v1` report (edges must be
retained), admitted and duplicate edges keep their targets, reduced edges take
their representative's target, and a node is measured only when its expansion
is `complete` and none of its edges was rejected. Depth frontiers, interrupted
and unexpanded nodes, and nodes with rejected successors are unmeasured: they
are never evidence of deadlock or of progress.

## Algorithms

**PROGRESS-002.** SCCs use Tarjan's algorithm with an explicit work stack (no
recursion). Members are sorted ascending and components ordered by smallest
member. Reverse reachability is breadth-first over the reversed edges accepted by
the filter. Both are linear in nodes plus edges.

## Classification

**PROGRESS-003.** Each node is classified in this order: unmeasured →
`unmeasuredCutoff`; kind `terminal` → `terminal`; `externalWait` →
`declaredExternalWait`; `intentionalWait` → `stableIntentionalWait`;
`transaction` with at least one fair outgoing edge → `transactionProgress`;
otherwise `stuck`. This preserves Illiquid's existing analyzer classifications
with depth cutoff generalized to unmeasured.

**PROGRESS-004.** Settled nodes are terminal, intentional-wait and
external-wait nodes. `settledTransactionStates` counts transaction-progress
nodes with a fair path to a settled node; the others are listed in
`unsettledTransactionStates`. An exit reachable only through unfair edges does
not settle a transaction.

**PROGRESS-005.** A closed nonterminal component is an SCC that contains a
`transactionProgress` or `stuck` node, has no edge leaving the component (fair
or unfair), and has no fair edge whose target rank is strictly lower than its
source rank. A component meeting those conditions that contains an unmeasured
node is not reported closed; it increments `unmeasuredComponents`. Ranks default
to zero. `graphComplete` is true only when every node is measured. Absent
findings on an incomplete graph are not evidence.

## Fixtures

`conformance/progress-analysis-v1.json` holds hand-authored graphs independent
of playback: settling transaction, intentional and external waits, closed
nonterminal cycle, unfair-only exit, fair rank decrease, depth cutoff, and
partial expansion, with expected classes and components. Tests also cover a
200 000-node cycle (explicit-stack SCC) and graphs built from search reports.
