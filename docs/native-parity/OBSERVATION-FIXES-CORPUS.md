# Observation corpus and Python consumer corrections — round 2

Source-only correction handoff, 2026-10-22, in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, base
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`. This addresses the assigned
R6/R7/R8 portion of [the proof review](OBSERVATION-REVIEW-PROOF.md) and the
Python Off-probe measurement gap in [the Python/Go review](OBSERVATION-REVIEW-PYGO.md).
The historical review documents retain their original 34-case snapshot claims.

No implementation, fixture validator, build, compiler, test, or consumer was
executed, and no Kyoto job was submitted. No expected bytes were obtained from
an implementation. No files were staged or committed; the parent owns the exact
integrated source freeze and shared Kyoto qualification. Source inspection found
no applicable `AGENTS.md`; the required `rust-builds-kyoto/SKILL.md` was read.

## R6 and Python Off-probe measurement

The [Python consumer](../../examples/native-observation/python/consumer.py) now
selects a `DeclarationModel` for declaration cases. Its bounded encoder methods
reach a trapping override; its legacy encoder methods also trap. The public
`model_codec(...).bind(model)` wrapper supplies schemas whose field-adapter
callbacks independently append the one-byte payload and record the existing
callback names/order. Those adapters do not delegate to the original model's
encoding method. Removing the binding therefore cannot yield the literal
declaration fixture's successful bytes and callbacks.

The corresponding [regression source](../../python/tests/test_observation.py)
checks the direct bounded and legacy traps for state/input/output and deliberately
replaces `declaration_bound` with an identity function. That bypass must produce
`encode_input`, no delivered bytes, no codec callbacks, and a healthy sink. It
compares the normal bound consumer against the independently authored fixture.
This is regression source only; the bypass and success assertions were not run.

`Sink` now counts every write attempt before any injected failure or delegation,
including a zero-byte write, as well as every flush attempt. The final Off health
probe snapshots both counts and the payload callback list, then requires all to
remain unchanged on both healthy and poisoned paths. Writer extraction also
requires no additional writes or flushes. The [consumer README](../../examples/native-observation/python/README.md)
documents these controls and the existing execution/authority boundary.

## R7: two independently authored Trace callback cutoffs

[The corpus](../../conformance/observation-v1.json) now has 36 cases, reflected in
[the source contract](../../spec/observation-v1.md). Both additions supply stream
`s`, sequence 1, Accepted, zero checks, before 0, input 1, output 2 and state 3,
with Trace/EveryTransition and a 16-byte encoding bound. The transition values
are supplied data; no reducer or checker produces them.

- `trace-zero-bound-callback-cutoff-recovery` sets the record bound to zero.
  Rust's first nonempty prefix write fails before any payload callback. Expected
  record text/hex are empty, callbacks are `[]`, first error is `record_limit`,
  and final sink poison is false. It then explicitly flushes and switches to Off
  before supplying sequence 2 with an input-codec fault armed. Those operations
  are admissible without a record allocation or payload call. A nonempty recovery
  record cannot fit this bound; this case claims sink-health/Off recovery only.
- `trace-input-bound-callback-cutoff-recovery` sets the record bound to 97.
  The prefix through `checks=0` is 84 ASCII bytes. Adding ` input=` costs seven,
  and `hex:01` costs six: exactly 97. The next ` output[` write fails before
  the output codec; post-state formatting is also unreachable. Callbacks are
  exactly `["encode_input:1"]`. The failed record publishes no bytes. It then
  switches to Debug, supplies sequence 2 with an input-codec fault armed, and
  flushes. Debug invokes no codec, so the recovery record is the following
  literal 84-byte line plus LF, within the same bound:

  ```text
  Debug stream="s" event=transition sequence=2 disposition=Accepted outputs=1 checks=0
  ```

  `record_hex` contains only this recovery record, including its final `0a`.
  The first error remains `record_limit` and the final sink is healthy.

These expectations were derived from [Rust observation formatting](../../src/observation.rs#L376),
especially the fallible prefix and payload-field writes at lines 391 and 431–447,
the [encoded payload formatter](../../src/observation.rs#L258), and
[sticky bounded writes](../../src/observation.rs#L472). The sink receives a
record only after the final LF succeeds at lines 451–452. The recovery paths use
the existing health-before-Off guard and explicit flush implementation; they do
not reset or replace the observer. The literal recovery hex was transcribed from
the independently authored ASCII line, without executing an encoder/observer.

## R8: native short-write scope

The spec now explicitly distinguishes Rust/Python completion of valid short
writes and interrupted-write retries from Go's writer contract. A Go short write
with nil error becomes `io.ErrShortWrite` and poisons the sink; partial bytes
accompanied by an error are not retried. Shared injected failing-prefix fixtures
do not establish complete writer behavior equivalence across languages.

The source references are the Rust short/interrupted-writer regression in
[tests/observation.rs](../../tests/observation.rs#L584), Python's corresponding
regressions in [test_observation.py](../../python/tests/test_observation.py), and
Go's `observationWriteAll` in [observation.go](../../go/observation.go). Reading
these regressions is not an executed test result.

## Preserved authority and remaining qualification

The [N07 plan](../NATIVE-PARITY-PLAN.md#n07-runtime-observation-and-bounded-recording)
and historical [checked-results audit](CHECKED-RESULTS-AUDIT.md) were read against
the actual current APIs. Existing checked tokens, periodic checking and bounded
Recorder already exist. No observation production implementation or checked/
Recorder implementation is changed by this correction scope. Diagnostic events
still confer neither continuity nor authority to bypass checks.

The Rust references preserve full-coverage/absolute-sequence admission in
[observe_checked](../../src/monitor.rs#L308), before-state continuity in
`prepare`, and promotion of only the evicted state-check prefix in `commit`.
Relevant source regressions include retained checkpoint replay in
[tests/monitor.rs](../../tests/monitor.rs#L125), variable check boundaries in
[tests/monitor_review.rs](../../tests/monitor_review.rs#L114), and preservation
of a coherent prefix when a candidate cannot fit in
[tests/monitor_limits.rs](../../tests/monitor_limits.rs#L186).

All compilation, runtime, installed-package, five-language fixture comparison,
and broader N07 gates remain with the parent's later frozen-source Kyoto run.
The two additional cases and their metadata are authored expectations, not
qualification results. No prior receipt is promoted to evidence for these edits.

## Source identities at handoff

These SHA-256 readings identify the corrected inputs and unchanged Rust
references. They are metadata readings, not validation receipts; the parent must
freeze and qualify the final integrated tree separately.

```text
20d7002957eb05f1d22c25f4a0be613105323fabcd2a49382588e6bc9ae7e8a8  conformance/observation-v1.json
90dbf8fc2f838231c798b6e938af734d0c9dfe98db137cbb2eb94222a3fa2972  spec/observation-v1.md
ab1cda741501de681f767fbb9f9ab22e867555b373f97f5c0458c8b6b47f4d6b  examples/native-observation/python/consumer.py
cf35277d9ee9711a64a895f2264b29590194f208bed858ee2fbf0bf6e2f26414  examples/native-observation/python/README.md
565db14b37211928c263aab59057b10aef1df9dc81821fec77a427123f7841db  python/tests/test_observation.py
c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870  src/observation.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d  tests/observation.rs
```
