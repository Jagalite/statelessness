# N06 Rust reference protocol v1

Source-derived contract, authored before reference execution on 2026-10-22.
Base: `0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`. This document is the early
adapter interface for the N06 language agents. Runtime receipts are separate;
unexecuted expectations are not passes. No N05 corpus or production Rust changes.

**Complete field/type authority:**
[`native-campaign-v1.schema.json`](../../conformance/fixtures/native-campaign-v1.schema.json).
The schema forbids extra fields and describes every public and lifecycle object,
including non-null failures/guidance, nullable fields and canonical byte hex.
It is a finite fixture projection, not a product serialization API. Semantic
u64/u32/u8/host-count range validation remains the actual API's responsibility;
the decimal-string grammar is not permission to clamp an unrepresentable count.
All shared count operands fit `2^53-1`. Full u64 IDs/seeds/transition bounds remain
required. Wider usize admission belongs to distinct host-only controls.

## Portable JSON projection

One JSON object per line, UTF-8, schema `native-campaign-v1`. Every u64 and
usize value is a **decimal string**, including job IDs, seeds and work counts;
booleans remain JSON booleans. Arrays retain observed order. Missing metadata,
failure, guidance or error is explicit `null`. Rust identifiers below are exact
variant strings. JSON object key order is not significant. Compare decoded JSON,
not whitespace. Never sort the delivery list to hide delivery-order differences.

```json
{"schema":"native-campaign-v1","case":"ordinary-basic","kind":"campaign","jobs":[{"job_id":"7","seed":"7191089600892374487","metadata":{"name":"n06-counter:7","model_version":"1","properties_version":"1","codec_version":"1","build":"n06-counter-v1"},"outcome":"Completed","error":null,"fuzz":{"termination":"CasesCompleted","cases":"1","transitions":"2","skipped_checks":"3","failure":null,"guidance":null}}],"report":{"requested":"1","started":"1","finished":"1","not_started":"0","delivered":"1","failures":"0","errors":"0","reported_transitions":"2","reported_cases":"1","reported_skipped_checks":"3","accounting_complete":true,"termination":"JobsCompleted","error":null}}
```

The example seed is `job_seed(0,7)`; the compact illustration omits fields.
The canonical full rows are defined in
[`native-campaign-public.rs`](../../conformance/fixtures/native-campaign-public.rs).
The independent expected-row author is
[`native-campaign-golden.py`](../../conformance/fixtures/native-campaign-golden.py),
which imports only Python's JSON serializer and does not inspect native output.
The public consumer includes config, fuzz seed, ordered jobs and `callbacks`.
Generated inputs are `ID:generate:STATE:INPUT`; applied inputs are
`ID:step:STATE:INPUT`. There are no redundant generated/applied arrays or thread
IDs in deterministic rows. Caller/spawned roles are separate host assertions.
All integer JSON fields, including u8 `mutation_percent`, are decimal strings.
Application inputs in this fixture are small unsigned integers. Each job collects
its own identity and callbacks before any deterministic comparison by job ID.
This collection is test data; Rust exports no generic `CampaignReport` merge.

Seed rows have `kind:"seed"`, `master_seed`, `job_id`, `seed`. Validation rows
have `kind:"validation"`, a full `config` and `error`. Rejected public entry tests
panic if factories/sink are called; no callback-count fields are serialized.
Zero-guidance precedence is a separate test, not an extra validation row.
Shared aggregate validation operands use JS-safe maximum (`9007199254740991`)
so every native host can pass them to real public APIs: jobs2049/casesMAX for case
overflow; jobs2049/cases1/stepsMAX/capU64MAX for transition overflow; jobs2/casesMAX/
stepsMAX/cap1 for wide cap-before-aggregate acceptance; first_job0/jobsMAX/cases1/
steps1/cap1 for the valid count edge. Rust's wider usize maximum is a separate
host-only assertion, not a required shared-row input.
Private lifecycle rows have `kind:"lifecycle"`, `config`, `events`, `steps`.
Each step contains `accepted`, `reason` (null on accept), ordered `effects` and
the full `state` below. All projections derive from actual runtime callbacks or
the actual private reducer; no native implementation output authors expectations.

## Private lifecycle adapter

This is a test-only adapter, not a public scheduler/checkpoint/resume API.
Events and effects use externally tagged objects:

```json
{"Claim":{"stop":null}}
{"Complete":{"job_id":"50","summary":{"outcome":"Completed","failure":false,"transitions":"2","cases":"1","skipped_checks":"1"}}}
{"Receive":{"job_id":"50"}}
{"SinkReturned":{"job_id":"50","error":null}}
{"WorkerFailed":{"error":"lost","lost_work":true}}
{"Finish":{}}
```

