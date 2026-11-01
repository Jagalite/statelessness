# graph-search-v1 (candidate extension)

Status: experimental candidate extension implementing migration proposals S1
(admitted graph and complete search reporting) and S2 (failure collection and
explicit result semantics). It is implemented in the Rust, Swift, Python, Go and
TypeScript engines; each passes the shared fixtures and its own
default-options/bfs-controls equivalence test. It is not part of the four
portable profiles, and no cross-implementation runner qualification exists. `enumerateStates` and
`bfs-controls-v1` are unchanged; this contract is additive and opt-in.

API: `exploreGraph(model, config:, limits:, options:) -> GraphSearchReport`.
Errors after search start throw `GraphSearchError { error, report }`.

## Relationship to bfs-controls-v1

**GRAPH-001.** With default `GraphSearchOptions` (stop at first failure, edges
retained, no reduction, no annotation), `exploreGraph` MUST execute exactly the
bfs-controls-v1 model callback sequence (initial, checks, observers, estimates,
hashes, iterator pulls, steps, equality comparisons, polls) and report the same
termination, `states`, `transitions`, `max_depth_reached`, `skipped_checks`,
retained-state estimate and witness. Graph capture MAY add detaching
`clone_input`/`clone_output` calls; these are cooperative copy hooks and are not
part of the observable callback log. The Swift test replays the entire
`conformance/bfs-controls-v1.json` corpus through both kernels and compares the
callback logs and counters exactly.

## Graph identity and evidence

**GRAPH-002.** Node IDs are run-local, dense, and equal to admission order (the
root is 0). Edge IDs are dense in evaluation order and count every executed
edge, including duplicate, reduced and rejected edges. While `edgesRetained`,
`edges[k].id == k`. IDs are not stable across runs with different bounds.

