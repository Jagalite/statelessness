# N06 Go campaign runtime

Implementation in `go/campaign.go`, `go/campaign_callbacks.go` and the actual
private decision core `go/campaign_lifecycle.go`. Source base is
`0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`; work is confined to the shared
`t3-25250336` checkout. Runtime/tests/examples are authored and source-reviewed, **not execution-qualified**.
The initial r1 Go source is frozen; its single submission is recorded below.
Current checkout source has post-r1 R2 changes and requires separate future
qualification. Queue admission or source review must not be read as execution.

## Public interface for installed consumers

Import `github.com/Jagalite/statelessness/go` (package `statelessness`).
`DefaultCampaignConfig()` returns master0, first0, jobs16, workers1, buffer1,
ordinary default fuzz and stop-on-failure false. Its exported fields are
`MasterSeed`, `FirstJob`, `Jobs`, `Workers`, `ResultBuffer`, `Fuzz`, `StopOnFailure`.
`JobSeed(master,id)` derives one SplitMix64 output from wrapping master+id.

```go
RunCampaign[S,I,O](config CampaignConfig, limits RunLimits,
    factory func(uint64) (CampaignModel[S,I,O], error),
    sink func(JobReport[I]) error) (CampaignReport, error)
RunGuidedCampaign[S,I,O,K](config CampaignConfig, limits RunLimits,
    guidance GuidanceConfig,
    factory func(uint64) (CampaignModel[S,I,O], error),
    feedbackFactory func(uint64, GenerateModel[S,I,O]) (CampaignFeedback[S,I,O,K], error),
    sink func(JobReport[I]) error) (CampaignReport, error)
```

`CampaignModel` contains `Model GenerateModel[S,I,O]`, `Metadata func()
(Metadata,error)` and optional `Close func() error`. No codec is required.
`CampaignFeedback` contains `Feedback Feedback[S,I,O,K]` and optional `Close`.
The feedback factory receives the job's model; public `GuidedFuzzWithLimits`
performs the search. Cleanup runs feedback then model, on the worker, before
completion/send and sink delivery. Model metadata has no additional identity
validation. A failed factory retains responsibility for partially created resources.

`JobReport`: JobID/Seed uint64, Config FuzzConfig, Metadata *Metadata,
Outcome JobOutcome[I]. Outcomes have Kind `completed`, `factory_error`,
`model_error`, `panicked`; completed has Report *FuzzReport[I], errors have Error,
panicked has Panic string. Model factory errors have nil metadata and known-zero
work. Feedback factory errors retain metadata and incomplete accounting. Native
metadata/cleanup returned errors become model_error; metadata errors have nil
metadata. Recoverable callback panics are panicked, including those contained by
the underlying standalone fuzz boundary; N05 code is not changed. For automatic
enumeration, call `CampaignAuto(model CampaignModel[S,I,O], equal, options)` inside
the factory. It installs tracking before public Auto captures enumeration, iterator
and equality callbacks, retaining metadata/cleanup. No codec is introduced.

Go cleanup has an explicit native-only distinction: a returned Close error is
model_error, while a recoverable Close panic is panicked. Rust Drop cannot return
an error. Cleanup runs before delivery; cleanup failures from later drained jobs
cannot replace an earlier sink error. Returned errors are copied into ModelError:
its Message is read directly; other Error methods run once under recovery, with
`error diagnostic unavailable` if they panic. Diagnostic failures never convert a
returned factory error into a panic or replace the first returned sink error.
Job-local diagnostic extraction snapshots/restores the worker panic tracker,
including reentry into guarded callbacks; an earlier genuine panic retains its
message. This contains panic classification, not arbitrary application side effects.
Among returned search/cleanup errors, the last cleanup error wins (feedback then
model). A tracked panic takes priority over returned errors; a later cleanup panic
replaces an earlier panic. Cleanup runs once for each successfully constructed
resource. Feedback leases, including copied handles, expire before Error methods.

### Separate N05 composition finding

Existing `Auto` catches enumeration/iterator/equality panics into ordinary
ModelError inside its captured closures. Passing a model already composed with
that API to RunCampaign cannot reconstruct the lost provenance; it remains
model_error. The campaign-aware CampaignAuto constructor supplies the equivalent
Panicked path without modifying N05. Retrofitting already-composed Auto (or other
opaque closures that intentionally catch panics) would require an independently
reviewed structured-panic provenance change in the N05/common callback layer.
This task does not silently change `ModelError`, `call`, `Auto`, or guided core.

