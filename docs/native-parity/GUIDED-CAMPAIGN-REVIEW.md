# N05/N06 guided/campaign audit review

Source-only review resumed 2026-10-22 in the shared checkout
`/Volumes/seed2/Projects/stateless-native-parity`, branch
`docs/native-language-parity`, initial HEAD
`b5c62f5258cc91e2f878cadb6020d5aea6ce1009`.
The Oct20 [audit](GUIDED-CAMPAIGN-AUDIT.md) was untracked when this review began;
this review document did not yet exist. No applicable AGENTS.md was found at the
checkout, its ancestors, or beneath docs/src.

Only the audit and this review document are owned by this work. No runtime source,
tests, packages, receipts, staging or commits were changed. No builds, tests,
benchmarks, fixtures or installed consumers were executed. The parent's installed
N14 work and frozen runtime source remain outside this task.

## Source identity and method

The following SHA-256 values pin the principal source/plan anchors read for this
review; line references below refer to these files in this checkout, not the
caller's default main checkout. Test files were read only as authored regression
intent, never treated as executed evidence. The native package inventory was a
scoped source search and does not qualify those implementations.

| File | SHA-256 |
| --- | --- |
| `src/guided.rs` | `bbc7b214510f862738991074f8f2053389c4cac85caf005ebc129df9b06a18d7` |
| `src/campaign.rs` | `4d4da9479fe3512589df379cba9a1f3e5df989bba6920e3691479e6b3ef19005` |
| `src/campaign/lifecycle.rs` | `7931c3ef82bf7f2b806448ef8a65f8f2e4a136d38c58d8e06307ee4b090ef44a` |
| `src/explore.rs` | `496c19c7181a6bf3f2af97d3e04df1d3c7b22c241351104b4cd66201f6706ecb` |
| `src/model.rs` | `e69a80ad96826a27871807f848e22fd213a7bee9f95d76be92069e68504f5beb` |
| `src/assurance.rs` | `bca62843eecca4c264a620e13a7ab274115e6d1f8e05ebefd8479d71bdecbbaf` |
| `src/operations.rs` | `ec19cf028645dd16f6f389cc7fa0fa3a44a72a02838842a906ca11bb2303649b` |
| `docs/NATIVE-PARITY-PLAN.md` | `8d9e0fa19796931a2b85368092120e692e9e0f50788b9f36b2f22aa463ab7301` |

Reviewed all behavioral sections of the audit against the implementations, with
particular attention to callback/control boundaries, mutation draws, feature
admission, lifecycle state/effects, channel ownership, and final error selection.
A separate read-only review of campaign/lifecycle used the same checkout and
reported five implementation-critical omissions plus the debug-snapshot
qualification against the original document snapshot. Its findings independently
agree with the corrected callback trace, job-error behavior, teardown/worker
handling, live-work bound, and inert late Claim behavior recorded below. The
reviewer made no edits or executions; this author reconciled its findings with
the revised audit and source.

## Three interrupted precision findings

All three are resolved in the audit, with the following source-derived scope.

1. **P2 — Assurance prerequisite was overstated.** Bare guided search and ordinary/
   guided campaigns have no assurance dependency in their public call paths
   (`src/guided.rs:165-232`, `src/campaign.rs:413-454`). N05/N06 are scoped at
   `docs/NATIVE-PARITY-PLAN.md:131-156`; N10 adds manifests, coverage and aggregation
   at 196-209; M4 combines N05/N06/N10 at 332-333. The audit now separates its
   bare acceptance work from the N10/full-M4 integration gate. Assurance remains
   required for that combined milestone, without blocking the bare APIs.

2. **P2 — Guided admission and factory outcomes required exact ordering.**
   `run_guided_campaign` calls guidance.validate at `src/campaign.rs:449`, then
   CampaignConfig validation at 471. The executor calls model factory, stores
   model metadata Some, and only then invokes the runner (338-347). The runner's
   feedback_factory Err (451-453) is therefore ModelError with metadata Some,
   not FactoryError. Only model-factory Err is FactoryError/metadata None.
   Lifecycle accounting marks ModelError incomplete even when that particular
   failure happened before fuzz work (`src/campaign/lifecycle.rs:225-231)).
   The audit and proposed acceptance witnesses now preserve all distinctions.

3. **P2 — Rust target restrictions cannot be generalized to native browsers.**
   The wall-clock guards are compiled only for
   `all(target_family = "wasm", target_os = "unknown")`
   (`src/explore.rs:134-146`, `src/campaign.rs:175-185)); the same target's
   worker guard rejects configured workers>1 at `src/campaign.rs:471-478`.
   Independent TypeScript browser capabilities need their own host APIs and
   qualification, as required by `docs/NATIVE-PARITY-PLAN.md:296-303`.
   This review neither forbids nor qualifies native browser workers/clocks.