Claim stop is null, `Cancelled`, or `Deadline`. Summary outcome is `Completed`,
`Failure`, `Cancelled`, `Deadline`, `FactoryError`, `ModelError`, or `Panicked`.
Effects are `{"RunJob":"50"}`, `{"Deliver":"50"}`, `{"Discard":"50"}`.
State fields: `report` as above, `stop` null/Failure/Cancelled/Deadline/Sink/Worker,
`pending` sorted by numeric ID as `[{"job_id":"50","status":"Running",
"summary":null}]` (or Ready with summary), `delivering` ID/null, `lost_worker`
boolean, `finalized` boolean. Sorting pending reflects the private BTreeMap;
it does not sort delivery effects or host receipts.

The adapter must execute source-bound `Lifecycle::step`/`step_owned`, its actual
codec, and separately collect event/effect ledger facts. A byte-for-byte frozen
copy is acceptable only with source hashes and an explicit private-fixture label;
changing production visibility or production behavior is outside this task.

Canonical codec: every integer is u64 little-endian; flags/tags are one byte;
strings are u64 byte-length followed by strict UTF-8. Inputs: Claim tag0 + stop
0/1/2; Complete tag1 + ID + summary; Receive tag2 + ID; SinkReturned tag3 + ID
+ optional error tag0/1 and string; WorkerFailed tag4 + string + lost flag;
Finish tag5. Summary: outcome tag0..6 in the order above, failure bool,
transitions/cases/skipped_checks. Outputs: RunJob0/Deliver1/Discard2 + ID.
State: seven report counts in report field order through errors, three reported
u64 counters, accounting flag, termination tag0..5 (JobsCompleted, FailureFound,
Cancelled, Deadline, CallbackError+string, WorkerError+string), stop tag0..5,
pending count and ascending (ID, Running0 or Ready1+summary), optional delivering
tag0/1(+ID), lost_worker flag, finalized flag. Reject truncation, trailing bytes,
unknown tags/flags, invalid UTF-8, unsorted/duplicate pending IDs, invalid state
properties and hostile counts before allocation. Input decode permits syntactically
valid inconsistent summaries; Complete then rejects `inconsistent job summary`.

## Expected decision and callback rules

- Validation: jobs>0; workers1..256; buffer<=1024; mutation<=100; last ID fits
  u64; aggregate cases fits; aggregate min(cases*steps,max_transitions) fits.
  Multiply cases*steps wide before cap. Guided validates all four positive
  guidance limits **before** campaign validation. Invalid config beats controls.
- `job_seed(master,id)` is SplitMix64.next of wrapping `(master+id)`, with wrapping
  increment/multiplications. Max ID with jobs1 is valid; max ID with jobs2 is not.
- Pre-cancel/zero deadline claims nothing and calls no factories/metadata/sink.
  Factory cancellation still proceeds through model metadata, feedback factory,
  feedback metadata, then fuzz preflight. Model-factory Err: FactoryError/null
  metadata/known-zero work. Feedback-factory Err: ModelError/Some metadata/lost
  accounting. Job errors never themselves trigger stop_on_failure.
- Model teardown occurs inside the job unwind boundary before delivery. A caught
  **model** metadata panic leaves metadata null; **feedback** metadata panic
  occurs inside the runner and retains the already stored model metadata.
  Both produce Panicked/no FuzzReport/incomplete accounting. Runner/destructor
  panic also retains model metadata.
  Feedback teardown precedes model teardown. No fatal abort/trap guarantee.
- Complete occurs before blocking send. Receive removes pending and accounts a
  report before sink return. A successful send frees that worker to claim while
  the caller is still in sink. Outstanding <= queue+actual_workers+1; this is not
  cumulative-start or RSS accounting. Configured workers1 uses caller; >1/jobs1
  spawns. Buffer0 is rendezvous. Real barrier tests remain a separate proof class.
- Duplicate/stale/wrong-ID ack is inert. Rejected Claim cannot observe a later
  control. Accepted completion priority: cancellation > deadline > property stop.
  CallbackError wins final termination over WorkerError and ordinary stops; later
  worker errors replace earlier worker errors. WorkerError alone allows Deliver.
  After sink error receipts emit Discard, no extra sink calls; drain before join.
