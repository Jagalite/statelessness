# N06 installed-harness review, round 1

**Disposition: source review only; five open P2 findings. No execution or N06 approval.**
This reviewer wrote only this file. No build, test, product, qualification,
submission, cancellation, staging, commit, push, or PR action was performed.
All scenarios below are unexecuted.

Checkout: `/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336`.
Branch: `t3/native-campaign-runtime`.
Observed HEAD/base: `0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`.
The tree contains concurrent uncommitted author work. The source inventory below
was captured at **2026-10-22T15:40:32.961579+00:00**; it is a review observation, not an
atomic source freeze or package receipt. Findings identify the inspected regions;
later edits need reconciliation. No author completion or queue status was polled.

## Open findings

### H1 — P2: ambient Go configuration can substitute unsealed build inputs

**Paths:** `scripts/native-guided-support/evidence.py:151–161`;
`scripts/native-campaign-support/qualification.py:243–250`;
`scripts/native-campaign-support/installed.py:145–158`.

The controlled environment retains HOME but neither sets GOENV=off nor binds the
effective persisted Go configuration. Go therefore still reads its user go/env
file. A saved GOFLAGS=-overlay=/path/overlay.json can supply different Go source
bytes without changing the frozen go-package tree. The go list Dir and Module
Replace checks still identify the expected directory; subsequent tree hashes
still match the untouched disk files. The resulting binary can consequently be
attributed to source bytes it did not compile. This is a concrete configuration
scenario, not a claim that the user's present Go configuration contains an overlay.

**Fix:** set GOENV=off in the N06 execution environment, or require a dedicated,
sealed configuration. Capture effective Go configuration, reject unowned
GOFLAGS/overlays, and bind any deliberately supported backing files. Add a
negative control with a saved external overlay. Make an N06-local change or
separately review any change to the shared N05 evidence helper.

Read-only local toolchain authority: `/usr/local/go/src/cmd/go/internal/cfg/cfg.go:310–334`
loads the user configuration unless GOENV=off; `internal/base/goflags.go:29–41`
reads cfg.Getenv("GOFLAGS"); `internal/work/build.go:168–177` defines overlay
replacement bytes. No Go command or overlay experiment was executed.

### H2 — P2: native toolchain receipts do not bind all actual tools before use

**Paths:** `scripts/native-campaign-support/installed.py:45–57,233–255`;
`docs/native-parity/N06-QUALIFICATION.md:24–27`.

The tool manifest binds launcher executable bytes, realpaths and version text.
It does not bind the Go compiler/linker/standard-library tree or the Swift
compiler/frontend/library/SDK selected behind the launcher. For example, changing
a Go tool below GOROOT while retaining bin/go can evade verify_installed. A Swift
launcher identity alone does not identify the selected developer toolchain.

The TypeScript compiler package and Python builder venv are first manifested at
the end of build(), after their compilation/build use. Later comparisons only
establish that they still equal this post-use state; they cannot substantiate
the documented before/after identity for those tools.

**Fix:** resolve and seal the effective native compiler/toolchain inputs before
their first use, then recheck after success and failure. Capture the actual Go
tool directory, relevant standard-library inputs and configuration; Swift
selected compiler/toolchain and SDK identity; and the installed TypeScript
compiler/Python build dependencies before invoking them. Keep launcher hashes
as additional evidence. Until then, narrow the documented identity claim.
The Rust installation receives a substantially stronger bin/lib manifest in
qualification.py; this finding concerns the remaining native paths.

### H3 — P2: Python installed acceptance cannot detect excess outstanding work

**Paths:** `scripts/native-campaign-consumers/python/consumer.py:831–849,872–910`;
`scripts/native-campaign-support/host-requirements.json:15–27`.

The held rendezvous sink uses workers=2, buffer=0, jobs=3. Three is already the
allowed queue + workers + active sink bound; there is no fourth job whose
admission could violate it. The buffered jobs=4 case has no held sink or
upper-bound assertion. Required Python labels contain no separate bounded-sink
test. An implementation with excessive capacity on longer campaigns can
therefore satisfy every required installed Python assertion.

The existing handshake is useful: it positively observes receive releasing a
sender before sink return, and the rejecting-sink variant checks drain. It does
not establish the upper bound; the Python README appropriately acknowledges
that limitation.

