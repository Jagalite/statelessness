# Exact Rust64 count continuation

Normative behavioral authority: `0876717bee85e335449c2c99ada41f6c6e6112d1`,
`src/explore.rs`, `src/guided.rs`, `src/campaign.rs`, and
`src/campaign/lifecycle.rs`. This contract is authored from those sources before
reviewing the new language implementations. It supplements the N05/N06 contracts;
it does not redefine their callback, ownership, failure, or delivery laws.

The integer domain is 0 through 18446744073709551615 inclusive for Rust64
case/step counts, guidance limits and counters, campaign counts, and lifecycle
report counts. Signed-host and JavaScript safe-number limits are not semantic
admission limits. Exact-width entrypoints must execute the same real kernels as
checked convenience APIs. Wire-only acceptance followed by narrowing is invalid.

## Bounded independent witnesses

1. Ordinary fuzz with maximum cases and steps and pre-cancellation returns
   Cancelled with zero cases/transitions and invokes no model callbacks. With
   zero cases and maximum steps it returns CasesCompleted without callbacks;
   pre-cancellation still wins over zero cases.
2. One case with maximum steps and an exhausted generator completes one case
   with zero transitions. Maximum cases/steps with an initial property failure
   returns one case, zero transitions, and the concrete empty failing prefix.
   A one-step failure returns one transition and its actual one-input witness.
3. Guided maximum counts preserve exactly one feedback metadata query before
   pre-cancellation, but no initial/transition feature callback. Invalid guidance
   precedes mutation validation; invalid mutation prevents metadata. Huge guidance
   limits with one or two actual observations retain only actual emitted values;
   they must not reserve the advertised capacity eagerly.
4. Campaign admission checks jobs-positive, workers-in-1..256,
   buffer-in-0..1024, mutation-in-0..100, job-ID range, aggregate cases, then
   aggregate transitions. Cancellation does not override invalid config. Guided
   campaign guidance validation precedes campaign admission. Zero jobs is invalid;
   zero per-job cases is valid. Range first=1/jobs=MAX is valid, first=2/jobs=MAX
   overflows. Jobs=MAX/cases=1/steps=MAX/transitions=1 is valid; jobs=2/cases=MAX
   overflows aggregate cases. Compute the transition product exactly before min.
5. A pre-cancelled valid MAX-job campaign starts no worker/model and reports
   requested=not_started=MAX, started=finished=delivered=0. A MAX-job campaign
   with one worker, stop-on-failure and an initial failing model executes only its
   first job. Multiple workers may finish jobs already claimed before the stop.
   It reports one started/finished/delivered/failure and not_started=MAX-1.
6. Lifecycle initial state with jobs=MAX has exact requested/not_started values.
   One claim, completion, receive, successful sink acknowledgement, stop, and
   finish maintain the original transition/check laws across codec round trips.
   Completion alone never acknowledges delivery. Restarted state preserves wide
   report fields. Valid received work sums exceeding MAX saturate each aggregate
   at MAX and set accounting_complete=false. Saturation is not wrapped arithmetic
   and never converts unexecuted work to completed work.

7. N03 automatic generation with max_candidates=MAX and an empty domain returns
   no input and leaves RNG state unchanged. Shrink with max_attempts=MAX and an
   empty-prefix initial failure validates the target in one attempt and zero
   transitions; pre-cancellation returns Cancelled with validation incomplete.
   Attempt counts and simplification ordinal arithmetic are exact-width too.

Tests must use early stop or bounded actual work, never iterate MAX operations.
Native array/string/map indexes and materialization sizes require checked
conversion at use. Configuration bounds must not trigger allocation proportional
to the requested search count. Legacy report adapters reject unrepresentable
results rather than clamp or round them. Preserve ModelError/CampaignPanic
provenance and Node/browser adapter ownership throughout conversion.

## Qualification boundary

Python remains an independent integer implementation and comparator. Language
tests and this source-derived contract are not the installed five-engine harness.
Only frozen-source Kyoto runtime receipts establish execution. Compiler failures,
queue delays, and compile-only successes are retained as such, never semantic
mutation detections. Installed packages, combined integration, platform matrices,
resource budgets, and final parent qualification remain distinct gates.
