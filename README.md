# Stateless

A Rust library for exploring application state transitions, checking invariants,
and recording replayable failures. Zero third-party dependencies, including tests
and builds. The application keeps its own state, transition function, and effects.

```text
State + Input -> Next State + Outputs -> Check rules -> Record evidence
```

Stateless is experimental; APIs may change. Development focuses on the Rust
library and CLI. C, Swift, and browser bindings are experimental ABI 1 byte-model packages.
Passing a finite model establishes only the supplied properties within that
model and its bounds.

## Use as a library

The crates.io package is `statelessness`; the Rust library is named `stateless`:

```toml
[dependencies]
stateless = { package = "statelessness", version = "0.1" }
```

## Try it

For a complete custom-model workflow, start with
[Test your own application](examples/README.md): write a regression test, find
and shrink a bug, save its evidence, and replay it with your own executable.

Rust 1.90 or later is required. No package download is needed.

```sh
cargo test --offline
cargo run --offline --example counter
cargo run --offline -- demo failure.sttrace
cargo run --offline -- inspect failure.sttrace
cargo run --offline -- replay failure.sttrace
cargo run --offline -- replay failure.sttrace --fixed --allow-build-mismatch
```

`demo` exits with status **1** because it deliberately records a bug: a completion
arrives after cancellation and incorrectly publishes readiness. Exact replay
exits **0** and reports that the recorded failure reproduced. The fixed model
diverges at transition 3 and exits **3**. A different build is rejected unless
`--allow-build-mismatch` is explicit; model/property/codec versions must still match.

Use new artifact paths for subsequent runs. Existing files are never overwritten.

```sh
cargo run --offline -- fuzz fuzz.sttrace --seed 42 --cases 100 --steps 20
cargo run --offline -- fuzz auto.sttrace --auto --seed 42
cargo run --release --offline -- campaign campaign-run --auto --jobs 32 --workers 4 --seed 42
cargo run --offline -- enumerate search.sttrace
cargo run --offline -- enumerate fixed.sttrace --fixed
cargo run --offline -- replay failure.sttrace --delay-ms 250
```

Fuzzing and enumeration always save a `TRACE.report.txt` summary for completed
search calls, including successful and budget-limited runs. A failure additionally
saves `TRACE` and an `.original.sttrace` sibling, with shrinking history and a link
to the original. Reports distinguish findings from search termination and include
model/build identities, bounds, counts, and skipped checks. Errors from model
callbacks are returned as errors; they do not produce a completed search report.

The CLI currently runs the bundled request-lifecycle fixture. Application models
use the library or bindings; the CLI does not dynamically load arbitrary models.

`campaign` requires a new directory and writes its configuration, one report per
delivered job, original/minimized failure traces, and a campaign summary. Its
default workload is 16 jobs independent of worker count. Add `--fixed` to exercise
the corrected fixture, `--max-ms` for a cooperative campaign deadline, or
`--stop-on-failure` to stop claiming jobs after a finding. `--cases` and `--steps`
apply per job. `--auto` opts into finite-domain sampling; custom generation remains
the default. Artifact I/O and bounded shrinking may outlast the search deadline.

## Independent oracles

`WithOracle::new(model, oracle)` adds first-class verification to any Rust model.
Implement `Oracle<M>` with independent initial history, advancement from inputs,
observed outputs and disposition, and comparison checks against actual state.
A reference reducer or an event ledger can supply those semantics. Advancement
cannot inspect the application's next state. Verify missing required outputs as
well as emitted outputs; reusing the implementation under test can hide its bugs.

The wrapper owns one `OracleState { model, oracle }`. Equality, hashing, fuzzing,
enumeration, shrinking, and snapshots retain both parts. It delegates input
selection to the application. `OracleCodec<M>` supplies history persistence; the
wrapper frames both codecs directly into the bounded encoding sink. Override the
streaming codec methods to bound allocation before growth. Oracle identity and
all semantic versions are checked even with `allow_build_mismatch` enabled.