**Fix:** require installed rendezvous and positive-buffer cases with jobs greater
than buffer + actual_workers + 1, hold the sink while filling capacity, and bind
the observation to actual transport admission. Cover both normal release and
sink rejection with complete drain/join accounting. Add mandatory acceptance
labels only when their assertions exist.

**Related evidence limit in the other installed applications:** Rust host.rs:92–96
waits for factory entries then observes a 50 ms absence of another entry;
Go host.go:378–413 signals model Close before actual send and observes 150 ms;
Swift HostChecks.swift:49–56,240–245 waits for teardown then a finite window.
TypeScript host-checks.ts:192–200 waits for an encodeInput notification emitted
at host-fixture.ts:143–152 before the encoder returns to transport. A worker
paused between those notifications and actual send can make even a faulty
transport look bounded. These are useful finite observations, not causal proof
that every relevant sender reached the blocking boundary. Retain their narrow
scope and connect the separately owned source-bound transport probes/fault
controls to the installed claims. This is not a finding that the runtimes
actually have unbounded queues, nor a duplicate review of their ongoing probe fixes.

### H4 — P2: native installed checks omit seeded multi-case callback equivalence

**Paths:** `tests/native_campaign_reference.rs:47–123`;
`scripts/native-campaign-consumers/go/host.go:83–85`;
`scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/HostChecks.swift:94–96`;
`scripts/native-campaign-consumers/typescript/host-fixture.ts:84`;
`scripts/native-campaign-consumers/python/consumer.py:630–635,740–750`.

The Rust reference exercises ordinary/guided campaigns, workers 1/3, three
cases, four steps, mutation 100%, fresh public bare runs, full per-job reports
and generated/applied callback sequences. Go, Swift and TypeScript installed
models ignore RNG and their host configurations use one case and mutation zero.
Python compares reports with fresh bare calls, but still uses one case/one
transition/mutation zero and does not compare those fresh callback sequences.
The deterministic shared counter rows do not fill this gap.

**Scenario:** a campaign uses the wrong RNG seed internally while reporting the
correct seed field, or mishandles mutation/restart between cases. Native
installed checks can pass despite those errors. Seed-function vectors alone do
not show that execution used their seeds.

**Fix:** add mandatory installed ordinary/guided tests with caller and spawned
worker paths, multiple cases and mutation, independently comparing each job
with fresh public bare search. Compare the complete report and callback/input
sequence for each job; preserve actual receipt order separately. If exact Rust
per-job reference traces are the qualification target, author and seal those
deterministic expectations independently instead of inferring them from a
native pass.

### H5 — P2: installed checks omit guidance-before-campaign validation

**Paths:** `tests/native_campaign_reference.rs:128–147`;
`scripts/native-campaign-support/host-requirements.json:2–62`;
`scripts/native-campaign-support/qualification.py:302–327`.

The Rust public reference combines each of the four invalid guidance limits
with jobs=0 and requires "guidance limits must be positive" without factory,
feedback-factory or sink calls. The 49 shared rows and current installed host
requirements do not exercise this precedence. The installed Rust phase builds
the external consumer application; it does not execute this source test.

**Scenario:** a native guided campaign validates campaign configuration first
and returns the jobs error. It can pass the entire installed acceptance set.

**Fix:** require installed host assertions for all four zero guidance limits
combined with invalid campaign configuration and relevant pre-set controls,
checking the exact first error and absence of callbacks. Keep the separate
source-bound Rust test gate as well.

## Earlier concerns corrected in source during this review

The initial qualification.py observation
(`497bcc1100d72b7dce63dd31da363d0d38d80a50289a1ebc358c452798050756`)
accepted any nonempty unique case list agreeing with its own seal. A shortened
list could omit the Swift panic mismatch and wide-arithmetic rows and still
reach aggregate success if the remaining gates passed.

The inspected replacement at qualification.py:113–122 requires the exact ordered
inventory in public-cases.json: 15 seeds, 15 validation rows and 19 campaign rows.
It also rejects unsupported dispositions for these shared cases. execution.py:28–50
revalidates every required engine/case object at finalization. Thus the present
49-row inventory requires 245 deterministic engine/case comparisons; the earlier
omission finding is **source-corrected, unexecuted**, not an open P1.

