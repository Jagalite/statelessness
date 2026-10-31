# N06 Go/Swift independent source review, round 1

Round 1 source review completed on 2026-10-22. This document is the only file owned by this
review. Checkout `/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336`, branch
`t3/native-campaign-runtime`, observed HEAD
`0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`.

No runtime/test edits, builds, tests, qualification commands, job submissions,
staging, commits, or other-checkout writes. Reading, Git inspection and SHA-256
hashing are source observations only. The Kyoto skill was read; no executable
qualification was authorized for this review.

The N06 plan section, GUIDED-CAMPAIGN-AUDIT.md and
GUIDED-CAMPAIGN-REVIEW.md were read fully, together with N06-INTEGRATION.md,
N06-REVIEW-CHECKLIST.md and N06-RUST-CONTRACT.md. Actual Rust campaign/lifecycle,
test codec/self-model, guided/explore/model/operations sources govern the review.
Existing N05 callback normalization is treated as an explicit foundation
boundary. N05 author90 final qualification remains pending; this review does not
restart its job or infer a FREEZE/pass. N10 and generic aggregate merge are out
of scope.

## Source observations

The production Go and Swift coordinators dispatch to their actual private
lifecycle reducers. The inspected worker paths complete before blocking send,
release senders on channel receipt before sink completion, retain receipt order,
use actual workers for configured workers>1/jobs=1, and defer worker-loss
observation until after drain. Worker-only errors do not suppress delivery;
the reducer keeps a first sink error authoritative over drained completions and
worker failures. These are source findings, not runtime qualification.

The direct, previously reported Go arbitrary `err.Error()` panic classification
case is addressed by `campaignOwnedError`: owned ModelError text is copied
directly and arbitrary diagnostics run under recovery. The reentrant feedback
diagnostic case below was a separate defect; a later source edit addresses its
lease ordering, with execution still pending. The Swift conditional Sendable
extensions are in the files defining Failure, FuzzReport, CorpusEntry and
GuidanceReport. Both resolutions still require source-bound execution.

The independent lifecycle fixtures use the runtime core, independent event
ledgers, separate predecessor admission checks and deliberate duplicate-Deliver
faults. They are not merely comparisons of two calls to the reducer. Their
authored BFS/fuzz/shrink/record/read/replay paths are not execution receipts.

## P2 — Go and Swift independent ledgers can miss fabricated worker loss

**Locations:** `go/campaign_lifecycle_model_test.go:146-219` and
`go/campaign_lifecycle_test.go:127`; the same omission exists at
`swift/Tests/StatelessnessTests/CampaignLifecycleTests.swift:194-218` and
`swift/Tests/StatelessnessTests/CampaignLifecycleCodec.swift:87-88`.
Reviewed hashes are `f2a9adb5...`, `5f95198b...`, `2759a11c...` and
`d5476af9...`, respectively, expanded in the supplemental manifest below.

**Scenario:** claim two jobs; complete, receive and successfully acknowledge a
ModelError for the first, leaving the second pending. Accounting is already
incomplete. Inject a faulty WorkerFailed(error, lostWork=false) transition that
sets the production state's lostWorker=true anyway. The ledgers correctly
retain lostWork=false, but neither fixture compares that fact with the core's
lostWorker flag. Their accounting check remains false because of the earlier
ModelError, so the corruption passes. Finish can then be accepted with the
second job pending: both predecessor admission checks trust the corrupted
core.lostWorker, and final audit checks only keep accounting incomplete.

**Impact:** a fabricated worker-loss/early-Finish regression can pass the
independent checks. This is a concrete test-oracle defect, not an observed
production-runtime defect. The Go independent reviewer identified the case;
the parent source review confirmed the corresponding Swift omission.

**Fix:** assert core.lostWorker equals ledger.lostWork, and validate Finish
eligibility from independently recorded claims/receipts, active sink returns
and genuine lost-work events. Add a corruption control for this sequence after
an earlier ModelError, proving both the flag corruption and premature Finish
are detected. Preserve the existing duplicate-Deliver discovery/shrink/replay
control. This does not require a production core or N05 change.

## P2 — Go feedback diagnostics run before lease revocation

**Disposition:** observed in the original wrapper; source-only fix inspected
later in this round. Not an outstanding defect in the inspected replacement.

**Location:** `go/campaign_callbacks.go:68` and `:73`, with
`go/campaign.go:411-423` and `:436-439`. Reviewed callback wrapper hash:
`894240ccadc46705818f5bf56b2de7eda05fd208d244becfa3f2db0be6f2b984`.

