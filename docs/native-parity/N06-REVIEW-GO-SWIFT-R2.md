# N06 Go/Swift independent source review, round 2

Reviewed on 2026-10-22 in `/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336`,
branch `t3/native-campaign-runtime`, HEAD
`0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`. This document is the only file written
by this review. Two fresh, read-only delegated reviews covered Swift and the
focused qualification drivers; their findings were reconciled with direct source
inspection. Neither delegated reviewer wrote files or executed qualification.

**Disposition:** the three round-1 findings have source resolutions described
below, with full Rust64 count support still unsupported. Five P2 test/evidence
findings remain at the inspected cutoffs. No new production campaign/lifecycle
defect was established in those snapshots. This is neither runtime approval nor
a source freeze. Concurrent edits and the limits of rereview are explicit below.

The N06 plan section, both GUIDED-CAMPAIGN audit/review documents, the complete
round-1 review, and the root INTEGRATION/CHECKLIST were read. Actual Rust
campaign/lifecycle, its test-only codec/self-model, and guided/explore/model/
operations sources remain authority. The Kyoto skill was read. No builds,
tests, guard tests, scripts, installed consumers, qualification submissions,
queue inspection/polling, staging or commits were performed. No other checkout
was edited. Reading and hashing are source observations, not execution receipts.

N05 remains materialized78/reviewed-author90 with final qualification pending;
the existing author job `36c3552df326429fb9e76d872c80aafa` was not inspected or
restarted. No FREEZE or 90-case pass is inferred. N10, generic CampaignReport
merge, profile/default/144-cell promotion, push and PR work remain separate.

## Remaining findings

### R2-1 — P2: blocked-send causality is still an open test gate in both languages

Locations: `go/campaign_test.go:334-364`,
`swift/Tests/StatelessnessTests/CampaignRuntimeTests.swift:575-597`,
`examples/native-campaign/swift/Sources/CampaignConsumer/HostChecks.swift:244-283`.
The separately owned installed Go consumer has the same evidence boundary at
`scripts/native-campaign-consumers/go/host.go:391-415`.

The unit tests release the sink after observing exactly buffer+workers+1
Complete notifications. These notifications precede send; the external consumers
observe model teardown, which precedes even Complete. Those are useful positive
witnesses of sender progress after receipt, but do not establish that senders
actually reached the channel's full/rendezvous wait. An enlarged or unbounded
channel can reach the expected count, allow the sink to resume on that schedule,
and pass before another claim exposes the excess capacity. The Go consumer's
explicit 150 ms extra-start window adds a finite negative observation, not the
causal blocked-worker evidence required by the root checklist. Swift's updated
unit-test comment at lines 584-588 correctly acknowledges this limitation.

Fix: add private observations at the actual channel wait and send-return
boundaries; hold the sink until the required workers have reached blocked send
paths for buffer0 and buffer1. Check that a blocked worker cannot claim again
until receipt, then release, drain and verify final accounting. Preserve public
consumer progress/order tests as a separate proof class. Do not substitute a
longer sleep for this witness. This is a test-oracle gap, not evidence that the
inspected production channels are unbounded.

### R2-2 — P2: the installed Swift example can miss omitted transition observers

Locations: `examples/native-campaign/swift/Sources/CampaignConsumer/HostChecks.swift:74-79`,
`Fixture.swift:88-97` and `Fixture.swift:130-133` in that same directory.

The installed host comparison checks generated, applied and enabled inputs, but
not observer counts/order. Assertions inside the transition observer run only if
the callback is invoked, and canonical Rust callbacks intentionally filter all
`observer_` events. Removing transition-observer invocation from the campaign
path can therefore preserve fuzz reports and compared callbacks while bypassing
the example's snapshot checks. The unit suite does contain observer-count/order
checks; those do not make this external application a fresh-installed witness of
that behavior.

Fix: compare per-job observer events/counts/order against each fresh public
bare-run reference and assert expected callback counts independently. Preserve
native observer evidence separately from the Rust protocol projection; do not
invent Rust observer entries or normalize the known N05 mismatch.

### R2-3 — P2: focused drivers do not bind the executing driver to the sealed copy

Locations: `validation/native-parity/n06/go/qualify.py:329-338` and
`validation/native-parity/n06/swift/qualify.py:198-211`.

