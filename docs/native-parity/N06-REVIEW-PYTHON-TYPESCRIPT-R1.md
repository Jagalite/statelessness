# N06 Python and TypeScript independent review, round 1

Source-only review on 2026-10-22 in the shared isolated checkout
`/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336`, branch
`t3/native-campaign-runtime`, HEAD
`0a26bf5a068e5948d82a3d8bd9eb4063ae85115f` throughout the recorded observations.
Only this document is written by this reviewer. No runtime/test edits, builds,
tests, fixture execution, package installation, Kyoto submissions, staging,
commits, pushes or PR operations were performed. Findings below are source traces,
not reproduced execution results.

The N06 section of the plan, both GUIDED-CAMPAIGN documents in full,
N06-INTEGRATION and N06-REVIEW-CHECKLIST were read. The actual Rust campaign and
lifecycle, lifecycle codec and independent-ledger/fault tests, guided/search
callback paths, RNG and native operations adapter are the authority. The Kyoto
skill was read; execution remains entirely with the coordinating owner.
N05 remains materialized78/reviewed-author90 with final qualification pending;
the existing author job `36c3552df326429fb9e76d872c80aafa` was neither inspected
nor restarted. This review establishes no N05, N06, N10 or 144-cell closure.

## Open runtime findings

R1, the TypeScript sink diagnostic/classification finding, is resolved in the final
bounded source read; its scenario and resolution are retained below.

### R2 — P2: Python's diagnostic fallback can escape the sink boundary

Locations: `python/src/statelessness/campaign.py:158-161`, `:291-294`,
with process draining at `:451-470`.

`_message` correctly avoids an arbitrary exception's `__str__`, but its fallback
`type(error).__name__` can invoke an application metaclass's `__getattribute__`.
For an Exception subclass with a throwing `__name__` lookup and no exact-string
first argument, a sink exception reaches this fallback and raises a secondary
exception. SinkReturned is skipped. On the process path this escapes the receive
loop before the drain/join block, so it can strand already active workers.
The same helper is also used while containing factory and worker failures.

Fix: make all diagnostic extraction nonthrowing, using a guarded builtin type
attribute lookup or a constant fallback; preserve the original error category.
Add a deterministic hostile-metaclass exception witness for serial and process
sinks, checking first-error priority and that all owned processes/bridges join.
This finding is in the new campaign boundary. It does not request a quiet change
to N05's existing callback normalization.

### R3 — P2: Node caller mode can ignore the cancellation token given to factories

Locations: `typescript/src/node_campaign.ts:55,60,67-68`.

With workers=1 and `limits.cancellation=()=>false`, the host creates a new
`context.cancellation` token for the application factory, but selects the original
control object because the limits already contain a cancellation function. That
control polls only the supplied function. A factory calling
`context.cancellation.cancel()` therefore cannot cancel its search or subsequent
claims through the token the host supplied.

Fix: compose the context token with the supplied cancellation function while
preserving the function's limits receiver and the existing deadline. Add a Node
caller-mode factory-cancellation witness with this combination, checking the
cancelled job result and subsequent claim cutoff.

## Test and contract findings, not demonstrated runtime defects

T1, the Python unsnapshotable-output fixture finding, is resolved below.

### T2 — P2: TypeScript's claimed exact lifecycle codec narrows u64 job counts

Locations: `typescript/test/fixtures/campaign-lifecycle.mjs:89,122,171-188`;
`typescript/test/campaign-lifecycle.test.mjs:261-277`.

The state codec stores the first seven Rust u64 wire counters as JS numbers and
rejects values above Number.MAX_SAFE_INTEGER. A valid Rust initial state with
first_job=0, jobs=9007199254740992, requested=not_started=jobs and all other
counters zero is tiny and requires no huge allocation, yet this decoder rejects
its first count. The test explicitly treats unsafe counts as malformed. This
does not meet the checklist's full-u64 test-codec gate or the fixture's claim to
the exact Rust layout/domain.

