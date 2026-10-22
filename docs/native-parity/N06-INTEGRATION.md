# N06 implementation coordination and evidence boundary

Implementation base: `0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`.
Owned checkout: `/Users/jagatranvo/.t3/worktrees/stateless/t3-25250336`,
branch `t3/native-campaign-runtime`. Integration into another branch belongs to
the parent task. No push, PR, default change, profile promotion or 144-cell
promotion is part of this implementation.

## Dependency boundary

The N05 implementation is reviewed; its final qualification remains pending.
The inherited materialized corpus has 78 cases. The reviewed author derives 90,
but its queued author job `36c3552df326429fb9e76d872c80aafa` has not supplied the
required FREEZE. This is not a 90-case pass. Its owner retains all existing job
handles; this task must not restart or replace those jobs. Native campaigns use
the actual public guided APIs. Any necessary N05 core change must be identified
separately with its dependency implications.

N10 assurance integration is separate. Rust exports no generic CampaignReport
merge. Tests may sort independently collected, identity-bearing job reports for
deterministic comparison; runtime sink delivery must retain host receipt order.
Sorting is not a new aggregation guarantee or a durable delivery protocol.

## Ownership

The language implementers own their native package files, language tests,
`examples/native-campaign/<language>`, language-specific N06 documentation and
language-specific evidence. The Rust reference author owns the independent
public-reference fixture, private test adapter and N06 Rust contract. The
qualification author owns the installed-consumer harness and its documentation.
The coordinating implementer owns cross-language review, this file, final source
freeze, evidence reconciliation and scoped commits. Child tasks do not stage or
commit shared files.

All builds, tests and executable qualification use the shared default Kyoto at
`/Volumes/seed1/Projects/kyoto/kyoto.py`. Direct Cargo jobs retain Kyoto policy;
compile-only success is not runtime evidence. Focused jobs must name an immutable
source identity and retain complete logs, including failures. Final aggregate
submission is **not yet authorized by this coordination marker**: source and
consumer interfaces are still being implemented. This marker coordinates owners;
it does not request another user approval.

## Required review gates

- The private decision core drives actual Claim, Complete, Receive,
  SinkReturned, WorkerFailed and Finish events. Independent ledger checks and
  codec acceptance remain test-only and detect false rejection as well as
  duplicated effects. They must not merely compare two calls to the same reducer.
- Ordinary and guided public calls preserve validation, factory, metadata,
  feedback, control, disposal and error priority. Model-factory error has absent
  metadata and known zero work. Feedback-factory error is ModelError with present
  model metadata and incomplete accounting.
- A job error is not a property failure and does not activate stop_on_failure.
  A worker error alone does not suppress the sink. The first sink error survives
  all later drain and worker events. Lost work never becomes a fabricated report.
- Configured workers=1 uses the caller; configured workers>1/jobs=1 still uses a
  spawned worker. Multiworker tests prove real workers, job-local factories and
  out-of-order completion. Buffer zero has rendezvous semantics.
- Complete precedes blocking send. Receiving releases a sender before sink
  completion. While the sink blocks, outstanding work is bounded by queue
  capacity plus actual workers plus one sink report, rather than by cumulative
  starts. Reports and application callbacks stay outside coordinator locks.
- Cancellation and deadlines are cooperative. Late rejected Claims are inert;
  accepted job completions still affect stop priority. Native fatal traps,
  aborts, process-wide failures, preemption, hard RSS limits, durable exactly-once
  delivery and queue resume are not promised.
- Native ownership, callback lifetime and worker-transfer policies are explicit.
  Models and feature keys stay worker-local. Application report inputs require
  faithful transfer; no canonical ModelCodec or arbitrary closure serialization
  is inferred from Rust.
- Public Rust one-worker comparisons, independent lifecycle/fault witnesses,
  actual host scheduling tests, fresh installed applications and baseline
  regressions remain separate evidence classes. Only executed hosts qualify.

## Current state

All four production runtimes, native lifecycle fixtures and public examples are
authored. The independent Rust reference slice is committed as
`2bcdd8bfd19744f50b6ed3819ac2451a8de101be`. Source review fixes and installed
qualification work are still in progress. No N06 runtime, installed-package,
browser, cross-platform or full-parity qualification is claimed by this file.
The [evidence index](../../validation/native-parity/n06/README.md) preserves the
live job handles and their distinct source identities.

Reviewed source commits so far:

- `2bcdd8bfd19744f50b6ed3819ac2451a8de101be`: independent Rust public contract,
  reference/author fixtures, lifecycle adapter, package inclusion and compact
  queued-job receipts.
- `188a1e4a69938cb4c433a7df8d53f16ab6044b50`: Python spawned-process and TypeScript
  caller/Node worker runtimes, exports, lifecycle codecs/ledgers/fault tests and
  both Python/TypeScript source-review rounds. Remaining runner/example changes
  are outside this commit.
- `ef5c46b5646eec78599c0ed69756df889a8126ce`: Go goroutine/channel and Swift
  POSIX-thread/condition-channel runtimes, independent lifecycle fixtures, causal
  transport tests, additive checked Sendable conformances and both Go/Swift
  source-review rounds. Runner and consumer changes remain outside this commit.
- `faa5baa6c9ddafbf0ff40bfd28f054709d65313e`: Go API/example, reviewed current
  driver and preserved r1 job/manifest receipts.