For runtime observation, execute the application transition once, then lift it:

```rust,ignore
let verified = WithOracle::new(application, reference);
let before = verified.initial_state()?;
let application_transition = verified.model().step(&before.model, &input)?;
let transition = verified.observe_transition(&before, &input, application_transition)?;
recorder.observe(&verified, &before, &input, &transition)?;
let next = transition.state;
```

`observe_transition` advances history once and never calls the application reducer.
Keep its result for checks and recording. If advancement fails, `ObservationError`
returns the original application transition without copying it, alongside the
`ModelError`. Retain the actual runtime state and report the verification gap;
repeating the application transition would execute it twice. Access the application
explicitly through `model()` and its state through `.model`. Explicit access prevents
accidental dispatch to application methods that bypass the wrapper.
To attach mid-run, use `attach(actual, saved_history)` or decode a saved composite snapshot; history cannot generally be
reconstructed from actual state. Advance on every observed transition, including
rejected/ignored inputs. Periodic checking policies skip comparison checks as
recorded; they do not skip history advancement. Oracle history and comparison
costs remain application-owned, so bound retained ledgers and expensive scans.
Use distinct property IDs for application and oracle checks.

Transition check callbacks take `&TransitionRef<'_, State, Output>` with borrowed
state, output slice, and disposition. `transition.as_ref()` constructs that view
without cloning.

## Model interface

Implement `Model` with your state, input, and output types:

```text
initial_state()                       -> State
step(&State, &Input)                   -> Transition<State, Output>
check_state(&State)                    -> named Check results
check_transition(&before, &input,
                 &transition)         -> named Check results
```

The optional `check_transition` method defaults to no checks. Return `Passed`,
`Failed(details)`, or `Skipped(reason)` for stable property IDs. Return `ModelError`
for a broken adapter/checker; it is not an application property violation.
Properties and their reporting order must be deterministic.

Property names use `PropertyId`: string literals are borrowed, and dynamic names
can be created once from a `String` and cheaply cloned. Names still compare and
serialize by text. Override `check_state_into` and `check_transition_into` to
append results into reusable caller storage; the original vector-returning
callbacks remain supported. `check_observed_into` clears and reuses its output
buffer, clearing partial results if a checker returns an error.

See [examples/counter.rs](examples/counter.rs) for a small complete model and
[src/demo.rs](src/demo.rs) for a model with effects, cancellation, and persistence.

Additional traits enable capabilities without burdening simple execution:

| Trait or function | Purpose |
| --- | --- |
| `Generate` | Stateful random inputs, causal validity, optional simplification hints. |
| `Enumerate` | Complete finite list of permitted next inputs, in stable order. |
| `ModelCodec` | Canonical state/input/output encodings and state/input decoding. |
| `Oracle`, `OracleCodec` | Independent verification semantics and persisted history. |
| `WithOracle` | Compose application and oracle into one checked, replayable state. |
| `execution::check_observed` | Check a transition the application already executed. |
| `execution::check_observed_into` | Check with caller-owned reusable result storage. |
| `execution::record` | Execute supplied inputs, fully check, and build an exact trace. |
| `execution::replay` | Restore the recorded snapshot and compare all observations. |
| `monitor::Recorder` | Keep bounded runtime history with a replayable starting checkpoint. |
| `explore::fuzz` | Seeded sequences and mutation of a previous passing sequence. |
| `automatic::Auto` | Generate random inputs from an existing finite `Enumerate` domain. |
| `guided::fuzz` | Search using a bounded corpus of feature-discovering input prefixes. |
| `guided::Feedback` | Application-defined state, transition, effect, or branch novelty keys. |
| `campaign::run_campaign` | Run fixed fuzz jobs on CPU workers and stream their outcomes. |
| `campaign::run_guided_campaign` | Run independent jobs with worker-local feedback corpora. |
| `explore::shrink` | Causal chunk deletion and application-supplied input simplification. |
| `explore::enumerate` | Bounded breadth-first exploration using exact equality. |