The public runtime's safe-integer jobs/cases/steps restriction is documented in
N06-TYPESCRIPT.md and is a distinct host representability limitation; it is not
silent rounding. Keep that limitation separate from codec wire acceptance.
Use bigint protocol DTO counters in the test-only codec, and perform an explicit
separate representability check when mapping to the native runtime. Add valid
large-count state vectors as well as overflow/invalid-state negatives. Root must
retain any unimplemented full-domain runtime cases as limitations, not parity.

### T3 — P2: Bounded-send tests still infer blockage from a short quiet interval

Locations: `python/tests/test_campaign.py:83-91,383-398`;
`typescript/test/campaign.test.mjs:449-464`;
`typescript/test/fixtures/campaign-worker.mjs:117-124`.

Python's saturation signal is set at factory entry. TypeScript improves this by
waiting for model disposal, but disposal precedes the Complete RPC and send.
Both then use a short negative wait (100 ms / 80 ms) to conclude that workers
cannot claim beyond capacity. A delayed completion/send path can satisfy those
observations even if its channel would later admit excess work. Neither signal
establishes that all relevant workers reached the blocked-send boundary required
by the review checklist.

Keep the positive progress witnesses: they do exercise receiver release during
a held sink. Strengthen the upper-bound assertion with deterministic evidence
that the expected sends are blocked/admission-limited before releasing the sink,
for example test-only transport instrumentation tied to the real channel. A
timeout remains a watchdog or failure; a short absence of progress is not the
queue-bound proof. This is an acceptance-test weakness, not evidence that either
current production queue exceeds its bound.

### T4 — P2: TypeScript range replay comparison omits private input values

Location: `typescript/test/campaign.test.mjs:188-197`.

The full-run public-reference comparison explicitly reads `.value()` because
deepEqual does not inspect `CampaignInput` private fields. The range comparison
only deep-compares `slice` to `jobs.slice(2, 4)`, so different generated input
values can pass. The maximum-ID comparison also only deep-compares the report.

Fix: compare failure-input value sequences and the input values in every retained
corpus entry for the replayed range, preserving entry boundaries. Apply the same
payload projection to any maximum-ID witness retaining inputs. This is a defect
in the authored replay assertion, not evidence that range replay currently differs.

## Findings resolved during concurrent authoring

- T1: `Counter.step` now returns an `Opaque` output with a faithful `__deepcopy__`
  and a rejecting `__reduce__` (`python/tests/test_campaign.py:55,103-110`, source
  `2c426904...`). This satisfies the existing local snapshot boundary while
  retaining the intended non-picklable-output witness. The prior raw-lock fixture
  in `4ddac742...` was invalid; no N05 change was needed. Tests remain unexecuted.
- R1: TypeScript source `1bd52448...` corrected the hostile message-getter path by
  copying a safe diagnostic into an owned ModelError before lifecycle submission.
  That snapshot still used unguarded `error instanceof ModelError`, so a revoked
  proxy thrown by a sink could reject the caller campaign and skip SinkReturned
  and Finish. The final bounded read, `516b8370...`, adds `isCampaignModelError`
  with a guarded instanceof (`campaign.ts:131-133`) and uses it at the sink boundary
  (`:197`). Both observed diagnostic defects are resolved in source. Synchronous
  throw and rejected-promise proxy tests with aggregate assertions still require
  execution on the final package.
- The initial Python source had Pipe/Process construction outside the startup
  catch, so a later Pipe failure escaped before draining previous workers.
  Source `1ac59f4f...` now covers Pipe, Process and bridge setup in the start
  boundary (`campaign.py:413-440`) and retains started processes for joining.
  `test_partial_pipe_startup_failure_drains_existing_rendezvous` was authored at
  `python/tests/test_campaign.py:484`. Resolution is source-only, not executed.