Both drivers verify the supplied snapshot, including a sealed copy of the driver,
without requiring that the currently executing `__file__` is that copy or matches
its sealed bytes. Running a later or different `qualify.py` against an older,
otherwise valid snapshot can produce a receipt naming the older source identity
while different command/validation logic actually runs. Go's nested guard command
also explicitly uses the executing `__file__`, not the snapshot driver.

Fix: before any qualification subprocess, verify the canonical executing driver
path and content against its exact sealed member, and record the driver's own
invocation and identity. If an outer launcher is intentional, bind and record
that launcher separately and explicitly invoke the sealed inner driver. Recheck
the executing identity at the final receipt boundary. Snapshot-only verification
does not establish which Python program interpreted it.

### R2-4 — P2: Swift SIGTERM does not enter owned-child cleanup or final receipts

Locations: `validation/native-parity/n06/swift/qualify.py:156-188`, `:252-282`.
Reviewed driver hash: `577b95d6b577860988c4a2592d3c783a7b44094c742fb1e01a138513ede0e0ed`.

The driver starts each command in a separate session and cleans up its owned
group on TimeoutExpired or a Python exception, but installs no handler for
SIGTERM/SIGHUP. A catchable termination signal to the Python driver takes the
default process-termination path, so its `except`/`finally` blocks do not run and
the separately grouped compiler/test/consumer can survive without a terminal
command/failure receipt. The implemented timeout cleanup is a distinct, useful
fix; it does not close this signal path.

Fix: turn catchable termination signals into a recorded interruption, protect the
Popen-to-handle handoff and cleanup against repeated signals, terminate/reap only
that command's owned group, and persist interruption and final identity evidence.
Do not claim SIGKILL/process-loss recovery. The later Go driver already contains
the corresponding signal/deferred-cleanup mechanism.

### R2-5 — P2: Go command tool identities are not enforced through execution

Locations: `validation/native-parity/n06/go/qualify.py:339-350`, `:370-371`,
`:388-412`.

The driver resolves/hashes Go and Python once, then launches Go commands using
the bare `go` name. It does not compare tool identities at completion, and the
race phase enables cgo without pinning the C compiler executable. A PATH target
or tool installation change between observation and a later command can leave a
successful receipt identifying a different Go/compiler image from the one used.
The source and installed-package final seals do not detect this tool drift.

Fix: invoke the pinned absolute executable paths, record the selected C compiler
and relevant toolchain configuration for the race phase, and compare tool
path/type/mode/content identities before and after. Preserve failures on a tool
mismatch. The existing safe environment whitelist and command environment/log
records are useful and should remain.

## Explicit round-1 dispositions

1. **Independent worker-loss equality and early Finish — source-resolved in both.**
   Go `campaign_lifecycle_model_test.go:53-94` derives admission from the ledger;
   line 260 checks `core.lostWorker == ledger.lostWork`. Lines 662-692 first
   receive a ModelError, fabricate loss on `lostWork=false`, and check both loss
   corruption and premature Finish, including an outstanding sink-return control.
   Swift `CampaignLifecycleTests.swift:39-44`, `:229-231`, `:461-550` checks direct
   loss equality, ledger Finish eligibility, fabricated/cleared loss after
   ModelError, and both incorrect admission and incorrect rejection. Existing
   duplicate-Deliver BFS/fuzz/shrink/record/read/replay and strict/relaxed healthy
   controls remain in the fixtures. These are authored tests, not passing runs.

2. **Go feedback diagnostics before lease revocation — source-resolved.**
   `go/campaign_callbacks.go:80-118` revokes the shared FeatureBuffer lease before
   diagnostic extraction, including copied handles and unwind cleanup.
   `campaign_review_test.go:105-149` covers initial and transition callbacks,
   late Push rejection and unreachable hostile Clone. The later campaign-local
   `campaignPanic.ownedError` at `campaign.go:460-466` also snapshots/restores panic
   provenance around diagnostic reentry; tests at `campaign_review_test.go:31-103`
   cover an existing genuine panic versus a diagnostic-only panic. Direct hostile
   Error formatting remains contained. No N05 callback implementation was edited
   by this reviewer, and these source fixes still need execution.

