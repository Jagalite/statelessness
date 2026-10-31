# N06 independent contract/reference review — round 2

Source-only review on 2026-10-22 in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336`, branch
`t3/native-campaign-runtime`, HEAD
`0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`.
Only this document was written. No source, frozen author inputs, manifests or
receipts were edited; no builds, tests, fixture execution, submissions, staging,
commits, queue changes or other-checkout work were performed. The Kyoto skill was
read. Recorded job metadata was inspected without querying or restarting jobs.

The four original contract findings are addressed in the reviewed source.
The independent 49-row derivation, private adapter and package inclusion change
are suitable for their pending execution gates. This is not runtime approval:
the native differences below remain open, expected/FREEZE artifacts are absent,
and the strengthened private adapter is outside the queued r1 compile snapshot.

## Open findings and integration dispositions

1. **P2 — Swift's example projection still rewrites observable diagnostics.**
   `examples/native-campaign/swift/Sources/CampaignConsumer/Projection.swift:123`
   calls `canonicalModelError`; lines 131–137 translate `transition: ` to
   `step: ` and `state check: ` to `check_state: `. For `step-error` and
   `check-error`, the example can therefore emit Rust's expected diagnostic while
   the actual public Swift diagnostic differs. Separate raw-policy output does
   not make those normalized rows exact observations. The installed harness
   correctly retains `failure.description` at
   `scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/Projection.swift:91`.
   **Required disposition:** keep the example's normalized rows outside strict
   parity evidence, or have its owner preserve the actual diagnostics. Do not
   substitute the example for the installed harness to obtain a passing row.

2. **P2 — Feedback-metadata panic provenance remains a native dependency in
   Swift and TypeScript.** The Swift fixture throws `CampaignPanic` at
   `scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/Deterministic.swift:86`.
   `swift/Sources/Statelessness/Guided.swift:227` passes it through
   `Model.swift:149–152`, which wraps it as `ModelError`.
   `CampaignRuntime.swift:187–193` has already stored model metadata, so this is
   **ModelError / Some metadata / incomplete accounting**, with diagnostic
   `feedback metadata: feedback metadata panic`.
   TypeScript has the same visible dependency: the ordinary Error thrown at
   `scripts/native-campaign-consumers/typescript/deterministic.ts:84` passes
   through `typescript/src/guided.ts:253` and `guided_sync.ts:27–29`, then becomes
   the ModelError outcome at `campaign.ts:159–164`.
   Rust instead unwinds from `src/guided.rs:208` into
   `src/campaign.rs:338–349`: **Panicked / Some metadata / incomplete**, diagnostic
   `feedback metadata panic`. The independent golden at
   `conformance/fixtures/native-campaign-golden.py:50–52` is correct.
   **Required disposition:** retain the differing native rows as failures of exact
   comparison. Root must decide any separately reviewed N05 provenance change or
   faithful campaign adaptation. Do not alter the Rust expectation, filter the
   case, or relabel ModelError as Panicked in a projection. None of this concerns
   catchability of Swift fatal traps or process aborts.

3. **P2 — Public native admission/diagnostic differences still prevent an exact
   all-engine 49-row pass.** Python `FuzzConfig.__post_init__` rejects mutation101
   at `python/src/statelessness/search.py:258–261`, using `_natural` at
   `core.py:30–32`. Thus `mutation-101` returns
   `mutation_percent must be an integer in 0..100`; `validation-first-error`
   cannot reach Rust's jobs-first validation at all. The consumer preserves this
   constructor result at
   `scripts/native-campaign-consumers/python/consumer.py:436–442`.
   Additional concrete diagnostic differences include Go's `initial state: `
   (`go/fuzz_kernel.go:28`) versus Rust's `initial_state: `, Python's
   `transition: ` (`core.py:310–313`) and `state check: `
   (`search.py:220–222`), Swift's `transition: ` (`Model.swift:171`) and
   `state check: ` (`CheckSink.swift:62`), and TypeScript's `state check: `
   (`typescript/src/search_support.ts:61`). Rust uses `step: ` and
   `check_state: ` at `src/explore.rs:849,1440`.
   **Required disposition:** these are public API/core compatibility decisions,
   not schema fixes. Preserve actual errors in the installed consumers and
   report the failing cases. Any production/N05 change needs separate review and
   regression evidence; the comparator must not erase the differences.

These findings do not request production Rust changes. The reference source
review found no additional substantive defect in the 49 public rows or the
targeted post-r1 worker-loss correction. Native runtime implementation coverage
and the entire installed harness are separate reviews; this list is not an
exhaustive native bug inventory.

## Disposition of the four original contract findings

- **R1-1, schema/projection ambiguity: resolved in the protocol source.** The
  complete schema defines decimal-string mutation percentage, input values and
  versions; `fuzz.seed`; flat `{phase,id,status}` violations; complete guidance,
  report, lifecycle and byte-hex objects; explicit nulls; and forbidden extra
  fields (`native-campaign-v1.schema.json:14–175`). Its fixed failed-check status
  is intentionally the finite fixture's `Failed("second transition")`, not a
  generic cross-language Debug serializer. The public emitter agrees at
  `native-campaign-public.rs:78–107,143–230`; the independent author agrees at
  `native-campaign-golden.py:27–29,85–87,98–130`. The current Go, TypeScript,
  Python and Swift installed projections now include the previously disputed
  fields/types. Raw native diagnostics and outcome differences above remain
  separate. Semantic integer bounds are explicitly outside the schema's decimal
  grammar; schema acceptance alone is not API admission or behavioral proof.
  The contract also explicitly withdraws the draft's serialized validation
  counters/thread-role fields; actual assertions cover those separately.
- **R1-2, one-way acceptance: resolved in source.** The contract now requires
  `accepted == admissible`. The adapter computes admission from ledger facts
  (`native-campaign-rust/adapter.rs:193–225`), including independent Finish
  admission. Its false-rejection control at 693–714 tests both the ledger
  disagreement and the actual core property. This adds independent checking
  beyond the original core-based checker at
  `src/campaign/lifecycle.rs:450–469,505`.
- **R1-3, metadata panic stage: resolved in Rust contract/fixtures.**
  `metadata-panic` leaves metadata None; `feedback-metadata-panic` retains Some.
  Both produce Panicked, no fuzz report and incomplete accounting. The latter's
  exact callbacks are factory, metadata, feedback_factory, feedback_metadata,
  feedback_drop, model_drop, sink. These follow the actual job boundary and
  Rust ownership unwinding; the independently authored rows distinguish them.
  Swift/TypeScript equivalence is explicitly unresolved above.
- **R1-4, JSON byte equality: resolved in source.**
  `native-campaign-golden.py:133–170` rejects duplicate keys/non-finite constants
  and recursively checks exact Python types, key sets, values, row order and
  array order. Object-key order and whitespace are insignificant. Numeric
  strings cannot equal numbers, and booleans cannot equal integer substitutes.
  `tests/native_campaign_reference.rs:32–43` invokes that comparator through
  `native-campaign-public.rs:710–728`. Artifact byte hashes remain provenance,
  not semantic equality. The same-source bare-per-job serializer comparison at
  `tests/native_campaign_reference.rs:106` is a separate differential test, not
  the independent JSON golden assertion.

## Independent derivation and Rust authority checks

The author imports only its independent stdlib fixture modules, never a native
engine or candidate result (`native-campaign-author.py:24–50`). It assembles
exactly 15 seed, 15 validation and 19 campaign rows, checks unique IDs and separate
inventories, and seals author/schema/contract/expected hashes with
`candidate_output_used:false` (45–65). Reading the schema for hashing is not
executing a JSON Schema validator; no schema-validation run is claimed here.

The 15 seed pairs at `native-campaign-inventory.py:15–22` correspond in order to
`native-campaign-public.rs:411–426`. The independent masked arithmetic at 8–12
matches wrapping master+ID followed by one SplitMix64 output, not a shared
worker RNG stream (`src/campaign.rs:91–93`, `src/model.rs:486–491`). The high-bit
and above-safe-integer wrapping pairs reduce to the intended zero/one seed
states. This review inspected their derivation; it did not execute the numeric
vectors or materialize a corpus.

The 15 validation specifications at `native-campaign-inventory.py:29–44` match
the real constructors at `native-campaign-public.rs:591–708`: zero jobs, workers
0/257/256, buffers1025/1024, max ID, adjacent ID overflow, mutation101/100,
aggregate cases/transitions, wide-product-capped, max shared job count and the
combined jobs-first error. For `S=2^53-1`, `2049*S > u64::MAX`, while `2*S` fits
and `S*S` exceeds u64. The valid capped case is `2*min(S*S,1)=2`, matching
`src/campaign.rs:79–84`. Max ID/jobs1 is valid; jobs2 overflows. All configuration
count operands fit the shared safe-number domain; full-width IDs, seeds and
max_transitions are retained unchanged. The separate usize::MAX test at
`tests/native_campaign_reference.rs:15–27` is Rust-only, not a substituted shared
row. Accepted huge budgets are validated, never executed.

The 19 ordered campaign names agree between `native-campaign-golden.py:10–14`
and `native-campaign-public.rs:569–589`. Manual callback paths agree with actual
factory/metadata/runner/drop boundaries and `src/explore.rs:738–890,1410–1461`:

- Ordinary success has cases1/transitions2/skipped3. Guided success adds initial
  feature0, then features1/2, retains `[1]` and `[1,2]`, unique3/inputs3, and
  neither saturation flag; exactly filling a limit is not attempted excess.
- FactoryError has None metadata, known zero work and complete accounting;
  feedback-factory error has Some metadata, ModelError and incomplete accounting.
  Both two-job rows continue despite stop_on_failure. These are job errors, not
  property/infrastructure failures.
- Pre-cancel and zero duration call no factories/metadata/sink. Factory-set
  cancellation still reaches model metadata, feedback factory and feedback
  metadata before the zero-work cancelled report. Feedback cleanup precedes
  model cleanup and delivery.
- Cases0 performs no initial callback; steps0 still performs two initial/check
  rounds even with transitions0; transitions0/steps2 performs initial/check work
  before returning TransitionLimit. Final-step cap equality can end
  CasesCompleted. Property failure still executes state then transition checks,
  retains `[1,2]`, counts skips and stops later claims.
- Initial/step/check errors retain actual Rust stage text and expose no guessed
  partial work; model destruction panic replaces the successful fuzz outcome.
  Sink error/panic retains a finished but undelivered report and the exact
  callback-error diagnostic. The one-job golden does not itself prove drain.

The public test additionally compares each ordinary/guided job to a fresh bare
public call with derived seed and actual callback/input history for workers1/3
(`tests/native_campaign_reference.rs:46–124`). Configured workers>1/jobs1 is
explicitly checked as spawned (151–171). The rendezvous test causally waits for
the sender to claim job2 while sink1 remains active and job0 is held; it requires
two distinct worker threads and out-of-order first receipt (174–227). Source
inspection confirms Complete-before-send and Receive-before-sink at
`src/campaign.rs:363–382,501–507`, serial caller sink and drain-before-join at
523–535. Those are authored tests/source facts, not runtime results or a measured
hard memory bound. Existing `tests/campaign.rs` remains a separate execution gate.

## Private ledger, fault controls and post-r1 loss correction

`native-campaign-rust/build.rs:6–26` copies the four actual campaign/lifecycle/
codec/test source files, then appends only a private test-module declaration.
`lib.rs` reexports the real crate and includes that generated entry. No production
visibility or transition law is changed. The actual test codec retains u64
little-endian values, tags, strict UTF-8, sorted unique pending IDs and state
validation (`src/campaign/lifecycle/codec.rs:7–235,244–335`). Inconsistent Summary
input can decode syntactically and then be rejected by Complete; output has an
encoder, not an invented public decoder/resume API.

The adapter's separate ledger records claims, completions, receipts, sink
attempts/returns/successes and discards, with u128 received sums
(`adapter.rs:176–307`). Scripted acceptance/effect expectations and inertness are
checked independently at 309–350. Duplicate/stale/wrong-ID acknowledgement,
overlapping delivery, sink-error discard, lost-worker Finish, each independent
counter overflow and exact-limit non-overflow have explicit controls (394–570).
The worker overwrite sequence at 647–690 pins internal stops
Cancelled -> Worker -> Deadline while final termination remains WorkerError;
this is correctly not a monotone historical stop-priority fold. The sink case
also distinguishes internal Worker stop from authoritative CallbackError.

The fabricated-loss control is substantive. After Claim50, Claim51,
Complete50(ModelError), Receive50, Ack50, WorkerFailed(false), the independent
ledger has one missing receipt and **no loss event**. Accounting is already
incomplete, so merely comparing accounting completeness cannot catch fabrication.
The new `loss_matches` comparison at 190–192 and its use at 275–279 require exact
loss-history equality. Finish admission uses `claims == receipts || ledger.lost`
at 214–217, not `core.lost_worker` or the core's disposition. At 741–755, setting
only core.lost_worker=true leaves original core state properties satisfied and
lets core Finish accept, while the independent ledger rejects both the false
loss identity and Finish. The real WorkerFailed(true) positive control then
enables Finish at 757–766. This resolves the reported oracle hole for the owned
adapter in source; the unchanged original BFS/fuzz ledger is not falsely claimed
to contain the new equality.

The original self-model supplies BFS, independent event accounting, stale ack
and false-rejection controls, shrink/record/read/replay, strict healthy build
rejection, relaxed healthy divergence and healthy replay
(`src/campaign/lifecycle/tests.rs:174–388,462–681,963–1189`). The new adapter adds
actual faulted fuzz over duplicate Deliver at 573–644. These test paths remain
distinct: the new fuzz wrapper uses the core effects property; it is not itself
the new history-ledger wrapper. No execution or trace receipt exists in this
review. A no-environment return from the persisted replay test at 1147–1150
must not be reported as fresh-process witness replay.

## Packaging and evidence gates

`Cargo.toml:19–21` now includes the exact public fixture, golden comparator and
schema. These cover the relative Rust source import at
`tests/native_campaign_reference.rs:1` and runtime Python script at 37–43;
the comparator imports only Python stdlib. Inventory/author/private-adapter files
are not dependencies of that packaged integration test. Thus the reported missing
fixture dependency is resolved in source. Actual package membership, package
hash, Python3 availability and packaged-test execution still need receipts.
The three-entry manifest change is the only reviewed root Cargo delta; production
Rust source hashes still match the base. It is outside the immutable r1 manifest.

The recorded show receipts have status queued, started/finished/exit_code null.
They are historical observations, not freshly queried scheduler state:

| Purpose | Existing job | Recorded command behavior |
| --- | --- | --- |
| Public Rust reference | `6ac33655baf9486895c626676a9740ff` | `cargo run`, pending |
| Private source tests | `5ea2d04832ab4cc0acb08d05e8e1384c` | `cargo test --lib ... --no-run`, pending |
| Public/baseline campaign tests | `c47fa958741f4502beac1404b613ad1d` | `cargo test --test campaign --test native_campaign_reference ... --no-run`, pending |
| Independent 49-row materialization | `1be30f3a8bea47758dd063d93a8970cb` | Python author, pending; not a runtime test |

Exact cwd/argv/resource/environment/log paths are retained in
`validation/native-parity/n06/rust-r1/README.md`, its submit/show JSON records,
and `rust-goldens-r1/README.md`, `submit.json`, `show.json`. The Rust snapshot has
135 manifest entries. Its adapter hash is
`ff39cccda7f081c220c8c915e600f2318daacc526efc7287865fc8c27e65bcc2`;
the current strengthened adapter is
`47ff5f466092ea02d9bfc7dc2e61931b69a5fbb3170c60f7634abd6e22cf07bb`.
The inspected diff contains only the loss-equality method/use and new corruption
control. Do not attribute that delta, or the new Cargo inclusion, to r1 execution.
The five live author inputs match the frozen author manifest. At review,
`rust-goldens-r1/artifacts/expected.jsonl` and `FREEZE.json` do not exist.

Required root-owned gates, without duplicating live work:

1. Reconcile the four existing jobs and preserve terminal results/full logs,
   including failures. Materialize and validate the independent 49-row seal;
   compare exact parsed output without native normalization. The public Rust run
   directly compares 19 callback rows and asserts its seed/validation vectors;
   the installed comparison must retain the complete 49-row inventory.
2. Compile the final source snapshot including the post-r1 adapter and root
   manifest change. For each compile-only job, identify and hash the exact emitted
   test binaries, then submit actual execution through shared-default Kyoto.
   Run original/private and public tests plus the new loss/false-rejection/faulted
   fuzz controls; compilation or queue admission is not their pass.
3. Export original/minimized fault traces create-new and execute the supplied
   minimized witness in a fresh process, with source/binary/trace identities and
   healthy strict/relaxed controls preserved.
4. Reconcile the native dependencies above, then qualify fresh installed packages
   with actual worker callbacks, ownership/isolation, rendezvous/backpressure,
   out-of-order delivery, stop/cancel/deadline, worker failure and bounded drain.
   Preserve baseline regressions and every failed aggregate attempt.
5. Keep shared count intersection separate from wider host-only/unsupported
   domains. `scripts/native-campaign-support/domains.py:5–7,20–37,41–62` retains
   full-u64 IDs/seeds/transition limits and marks wider count operands unsupported;
   it does not clamp them. The 49 shared rows do not prove Rust's full usize/u64
   count domain in Go/Swift Int or TypeScript safe numbers. Wider valid Rust codec
   states are not corrupt merely because a native host cannot represent them.

N05 remains materialized78/reviewed author90, final qualification pending on
`36c3552df326429fb9e76d872c80aafa`; this review neither inspected another tree nor
restarted that work. No N05 FREEZE/90-case pass, N06 completion, N10 assurance,
generic CampaignReport merge, platform/profile/default/144-cell promotion,
durable delivery/resume, preemption, hard RSS or fatal-trap recovery is claimed.

## Source identity and method

SHA-256 of the reviewed files (repository-relative). These bind source review,
not compilation or executed behavior. A separate read-only subsidiary review of
schema, derivation and projections agreed with the dispositions above; its
additional native differences were checked against the cited source here.

```text
4598c1c167f1cbe72c38ef165c0b4e1bfec5aa57ed65ef23d70185286b1627fc Cargo.toml
4d4da9479fe3512589df379cba9a1f3e5df989bba6920e3691479e6b3ef19005 src/campaign.rs
7931c3ef82bf7f2b806448ef8a65f8f2e4a136d38c58d8e06307ee4b090ef44a src/campaign/lifecycle.rs
a93ee1cf524339743564923dacd6d8aac007e95d9ecd68e00f5f4e08de8b579d src/campaign/lifecycle/codec.rs
3102ec704a8757f339d558e93faf98c82499076ffc94d8177551aba2af582b3a src/campaign/lifecycle/tests.rs
bbc7b214510f862738991074f8f2053389c4cac85caf005ebc129df9b06a18d7 src/guided.rs
496c19c7181a6bf3f2af97d3e04df1d3c7b22c241351104b4cd66201f6706ecb src/explore.rs
e69a80ad96826a27871807f848e22fd213a7bee9f95d76be92069e68504f5beb src/model.rs
ec19cf028645dd16f6f389cc7fa0fa3a44a72a02838842a906ca11bb2303649b src/operations.rs
6c232b3961e4c4ed52d49f55ed1182f22be9080e7c1bbc37b7445bdd370c2082 docs/native-parity/N06-RUST-CONTRACT.md
8d990b699cbd2bf6dfe812f3c13c9b416372a9d6e99aeebc650f6c3c8867a5d5 docs/native-parity/N06-CONTRACT-REVIEW.md
fdcb53eb2cccf0a7eff89ae02e92232496687797df1e105a7e57cf1395737829 docs/native-parity/N06-INTEGRATION.md
a12d67480d8a3ad79609a231b9513217e2d089805e20b5973dc77bac8c4081a3 docs/native-parity/N06-REVIEW-CHECKLIST.md
532bb5736cac7a9eb65b5b1f442bb61ba53f5ae7dbb2b8544950411b6231c2a8 conformance/fixtures/native-campaign-public.rs
dc1d4e75b9d99dc790a530bfddb742c1acefdf9226401db7b7a5659372c75e76 conformance/fixtures/native-campaign-golden.py
79b9faf376439dd2945a1df021b51e16c7b504882c32ccc927e712ee00cf7409 conformance/fixtures/native-campaign-inventory.py
4e6a4993b5fb06b141465fd29333bf08ea39ad85237fddb9f404c3165cfc9c6b conformance/fixtures/native-campaign-author.py
59138f9b05ecbad86fd4bd559ce3d6e44d70a23bf6b6e9f7e354525b3a462d9f conformance/fixtures/native-campaign-v1.schema.json
47ff5f466092ea02d9bfc7dc2e61931b69a5fbb3170c60f7634abd6e22cf07bb conformance/fixtures/native-campaign-rust/adapter.rs
8a28f370ca38baa177618ae20f650ea267cb1360d393203d2768f9dd2ca5897b conformance/fixtures/native-campaign-rust/build.rs
7a555271c7ae5f14d6169d720dda270897bb6d762aab0590a15a345d20eaef9f conformance/fixtures/native-campaign-rust/lib.rs
49b64ecae6c0b30cb089e687423c11f0f391eb719afa0fce575fb7f19cb5260c tests/native_campaign_reference.rs
b9d4dcdcfc003c046e505ac1de94b4adad2fb37ec34c951f33ee24298317cc18 validation/native-parity/n06/rust-r1/source-manifest.json
31fc562b38c012eb6712a81dea1aebbbfef5e8a2ce9d75d4c51bcae22e4271d4 validation/native-parity/n06/rust-goldens-r1/source-manifest.json
4e1199c1fee2d705e0e7695073d44193c2ef6b844064809964afd824cf40e088 validation/native-parity/n06/rust-handoff.md
53c8b703dcaea43f1298c95c617705f93616f6b172ff6a550889516c663ed19b examples/native-campaign/swift/Sources/CampaignConsumer/Projection.swift
854e31bd74a6cb9f511ad5c89a0f93be2644fffe674d7deb331527c797180359 scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/Projection.swift
226717631736d56dca3612c379aea34cc49a5da6fb876f881ac0de0a52c9320c scripts/native-campaign-consumers/swift/Sources/CampaignConsumer/Deterministic.swift
2b24f29eb736813403e0d431dd3fbbcf8ba7cdc965b0eb721de1fd3edbe047c4 scripts/native-campaign-consumers/go/wire.go
d9a6340a39f349177675bd09badd94520ec84f16b53fdc24566d91be9b05bab4 scripts/native-campaign-consumers/typescript/projection.ts
1972fd12455c8dacefb8044bc28ff2e540a7786ff5331924ddf9feb73e9d5acd scripts/native-campaign-consumers/typescript/deterministic.ts
41a025ed7d3da1d8453dfccfe9a925d864a892154ea7e821fe79b21af05dbca4 scripts/native-campaign-consumers/python/consumer.py
4cef4f85ff012660764b473f4e8f30ca2df1384f51932af175d090cf58c92fb6 python/src/statelessness/search.py
d3f49de2cda63b0b6c4031809b74235b773ee59ad9a65be7fc72029e83edf397 python/src/statelessness/core.py
87f69f8144004490a913230c0b34c676a5d1e3e7b491aba184e9614671237c46 swift/Sources/Statelessness/Guided.swift
3c00084776a8d530b3edca6d90dcac839d422880b8ba996cda7ebc7d9db6270c swift/Sources/Statelessness/Model.swift
3fd338b59ddc1c7f4b66db003b9996caf458b3635f90247c3e2be960e1ff52ea swift/Sources/Statelessness/Campaign.swift
082d2967d52fff1238221520682dfbed59b4633c58c4ef10555d4a4561fbcb0d swift/Sources/Statelessness/CampaignRuntime.swift
1e758da07c07a1b8dfb6600b2207fd09d0295cd5b15368ad891840d422b17f74 swift/Sources/Statelessness/CheckSink.swift
d8597fe3ba80cfebce4629e1bda04d171c6b64afdb821c86db377d3115cb515a typescript/src/guided_sync.ts
e15997ee24338e2f0c7c78ebd14c29275086313b467a6d135090632a70b9ec82 typescript/src/guided.ts
516b83703c75c93ea4f7c4b0674eb1855374eada424b7f3496d20c5ac25cc6d8 typescript/src/campaign.ts
f0ad6fbd97f3e5730327b2d43bfbee26fa5348dc306bb1028aed4bb962f91403 typescript/src/search_support.ts
cb5eef46acefbb7f2da6b1bab20f8442ee2148277bc626d93f90a4d5ac5c0513 go/fuzz_kernel.go
1dec7eafc33c35f72a6c39d33d04eab0ef64a97af288f34dcd554375244bce75 scripts/native-campaign-support/domains.py
```

Method: `pwd`, `git branch --show-current`, `git rev-parse HEAD`,
`git status --short`, `git worktree list`, scoped `rg --files`/`rg -n`,
`cat`, `nl -ba FILE | sed -n 'START,ENDp'`, `wc -l`, `ls -ld`,
`shasum -a 256 FILE...`, `git diff -- Cargo.toml`, and the explicit read-only
diff of r1/current adapter. N06 plan, original contract review, integration and
checklist were read; GUIDED-CAMPAIGN-AUDIT.md and GUIDED-CAMPAIGN-REVIEW.md were
read in full. No reviewer command ran project code or supplied execution evidence.
Final read-only checks found no whitespace errors in this document and confirmed
unchanged HEAD, Cargo inclusion, frozen author/contract/schema/public-fixture
hashes, strengthened adapter and public-reference test identities.