**Scenario:** InitialFeatures or TransitionFeatures returns an error retaining
its `out` buffer. The new wrapper immediately calls `campaignOwnedError`, which
invokes that error's application-defined `Error()` before returning to the
guided driver. The driver's revocation at `go/guided.go:205-209` has not happened
yet. `Error()` can therefore call `out.Push` successfully and invoke Key.Clone
after the feedback callback has already returned. If Clone panics, its campaign
guard sets the job's sticky `campaignPanic.seen`; `copied` contains the panic,
but `campaignExecute` subsequently replaces the returned ModelError with
Panicked at `go/campaign.go:313-315`. Even without a panic, the late Push invokes
an application clone callback that an expired lease must reject.

This is a new campaign-wrapper regression. The direct public N05 guided path
revokes this feature lease before its `call` boundary formats a returned error;
the fix must preserve that existing behavior. Merely recovering a direct panic
from Error(), as the current helper does, does not cover reentrant guarded
callbacks.

**Fix:** ensure application-error diagnostics execute after feature-lease
revocation, using a campaign-local lazy safe error wrapper or an explicit
campaign wrapper boundary that revokes the supplied lease before normalizing
the returned error. Preserve the application's original error category and
callback-error precedence. Add a public guided-campaign regression whose error
formatter tries Push, whose Key.Clone would panic if reached, and assert an
expired lease, no clone invocation, ModelError with metadata present, and normal
drain/accounting. Exercise both initial and transition feedback. No N05 core
change is required by this finding.

The replacement at `go/campaign_callbacks.go:80-96` explicitly expires the
shared feature lease before calling campaignOwnedError and also defers expiry
for panic unwinding. It clears the lease's pointer to the driver's scratch
storage without clearing the stored observations. Both retained and copied
handles therefore expire before diagnostic callbacks. Inspected replacement
hash: `a1229236f6bb90976d589722178a40ca9fd45d6219e4cfe6821f2dd64072400d`.
`go/campaign_review_test.go:30-73` adds initial/transition hostile-diagnostic
regressions. These tests were read, not run; their existence is not a pass.

## P2 — Document and test the narrower native job-count domain

Go `CampaignConfig.Jobs` and the seven CampaignReport job counts are `int`
(`go/campaign.go:14`, `:100`); Swift uses `Int`
(`swift/Sources/Statelessness/Campaign.swift:8`, `:106`). Their test codec count
readers reject values above the signed host maximum
(`go/campaign_lifecycle_codec_test.go:425`,
`swift/Tests/StatelessnessTests/CampaignLifecycleCodec.swift:338`). Job IDs,
seeds, Summary counters and aggregate work totals retain full u64. The Rust
reference's usize count decoder accepts the full u64 range on 64-bit hosts
(`src/campaign/lifecycle/codec.rs:302`).

**Scenario:** a Rust64 campaign with first_job=0, jobs=2^63, cases=0 is valid
without allocating all jobs; its initial lifecycle state cannot be represented
by these native count fields. This is a host-domain limitation, not evidence of
an overflow on admitted native configurations. N06-SWIFT.md:111 describes a
matching Rust private codec without this restriction; N06-GO.md lists `int`
fields but does not state the interoperability limit. The checklist explicitly
requires documented host limits and full-u64 codec preservation.

**Fix:** explicitly document the native count domain and codec interoperability
intersection, and add consistent boundary accept/reject witnesses. The Swift
`hugeReport` test at CampaignLifecycleTests.swift:723 only changes requested,
leaving an independently inconsistent state, so it cannot establish whether
rejection came from representation or lifecycle validation. Keep the full-u64
fixture gate open unless a separate wire representation supports it. If full
Rust64 job-count parity is required, keep that as a separate N06 representation
change; changing accepted N05 fuzz count types is not requested by this finding.
Do not silently coerce large counts.

**Supplemental disposition:** Go now explicitly documents this intersection
and unsupported disposition in N06-GO.md:78-95 and go/CAMPAIGN.md:93-96, with
new native-count boundary tests in campaign_review_test.go and
campaign_lifecycle_codec_test.go. Swift now states the API Int limit at
N06-SWIFT.md:51-54, but its codec paragraph at :122-124 still says it matches
the Rust format without specifying the count intersection, and the inspected
fixture still uses the inconsistent hugeReport witness. Thus Go's disclosure
is addressed in source, Swift's is partial, and Swift's consistent codec
boundary vectors remain open. Full-u64 count support is not established in
either language. New tests and documentation have no execution qualification.

## Unfinished tests and execution gates, not demonstrated runtime defects

