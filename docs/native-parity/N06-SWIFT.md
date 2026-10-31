# N06 Swift campaign implementation

Implementation in the isolated `t3/native-campaign-runtime` checkout based on
`0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`. Runtime/compiler qualification is
pending. This document does not close N06, N05, N10 or a profile. See
[integration gates](N06-INTEGRATION.md) and the independently authored
[reference protocol](N06-RUST-CONTRACT.md).
Root-owned commits may advance the shared branch; each focused manifest records
the observed HEAD and exact working-source identities. HEAD alone is not the
qualification payload identity.

## Package and public API

The source SwiftPM package is the repository's `Package.swift`, target
`swift/Sources/Statelessness`; tests are `swift/Tests/StatelessnessTests`.
The public consumer is [`examples/native-campaign/swift`](../../examples/native-campaign/swift/README.md).
Its package accepts `STATELESS_SWIFT_PACKAGE_PATH` to select an independently
copied source package without depending on the working checkout.

```swift
import Statelessness

let report = try runCampaign(
    config: CampaignConfig(masterSeed: 0, firstJob: 7, jobs: 4,
                           workers: 2, resultBuffer: 0,
                           fuzz: FuzzConfig(cases: 2, maxSteps: 3)),
    factory: { id in
        let model = Model<Int, Int, Int>(
            initialState: { 0 },
            step: { state, input in Transition(state: state + input) },
            checkState: { _ in [] },
            cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 })
        let generated = Generate(model: model,
            generate: { _, rng in Int(rng.index(3)!) }, equalInputs: { $0 == $1 })
        return CampaignJob(generated, metadata: {
            Metadata(name: "counter:\(id)", build: "application-v1")
        })
    },
    sink: { job in print(job.jobID, job.seed) })
print(report.finished, report.delivered, report.accountingComplete)
```

`runGuidedCampaign` has the same config/limits/factory/sink, plus `guidance` and
`feedbackFactory: @Sendable (UInt64, Generate<S,I,O>) throws -> Feedback<S,I,O,K>`.
The feedback factory executes after model metadata on that job's worker. Use the
existing public `Feedback` or `exactStateFeedback` APIs. No guided implementation
is duplicated inside the campaign wrapper. `CampaignJob.observer` permits real
worker-local ordinary/guided callback observation through `SearchObserver`.

`CampaignLimits(maxDuration:isCancelled:)` uses a monotonic clock and a
`@Sendable` cancellation closure. The closure must be thread-safe. A nil duration
does not read the clock. Cancellation wins a simultaneous deadline. Admission
errors precede control checks; guided limits precede campaign validation.
Job counts, cases and steps use the existing native `Int` representation
(`0...Int.max` for nonnegative values), while seeds, IDs and transition counters
retain the full UInt64 range. Counts beyond Int.max cannot be constructed in this
Swift API; they must not be silently narrowed by a cross-language adapter.

## Ownership, scheduling and native policy

Factories are `@Sendable`, and report input `I` must be `Sendable`. State, model,
output, feedback, feature keys and observer need not conform: they are constructed,
used and released inside their executing worker. Applications remain responsible
for lawful input snapshots and for not returning aliased mutable model storage
from concurrent factories. The implementation does not serialize closures or
require a canonical `ModelCodec`. Checked conditional `Sendable` conformances
were added to `Failure`, `CorpusEntry`, `GuidanceReport` and `FuzzReport` in their
defining files; these are additive type contracts, not N05 algorithm changes.

`CampaignJob.metadata` is a native addition because Swift's existing `Model`
exposes metadata only through an optional codec. The callback does not imply or
construct a codec. Its invocation order is factory, metadata, runner; cancellation
inside factory does not skip metadata or guided feedback construction. Model
factory errors are `FactoryError` with absent metadata and known-zero work;
feedback factory errors are `ModelError` with metadata present and incomplete
accounting. Metadata failure leaves metadata absent.

Ordinary thrown search errors retain N05's existing contextual `ModelError`
normalization. Swift fatal traps, Objective-C exceptions, abort, OOM, deinit traps
and process termination are not catchable. `CampaignPanic` is an explicit
recoverable boundary marker, not a trap catcher: factory/runner boundary throws
can produce `Panicked`, while a marker thrown inside an existing N05 callback
wrapper is normalized to `ModelError`. A throwing metadata callback maps to
`Panicked` because Rust metadata has no ordinary error return. Optional explicit
`CampaignJob.tearDown` runs before completion after metadata/search success or
failure; its thrown failure replaces the outcome with `Panicked`. This supplies
a testable cleanup boundary; Swift deinitializers themselves cannot throw.