3. **Native count disclosure and consistent boundary witnesses — source-resolved;
   full Rust64 support remains open.** Go documents the signed-int intersection
   in `N06-GO.md:80-95` and `go/CAMPAIGN.md:93-96`; its codec test at
   `campaign_lifecycle_codec_test.go:436-453` changes requested and not_started
   together and requires the representation-specific error. Swift now documents
   the same distinction in `N06-SWIFT.md:124-132` and its codec header. Its test at
   `CampaignLifecycleTests.swift:875-911` admits the consistent Int.max initial
   state and rejects the internally consistent 2^63 state specifically as beyond
   native Int, while full-u64 Summary counters and IDs roundtrip. Neither native
   count representation supports all Rust64 usize states. This is not malformed
   lifecycle input and must not become a full-u64 interchange pass.

The earlier Swift missing-host-test list is also **source-addressed** at runtime
test hash `c9b12c0b...`: the 30 authored tests now include first/later start
failure, after-drain lost claims, exit failures after all outcomes, first sink
error/panic versus later worker errors, positive factory/feedback deadlines,
already-claimed work after property stop, callback order, actual worker identity,
and non-Sendable worker-local state/output/features. Their compilation and
execution are pending; R2-1 remains a distinct stronger channel witness.

## Runtime integration and dependency boundaries

The inspected Go and Swift runtimes still drive their actual private reducer for
Claim/Complete/Receive/SinkReturned/WorkerFailed/Finish. Complete precedes send;
receipt frees a sender before sink return; reports retain receipt order; workers1
uses the caller and configured workers>1/jobs1 uses a spawned worker. Join/lost
outcome observation follows channel drain. Worker errors alone retain delivery,
and the first sink error remains authoritative. Job errors do not activate
property-stop. Host policies preserve worker-local model ownership and explicit
report-input transfer without requiring a canonical ModelCodec.

The Swift external example's 49 projections mean 15 seed, 15 validation and 19
campaign rows. Its 14 host groups include one native-policy group containing 13
cases. These counts describe authored coverage, not passed assertions or exact
Rust/native agreement. Source drift in several projection/policy files is listed
below. The root owns actual public Rust comparison and installed receipts.

**OPEN N05 dependency:** Swift feedback-metadata CampaignPanic normalization
still yields ModelError with present model metadata and incomplete accounting,
where Rust produces Panicked. The actual public guided API and the dedicated
Swift test preserve that result. There is no request to silently change N05 or
rewrite expected/output rows. Other context-diagnostic differences added to
INTEGRATION during this review remain separately catalogued by the root.
Arbitrary Swift traps/deinit recoverability is neither required nor claimed.

`Cargo.toml` adds only the three fixture package-include entries. The inspected
production Rust authority hashes remain identical to the base. Swift's tracked
Explore/Fuzz/Guided changes are the four additive conditional Sendable conformances.
Source review does not establish baseline regression preservation at runtime.

## Focused-script corrections and concurrent changes

The later Go driver source addresses full membership/type/permission-mode/content
sealing, the same bytes for manifest hashing/parsing, startup failure receipts,
final identity rechecks that fail the result, installed-artifact observations,
raw command logs, and catchable-signal owned-group cleanup. Swift similarly
compares complete source/installed membership, types, modes and contents and
records tool identities before/after. Its later timeout implementation retains
the child identity until group signalling is finished. These are source findings;
none of the guard/fault tests or process-cleanup paths was executed here.

The script subreview reported two transient findings that are **not open at the
parent's later inspected cutoff**:

- Go called `guard-tests` before its CLI accepted the mode. The later driver
  implements `guard_tests`, includes the mode in argparse, and dispatches it
  (`62303a2c...:419-581`). This closes the missing-dispatch finding only; the new
  guard suite was not executed or fully rereviewed.
- Swift previously used lexical `absolute()` output separation. The inspected
  `577b95d6...:125-130` resolves source and output before containment checks and
  directory creation, closing the observed symlink-alias case.

No reviewer waited for authors or Kyoto admission. Further edits require their
own source review and bound execution; they are not silently covered by this file.

## Compact identity manifest and cutoff

SHA-256 values below identify the principal inspected source, not an atomic
package freeze. Runtime/test files were read at these identities; Go's later
diagnostic additions were reread. The final Go qualifier reread covered the
finding locations and CLI dispatch only. Swift snapshot.py's last observed
change is explicitly unreviewed below.

