# N05/N06 guided exploration and campaign lifecycle source audit

Source-only implementation input. No builds, tests, benchmarks, fixture execution,
package edits or qualification were performed. This document does not close N05
or N06, alter production equality, promote profiles or change existing receipts.
The authority is the actual Rust implementation, not an inferred smaller fuzz
wrapper or historical guide claim.

Resumed and source-reviewed on 2026-10-22 in the shared checkout
`/Volumes/seed2/Projects/stateless-native-parity`, branch
`docs/native-language-parity`, initially at HEAD
`b5c62f5258cc91e2f878cadb6020d5aea6ce1009`. At final verification the shared branch
was at `32325900335ccf770a33a5b00dff4ab536dfd82f`; the eight pinned source/plan
hashes were unchanged. This review made no commits.
The [review record](GUIDED-CAMPAIGN-REVIEW.md) pins source hashes, resolves the
interrupted review's findings, and separates remaining API decisions from source
facts. No applicable `AGENTS.md` was found in the checkout or its ancestor paths.

## Sources and native foundation

The public reference is [guided.rs](../../src/guided.rs),
[campaign.rs](../../src/campaign.rs), [Rng/Generate](../../src/model.rs) and the
shared [fuzz driver/kernel](../../src/explore.rs). Both guided and campaign modules
are exported by [lib.rs](../../src/lib.rs). The executor's actual private decision
core is [campaign/lifecycle.rs](../../src/campaign/lifecycle.rs). Its
[test-only codec](../../src/campaign/lifecycle/codec.rs) is not a public lifecycle
serialization API. Source regression inputs are [guided tests](../../tests/guided.rs),
[campaign tests](../../tests/campaign.rs) and the
[lifecycle tests/independent ledger](../../src/campaign/lifecycle/tests.rs).
[Assurance integration](../../src/assurance.rs) supplies a separate coverage layer;
[ASSURANCE.md](../ASSURANCE.md) and CLI configuration in [main.rs](../../src/main.rs)
are useful guides, not substitutes for these implementations.

All four native packages have source surfaces for deterministic RNG,
Generate/Auto adapters, ordinary fuzz/observed fuzz, full checks, cooperative
controls, shrink and record/replay. Sources include Python search.py, Go generate.go/fuzz.go,
TypeScript search.ts and Swift Generate.swift/Fuzz.swift. Check sinks, detached
observer batches and native clone/equality conventions are reusable foundations.
A scoped search found no corresponding native Feedback/FeatureBuffer,
GuidanceConfig/GuidanceReport or campaign executor/lifecycle surface. Ordinary
fuzz's previous-sequence mutation is not feature-corpus-v2, and a loop invoking
fuzz is not the bounded campaign delivery/accounting contract. No codec is
required by Rust guided search or campaigns. This inventory does not qualify the
existing native foundations or their cross-engine behavior.

## N05 public contract and ordering

`Feedback<M>` has Eq+Hash feature keys, metadata, default-empty initial feedback
and mandatory transition feedback. `StateFeedback` projects initial and successor
states; `exact_state` uses full cloned state, including represented oracle history,
with identity exact-state/version1/clone-eq-hash-v1. Algorithm identity is exactly
`feature-corpus-v2`. Feature identity is not replay/state equality or exhaustive
coverage, and hash collisions must resolve through exact key equality.

Guidance limits default to entries256, retained inputs25600, features100000 and
per-observation emissions256; every limit must be positive. These are independent
count limits, not byte/RSS caps. Application feature/key/callback allocations are
outside them. Features must have stable lawful equality/hash and emission order;
native reference keys need faithful ownership and no retained writable sink.

The bare guided entry point validates guidance, then mutation percent, creates
control timing, and calls feedback metadata once before entering the shared fuzz
kernel with the remaining duration. Metadata time counts toward that deadline.
After successful admission, pre-cancelled search still performs that metadata call
but no initial/generation/feature-feedback execution. Feedback identity is copied;
Rust does not validate nonblank name/build or positive metadata version here.
Do not invent that restriction as
Rust-equivalent admission. Bare guided search does not call model metadata.
Wrapper-specific assurance calls have different order.
Source: `src/guided.rs:191-232`, `src/explore.rs:731-789`.

