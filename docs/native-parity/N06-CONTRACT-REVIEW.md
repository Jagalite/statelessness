# N06 independent contract review — round 1

Source-only review on 2026-10-22, confined to
`/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336`, branch
`t3/native-campaign-runtime`, HEAD
`0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`.
Only this document is written by this reviewer. No builds, tests, qualification,
Kyoto submission, staging or commits were performed. The Kyoto skill was read.
The full guided/campaign audit and review were read, followed by the N06 plan,
contract/checklist and actual Rust campaign, lifecycle, codec, self-model tests,
public tests, guided kernel, RNG and native operations.

The contract is broadly faithful to the Rust decision law. The findings below
concern the available contract and reference/consumer snapshots, which are being
authored concurrently. They are not a final review of the native runtimes.

## Findings before freezing the golden protocol

1. **P2 — The public projections already disagree on JSON structure and types.**
   `conformance/fixtures/native-campaign-public.rs:29,39,51-54` emits string input
   values, string mutation percentage, a `fuzz.seed` field, and flattened failure
   violations with a debug-formatted status such as
   `Failed("second transition")`. In contrast,
   `scripts/native-campaign-consumers/go/wire.go:47-52,96-141` emits numeric
   mutation percentage and input values, omits `fuzz.seed`, and nests
   `{id,status,message}` under `check`. TypeScript's
   `scripts/native-campaign-consumers/typescript/projection.ts:16-21,33-71`
   agrees with Rust on mutation percentage but with Go on input values, failure
   structure and omission of `fuzz.seed`. The available Swift projection also
   uses numeric inputs, nested checks and no `fuzz.seed`
   (`examples/native-campaign/swift/Sources/CampaignConsumer/Projection.swift:57-91`).
   Thus even an ordinary passing job cannot compare exactly across these current
   adapters; `property-stop` and `guided-basic` have additional differences.
   `N06-RUST-CONTRACT.md:10-33` supplies only a passing example and leaves the
   non-null failure/guidance and smaller integer representations underspecified.
   **Fix:** publish one complete field/type schema, including application input
   representation, u8/u32 fields, failure phase/check/message, and whether fuzz
   duplicates the job seed. Reconcile adapters before independently sealing
   expectations; do not normalize away these differences in the comparator.
   Also reconcile the promised validation callback counters and caller/spawned
   role (`N06-RUST-CONTRACT.md:21-29`) with their absence from the current Rust
   row constructors (`native-campaign-public.rs:160,184-187`).

2. **P2 — The admission instruction is one-way and cannot detect false rejection.**
   `N06-RUST-CONTRACT.md:113-114` says to assert that every *accepted* event was
   admissible, then claims this detects an incorrect inert rejection. A reducer
   that rejects an enabled initial Claim without changing anything satisfies
   that implication. The actual Rust rule is `accepted == admissible`, calculated
   from the predecessor (`src/campaign/lifecycle.rs:450-469,505`), and
   `src/campaign/lifecycle/tests.rs:462-521` injects precisely this false rejection.
   **Fix:** state the biconditional explicitly and require both directions in the
   independent fixture. The checklist's predecessor-based intent at
   `N06-REVIEW-CHECKLIST.md:79-83` is sound.

3. **P2 — Qualify which metadata panic clears metadata.**
   `N06-RUST-CONTRACT.md:91-93` says a caught metadata panic leaves metadata null.
   That is true for **model** metadata. In a guided job, **feedback** metadata
   executes inside the runner after model metadata has already been stored
   (`src/campaign.rs:338-349,450-453`; `src/guided.rs:203-218`). A recoverable
   feedback-metadata panic therefore yields `Panicked` with model metadata
   **Some**, no FuzzReport, and incomplete accounting when received. The current
   public fixture has only model-metadata panic mode
   (`native-campaign-public.rs:67,82-84,110,139`).
   **Fix:** distinguish the two callbacks in the contract and add independently
   authored paired expectations. This requires no change to N05 production code.

4. **P2 — The golden assertion contradicts decoded-JSON comparison.**
   `tests/native_campaign_reference.rs:16-20` compares serialized JSONL strings,
   while `N06-RUST-CONTRACT.md:14-15` explicitly makes key order and whitespace
   insignificant. Reordering keys in independently authored expected rows would
   fail this test without any semantic difference.
   **Fix:** compare strict parsed JSON values, preserving row/array order and
   string-versus-number distinctions. Keep expected-file byte hashes separately
   for provenance. Do not regenerate expected bytes from the Rust serializer to
   make a textual comparison pass.