- The earlier Python feedback-cleanup classification is corrected by
  `_CleanupPanic` (`campaign.py:188-204,229-241`). A cleanup ModelError no longer
  becomes a search ModelError. `_deliver` submits Receive and returns on Discard
  before application decoding (`:266-278`); later drain reports do not run decode.
- TypeScript now uses a separate coordinator worker (`node_campaign.ts`,
  `node_campaign_worker.ts`). Its coordinator can service Complete/Claim and
  bounded sends while the caller blocks synchronously in a sink. This resolves
  the earlier main-event-loop-only design objection at source level. Tests using
  Atomics.wait on the caller were authored; execution remains pending.
- TypeScript originally used `job.jobId` after the mutable report reached the
  sink. The interim rejection guard prevented silent success but still lost the
  aggregate on mutation. Source `231ffdfa...` captures `receivedJobId` before
  invoking the sink (`campaign.ts:200-202`), preserving the acknowledgement ID.
- The TypeScript deadline test originally mocked only `performance.now`, making
  CampaignControl.now's timeOrigin addition NaN. Source `afc5efce...` supplies
  `timeOrigin: 0` at `campaign.test.mjs:308`. This is a source correction only.
- The completed TypeScript subreview also confirms that cancellation function
  calls retain their limits receiver. R3 concerns composing the separately
  supplied context token, not that resolved receiver issue.

Swift Sendable relocation and Go diagnostic containment were outside this
Python/TypeScript review; no independent disposition is claimed for them.

## Confirmed source structure and remaining gates

Both production runtimes invoke their actual private lifecycle for claim,
completion, receipt, acknowledgement, worker failure and finish. Python uses
spawned processes plus independent bridge threads; TypeScript uses a coordinator
and job workers. The configured worker count selects caller versus spawned path,
including workers>1/jobs=1. Complete is acknowledged before encoding/sending;
receipt releases the sender before the sink returns; only explicit application
report inputs cross the worker boundary. Queue admission is separate from sink
acknowledgement, and neither runtime sorts sink delivery by job ID.

Lifecycle source preserves inert late claims, property versus job versus
infrastructure errors, first-sink-error precedence, worker-only continued
delivery, per-counter saturation and sticky accounting loss. Model-factory
ModelError has absent metadata/known zero work; feedback-factory ModelError has
model metadata/incomplete accounting. Existing public guided functions are used.
The reviewed N05 callback-normalization policy is not recast as Rust unwind
equivalence. No required N05 implementation edit was identified by this review.

Both test fixture families contain an independent identity ledger and admission
predicates, test-only codecs, stale/duplicate events and a duplicate-Deliver
fault. Newly authored tests call actual enumeration/fuzz/shrink/record/read/replay
and include healthy strict/relaxed controls. These are substantive source tests,
not passes. They must execute on the final source, along with baseline suites,
the independent public Rust protocol and freshly installed consumers. Installed
consumer code was inspected selectively for public imports, provenance checks,
worker-local values and callback/barrier design; this is not a complete review
or execution of the root's changing integration harness.

The completed TypeScript subreview identifies unfinished acceptance work separate
from the defects above: a barrier between Complete and encoding/send proving that
a completed property failure already prevents another claim; a genuine startup
failure witness for lostWork=false; positive maximum-u64 decoding witnesses for
summary counters, state totals and pending/delivering IDs; and syntactically valid
inconsistent summaries in the self-model BFS/fuzz alphabet. Existing worker-exit,
transfer-error and direct negative-codec tests do not substitute for those paths.

The installed TypeScript validation consumer changed during review. Its latest
`deterministic.ts:150-153` uses representable host configurations for four wide-count
case names, replacing earlier unsupported dispositions. Those are different input
vectors from the Rust full-u64 cases and must remain explicit in integration
comparison evidence; they cannot establish the missing full-domain acceptance.
The consumer emits its actual config. Widening N05 case/step domains requires a
separate review; no N05 core edit is requested here.

