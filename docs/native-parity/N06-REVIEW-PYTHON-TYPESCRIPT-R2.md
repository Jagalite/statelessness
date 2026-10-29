# N06 Python and TypeScript independent review, round 2

**Source-only disposition: five open P2 qualification/acceptance findings; no new
actionable runtime defect found in the bounded Python/TypeScript review.** The
seven numbered R1 findings are closed in inspected source, with execution still
pending. Finding Q5 below overlaps the TypeScript observation in the separately
owned installed-harness R1 H3; it is not an additional distinct runtime defect.

Reviewed on 2026-10-22 only in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336`, branch
`t3/native-campaign-runtime`, initial HEAD/base
`0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`. This reviewer writes only this file.
Read-only delegated Python and qualification-script inspections were reconciled
with local source reads. No reviewer edited implementation, built, tested,
installed packages, executed fixtures, submitted/polled Kyoto jobs, staged,
committed, pushed or opened a PR. All scenarios below are source traces, not
reproduced runtime results.

The plan's N06 section, both GUIDED-CAMPAIGN documents in full, the complete
Python/TypeScript R1 review, root INTEGRATION and CHECKLIST were read. Actual Rust
campaign/lifecycle, test codec/self-model, guided/explore/model/operations paths
remain authority. The rust-builds-kyoto skill was read; any later executable
qualification belongs to the owner through shared-default Kyoto.

N05 remains **final qualification pending: materialized78/reviewed author90**.
The required FREEZE from author job `36c3552df326429fb9e76d872c80aafa` is absent
under the supplied boundary; this review neither inspects/restarts that job nor
claims a 90-case pass. N10, baseline execution, installed execution, exact Rust
comparison, platform qualification and 144-cell promotion remain separate.

## Open findings

### Q1 — P2: standalone Python installed runner and example disagree with the current protocol

Locations: `python/tests/qualify_campaign_install.py:280-283`;
`examples/native-campaign/python/consumer.py:33-54,381-390`;
`conformance/fixtures/native-campaign-public.rs:643-674`.

The runner requires exactly 48 unique cases. The current example includes
`feedback-metadata-panic`, giving 15 seeds + 15 validation + 19 campaign cases =
49. A successful build/install/demo reaches an unconditional inventory failure
before its reference-case loop, even without `--expected`.

Updating the count alone is insufficient: the example's aggregate-cases and
aggregate-transitions inputs still use jobs=3 and I64_MAX; its capped-product and
maximum-job-count inputs also use I64_MAX. The current independent shared Rust
protocol uses jobs=2049 and 2^53-1 for those respective operands. Exact comparison
must reject the differing config fields. This concerns the standalone example
and its driver; the separate root Python/TypeScript consumers already use the
revised common-domain inputs.

Fix: reconcile the exact expected case membership and operands with the current
independently authored protocol, retaining full-width native boundary cases as
separate host checks. Do not hide these differences through projection or count
clamping. Preserve the standalone example's actual feedback-error classification
as a separate native-policy limitation.

### Q2 — P2: several command watchdogs stop checking when the leader exits

Locations: `python/tests/qualify_campaign_install.py:177-186`;
`validation/native-parity/n06/typescript/qualify.py:227-255`;
`scripts/native-guided-support/evidence.py:192-215`, imported by
`scripts/native-campaign-support/execution.py:10,192` and `installed.py:12,44`
(the latter constructor moved to line 67 in the final observed revision).

These command paths wait for the leader PID. The TypeScript runner's group
cleanup is reached only on an exception during the guarded wait; a nonzero exit
is raised afterward. The Python installed driver and shared Recorder similarly
kill the group on timeout, or selected exceptions while the leader is alive.
If the leader exits while a descendant remains in its private group, normal
return bypasses cleanup. Such a command can be recorded successful while a child
still holds its stdout/stderr or can mutate evidence. A nonzero leader exit can
also leave descendants running. This is a concrete watchdog control-flow gap,
not an observed leftover process in this review.

Fix: check the invocation's owned group on every exit path with a bounded grace
period, record whether cleanup was required and whether the group disappeared,
and fail qualification when descendants outlive the leader. Restrict cleanup to
the exact newly created group. Preserve the primary command error and all logs.
The Python focused runner already has the stronger all-exit-path pattern at
`validation/native-parity/n06/python/focused-r1/run.py:47-67,144-152`.
Correct the aggregate use through N06-owned code or separately reviewed shared
helper work; this finding does not authorize changing N05 sources/receipts.

### Q3 — P2: standalone Python manifests omit directory and special-entry identity

Location: `python/tests/qualify_campaign_install.py:41-51`.

`inventory` records regular-file bytes, size and mode, and rejects symlinks, but
omits the root, directories and unexpected special entries. Adding/removing an
empty directory, changing a directory's mode, or introducing a FIFO does not
produce the required membership/type/mode observation. A missing or substituted
root also lacks an explicit root identity. Consequently equality of these maps
does not establish the full manifest guarantee required by the checklist.

Fix: inventory the root and every entry with lstat, recording type and mode as
well as file hashes, and reject unsupported types explicitly. Keep readable
partial evidence when inventory itself fails. The focused Python runner's full
inventory is a separate implementation and is not affected by this finding.

### Q4 — P2: standalone Python failures can bypass final identity capture

Locations: `python/tests/qualify_campaign_install.py:265-317,330-333`.

Installed before/after manifests are written only after the consumer block;
snapshot and archive rechecks are later success-path operations. A build failure
after the snapshot exists, an installation failure, an interruption, or an error
during the after-inventory jumps to the outer handler without the corresponding
final observations. For example, package-build failure retains its log but skips
`source_snapshot_after`; install failure skips installed-tree captures. This
prevents distinguishing failed execution from input/artifact drift on those paths.

Fix: use independently guarded final captures for each attempted source, build,
package, consumer and installed stage. Persist each before observation before
execution; preserve partial after observations and the original failure even if
a later capture also fails. This is not fixed by the focused unit runner's
separate failure manifests.

### Q5 — P2: installed TypeScript upper-bound assertion still observes encoding before send admission

Locations: `scripts/native-campaign-consumers/typescript/host-checks.ts:177-207`
(especially 192-197), with related rendezvous/drain checks at 155-156 and 218-219;
`scripts/native-campaign-consumers/typescript/host-fixture.ts:143-152`;
actual send at `typescript/src/node_campaign_worker.ts:40-45`.

The held sink resumes when `WORD.encoded` reaches the target. That counter is
incremented inside encodeInput, before encoding returns and before the worker's
send reaches the real coordinator. A worker delayed at that boundary allows the
sink to resume before even an excessive-capacity channel admits its report.
The transient factory guard only detects over-admission that happens while the
sink remains held; it does not force that opportunity before release. Repeating
this across sinks does not establish the required blocked-send observation.

Fix: bind the upper-bound claim to actual blocked-send/admission state, or narrow
this installed check to the progress/transfer observations it establishes and
require a separately source/package-bound transport witness. The corrected
in-tree test uses a real coordinator probe and closes the original R1 T3 finding
for that test; it does not automatically qualify the installed acceptance label.
This is the same installed-evidence limit noted in harness R1 H3, not evidence
that production queues actually exceed their capacity.

## Explicit disposition of every numbered R1 finding

All closures in this table mean **inspected source only, unexecuted**.

| R1 ID | Disposition and inspected evidence |
| --- | --- |
| R1: TypeScript hostile sink diagnostic/proxy classification | Closed. `campaign.ts:121-133,189-197` guards inspection and copies the diagnostic into an owned ModelError. Revoked-proxy/prototype-trap synchronous and asynchronous witnesses remain at `campaign.test.mjs:889-914`. |
| R2: Python hostile metaclass fallback | Closed. `campaign.py:158-167` guards builtin argument extraction and ends with a constant fallback. Serial and process witnesses at `test_campaign.py:400,621` retain first-error accounting and cleanup checks. |
| R3: Node caller token ignored with limits function | Closed. `node_campaign.ts:67-71` composes the context token with the original control and passes its absolute deadline. The original control retains the limits receiver. `campaign.test.mjs:627-644` exercises factory cancellation, receiver identity, metadata/feedback timing and subsequent claim cutoff. |
| T1: Python unsnapshotable output fixture | Closed. `test_campaign.py:103-110` gives Opaque a faithful deepcopy and rejecting pickle reduction; successful process paths assert Completed. No N05 snapshot policy changed. |
| T2: TypeScript codec narrows Rust u64 counts | Closed. `campaign-lifecycle.mjs:86-122,197-228,262-294` separates full-u64 bigint wire DTOs from native safe-number conversion. `campaign-lifecycle.test.mjs:255-364` includes 2^53, 2^53+1, u64 maximum, exact near-limit deficits, explicit representability errors and malformed negatives. This does not widen runtime/N05 count domains. |
| T3: short quiet interval substituted for blocked sends | Closed for the original unit tests. Python `test_campaign.py:448-520` observes production Condition.wait calls and rechecks two blocked bridges plus queued/offered/sink identities under the transport lock. TypeScript `campaign.test.mjs:478-513` waits for a consistent actual queue/full-blocked-send probe. The installed-consumer extension remains Q5. |
| T4: private input values omitted from range/max-ID equality | Closed. `campaign.test.mjs:67-72,191-227` projects failure inputs and every individual corpus entry through `.value()`, including ordinary/guided maximum-ID cases. Entry boundaries are retained. |

## Surrounding corrections and invariants

- Python startup handling still encloses Pipe, Process and bridge construction,
  retains owned processes, drains existing work and joins. The expanded failure
  witnesses are at `test_campaign.py:705-735`. The production change is at
  `campaign.py:427-479`.
- Python `_deliver` captures the original ID, applies Receive before application
  decode, and returns immediately on Discard (`campaign.py:280-302`). Its
  `_CleanupPanic` stays outside the runner's ModelError handler. TypeScript caller
  and Node host also capture the acknowledgement ID before mutable reports reach
  user code (`campaign.ts:210-212`, `node_campaign.ts:91-99`).
- Python records bridge errors by worker slot and emits at most one joined error
  per process in launch order after draining (`campaign.py:389-418,470-478`).
  Nonzero exit supersedes that worker's pipe diagnostic. Reverse-exit tests at
  `test_campaign.py:637-677` check the exact order and sink-error precedence.
  Node likewise observes joined failures in worker-array order after drain
  (`node_campaign_worker.ts:102-108`).
- Both independent ledgers now directly compare genuine loss history with the
  production flag and derive Finish admission from independent claims, receipts,
  active sink and loss facts. Python locations are
  `campaign_lifecycle_fixture.py:445-447,529`; TypeScript locations are
  `campaign-lifecycle.mjs:317-318,411`. Corruption controls at
  `test_campaign_lifecycle.py:418` and `campaign-lifecycle.test.mjs:489-516` start
  after ModelError has already made accounting incomplete, then detect fabricated
  loss and premature Finish. The prior oracle gap is closed in source.
- TypeScript now has the requested Complete-before-encoding/send barrier test
  (`campaign.test.mjs:584-609`), worker-construction failure with no lost work
  (`:647-655`), positive maximum-u64 wire states, and semantically inconsistent
  summaries in the actual self-model alphabet (`campaign-lifecycle.mjs:432-440`).
  The corrected fake clock includes timeOrigin (`campaign.test.mjs:334-353`).
- Production still uses real spawned Python processes and Node coordinator/job
  workers; workers1 uses the caller and configured workers>1/jobs1 spawns.
  Complete precedes transfer/send; receipt frees the sender before sink return.
  Runtime delivery is unsorted. The private core owns every lifecycle event;
  worker-local models/feedback/features stay local and report inputs use explicit
  application transfer. No generic CampaignReport merge, fatal-trap recovery,
  hard-RSS/preemption or durable delivery/resume promise is added.

## Existing dependency and execution gates

The exact-comparison limitations already catalogued in N06-QUALIFICATION and
the consumers' READMEs remain open. In particular, TypeScript's public guided
wrapper converts the installed ordinary Error feedback-metadata witness to
ModelError (`guided.ts:253`, `guided_sync.ts:27-29`), whereas Rust unwinds to
Panicked. Python's standalone example also retains its chosen ordinary-exception
policy; the separate root consumer uses an explicit BaseException panic analogue.
Callback diagnostic spellings and Python FuzzConfig's early mutation validation
remain visible differences. No N05 normalization change is requested or made
silently. Preserve the real comparison rows and separately review any resolution.

The shared Rust validation reference now deliberately uses the common 2^53-1
count domain. Root Python/TypeScript consumers match those operands; broader
native boundary tests remain separate. This closes the earlier operand-substitution
concern for those root consumers, not for the standalone Python example in Q1.

The TypeScript focused script retains full frozen/work/installed/consumer trees,
declared generated exclusions, exact commands, tool hashes and guarded failure
captures. The Python focused unit runner checks manifest/runner/archive identity,
extracts declared files, records full before/after lstat membership and checks its
owned process group even after leader exit. Those are inspected designs, not
successful executions. Python focused testing excludes baseline/install evidence;
TypeScript focused execution excludes exact Rust comparison and browser evidence.

The separately owned harness R1 review also records installed Python bounds,
multi-case seeded reference coverage, guidance-before-campaign admission and
toolchain/configuration findings. This R2 report does not close those findings or
claim a complete rereview of the changing aggregate harness. No queue was waited
on or polled, and no duplicate jobs were submitted.

## Identity, ownership and drift

The following SHA-256s bind the inspected source regions. Runtime/test hashes
matched the closing observation. All listed source/fixture files observed with
stat were regular mode 0644, except the sealed Python focused runner, mode 0444.
These are review observations, not an atomic freeze, built artifact identity,
installed-package attestation or execution receipt.

```text
2901518ab33f887864fb2f0b689fc2e588f889dc20755006f5a9c1942bc2c01c  python/src/statelessness/campaign.py
122243c6c10dd48758a1d021b18354d1943408f38b26f7f916d7e2119ab07743  python/src/statelessness/_campaign_lifecycle.py
4c05d49a54ebb2b15e0bdb12c74986eacfd996bbc863e50decad685cae4c2c5c  python/tests/test_campaign.py
8d0805ce35d700fe6f7fe9e4b8eb419d9f9c5a1e4a0e76037ecc39074942b5d2  python/tests/test_campaign_lifecycle.py
03e9e4b3e6612766836e85e2ee1728c5ddd4ad92daf264b9249937472c116b94  python/tests/campaign_lifecycle_fixture.py
a87c1c4d2b5592357ea6a284069247fbc7a89e9b94dcbeac3d139d6b74162e8a  python/tests/qualify_campaign_install.py
4545398d812c034dbe9ba0222af2e4272d5448f2763c733bf47498770feeb8c0  validation/native-parity/n06/python/focused-r1/run.py
401993bf223e02051f0f813ac316cc27ffee42f6306b02eb18043d70e6cc2fd6  examples/native-campaign/python/consumer.py
41a025ed7d3da1d8453dfccfe9a925d864a892154ea7e821fe79b21af05dbca4  scripts/native-campaign-consumers/python/consumer.py
516b83703c75c93ea4f7c4b0674eb1855374eada424b7f3496d20c5ac25cc6d8  typescript/src/campaign.ts
9fbc11dfa457ea94e2194b05affc4061aefe10572ec439a704fd1c2ab657ebf0  typescript/src/node_campaign.ts
8c9bdfad1d24a2c0b167a80c584c768ab35b29da7513f01f7549eab609957f59  typescript/src/node_campaign_worker.ts
0e5673c9fb63f5eaea311b36ad0b6b618392cca5f5670fd699bfd943a784cc37  typescript/src/campaign_lifecycle.ts
11edb0a50390eb96ddee4a9f52011feaa968a0147929ea4114a2f46a14fcfce2  typescript/src/campaign_wire.ts
eef2be03aa3500a38e1d1a8c22f1b28c2695f6e678630e83793373d002587238  typescript/src/campaign_test_support.ts
d7da179add5a95c9a8cf4f6f5aeb3c170c586fed98915189e0193594137c29bc  typescript/test/campaign.test.mjs
50b3aeb77e72cb80c192de893025d80d5c110d4f77377766091e84313543a62e  typescript/test/campaign-lifecycle.test.mjs
cc58a3dfbd660aac9306228680f8fc031d008139a824008e6541d60fcdf87b8b  typescript/test/fixtures/campaign-lifecycle.mjs
7b585739473532e5ded08a2ae06d5d825eb78ffe4efb829cadcadc4d6ef74e87  typescript/test/fixtures/campaign-worker.mjs
8dffa9799a2014def5fa27c7c20bb235beed83609a3a1177da3baf49545ee797  validation/native-parity/n06/typescript/qualify.py
1972fd12455c8dacefb8044bc28ff2e540a7786ff5331924ddf9feb73e9d5acd  scripts/native-campaign-consumers/typescript/deterministic.ts
d9a6340a39f349177675bd09badd94520ec84f16b53fdc24566d91be9b05bab4  scripts/native-campaign-consumers/typescript/projection.ts
08138ffad33f5a77cc54c4dc9928609d139f3f098240ad90a335e291cc6cfa04  scripts/native-campaign-consumers/typescript/host-checks.ts
e2738b80cf87863ea68c33b28c2a0fd0690136bd1559e7299ba017b2fad7bf58  scripts/native-campaign-consumers/typescript/host-fixture.ts
e5d934535179ea9262ce164cf865b1ff4b6214bd1f66938d088c41a358e3eea0  scripts/native-guided-support/evidence.py
1630cbdce7d1354d68f5e7917e8b87ae4c324301fc4c538cd53dabae8c218846  scripts/native-campaign-support/execution.py
7c8fc64a6950f98f1ffea2442f533acc845fe61c26ef9a0703386df890161f9d  scripts/native-campaign-support/installed.py
532bb5736cac7a9eb65b5b1f442bb61ba53f5ae7dbb2b8544950411b6231c2a8  conformance/fixtures/native-campaign-public.rs
4d4da9479fe3512589df379cba9a1f3e5df989bba6920e3691479e6b3ef19005  src/campaign.rs
7931c3ef82bf7f2b806448ef8a65f8f2e4a136d38c58d8e06307ee4b090ef44a  src/campaign/lifecycle.rs
a93ee1cf524339743564923dacd6d8aac007e95d9ecd68e00f5f4e08de8b579d  src/campaign/lifecycle/codec.rs
3102ec704a8757f339d558e93faf98c82499076ffc94d8177551aba2af582b3a  src/campaign/lifecycle/tests.rs
bbc7b214510f862738991074f8f2053389c4cac85caf005ebc129df9b06a18d7  src/guided.rs
496c19c7181a6bf3f2af97d3e04df1d3c7b22c241351104b4cd66201f6706ecb  src/explore.rs
e69a80ad96826a27871807f848e22fd213a7bee9f95d76be92069e68504f5beb  src/model.rs
ec19cf028645dd16f6f389cc7fa0fa3a44a72a02838842a906ca11bb2303649b  src/operations.rs
64157bfec922b4ad9bc7d37c715f800c28ebe58451584650d92996848a2e2c1e  docs/native-parity/N06-REVIEW-PYTHON-TYPESCRIPT-R1.md
a12d67480d8a3ad79609a231b9513217e2d089805e20b5973dc77bac8c4081a3  docs/native-parity/N06-REVIEW-CHECKLIST.md
```

During review INTEGRATION changed from
`6e494ce8573bcba3454cce56f81d833335cdf31e2991b58f307376260836e859`
to `fdcb53eb2cccf0a7eff89ae02e92232496687797df1e105a7e57cf1395737829`.
It was reread fully; the addition records the existing exact-comparison
dependencies. N06-QUALIFICATION was inspected at
`a08a8eadf9e4d70e22183098a0f29753e3b5f562f52296fc502cec3ae2df509a`.
The delegated script inspection observed concurrent TypeScript runner/Python
example edits and reread finding regions at the hashes above. Aggregate
`qualification.py` changed from the locally inspected
`312376ce830ae924f2a8ac5a7c32df5ee73958c90a17e3e225b1406962fddc4d`
to an intermediate observation
`f0be77058aa3a06ada54741d912308ab4bf289575917022080cafb0c2428e6fb`;
the final identity check observed
`5c5b08a0fc4f11f3b8ee3622ee0647d2dc730c6e42087de526be65e26894f3e8`.
Neither later revision is fully reviewed.

At final identity capture HEAD had advanced concurrently to
`2bcdd8bfd19744f50b6ed3819ac2451a8de101be` (`test: add independent N06 campaign
contract and reference fixtures`), on the same branch. This reviewer made no Git
mutation. Every listed runtime/test/authority hash and the Q1/Q3/Q4/Q5 finding
files still matched. Two additional aggregate files had drifted:

- `scripts/native-campaign-consumers/python/consumer.py` changed from the table's
  inspected hash to `aa6efbe4814af32ef4d620c99fbbce94d4dcf1205003d6f2dbba15e5f948cffa`.
  A bounded reread confirmed the shared validation operands and explicit
  FixturePanic policy remain; this later revision is not fully reviewed.
- `scripts/native-campaign-support/installed.py` changed from the table's hash to
  `8928c3d5189cddcf5f978cf7d6ea053093102a0113fb0f7035f754a57415ed8c`.
  Lines 1-115 were reread: the same Recorder remains imported at line 12 and
  instantiated at line 67. This preserves Q2's call-site evidence, but the later
  file is not fully reviewed. The imported Recorder and execution.py hashes
  remained unchanged.

The new commit as a whole is not approved by this source review. No future author
hash is implicitly covered by this report.

Read-only commands were `pwd`, `git status --short --branch`, `git worktree list`,
`git rev-parse HEAD`, scoped `rg`/`rg --files`, `cat`, `nl -ba ... | sed -n ...`,
`wc -l`, `shasum -a 256 <listed paths>`, and
`stat -f '%Sp %z %N' <listed paths>`. Only this document was created with
apply_patch. Root owns reconciliation, source freeze, package receipts and later
execution; no currently queued artifact is modified by this review.
