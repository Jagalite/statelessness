# N05 qualification evidence index

All executable work uses the default shared Kyoto queue. Source and consumer
implementation is complete, but final installed qualification is still pending.
No profile/default promotion, push, PR or broad platform claim is made.

## Native execution and remaining jobs

| Scope | Job | Recorded result |
| --- | --- | --- |
| Go package and portable JSON tests | `29ebddcc9c834023b6b0fc4c57d4881c` | Succeeded; final package source seal unchanged |
| Go first external public consumer | `145cd1af6dd945c28c83fdfbcb294509` | Compiled successfully; earlier consumer schema, no execution claim |
| Go revision-3 external consumer | `b6c08b43052544cea6f8592ae77d0ac7` | Queued, compilation only |
| Python initial focused suite | `468ec8e2ad0e498ba0ef797f56dfd5a8` | 75 tests passed on earlier boundary revision |
| Python final boundary suite | `8c97dbd659674c739cffa1adcdd7127f` | Queued, 27 tests requested |
| TypeScript initial package suite | `73d5c99651344d699b1c6eb84a48c65c` | Build and 949 tests passed before ordinary formatting correction |
| TypeScript ordinary correction suite | `e18721828a2e4212b4f21decfaf279ea` | Queued against live source; earlier hashes cannot identify later execution |
| TypeScript final formatting/seal tests | `0f40d4b1a2e84dc598c409af74f657f7` | Queued; isolated compiler output and before/after source hash checks |
| Swift R2 package, compile contracts, example | `10982113bd6c4b1589d10a47ef72c088` | 163 tests passed, including 16 guided; earlier sink revision |
| Swift final GuidedTests | `50aef69ec07148cc81e9ed6ab82c5521` | Queued, 18 tests requested, frozen R4 source snapshot |
| Actual Rust source-path public consumer | `717c02a2dfd84bd49c7da261f0055201` | Queued after automatic pressure deferral; no build/runtime pass |
| Independent revised corpus author/seal | `36c3552df326429fb9e76d872c80aafa` | Queued; final source derives 90 cases, no active seal yet |
| Parent source guard after final source review | `4baa668b525940e080684b11abc6680f` | Queued; the earlier 254-file observation passed |

These are observations, not permanent job states. Retrieve terminal `show` and
complete `logs` before updating a claim. Do not resubmit a live job. Cancellations
of superseded unstarted Python R2, Swift R1/R3/old installed, and intermediate
author jobs are recorded in their owners' receipt directories; no execution is
claimed for them. No watcher agent is required to retain a queued job.

At the final source handoff on 2026-10-22, the shared scheduler reported
`OS memory pressure: warning` and `effective_max_running: 0`. Its policy and
competing jobs were left unchanged. The active corpus is the earlier 78-case
materialization with no current seal; the reviewed independent author now
derives 90 cases. That difference is an explicit unfinished authoring gate,
not a 90-case execution result. Consumers/harness refuse the absent seal.

Exact command, source, toolchain and raw evidence is retained under:

- [Go](../../../go/receipts/n05/README.md)
- [Python](../../../python/receipts/n05-guided/README.md)
- [TypeScript](../../../typescript/receipts/n05/README.md)
- [Swift](../../../swift/validation/n05/README.md)
- [Rust](../../../scripts/native-guided-consumers/rust/receipts/README.md)
- [Independent corpus](../../../conformance/guided-v2/receipts/README.md)

## Final installed gate

The [harness](../../../docs/native-parity/N05-QUALIFICATION-HARNESS.md) stages
immutable sources and actual public packages in a fresh external workspace.
After independent `FREEZE.json` publication, its supplied corpus/seal hashes and
an explicit candidate HEAD are mandatory inputs. Pin the real Rust toolchain
`/Users/jagatranvo/.rustup/toolchains/1.90.0-aarch64-apple-darwin`.
The five-engine run and preserved portable baseline remain unexecuted. A
successful prepare or package/build phase is not runtime qualification.

[Source-review hash records](source-review/) preserve separate independent and
root-inspected identities; they are not test receipts. [Parent isolation](parent-guard/)
records a read-only 254-file comparison to pinned Git objects. The final
qualification must repeat that observation, retain exact source/package/corpus
identities and raw results, and report only platforms actually executed.