## Affirmable Rust source facts

- Admission order and wide-product-before-cap are exactly
  `src/campaign.rs:55-85`; guided validation comes first at 449. Job seeds use
  wrapping master+ID followed by one SplitMix64 output (91-93;
  `src/model.rs:486-510`). The seed fixture's wrapping-equivalence pairs and its
  capped-wide-product validation case agree structurally with these laws
  (`native-campaign-public.rs:116-124,176-181`); no numeric vectors were executed.
- Factory Err is FactoryError/None; feedback-factory Err is ModelError/Some.
  Model destruction stays inside the unwind boundary. Factories returning
  successfully after cancellation still reach metadata and the guided feedback
  factory before fuzz preflight (`src/campaign.rs:338-349,450-453`;
  `src/guided.rs:199-229`; `src/explore.rs:738-756`).
- Configured workers=1 selects the caller; configured >1/jobs=1 selects a spawned
  worker. Complete precedes send; receipt releases the sender independently of
  SinkReturned; the sink is outside the coordinator lock. Sink failure drains
  before joining (`src/campaign.rs:359-384,480-537`). The new rendezvous test
  has a causal handshake for reclaim-before-sink-return and out-of-order receipt
  (`tests/native_campaign_reference.rs:72-95`), not just an elapsed-time assertion.
- Job errors do not trigger stop_on_failure. Receive accounts before invoking
  the sink; FactoryError retains complete accounting, ModelError/Panicked do not.
  Each aggregate work counter saturates independently. WorkerError permits
  delivery; CallbackError causes discard and survives subsequent worker errors
  (`src/campaign/lifecycle.rs:194-301`). Late Claim rejects before observing its
  supplied stop (175-185).
- Codec tags, field order, strict UTF-8, ascending pending IDs, count guards and
  semantic state validation match `N06-RUST-CONTRACT.md:63-76`
  (`src/campaign/lifecycle/codec.rs:7-235,244-335`). Inconsistent Summary input
  is syntactically decodable and is rejected by Complete. Output has canonical
  **encoding**; Rust's test codec does not expose an output decoder.
- The three-event duplicate-Deliver witness and strict/relaxed healthy replay
  expectations match the authored Rust tests
  (`src/campaign/lifecycle/tests.rs:963-1118`). The independent ledger detects
  repeated effects (224-242); the fault wrapper itself uses core State, while
  ledger checking is a separate fixture concern. Neither is an execution result.

## Important vector and review boundaries

Golden state projections must distinguish `state.stop` from final termination.
For example, after two claims, Complete(50,Cancelled), WorkerFailed("w",false),
Complete(51,Deadline), the successive stop values are Cancelled, Worker, Deadline,
while termination remains WorkerError("w") after the worker event. WorkerFailed
unconditionally writes Worker; Complete(Deadline) only guards against the
*current* Cancelled stop (`src/campaign/lifecycle.rs:204-211,271-280`). A monotone
priority fold over all prior stops would produce wrong private state bytes even
though final termination agrees. Include this interleaving and sink-error/worker
interleavings in the independently authored lifecycle steps.

At the reviewed snapshot, `tests/native_campaign_reference.rs:17` referred to
`conformance/fixtures/native-campaign-expected.jsonl`, which was not present in
the file inventory. No complete independent golden file or private N06 adapter
was available for this round. The contract itself labels them in progress
(129-133). This is a review limit, not a failed build or a request to wait for
authors. Later golden and runtime changes require separate source-bound review.

N05 remains materialized78/reviewed author90 with final qualification pending on
`author36c3552df326429fb9e76d872c80aafa`; no FREEZE or 90-case pass is inferred.
No N05 core change is requested by the Rust contract findings above. N10,
generic CampaignReport merging, platform promotion and execution receipts remain
outside this review.

## Supporting prospective architecture review

The delegated source-only pass completed after the contract review. Its findings
were reconciled once against the then-current source, without waiting for authors
or executing any code. This addendum is still round 1; it does not qualify or
constitute a complete review of the finished runtimes.

