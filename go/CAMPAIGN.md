# Bounded independent campaigns

`RunCampaign` and `RunGuidedCampaign` run fresh models per job. This N06 candidate
does not expand `Profiles()` and remains subject to source-bound qualification.
No persistence codec or Rust library is needed to run campaigns.

```go
config := s.DefaultCampaignConfig()
config.Jobs, config.Workers, config.ResultBuffer = 16, 4, 0
config.Fuzz = s.FuzzConfig{Cases: 10, MaxSteps: 20,
    MaxTransitions: 200, MutationPercent: 50}
report, err := s.RunCampaign(config, s.RunLimits{},
    func(id uint64) (s.CampaignModel[State, Input, Output], error) {
        model := newJobModel(id) // fresh GenerateModel and private state
        return s.CampaignModel[State, Input, Output]{
            Model: model,
            Metadata: func() (s.Metadata, error) {
                return s.Identity("application", "build-identity"), nil
            },
        }, nil
    }, func(job s.JobReport[Input]) error {
        // Serial callback on the caller; inspect all outcome kinds.
        return saveJob(job)
    })
```

`RunGuidedCampaign` additionally takes a `GuidanceConfig` before the factories,
and a feedback factory between model factory and sink:

```go
func(id uint64, model s.GenerateModel[State, Input, Output]) (
    s.CampaignFeedback[State, Input, Output, State], error) {
    return s.CampaignFeedback[State, Input, Output, State]{
        Feedback: s.ExactStateFeedback(model.Model),
    }, nil
}
```

Each job overrides `Fuzz.Seed` with `JobSeed(MasterSeed, jobID)`. Deterministic
models and logical budgets produce repeatable per-job results across worker
counts. Receipt order, deadline coverage and which jobs start after a property
failure depend on scheduling. Reports are never sorted before delivery.

Workers1 runs on the calling goroutine. Configured Workers>1 spawns
min(Workers,Jobs) goroutines, including Jobs1. ResultBuffer0 is rendezvous.
Complete precedes send; receipt releases a sender before the sink returns.
Outstanding work while a healthy sink blocks is bounded by buffer+workers+1,
also limited by Jobs. This counts objects, not memory, cumulative starts, or
reports retained by the sink.

Factories must support concurrent calls and return fresh model/feedback closures.
State, outputs and feature keys stay on their worker. Report inputs are detached
through the application CloneInput hook before transfer and model cleanup.
ValueCopy is only suitable for immutable scalars; slices/maps/pointers require
faithful copies. Aliased application captures and data races cannot be prevented
by the engine. No arbitrary closure serialization or canonical ModelCodec is used.
For automatic enumeration, use `CampaignAuto(campaignModel, equal, options)`
inside the factory. It installs panic tracking before Auto captures callbacks.
Models already composed with ordinary Auto have lost internally caught panic
provenance; those errors remain ModelError. Other opaque error-catching wrappers
have the same limitation. N05's standalone API behavior is unchanged.

Optional `Close func() error` on CampaignModel and CampaignFeedback provides
explicit cleanup, on the worker before delivery, feedback before model. Close
returned errors are ModelError; Close panics are Panicked. This returned-error
cleanup distinction is Go-specific: Rust Drop has no returned-error channel.
A factory returning an error must clean up its partially constructed resources.
The last returned cleanup error replaces earlier returned errors (feedback then
model); panics take precedence over returned errors, and later cleanup panics
replace earlier panics. A first sink error remains authoritative during drain.

JobOutcome.Kind is completed/factory_error/model_error/panicked. Completed owns
Report; errors own Error; panicked owns a safe Panic string. Only model factory
errors have known zero work. Search and feedback factory errors/panics lose
accounting. Metadata is absent if model construction or metadata failed.
Recoverable callback panics remain distinct from returned errors in campaigns.
Non-string panic payloads are not formatted. Hostile returned-error diagnostics
use a guarded fallback while retaining their original error classification.
Diagnostic extraction restores the worker's prior panic tracker even if an Error
method reenters a guarded model callback; it cannot invent or overwrite a job
panic. Arbitrary diagnostic methods can still have application side effects.

StopOnFailure stops claims on property failure; it does not cancel claimed jobs
or stop for job errors. RunLimits provides atomic cancellation and a snapshotted
duration. Controls are cooperative and cannot preempt callbacks, cleanup or sink
I/O. Successful sink returns alone count Delivered. A sink error/panic stops new
sink calls and claims; active/queued outcomes drain and workers join. The first
sink error wins over later worker/cleanup faults. Infrastructure errors are in
Termination and do not increment job Errors. AccountingComplete does not mean
all requested jobs ran, were delivered, or passed properties.

The private lifecycle and its evidence codec are not public checkpoint or queue
resume APIs. No generic CampaignReport merge, durable exactly-once delivery,
fatal runtime recovery, hard RSS bound or preemption guarantee is provided.
Goexit on the serial caller, process abort and data races are outside recovery.
Native job/case/step counts are signed int (up to MaxInt); IDs, seeds and work
totals are full u64. The test codec interoperates with Rust only for representable
counts. Consistent Rust64 states with jobs above MaxInt are unsupported by this
native representation, not malformed wire; full-u64 count parity remains open.
The repository example is `examples/native-campaign/go`; its separate module
is installed through a private source-bound module proxy for qualification.
