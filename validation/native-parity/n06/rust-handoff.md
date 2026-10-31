# N06 Rust author handoff

Source-ready for root integration, with the open items below. No staging/commits,
production Rust/N05 edits, pushes, queue policy changes or duplicate live jobs.
All eight principal Rust authority files still match r1 and the required base.

Four live jobs were last observed queued, exit_code null:

| Purpose | Handle | Source identity |
| --- | --- | --- |
| Actual public Rust reference | `6ac33655baf9486895c626676a9740ff` | r1 |
| Private lifecycle/codec/self-model compile | `5ea2d04832ab4cc0acb08d05e8e1384c` | r1; effective `--no-run` |
| New public + unchanged campaign tests compile | `c47fa958741f4502beac1404b613ad1d` | r1; effective `--no-run` |
| Independent expected JSONL + FREEZE author | `1be30f3a8bea47758dd063d93a8970cb` | golden-author r1 |

Rust r1 manifest SHA-256:
`b9d4dcdcfc003c046e505ac1de94b4adad2fb37ec34c951f33ee24298317cc18`.
Golden author manifest SHA-256:
`31fc562b38c012eb6712a81dea1aebbbfef5e8a2ce9d75d4c51bcae22e4271d4`.
Golden contract hash:
`6c232b3961e4c4ed52d49f55ed1182f22be9080e7c1bbc37b7445bdd370c2082`.
Current strengthened private adapter SHA-256:
`47ff5f466092ea02d9bfc7dc2e61931b69a5fbb3170c60f7634abd6e22cf07bb`.
That last adapter is a post-r1 source-only delta, not covered by a queued compile.

`rust-r1/README.md` and `rust-goldens-r1/README.md` preserve exact argv/cwd/env,
shared queue/job ownership, output paths, full logs and immutable input manifests.
Expected output paths after the author executes are
`rust-goldens-r1/artifacts/expected.jsonl` and `FREEZE.json`; both were absent at
handoff. The author emits their exact hashes and a compatible
`n06-independent-spec/1` seal. Its independent inventory has 15 seeds, 15
validation rows and 19 campaign rows. No native output authors expected values.

The complete schema and four-finding mapping are in the frozen
`docs/native-parity/N06-RUST-CONTRACT.md`. Independent source review affirmed
callback/error/metadata expectations, parsed-JSON strictness, admission
biconditional, faulted fuzz and worker-stop overwrite; no runtime was executed.

Root integration items:

1. Existing Cargo include excludes the fixture and Python comparator imported by
   the new packaged integration test. Include those paths/schema or explicitly
   exclude the checkout-only test. Checkout queued jobs are unaffected.
2. Existing Swift N05 feedback metadata maps CampaignPanic to ModelError. Retain
   Rust Panicked/metadata Some/incomplete expected row; expose the mismatch as an
   open native dependency, never a normalized pass. No N05 edits were made here.
3. Original Rust self-model misses direct core.lost_worker == ledger.lost_work
   identity. A prior ModelError can mask fabricated loss and falsely admit Finish
   in a deliberately corrupted model. Current owned adapter adds that independent
   equality, a fabricated-loss control and real-loss positive control. This is a
   test-oracle strengthening, not a demonstrated production executor bug. Compile
   and execute the post-r1 adapter in the final source snapshot; do not attribute
   that test to the earlier immutable compile.
4. After successful test compilation, select the exact emitted Cargo JSON test
   executables, hash them, then submit actual execution through Kyoto. Private
   tests include full original codec/self-model BFS/fuzz/shrink/record/read/replay
   plus N06 projections. Export fault traces to a fresh directory and submit a
   fresh-process replay of the emitted minimized witness. Retain failures and
   source/binary/trace identities. All these executions are pending.

N05 remains materialized78/reviewed author90 with final qualification pending;
its existing `36c3552df326429fb9e76d872c80aafa` author was untouched. No N10,
fresh-installed native integration, profile/default/144-cell, durable-delivery,
hard-RSS or fatal-trap recovery claim is made.
