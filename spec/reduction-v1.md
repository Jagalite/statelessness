# reduction-v1 (candidate, Swift-only extension)

Status: experimental Swift-only extension implementing migration proposal S4.
Scope is deliberately narrow: action-equivalence grouping of edges from the same
node and enabled-action diamond validation. No independence-based pruning (sleep
sets, persistent sets) is introduced. Evidence from `Research.compareEquivalence`
(research-equivalence-v1) is bounded research and does not authorize reduction.

API: `GraphSearchOptions.reduction: EquivalenceReduction`, report fields
`reducedEdges` and `reductionDecisions`, `validateDiamonds(...)`,
`compareReduction(...)`.

## Equivalence grouping

**REDUCE-001.** Reduction never skips executing or checking an edge: every
enumerated input is stepped, checked, observed, annotated and counted in
`transitions`. Only after its checks pass the control poll is an edge
considered. The application supplies `eligibleClass(state, input)` (nil means
not a candidate) and a deterministic `projection(input, transition, labels)` of
ordered effects and progress annotations that equivalence must preserve.

**REDUCE-002.** An eligible edge is collapsed into the first earlier
representative of the same class from the same node whose disposition is
equal, whose complete ordered check batch is exactly equal, whose projection
bytes are equal, and whose successor is equal by the model's `equalStates`
(never a hash or digest). It is then `reduced(representative)` and skips
duplicate resolution and admission, because its successor is the
representative's. Otherwise the edge becomes a representative and is resolved
normally. Comparisons stop at the first mismatch in the order disposition,
checks, projection, successor; equality callbacks are polled like duplicate
resolution.

**REDUCE-003.** Every eligible edge yields a `ReductionDecision` with its class,
`reducedTo` (nil for a new representative) and every compared representative
that was rejected with reason `disposition_differs`, `checks_differ`,
`projection_differs` or `successor_differs`. Findings on reduced edges are kept,
so failed property counts are unchanged by reduction.

## Diamond validation

**REDUCE-004.** `validateDiamonds` visits completely expanded nodes. For each
pair of candidate inputs (a, b) in enumeration order with a before b, it steps
both. If b remains enabled after a and a after b (membership by `equalInputs` in
the model's input domain), it executes a·b and b·a, counts the pair as validated,
and reports it as non-commuting when the final states differ by model equality
(`state_differs`) or the two steps' observations differ (`observations_differ`):
the set of failed (phase, check ID) pairs and the multiset of projection bytes.
This is evidence about independence, never permission to prune. RunLimits are
polled per node and per pair.

## Reduced versus unreduced comparison

**REDUCE-005.** `compareReduction` requires two finished searches (coverage not
`incomplete`) with retained edges. It matches reachable states by hash bucket
plus model equality and reports unmatched states in each direction. It compares
represented edge classes `(source, resolved target, disposition, state checks,
transition checks)` in both directions, so raw edge counts may differ when
actions collapse. An edge whose target has no counterpart is never represented.
It also compares finding counts, failed property counts and coverage. When
`ReductionProgressPredicates` (edge fairness, state kind, optional state rank)
are supplied, it runs progress-analysis-v1 on both graphs and requires, under the
state mapping, equal classes, equal unsettled-transaction membership, member-for
member equal closed nonterminal components, and equal graph completeness. Without
predicates `progressMatch` is nil: progress was not compared and the comparison
establishes no progress evidence. The check is quadratic and intended for
exhaustive small bounds.

Qualification keeps an unreduced profile. Any change that strengthens the
comparison signature is a separately versioned semantic change, not engine
parity.

## Negative controls

Tests tamper with a reduced report (a missing state, a rewritten target, a
dropped finding) and require the comparison to fail. They also check that each
rejection reason is reported and that non-commuting arithmetic diamonds are
detected.