The existing public Go error contexts also differ from Rust in the root-owned
comparison fixture: `initial state: initial error`, `transition: step error`,
and `state check: check error` versus Rust's `initial_state: initial error`,
`step: step error`, and `check_state: check error`. These inherited N05/common
callback spellings remain unchanged. Exact comparison must retain the mismatch;
the focused Go suite does not close it or rewrite diagnostics into agreement.

`CampaignReport` fields: Requested/Started/Finished/NotStarted/Delivered/Failures/
Errors int; ReportedTransitions/ReportedCases/ReportedSkippedChecks uint64;
AccountingComplete bool; Termination {Kind string, Error error}. Kinds are
jobs_completed, failure_found, cancelled, deadline, callback_error, worker_error.
Public configuration errors return the outer error without running callbacks;
job and infrastructure failures remain in structured reports.

### Native count and wire domain

Jobs and the seven aggregate job counts are native signed `int`, as are N05
FuzzConfig.Cases/MaxSteps and FuzzReport.Cases. The admitted count domain is
0..MaxInt (Jobs must be positive), usually 0..2^63-1 on 64-bit Go and
0..2^31-1 on 32-bit Go. IDs, seeds, summary work counters and aggregate work
totals retain the full u64 domain. They are never narrowed through int.

The test-only core codec matches Rust's canonical layout on this representable
intersection. Rust64 can admit jobs=2^63 or larger with cases0 and a fitting ID
range, while Go cannot represent those job counts. Such a consistent Rust state
is **unsupported by the native count representation**, not malformed wire.
The decoder returns `campaign lifecycle count exceeds native int range` before
narrowing. Tests admit MaxInt without allocating per-job state, and encode both
requested/not_started above MaxInt together to isolate representation rejection.
Full-u64 count-wire qualification remains open; it would need a separate wire
representation or a separately reviewed count-API change, not integer coercion
or an unannounced N05 fuzz-count change.

## Host ownership and timing

Workers1 executes on the calling goroutine. Configured workers>1 spawns actual
goroutines, even for jobs1. Sink calls are serial on the caller. Buffer0 uses an
unbuffered Go channel; successful channel send releases the worker before the
sink returns. Complete precedes sending; receipt drives Receive before sink entry.
No delivery sorting occurs. Factories must be concurrency safe and return fresh
models/feedback with no mutable aliases across jobs. Go goroutines support actual
CPU parallelism subject to the host's GOMAXPROCS and scheduling.

State/output/feature keys stay on the worker. Report inputs use the application's
explicit CloneInput hook, including a final detach before model cleanup. CloneInput
must faithfully copy slices/maps/pointers; no serialization or closure transport is
guessed. The caller owns received report values and must not concurrently mutate
aliases retained in model closures. Immutable scalar fixtures need only ValueCopy.

RunLimits cancellation uses the existing atomic flag; duration is snapshotted.
Deadlines cover factories, metadata, feedback and search. Sink/cleanup/callbacks
are never preempted. Late rejected Claim cannot change a stop. Factory/search
errors do not activate StopOnFailure. Sink rejection/panic drains and joins without
another sink call; first callback error wins over later worker failures. Go runtime
goroutine creation has no recoverable OS-thread-start error; private executor
injection tests exercise start failures. Recoverable infrastructure panics and
unexpected worker goroutine exits lose accounting. Goexit on the calling serial
path, fatal runtime errors, process aborts and data races are outside recovery.

The lifecycle codec, independent ledger and deliberately faulty model are test-only.
There is no public checkpoint, queue resume, generic CampaignReport merge, durable
delivery guarantee, hard RSS limit, N10 assurance integration or profile promotion.
N05 author90 final qualification remains pending on its existing owner/job.

## Qualification handoff

External-module example: `examples/native-campaign/go/main.go` with `go.mod`.
It requires `github.com/Jagalite/statelessness/go v0.0.0-n06` from the source-bound
private module proxy prepared by qualification; no local replace directive or
checkout import is present. Run the installed example with `go run .` and
`go run . --host-check` through Kyoto. The latter uses workers3/buffer0, barriers,
fresh model-local counters, real metadata/feedback/cleanup callbacks and
out-of-order sink receipt. It emits `n06-go-example-v1` demonstration JSON;
the separately owned Rust comparison consumer emits `native-campaign-v1`.
Add `--ordinary` for the ordinary public campaign path; focused qualification
executes both ordinary/guided variants in serial and multiworker modes.