The new native-campaign-author.py and native-campaign-inventory.py supply the
missing independently authored seed/validation rows. The author imports only
its independent stdlib fixtures and preserves masked u64 seed arithmetic;
the four large-count validation cases keep count operands within the shared
2^53-1 intersection while still testing u64 aggregate overflow/capping.
Its materialization and seal remain an execution gate. The 19-row golden.py
entry point remains a separate reference assertion, not the whole installed corpus.

The independent golden comparator now uses recursive exact parsed-JSON types,
keys, values and array order. Go wire.go:153–164 and Swift Projection.swift:84–96
now preserve actual public diagnostics. Earlier diagnostic spelling aliases
were removed. These corrections must retain their real mismatch results.

## Mismatches that must remain visible

**Swift feedback metadata is a real dependency, not an unsupported row.**
The installed fixture throws CampaignPanic, but the public guidedFuzz call at
swift/Sources/Statelessness/Guided.swift:227 invokes Model.swift:149–153, which
wraps the error as ModelError. CampaignRuntime.swift:187–202 therefore produces
ModelError with model metadata Some and incomplete accounting. The independent
Rust row requires Panicked with Some/incomplete. Projection.swift retains this
discriminant; all operands fit the common domain. A complete comparison must
fail for the observed source until an explicitly reviewed dependency resolution
changes behavior. Do not delete the row, relabel it unsupported, flatten its
outcome, or rewrite the expected value. No unchanged-N05 policy was edited here.

Python's FuzzConfig constructor also rejects mutation 101 before campaign
validation, changing both mutation-101 and validation-first-error. Its public
step/check diagnostic context differs from the Rust golden. The current
adapters preserve those values; Rust itself adds initial_state/step/check_state
context, so describing all Rust errors as bare application strings is incorrect.
The shared initial-error Python text agrees at the inspected source. Further
language diagnostic differences documented by their owners remain separate
integration gates; this review did not requalify those runtimes.

N05 remains separately pending under the supplied status: materialized78,
reviewed author90, with author job
`36c3552df326429fb9e76d872c80aafa` not a pass. This review neither restarted nor
queried it, and establishes no N05 FREEZE or 90-case execution.

## Architecture and evidence assessment

The inspected applications call actual public APIs and real worker paths.
There are substantive barrier-controlled out-of-order, configured>1/jobs1
spawn, caller-sink and worker-local model/feedback checks. Python supplies
application report-input transfer explicitly; TypeScript imports an application
worker module and transfer hooks. No serial substitute, canonical ModelCodec
requirement, or guessed serialization of arbitrary model closures was found
in those installed paths. Host scheduling observations remain distinct from
one-worker deterministic protocol rows.

Rust's source runtime calls the private lifecycle core; its external consumer
does not import that private core. Completion precedes blocking transport, and
the receive/acknowledge split is represented by the source authority. Private
codec, independent ledger, duplicate/stale acknowledgement, deliberate-fault
and injected worker-loss tests remain separate gates. Reading selected
self-model regions does not close the original ledger/loss-oracle findings
recorded in the contract and language reviews.

Preparation requires committed selected inputs and copies a private read-only
snapshot. The evidence library records membership, type, mode and content,
including empty-directory additions, rather than rescanning only previously
known filenames. Source/package manifests and failure-path after-capture are
substantive safeguards, subject to H1/H2's actual-input limitations.

Recorder retains exact binary stdin/stdout/stderr, requested argv, cwd,
controlled environment, exit status and timing. Failed deterministic cases
retain expected objects, parseable actual objects and original raw logs; the
loop continues to other cases. Interrupted/incomplete inventories remain
explicit. Failed phase checks retain before/after evidence and the original
error. Kyoto attachment checks requested/effective argv and successful terminal
execution. The inspected Kyoto logs implementation streams the complete log;
no claim here is based on a truncated UI excerpt.

Strict JSON rejects duplicate keys and noncanonical floating values. Whole-object
comparison preserves decimal strings versus numbers, booleans versus integers,
nulls, nested check/failure data and array order. Admission checks inventory and
domains rather than executing a general JSON Schema validator; its expected
schema/type authority is the separately reviewed, hash-bound complete corpus.
The independent author seal is therefore an essential gate.

Python wheel/RECORD/direct_url, npm archive integrity/resolution, Cargo package
and dependency inputs, Go frozen-module import path and SwiftPM local-package
resolution checks are present. Go/Swift here qualify external consumers of
frozen local source packages, not a remote registry release. No actual installed
package or compiler artifact was built or qualified by this review.

