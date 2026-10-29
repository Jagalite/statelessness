# TypeScript native campaign runtime

Implementation base: `0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`, branch
`t3/native-campaign-runtime`. This document coordinates the TypeScript owner with
the root installed-consumer harness. The source candidate and focused qualification
runner are authored; **no execution or qualification is claimed yet**. N05 author90 qualification and
its missing FREEZE remain with the existing owner; no N05 jobs are restarted.

## Public entries and harness interface

Package: `typescript`, npm `@jagalite/statelessness`. Build: `npm run build`.
Tests: `npm test` (builds, then `node --test test/*.test.mjs`). All commands must
execute through shared-default `/Volumes/seed1/Projects/kyoto/kyoto.py`, from a
frozen snapshot with a source manifest; queued admission is not execution.

The browser-safe root export provides `DEFAULT_CAMPAIGN`, `CampaignCancellation`,
`jobSeed`, `validateCampaignConfig`, `runCampaign`, and `runGuidedCampaign`.
The two runners return `Promise<CampaignReport>` and execute configured workers1
on the caller. Their positional signatures are:

```ts
runCampaign(config, limits, factory, sink)
runGuidedCampaign(config, limits, guidance, factory, feedbackFactory, sink)
```

Factories receive `jobId: bigint`. A campaign model is the existing `GenerateModel`
plus `metadata(): Metadata` and optional synchronous `dispose(): void`. Feedback
is the existing public `Feedback` plus optional synchronous `dispose(): void`.
`sink(job)` returns `void | Promise<void>`; sink calls are serial. Workers>1 on
these caller entries reject before any model/sink callback and require a host API.

The Node-only `@jagalite/statelessness/node-campaign` export provides:

```ts
runNodeCampaign(config, limits, workerModule, sink)
runNodeGuidedCampaign(config, limits, guidance, workerModule, sink)
```

`workerModule` is `{module: URL | string, data?: unknown, decodeInput(wire)}`.
`module` must be an absolute URL. Initialization data must be structured-cloneable.
The application module exports synchronous functions:

```ts
createModel(jobId, data, context)         // required
createFeedback(jobId, model, data, context) // required for guided
encodeInput(input)                        // required for workers>1
disposeModel(model)                       // optional; replaces model.dispose
disposeFeedback(feedback)                 // optional; replaces feedback.dispose
```

`context.cancellation` is a `CampaignCancellation` shared flag. The worker creates
each model/feedback and retains all states, outputs and feature keys locally.
Only input values in failure witnesses and guidance corpora use `encodeInput`
and caller `decodeInput`. The protocol envelope uses structured clone. These hooks
must faithfully preserve application input identity; no canonical `ModelCodec`,
function source serialization, or guessed class reconstruction is involved.
Configured workers1 imports the module on the caller and does not transfer inputs.
Configured workers>1/jobs1 still spawns a job worker.

`config` is `{masterSeed, firstJob, jobs, workers, resultBuffer, fuzz, stopOnFailure}`.
Defaults match Rust: seed0/id0/jobs16/workers1/buffer1/ordinary fuzz/false.
IDs/seeds and aggregate work counters are exact u64 `bigint`. Job counts and
N05 case/step counts are nonnegative safe JavaScript integers, an explicit native
representability boundary below Rust's full 64-bit usize domain. Validation
multiplies cases*steps as `bigint`, caps by maxTransitions, then multiplies by jobs.
`jobSeed` wraps master+id to u64 before SplitMix64.next. No number bitwise coercion
is used for IDs, seeds or counters. Guided validation precedes campaign validation.

`limits` is `{maxDurationMs?, cancellation?}`. Cancellation accepts a shared
`CampaignCancellation`; caller-only APIs also accept a synchronous boolean function.
Node workers reject cancellation closures before worker startup. `new
CampaignCancellation()`, `.cancel()`, `.isCancelled()` and `.sharedBuffer` support
explicit cross-thread cancellation. The deadline includes module loading, worker
startup, factories, metadata, feedback and search; callback/sink execution is
cooperative and cannot be preempted.
For Node workers1, the token supplied in `context.cancellation` is combined with
the caller's cancellation function. That function retains the original limits
object as its receiver, and module loading does not reset the original deadline.

`JobReport` fields: `jobId`, `seed`, `config`, `metadata` (object or null), `outcome`.
Outcome discriminants are `Completed` with `report`, `FactoryError`/`ModelError`
with `error: ModelError`, or `Panicked` with `message`. Completed reports are the
actual public `FuzzReport` or `GuidedFuzzReport`, including native snake_case
report fields and lowercase termination values. No per-job guidance is discarded.

`CampaignReport` has number counts `requested`, `started`, `finished`,
`notStarted`, `delivered`, `failures`, `errors`; bigint `reportedTransitions`,
`reportedCases`, `reportedSkippedChecks`; `accountingComplete`; and `termination`
with `kind` one of `JobsCompleted`, `FailureFound`, `Cancelled`, `Deadline`,
`CallbackError`, `WorkerError`. The last two carry `error: ModelError`.
Counter saturation, hidden-work accounting and stop precedence follow the private
lifecycle law, which drives both real executors. No generic report merge exists.