Each case follows the real fuzz kernel: control check; initial_state; case count;
initial checks/observer; property failure return; control check; initial feedback;
control check; candidate mutation; then per-step transition cap/control boundaries,
causal reuse or generate/is_enabled, reducer, transition count/input append,
state/transition checks and observer, property failure return, control check,
transition feedback, control check, successor-state commit. Property failure
therefore precedes feedback and its errors; a failing turn does not teach its
feature. Completed checks and cancellation boundaries must retain existing fuzz
observer/error precedence. Feedback errors return ModelError (initial feedback or
transition feedback context), not a fabricated successful partial GuidanceReport.
A blocking callback cannot be preempted. Zero cases/steps/transitions retain the
shared fuzz meanings rather than new guidance-specific shortcuts.

In detail, state checks precede transition checks even if the state batch already
contains a property failure; only after both callbacks succeed does the transition
observer run, followed by failure extraction. A transition-checker Err therefore
supersedes a previously emitted failed state check. Initial/state-checker Err sends
`check_error(phase, partial_checks, false)`; transition-checker Err sends the complete
state batch with `true`, then the partial transition batch with `false`. No normal
initial/transition observation or feedback follows that error. Public Rust observer
hooks return `()`, not Result; observer panic unwinds from bare guided search and
is caught only if an enclosing campaign job boundary handles it. Successful hooks
see failed checks before failure return. These rules also apply to rejected
transitions: they consume a transition, are checked/observed, and can teach features
if checks pass. Source: `src/explore.rs:17-39,763-886,1410-1462`.

There is no control poll between a successful reducer and its checks, nor between
the two checker phases. Their errors win before the next poll; completed property
failures win over cancellation/deadline set inside those callbacks. Successful
feedback learns before the post-feedback poll, so cancellation from feedback can
return a report containing that discovery. Its Err or sticky overrun wins instead
and returns no report. Generation and enabled-check Err similarly return before
their following control poll. Successful generation is polled before inspecting
None; successful generated-input validation is polled before rejecting a disabled
input. Enabled reused input is not validated twice. Source:
`src/explore.rs:804-886`, `src/guided.rs:323-362`.

Budget edge cases are observable. After successful admission the kernel polls
controls even for cases0; otherwise cases0 returns CasesCompleted. With steps0 it
still performs each case's initial checks/feedback and candidate selection, but
never tests the transition cap. With steps>0, transitions0 is tested only after
that initial work. At each step the transition cap precedes the control poll.
Reaching the cap on the final configured step of the final case can return
CasesCompleted; if a new case remains, its initial work and candidate draws happen
before its first step tests the already-reached cap. Do not add an eager cap check
or suppress those RNG calls. Source: `src/explore.rs:754-890`.

`FeatureBuffer.push` bounds emissions, including duplicates, and records overrun
so swallowing its returned error cannot turn callback success into success. The
callback's own returned Err is propagated before the later sticky-overrun check:
overrun-then-return-different-error and ignored-overrun-then-Ok need separate
witnesses. Each observation clears reusable scratch. Only a successful callback
teaches features; no partial callback discovery is admitted after Err.

## Novelty, FIFO corpus and exact RNG consumption

The driver stores `feature -> has_initial_or_retainable_witness`. For every emitted
key in order, a new key is tracked until max_features; further novel keys set
feature_limit_reached but search continues. Duplicates do not inflate discoveries.
An existing key first seen on an overlong prefix can later recover a retainable
witness, even at the feature cap, admitting a corpus entry with new_features0.
Initial empty prefixes mark tracked features witnessed but are never corpus
entries. Eviction intentionally leaves the witnessed bit true: rediscovering an
evicted feature alone does not automatically reinsert its old prefix.