## Source membership and drift

Authors were active throughout. No source waiting/polling or freeze was requested.
The review used bounded reads and hash observations; the following values pin
the inspected snapshots. Files first appearing during review are marked added,
not falsely reported missing. Findings apply to their pinned source; later edits
require the owning integrator's reconciliation.

The completed delegated TypeScript review was reconciled against bounded local
rereads. The latest hashes below matched before/after those rereads for the open
finding regions. Python's latest runtime/test entries cover the reread diagnostic
and output-fixture regions, not an additional full review of every concurrent edit.
Campaign-file membership was unchanged in the final scoped listing; the installed
consumer's `consumer.ts` and `host-checks.ts` appeared during the delegated review.
The closing hash observation then detected further TypeScript edits. A bounded
reread confirmed R1's new classification guard and R3's remaining control selection;
new transport-probe plumbing in that later node host was not fully reviewed.

| File | Initial SHA-256 | Later inspected SHA-256 |
| --- | --- | --- |
| python/src/statelessness/campaign.py | b6159e33e7a8c2d194ad0c2e86703f5e31ddb45aa0fc419e2e3c279f363e5c63 | 346620ec8321dbcd12831f360373f5386c0cc9000489c93f056f030606717fe5; finding regions reread |
| python/src/statelessness/_campaign_lifecycle.py | 122243c6c10dd48758a1d021b18354d1943408f38b26f7f916d7e2119ab07743 | unchanged |
| python/src/statelessness/__init__.py | fbb84a87d5d1736b4c699254e4dc28cabb9ca43b7c73cd40f23201edb3f77b00 | unchanged |
| python/tests/campaign_lifecycle_fixture.py | added; 9ccdb06c11d8d59968592045eeaf76f6bb4b3401c6a491e22d8e9d4aebf6fc24 | d7f7654668e227f1ebe88f6f91531717cf336beb73fc5ae5cefb81cef13e2798; later drift, not fully rereviewed |
| python/tests/test_campaign.py | added; 7b3fc9f54f3886ab9182865d9f7b06236405030e31ca58d79c285fdd88fc34f8 | 2c42690478364a2960d0228809437965884a40760c26c78acdedbd4737550ce2; finding regions reread |
| python/tests/test_campaign_lifecycle.py | added; a0efb944b80d86cddd6ee77bc947c5c3d4e7a15e76d4d2e5ff8e17f2145d72ff | unchanged |
| typescript/src/campaign.ts | 9f328446d6dedc2a13bb676c6090dd24c05864d4fdc361ded63f4234cc2cc536 | 516b83703c75c93ea4f7c4b0674eb1855374eada424b7f3496d20c5ac25cc6d8; final diagnostic regions reread |
| typescript/src/campaign_lifecycle.ts | a49c3bcfa2af11c60286dada6c6257a14a4dccdb6aaab39eb9ccbdf7e135e272 | 0e5673c9fb63f5eaea311b36ad0b6b618392cca5f5670fd699bfd943a784cc37 |
| typescript/src/campaign_wire.ts | 11edb0a50390eb96ddee4a9f52011feaa968a0147929ea4114a2f46a14fcfce2 | unchanged |
| typescript/src/node_campaign.ts | cf24829b0038a1b8bcacdf768d37506e07f2fedfb2a5bba22b98ddccd5ff2046 | 0cd8afb39341c088761636896b23c7ec1f6124f786c0b7de3791ba2e7c96c710; final cancellation region reread |
| typescript/src/node_campaign_worker.ts | b6a4c38a92be7e8d7cf0768f53eac831964354bf50b8c00c433a6a947934bb5e | 3edf0ebf8cc1d18d30d4b724f7b23cdbef6edfefb53561d28a991e50927dfea8 |
| typescript/test/campaign.test.mjs | 89b3ee165a67a33ffb2d543da0bbe722762f7c9d6092c3e4dbca33f588a29fb9 | 08f8d87d922e9f82dd4439dde8586ad0ebb21d39c0d8151bfef3fb678d143911 |
| typescript/test/campaign-lifecycle.test.mjs | added; f798412298e86edbd6d7f43601cd9ba630984ea0accc12f6bc87cf807d41d84e | unchanged |
| typescript/test/fixtures/campaign-lifecycle.mjs | 63234f803bbc6346475fd8316dd66434bf228134a72e2178dba1cd38579c7e7f | unchanged |
| typescript/test/fixtures/campaign-worker.mjs | 203cfa469862db2a98b2a43ef2bf61174e3d669e6a0784f5e8bfcfe2480f914b | 4b1f0c0caebf9bf48abdd815c020393d10d17e98d0707d4a21444cbf9a1f96bd |