The root owns Rust/native comparisons and final aggregate receipts. The early
protocol is `N06-RUST-CONTRACT.md`; `N06-INTEGRATION.md` governs submission.
Focused Go execution, if submitted, must freeze package/examples/tests, retain
commands/logs and source identities, and use shared-default Kyoto. Queue admission
is not execution. Existing N05 jobs must not be restarted.

## Source-review reconciliation

`N06-REVIEW-GO-SWIFT-R1.md` was read fully. Its Go lease finding is addressed by
campaign-local shared-lease expiration before diagnostic extraction; both initial
and transition formatter witnesses retain copied buffers and would panic in Clone
if it were reached. The native-count finding is addressed by the domain statement
and consistent boundary tests above; full-u64 count support is not claimed.
The nested Go review's Auto finding is addressed by CampaignAuto plus the explicit
separate N05 limitation. Added tests cover cleanup priority across search/error/
panic combinations and guided final-transfer cloning/panic after feedback cleanup.
These are authored fixes/tests, not executed qualifications.

The final round-1 worker-loss-oracle finding is also addressed: audit.worker_loss
compares the core flag with independently observed WorkerFailed.lostWork; every
event's audit.admission derives from ledger claims/completions/receipts/sink
returns/stops/finalization. Finish never trusts the core loss flag. A corruption
witness first loses accounting through ModelError, invents worker loss on a
lostWork=false event, and detects both that flag and the resulting premature
Finish. The existing duplicate-Deliver pipeline remains intact. The private
audited fixture is now build `go-campaign-lifecycle-fixture-v2`, properties3,
codec2 (explicit ledger-finalization byte); the Rust-compatible core codec is
unchanged except for the documented native-count error diagnostic.

The second nested source review found diagnostic reentry could still mutate the
job panic tracker. Every job-local error normalization now restores the prior
tracker. New factory/initial/transition/feedback-close/model-close witnesses
cover both no earlier panic and an already tracked genuine panic. Sink/start
errors use standalone owned diagnostics because they have no job tracker.
The focused qualification driver addresses the same review's catchable-
cancellation cleanup, durable identity-failure status, final frozen/origin
identity recheck and complete permission modes. Four embedded driver guards
run first in the eventual focused job; source review does not qualify real
process cancellation. The author stopped editing before preparation.

The focused snapshot selects only the Go module, this owner's external
example, explicit baseline corpus files used by Go tests, and its qualification
runner. Root-authored `conformance/fixtures` and installed-comparison consumers
are excluded from this focused identity; their source stability and independent
seal remain root integration gates. No other author is assigned to the Go module
or this external example. The qualification-runner author finished before
freezing. No shared queue policy, N05 job or aggregate job is changed.

The third nested read-only review returned no actionable blocker for the final
diagnostic-provenance fix and its ten regression cases. Its verified SHA-256s:

```text
7a26f414293241e0d9ef126e0ac9735bfddb2e2764612d95f0e3780c134e9ec1  go/campaign.go
946250e5ebe55c65a20bbb958e9698ebcba6e1c09efd0c2f7a74befef9d4286d  go/campaign_callbacks.go
6c0b065d9ed5587e644ad16f8c40e696000b2512273b89f5d702e059b7a6359f  go/campaign_review_test.go
```

Owned source inventory: `go/campaign.go`, `go/campaign_callbacks.go`,
`go/campaign_lifecycle.go`, `go/campaign_test.go`, `go/campaign_review_test.go`,
`go/campaign_lifecycle_test.go`, `go/campaign_lifecycle_codec_test.go`,
`go/campaign_lifecycle_model_test.go`, `go/CAMPAIGN.md`, the campaign paragraph
in `go/README.md`, `examples/native-campaign/go/{go.mod,main.go}`, this document,
and `validation/native-parity/n06/go/{qualify.py,README.md,.gitignore}` plus
its focused-run manifests/receipts. No N05 implementation, public profile,
root integration harness, Rust fixture, or other language source is edited.

## Focused frozen handoff, 2026-10-22

Run directory: `validation/native-parity/n06/go/runs/focused-r1-20261022`.
Preparation copied/hashed files only; it did not execute tests or consumers.
All Go module/example authors stopped editing before preparation. The snapshot
has 140 members, including directories and the runner. No aggregate or other
owner's job is included or restarted.