The preserved portable conformance runner is invoked unchanged except for
lossless subprocess logging. Its asserted inventory is 94 golden, 530 graph,
32 metamorphic, 38 portability and 23 transport cases per engine, plus 550
ordered-pair replay checks and 45 fresh processes including identity probes.
Those are acceptance requirements, not executed counts. Baseline preservation
still requires actual successful receipts.

## Remaining execution and integration gates

1. Resolve or explicitly retain H1–H5 and the exact public diagnostic/N05
   dependencies. Keep failed and superseded evidence; no normalization to green.
2. Execute the independently authored 49-row materialization/seal and the
   harness negative controls through shared-default Kyoto. Add omission controls
   specifically for feedback-metadata-panic and arithmetic-boundary rows,
   strict type/field/order changes, tool-input drift and the Go configuration case.
3. Root must reconcile current source, package inclusion and prior review deltas,
   then own the final commit, immutable identities and integration marker.
   The contract's Cargo include issue for packaged reference tests is still a
   separately recorded integration item. No marker was written by this reviewer.
4. Execute exact direct-Cargo plans and fresh-installed native applications,
   preserving package/tool identities and requested/effective commands. A
   --no-run compilation or queue admission is not test/runtime execution.
   Existing author queue handles recorded in the contract were not rechecked;
   this review submitted no duplicate job.
5. Require all 245 exact deterministic comparisons, complete mandatory installed
   host assertions, baseline receipts and final membership/type/mode/hash checks.
   Independently execute source-bound lifecycle/codec/self-model, fault/shrink/
   replay, cancellation/deadline/worker-failure, drain/join and runtime suites.
   An installed-harness pass alone does not close full N06.
6. Preserve N05 qualification as pending separately. N10, generic CampaignReport
   merge, browser/platform expansion, profiles/defaults/144-cell promotion,
   hard RSS, callback preemption, fatal-trap recovery, durable delivery and
   queue resume are outside this receipt.

## Read scope and method

Read fully during this round: GUIDED-CAMPAIGN-AUDIT.md,
GUIDED-CAMPAIGN-REVIEW.md, N06-CONTRACT-REVIEW.md, both N06 language R1 reviews
(including appended cutoff sections), N06-QUALIFICATION.md, N06-RUST-CONTRACT.md,
N06-INTEGRATION.md and N06-REVIEW-CHECKLIST.md; the N06 plan section and Kyoto
skill; scripts/check-native-campaign-consumers.py, all listed
native-campaign-support modules/data, evidence.py; the five installed consumers'
source files/manifests/READMEs listed in the inventory; native-campaign-public.rs,
golden.py, author.py, inventory.py and schema; src/campaign.rs and lifecycle.rs;
tests/native_campaign_reference.rs; conformance/run.py; and fixture build/lib
manifests. Concurrent harness additions were reread in the affected regions.

Selective reads only: lifecycle/codec.rs:1–335 (codec logic, not all codec tests);
lifecycle/tests.rs:1–265,455–525,960–1217; private adapter.rs:1–240;
guided.rs:165–235; explore.rs:731–890,1380–1465; model.rs:470–515;
operations.rs:1–160; Python/Swift production error and validation regions
referenced above; the local Go toolchain configuration/overlay definitions and
Kyoto log-streaming function. tests/campaign.rs was inventoried by test names,
not reviewed fully. No full native-runtime, example-tree, installed-artifact or
whole-repository review is claimed. One read-only delegated cross-check of
consumer/reference coverage contributed H3–H5; it wrote and executed nothing.

Inspection used git identity/status/worktree queries, rg, cat, nl/sed, wc,
shasum and a Python stdlib file-read/hash/stat inventory. No repository module
was imported or executed for this review. Exact source pins below identify
review inputs, not successful build or test output.

## Source identity inventory

SHA-256, observed mode, byte size and path at the stated cutoff. This is a
selected-file inventory; listing a partially read file does not expand the
read scope above. Files were read sequentially while authors worked, so this
is not an atomic freeze. Only this review document is owned by this reviewer.

