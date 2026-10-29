# Native parity completion before PR #12 merge

User direction on 2026-10-29: complete the native parity plan before merging,
using the shared Kyoto queue. PR #12 remains unmerged. Package publication and
release are separate actions, as specified by the existing plan.

## Starting point and acceptance

- Integration baseline: `f9ff823089aba2d56abc25abd7238bb866afb8bb` (PR #12).
- Rust behavior baseline: `0876717bee85e335449c2c99ada41f6c6e6112d1`.
- Normative scope: [NATIVE-PARITY-PLAN.md](../NATIVE-PARITY-PLAN.md), N01–N14
  and milestones M0–M6. This continuation does not narrow that scope.
- The existing 36-feature ledger has 144 pending language qualification cells.
  Some implementation entries are stale. Source reconciliation and current-source
  execution are separate obligations; neither counts as the other.
- The compiler inventory accounts for 5,864 records for its recorded target.
  A resolved Rust inventory does not establish native semantic equivalence.
- The passing PR baseline CI remains evidence for that baseline and its tested
  scope. Every integrated source change needs appropriate renewed qualification.

## Implementation ownership

The parent integration agent owns final integration, independent review assignment,
shared-export conflict resolution, combined qualification, and the completion
decision. Each lane owns its contracts, native implementations, meaningful
regressions, installed examples, and source-bound qualification evidence. Each
lane must receive independent review before integration and acceptance.

1. **Guided search and campaigns (N05/N06).** Recover
   `t3/native-campaign-runtime` at `028167236bcc783cc497b7ce65400c33b1294d87`,
   which already contains guided source `0a26bf5`. Resolve
   `N06-DEPENDENCY-GAPS.md`; complete deterministic fixtures, workers,
   cancellation, delivery/acknowledgement, lifecycle, and installed consumers.
2. **Runtime observation (N07).** Recover
   `cb80c2eae9d17d77790c3af6612b0efa0644cbdf`. Complete DebugObservation API
   correspondence, logging/snapshot/sink behavior, checked-token integration,
   bounded retention, and fresh-process retained-suffix replay.
3. **Stateful systems (N08/N09).** Implement independent composition, network
   scheduling, and legacy/indexed lifecycle APIs in all four native languages.
   Qualify explicit routing, identity reuse, fences, stale deliveries, restart,
   historical encodings, bounded retention, and exact failure replay.
4. **Assurance (N10).** Implement independent manifests, evidence collection,
   assessment and merges, plus the production-shaped reference controller.
   Include independently authored requirements and executable semantic mutants;
   preserve failure, missing, skipped and incomplete evidence.
5. **Modeling and tooling (remaining N13/N14).** Complete domain/model/inspection
   helpers, lazy checks and native application/tooling examples. Preserve the
   existing codec, derive and research APIs and identity contracts.
6. **Semantic accounting and debugger (M0/N12).** Reconcile the actual native
   surface with the compiler inventory; finish optional debugger gaps and their
   tests. Add explicit source/qualification requirements without promoting
   historical or lexical-bootstrap records by inference.

Shared exports, package manifests, Oracle adapters, and aggregate ledgers require
combined review when multiple lanes change them. Each lane returns a reviewable
commit; the parent integrates after examining its evidence and conflicts.

## Kyoto and evidence rules

Read `/Users/jagatranvo/.codex/skills/rust-builds-kyoto/SKILL.md` before execution.
Use `python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- COMMAND` for builds,
tests, corpus generation, consumer qualification and measurements. Do not change
shared scheduler settings or policies. Inspect the effective command and policy
message: a Cargo `--no-run` result proves compilation only. Submit execution of
the compiled tests or the existing explicit runtime runner separately as needed.
Do not nest queue submissions inside a job that waits on them.

Refresh recorded job states before duplicating work. Preserve failed attempts.
Recover actual authored corpus/seal files or reproduce them from their reviewed
authors; a success log alone is not a replacement for an unavailable artifact.
Freeze each tested source and retain source/package/corpus/toolchain/executable
identities. Do not edit a source snapshot while its qualification is running.
Store large raw runs outside tracked source and commit concise reviewed receipts
and reproduction instructions. Never count compilation failures, crashes or
timeouts as successful semantic mutation detection.

## Combined completion gates

- [ ] Independently reviewed per-language correspondence for the pinned surface.
- [ ] N01–N14 implementation and qualification requirements satisfied separately.
- [ ] Reviewed contracts, independent expectations, negative controls and mutations.
- [ ] Installed five-engine interchange and fresh-process historical/failure replay.
- [ ] Current-source native suites, Rust runtime tests, formatting, lint and docs.
- [ ] Clean Python wheel/sdist, npm declarations/tarball, Go and SwiftPM consumers.
- [ ] Required desktop OS/runtime matrices; browser/mobile evidence for any claims.
- [ ] Equivalent-work resource/performance measurements and reviewed budgets.
- [ ] Explicit compatibility decisions, migration examples and package identities.
- [ ] Final integrated-source review and CI; reconcile every remaining open cell.

Full parity must remain open if any required capability or qualification is
missing. A lane pass is not a combined-source pass. Completion of this checklist
does not itself merge the PR, publish a package, or authorize a release.

## Integration checkpoints (not acceptance)

- M0/N12 candidate `630feb56f381ce661ee1cdb26d9cb2b72b5ed9f5` is retained
  locally. Its ownership/protocol corrections and gate infrastructure have static
  review, but its three current-source Kyoto jobs remain queued at integration.
  Concrete optional protocol/service integrations continue in a separate lane.
  See [the partial handoff](../../validation/native-parity/n12-completion-20261029/HANDOFF.md).
- N07 candidate `b478e67f1668385d120cef2e8debb64042d32cb3` contributes seven
  commits after the continuation baseline. Parent review examined the public
  exports, oracle error ownership, Swift recording/replay budget changes, and
  qualification dependencies. Integration with the M0/N12 checkpoint had no
  textual conflicts. This does not establish combined runtime correctness.
  Its native, Rust compilation, and extended execution jobs remain queued;
  dependent Rust execution and installed replay/browser/resource gates remain
  unexecuted. See [the N07 handoff](../../validation/native-parity/observation-slice/CURRENT-20261029.md).
- The original N07 and M0/N12 worktrees remain frozen for their existing jobs.
  Those jobs qualify only their sealed source if they pass. Final integration
  needs its own current-source qualification after the other lanes arrive.

No final checklist item or broad language-parity cell closes from these source
integrations. PR #12 remains unmerged and unchanged remotely.
