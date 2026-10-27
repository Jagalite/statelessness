# Codec and replay slice review

Static review is separate from execution qualification. The slice retains the
four promoted profiles and does not establish allocation, RSS, performance,
browser, platform, or full N01/N11 parity.

Review compared native implementations with `src/model.rs`, the public recording
path in `src/execution.rs`, `src/execution/replay_kernel.rs`, and binary sizing in
`src/trace.rs`. Reviewers covered language implementations independently of their
authors. Findings addressed during implementation include:

- Preserve a produced transition before any fallible snapshot; recovery must not
  execute the reducer again.
- Classify snapshot failures through explicit origin, rather than user-controlled
  error text. Preserve the existing public model-error classification.
- On failed snapshot, transfer retained actual evidence to the returned error;
  send the error observer unavailable-snapshot metadata rather than a mutable
  alias to recovery evidence. Do not retry a known failed snapshot.
- Size actual bounded observations using current model metadata and stored run
  configuration. Reserve exactly 18 footer bytes during replay, separately from
  recording's larger future-error allowance.
- Admit output prefixes and bodies against a shared remaining budget. Avoid
  reserving an output collection known to exceed the available recording frame.
- Keep callback leases revocable, nested errors sticky, and asynchronous or
  non-void codec/observer results invalid where runtime types permit them.
- Capture caller-owned limits before application callbacks can change them,
  while preserving identity-before-validation behavior for incompatible replay.

The independent shared corpora and public Rust reference establish finite semantic
expectations. Native-only tests cover escaped leases, runtime invalid arguments,
snapshot failures and mutable aliases that Rust's types or borrows cannot express.
Final execution receipts must identify their exact source snapshots; intermediate
passes and failures do not qualify subsequent edits.