```text
4598c1c167f1cbe72c38ef165c0b4e1bfec5aa57ed65ef23d70185286b1627fc  0644  1881  Cargo.toml
7aa837eaa71019a2fc6994d4c5886a0aabd6e0dc523e8cd8e964decde1dcf37b  0644  2124  build.rs
4e6a4993b5fb06b141465fd29333bf08ea39ad85237fddb9f404c3165cfc9c6b  0644  3686  conformance/fixtures/native-campaign-author.py
dc1d4e75b9d99dc790a530bfddb742c1acefdf9226401db7b7a5659372c75e76  0644  9478  conformance/fixtures/native-campaign-golden.py
79b9faf376439dd2945a1df021b51e16c7b504882c32ccc927e712ee00cf7409  0644  3167  conformance/fixtures/native-campaign-inventory.py
532bb5736cac7a9eb65b5b1f442bb61ba53f5ae7dbb2b8544950411b6231c2a8  0644  23275  conformance/fixtures/native-campaign-public.rs
306fd52e74fca6746e12acc750f232de15e71da917756a1270d74c91f5eb7368  0644  9  conformance/fixtures/native-campaign-rust/.gitignore
caa26b073414ebe2d82f01ebf0be87e71abf0792d00fd7f491c9d817e2e68b4b  0644  265  conformance/fixtures/native-campaign-rust/Cargo.lock
273b4cac3df3234228090ca7f8d5644470004d61324011be8d98ce5c8d82fda0  0644  321  conformance/fixtures/native-campaign-rust/Cargo.toml
47ff5f466092ea02d9bfc7dc2e61931b69a5fbb3170c60f7634abd6e22cf07bb  0644  24975  conformance/fixtures/native-campaign-rust/adapter.rs
8a28f370ca38baa177618ae20f650ea267cb1360d393203d2768f9dd2ca5897b  0644  1385  conformance/fixtures/native-campaign-rust/build.rs
7a555271c7ae5f14d6169d720dda270897bb6d762aab0590a15a345d20eaef9f  0644  160  conformance/fixtures/native-campaign-rust/lib.rs
59138f9b05ecbad86fd4bd559ce3d6e44d70a23bf6b6e9f7e354525b3a462d9f  0644  12927  conformance/fixtures/native-campaign-v1.schema.json
574d639d96829164bb139f797ea8b1f7aa053d5e4cfaef9337596418a46dc0db  0644  11199  conformance/run.py
8d9e0fa19796931a2b85368092120e692e9e0f50788b9f36b2f22aa463ab7301  0644  22621  docs/NATIVE-PARITY-PLAN.md
0e21253da3acf41ebc406d8b8224d4572519a42c3b5f66fd9b7c63747fa4fbb3  0644  32963  docs/native-parity/GUIDED-CAMPAIGN-AUDIT.md
6343d7714e1d06e95527eb78ff072857f774bec2e885f91712bdc31a48650360  0644  13535  docs/native-parity/GUIDED-CAMPAIGN-REVIEW.md
8d990b699cbd2bf6dfe812f3c13c9b416372a9d6e99aeebc650f6c3c8867a5d5  0644  16800  docs/native-parity/N06-CONTRACT-REVIEW.md
6e494ce8573bcba3454cce56f81d833335cdf31e2991b58f307376260836e859  0644  7409  docs/native-parity/N06-INTEGRATION.md
3d72637d32838881714a5517adad0c2015c9e6543cd71635304bab0ddfd14988  0644  12437  docs/native-parity/N06-QUALIFICATION.md
a12d67480d8a3ad79609a231b9513217e2d089805e20b5973dc77bac8c4081a3  0644  6614  docs/native-parity/N06-REVIEW-CHECKLIST.md
5ee879de57d4f490da191ceef6d09cade3e739f9303a6a46fdfc346498fae270  0644  21908  docs/native-parity/N06-REVIEW-GO-SWIFT-R1.md
64157bfec922b4ad9bc7d37c715f800c28ebe58451584650d92996848a2e2c1e  0644  19117  docs/native-parity/N06-REVIEW-PYTHON-TYPESCRIPT-R1.md
6c232b3961e4c4ed52d49f55ed1182f22be9080e7c1bbc37b7445bdd370c2082  0644  19341  docs/native-parity/N06-RUST-CONTRACT.md
d3f49de2cda63b0b6c4031809b74235b773ee59ad9a65be7fc72029e83edf397  0644  28886  python/src/statelessness/core.py
4cef4f85ff012660764b473f4e8f30ca2df1384f51932af175d090cf58c92fb6  0644  23552  python/src/statelessness/search.py
ea90950309be5b272d0c4bc58881125e24eb80b6746ed6d837eaeac73e7278c8  0644  707  scripts/check-native-campaign-consumers.py
8f47291cf95f84caa11d9a98c66a3f2754b12456b8da53e3410a2731ead2ad1b  0644  8988  scripts/native-campaign-consumers/go/README.md
678201184fdc8a1fdbd830f48dc72743888a952266c65d48a8c902c48a2c384c  0644  11027  scripts/native-campaign-consumers/go/cases.go
24b052be80b3034ac66cfdda8dbe9574fc123ecc5f568bc93280e591dea7e2b9  0644  240  scripts/native-campaign-consumers/go/go.mod
2cee4c99eb9115d17515ada092bf86a2af14545b62e584d251fb7876fb7fa872  0644  18595  scripts/native-campaign-consumers/go/host.go
006a75acab87547f120029bb26dc748ae812c453112f0019055b9a2324ffa99e  0644  864  scripts/native-campaign-consumers/go/main.go
2b24f29eb736813403e0d431dd3fbbcf8ba7cdc965b0eb721de1fd3edbe047c4  0644  5930  scripts/native-campaign-consumers/go/wire.go
d982a5d4701159f1cb356425c8b1f24818bba673e84a761e3a9c29b0531348c6  0644  9679  scripts/native-campaign-consumers/python/README.md
41a025ed7d3da1d8453dfccfe9a925d864a892154ea7e821fe79b21af05dbca4  0644  46948  scripts/native-campaign-consumers/python/consumer.py
6f67f973b5b4f82a2e8a7b11ce292d6035097d8e17fdff21f7c5255869ae48aa  0644  203  scripts/native-campaign-consumers/rust/Cargo.toml
b3da034e32aaa75c9ed1ed18e9bfc7e8ca2e16e65fc8fbe7ea55b94041192a5c  0644  1124  scripts/native-campaign-consumers/rust/README.md
feda9962fed07eaf472da7e532f7fc8b31befc5da54a58dbd1136c986f7f3435  0644  9683  scripts/native-campaign-consumers/rust/src/host.rs
f66d7df6022530f5d95523c8ab1e3ee8713abc5a72292147ec63b6a7a1f3703e  0644  1143  scripts/native-campaign-consumers/rust/src/main.rs
b904826dde3f67a42866f4369ea20b1eb39728a6f2dfe569803d1c16c087801c  0644  848  scripts/native-campaign-consumers/swift/Package.swift
43269e2d6fb81cc033815d69485df5e264903bc539b2a3babae18916fbae9f96  0644  11478  scripts/native-campaign-consumers/swift/README.md
98303ddcf90a9026af5eeb24e43886d57a465269594a749cd499c024a7228b68  0644  629  scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/CampaignConsumer.swift
226717631736d56dca3612c379aea34cc49a5da6fb876f881ac0de0a52c9320c  0644  11384  scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/Deterministic.swift
704baa0e1e6b056d922ef5a96c6ae952d090a60325a0736287c62e59fde1b56c  0644  20685  scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/HostChecks.swift
854e31bd74a6cb9f511ad5c89a0f93be2644fffe674d7deb331527c797180359  0644  5340  scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/Projection.swift
57c2ca611a7f7464a9a5e3973e04df905a0cefcbb179c317831da473a48a3c71  0644  1952  scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/Support.swift
a626596fca8cfe96f2845aaa38ae25076567df4e589fb96320077b3a01cf69a6  0644  7675  scripts/native-campaign-consumers/typescript/README.md
64fc1b815d0497e2ae9c0e92f94b10968511d7e217ceb231a778f03887a92d99  0644  1790  scripts/native-campaign-consumers/typescript/assertions.ts
1db1cd67392e9f9670155cf8fb30edf7208da741d04f825a58eaadf37ed4825b  0644  1569  scripts/native-campaign-consumers/typescript/consumer.ts
1972fd12455c8dacefb8044bc28ff2e540a7786ff5331924ddf9feb73e9d5acd  0644  10024  scripts/native-campaign-consumers/typescript/deterministic.ts
08138ffad33f5a77cc54c4dc9928609d139f3f098240ad90a335e291cc6cfa04  0644  15344  scripts/native-campaign-consumers/typescript/host-checks.ts
e2738b80cf87863ea68c33b28c2a0fd0690136bd1559e7299ba017b2fad7bf58  0644  8313  scripts/native-campaign-consumers/typescript/host-fixture.ts
6e1b604755e9654cfbf976662cc98bf9b3cce00f3211cf63ac8fcc9df6ecfe2c  0644  568  scripts/native-campaign-consumers/typescript/node-runtime.d.ts
c187c827534e8181b2893540200827ba83476ca5e11c272466bd2f85b44ed656  0644  89  scripts/native-campaign-consumers/typescript/package.json
d9a6340a39f349177675bd09badd94520ec84f16b53fdc24566d91be9b05bab4  0644  5101  scripts/native-campaign-consumers/typescript/projection.ts
527399fb11aecc1c257fc2b7959f59856d6488ffae2e07b0c26ccadbd724f120  0644  332  scripts/native-campaign-consumers/typescript/tsconfig.json
1dec7eafc33c35f72a6c39d33d04eab0ef64a97af288f34dcd554375244bce75  0644  2825  scripts/native-campaign-support/domains.py
1630cbdce7d1354d68f5e7917e8b87ae4c324301fc4c538cd53dabae8c218846  0644  16493  scripts/native-campaign-support/execution.py
58099cfbce16a171846a022b96f2aeeeb2f5e648da08f3eba940826581add429  0644  2915  scripts/native-campaign-support/focused_run.py
7db111d812692ca5d4ee4d7a1a59ea331824a84db3d54511f0f75e25cf640d49  0644  1512  scripts/native-campaign-support/host-requirements.json
7c8fc64a6950f98f1ffea2442f533acc845fe61c26ef9a0703386df890161f9d  0644  17862  scripts/native-campaign-support/installed.py
a338916034300132645a47ad239d80e68ce4e08b2be8b9599c9b22016c90b5d4  0644  798  scripts/native-campaign-support/public-cases.json
312376ce830ae924f2a8ac5a7c32df5ee73958c90a17e3e225b1406962fddc4d  0644  45712  scripts/native-campaign-support/qualification.py
d051b4b24e60733ac50f6e73ba5aafc2820ba639c91e9a61802120652ec337d1  0644  11194  scripts/native-campaign-support/test_harness.py
e5d934535179ea9262ce164cf865b1ff4b6214bd1f66938d088c41a358e3eea0  0644  8961  scripts/native-guided-support/evidence.py
4d4da9479fe3512589df379cba9a1f3e5df989bba6920e3691479e6b3ef19005  0644  19835  src/campaign.rs
7931c3ef82bf7f2b806448ef8a65f8f2e4a136d38c58d8e06307ee4b090ef44a  0644  21378  src/campaign/lifecycle.rs
a93ee1cf524339743564923dacd6d8aac007e95d9ecd68e00f5f4e08de8b579d  0644  24482  src/campaign/lifecycle/codec.rs
3102ec704a8757f339d558e93faf98c82499076ffc94d8177551aba2af582b3a  0644  42630  src/campaign/lifecycle/tests.rs
496c19c7181a6bf3f2af97d3e04df1d3c7b22c241351104b4cd66201f6706ecb  0644  48611  src/explore.rs
bbc7b214510f862738991074f8f2053389c4cac85caf005ebc129df9b06a18d7  0644  12345  src/guided.rs
e69a80ad96826a27871807f848e22fd213a7bee9f95d76be92069e68504f5beb  0644  15663  src/model.rs
ec19cf028645dd16f6f389cc7fa0fa3a44a72a02838842a906ca11bb2303649b  0644  10443  src/operations.rs
3fd338b59ddc1c7f4b66db003b9996caf458b3635f90247c3e2be960e1ff52ea  0644  8421  swift/Sources/Statelessness/Campaign.swift
082d2967d52fff1238221520682dfbed59b4633c58c4ef10555d4a4561fbcb0d  0644  12918  swift/Sources/Statelessness/CampaignRuntime.swift
87f69f8144004490a913230c0b34c676a5d1e3e7b491aba184e9614671237c46  0644  12976  swift/Sources/Statelessness/Guided.swift
3c00084776a8d530b3edca6d90dcac839d422880b8ba996cda7ebc7d9db6270c  0644  12419  swift/Sources/Statelessness/Model.swift
4ef4844eb77de0f120acbeaefdaea372a8e08caf7fc3466d0ccee10452aca449  0644  23622  tests/campaign.rs
49b64ecae6c0b30cb089e687423c11f0f391eb719afa0fce575fb7f19cb5260c  0644  7759  tests/native_campaign_reference.rs
```