At the reviewed Swift runtime-test snapshot (`cab2202f...`), the authored tests
cover admission/range/seeds, caller versus spawned jobs1, ordinary/guided
determinism, factory cancellation ordering, metadata/error categories and
teardown replacement. That snapshot does not yet exercise its worker hooks for
after-drain lost outcomes/start failures, first sink error versus later worker
failure, nonzero factory/feedback deadlines, or property-stop with already
claimed work. The installed Swift consumer separately authors actual-worker,
out-of-order, rendezvous, slow-sink and active/queued-drain scenarios in
`scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/HostChecks.swift`.
It is source-only evidence here; it does not supply the missing host-fault tests.

Go runtime tests appeared during the review and include real-worker identity,
out-of-order callbacks, complete-before-send hooks, rendezvous/slow-sink drain,
error diagnostics, worker failure after draining, stop-on-failure and input
detachment before cleanup. Review membership/hashes below delimit this growing
suite; no claim is made that a newly appended test passed or that the final
suite is complete.

Swift CampaignRuntimeTests.swift grew after the snapshot above, reaching
`ce137d2c...` at the supplemental cutoff. The earlier missing-test list describes
the reviewed snapshot only; it is not a claim that those tests are still absent
from that later unreviewed version.

Required before closure: finish/review the host fault/deadline tests, freeze the
final package/test/consumer membership, run the actual public Rust comparison,
execute the independent lifecycle/fault fixtures and real host tests, and run
fresh installed consumers and baseline regressions through shared-default Kyoto.
Retain source/package identities, effective commands and complete failed/passing
logs. Admission, compilation, focused deterministic agreement and full runtime
qualification remain separate. The Go/Swift resolution checks in this document
make no claim about the separately assigned Python/TypeScript fixes.

## Source identity and active drift

The first observation had six Go campaign files, three Swift campaign runtime
files, and only CampaignLifecycleCodec.swift among the new Swift campaign test
files. During review, Go campaign_test.go and Swift CampaignLifecycleTests.swift
and CampaignRuntimeTests.swift appeared. Go campaign.go, campaign_callbacks.go,
both Go codec/model test files, Swift CampaignRuntime.swift and the Swift codec
changed. This is concurrent authorship, not reviewer modification. No polling
for implementation completion is part of this review.

First observed SHA-256 values for initial campaign membership:

```text
64de75d4bf8f377ddaf9502911aba03795c93792134eababaaf8278b6e7dc500  go/campaign.go
04d6c21df708cef58dcc5e2086ce2ad66ba288ac573994291dc08a7fa49152b4  go/campaign_callbacks.go
8ab53ac15e283964f3a6e161f334ce8e2a90a515c3f96ed7ad1aad39f63576b9  go/campaign_lifecycle.go
f1bc425b9a5de010c258a87220abcf8762477b63086b7a5d69bd497e99deb570  go/campaign_lifecycle_codec_test.go
44aa0e4fccf2bf9aaceb557988a1eb38f36d0c128d3a99aea283746bfe109137  go/campaign_lifecycle_model_test.go
1e7ca6a4dcac61099a3f64a383a4d62c0820fb067ae5f8aa3ee161e2700b384b  go/campaign_lifecycle_test.go
3fd338b59ddc1c7f4b66db003b9996caf458b3635f90247c3e2be960e1ff52ea  swift/Sources/Statelessness/Campaign.swift
598fab867564f79c350a7ea4bf170a3be0adbb8ae13e5483f914ec6a3a4c827f  swift/Sources/Statelessness/CampaignLifecycle.swift
7ddade2f9efec61af426de65a64fb10ac40d8a4b6a08ce99f68974d9bc4b3c2d  swift/Sources/Statelessness/CampaignRuntime.swift
cc6859c5d97b5cf117d729ace9746fbf20f329d7c9912048d76551cc72365d40  swift/Tests/StatelessnessTests/CampaignLifecycleCodec.swift
```

Rust authority and source-package manifests observed during review:

```text
4d4da9479fe3512589df379cba9a1f3e5df989bba6920e3691479e6b3ef19005  src/campaign.rs
7931c3ef82bf7f2b806448ef8a65f8f2e4a136d38c58d8e06307ee4b090ef44a  src/campaign/lifecycle.rs
a93ee1cf524339743564923dacd6d8aac007e95d9ecd68e00f5f4e08de8b579d  src/campaign/lifecycle/codec.rs
3102ec704a8757f339d558e93faf98c82499076ffc94d8177551aba2af582b3a  src/campaign/lifecycle/tests.rs
bbc7b214510f862738991074f8f2053389c4cac85caf005ebc129df9b06a18d7  src/guided.rs
496c19c7181a6bf3f2af97d3e04df1d3c7b22c241351104b4cd66201f6706ecb  src/explore.rs
e69a80ad96826a27871807f848e22fd213a7bee9f95d76be92069e68504f5beb  src/model.rs
ec19cf028645dd16f6f389cc7fa0fa3a44a72a02838842a906ca11bb2303649b  src/operations.rs
a2b61ffbfbe9d9786adc0f54546352bac5908bfd0e8076a0a0df9681ea887af4  Package.swift
b5cc32ecad4413f0f9952bd52388d6925c59b5031da3668aa8aa4467f2e39dff  go/go.mod
```