## Host scheduling and callback policy

Node multiworker campaigns use one dedicated coordinator thread and
`min(workers,jobs)` job threads. The coordinator executes the private lifecycle,
bounded queue and acknowledgement protocol. It never calls a model, factory,
feedback or user sink. This separate coordinator permits progress while the caller
is inside a synchronously blocked sink; merely awaiting a Promise on the caller
event loop would not satisfy this requirement.

A job worker applies Complete through an acknowledged coordinator message before
encoding/transferring its report. It cannot claim again until the bounded send is
accepted. At buffer0, acceptance waits for Receive; a buffered send is accepted
only into an available queue slot. Receive releases its sender before caller sink
execution. There is one sink report, at most resultBuffer queued reports and one
blocked send per job worker. Delivery is receipt order with no job-ID sort.
Messages cannot create an unbounded backlog: each worker waits for each protocol
reply. This bounds report count, not payload bytes or RSS.

The existing public N05 fuzz/guided callback wrappers are unchanged. Runner
exceptions they normalize remain ModelError, including ordinary Error from a model
callback. N06 does not manufacture Rust unwind provenance. Direct model-factory
ModelError becomes FactoryError with metadata null and known zero work; direct
feedback-factory ModelError becomes ModelError with metadata and incomplete
accounting. Other direct factory/feedback-factory exceptions and any metadata or
explicit cleanup exception become Panicked. Feedback cleanup precedes model cleanup;
the first cleanup error is preserved. JavaScript GC/finalizer timing is not modeled.
Sink exceptions become CallbackError; a synchronous throw or rejected Promise
prevents further sink calls, while all reports are drained and workers joined.
The first sink error survives later worker errors. A failed encode/send/import or
unexpected job-worker exit is an infrastructure WorkerError, with lost-work
accounting; no missing JobReport is fabricated. Input decode runs at the caller's
delivery callback boundary; decode failure is CallbackError before the user sink.
Fatal process/OOM/abort failures and coordinator corruption cannot promise recovery;
a coordinator failure rejects with an unavailable aggregate rather than inventing
counter values. No preemption, hard RSS cap, durable delivery or queue resume claim.

The root package imports no Node built-ins. Browser caller execution is available
in source; a browser multiworker adapter would need the same dedicated coordinator,
bounded port protocol, explicit module/input transfer, shared cancellation (and its
browser isolation requirements), clock and teardown tests. No browser-worker
implementation or browser qualification is claimed from Node evidence.

## Examples and acceptance evidence

Public examples live under `examples/native-campaign/typescript`. A fresh installed
consumer must import the package name and `node-campaign` subpath, invoke ordinary
and guided jobs, prove configured workers1 versus workers>1/jobs1 roles, and use
an application input adapter with real worker callbacks. The root owns that
cross-language installed harness and the final source/package receipts.

Executable Node entries are `node node.mjs ordinary` and `node node.mjs guided`
from that example directory after installing the built tarball. `serve.mjs` hosts
the optional browser caller example; it is not part of Node qualification.

TypeScript runtime tests are `typescript/test/campaign.test.mjs` with an explicit
worker module fixture. Private decision-core tests and the independent test-only
codec/self-model fixture are in `campaign-lifecycle.test.mjs` and
`test/fixtures/campaign-lifecycle.mjs`. Canonical Rust protocol/bytes come from
`N06-RUST-CONTRACT.md`; exact public Rust comparison remains a distinct gate from
native scheduling tests. Baseline package tests must remain green on the same
source identity. The focused runner is
`validation/native-parity/n06/typescript/qualify.py`. Frozen inputs, source manifests,
the Kyoto submission receipt and eventual execution logs live under that directory.
The submission receipt is the authority for queued versus executed status.

Source review identified and fixed three callback-boundary defects before freezing:
acknowledgements now capture the original protocol ID before transferring a mutable
JobReport to the sink (both hosts), cancellation functions retain their original
limits receiver, and sink diagnostics are copied without invoking a hostile
Error.message getter. Proxy category inspection is also guarded, including revoked
proxies and throwing prototype traps. Runtime acceptance guards remain active.
The authored runtime tests include mutation-then-return/throw, hostile jobId getters,
cancellation receiver/token composition, synchronous and asynchronous hostile error
diagnostics, and first-error precedence through worker drain.

The bounded-send witness uses a private, non-exported shared-memory probe of the
real coordinator. Its versioned snapshot records actual Complete, send, acceptance,
queue and blocked-send states. While the caller sink blocks synchronously, tests
wait for all workers' sends to be admission-limited with a full queue and assert
exactly buffer+workers+1 started/completed reports. A timeout is only a watchdog;
disposal counts and quiet intervals are not the upper-bound evidence. A separate
encoding barrier holds a failed job after its acknowledged Complete while a healthy
sibling delivers and receives a denied later Claim. Startup construction failure
also has a no-lost-work witness. This instrumentation adds no application callback,
transport replacement or extra protocol message.

