# N05 Rust public reference consumer

Authority is commit `a4cc11eea8db62115b452eee3c6610c04fa06b96`, with source read from
Git objects until the shared isolated checkout finished materializing that HEAD.
The source-only guided audit/review and actual `src/guided.rs`, `src/explore.rs`,
`src/model.rs`, `src/execution.rs`, and guided regression tests were inspected.
No authority source, independent expected fixture, parent checkout, or other
worktree is edited by this consumer work.

The [consumer](../../scripts/native-guided-consumers/rust/README.md) is a separate
Cargo workspace with a normal public package dependency. It calls the actual Rust
guided/ordinary search, Feedback/StateFeedback/exact-state, observation/control,
record/replay and shrink APIs. It implements only the specified fixture callbacks
and output normalization. The corpus expected outputs are absent from its parsed
input types and never determine a search, replay, or shrink result.

The independent spec agent owns `conformance/guided-v2`. Its `FREEZE.json` must
exist before any candidate/expected comparison. Passing retained witnesses replay
exactly and are not shrink targets; requested property failures replay and shrink
using public APIs. Host-only checks cover Rust ownership, trait bounds, panic
behavior and entry-point boundaries separately from shared deterministic cases.

## Execution record

Initial source consumer build submitted to the shared Kyoto queue as
`717c02a2dfd84bd49c7da261f0055201` with requested command
`cargo build --manifest-path Cargo.toml`, from
`scripts/native-guided-consumers/rust`, with `CARGO_INCREMENTAL=0`.
The first attempt resolved dependencies and produced a consumer Cargo.lock, then
Kyoto stopped it for sustained memory/swap pressure and retained the job for
retry. The raw deferred attempt is preserved in `build-01-pressure-deferred.log`
and `build-01-pressure-deferred-show.json`; it is neither a compilation success
nor a test failure. The source dependency path was corrected before execution.
While the job was deferred, the consumer followed the independent schema review:
helper pushes remain opaque; replay checks preserve phase batches; dispositions
are retained in traces and replay; requested original/minimized failing witnesses
include actual public replay results. The before/after source manifests preserve
that revision boundary. No candidate/expected comparison has run. Terminal build,
runtime and comparison outcomes will be added after execution.

Source-path reference execution and an independently staged/packaged installed
consumer are separate gates. The parent orchestrator owns installed staging and
cross-engine qualification. No N05 completion, installed qualification, N06/N10
assurance/campaign coverage, profile promotion, or default promotion is claimed
from a queued build or source-only review.
