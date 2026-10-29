# TypeScript observation review corrections

Round 2, 2026-10-22. Shared isolated worktree
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, base
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`.
Source changes only; the parent owns the integrated freeze and Kyoto qualification.

- **TSSWIFT R1:** renamed the diagnostic class and stage type to
  `TextObservationError` / `TextObservationErrorStage`. The existing generic
  oracle `ObservationError` export remains intact. Updated diagnostic integration
  tests, consumer and README. Added an actual `WithOracle.observeTransition`
  error regression, both public declaration types, and an installed-consumer
  self-test of distinct constructors and retained oracle cause/transition.
  Existing paired tests and `oracle.ts` are untouched.
- **TSSWIFT R2:** public errors no longer register their own attribution.
  Internal error identities belong to a specific record or payload callback;
  `EncodedPayloads` uses the active callback writer lease. Forged and nested
  errors become the current `format_input/output/state` failure. Current codec
  errors keep `encode_*`, and a current sticky record failure keeps
  `record_limit`, even if the formatter catches it and throws another error.
  Attribution expires across callbacks, records and observers. Source-authored
  regressions cover direct stage forgery, nested codec/record/write/flush/poison
  errors in all three payload positions, retained errors, writer expiry before
  diagnostic rendering, zero outer writes, healthy Off probes and recovery.
  Existing encoded-stage and sticky-bound regressions remain.
- **Proof R6 (TypeScript):** declaration cases now make every original codec
  hook increment the forbidden-call counter and throw. Only schemas installed by
  `bindModelCodec` produce declaration bytes. Native custom hooks remain
  instrumented, and the existing final counter rejects a binding bypass even in
  `observe` mode. Expected fixture fields and callback names/order are unchanged.

The pinned Rust observation/monitor APIs and relevant tests, N07 plan, shared
observation specification, historical checked-results audit and current native
APIs were inspected. Existing checked tokens, periodic checking and bounded
Recorder are preserved; diagnostics add no reducer/check/effect execution,
continuity proof or checked authority. Checked consumers and other agents' files
were not edited.

An independent read-only review found no blocking defect in these corrections,
including attribution, lease expiry and the declaration bypass control. This is
source-review evidence only, not compilation or execution evidence.

No builds, tests, compilation, syntax checks, implementation execution, fixture
generation or Kyoto submission ran. No staging, commits, push or PR. All new
regressions and the installed consumer remain unexecuted pending the parent's
shared qualification; this correction is not N07 completion evidence.