`Enumerate::input_iter` supports lazy input generation. Its compatibility default
collects the original `inputs()` vector, so override it for large input domains.
`ModelCodec::encode_*_into` writes to a reusable `EncodeBuffer` that checks byte
limits before growing. Legacy codecs remain supported, but their temporary vector
allocations occur before the engine can check their size.

No persistence trait is required for checking or searching. Enumeration additionally
requires state `Hash`; collisions are resolved with `Eq`. Initial states and every
executed edge are checked, including edges to visited states and failing successors
encountered at the state storage cap.

## Feedback-guided fuzzing

`guided::fuzz` retains multiple passing input prefixes that discover new features.
For each case it can choose a retained prefix uniformly, extend it with fresh
inputs, delete a span, or replace an input. Reused inputs are checked against the
current state and repaired through `Generate` when causal dependencies changed.
Every executed transition, including reused prefixes, counts against the fuzz
budget and runs the normal application and oracle checks.

Provide `Feedback<M>` to emit stable keys for interesting states, transitions,
effects, or branches. `StateFeedback` projects state into a key; `exact_state`
uses full state equality and hashing. Compact projections avoid cloning large
states and keep monotonic event IDs from making every transition appear new.
Feature keys guide selection; the full state and oracle history remain intact
for checking, shrinking, and replay. Compiled-code branch instrumentation is
application-supplied through feature keys.

```rust
use stateless::{demo::{RequestModel, State}, explore::FuzzConfig};
use stateless::guided::{self, FeedbackMetadata, GuidanceConfig, StateFeedback};

let feedback = StateFeedback::new(
    FeedbackMetadata {
        name: "request-phases".into(), version: 1, build: "phase-projection-v1".into(),
    },
    |state: &State| (state.generation, state.active, state.ready, state.pending.len()),
);
let report = guided::fuzz(
    &RequestModel::buggy(), FuzzConfig::default(), GuidanceConfig::default(), feedback,
)?;
let corpus = &report.guidance.as_ref().unwrap().corpus;
assert!(!corpus.is_empty());
# Ok::<(), stateless::ModelError>(())
```

`GuidanceConfig` bounds retained corpus entries, total retained inputs, tracked
feature keys, and features emitted per observation. Oldest corpus prefixes are
replaced when admitting new discoveries would exceed retention bounds; a prefix
larger than the total input limit is dropped. Feature tracking stops admitting
new keys when its cap is reached. A later fitting prefix can recover features
whose first witness was dropped, even at the feature cap. Its `new_features` count
is zero for previously tracked keys. Evicted witnesses stay marked as discovered;
FIFO replacement does not guarantee retention of a witness for every feature.
Fuzzing continues with the retained corpus,
and reports distinguish eviction, dropped prefixes, and feature/corpus limits.
These are count limits: input/key payloads and allocations inside callbacks remain
application-owned. Oversized feedback batches and callback errors return engine
errors; they cannot become a successful partial-feedback observation. Properties
on a failing transition take precedence over feedback errors.

Reports retain the search algorithm identity, feedback name/version/build, guidance limits, discovery counts,
and concrete corpus sequences. Failures use the existing shrinking and trace
replay APIs. `campaign::run_guided_campaign` creates feedback and a private corpus
per job, preserving fixed-job reproducibility across worker counts. Feature keys
and model state remain on their worker and need not be `Send` or `Sync`.

```sh
cargo run --offline -- fuzz failure.sttrace --guided --auto
cargo run --offline -- campaign NEW_DIRECTORY --guided --auto --workers 4
cargo run --offline -- fuzz search.sttrace --guided --fixed --corpus 256 --features 100000
```