A prefix is retainable when length <= max_corpus_inputs. An overlong nonempty
prefix increments dropped_prefixes once only when that observation tracked at
least one new key; it marks corpus saturation and is not retained. Newly tracked
features remain known but unwitnessed. Repeated overlong observations of only
known keys do not increment drops; recovery itself requires a retainable prefix.
For a retainable admission, evict oldest entries until both entry count and total
input count fit, incrementing evicted_prefixes and subtracting each old length.
Then append a full cloned prefix with newly tracked count, 1-based discovered_case
and global discovered_transition. Admission is FIFO, not shortest-prefix, score,
random eviction, hash iteration or feature-key order. Corpus saturation is not a
search stop. The report contains retained ordered entries and their exact total
input count, mutation cases, dropped/evicted counts and both saturation flags.
The flags record attempted excess: exactly filling the feature or corpus limit
does not alone set them. Feature saturation requires an untracked key at capacity;
corpus saturation requires a drop or eviction. Source: `src/guided.rs:243-291`.

Guided candidate selection ignores the ordinary driver's previous sequence.
Preserve the source RNG call graph with the same SplitMix64/rejection index:

- Empty corpus returns empty candidate/no replacement without a percent draw.
- Otherwise draw index100 even at mutation_percent0; if outside the percentage,
  return empty/no replacement. Selection increments mutation_cases once.
- Draw corpus index, clone that prefix, draw mode index3. Mode0 preserves it and
  generates a suffix after exhaustion, with no replacement index draw.
- Mode1 draws deletion start, positive deletion length, deletes that contiguous
  range, then draws replacement from new_length+1. A replacement at the end is a
  generation position if budgets reach it. Mode2 draws replacement from current
  nonempty length. Replacement regenerates one position, not the entire suffix:
  later candidate positions may still be reused when causally enabled. Candidate
  exhaustion uses fresh generation; None ends the current case, not the campaign.
- Reused inputs still undergo causal is_enabled; disabled reuse falls back to
  Generate. Generated inputs undergo enabled validation. This cannot become a
  mutation that skips checks or treats corpus history as current model state.

`index(0)` returns None without advancing RNG; `index(1)` still consumes a draw.
For positive upper, the rejection threshold is wrapping-u64 `(-upper) % upper`;
consume SplitMix64 values until value >= threshold, then return value % upper.
One index call can therefore consume multiple draws. Candidate draws and model
generation share a single stream across all cases; initial features can change
which tracked keys have witnesses before candidate selection (they add no corpus
entry). Source: `src/guided.rs:295-320`, `src/model.rs:477-510`,
`src/explore.rs:743-889`.
The report and all retained witness sequences must match fixed-seed call traces,
not just eventual failure discovery. No public guided checkpoint/seeded-corpus
resume API exists here: each call begins with an empty feature set and corpus.
Persisting a report does not authorize reconstructing hidden seen/witnessed bits
or resuming the RNG stream. A future resume format requires its own contract.

## N06 configuration, job identity and ownership

CampaignConfig defaults: master_seed0, first_job0, jobs16, workers1,
result_buffer1, ordinary FuzzConfig and stop_on_failure=false. Validation precedes
factory, worker and sink callbacks: jobs positive; workers1..256; result_buffer
0..1024; mutation percent <=100; job-ID last bound and aggregate case/transition
upper bounds fit u64. Transition upper bound is jobs times
min(cases*max_steps,max_transitions), using wide multiplication before min. Do not
reject a safe configured transition cap merely because cases*steps alone is huge.
Per-job zero budgets retain fuzz semantics.

For `run_guided_campaign`, guidance validation comes first, before campaign
configuration validation. The shared executor then validates CampaignConfig,
rejects unsupported Rust multiworker targets, creates the campaign clock/control,
creates the coordinator, and samples preflight cancellation/deadline. Invalid
admission wins over pre-cancellation. Successful preflight stop claims no jobs and
calls neither model/feedback factories, metadata nor sink. Source:
`src/campaign.rs:433-454,471-482`.

`job_seed(master,id)` is SplitMix64.next_u64 from wrapping master+id, not repeated
advance from a shared worker RNG. Each job replaces config.seed with that value.
The factory receives stable job ID, constructs a fresh model on its executing
worker, then metadata runs and the runner uses remaining global time. Models,
outputs and feature keys need not be Send/Sync in Rust; only report inputs cross
workers and the factory is Sync. A native worker design must preserve model-local
ownership and application-defined input transfer without imposing canonical
ModelCodec or a shared mutable model. Guided campaigns additionally create fresh
feedback and corpus per job; feedback_factory time counts against global time.