Concrete N05 dependency: `feedback-metadata-panic` is `Panicked` in the Rust
unwind fixture, but throwing `CampaignPanic` from Swift feedback metadata passes
through `guidedFuzz`'s existing `call("feedback metadata", ...)` wrapper, producing
`ModelError` with model metadata present. The adapter must retain this mismatch.
Preserving a catchable failure marker through arbitrary N05 callbacks would
require an independently reviewed error-provenance change; it is not included
here. This is separate from actual Swift traps, which remain unrecoverable.
Default `native-campaign-v1` example rows also retain native diagnostic labels
such as `transition:` and `state check:`; they are not rewritten to Rust's
`step:` or `check_state:` spellings. Diagnostic mismatches therefore remain
visible alongside the outcome mismatch in strict comparison evidence.

Configured workers=1 executes jobs on the caller. Configured workers>1 spawns
`min(workers,jobs)` POSIX threads, including workers>1/jobs=1. Darwin and Glibc
hosts have this implementation; other Swift hosts reject multiworker admission.
Callbacks are synchronous. The sink is not `@Sendable` and runs serially on the
caller. Every worker completes the lifecycle before blocking send. Buffer zero
is rendezvous, and receipt frees its sender before the sink returns. Reports are
delivered in receipt order without sorting. Outstanding started but not
successfully delivered work while a healthy sink blocks is bounded by
`resultBuffer + actualWorkers + 1`, also by jobs; this does not bound retained
sink data, cumulative starts, or RSS.

The runtime drives `CampaignLifecycle.apply` under a private mutex; its state
contains summaries only. The few `@unchecked Sendable` classes are mutex or
condition protected protocol/channel storage; they never contain a model or
feedback closure. Application callbacks and application report destruction run
outside these locks. Worker-start failure records no lost work; worker failure
observed after drain/join records conservative lost work. Internal injection hooks
exercise host failures without pretending Swift traps can be recovered.

Sink errors stop claims and further sink calls, drain/count queued and active
outcomes, and preserve the first sink error over later worker failures. Property
stops do not cancel already claimed jobs; ordinary job errors do not trigger
stop-on-failure. Aggregates count only exposed work, saturate at UInt64.max and
mark lost/overflowed accounting. No generic campaign merge, hard preemption,
durable delivery, resume/checkpoint, byte/RSS ceiling or N10 assurance is added.

## Qualification plan and current disposition

The independent test-only lifecycle codec/self-model, runtime tests and fresh
consumer are authored and source reviewed, with execution pending. The codec matches the source-bound Rust private
layout within the native `Int` count domain and is not a public persistence API.
On a 64-bit Swift host the seven report job counts and encoded collection/string
counts accept `0...2^63-1`, while Rust64 usize accepts `0...2^64-1`. Seeds, job IDs,
summary work counters and aggregate work totals preserve the full UInt64 range.
The consistent initial-state boundary fixture accepts requested=notStarted=Int.max
and rejects requested=notStarted=2^63 with an explicit native-count diagnostic.
The latter is valid for the corresponding Rust64 jobs=2^63/cases=0 lifecycle;
it is not malformed wire data. Full-Rust64 count interchange remains an open
representation gate. No large count is coerced or allocated. Authored controls include stale
and duplicate acknowledgements, duplicate-Deliver fault discovery/shrink/replay,
healthy strict/relaxed replay, real concurrent workers, out-of-order delivery,
slow sinks, rendezvous, cleanup failures, cancellation/deadline and bounded drain.

The independent event ledger directly compares the core's lost-work flag with
observed lost-work events. Finish admission comes from ledger claims, receipts,
sink returns, stop events and finalization, without trusting the core's lost
flag. A deliberate corruption after an earlier ModelError proves that incomplete
accounting alone cannot mask fabricated loss or premature Finish. The opposite
cleared-loss corruption and incorrect Finish rejection are also covered; the
duplicate-Deliver fault discovery/shrink/record/read/replay pipeline remains.
These are authored witnesses, not executed results.

`CampaignRuntimeTests.swift` contains 32 authored tests, including startup
failure, lost work observed after drain, first sink error versus late worker
failure, positive factory and feedback deadlines, and property-stop with another
job already claimed. The public consumer emits 49 reference projections and 14
host check groups (including 13 native-policy cases with raw callback/error
evidence). Its default output retains the `feedback-metadata-panic` mismatch;
neither the example nor the focused runner declares cross-language equality.