The following ending observation is a review cutoff, not an immutable execution
freeze. Core/codec files matching a previous observation were rechecked at these
identities. Runtime tests were newly added during this round. The Swift fixture
review additionally observed CampaignLifecycleTests.swift changing from
`438e83ee49ee3da2608014a3468a414f1fad7968e4c3155ec08202cb19852fd0`
to the ending hash; its ledger/codec/fault assessment covers that interval.
Later author edits need their own review and source-bound execution.

```text
f040e5b634cecbceb00322c97122e6145f071a375fba5e69999df7c65b84f4bd  go/campaign.go
894240ccadc46705818f5bf56b2de7eda05fd208d244becfa3f2db0be6f2b984  go/campaign_callbacks.go
8ab53ac15e283964f3a6e161f334ce8e2a90a515c3f96ed7ad1aad39f63576b9  go/campaign_lifecycle.go
1f00b57dd0bbc73d2cf207fca683155a0ae445bc9ab776122a778ed3848bd56e  go/campaign_lifecycle_codec_test.go
868ffd0827b1bcaf102ad80b44c4ea8d37942ebb9033f7a4f5ecefd90b313afc  go/campaign_lifecycle_model_test.go
1e7ca6a4dcac61099a3f64a383a4d62c0820fb067ae5f8aa3ee161e2700b384b  go/campaign_lifecycle_test.go
860c8a901d78f028aa513a1070d618acb8d684daa583d95364a4c636e1ccaee5  go/campaign_test.go
3fd338b59ddc1c7f4b66db003b9996caf458b3635f90247c3e2be960e1ff52ea  swift/Sources/Statelessness/Campaign.swift
598fab867564f79c350a7ea4bf170a3be0adbb8ae13e5483f914ec6a3a4c827f  swift/Sources/Statelessness/CampaignLifecycle.swift
082d2967d52fff1238221520682dfbed59b4633c58c4ef10555d4a4561fbcb0d  swift/Sources/Statelessness/CampaignRuntime.swift
c18ac7a95d800004d15200fec69878ffe8be6124e919eb54e44456f3131f8c9e  swift/Sources/Statelessness/Explore.swift
496e6d7114eaf5fad7cf8978d2bd4fb480ca8f938d256687bcf49ef615852102  swift/Sources/Statelessness/Fuzz.swift
87f69f8144004490a913230c0b34c676a5d1e3e7b491aba184e9614671237c46  swift/Sources/Statelessness/Guided.swift
d5476af9fe0113f137af00a31034b8cfa4f7fec164386408df0cad8080a322b7  swift/Tests/StatelessnessTests/CampaignLifecycleCodec.swift
2759a11cd1f408ef965b69f09f8d8b1dce5247b8a30f31a81f2a36a4627cacb4  swift/Tests/StatelessnessTests/CampaignLifecycleTests.swift
cab2202fcdb41185550789c46e1725194c5732afd7ef8b141d55fca7e3d2998f  swift/Tests/StatelessnessTests/CampaignRuntimeTests.swift
```

Explore.swift/Fuzz.swift/Guided.swift had these same hashes at the start. Their
diff from N05 consists of the four additive conditional Sendable conformances.
CampaignRuntime.swift's inspected change expanded the runner result binding
without changing the worker/channel protocol. The original Rust campaign and
lifecycle hashes remained unchanged. HEAD and branch also remained unchanged at
the ending observation.

Additional inspected Swift installed-consumer source identities (no installed
artifact was produced or executed by this reviewer):

```text
b904826dde3f67a42866f4369ea20b1eb39728a6f2dfe569803d1c16c087801c  scripts/native-campaign-consumers/swift/Package.swift
98303ddcf90a9026af5eeb24e43886d57a465269594a749cd499c024a7228b68  scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/CampaignConsumer.swift
ffd624efe8eff188e0c52774763ce96ed353dc4ec18eaa5740351f2d8dd9e8c1  scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/Deterministic.swift
5e603ce59026ed77fc84cf0b2b6fbb1e57b3eb93ac8ee6331adc7d7d2770471c  scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/HostChecks.swift
07bfc90238811fb2fdbae817bc3ac974d30878684795f90ed109b69c58392e37  scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/Projection.swift
57c2ca611a7f7464a9a5e3973e04df905a0cefcbb179c317831da473a48a3c71  scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/Support.swift
```