Within a claimed job the order is model factory -> model metadata -> runner;
the guided runner creates its remaining-time control -> feedback factory -> bare
guided validation/control -> feedback metadata -> fuzz preflight. There is no
intervening cancellation short-circuit that skips metadata or the feedback factory
after model construction. Only model-factory Err becomes FactoryError, with
metadata None and known zero fuzz work. Feedback-factory Err is runner ModelError
with model metadata already Some; it makes accounting incomplete even when no
fuzz work occurred. Metadata panic leaves None; runner/feedback panic normally
retains Some. Recoverable model destruction panic inside the job boundary also
turns its outcome into Panicked. The factory/feedback closures must be Sync and
report input values Send even in the one-worker public API; the sink need not be
Send/Sync, runs serially on the caller, and receives ownership of each JobReport.
The engine invokes fresh factories but
cannot prevent an application from returning aliased interior mutable storage.
Native isolation proposals must specify this responsibility explicitly. Source:
`src/campaign.rs:322-356,413-469`.

Fixed job ID/model/seed yields reproducible inputs under deterministic callbacks
and matching logical budgets when timing controls do not truncate the run.
Completion order, deadline coverage and the subset
claimed under stop_on_failure are scheduler dependent. Native process/thread/task
mechanisms can differ, but cannot collapse multi-worker qualification into serial
runs. First_job and jobs replay an independent job range; this is not restoration
of an active queue, durable exactly-once delivery or shared corpus resume.

One worker runs on the calling thread with no spawned worker threads. Multiworker
execution attempts min(workers,jobs) workers and uses a bounded synchronous result
queue; buffer0 is rendezvous. The branch is chosen by configured workers, so
workers>1/jobs1 still spawns one thread. Each worker completes its lifecycle event
before sending and cannot claim again until the send succeeds. Channel receipt
order can differ from completion-event order because another worker can send
between a worker's Complete and send. No job-ID sort or global completion ordering
barrier precedes the sink.

A worker can hold one completed report in addition to queue capacity; the sink may
hold one. While a healthy sink call is blocked, outstanding started-but-not-yet-
successfully-delivered work is bounded by result_buffer+actual_workers+1 (and jobs).
This is not a bound on cumulative started jobs after earlier deliveries, or on
reports retained by the application sink. Factories/models/sinks and application
report destruction run outside the coordinator lock. Release execution moves its
live lifecycle state instead of cloning pending state; debug_assertions builds do
clone a snapshot for checks, containing protocol summaries rather than application
reports. Source: `src/campaign.rs:218-262,359-387,480-537`.

Rust's `cfg(all(target_family = "wasm", target_os = "unknown"))` rejects requested
wall-clock limits and configured workers>1, including workers>jobs with jobs1.
One-worker runs without wall-clock limits retain cancellation and logical budgets.
These guards describe this Rust target and its standard-library facilities, not
a blanket prohibition on independent native TypeScript browser clocks or workers.
Browser-native capabilities require explicit host APIs and separate qualification;
they cannot be inferred from the Rust guard or a Node pass. Source:
`src/explore.rs:98-105,134-173`, `src/campaign.rs:175-215,471-478`,
`docs/NATIVE-PARITY-PLAN.md:296-303`.

## Lifecycle, delivery and faults

The runtime uses the private Lifecycle law, not merely an illustrative demo:
Claim -> Complete -> Receive -> SinkReturned, with explicit WorkerFailed and
Finish. Pending entries contain Running or Ready(summary), never application
reports. Claims allocate monotone IDs exactly once. Complete validates a running
job and coherent summary and can stop new claims immediately, before receipt.
Receive removes Ready, increments finished, failure/error counts and reported
work, then emits Deliver or (after sink error) Discard. Only successful
SinkReturned increments delivered. A failing sink may already have external side
effects; it is not counted delivered and is never blindly retried.