The CLI uses compact request-lifecycle features. `--corpus`, `--corpus-inputs`,
`--features`, and `--features-per-step` require `--guided` and apply to fuzz and
campaign commands. Audit reports retain the corpus and learning statistics;
failure traces record guidance identity, limits, seed, and search outcomes.
The existing `explore::fuzz` keeps its previous-sequence strategy and seed behavior.
`FuzzReport::guidance` is a new optional boxed report, populated only by guided runs.

## Automatic and parallel fuzzing

Wrap a model implementing `Enumerate` in `automatic::Auto` to fuzz without writing
a separate `Generate` implementation. The wrapper uniformly samples entries from
the complete input iterator using reservoir sampling. Duplicate entries provide
extra weight. It retains one selected input, but scans the domain on each call;
override `input_iter` to avoid the legacy eager vector. For very large domains,
an application-specific `Generate` implementation can sample more efficiently.

`AutoOptions::max_candidates` defaults to 100,000. An oversized domain returns an
error instead of sampling only its prefix. Generation probes at most one entry
beyond the cap and leaves the RNG unchanged on error or an empty domain. Causal
validation uses bounded membership lookup. A scan is one cooperative callback;
the engine cannot preempt a blocking iterator. Model behavior, checks, and codecs
are delegated unchanged, so the wrapper also supports shrinking and exact replay.

```rust
use stateless::automatic::Auto;
use stateless::campaign::{CampaignConfig, run_campaign};
use stateless::demo::RequestModel;
use stateless::explore::RunLimits;

let report = run_campaign(
    CampaignConfig {
        master_seed: 42,
        jobs: 32,
        workers: 4,
        stop_on_failure: false,
        ..CampaignConfig::default()
    },
    RunLimits::default(),
    |_job_id| Ok(Auto::new(RequestModel::buggy())),
    |job| {
        // Persist each job here; failures contain the concrete input sequence.
        println!("job {}: {:?}", job.job_id, job.outcome);
        Ok(())
    },
)?;
```

The factory constructs a fresh model for every job inside its worker. Models,
states and outputs need not implement `Send` or `Sync`; returned inputs must be
`Send`, and the factory is shared through `Sync`. Each job has its own RNG and
mutation history. Job IDs determine seeds independently of worker count, and
reports include the actual fuzz configuration and model identity. With unchanged
model behavior and no interruption, fixed jobs reproduce across worker counts.
The factory must produce the same model for a given job ID on every call.
Completion order and early-stop subsets are scheduling-dependent.

Results pass through a bounded channel to the caller's sink. The engine does not
collect all job histories; the sink controls persistence or retention. Worker
count, result buffering, and finite per-job work limits bound concurrency and
engine work. They do not bound allocations hidden in callbacks or the size of an
individual input. A slow sink applies backpressure. Cancellation and the campaign
deadline are cooperative, and stopping on a failure stops new jobs while allowing
already running jobs to finish under their controls. Shrinking or I/O performed
by a sink is outside the fuzz transition budget.

Inspect job and campaign outcomes: factory errors, model errors, panics, sink
errors, and unfinished jobs are distinct from property failures. Aggregate
reported work excludes partial work lost when a model returns an error; the
campaign exposes whether accounting is complete. One worker runs inline without
spawning threads. Unknown-OS Wasm supports that path; requesting multiple workers
returns an explicit error.

Worker counts are 1–256; result buffering is 0–1,024 reports, with zero requesting
a rendezvous channel. A sink error stops further sink calls: queued and active
jobs are drained and counted, but their reports are discarded. Compare `delivered`
with `finished` before treating persisted evidence as a complete job audit.

Parallelism here explores independent simulations of the same model. Exact
enumeration remains single-threaded. Coverage-guided corpus sharing, GPU kernels,
and distributed scheduling remain separate work.
See [validation](VALIDATION.md) for the test suite, benchmark commands, and
qualification limits.

## Stateless checking itself

