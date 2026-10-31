# N06 source review and execution checklist

This checklist records acceptance obligations, not execution results. The Rust
reference is pinned by `N06-RUST-CONTRACT.md`; its private fixture must retain the
actual runtime reducer and test codec identities. Implementer/reviewer findings
and source-bound execution receipts are recorded separately.

## Admission and deterministic jobs

Review both public entries. Guided admission validates guidance before campaign
configuration. Campaign validation rejects jobs, workers, result buffer, mutation,
ID-range overflow, aggregate cases and aggregate transitions in source order.
Host representation errors must be documented and must never coerce or wrap an
invalid configuration. Seed addition and SplitMix64 arithmetic deliberately wrap;
range validation and accounting do not.

The maximum transition calculation uses a wide cases-times-steps product, caps
it by max_transitions, and only then multiplies by jobs. Exercise a product too
large for u64 with a safe transition cap. Exercise first_job=u64::MAX/jobs=1,
the invalid adjacent range, and master+job wrap. Every emitted report retains
job ID, derived seed, effective fuzz config, model metadata and outcome.

Pre-cancel and zero deadline follow admission and cause no job callbacks. A
control change inside a factory still allows model metadata and guided feedback
construction/metadata before fuzz preflight. A callback failure has its actual
precedence over the next cooperative control poll. Zero cases, steps and
transitions retain public fuzz semantics. FactoryError has known zero work;
ModelError/Panicked have unknown partial work. None activates stop_on_failure.

## Live protocol and transfer

Trace the production scheduler to its private decision core for every accepted
event. A duplicate or stale event must reject without mutation or effects. A
late Claim must not sample a new stop into state. Complete validates summary,
marks Ready and observes job termination before any blocking send. Receive
accounts/removes Ready before sink invocation. SinkReturned acknowledges the
currently delivering ID exactly once. Finish occurs after drain/join.

Inspect both worker branches: workers=1 invokes factories on the caller;
configured workers>1/jobs=1 uses a spawned worker. Each worker constructs and
uses its own model and guided feedback. Application factories remain responsible
for not returning aliases into shared mutable storage. Native types/contracts
must prevent or explicitly govern transfer of model/state/output/feature owners.
Only report inputs cross the host-worker boundary, under a faithful declared
application transfer policy independent of canonical ModelCodec.

Exercise buffer0 and buffer1 with a held sink. Receipt must free the sending
worker before SinkReturned. While sink1 blocks, no more than buffer+workers+1
jobs can remain started and not successfully delivered; bound tests must wait
for evidence of blocked workers, not infer correctness from a short sleep.
Test out-of-order jobs by a barrier that proves both jobs are active. A worker
must not claim another job while its previous send is blocked. No host runtime
message queue may silently add unbounded report capacity.

## Stop, fault and cleanup priority

Exercise stop_on_failure with another job already claimed. Property stop does
not cancel that job. Among accepted completions, Cancelled dominates Deadline,
which dominates property failure. These rules do not override infrastructure
termination. WorkerError alone keeps sink calls enabled; later worker errors
replace earlier worker errors. Sink failure suppresses all later sink calls,
drains/counts reports, joins workers, and remains the authoritative error.

Factory failure, feedback-factory failure, metadata failure, native recoverable
panic/throw, transfer failure, start failure and worker loss need distinct
witnesses. Do not synthesize a finished JobReport for infrastructure loss. Start
failure records lost_work=false; join failure/lost outcome records true. Dispose
worker-local values within the job boundary using the host's declared cleanup
policy. Callback diagnostic formatting must not replace an earlier sink error.
No catch of a language-level exception proves recovery from fatal traps or aborts.

Saturate each aggregate work counter independently and make accounting loss
sticky. Accounting completeness is separate from requested completion,
successful delivery and property success. Per-job saturated skipped checks lack
their own overflow flag in Rust; do not invent a stronger aggregate guarantee.

## Independent lifecycle fixture

The finite alphabet includes wrong IDs, stale/duplicate Complete/Receive/ack,
inconsistent summaries, controls, WorkerFailed and Finish. An independent ledger
records claims, completions, receipts, sink attempts/returns/successes, discards,
first sink error and last worker error. Validate admission from predecessor
facts: incorrect inert rejection is also a bug. Wider sums verify saturation.

Test-only state/input/output codecs preserve full u64 and strict UTF-8. Exercise
every tag/optional field, truncation, trailing bytes, invalid flags, unsorted or
duplicate pending IDs, hostile lengths and impossible state properties. An
inconsistent but syntactically valid Summary input decodes and is then rejected
by Complete; do not confuse syntactic and semantic acceptance.

The duplicate-Deliver fault must be discovered with actual native BFS/fuzz,
shrunk to its causal witness, recorded, read and replayed. Strict healthy replay
rejects its different build; relaxed healthy replay diverges without reproducing
the fault. Healthy replay is a separate control. Persisted fixture bytes are not
a public runtime checkpoint or queue-resume feature.

## Evidence boundaries

One-worker comparisons use independently authored expectations and actual public
Rust calls. Deterministic per-job records retain identity before any comparison
sorting. Scheduler-dependent traces are checked by host invariants. Neither is a
generic CampaignReport merge. Installed applications run fresh packages/processes
and real worker callbacks; an in-tree import is not installed-consumer evidence.

All executable checks route through shared-default Kyoto. Preserve requested and
effective commands, source/package/corpus identities, complete logs and before/
after membership/type/mode/hash manifests. A failed run remains in evidence after
a fix; a pending job is not a pass. Browser, Node, macOS, Linux and Windows claims
are separate; N05 final qualification and N10 assurance remain separate gates.
