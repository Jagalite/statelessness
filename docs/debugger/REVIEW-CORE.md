# Independent pre-PR review: core observations, replay, sessions, and workbench

Reviewed on 2026-10-10 against `docs/Statelessness-Rust-Debugger-Design-v1.1.md`, especially §§4, 7, 8, 12.4, 13, 17, and M0–M2. Review input was the full implementation diff from `f3e00e79471b928d67ddb530c113d6d51af6f277` to `461a369eb161d3924e9e9dd1e72633e122f144f7`, followed by the working-tree fixes below. Prior qualification PASS claims were not treated as proof.

Owned source review: `src/observation.rs`, `src/execution.rs`, `src/monitor.rs`, `src/trace.rs`, `crates/statelessness-debug/src/session.rs`, and `crates/statelessness-debug/src/workbench.rs`. New tests use public APIs and independently constructed fixtures. This report covers these subsystems; it is not a review result for the protocol, inspection, live controller, or telemetry implementations being reviewed separately.

## Findings, including resolved defects

### CORE-1: minimization changed the recorded environmental assumptions — fixed

**Severity: high correctness.** The original `workbench::minimize` used only `Generate::is_enabled` while shrinking, even when the input trace was recorded with `InputPolicy::enumerated` or a stricter declared callback. `Generate` has a permissive default, so it cannot stand in for a separately selected environment. This violated §8.5's requirement to validate candidates against the same environmental policy.

The independent regression first failed with a valid recorded sequence `[0, 1]` minimized to `[1]`. The recorded enumeration forbids input `1` at the initial checkpoint; deleting input `0` therefore manufactured an out-of-domain reproducer.

Resolution:

- Added `minimize_with_policy(model, trace, &policy, max_admission_candidates, target, config, limits)`
- Reused the session's admission implementation on every candidate, with finite enumerated work and the original checkpoint
- Kept `Generate::is_enabled` as an additional causal restriction
- Matched the supplied policy label and unrestricted/declared origin to recorded provenance, rejecting duplicate, unknown, incomplete, and mismatched provenance
- Retained legacy `minimize` for unlabelled legacy traces and explicitly unrestricted captures; declared policies now require the new API
- Updated the existing mid-run minimization example/test and `docs/DEBUGGER.md`

Regression coverage includes the originally forbidden shrink, a valid same-domain simplification, explicit unrestricted and legacy behavior, label/origin mismatches, malformed provenance, infinite enumeration bounded to four candidates plus one lookahead, and cancellation raised inside admission before candidate delivery.

### CORE-2: invalid minimization commands executed reducers before rejection — fixed

**Severity: medium correctness/admission.** `max_attempts = 0` and a passed target check were rejected only after replaying the entire trace for verification. The independent regression observed two reducer calls for an invalid request. This contradicted §4.2's pre-execution command/limit admission order.

Resolution: preflight positive attempt count, failing target status, and presence of the target ID in the appropriate recorded boundary before verification. The state/transition split still must be established by the existing shrink validation because v1 trace checks do not encode that split. The regression now asserts zero reducer calls for both invalid cases.

### CORE-3: initial failure wrongly required a positive transition budget — fixed

**Severity: low boundary correctness.** A valid sequence-zero failure with `max_replayed_transitions = 0` was rejected by the verification-budget preflight, although neither verification nor original validation needs a reducer call.

Resolution: permit the zero-step case while retaining the existing preflight for nonempty prefixes. The regression confirms an exactly validated initial failure, empty reproducer, and zero replayed transitions.

## Independently checked invariants

New `tests/pr_review_core.rs` has six tests:

- All 15 nonempty combinations of disposition/output/state/check divergence agree between old and rich replay APIs; primary mismatch priority, legacy callback boundaries, sequence-zero rich observation, first-divergence stopping, and failure reproduction remain consistent
- A checker error retains the one actual transition, exposes no partial check batch as complete, preserves the primary error against a second observer error, and performs no output encoding
- Bounded replay rejects an oversized stored check batch before any model decoding, checking, or reducer execution
- Actual output-count overflow preserves the completed check batch and result without starting encoded-output allocation/encoding work
- Sealed checked recording runs no additional reducer/check callbacks and rejects a periodic policy even on a turn where all callbacks happened to run
- Every single-bit mutation in every byte of the generated fixture artifact is rejected by the finite trace reader; the original artifact round-trips unchanged

New companion `tests/pr_review_sessions.rs` has twelve tests. Besides the resolved minimization findings, these establish:

- A verified fork preserves its parent and checkpoint and rejects a content-mismatched parent binding
- Simultaneous property failure, exact-state encoding failure, and frontend failure leave the actual state/revision committed, preserve property failure as the stop reason, retain both diagnostic errors, and export only the replayable coherent prefix
- Borrowed non-`'static`, non-`Debug`, non-`Send`/`Sync` state and output values still compile and execute without a codec

Existing suites were rerun, rather than merely inspected. Their passing assertions additionally cover exactly-once oracle advancement through recording/inspection/breakpoints, retained composite oracle/queue checkpoints, initial checker errors, cancellation between replay turns, actual byte/item/frame budgets, recorder freeze/eviction, stale candidates and reused display IDs, and no ordinary exact continuation after failure.

## Commands and actual results

Environment: Linux x86_64, isolated official Rust 1.95.0 toolchain via `/workspace/shared/render-rust-1.95.0/activate.sh`, debug test profile.

```sh
source /workspace/shared/render-rust-1.95.0/activate.sh
cargo test --test pr_review_core --test checked_replay --test review_replay_bounds --test monitor
cargo test -p statelessness-debug --test pr_review_sessions --test session --test workbench --test review_debugger --test oracle_lifecycle
cargo clippy -p statelessness -p statelessness-debug --all-targets --all-features -- -D warnings
rustfmt --edition 2024 --check tests/pr_review_core.rs crates/statelessness-debug/src/session.rs crates/statelessness-debug/src/workbench.rs crates/statelessness-debug/tests/pr_review_sessions.rs crates/statelessness-debug/tests/workbench.rs
```

Results: **81 focused tests passed**, including **18 new independent tests**. Strict all-target/all-feature Clippy and owned-file formatting checks passed. The pre-fix failures described above were observed before their fixes; they are retained here rather than erased by the final passing result. No commit, push, or PR was performed by this review.

## Caveats and release interpretation

- These bounds constrain debugger-owned evidence/work. Application reducers, checkers, decoders, legacy allocating codecs, and custom validators remain cooperative; this is not a hard process-memory or timeout guarantee
- A policy label is provenance, not authentication of a function pointer or executable. The caller must supply the original policy implementation. The APIs now reject silent substitution but cannot prove semantic equivalence of two callbacks with the same label
- Unlabelled legacy traces retain the historical Generate causality contract; that does not prove they were originally captured under a comprehensive environmental model
- Both legacy and explicit-policy minimization reject unknown/incomplete debugger provenance. A live capture without a declared environmental policy cannot automatically acquire one through minimization
- V1 traces omit the state/transition check split; minimization still requires the application to select the target phase, and original validation verifies it
- Artifact CRC and FNV content bindings detect accidental corruption/mismatch, not malicious authenticity. The mutation sweep is a deterministic adversarial regression, not exhaustive structured fuzzing or an allocator proof
- Cloned state must obey the model's determinism/independent-checkpoint contract. Interior-mutated aliases cannot become valid replay evidence merely by passing through sealed checked tokens
- This run did not independently qualify other operating systems, minimum Rust 1.90, Wasm clocks, native-debugger integration, or release performance. Workspace-wide release qualification remains a separate integration gate

No unresolved blocking defect remains in the reviewed scope after these fixes and tests. This is a scoped review result, not a universal correctness claim.