Duplicate/stale/out-of-range completion/receipt/acknowledgement, inconsistent
summary, overlapping sink delivery, premature finish and post-finalized input
reject inertly. Controls sampled after all jobs were claimed cannot retroactively
change a completed campaign; active job cancellation/deadline outcomes remain
observable. Failure stops claims only when configured, and never cancels already
claimed work by itself. Such jobs finish unless external controls stop them.
Claims also reject inertly when a stop is already set: a flag sampled by a later
rejected Claim does not upgrade a prior failure/deadline stop. An active job's
Cancelled completion can upgrade either; among ordinary completion-derived stops,
cancelled dominates deadline, which dominates failure stop. Sink error
remains the authoritative termination over worker errors and drained reports;
worker error remains over ordinary cancellation/deadline/failure at finalization.
Do not reduce this to a single last-completion-wins enum.

FactoryError, ModelError and Panicked are job errors, not property failures, and
do not stop new claims even with stop_on_failure=true. A campaign can therefore
end JobsCompleted with errors>0, or with failures>0 when stop_on_failure=false.
The errors field counts received erroneous job outcomes; sink and worker
infrastructure errors are represented in termination, not added to that counter.
CasesCompleted and TransitionLimit both map to lifecycle Completed. Summary
validation checks the failure/outcome relation and zero counters for error outcomes;
it does not prove the report's work against configured budgets. The private runtime
owns summary construction. Source: `src/campaign/lifecycle.rs:51-89,194-213,282-299`.

FactoryError is known zero fuzz work and increments errors without losing
accounting. ModelError and Panicked increment errors and make accounting
incomplete; their hidden partial fuzz work is not guessed. Aggregation uses the
counters carried by completed fuzz reports, including property failures or
cancelled/deadline reports. Aggregates saturate u64 on their own addition overflow
and mark incomplete. A per-job skipped_checks counter itself saturates without a
separate saturation flag, so accounting_complete does not prove that the underlying
number of skipped checks was unsaturated (`src/explore.rs:1381-1407`).
Missing worker outcome similarly loses accounting. accounting_complete does not
mean every requested job started, every outcome delivered or every property passed.

A sink error/panic stops new sink calls and scheduling; active/queued reports are
drained and counted but discarded, workers are joined, and the original sink
error survives later disposal or worker faults. Per-job factory/metadata/runner
unwinds become Panicked only with unwind builds; abort/OOM/fatal host traps are
not recoverable outcomes. Rust reads only known String/&str panic payloads, not
arbitrary formatting, and contains payload-destruction panic (secondary payload
can be leaked). Native exceptions/disposal need explicit faithful policies, not
a promise to catch Swift traps or process death. Thread-start/join failure is
WorkerError, with explicit lost_work for missing outcomes. Deadline covers
scheduling/factory/feedback/search, but not preemption of callbacks/destructors or
sink I/O. Cancellation wins simultaneous external deadline sampling.

WorkerError alone does not disable the sink: remaining received reports still
deliver until a sink error causes Discard. Worker-start failure records
lost_work=false, while any joined worker panic records lost_work=true conservatively,
even if its last job already sent an outcome. Later WorkerFailed events overwrite
the earlier worker error unless CallbackError already owns termination. Pending
work may remain at Finish only after explicit lost_worker, with no active delivery;
otherwise it must all have been received. No unconditional outer catch guarantees
recovery from coordinator assertion/poisoning, abort, or double panic while
unwinding. Source: `src/campaign.rs:359-387,510-539`,
`src/campaign/lifecycle.rs:249-302`.
Join panics are observed after channel draining; surviving workers may keep
claiming before that observation. Missing outcomes are not replaced with synthetic
JobReports. Worker startup failure is observed during the spawn loop.
The precedence is based on accepted events, not on a final reread of flags;
for example cancellation set by the last sink does not change JobsCompleted once
all jobs are claimed. Source: `src/campaign/lifecycle.rs:175-213,282-301`.

## Lifecycle self-model, codecs and coverage integration

