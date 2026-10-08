# M7 decision record

Decision: **no-go for a public transition-table/statechart language or search
pruning in this release**. This is the roadmap's separate experiment gate, not a
claim that those features were implemented or proved safe.

The application-owned job fixture supplies a concrete authoring case. Its transition
logic includes guards, ordered effects, pending-ledger updates, delayed deliveries
and ignored duplicate commands. An ordinary Rust match expresses that behavior
once, and the adapter registers it without reproducing its semantics. A table macro
that merely wraps this match would remove little additional work while adding a
second grammar for guards and effects. A table that generates an approximation
would violate the single-production-reducer requirement. There is currently no
qualified use case demonstrating a benefit sufficient to maintain another DSL.

The experiment retained here is the contrast between reservoir sampling and
explicit indexed selection. `examples/measure.rs` in the qualification package
measures materialization separately, then both sampling algorithms. Index-to-entry
and duplicate-weight equivalence are tested structurally. Different same-seed input
sequences are expected: this is an explicit algorithm choice, never a silent change
to Auto. These measurements do not justify search pruning.

Structural shrinking of correlation IDs remains no-go: the stale-completion
counterexample demonstrates why an integer simplification can change causality.
The existing state-aware shrink validation remains the authority. Incremental
checks would require dependency contracts for both state and transition properties;
symmetry and partial-order reduction would additionally require a scoped property-
preservation argument, including pending queues and oracle history. None has been
established by the supplied fixtures. No optimizer is enabled on that basis.

Revisit only with an application that needs the additional authoring surface, an
independent handwritten comparator, adversarial tests, build/authoring/runtime
measurements, and an explicit correctness argument for each proposed optimization.