R2-1 adds a private value-only channel probe that shares the actual transport
condition. Its snapshots cannot interleave between a recorded blocked path and
the corresponding `NSCondition.wait`. Buffer-zero and buffer-one tests hold two
sink calls until both senders reach actual wait paths, then check receipt,
send-return and next-claim order, final drain and joins. The earlier completion
and public teardown observations remain separate progress evidence. R2-2's
public consumer compares each job's observer/model/feedback trace with a fresh
bare runner and independently requires three initial and twelve transition
observations, zero check-error observations, and fixed check/observer/feedback
placement. Raw native traces remain separate from the Rust callback projection.
Failures before a bare reference is available retain every campaign callback and
delivered job's actual error, including observer errors surfaced as ModelError;
per-job comparison failures additionally retain the available bare trace.

The focused runner installs fresh source copies from an immutable private
snapshot. Its seals compare complete membership, directory/file/symlink type,
mode and file hash before and after, with only the exact root `.swiftpm`
bookkeeping directory excluded in installed copies. A file or symlink at that
path is rejected; scratch builds, caches and configuration are outside the
source copies. Initial directories and the manifest's own mode are bound too.
Preflight and partial-copy failures retain a failure receipt. Copy and final
expectations come from the authenticated manifest; snapshot copies compare with
saved source identities rather than a later live baseline. Directory enumeration
fails explicitly on unreadable members. Commands receive
an allowlisted environment and recorded tool identities, run in individually
owned process groups, and retain raw stdout/stderr. Timeout cleanup targets only
that command's group, retaining its unreaped leader until signalling finishes
to prevent process-group ID reuse. The same cleanup runs on normal/nonzero exits.
Catchable SIGTERM/SIGHUP/SIGINT are recorded without throwing during the spawn
handoff or cleanup; interruption is checked after acquiring the owned handle,
and repeated signals cannot interrupt reap/receipt work. The executing driver's
canonical path and file identity must equal the sealed member and pass a final
recheck. Symlink and filesystem case aliases are checked before output creation,
and snapshot mapping rejects collisions. Guard tests are authored for execution
inside the focused Kyoto job; none has run yet. This is a Darwin-focused Swift execution receipt, not
host-wide process supervision or a global N06 gate.
After cleanup and final identity checks, the driver blocks catchable termination
signals and samples the pending set before writing its terminal receipt. Receipt
status and process exit use that same frozen decision. Signals arriving after
that explicit terminal sample are outside the interruption window.
No SIGKILL, process-loss recovery or cleanup of descendants escaping their owned
process group is claimed. Older other-language r1 cleanup receipts cannot qualify
this runner. Timeout/interruption command receipts retain `returncode: null`;
the guard fixtures separately check actual owned-child exit and pipe closure.

All compiler/test/consumer executions must use the shareddefault Kyoto queue at
`/Volumes/seed1/Projects/kyoto/kyoto.py`. One focused Swift job was submitted:
`c23862ecdfe64abd84336b825cc5e62c`. The submission and first show receipt both
report queued, with no start or exit code. No watcher or duplicate job was added.
Queue admission is not execution.

The private immutable input copy is
[`validation/native-parity/n06/swift/inputs/r1`](../../validation/native-parity/n06/swift/inputs/r1).
Its [manifest](../../validation/native-parity/n06/swift/receipts/r1-source-identity.json)
has SHA-256 `94381466b7d64f313221d5f44c10189770fcaa3e16ec61427b274a4a6b1aab97`,
83 source files and 96 sealed entries. It records observed HEAD
`ef5c46b5646eec78599c0ed69756df889a8126ce`; content identities remain authority.
The [submission plan](../../validation/native-parity/n06/swift/receipts/r1-submission-plan.json)
records the exact packaging, Kyoto submission and requested executable argv.
The [submission receipt](../../validation/native-parity/n06/swift/receipts/r1-submission.json)
and raw submit/show logs preserve the unchanged requested/effective command:
clean `/usr/bin/env -i`, actual HOME, fixed PATH/locale, `/usr/bin/python3 -B -E -s`,
the sealed `inputs/r1/qualify.py`, `inputs/r1`, fresh `outputs/r1`, and the manifest
hash above. Kyoto classifies this wrapper as opaque with no command rewrite;
its `allow_test_run: 0` field does not convert this non-Cargo wrapper to a compile-only command.

The queued job will run 30 runner guards, the complete native Swift package
tests (including 32 runtime and 18 lifecycle tests), and a fresh source-package
consumer's 49 projections and 14 host groups. All remain unexecuted in this
handoff. Final R2 transport, host evidence, terminal-boundary and guard reviews
reported no remaining concrete source findings in their respective scopes.
The final cross-language freeze and installed comparison are owned by the root.