The exact principal identity commands, run from the owned checkout, were:

```sh
git rev-parse HEAD
git branch --show-current
git status --short
git worktree list
shasum -a 256 go/campaign*.go swift/Sources/Statelessness/Campaign*.swift swift/Sources/Statelessness/Explore.swift swift/Sources/Statelessness/Fuzz.swift swift/Sources/Statelessness/Guided.swift swift/Tests/StatelessnessTests/Campaign*
shasum -a 256 src/campaign.rs src/campaign/lifecycle.rs src/campaign/lifecycle/codec.rs src/campaign/lifecycle/tests.rs src/guided.rs src/explore.rs src/model.rs src/operations.rs
shasum -a 256 Package.swift go/go.mod
```

## Supplemental cutoff after both independent reviews returned

Both delegated reviews completed without writes or execution. The Go review
confirmed the diagnostic-lease and worker-loss-oracle findings and excluded
earlier missing finalization/malformed-summary checks that concurrent authors
had added. The Swift fixture review identified the native-count gap. The parent
then checked the Go lease fix and confirmed that the worker-loss-oracle case
also applies to the Swift fixture.

Additional author changes continued during this bounded follow-up. The following
hashes are observed membership at the supplemental cutoff, not a claim that all
new lines were reviewed, an atomic freeze, or a qualified package. In particular,
later Go runtime additions and the expanded Swift runtime tests need a further
source review; the ledger and Swift codec hashes associated with the new finding
were stable across the follow-up reads. No polling for author completion occurred.

```text
ca824cb298380cc071f96ab11c32d90b9ff89833ca09bbb949765a28adb6f14a  go/campaign.go
a1229236f6bb90976d589722178a40ca9fd45d6219e4cfe6821f2dd64072400d  go/campaign_callbacks.go
b3cc2da75a3d07799e1bd045ed953f8260e18d5b868dd59d3dfceee136f20927  go/campaign_lifecycle.go
f3b044674d5d1acfae44ed025b76cead79b38809a3043c2f9dd0f27f424232f6  go/campaign_lifecycle_codec_test.go
f2a9adb5a309036470e0b47b872cd30042c1a9fc6b2692b3d6156526778eb495  go/campaign_lifecycle_model_test.go
5f95198b6243bbbd23efdb6d46af06cae7ec4cda66edb278523eded37988c894  go/campaign_lifecycle_test.go
1159a928da572ea1a1377b1f8643780879355fcfa09ee3ff8d9b62cf287d3679  go/campaign_review_test.go
112b6166be9cd744f2a5154dcbb486f6ca5b96f8d7d18933df42c843f4c2581d  go/campaign_test.go
3fd338b59ddc1c7f4b66db003b9996caf458b3635f90247c3e2be960e1ff52ea  swift/Sources/Statelessness/Campaign.swift
598fab867564f79c350a7ea4bf170a3be0adbb8ae13e5483f914ec6a3a4c827f  swift/Sources/Statelessness/CampaignLifecycle.swift
082d2967d52fff1238221520682dfbed59b4633c58c4ef10555d4a4561fbcb0d  swift/Sources/Statelessness/CampaignRuntime.swift
d5476af9fe0113f137af00a31034b8cfa4f7fec164386408df0cad8080a322b7  swift/Tests/StatelessnessTests/CampaignLifecycleCodec.swift
2759a11cd1f408ef965b69f09f8d8b1dce5247b8a30f31a81f2a36a4627cacb4  swift/Tests/StatelessnessTests/CampaignLifecycleTests.swift
ce137d2cf64ff3aa683e9fb2d2fe800dcfaf5466fd4879ab8735733dc92291e2  swift/Tests/StatelessnessTests/CampaignRuntimeTests.swift
71c01903285df2770777a5a82c62073b02bfd1381a857e4b0585bf51e16ec4a2  docs/native-parity/N06-GO.md
58404fb47fc3903c7f01d2a945d8a89cb48c3828f7359b19671b599f2cf5a746  docs/native-parity/N06-SWIFT.md
```

Final round-1 disposition: the Go/Swift worker-loss oracle defect and remaining
Swift codec-domain witness/disclosure work are open. The observed Go feedback
lease defect has a source-only fix. Runtime qualification, final-source review
after drift, the full-u64 fixture gate and the inherited N05 qualification gate
remain pending. This review does not claim broad parity or milestone closure.