- Received FactoryError counts error without incomplete accounting; ModelError
  and Panicked lose accounting. Totals saturate and mark incomplete on aggregate
  overflow. Finish permits pending only after lost_worker, never active delivery;
  finalized inputs reject unchanged. Infrastructure errors do not increment errors.

## Independent ledger and fault witness

Maintain sets of claimed IDs, completed summaries, receipts, sink attempts,
returns, successes and discards. Detect duplicate effects even if core counts look
correct; assert `accepted == admissible`, with admissibility derived from the
predecessor ledger independently of the returned disposition. Both directions
are required: an accepted invalid event and an incorrectly rejected enabled event
fail. Sum received summaries in wider arithmetic;
check missing claims/receipts, active sink, disjoint destinations and stop priority.

Deliberate fault: append a second Deliver whenever the actual reducer emits one.
Minimal causal witness with first_job50 is Claim(None), Complete(50,Completed,
failure=false,transitions2,cases1,skipped1), Receive(50). Expected failure target
is transition property `campaign.effects`; independent ledger also fails
`audit.protocol`. BFS must discover it; fuzz must exercise it; add stale-ack/extra
claim noise and shrink to three inputs; record/read/replay must reproduce. Fault
build appends `:duplicate-delivery`; strict healthy replay is Incompatible and
relaxed healthy replay Diverged with failure_reproduced=false. Healthy trace
roundtrip/replay is a distinct control. Neither fixture enables durable queues.

## Qualification status

The public fixture authors 15 seed rows, 15 shared validation rows on a 64-bit
host, and 19 exact ordinary/guided campaign rows. Callback error/panic paths,
factory cancellation, zero budgets, property stop and teardown are represented.
`tests/native_campaign_reference.rs` adds public bare-per-job comparisons for
ordinary/guided workers1/3, true out-of-order rendezvous worker progress while
sink blocks, configured workers2/jobs1, validation precedence and Rust-only
usize bounds. The existing `tests/campaign.rs` remains an independent host
regression suite for bounded sinks, drain, cancellation, deadlines and cleanup.

`conformance/fixtures/native-campaign-rust` is a separate test crate. Its build
script copies four exact campaign source files to OUT_DIR and appends only a
test module declaration inside campaign.rs; it does not change the reducer,
codec or visibility. Existing source codec/self-model tests run there unchanged.
The adapter adds full JSON state/bytes/effects for hostile acknowledgements,
sink precedence, worker loss, and per-counter overflow plus the exact-limit
negative control. It additionally executes faulted fuzz: the existing Rust fault
test covers BFS/shrink/record/read/replay but did not itself invoke faulted fuzz.

No N06 execution result yet. N05 remains reviewed with author90 final
qualification pending on `author36c3552df326429fb9e76d872c80aafa`; FREEZE absent.
Do not restart that work or infer a pass. N10 and profile/144-cell promotion are
outside this contract.

## Round-1 review resolution and harness handoff

Finding 1: the complete schema linked above is authoritative. Inputs and every
integer are decimal strings; fuzz includes seed; violation shape is flat
`{phase,id,status}` with exact status `Failed("second transition")` for the
fixture's sole failing check. This preserves the Rust observation; consumers
must project their actual corresponding check into that explicit finite format.
No extra validation counters, guidance or thread role are serialized. Real
callback assertions and host-role tests cover those behaviors separately.

Finding 2: the requirement is the admission biconditional, including false-
rejection fault controls. The original unchanged Rust self-model has that fault
test, and the N06 adapter also checks an independently maintained ledger.

Finding 3: `metadata-panic` is the **model** callback and yields metadata null.
`feedback-metadata-panic` is guided and yields metadata Some; its callback order
is factory, model metadata, feedback factory, feedback metadata, feedback drop,
model drop, sink. Both expectations are independently authored in Python before
running Rust; the Rust fixture changes no production source.

**Open native dependency:** the Swift owner reports existing N05 Guided.swift
feedback-metadata exception handling maps CampaignPanic to ModelError. Its actual
N06 result is therefore ModelError/Some model metadata/incomplete accounting,
which differs from Rust's Panicked/Some/incomplete row. Retain both observed
classifications and expose the mismatch; do not normalize it into a shared pass,
drop the vector, or quietly change N05 behavior. Native exception policies remain
documented, and resolving this parity dependency belongs to the coordinating root.

Finding 4: `native-campaign-golden.py --compare` parses JSON strictly, rejects
duplicate object keys and non-finite numbers, and recursively compares exact
types/keys/values plus row/array order. Object-key order/whitespace is immaterial;
missing fields, numeric/string changes, boolean/integer confusion and array
reordering fail. Public tests and the queued source-bound reference use it.
Expected source and artifact byte hashes remain separate provenance checks.