**GRAPH-003.** A node records its state, parent node, admitting input and edge,
depth, the check batch that admitted it (initial checks for the root; the
admitting edge's state checks otherwise), `pathFailed`, and `expansion`:

| Expansion | Meaning |
| --- | --- |
| `complete` | Every enumerated input was evaluated and its successor resolved (admitted, duplicate, reduced, or rejected as `failing_successor`); or at max_depth the domain was empty. |
| `depthFrontier` | At max_depth with at least one unexamined input. |
| `failurePruned` | Only the root: a failing initial state under `collect(expandFailingSuccessors: false)`. It is never dequeued for expansion and receives no `expansionStarted`. |
| `interrupted(reason)` | Expansion started and the search ended first. |
| `unexpanded` | Still queued when the search ended. |

A node whose expansion was interrupted MUST NOT appear `complete`.

**GRAPH-004.** An edge records source, input, disposition, ordered outputs,
ordered state checks, ordered transition checks, user annotations and target:
`admitted(node)`, `duplicate(node)`, `reduced(edge)` (reduction-v1), or
`rejected(reason)`. An evaluated successor that was not admitted is never
reported as an admitted node. Rejection reasons are `state_limit`,
`estimated_state_byte_limit`, `graph_retention_limit`, `failing_successor`, or
the control stop reason (`cancelled`, `deadline`, `failure_found`,
`model_error`). `pending` appears only inside `edgeEvaluated` events. Duplicate
and capped edges keep their checks, so failure evidence on those edges survives.

## Events

**GRAPH-005.** The optional synchronous sink receives detached copies, in this
order: `rootAdmitted`; per dequeued node `expansionStarted`; per edge
`edgeEvaluated`, then `finding` when its batch failed, then exactly one of
`successorAdmitted`, `duplicateEdge`, `edgeReduced` or `successorRejected`
(absent only when the search ends on that edge for a property failure under
stop-at-first or an error); `expansionFinished(node, status)` for nodes that
finish (interrupted nodes receive none); `layerCompleted(d)` for the previous
layer `d` when the first node of a deeper layer is dequeued, before that node's
control poll and `expansionStarted`, and `layerCompleted(d)` for the deepest
dequeued layer when the queue exhausts normally (never after a cap, stop or
error); finally `searchFinished(termination)` on non-error returns. An
initial-state finding precedes `rootAdmitted`. Pre-start stops emit only
`searchFinished`. Sink errors abort with `GraphSearchError`.

## Fixture spellings

Fixture event logs spell events as `root:N`, `expand:N`, `edge:ID:SOURCE:INPUT`
(SOURCE is the source node ID), `finding:INDEX:EDGE:phase/id[,phase/id]` (EDGE is
`-` for an initial finding), `admit:EDGE:NODE`, `dup:EDGE:NODE`,
`reduced:EDGE:REP`, `reject:EDGE:REASON`, `finish:NODE:STATUS`, `layer:DEPTH` and
`end:TERMINATION`. STATUS is `complete`, `depthFrontier` or `failurePruned`;
interrupted and unexpanded nodes receive no `finish` event, so those statuses
have no event spelling. Targets are `admitted:N`, `duplicate:N`, `reduced:E` and
`rejected:REASON`. Coverage is `exhaustive`, `bounded:` followed by
`depth_cutoff`, `failure_pruned` or both joined by `+`, or `incomplete:REASON`.

## Report counters

`transitions` counts evaluated edges. `graphEdges` counts edges between
admitted nodes (admitted plus duplicate targets). `duplicateEdges`,
`reducedEdges`, and `rejectedSuccessors` count those outcomes; an edge still
unresolved when the search ends (stop-at-first failure, error) is a rejected
successor with the termination as its reason, whether or not edges are retained. `frontierSize`
counts admitted nodes whose expansion is not `complete`. `completedLayers`
counts emitted `layerCompleted` events. `states` keeps its bfs-v1 meaning.

**GRAPH-006.** `estimatedGraphBytes` is a saturating engine estimate of node and
retained-edge bookkeeping (struct strides, check text, outputs, labels). It is
separate from `estimatedRetainedStateBytes`, which covers model state
representation only. Neither is process RSS.

**GRAPH-006a.** `max_states` is the maximum admitted count: a new successor is
rejected with `state_limit` when the admitted count already equals it.

**GRAPH-007.** Retention: `retainEdges: false` streams edges through events
only. `maxRetainedEdges` and `maxGraphBytes` are caps; exceeding either ends the
search with `graph_retention_limit` and `incomplete` coverage. Retention
truncation never silently becomes exhaustive coverage. Negative caps are
configuration errors.

## Coverage and termination

**GRAPH-008.** `termination` uses the bfs-v1 vocabulary plus
`graph_retention_limit` and `model_error` (errors only). Coverage:

| Coverage | Condition |
| --- | --- |
| `exhaustive` | `graph_exhausted`, no depth cutoff, nothing failure-pruned. |
| `bounded(depthCutoff, failurePruned)` | Finished (`graph_exhausted`/`depth_bound`) but within the declared depth and/or with failing successors unexpanded. Admissible only as bounded coverage. |
| `incomplete(reason)` | Any other termination: property failure under stop-at-first, state/transition/byte/retention caps, cancellation, deadline, errors. |

Reaching max_depth at a node whose input domain is empty is not a cutoff.

## Failure collection (S2)

**GRAPH-009.** `stopAtFirst` (default) is bfs-v1: the first failing batch ends
the search with `failure_found`. `collect(maxRetainedFindings,
expandFailingSuccessors)` records each failing batch as a finding and continues:

- Violation phases are `initial_state` (initial checks), `state` (successor
  state checks) and `transition` (transition checks), as in bfs-v1.
- A finding has a run-local index, a deterministic witness (BFS root path plus
  the edge input; empty for the initial state), every failed check of its batch
  in order (state checks before transition checks, so two violations on one
  edge stay one finding), its edge ID (nil for initial), and
  `requiresContinuedEvidence`. The latter is true exactly when the source node's
  `pathFailed` is true, i.e. some failed batch (the initial checks, or the state
  or transition checks of an admitting edge) occurs on the root path to the
  edge's source. A failing duplicate edge does not mark the node it targets.
- Failing edges still resolve duplicates. With `expandFailingSuccessors`, new
  failing successors are admitted and expanded. Without it they are rejected as
  `failing_successor` and coverage is failure-pruned. A failing root is then
  admitted but `failurePruned`.
- `maxRetainedFindings` bounds retained witnesses only (zero is allowed).
  `findingCount` and `failedProperties` (stable `(phase, id)` identity, count,
  first finding index, in first-occurrence order) are never truncated, and
  `findingsTruncated` is visible. Collection never changes failed checks to
  passing ones.
- A later cap, cancellation or deadline keeps the findings collected so far and
  makes coverage `incomplete`. A checker, model or sink error throws
  `GraphSearchError` whose report retains earlier findings, termination
  `model_error`, the interrupted node, and coverage `incomplete(model_error)`.
  The batch that errored has no edge record.

Illiquid's configuration `failureLimit` bounds modeled failure scenarios; it is
not this retention limit.

## Fixtures

`conformance/graph-search-v1.json` contains hand-derived diamond, self-loop,
collision, cap, depth and collection cases with exact event logs and targets.
`GraphSearchTests` additionally covers cancellation timing, detached payloads,
annotations, retention, sink errors and checker errors after partial evidence.