```text
4d4da9479fe3512589df379cba9a1f3e5df989bba6920e3691479e6b3ef19005  src/campaign.rs
7931c3ef82bf7f2b806448ef8a65f8f2e4a136d38c58d8e06307ee4b090ef44a  src/campaign/lifecycle.rs
a93ee1cf524339743564923dacd6d8aac007e95d9ecd68e00f5f4e08de8b579d  src/campaign/lifecycle/codec.rs
3102ec704a8757f339d558e93faf98c82499076ffc94d8177551aba2af582b3a  src/campaign/lifecycle/tests.rs
bbc7b214510f862738991074f8f2053389c4cac85caf005ebc129df9b06a18d7  src/guided.rs
496c19c7181a6bf3f2af97d3e04df1d3c7b22c241351104b4cd66201f6706ecb  src/explore.rs
e69a80ad96826a27871807f848e22fd213a7bee9f95d76be92069e68504f5beb  src/model.rs
ec19cf028645dd16f6f389cc7fa0fa3a44a72a02838842a906ca11bb2303649b  src/operations.rs
7a26f414293241e0d9ef126e0ac9735bfddb2e2764612d95f0e3780c134e9ec1  go/campaign.go
946250e5ebe55c65a20bbb958e9698ebcba6e1c09efd0c2f7a74befef9d4286d  go/campaign_callbacks.go
b3cc2da75a3d07799e1bd045ed953f8260e18d5b868dd59d3dfceee136f20927  go/campaign_lifecycle.go
f3b044674d5d1acfae44ed025b76cead79b38809a3043c2f9dd0f27f424232f6  go/campaign_lifecycle_codec_test.go
529957e437c20a385c5c9f50efb8d6b5b85196590359178b041e8fa0ec41c4bb  go/campaign_lifecycle_model_test.go
5f95198b6243bbbd23efdb6d46af06cae7ec4cda66edb278523eded37988c894  go/campaign_lifecycle_test.go
6c0b065d9ed5587e644ad16f8c40e696000b2512273b89f5d702e059b7a6359f  go/campaign_review_test.go
112b6166be9cd744f2a5154dcbb486f6ca5b96f8d7d18933df42c843f4c2581d  go/campaign_test.go
3fd338b59ddc1c7f4b66db003b9996caf458b3635f90247c3e2be960e1ff52ea  swift/Sources/Statelessness/Campaign.swift
598fab867564f79c350a7ea4bf170a3be0adbb8ae13e5483f914ec6a3a4c827f  swift/Sources/Statelessness/CampaignLifecycle.swift
082d2967d52fff1238221520682dfbed59b4633c58c4ef10555d4a4561fbcb0d  swift/Sources/Statelessness/CampaignRuntime.swift
a450dbc9a47a4e7cbffecd3fa1908e76826f63632cf03df100c123e7bfc56b43  swift/Tests/StatelessnessTests/CampaignLifecycleCodec.swift
818301d9013af257c54a5d018c6a52242cbeed7a2b297721b2da8146bb784d28  swift/Tests/StatelessnessTests/CampaignLifecycleTests.swift
c9b12c0b40a20b706f48a395603011b9f1724ce9dfdcf7083ea5f372616940aa  swift/Tests/StatelessnessTests/CampaignRuntimeTests.swift
94366fbfc61f6427006447147f505ae053695a5b560a2ba1a292337a9d1f4cda  examples/native-campaign/swift/Sources/CampaignConsumer/HostChecks.swift
500af31e9291a6079ad12e05b32f7216d667bab656b922f5f47ffd22e29de8c8  examples/native-campaign/swift/Sources/CampaignConsumer/Fixture.swift
782f6570d812d0a6ec0464bb46d7cb765eb7e184b90701d18fde686e06ad50db  examples/native-campaign/go/main.go
2cee4c99eb9115d17515ada092bf86a2af14545b62e584d251fb7876fb7fa872  scripts/native-campaign-consumers/go/host.go
62303a2c4baba2a5674dcec9d31e2f51f77ad64994792cc3a1735911e3f7a65f  validation/native-parity/n06/go/qualify.py
577b95d6b577860988c4a2592d3c783a7b44094c742fb1e01a138513ede0e0ed  validation/native-parity/n06/swift/qualify.py
92eb34d32fec813737753cbdc95ae324eb2f35527172f840b699c44e779ab0ba  validation/native-parity/n06/swift/snapshot.py (reviewed version)
b5cc32ecad4413f0f9952bd52388d6925c59b5031da3668aa8aa4467f2e39dff  go/go.mod
a2b61ffbfbe9d9786adc0f54546352bac5908bfd0e8076a0a0df9681ea887af4  Package.swift
4598c1c167f1cbe72c38ef165c0b4e1bfec5aa57ed65ef23d70185286b1627fc  Cargo.toml
5ee879de57d4f490da191ceef6d09cade3e739f9303a6a46fdfc346498fae270  docs/native-parity/N06-REVIEW-GO-SWIFT-R1.md
fdcb53eb2cccf0a7eff89ae02e92232496687797df1e105a7e57cf1395737829  docs/native-parity/N06-INTEGRATION.md
a12d67480d8a3ad79609a231b9513217e2d089805e20b5973dc77bac8c4081a3  docs/native-parity/N06-REVIEW-CHECKLIST.md
```