The real campaign coordinator's [decision core](src/campaign/lifecycle.rs)
implements the same `Model` interface as application models. It owns one combined state for
job claims, pending completions, report delivery, stop reasons, and accounting.
Both ordinary and guided campaigns, with one or multiple workers, execute that
core. Threads, clocks, the bounded channel, and sink callbacks remain external
effects; their observations enter as explicit inputs. The executor moves its live
state through the shared reducer; `Model::step` copies the predecessor for
exploration. Both paths execute the same transition function.

Completion, channel receipt, and sink return are separate events. Job IDs prevent
duplicate completion and delivery; a failed sink stops further calls while the
executor drains existing reports. The state retains metadata for outstanding
jobs, without retaining completed application histories. Checks require valid
events to be accepted, stop observations to prevent further claims immediately,
and sink acknowledgements to match the active job. Debug builds retain a predecessor
snapshot and check the core's properties on actual runtime transitions.

```sh
cargo test --offline --lib campaign::lifecycle -- --nocapture
```

Self-checks explore bounded event orderings through this actual transition
function, compare against an independent event ledger, inject a fault to check
detection, and exercise shrinking and exact replay. The lifecycle codec is
test-only; campaigns do not automatically record their own runtime traces.
These checks cover campaign decisions. They do not model OS thread/channel
internals, prove liveness, or put the CLI, search algorithms, and trace parser
into a single self-model. See the [lifecycle tests](src/campaign/lifecycle/tests.rs)
for the bounded models and fault controls.

## Model responsibilities

All behavior-relevant data must be in state or input: logical time, pending effects,
callback identities, random outcomes, and history needed by properties. A model
may contain several subsystems; Stateless sees one state and one transition.

Transitions must not execute real external effects or depend on hidden mutable
state. Effects are output data; simulated outcomes enter as later inputs. The
application defines which input schedules are valid. Rust traits cannot prove
purity or completeness of a supplied enumerator.

Model equality must preserve future enabled behavior and all checked properties.
The initial engine uses exact equality; normalized equivalence and partial-order
reduction are not implemented. Enumeration is single-threaded and limited by
depth, retained state count, and executed transition count. `enumerate_with_limits`
adds an optional state-byte budget using `Model::estimated_state_bytes`, which
must be supplied when that budget is enabled. It counts admitted state estimates,
excluding the frontier, inputs, temporary successors, and allocator overhead.
It is not a hard process-memory cap.

`enumerate_with_limits`, `fuzz_with_limits`, and `shrink_with_limits` accept
cooperative deadlines/cancellation. Shrinking can additionally bound total
replayed transitions, including original-failure validation. Inspect
`validated_original` when a resource limit interrupts that validation. Existing
convenience functions leave these additional limits unset. No cooperative limit
can preempt an application callback that blocks or allocates arbitrarily.
On `wasm32-unknown-unknown`, wall-clock deadlines return `ModelError`; cancellation
and count/byte budgets remain available, and runs without deadlines do not read
the clock.

Shrinking targets the first violation's property ID **and phase**, preserves the
original input sequence, checks causality, and records attempts. It is a bounded
heuristic, not a guarantee of global minimality.

## Trace and replay contract

Trace format version 1 uses little-endian, length-framed records with sequence
numbers, CRC32 checksums, and a required footer. Readers reject truncated, corrupt,
unsupported, trailing, or over-limit data. Checksums detect corruption; they do
not authenticate artifacts. `ReadLimits` bound file, frame, blob, string, and
collection sizes before allocating from supplied lengths.

The recorder stores the initial state and checks, then each concrete input,
disposition, ordered outputs, exact post-state, and check observations. This first
implementation retains full traces in memory. `record` enforces default format
limits during capture; `record_with_limits` accepts explicit limits. After a
valid initial checkpoint, callback, codec, and budget errors preserve the last
coherent prefix with error termination. Errors before a usable checkpoint return
`Err`. `write_to` validates default size limits before writing. CLI files
use exclusive creation and `sync_all`; a failed write can leave a partial file,
which the reader rejects. This is not an atomic multi-file transaction.

