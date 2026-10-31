# N06 implementation evidence index

This directory records the implementation in the isolated checkout
`/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336`, based on
`0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`. Integration into another branch belongs
to the parent task. Nothing here authorizes a push, PR, default/profile change or
144-cell promotion.

Source integration and final review are still in progress. No N06 runtime or
installed-package qualification is claimed. Task IDs in
`implementation-tasks.json` identify delegated authors/reviewers; they are not
Kyoto job receipts.

## Immutable executions submitted

| Purpose | Kyoto handle | Evidence |
| --- | --- | --- |
| Rust public reference executable | `6ac33655baf9486895c626676a9740ff` | [r1](rust-r1/README.md) |
| Rust private lifecycle tests, compilation only | `5ea2d04832ab4cc0acb08d05e8e1384c` | [r1](rust-r1/README.md) |
| Rust public/host tests, compilation only | `c47fa958741f4502beac1404b613ad1d` | [r1](rust-r1/README.md) |
| Independent 49-row specification author | `1be30f3a8bea47758dd063d93a8970cb` | [author r1](rust-goldens-r1/README.md) |
| Python campaign/lifecycle runtime tests | `be50a84c471f4fb6908ce960e2a54fd7` | [handoff](python/focused-r1/handoff.json) |
| TypeScript baseline/campaign tests and fresh npm examples | `84048d5f31b4404ba8a6c07e1af98f6a` | [handoff](typescript/HANDOFF.md) |
| Go campaign/baseline tests, race/vet and fresh module example | `9dc6574d3c8840f6b5cb7dd3d0e7ef3b` | [handoff](go/runs/focused-r1-20261022/handoff.json) |
| Swift guards, baseline/campaign tests and fresh source-package consumer | `c23862ecdfe64abd84336b825cc5e62c` | [handoff](swift/receipts/r1-handoff.json) |

The [root read at 16:11 UTC](queue-observations/20261022T161104Z.json) confirms all
first seven jobs queued and unexecuted. Swift's later submission and first show
also record queued, with no start or exit; its manifest is
`94381466b7d64f313221d5f44c10189770fcaa3e16ec61427b274a4a6b1aab97`.
There are eight retained execution handles. The two Rust
test commands are effectively `--no-run`; even successful completion would not
execute tests. The source-only fabricated-worker-loss regression was added after
the r1 snapshot and is not covered by that compile. The package include fix in
the working Cargo manifest is likewise later than r1. Keep those source identities
distinct when finalizing evidence.

The round-two reviews found further test and qualification-driver defects.
Their fixes are being made in working source; they do not alter the already
queued Go, Python or TypeScript r1 snapshots. Those receipts cannot qualify
later source. No replacement or duplicate execution has been submitted.
Root source inspection also found that Python's frozen focused runner probes
and may signal its numeric process-group ID after reaping the leader. Its
all-exit cleanup therefore lacks the retained-PID ownership protection required
of the revised runners. The old payload is preserved; its eventual result cannot
qualify that cleanup contract. No unrelated process or job was touched.

The author derives 15 seed, 15 validation and 19 campaign rows independently.
Its expected JSONL and FREEZE remain absent until the queued author executes.
Materializing the specification is not a runtime comparison pass. Exact commands,
source/corpus hashes, paths and remaining test/replay steps are in the linked
receipts and [Rust handoff](rust-handoff.md). Do not restart these jobs or modify
their frozen inputs. Additional language submissions will be added when each
owner finishes its immutable snapshot.

That root read of shared-default Kyoto health reported OS memory pressure
`warning` and effective admission concurrency `0` (sample
`1792685463.806828`). No queue policy was changed and no unrelated job was
cancelled. This is an admission observation, not a terminal job status.
The initial read used an unsupported `--json` option; its exact failed command
and raw stderr identity are retained beside the corrected read. It was a CLI
metadata error, not a build or test result.

## Gates that remain separate

- Independent source reviews and fixes are recorded under `docs/native-parity`.
  A source review does not prove compilation or execution.
- Focused language jobs must execute the live lifecycle, actual worker paths,
  callback/fault fixtures and fresh native package applications. Baseline tests
  and cross-language comparisons retain their own receipts.
- The installed harness still needs a committed source freeze, materialized
  independent seal, package/build phases, actual consumers and unchanged baseline.
  The coordinating root has not set `N06_AGGREGATE_READY`.
- Wider Rust usize counts exceed Go/Swift signed counts and TypeScript safe
  numbers. Shared comparisons use an explicit intersection; wider unsupported
  cases cannot be clamped or counted as passes. Seeds, IDs and work counters keep
  their distinct full-u64 requirements.
- Swift's unchanged N05 feedback-metadata wrapper normalizes a catchable marker
  to ModelError with model metadata present, whereas Rust returns Panicked.
  The expected and actual rows must expose this dependency mismatch.

N05 remains reviewed implementation with final qualification pending:
78 materialized cases, reviewed 90-case author, existing author job
`36c3552df326429fb9e76d872c80aafa`. Its missing FREEZE and owner-held jobs are
unchanged by N06. N10 assurance, browser/cross-platform qualification, generic
report merging, durable delivery/resume, preemption and hard RSS are not implied.