## Additional findings corrected in the audit

These are documentation/implementation-input findings; none requests a change to
the frozen Rust runtime.

- **P2 — Callback errors are not interchangeable with property/control stops.**
  The audit now specifies complete versus partial `check_error` batches,
  state-check before transition-check ordering, normal observer-before-failure
  extraction, and the absence of a Rust observer Result/abort channel
  (`src/explore.rs:17-39,1410-1462`). A transition-checker Err can supersede
  already-emitted failed state checks. Successful reducer/checks are not separated
  by control polls; successful feedback learns before its following poll.
  Feedback/callback Err returns no successful partial guided report
  (`src/explore.rs:763-886`, `src/guided.rs:323-362`).

- **P2 — Exact RNG and budget edges were underspecified.** A replacement generates
  one candidate position; later positions remain eligible for reuse. None from
  generation ends the case. The transition cap is checked at step entry, after
  case setup/candidate selection, and never for steps0; a final transition can
  end CasesCompleted without another cap check. A subsequent case can consume
  candidate draws before observing the cap (`src/explore.rs:743-890`,
  `src/guided.rs:295-320`). The audit states the rejection threshold and variable
  draw count from `src/model.rs:477-510`, including index0/index1 behavior.

- **P2 — Corpus counters must follow attempted admission, not occupancy.**
  Repeated overlong observations containing no newly tracked key do not increment
  dropped_prefixes. Recovery requires a retainable prefix; merely filling limits
  does not set saturation flags. Eviction leaves witnessed keys marked and
  recovering tracked-but-unwitnessed keys at the cap can admit new_features0
  (`src/guided.rs:243-291`). The revised acceptance work includes these cases.

- **P2 — Delivery bounds and ordering needed their actual domain.** The bound is
  on outstanding work while a healthy sink call blocks, not cumulative starts
  after prior successful deliveries or reports retained by the sink. Each worker
  completes before its blocking send and cannot claim another job until that send
  succeeds. Channel receipt can differ from lifecycle completion order; there is
  no deterministic job-ID delivery sort. Configured workers>1/jobs1 takes the
  spawned path (`src/campaign.rs:480-537`). Release execution moves lifecycle
  state, but debug assertions clone protocol-only snapshots
  (`src/campaign.rs:232-259`).

- **P2 — Job errors, worker errors and accepted control events have different
  effects.** FactoryError/ModelError/Panicked do not trigger stop_on_failure;
  JobsCompleted may coexist with errors or non-stopping property failures.
  A Claim after an existing stop or after all jobs were claimed is inert, even
  when its sampled flag is set. Accepted job Cancelled/Deadline completions still
  affect ordinary stop priority (`src/campaign/lifecycle.rs:175-213,282-301`).
  WorkerError alone keeps delivery enabled; only CallbackError switches receipts
  to Discard. The first sink error remains authoritative, while later worker
  errors replace earlier worker errors (`src/campaign/lifecycle.rs:249-280`).
  The errors counter excludes sink/worker infrastructure failures. Reported
  counters aggregate only exposed job counters; skipped_checks can already have
  saturated inside a job without a flag (`src/explore.rs:1381-1407`).

- **P2 — Ownership and panic boundaries needed explicit limits.** Model, state,
  outputs and feature keys stay worker-local; report inputs cross workers.
  Send/Sync bounds still apply to the public serial API; the sink is serial on
  the caller and receives each JobReport by value. Fresh factory calls do not
  themselves prevent aliased application storage (`src/campaign.rs:322-356,
  390-469`). Model destruction inside execute_job can replace a successful
  outcome with Panicked. Start failure has lost_work=false; joined-worker panic
  has lost_work=true conservatively and is observed after channel draining,
  without synthesizing missing job reports. Surviving workers may claim more jobs
  before join failure is observed. This is not universal recovery from abort,
  double unwind, or poisoned coordinator state (304-387,510-539).