Port a real finite self-model fixture over the runtime decision law, with an
independent event ledger, not a second implementation comparing to itself.
Source tests include stale/duplicate events, all stop completion permutations,
first sink error, missing worker outcome, saturated accounting and exhausted ID
ranges. The fault model deliberately duplicates Deliver; independent checks must
find it, shrink its witness, record/read the fault trace and reproduce the failure.
Strict healthy replay must reject its altered build; explicitly relaxed build
comparison must diverge without reproducing the faulty property. Healthy traces
must roundtrip and replay independently. The lifecycle codec is test-only today:
its persistence is acceptance evidence, not a newly public scheduler resume API.

Guidance feedback is independent of assurance coverage. Bare N05/N06 do not
require assurance manifests, coverage observers, or assurance report merging.
N10 and full M4 integration do require that additional coverage layer; do not make
it a prerequisite for implementing or qualifying the bare guided/campaign APIs.
The plan scopes N05/N06 separately at `docs/NATIVE-PARITY-PLAN.md:131-156`, N10 at
196-209, and combines them in M4 at 332-333. Reuse observed fuzz for the integration
so checks/scenarios retain their own manifests, identities and failure reports.
Assurance guided_fuzz records algorithm, all guidance bounds, feedback
name/version/build plus ordinary seed/budgets. That wrapper queries feedback
metadata for budgets and the guided driver queries it again if guided admission
succeeds; bare guided fuzz queries once after admission. Before either query,
the wrapper creates its observer from model.metadata and validates the manifest.
The first feedback metadata call precedes guided validation and the guided clock;
only the second call falls within that clock. Do not apply the bare timing or
one-call count to wrapper fixtures. Source: `src/assurance.rs:784-823`.
CLI audit parameter spellings differ from assurance keys and must be scoped
(`src/main.rs:411-437`).
Assurance campaign merging is an adjacent N10/M4 integration: per-job coverage
is independent; merge sorts job IDs, rejects unexpected/duplicate IDs and
incompatible manifests or executable metadata, and retains missing/interrupted
jobs. The assurance campaign wrapper separately incorporates sink/worker errors,
lost accounting and unsuccessful delivery; merge_reports alone does not receive a
CampaignReport. That wrapper currently runs ordinary observed fuzz, not guided
campaigns. A combined guided-and-assured campaign API is a remaining integration
decision, not an existing public wrapper. Source: `src/assurance.rs:846-966,974-1117`.
Feature novelty and ordinary campaign aggregate counters alone are not coverage
certification or graph exhaustion. No generic associative merge of CampaignReport
is exported; do not invent one by analogy with assurance merge_reports.
The N06 plan's "deterministic merges" requirement still needs an explicit native
contract: job-ID ordering and result collection are available facts, while a new
generic aggregate-merge API would need separately specified identity, overlap,
error precedence and accounting laws.

## Independent finite acceptance work before implementation closure

Author manual expected bytes/counters/callback/RNG traces before running the real
public Rust references, then execute native and installed consumers via Kyoto.
Items 1-9 concern N05/N06; item 10 is the separate N10/full-M4 integration gate.
These are future acceptance tasks, none executed by this audit:

1. Guidance admission rejects each zero bound before feedback/model callbacks;
   invalid mutation percent follows guidance validation. Metadata/deadline and
   pre-cancel cases pin feedback-metadata1, model-metadata0, initial0 and feature-
   feedback0 in the bare path; a pre-cancelled campaign calls no metadata at all.
2. Tiny sequence fixtures pin every mutation mode, RNG draw/state, corpus choice,
   deletion endpoints, full-prefix versus replacement and disabled-reuse fallback.
   Include percent0/100, empty corpus, index1 and rejection-loop draws; pin reuse
   after a single replacement and candidate draws before a reached transition cap.
   Compare exact case/turn
   sequences, check order, witnesses and final corpus/report, not just counts.
3. Constant-hash distinct features versus duplicate emissions: emissions hit the
   per-observation bound independently of unique keys. Test swallowed overrun,
   overrun then custom Err, initial/transition errors, and failure before feedback.