```text
frozen.json SHA-256: 0b053642eb9a341fcc9c8d99d309e5fbbddd3cd2794f0ac058ec27a0c2474238
runner SHA-256: 62303a2c4baba2a5674dcec9d31e2f51f77ad64994792cc3a1735911e3f7a65f
```

`origin.json` binds selected checkout files, types, modes, sizes and hashes;
`frozen.json` pins the entire read-only snapshot and origin hash.
`prepare.json` records the exact preparation command, and `submission-plan.json`
records the requested Kyoto command. The queued command must execute this frozen
runner with the exact `--expect-frozen-sha256` value above. This document is the
Go owner's source-stability signal for that identity only; it does not authorize
or independently seal the final cross-language aggregate.

One shared-default Kyoto job was submitted: `9dc6574d3c8840f6b5cb7dd3d0e7ef3b`.
Last `show` observation: **queued**, started/finished/exit_code all null;
`result.json` does not exist. No tests, guard regressions, package install or
consumer execution can be claimed from this receipt. No watcher was started.
The exact requested and effective commands are identical:

```sh
python3 -I /Users/jagatranvo/.t3/worktrees/stateless/t3-25250336/validation/native-parity/n06/go/runs/focused-r1-20261022/source/validation/native-parity/n06/go/qualify.py execute /Users/jagatranvo/.t3/worktrees/stateless/t3-25250336/validation/native-parity/n06/go/runs/focused-r1-20261022 --expect-frozen-sha256 0b053642eb9a341fcc9c8d99d309e5fbbddd3cd2794f0ac058ec27a0c2474238
```

Submission used `--estimate 600 --cpu-slots 2 --memory-mb 2048` and the run
directory as its exclusive resource. No retry opt-in or queue policy change.
`kyoto-submit-command.json`, `kyoto-submit.json` and `kyoto-show.json` retain exact
submission/effective-command receipts; `handoff.json` is the compact manifest
of source identity, live handle and pending gates. All selected checkout files
still matched `origin.json` at handoff. Frozen inputs must not be regenerated.

Pending execution is four driver guards, all Go package tests/baseline fixtures,
original/minimized fresh-process replay, focused race tests, vet, fresh module
installation and ordinary/guided serial/multiworker examples. Failures stop later
phases and retain command logs plus installed/source before/after observations.
Root should inspect the existing job and its eventual `result.json`/identity/log
receipts without creating a duplicate submission. This owner makes no runtime
pass claim and performed no staging or commit.

Root's public Rust comparisons, inherited Go diagnostic differences, precomposed
Auto provenance limitation, full-u64 count-wire gate, N05 final qualification
(including the separately documented Swift feedback metadata provenance gap),
and the independent aggregate source seal remain open. The final cross-language
aggregate remains blocked; this focused job cannot close those gates.

## Post-r1 source fixes for independent review R2

At the start of these fixes, checkout HEAD was root's Rust/reference commit
`2bcdd8bfd19744f50b6ed3819ac2451a8de101be`. It advanced externally to
`188a1e4a69938cb4c433a7df8d53f16ab6044b50` during the targeted transport review;
the reviewed Go source hashes remained unchanged. The implementation owner read the
complete `N06-REVIEW-GO-SWIFT-R2.md` and matched its principal Go source hashes
before changing them. No N05 or Rust/reference file is changed. The old r1
manifest and all 140 snapshot members still match their original identity;
job `9dc6574d3c8840f6b5cb7dd3d0e7ef3b` is neither replaced nor resubmitted.
This section supersedes the earlier source-readiness statement for current
checkout bytes, not the historical r1 receipt.

R2-1: the private runtime's `sendBlocked` observation follows a native
nonblocking `select` on the actual result channel taking its `default` arm.
Only then does it invoke the private hook and perform the blocking send.
Complete still precedes the attempt; `afterSend` still follows actual send
return. These hooks run outside the lifecycle coordinator lock. No public
application API or new transport/queue abstraction is introduced.

The buffer0/buffer1 tests gate noninitial reports until the first sink has
entered, avoiding stale pre-receipt blocked notifications. The sink waits for
both workers' rejected transport admissions, checks Complete→waiting report
phases, exact starts/completions of buffer+workers+sink, and exactly buffer+1
send returns. While the sink is held there is no receiver to free capacity, so
these workers cannot return their sends or claim again. After release, tests
check successful delivery or first-error/panic/hostile-error drain, every send
return and cleanup count. Their timeout is a failure bound, not absence-window
evidence. External installed-consumer progress/order tests remain a separate
proof class and are not rewritten by this private probe.