- **P2 — Passing corpus witnesses are not shrink targets.** The prior acceptance
  wording said to replay/shrink every corpus witness. The audit now requires
  replay of passing witnesses and shrinking of property failures. The shrinker
  explicitly requires a target violation (`src/explore.rs:1006-1010`).

- **P2 — Assurance wrappers need their own timing and aggregation description.**
  Successful assured guided admission queries feedback metadata twice; the first
  call precedes the guided clock and its validation. Invalid guided admission
  may occur after that first call. Bare guided search queries once after admission
  (`src/assurance.rs:784-823`, `src/guided.rs:199-229`).
  merge_reports handles expected/job report identities, not CampaignReport;
  the assurance campaign wrapper adds delivery/loss/errors afterward and currently
  runs ordinary observed fuzz (`src/assurance.rs:846-966,974-1117`).

## Confirmed assertions and remaining design decisions

Source inspection confirms sticky feature-buffer overrun with callback Err taking
priority, feature equality separate from state/replay equality, FIFO retention,
stable per-job seed derivation, live lifecycle summary ownership, at most one
successful delivery acknowledgement per received report, drain-after-sink-error
accounting, and test-only lifecycle codecs. The self-model source contains an
independent ledger, duplicate-Deliver fault, shrink/record/read/replay, strict
healthy build rejection and relaxed healthy divergence controls
(`src/campaign/lifecycle/tests.rs:84-115,174-248,1039-1118,1167-1189`).
Those tests were not run here.

The following remain design/qualification work rather than resolved runtime
claims:

1. Define native feature equality/hash, owned key/input snapshots and callback
   lifetimes. Specify async return rejection, revocable sinks, recoverable
   exceptions, and cleanup errors per language without claiming Rust provides
   those native policies or deep-copy enforcement.
2. Choose each host's worker/factory/input-transfer mechanism, including browser
   integration, and state supported clock/cancellation semantics. Preserve bounded
   delivery and actual multiworker evidence without requiring canonical ModelCodec
   or implying recovery from fatal process/trap failures.
3. Resolve the N06 plan's deterministic-merge acceptance wording: Rust exports no
   generic CampaignReport merge. Distinguish sorted independent job collection
   from any new aggregate merge with overlap/identity/error/accounting laws.
4. Scope a combined guided-and-assured campaign API for N10/full M4 separately:
   existing public assurance.run_campaign uses ordinary observed fuzz. Do not
   infer that combined API from two adjacent wrappers.
5. Choose a source-bound test-only reference adapter for the private Lifecycle and
   codec; independently author expected outcomes before later Kyoto execution.
   Public checkpoint, seeded-corpus import, durable queue resume and hard memory
   caps remain separate API proposals.
6. Implementations, deterministic fixtures, real host scheduling, lifecycle
   persistence, installed-consumer and platform qualification remain outstanding.
   This source audit closes none of N05, N06, N10 or M4.

## Review disposition

The three interrupted findings and the additional findings above have been
corrected in the audit. The independent campaign review's five omissions and
debug-snapshot qualification are incorporated, including join-after-drain timing
and ownership of delivered reports. No unresolved implementation-critical
documentation findings remain within this source-only review scope. The open
design decisions above remain explicit; this is not runtime approval or N05/N06
closure.

The final Git observation found shared HEAD at
`32325900335ccf770a33a5b00dff4ab536dfd82f`, no tracked working-tree changes, and
both owned documents still untracked. The branch advanced outside this review;
no staging or commits were performed by this reviewer. A subsequent hash read
confirmed all eight pinned source/plan files still matched the initial snapshot.

Final read-only verification found all eight pinned source/plan hashes unchanged,
all 15 local Markdown links resolving, and no trailing whitespace in either
document. These are source/document checks, not executed behavioral evidence.

Exact files changed by this review:

- `docs/native-parity/GUIDED-CAMPAIGN-AUDIT.md` — revised the interrupted audit.
- `docs/native-parity/GUIDED-CAMPAIGN-REVIEW.md` — added this resolution record.