- `bc93b05db878bba433d211076eddf3030cdc264c`: Swift API/example, reviewed runner
  and guard fixtures, immutable source manifest and queued focused execution.
- `ee69e247e0b7a26a9c77a84f1228718f2922b14b`: TypeScript browser/Node examples,
  API notes, reviewed current driver and preserved r1 job/manifest receipts.
- `eb7a0b8f65735247d733dc5f34dc231cef2d3550`: Python public example, reviewed
  standalone driver and regression fixtures, API notes and preserved r1 receipts.

| Native runtime | Real worker/transfer mechanism | API notes |
| --- | --- | --- |
| Go | Goroutines and bounded native channels; application input clone hooks | [N06-GO](N06-GO.md) |
| Python | Spawned processes, per-worker bridge and explicit input transfer | [N06-PYTHON](N06-PYTHON.md) |
| TypeScript | Caller entry or Node coordinator plus job workers; explicit module and input adapter | [N06-TYPESCRIPT](N06-TYPESCRIPT.md) |
| Swift | POSIX threads and condition channel; Sendable report inputs | [N06-SWIFT](N06-SWIFT.md) |

Each implementation drives its live scheduler through the private lifecycle
reducer. Native factories construct worker-local models; sinks remain serial on
the caller. Browser multiworker execution is not supplied by the Node adapter.

### Review disposition before source freeze

The independent reviews are retained in `N06-CONTRACT-REVIEW.md`,
`N06-CONTRACT-REVIEW-R2.md`, both rounds of `N06-REVIEW-GO-SWIFT`, both rounds of
`N06-REVIEW-PYTHON-TYPESCRIPT`, and `N06-REVIEW-HARNESS-R1.md`. Each report binds
its observations to hashes; later source changes are not implicitly covered.
Source review is not compilation or runtime qualification.

Round two confirms source fixes for the earlier production findings: Python
startup ownership, drain/decode ordering and diagnostic containment; TypeScript
mutable acknowledgement identity, diagnostic ownership and caller cancellation;
and Go feedback lease ordering and diagnostic panic provenance. All four
independent ledgers now check genuine worker loss and Finish admission directly,
with deliberate fabricated-loss controls. Those fixes still require execution.

The Go and Swift transport fixes now observe actual channel admission or
condition waits; targeted source reviews found no actionable transport defect.
Swift's observer evidence and its driver/guard fixes have completed targeted
source review; the sealed focused job remains queued. Go and TypeScript's current
driver fixes have also completed source review, but their existing r1 jobs cover
earlier driver bytes. Python's standalone driver, fixture membership and
validation operands have completed targeted source review. Its caller-selected
driver hash and new failure/cleanup fixtures still require authenticated
preparation and execution; two receipt-correction branches lack dedicated fault
tests. These changes do
not mutate frozen queued snapshots. The installed harness has a stable manifest
and a fresh independent second review covering controlled tool environments,
toolchain identity, seeded multi-case callback comparisons and guidance-validation
precedence. The completed review documents remain historical observations;
fix dispositions identify later source. Every execution gate remains pending.

The contract round-two finding about Swift example diagnostic rewriting is
source-addressed after that review cutoff. Root inspected `jobJSON` in
`examples/native-campaign/swift/Sources/CampaignConsumer/Projection.swift`
at SHA-256 `14ef7d4e8dc7a1aaf2ff09048427da387bda20849f99383e74e1b2e84462536c`:
FactoryError and ModelError retain the returned description, and Panicked retains
its message. No default diagnostic alias remains in that projection. The
underlying public diagnostic/outcome differences remain open and unexecuted.

The test-only codec's full Rust wire domain is distinct from each native
runtime's representable count domain. Go and Swift expose signed native counts;
TypeScript runtime counts use safe integers, while its codec preserves full-u64
wire values separately. Valid wider Rust counts must not be called corrupt,
silently clamped or reported as passing shared cases. Wider unsupported count
domains remain an explicit parity gate.

### Explicit N05 dependency: Swift feedback metadata failure provenance

Rust unwinding from feedback metadata produces `Panicked` after model metadata
is present. The unchanged Swift public `guidedFuzz` calls the existing
`call("feedback metadata", ...)` boundary, which normalizes thrown errors to
`ModelError`, including a recoverable `CampaignPanic` marker. The current Swift
campaign therefore observes `ModelError` with present model metadata and
incomplete accounting for that fixture. This is a concrete dependency limitation,
not an equivalent result. The Rust expected row and actual Swift result must
remain intact in comparison evidence. No filter, projection or expected-value
rewrite may turn the mismatch into a full-parity pass.

An N05 provenance change, or a separately reviewed campaign adapter preserving
provenance before the public wrapper normalizes it, would need its own source
and regression evidence. Neither is implied by the current Swift policy. Swift
fatal traps remain unrecoverable regardless of such a catchable-error policy.

The [separate dependency-gap inventory](N06-DEPENDENCY-GAPS.md) also records
count-domain, diagnostic, admission and Go composition changes needed for exact
parity. These must be reviewed against the existing public foundation APIs.

The TypeScript public guided wrapper similarly normalizes its ordinary Error
fixture at feedback metadata to ModelError. The consumers also expose existing
callback-context diagnostic differences in all four languages and Python's
earlier FuzzConfig constructor rejection of mutation 101. These are additional
exact-comparison dependencies, catalogued in `N06-QUALIFICATION.md`; they must
not be aliased away in protocol rows. They are source-derived differences until
the queued executions produce bound runtime evidence.