5. **P2 — Python startup setup can escape before draining/joining earlier workers.**
   The original snapshot placed `context.Pipe()` and `context.Process(...)`
   outside the startup handler (`python/src/statelessness/campaign.py:399-410`).
   The issue remains at **413-416** in the reconciled snapshot: only
   `process.start()` is caught. A later Pipe/Process construction failure can
   leave earlier workers and bridges running or blocked, bypassing drain/join at
   **442-461**. `threading.Thread(...)` construction is likewise outside its
   handler, after incrementing the active count (**426-431**).
   **Fix:** contain the whole per-worker setup, track partial ownership, record
   startup failure without fabricating a job report, and guarantee cleanup plus
   drain/join of all previously started workers. Include later-worker setup
   failure, not only a failing `Process.start()`, in the fault witnesses.

6. **P2 — TypeScript acknowledgement still rereads a sink-mutable job ID.**
   The original snapshot acknowledged `job.jobId` after the sink and ignored
   lifecycle rejection, permitting a false successful aggregate. The reconciled
   `typescript/src/campaign.ts:176-180,195-203` now checks lifecycle acceptance,
   so that specific silent-success symptom is addressed. However, **200** still
   rereads `job.jobId` after passing the same mutable object to the sink at 199;
   `executeCampaignJob` returns an unfrozen object at 168. A JavaScript sink can
   change the ID and then return or throw, causing an internal acknowledgement
   error instead of the required delivered count or CallbackError aggregate.
   **Fix:** capture the original protocol ID before invoking application code,
   and retain the new acceptance guards. Rust already captures `job_id` before
   transferring the report to the sink (`src/campaign.rs:363,373-382`).

Two earlier observations are **addressed in source**, with execution unverified:

- **Python discard/decode ordering:** original `campaign.py:433-439` decoded
  before Receive, and a decoder failure could omit a completed report from drain
  accounting after a sink failure. The reconciled source dispatches Receive at
  **268**, returns immediately on Discard at **269-270**, and decodes only for a
  live delivery at **273-278**; `_run_processes` delegates the raw report at
  **442-449**. This addresses the reported scenario.
- **Go diagnostic precedence:** original `go/campaign.go:282-294` allowed a
  returned error's panicking `Error()` method to replace the original sink
  failure. Reconciled `campaignOwnedError` at **411-423** directly copies
  ModelError or guards the formatter with the stable fallback
  `error diagnostic unavailable`. The sink uses it at **391-392**. This
  addresses the reported diagnostic scenario without claiming fatal recovery.

Affirmable architectural choices from the delegated snapshot: Python explicitly
requires spawnable factories and application-defined input transfer
(`N06-PYTHON.md:16-26`); Go uses worker-local construction, bounded channels and
input detachment before model cleanup (`N06-GO.md:28-34,60-74`). Swift declares
Sendable factories/report inputs without requiring the created model or feedback
to be Sendable (`swift/Sources/Statelessness/Campaign.swift:121-145`); its channel
and pthread runtime implement completion-before-send and drain-before-join
(`CampaignRuntime.swift:96-131,151-168,238-275` in the delegated snapshot).
TypeScript's browser-safe entry explicitly requires a host adapter for workers>1.
Swift/TypeScript architecture documents were absent at that pass's snapshot;
this is a scope observation, not a missing-code defect. None of these choices
establishes runtime/platform qualification or universal callback recovery.

Original delegated snapshot SHA-256 (retained even where superseded):

```text
becae4a7e78cb5f2dee25799accb178df6270b80e6227227fc1a1c491b7dfa56 python/src/statelessness/campaign.py
122243c6c10dd48758a1d021b18354d1943408f38b26f7f916d7e2119ab07743 python/src/statelessness/_campaign_lifecycle.py
9f328446d6dedc2a13bb676c6090dd24c05864d4fdc361ded63f4234cc2cc536 typescript/src/campaign.ts
a49c3bcfa2af11c60286dada6c6257a14a4dccdb6aaab39eb9ccbdf7e135e272 typescript/src/campaign_lifecycle.ts
ca3623a8b14fac01154a07a14933acc5603831dba21976ee42fee003168b7d88 go/campaign.go
3fd338b59ddc1c7f4b66db003b9996caf458b3635f90247c3e2be960e1ff52ea swift/Sources/Statelessness/Campaign.swift
7ddade2f9efec61af426de65a64fb10ac40d8a4b6a08ce99f68974d9bc4b3c2d swift/Sources/Statelessness/CampaignRuntime.swift
d4dd2f20f93f69c1edff52759be4d6425b04cd25bb2bea4bcbf281b0ccc4b149 docs/native-parity/N06-PYTHON.md
23eb5afb574cc382b47373c5d99dea8ce7c406548e5565d2720c7e557e56f638 docs/native-parity/N06-GO.md
```

