# Finite-fixture reduction research

`stateless::research::compare_equivalence` evaluates a proposed state projection
against an explicitly supplied finite model domain. It is disabled by default,
requires recorded assumptions to be declared established, and never modifies
production explorer equality, input enumeration, replay, or guided search.

Supply the complete list of exact states, a `Proposal` with independent expected
property IDs, and an explicit `ExperimentConfig { enabled: true, .. }`. The
proposal records model/proposal source identity, finite-domain assumptions,
equivalence, effects, transition properties, queue order, oracle history, and
visible timing. Empty assumption statements are rejected. The `established`
field is a caller/reviewer declaration, not a proof from this library.

The harness materializes each declared transition once, checks that every
successor belongs to the explicit domain, and stores ordered effects,
disposition, and checks. It then performs exact and projected traversals over
that immutable finite table. Neither traversal reruns reducers. There are
separate bounds for declared states, materialized edges, and equivalence pairs.
A limit, callback error, absent expected property, or skipped check prevents
qualification.

Every reachable pair sharing a projected key is compared for:

- Exact state-check observations.
- Identical enabled input labels and order.
- Exact ordered effects and disposition.
- Exact transition-check observations.
- Equivalent successor keys for the corresponding inputs.

These conditions are intentionally strict. For a closed finite table they form
a directly checked labeled bisimulation condition: matching inputs preserve
observable results and lead to another related state. The harness also compares
projected reachable classes and property-failure reachability. It retains exact
shortest input witnesses for failures lost by the projected traversal and both
prefixes for inconsistent equivalence pairs.

`AgreesWithinDeclaredFixture` means the candidate preserved the checked fixture's
behavior and property reachability, including any failures. It does **not** mean
the application properties passed, that a broader domain is equivalent, that a
history-dependent property omitted from state is preserved, or that any general
partial-order reduction is sound. A `Counterexample` rejects the candidate even
if performance would improve. No outcome activates production pruning.

The regression fixtures include a behaviorally equivalent two-state toggle and
a two-machine interleaving where projecting states to a population count hides
`Right -> Observe`. The exact witness exposes the hidden transition failure.
Other cases reject changed effect order, unclosed domains, missing checks, and
budget-limited comparisons. These cases are authored runtime regressions;
compile-only qualification does not establish their executed results.

The initial harness studies exact label-preserving equivalences. Symmetry that
renames inputs/effects, partial-order independence, queue reordering, timing
abstraction, and history compaction need separate explicit equivalence contracts
and qualification. They are not enabled by this research API.