4. For one case with successive novel prefixes lengths1,2,3, entries2 and total
   inputs5: after third admission retain [length2,length3], inputs5, evicted1.
   Initial features add no empty corpus entry. At total inputs1 a novel length2
   prefix is dropped; later length1 observation of that key recovers an entry
   new_features0 even at max_features1. Eviction-only rediscovery does not refill;
   repeat overlong known features without extra drops and exactly fill limits
   without prematurely setting saturation flags.
5. Full checks/observers plus cancellation set inside initial/transition feedback,
   callbacks, generation and checks; exact failure/control/error precedence,
   partial check_error batches, observer panic, retained report shape and no
   repeated reducer/checker. Replay retained passing corpus witnesses; shrink and
   replay generated property failures. Passing corpus entries have no failure
   target and are not accepted shrink inputs (`src/explore.rs:1006-1010`).
6. Seed arithmetic includes master/id wrapping, first_job slices, max job ID,
   aggregate overflow validation and actual cap-before-aggregate multiplication.
   Invalid config/guidance causes factory0/sink0; zero cases/steps/transitions and
   pre-cancel/zero duration retain exact source meanings. Combine invalid guidance
   and config to pin first error; contrast model-factory Err/metadata None with
   feedback-factory Err/metadata Some/accounting incomplete. Factory cancellation
   must preserve subsequent metadata/feedback construction before fuzz preflight.
7. Compare one-worker and controlled multi-worker ordinary/guided jobs by stable
   job ID, config/seed, inputs, metadata, local features and outcomes. Force
   out-of-order completions and prove no model/corpus/coverage sharing. Distinguish
   host receipt order from lifecycle completion order and deterministic sorted
   per-job comparisons. The bounds use configured workers to select the execution
   branch; workers>1/jobs1 is not the calling-thread path.
8. Barrier-driven queue/sink tests with buffer0/1, workers>jobs, blocked sink and
   stop_on_failure with already claimed work. Acknowledge exactly once, inject
   sink error/panic and start/join faults, drain without further sink calls, preserve
   first sink error and exact started/finished/delivered/not_started/accounting.
   Distinguish job errors from stop_on_failure and worker-only faults from sink
   suppression. Keep live-work bounds distinct from cumulative started counts.
9. Finite lifecycle alphabet with wrong IDs, duplicate completion/receipt/ack,
   inconsistent summaries, every stop precedence permutation, finish constraints,
   saturated counts and terminal immutability. Use an independent ledger to detect
   incorrect rejection as well as duplicate work. Port the duplicate-Deliver
   fault through actual BFS/fuzz/shrink/record/read/replay and healthy negative
   control; keep fixture build/codec identity explicit.
10. N10/full-M4 assurance integration/installed applications: pin feedback identities and
    budgets, independent per-job observer state, sorted deterministic merge with
    missing/duplicate/incompatible job negatives, and cancellation/delivery loss
    that cannot be washed out by successful siblings. Existing profile baselines
    and fresh witness replay remain independent regression gates.

Native unit tests, lifecycle byte roundtrips, deterministic algorithm fixtures,
actual host scheduling tests and installed package interchange are separate
proof classes. No timing/performance, broad browser/platform, hard-RSS or general
coverage claim should be inferred from a focused deterministic pass.

## Explicit scope decisions and source ambiguities to settle

The core guided/campaign laws above are concrete. Native callback containment,
revocable leases, mutable-value snapshots, async rejection and worker startup/
disposal failures need language-specific API proposals and honest native-only
controls. Stable feature equality/hash must not inherit invalid NaN or canonical
string identity silently; fixed framework identifiers use exact text, application
compound payload equality remains explicit. The source counts guidance resources
but does not expose byte admission or persistent corpus import; these cannot be
claimed without a separate reviewed API. Lifecycle internals/codec are private
and test-only, so an external reference harness needs an approved test fixture or
private-source adapter, not a claim that callers can import Lifecycle today.
Guided/assurance wrappers query metadata differently and do not validate all
identities uniformly; record which actual public entry is qualified. Native
parallelism/serialization and recovery after process death are not specified by
Rust's in-process worker mechanism; define host limits without changing the job
seed, lifecycle, result-delivery and accounting contracts. This audit authorizes
none of those implementations or new qualification claims.