Reconciled finding-source SHA-256:

```text
b6159e33e7a8c2d194ad0c2e86703f5e31ddb45aa0fc419e2e3c279f363e5c63 python/src/statelessness/campaign.py
ea62d66304898a27a68055551943566cdddd4921edcb094b54c4a1142efbb51a typescript/src/campaign.ts
0e5673c9fb63f5eaea311b36ad0b6b618392cca5f5670fd699bfd943a784cc37 typescript/src/campaign_lifecycle.ts
f040e5b634cecbceb00322c97122e6145f071a375fba5e69999df7c65b84f4bd go/campaign.go
```

## Source identity

SHA-256 of reviewed files; the mutable fixture/projection observations apply only
to these snapshots. Paths are repository-relative. The Rust authority files are
unchanged from the required base.

```text
8d9e0fa19796931a2b85368092120e692e9e0f50788b9f36b2f22aa463ab7301 docs/NATIVE-PARITY-PLAN.md
0e21253da3acf41ebc406d8b8224d4572519a42c3b5f66fd9b7c63747fa4fbb3 docs/native-parity/GUIDED-CAMPAIGN-AUDIT.md
6343d7714e1d06e95527eb78ff072857f774bec2e885f91712bdc31a48650360 docs/native-parity/GUIDED-CAMPAIGN-REVIEW.md
a107d67a32d6317fa770013e31f00c98198aa4b97c0ec4a64d9db506b685a9a1 docs/native-parity/N06-RUST-CONTRACT.md
a12d67480d8a3ad79609a231b9513217e2d089805e20b5973dc77bac8c4081a3 docs/native-parity/N06-REVIEW-CHECKLIST.md
4d4da9479fe3512589df379cba9a1f3e5df989bba6920e3691479e6b3ef19005 src/campaign.rs
7931c3ef82bf7f2b806448ef8a65f8f2e4a136d38c58d8e06307ee4b090ef44a src/campaign/lifecycle.rs
a93ee1cf524339743564923dacd6d8aac007e95d9ecd68e00f5f4e08de8b579d src/campaign/lifecycle/codec.rs
3102ec704a8757f339d558e93faf98c82499076ffc94d8177551aba2af582b3a src/campaign/lifecycle/tests.rs
bbc7b214510f862738991074f8f2053389c4cac85caf005ebc129df9b06a18d7 src/guided.rs
496c19c7181a6bf3f2af97d3e04df1d3c7b22c241351104b4cd66201f6706ecb src/explore.rs
e69a80ad96826a27871807f848e22fd213a7bee9f95d76be92069e68504f5beb src/model.rs
ec19cf028645dd16f6f389cc7fa0fa3a44a72a02838842a906ca11bb2303649b src/operations.rs
4ef4844eb77de0f120acbeaefdaea372a8e08caf7fc3466d0ccee10452aca449 tests/campaign.rs
0c29e8296b3b141dd4fc263da11f476f80d0661812eb8c87fc67089f8c8fbc86 conformance/fixtures/native-campaign-public.rs
2c37a235f7dcb4cecbe817c4c29f34e656030df5da32681e58ed471fc7e23074 tests/native_campaign_reference.rs
3c5b028be3083938df48ef4ce10f71570e42fecc027085d8c3674def7e15e84f scripts/native-campaign-consumers/go/wire.go
d733fbc6fefb3a9b26fa05e8baaba9a357cb5be9ecae7f5718c646bc94040a0c scripts/native-campaign-consumers/typescript/projection.ts
0ab2f0970ba24d7c68589feef5c4838dbae808011a69e7f823e10ceb3b72db3a examples/native-campaign/swift/Sources/CampaignConsumer/Projection.swift
```

Read-only method: `git status --short`, `git branch --show-current`,
`git rev-parse HEAD`, `git worktree list`, scoped `rg --files`/`rg -n`,
`nl -ba FILE | sed -n 'START,ENDp'`, `cat`, `wc -l`, and
`shasum -a 256 FILE...`. These are source inspection, not runtime qualification.