Additional reconciliation snapshots (selective reads, not complete harness review):

```text
1bd5244884ef50b155c0ede7db7b012e77bf3b9c3c75772a031a14e57865b9c9 typescript/src/campaign.ts (before classification guard)
008c1502cb9182a5e780e4c4f29a54e62e706c72f3120f3fa137936de0e8cb4b typescript/src/node_campaign.ts (completed delegated review)
1972fd12455c8dacefb8044bc28ff2e540a7786ff5331924ddf9feb73e9d5acd scripts/native-campaign-consumers/typescript/deterministic.ts
0e2666acc179ffdacf70d0b6a63a3151f101ba0e93f9a74a1b4533a6df8e3bea docs/native-parity/N06-INTEGRATION.md
```

All runtime/fixture files in the scoped stat observation were regular files with
mode 0644. Rust campaign/lifecycle hashes matched their initial observations:

```text
4d4da9479fe3512589df379cba9a1f3e5df989bba6920e3691479e6b3ef19005 src/campaign.rs
7931c3ef82bf7f2b806448ef8a65f8f2e4a136d38c58d8e06307ee4b090ef44a src/campaign/lifecycle.rs
a93ee1cf524339743564923dacd6d8aac007e95d9ecd68e00f5f4e08de8b579d src/campaign/lifecycle/codec.rs
3102ec704a8757f339d558e93faf98c82499076ffc94d8177551aba2af582b3a src/campaign/lifecycle/tests.rs
bbc7b214510f862738991074f8f2053389c4cac85caf005ebc129df9b06a18d7 src/guided.rs
496c19c7181a6bf3f2af97d3e04df1d3c7b22c241351104b4cd66201f6706ecb src/explore.rs
e69a80ad96826a27871807f848e22fd213a7bee9f95d76be92069e68504f5beb src/model.rs
ec19cf028645dd16f6f389cc7fa0fa3a44a72a02838842a906ca11bb2303649b src/operations.rs
4cef4f85ff012660764b473f4e8f30ca2df1384f51932af175d090cf58c92fb6 python/src/statelessness/search.py
d3f49de2cda63b0b6c4031809b74235b773ee59ad9a65be7fc72029e83edf397 python/src/statelessness/core.py
e26afd7623f4cd1566be8b5dd28ac063a24bdf1ae0c164e4265af33294359caf python/src/statelessness/guided.py
298ba5cbb468bb9976a44e02fabec3354e8d7da0ef2381eb1c1ebbc5e4930dc2 docs/native-parity/N06-INTEGRATION.md
a12d67480d8a3ad79609a231b9513217e2d089805e20b5973dc77bac8c4081a3 docs/native-parity/N06-REVIEW-CHECKLIST.md
```

Read-only identity commands were `pwd`, `git branch --show-current`,
`git rev-parse HEAD`, `git status --short`, `git worktree list`, scoped `rg --files`,
`shasum -a 256 <listed paths>` and `stat -f '%Sp %z %N' <listed paths>`.
Source inspection used `cat`, `rg -n`, and `nl -ba ... | sed -n ...`.
No package artifact identity or executable receipt is claimed by these hashes.