Exact replay compares state, outputs, disposition, and checks; `failure_reproduced`
is reported separately. The first divergence stops replay. `Completed` means the
supplied sequence ended, not that the application terminated or its state graph
was exhausted. Exact replay of a step-limited prefix verifies only that prefix.

Supply real build and property identities and deterministic canonical payload
encodings. The bundled Rust fixture uses a source/toolchain fingerprint; the
foreign examples have explicit fixture version labels. Neither is a signature.
Restored payloads must be validated by the adapter. Do not put credentials or real
external handles in model data. Redaction that loses behavior prevents exact replay.

## Runtime and language adapters

`check_observed` accepts an already executed transition and never repeats the
reducer. Its default policy checks every state and transition. Periodic state
checks or disabled transition checks emit explicit skipped observations. Check
the initial state separately. The application decides how to record or react to
violations; Stateless does not abort live operations, execute effects, or change
event-loop timing. CLI pacing affects replay wall time, not model time.

`monitor::Recorder` checks and records already executed transitions with full
checking. When it evicts old entries, it advances the initial checkpoint and
records how many transitions were evicted. It freezes at the first property
failure or recording/checker error. This keeps a coherent suffix rather than
claiming to preserve the entire session. `Recorder::new` applies default format
limits and a 64 MiB serialized-evidence budget; `Recorder::with_options` accepts
custom byte/step/format limits. Eviction preserves a checkpoint, and a candidate
that cannot fit freezes the previous coherent window. `retained_bytes()` reports
the current artifact's exact encoded length; spare buffer capacity and application
allocations are outside that count. Capture also reserves room for an error footer.
Once frozen on a property failure, only its actual terminal footer must fit.

`Recorder::write_to` exports borrowed evidence directly, and `into_trace` moves
payload ownership. Use `snapshot` only when a separate owned copy is needed.
Recording still encodes exact before/after states; buffer reuse reduces allocation
without weakening continuity or replay comparisons. Use `check_observed_into`
when persistence is unnecessary.

The [C, Swift, and browser packages](bindings/README.md) keep model logic in its
original language and use the Rust engine. This initial boundary copies canonical
byte representations. Native application state handles and foreign fuzz/shrink interfaces remain
future work; no low-overhead foreign-runtime claim is made.

## Validation and performance

```sh
cargo test --offline
cargo clippy --offline --all-targets -- -D warnings
cargo fmt --check
cargo bench --offline --bench engine
cargo bench --offline --bench scaling
cargo bench --offline --bench campaign
```

Tests cover graph accounting, hash collisions, initial/edge properties, causal
shrinking, budget distinctions, fresh-process replay, changed behavior, trace
corruption/truncation, and binding error handling. The benchmark compares direct
transitions, full checking, trace construction/encoding, and exact replay for a
small fixture. It reports sample medians and ranges, not production performance.

See [VALIDATION.md](VALIDATION.md) for available checks and their limits.
Benchmark results depend on the model, enabled checks, build configuration,
and host. Measure your workload before making performance claims.

Remaining Rust work includes targeted checking with auditable replay policies,
configurable checkpoints/lossless state sharing, and representative long-session
measurements. Full-state scans, full-state capture, and the number of distinct
reachable states still determine scalability. See [PLAN.md](PLAN.md#remaining-rust-work)
for completion criteria.

General liveness, parallel enumeration, state normalization, and broader production
runtime qualification remain later work. See [support status](SUPPORT.md) for
installation paths, current evidence, and remaining release gates.

## Releases

See [RELEASING.md](RELEASING.md) for first publication and trusted publishing
from GitHub Actions.

## License

Licensed under the [MIT License](LICENSE).
