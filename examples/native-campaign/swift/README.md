# Swift public campaign consumer

This standalone Swift 6 package imports the public `Statelessness` product from
the repository root (`../../..`). Set `STATELESS_SWIFT_PACKAGE_PATH` to a staged
native root package to resolve an immutable external dependency instead. The
package does not import the Rust-backed bindings or private campaign lifecycle.

`CampaignConsumer --case ID` emits one `native-campaign-v1` JSON object followed
by a newline. `--list` lists the supported IDs. With no arguments it produces
all 49 current public reference case projections: 15 seeds, 15 validation cases,
and 19 ordinary/guided campaign cases. Known N05 diagnostic and outcome/metadata
mismatches are retained below. Unknown IDs fail with a diagnostic on stderr
and exit status 1; there is no placeholder result or expected-output fallback.

The canonical case definitions and row shape come from
[`native-campaign-public.rs`](../../../conformance/fixtures/native-campaign-public.rs)
and the [N06 Rust contract](../../../docs/native-parity/N06-RUST-CONTRACT.md).
The consumer does not read the independently authored golden rows. Seed values
come from `jobSeed`, admission results from `CampaignConfig.validate` and the
public campaign entry, job reports from `runCampaign`/`runGuidedCampaign`, and
callback traces from the fixture's actual callbacks. The four aggregate
validation cases require a 64-bit host and use the shared safe-integer ceiling
`9_007_199_254_740_991` (`2^53-1`) as actual count inputs. The case and transition
overflow cases use 2,049 jobs; the wide-product capped case uses two jobs, and
the valid job-count edge uses the ceiling itself. Their huge budgets are
validated only, never executed. Wider native `Int.max` admission remains a
separate runtime test.

The deterministic campaigns include ordinary and guided success, factory and
feedback-factory errors, cancellation in the factory, pre-cancellation, zero
deadline, zero cases/steps/transitions, initial/reducer/check errors, metadata and
teardown failures, sink error/panic markers, and property-stop behavior. A
throwing sink's received report remains in the `jobs` array while the aggregate
retains its unsuccessful delivery count. The sink array and callback log are
never sorted.

`CampaignConsumer --host-checks` runs 14 named application check groups and
emits `native-campaign-example-host-v1`. Ordinary and guided cases each exercise
workers=1/jobs=1, workers=2/jobs=1, and workers=3/jobs=7. Actual thread identity
determines calling/spawned roles; it is not inferred from configuration. Every
job's seed, fuzz report, generated inputs, applied inputs, and enabled checks are
compared with a fresh public bare `fuzz`/`guidedFuzz` call. Each campaign job and
its bare reference must independently record exactly three initial observers,
twelve transition observers and zero check-error observers: the healthy fixture
always enables an input and completes three cases of four transitions. Each
initial observation must precede its four transition observations; the observed
transition inputs must match the twelve applied inputs in order. Independent
callback-placement assertions require each observer after its check batches and,
for guided runs, before feedback; guided metadata/initial/transition feedback
counts are thereby fixed at 1/3/12, with none in ordinary runs. Both reports must
independently show three cases, twelve transitions, fifteen skipped checks and
`cases_completed`. The combined model/generation/enabled/check/feedback/observer
event sequence, including input values, must also match the fresh bare run.
These count assertions do not derive
from either report or from equality between two runs sharing the same kernel.
`native_observer_evidence` keeps each job's campaign and bare unfiltered event
records, observed counts, and independent expected counts separately from the
Rust canonical `callbacks` field. Raw records retain callback positions, inputs,
and actual caller/worker roles; roles are checked separately because the bare
reference runs on the caller. Reports and per-job evidence remain in sink receipt
order; callback events are never sorted.

If the fresh bare call or a per-job comparison fails, stderr retains these raw
traces in `native-campaign-example-observer-failure-v1` before the error is
rethrown. A failed comparison never emits a successful host row.
Failures during campaign execution, aggregate checks or preliminary job ID/seed
checks also emit `native-campaign-example-host-failure-v1`, retaining every
available campaign callback and each delivered job's actual outcome and error.
This includes observer errors normalized by the existing runner to `ModelError`.

Buffer-zero and buffer-one handshakes force job 1 to arrive while job 0's factory
is blocked, then require job 2 to be claimed before the first sink returns. The
slow-sink cases wait for `buffer + workers + 1` application teardowns and inspect
the exact claimed/completed snapshot before returning from that sink. Factory
events record any observed over-admission during the hold. Teardown occurs before
the transport send: this public application progress evidence does not establish
that senders reached a full/rendezvous channel wait. An enlarged or unbounded
channel could pass this schedule. Output therefore identifies the proof class as
`public-application-progress` and sets `blocked_send_observed` to false. Actual
blocked-send/send-return causality belongs to separately owned private transport
probes; no public internals are added here. No sleep or timed absence of progress
supplies success evidence; the only timeouts are failed-handshake exits. This is
neither a channel-capacity proof nor a memory/RSS bound.