R2-3/R2-5: the current driver now compares canonical executing `__file__`
with the exact sealed regular-file member, including mode/size/hash, before any
subprocess and at final receipt. It records the invocation and repeats driver
and selected-tool checks at command boundaries. Nested guards use the pinned
absolute Python and sealed script path; Go commands use the pinned absolute Go.
The race path enforces `CGO_ENABLED=1` and explicit absolute `CC`. On macOS a
pinned xcrun discovers the developer clang (a `/usr/bin` selector is rejected)
and SDK directory; both CGO profiles' Go configuration is compared before/after.
Tool/driver/configuration mismatches fail durable receipts. The author stopped
editing and a fresh independent driver review returned no actionable findings
for R2-3, R2-5 and retained-leader cleanup (Q2). No runtime qualification is
inferred.

The additional root cleanup instruction is implemented with POSIX
`waitid(WNOWAIT)`: every normal, nonzero, timeout and cancellation path retains
the leader PID through TERM/KILL, checks child ownership before signalling, and
reaps last. Missing wait support rejects before spawn; lost ownership stops
numeric group signals and fails the receipt. Authored guards exercise real
runner/receipt code with mocked processes/signals. These are not real-process
cleanup qualification. The driver assumes sole child-reaper ownership and does
not contain descendants that deliberately escape the owned group.

Old r1 only has its earlier timeout/exception cleanup; it does not establish
this retained-leader/all-exit boundary. An eventual old r1 success cannot qualify
these changed source bytes or cleanup behavior. The current README also limits
identity claims to sampled on-disk identity/configuration: loaded interpreter
memory, Go helper binaries, SDK contents, dynamic libraries and other transitive
dependencies are not a fully sealed toolchain. See the runner README for the
precise boundary and guard inventory.

No post-r1 build, test, guard test, consumer execution, source preparation or
additional Kyoto submission has been performed. No staging or commit. Future
execution remains explicitly pending root coordination after review; the
existing snapshot must not be regenerated to incorporate these fixes.

The fresh R2-1 delegated source review returned no actionable findings. It
confirmed that an enlarged buffer produces a count mismatch or missing-witness
timeout, and found no concrete race/hang/false-pass schedule in the eight test
variants. The positive witness is channel nonadmissibility, not a claim that a
goroutine has already parked. Source identities rechecked by that reviewer:

```text
6638852583903bbeb70592158c20cf831cfc5934b38b1092cd1148ab50ee1a7c  go/campaign.go
6d57c2794db57fdf3492bad154f33888808458d0e6ebdd8d16054efc7c0655dc  go/campaign_test.go
b3cc2da75a3d07799e1bd045ed953f8260e18d5b868dd59d3dfceee136f20927  go/campaign_lifecycle.go (unchanged)
```

The review was read-only; it did not execute tests or examine the separately
authored driver changes mid-edit. The r1 job contains the earlier transport
test/probe source and cannot supply execution evidence for this R2-1 fix.

The separate final driver review read both current files and the complete R2
review. It confirmed sealed-driver binding before children and at completion,
absolute tool/configuration enforcement, and retained-leader cleanup on normal,
nonzero, timeout and cancellation exits. Lost ownership prevents further group
signals; no group probe or signal follows reaping. Its bounded source-ready
disposition retains the README's sampled disk-identity, transitive-dependency
and sole-reaper limits. Both hashes remained unchanged through that review:

```text
026573750c6be950b916ced556c2670b803a542f6189e5d534ee070595cc72ac  validation/native-parity/n06/go/qualify.py
bb7da66e2f14e9ccf798771d26e3da159b9f36e3c2dbc9a57b806ad0a9e8a0d5  validation/native-parity/n06/go/README.md
```

Review task identifiers are retained in the
[post-r1 source observation manifest](../../validation/native-parity/n06/go/post-r1-source.json),
along with the five changed source/document paths and their full hashes, the
unchanged r1 snapshot identity and historical command. This is a source handoff,
not a new frozen payload or executable receipt. Both fresh reviews were
read-only; no new job was submitted and current runtime, race, guard and real
process-cleanup qualification remain unexecuted. Root retains coordination of
any later preparation/execution and the independent aggregate seal.
