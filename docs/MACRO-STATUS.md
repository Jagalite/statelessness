# Macro implementation status — 2026-10-08

Implementation is based on checkout `6c8aed126977204f8cf35248947c4f78dd00bb54`,
which is newer than the supplied roadmap's pinned baseline. This work is released as 0.2.0.
Source SHA-256 manifests identify the snapshots covered by local evidence.
Hosted verification must pass on the release commit before publication.
This release does not close the broader performance and authoring gates.

| Milestone | Delivered here | Gate status |
|---|---|---|
| M0 | First-party companion package, parser/validation/emission modules, explicit syntax/protocol, macro source stamp, renamed/cold-offline packaged consumers, expansion inspection | Implemented for documented syntax; clean committed CI matrix remains a release gate |
| M1 | Model adapter, stable ordered properties, batched hooks, reusable sinks, lazy check helper, explicit unchecked mode, memory-estimate delegation | Runtime and diagnostic fixtures pass locally |
| M2 | Bounded ordered domains, exhaustive variant accounting, descriptors, shared membership, checked products, explicit indexed sampling; existing Auto unchanged | Handwritten request-domain equivalence and stale cancellation path pass |
| M3 | Bounded value reader, canonical owned codecs, derives, custom field adapters, model codec delegation, schema/variant diagnostics | Golden bytes, malformed/resource-limit cases and failing-snapshot replay pass |
| M4 | Counter/request/jobs fixtures; fuzz, shrink, guidance, campaigns, fresh-process replay; package checks; existing platform CI expanded | Local qualification; Linux/Windows/current hosted stable jobs and release review still pending |
| M5 | Independent lifecycle templates implementing Oracle/OracleCodec, explicit logical deadlines, resource and settlement tracking, bounded persistent histories | Fault-injection and observation-error fixtures pass; classifier independence remains application-owned |
| M6 | Two-machine routing helper, explicit queued delivery, child/global checks and identities, namespaced collision errors, aggregate codec | Handwritten interleaving comparison and delayed-delivery replay pass |
| M7 | Separate authoring/optimization evaluation and explicit decision record | No-go for a new public DSL or pruning; see MACRO-EXPERIMENTS.md |

## Review fixes

The subsequent review fixed overdue-obligation cancellation and restored-history
checking, generated-name capture (including Result constructors and primitive wire
widths), generic associated-binding/return-arrow parsing, and suppressed decoder
errors. Regression tests cover each. `validation/macros/review.json` records the
reviewed source and fresh checks. The earlier `qualification.json`, expansion
excerpt and performance measurements describe the initial source snapshot; they
must not qualify changed bytes. Hosted platform and performance gates remain open.

## Local evidence

- macOS arm64 workspace tests on Rust **1.90.0** with the compiler selected by an
  explicit toolchain-bin PATH, and on installed Homebrew **1.99.0** stable.
- Warning-free workspace/all-target Clippy, formatting and documentation checks.
- Extracted core and macro consumers with a completely empty Cargo home, renamed
  imports, generic and const-generic declarations, configured fields and encode-only
  outputs. The core-only consumer does not compile the macro crate.
- Compile-fail consumers check stable diagnostic families, duplicate tags/IDs/roles,
  unsupported fields, malformed receivers/items, missing variant coverage and old
  runtime protocol rejection. Compiler-driven diagnostics are retained for trait
  bound and exhaustiveness failures.
- Existing packaged core starter: separate-process failure search, original/minimized
  replay, changed-build divergence, exclusive evidence creation, malformed trace.
- Generated application jobs: persisted missing-cleanup failure, separate-process
  exact replay. Explicit domain assumptions and bounds are included in its run config.
- Fresh native/Wasm artifacts: C ABI relocation, eight JavaScript adapter tests,
  Wasm replay/enumeration smoke, installed npm-tarball smoke. These are local Node/C
  checks, not a browser UI or additional platform qualification claim.
- Source-bound package receipts, runtime microbenchmark CSV, build measurements,
  and an expansion inspection artifact are retained under `validation/macros/`.

The first external-volume incremental build failed writing a dependency graph.
Validation used isolated local target directories afterward. An initial `rustup run`
attempt still selected Homebrew rustc; it is not counted as MSRV evidence. The explicit
Rust 1.90 rerun supplies that evidence. An initial browser-binding build used a compiler
without its Wasm target; the final binding build used the installed rustup toolchain
with its Wasm target. Old-artifact checks are not the final binding qualification.

## Measurements and limits

`cargo run --release -p macro-qualification --example measure` compares an optimized
handwritten counter with the generated counter. Each row covers 500,000 transitions
or reusable checks; checksums agree. Both transition loops allocate once per step
for the application output vector; both reusable-check loops allocate only the initial
buffer. This fixture does not measure clone-heavy states, monitor-retention costs,
or production application throughput. Timing variability and small differences remain;
there is **no speedup or zero-overhead release claim**. Inline hints expose generated
forwarders and callbacks across crate boundaries and respect explicit callback policy.

The sampling rows report domain materialization separately from 1,000 indexed or
reservoir selections for 16/256/4096 entries. Different checksums are expected because
the algorithms use RNG draws differently. Structural index/weight tests supply the
semantic check; these timings are not a statistical correctness proof.

`python3 scripts/measure-macros.py --output FILE` uses separate clean local target
directories and records unchanged rebuilds with incremental compilation disabled.
The core build emits all configured library crate types; the macro consumer builds
its dependency rlib and fixture. Those artifacts differ, so the measurements are
**not** an apples-to-apples macro build-speedup comparison. Source-edit incremental
rebuilds, large production binary size, and repeatable authoring-effort tasks remain
unqualified. The explicit release gate for unexplained hot-path overhead remains open.

## Remaining release work

Run the expanded existing hosted Linux/macOS/Windows × Rust 1.90/stable matrix from
a clean committed checkout and retain those receipts. Perform the broader performance
and authoring measurements above, review diagnostics and the documented syntax envelope,
assign release versions, and review package provenance before publication.

Advanced higher-ranked/associated-const syntax and recursive structural derives are
outside the current qualified parser envelope. No structural-diff derive, inferred
purity, automatic correlation-key shrinking, statechart language, symmetry reduction,
or partial-order pruning is claimed. The original handwritten APIs remain available.

## Release scope

0.2.0 adds serialized trace replay with lifecycle monitors, saved-history
attachment, and composition with actual monitor histories in equality and replay.
A zero-sized product fixture checks real usize cardinality overflow before mapping.
These fixtures supplement the prior review; hosted release verification covers them.
M7 remains deferred. Broad performance, production throughput, authoring-effort
measurements and unsupported syntax remain outside this release.