Active drift observed during this round:

- Go campaign.go/callbacks/review tests moved from `ca824cb2...` / `a1229236...` /
  `1159a928...` to the inspected identities above. The new diagnostic-provenance
  code/tests were reread; earlier transient test edits are not final findings.
- Go qualifier moved from `d4bb40fd...` through `38da5131...` and `22814bda...` to
  `62303a2c...`. Swift qualifier moved from `73759848...` to `577b95d6...`; the
  changed path/timeout handling and the remaining finding sites were reread.
- Swift snapshot.py subsequently became
  `01adb65018ac49a0cf6f4a24b1be5dd414c0cac300d884ee20d731ffee866afc`.
  That ending version was observed but not rereviewed.
- INTEGRATION moved from `6e494ce8...` to the manifest value. Its appended
  dependency paragraph was read. N06-GO.md also changed; the count-domain and
  callback policies reviewed above are not a review of every later prose edit.
- Swift example README/Cases/NativePolicy/Projection changed after the delegated
  review read them. Ending observations, **not full rereview identities**, were:

```text
5dbb67364b48673357de17e1af15c4ab5cece3fff53b284f77d6ae631db59e39  examples/native-campaign/swift/README.md
bee62cb220ef78302998196b8e37d2b8129b97c2e28b1b8c9123499c236d25bf  examples/native-campaign/swift/Sources/CampaignConsumer/Cases.swift
c8cc268a50ba31e44bfbc5bb0cb39752c97398d58d0c2835d47ddb6340921404  examples/native-campaign/swift/Sources/CampaignConsumer/NativePolicy.swift
14ef7d4e8dc7a1aaf2ff09048427da387bda20849f99383e74e1b2e84462536c  examples/native-campaign/swift/Sources/CampaignConsumer/Projection.swift
```

Principal read-only identity commands, from the owned checkout:

```sh
pwd
git branch --show-current
git rev-parse HEAD
git status --short
git worktree list
shasum -a 256 src/campaign.rs src/campaign/lifecycle.rs src/campaign/lifecycle/codec.rs src/campaign/lifecycle/tests.rs src/guided.rs src/explore.rs src/model.rs src/operations.rs
shasum -a 256 go/campaign*.go swift/Sources/Statelessness/Campaign*.swift swift/Tests/StatelessnessTests/CampaignLifecycle*.swift swift/Tests/StatelessnessTests/CampaignRuntimeTests.swift
shasum -a 256 validation/native-parity/n06/go/qualify.py validation/native-parity/n06/swift/qualify.py validation/native-parity/n06/swift/snapshot.py
git diff -- Cargo.toml swift/Sources/Statelessness/Explore.swift swift/Sources/Statelessness/Fuzz.swift swift/Sources/Statelessness/Guided.swift
```

No test/build command or package was executed or produced by this review. Final
membership/source freeze, fresh installed execution, public Rust comparison,
baseline regressions and full failed/passing logs remain with their respective
owners and existing Kyoto jobs. Admission pending is not execution.

### Final observation after writing this document

The reviewed Go runtime/callbacks/runtime tests and Swift runtime/runtime tests/
HostChecks hashes above were unchanged. The shared branch remained
`t3/native-campaign-runtime`, but HEAD advanced externally to
`2bcdd8bfd19744f50b6ed3819ac2451a8de101be` while this review was being written.
This reviewer did not stage or commit anything; the review still concerns the
source identities listed above, not blanket approval of that new commit.

The Swift qualification driver additionally changed to
`0da3c9d516df86f45c9d0c7a1651061aea20dfd3c0efb540c910ffec843847e7`.
That new version was observed but not rereviewed. R2-3/R2-4's Swift observations
remain bound to `577b95d6...`; no assertion is made that the later version retains
or fixes them. The review document's whitespace check reported no errors.