Private stop interleaving: Cancelled completion -> WorkerFailed("w",false) ->
Deadline completion yields internal stop Cancelled -> Worker -> Deadline and
final WorkerError("w"). The adapter includes this alongside sink-error ordering.

Harness owners: consume these precise schema/source definitions; do not coerce,
drop fields or regenerate expectations from native/Rust output to make comparison
pass. The full shared set has 49 rows (15 seed + 15 validation + 19 campaign).
Rust-only host checks/private lifecycle rows are separate. Pending source-bound
queue receipts will be published here; they do not seal N05 or qualify native
installed consumers.

## Source-bound queued reference r1

The immutable copy is `validation/native-parity/n06/rust-r1/snapshot` (135 files).
Its compact source manifest SHA-256 is
`b9d4dcdcfc003c046e505ac1de94b4adad2fb37ec34c951f33ee24298317cc18`.
Full input path/type/mode/size/hash inventory and requested/effective commands are
in [`rust-r1`](../../validation/native-parity/n06/rust-r1/README.md).

- Actual public reference plus independent parsed-JSON comparison:
  `6ac33655baf9486895c626676a9740ff` (`cargo run`, queued).
- Private source adapter/codec/self-model compilation:
  `5ea2d04832ab4cc0acb08d05e8e1384c` (queued; effective `--no-run`).
- New public-reference and unchanged campaign host-test compilation:
  `c47fa958741f4502beac1404b613ad1d` (queued; effective `--no-run`).

No duplicate was submitted. All use the shared default queue and one explicitly
exclusive target directory. Public runtime admission is not execution; both
compile jobs still need emitted test binaries run through Kyoto afterward.
No fresh-installed/native integration, N05 completion, N10, or promotion receipt
is produced here. The independent final source-only fixture review found the
package-inclusion item below and no additional substantive type/compile or golden
callback/value mismatch. It affirmed the four contract fixes, independently
checked admission, faulted fuzz and WorkerFailed overwrite against r1 source.
That review executed nothing and is not a compile/runtime pass.

Package integration item for the root: the existing Cargo `include` list includes
`tests/**` but not the new `conformance/fixtures` sources. If packaged tests are
retained, add `conformance/fixtures/native-campaign-public.rs` and
`conformance/fixtures/native-campaign-golden.py` (plus this schema for consumers)
to the package include list before fresh packaged-test qualification. Otherwise
the new integration test's relative source path is absent from the package.
This author has not edited the root manifest, which is outside its owned paths.

## Independent expected materialization and later private test delta

[`native-campaign-author.py`](../../conformance/fixtures/native-campaign-author.py)
materializes `expected.jsonl` and a `n06-independent-spec/1` `FREEZE.json` in
`validation/native-parity/n06/rust-goldens-r1/artifacts` through a separate Kyoto
job. It combines independently masked SplitMix arithmetic and manual validation
inventory from `native-campaign-inventory.py` with the pre-authored 19 callback
cases from `native-campaign-golden.py`. The seal includes all 49 IDs, distinct
15/15/19 inventories, author/schema/contract hashes, expected-file hash, N05 base
identity and `candidate_output_used:false`. It never imports native code, launches
the reference, or reads reference output. The immutable author snapshot and exact
job handle are recorded in `validation/native-parity/n06/rust-goldens-r1/README.md`.
No expected file or seal exists until that job actually executes. A materialized
specification is still not a runtime pass. Keep this contract unchanged after its
author snapshot; changed source identities require a new specification seal.

The requested fabricated-worker-loss review found a **test-oracle gap** in the
original Rust self-model: its ledger uses lost_work to calculate accounting, but
does not directly require core.lost_worker == ledger.lost_work. After a received
ModelError already sets accounting incomplete, an injected false->true
lost_worker can satisfy the original state/accounting checks; the core-based
Finish admission then allows outstanding work without a true loss event.
This is not evidence of a real executor spontaneously fabricating loss.

The owned private adapter now explicitly checks this equality and has a control
with two claimed jobs, a received/acknowledged ModelError, WorkerFailed(false),
and fabricated loss. The independent ledger rejects Finish without a true loss
event even though the corrupted core would accept; a subsequent real
WorkerFailed(true) enables Finish. Production and original Rust test sources
remain unchanged. **This strengthening was authored after the r1 snapshot and
is not covered by its queued compile.** Root must compile/run this later delta
in its final source snapshot after reconciling r1; no duplicate live compilation
has been submitted for it.