Range and maximum-ID replay assertions read every failure input's private `.value()`
and every corpus entry's input values separately; deep object equality alone does
not compare private fields. Corpus entry boundaries remain part of the assertion.
The independent lifecycle fixture additionally exercises admission in both
directions, stale acknowledgements, canonical private bytes, saturation and
duplicate-Deliver search/shrink/record/read/replay with healthy controls.
The BFS/fuzz alphabet includes encodable but semantically inconsistent summaries.
Its independent ledger compares the core lost-worker flag directly, and derives
Finish admission from claims, receipts, sink returns and genuine lost-work events.
A fabricated lost-worker/early-Finish witness follows an earlier ModelError so
already-incomplete accounting cannot conceal the fault.

The test-only wire codec uses bigint for all seven Rust u64 job counters, including
valid tiny states with requested/notStarted at 2^53, 2^53+1 and u64 maximum.
`encodeWireState`/`decodeWireState` preserve this domain; explicit native conversion
raises `NativeStateRepresentationError` when counts cannot fit safe numbers.
Valid large wire states are not classified as malformed. This does not widen the
public runtime or N05 FuzzConfig domain and does not establish full Rust usize parity.

The focused runner verifies full frozen and executed-work membership/types/hashes
before and after execution, with only declared generated directories excluded.
It retains package, installed package and consumer source manifests on failure as
well as success, records interpreter/executable hashes and exact commands, uses an
allowlisted child environment, and bounds timeout cleanup to each owned command's
process group. It builds, runs baseline plus campaign tests, packs and freshly
installs the candidate, then executes both Node examples. Rust protocol comparison,
cross-language installed integration, browser hosts and the independent final seal
remain root-owned gates; focused execution cannot close the explicit Swift N05
feedback-metadata provenance gap recorded in N06-INTEGRATION.md.

## Post-r1 qualification-driver correction

R2 Q2 applies to the original focused runner: its normal/nonzero leader exits did
not verify the remaining group, and timeout cleanup could inspect a numerical
PGID after reaping the leader. The frozen r1 source manifest
`d5fa4f4fe098aaca7389518f542e859e578257777579ee088868255c8af21c41` and job
`84048d5f31b4404ba8a6c07e1af98f6a` are preserved unchanged. Their original identities
and narrower cleanup guarantees remain attached to their receipts.

The mutable `validation/native-parity/n06/typescript/qualify.py` now observes all
leader exits through `waitid/WNOWAIT`, retains that child PID during direct group
membership inspection and TERM/KILL escalation, and reaps only after all signalling
is over. It performs no group probe or signal after reaping. Darwin uses libproc;
other hosts fail closed because a procfs enumeration followed by per-PID lookups
cannot reliably establish an empty group. Signal
handlers record interruption requests so Popen ownership cannot be lost through
an asynchronous exception before its handle is assigned. Success, nonzero exit,
timeout and interruption all pass through the same final cleanup. Observed
descendants after leader exit cause qualification failure even if they exit during
the grace interval. The original command error/exit and raw logs remain recorded.

Receipt writes use temporary files and atomic replacement. A final persistence
failure keeps the prior receipt and emits the primary and persistence errors to
a separate best-effort receipt and Kyoto stderr. Recorded interruptions are
reconciled after final persistence and handler restoration, with a failed result
and best-effort receipt correction. Complete loss of all diagnostic sinks cannot
guarantee durable diagnostics.

Before commands, the runner requires its canonical `__file__` to be the exact
sealed `source/validation/native-parity/n06/typescript/qualify.py` member, matching
regular-file type, mode, size and bytes. It preserves raw `__file__` and rejects
relative paths, empty components, `.` and `..` before any lexical normalization;
otherwise a symlink followed by `..` could select a different executed file.
It retains its argv, original Python argv,
cwd and file identity, and rechecks that identity in the final captures. An outer
driver copy is rejected even if its bytes match. The frozen r1 command retains its
original requested path; these newer guards do not retroactively qualify it.

This driver is newer than r1 and **unexecuted**. No replacement snapshot or job is
submitted for it; root must bind any later source seal to the then-current HEAD.
Even a successful old r1 run cannot close Q2 for the revised driver. The required
source review, identities and pending execution cases are recorded in
`validation/native-parity/n06/typescript/POST-R1-Q2.md`.
The final focused source review found no actionable issues at qualifier SHA-256
`70c05fcafac2a2c37d52bcb0d7cfd838af27f1fdbbdcbb19890cfeacabd93882`;
cleanup and provenance execution remain pending.

R2 Q5 remains the installed-harness owner's scope. The in-tree actual-transport
probe is a distinct witness; an installed encodeInput counter proves progress and
transfer, not blocked-send capacity. The harness must bind a suitable transport
witness to its installed package or narrow that acceptance label. TypeScript
runtime/tests and harness-owned files are unchanged by this Q2 correction.