Three-worker sink-error cases synchronize already-claimed jobs before allowing
any report to reach the sink. They require one failed sink attempt, no successful
delivery, teardown of every started model, full drain accounting, and retention
of the first callback error. A two-worker cancellation barrier checks delivery
of already-claimed jobs with zero fuzz work. The exact group labels are:

```text
ordinary-calling
ordinary-workers2-jobs1
guided-calling
guided-workers2-jobs1
ordinary-isolation
guided-isolation
out-of-order-buffer0
slow-sink-buffer0
sink-error-drain-buffer0
out-of-order-buffer1
slow-sink-buffer1
sink-error-drain-buffer1
cancellation-drain
native-policy
```

The `native-policy` group runs 13 additional native-policy cases. The same
output is available separately as `CampaignConsumer --native-policy-checks`,
schema `native-campaign-swift-policy-v1`. Each case exposes `raw_outcome`,
`raw_native_error`, actual metadata, the canonical projection, an explicit
`error_spelling_mapped` flag, and the unfiltered native callback log. Assertions
require the projected outcome and diagnostic to equal the raw native values,
so `error_spelling_mapped` must remain false. Both default `native-campaign-v1`
rows and policy output preserve the N05 diagnostics `transition: step error`
and `state check: check error`. Rust's corresponding `step: step error` and
`check_state: check error` diagnostics differ; strict comparison exposes these
mismatches without rewriting any native diagnostic.

The canonical `feedback-metadata-panic` case deliberately retains a known N05
dependency: Rust reports `Panicked("feedback metadata panic")`; Swift's existing
`call("feedback metadata", ...)` wrapper catches the thrown `CampaignPanic` and
returns `ModelError("feedback metadata: feedback metadata panic")`, with model
metadata present. Default JSONL and native-policy output preserve that actual
Swift outcome and raw error. Strict comparison with all Rust rows must expose
this mismatch; the consumer does not reclassify it or claim a 49-row parity pass.
No N05 source was changed. See the dependency explanation in
[N06-SWIFT.md](../../../docs/native-parity/N06-SWIFT.md).

Adapter assumptions:

- Inputs are `UInt64`, which is `Sendable`. Model state, outputs, and feature
  keys are deliberately non-Sendable reference types, freshly created on the
  worker. Clone callbacks faithfully copy their immutable values. The only
  shared application objects are lock-protected callback/cancellation and
  barrier ledgers. They contain value-only observation records, never models.
- Factories are `@Sendable` and return `CampaignJob` with `Generate`, metadata,
  observer, and explicit teardown callbacks. No codec is required.
- Observers validate native snapshot/check ordering. Host checks additionally
  assert their presence, counts, input order and placement among the fresh bare
  run's callbacks, retaining the raw evidence under `native_observer_evidence`.
  They are omitted from the canonical callback list because the Rust campaign
  entry has no observer argument. Generated/applied inputs remain in the
  canonical callback strings; no Rust observer entries are invented.
- Rust's feedback `Drop` log is represented by a nonthrowing worker-local
  lifetime token captured by the actual feedback closures. Its `deinit` records
  `feedback_drop`. The model `Drop` log/error uses `CampaignJob.tearDown`.
  Native-policy cases require the observed ARC release to precede model teardown
  and delivery on both success and feedback metadata failure. The token is not
  retained elsewhere. This describes the fixture's closure ownership policy, not
  a general Swift guarantee about arbitrary application deinitializer timing.
- `CampaignPanic` is the explicit catchable boundary marker. Metadata failure
  must yield `Panicked` with null metadata; teardown failure must yield
  `Panicked` retaining metadata. Sink markers must retain the callback-panic
  prefix. This consumer never calls `fatalError`, `precondition`, or `try!`.
  The native-policy output separately checks ordinary thrown metadata/teardown
  errors and explicit markers. Direct factory/feedback-factory markers reach the
  campaign boundary; markers thrown inside N05 reducer, observer, or feedback
  metadata callbacks become contextual `ModelError`. Swift deinitializers do
  not throw, and this policy does not catch fatal traps or process death.
- Existing N05 wrappers normalize throwing model/feedback/observer callbacks to
  `ModelError`. The adapter preserves that outcome and the entire diagnostic,
  including native context labels such as `transition:`, `state check:`, and
  `transition check:`, in default JSONL and native-policy output. The fixture's
  state-check error occurs after a transition, not at initial admission.
- Every integer, including inputs and mutation percentage, is a decimal string.
  Fuzz reports include their seed. Failure violations use the reference's flat
  `phase`, `id`, and Rust Debug-style `status` fields. Shared failure messages
  are ASCII, for which Swift reflection supplies matching quoted payload text.
  Absent reports, metadata, errors, failure, and guidance are explicit nulls.

Source authored and reviewed only. No package resolution, compilation, tests,
consumer execution, or Kyoto submission was performed by this implementation
task. The ancestor owns the focused immutable Kyoto validation job. The separate
installed harness under `scripts/native-campaign-consumers/swift` has its own
entry point and host checks; this example does not silently replace that harness.
