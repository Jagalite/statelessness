# Python observation attribution fix, round 2

Source-only implementation against base
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83` in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`.
This note describes working sources, not a frozen or qualified result. No build,
test, compilation, implementation execution, fixture generation, Kyoto job,
staging, commit, push, or PR was performed for this fix. The parent owns the
integrated source freeze, exact staged commits, and shared Kyoto qualification.

## R2 and the change

[R2](OBSERVATION-REVIEW-TSSWIFT.md) also applies to Python's former
`except TextObserverError: raise` in `_payload`: a custom formatter could raise
a public error claiming `sink_write`, another payload's encoding stage, or
`sink_poisoned`. A nested observer's genuine error could leak the same unrelated
stage. The outer writer had never been called and remained healthy.

[observation.py](../../python/src/statelessness/observation.py) now gives every
payload invocation a fresh private operation identity on its revocable writer
lease. `EncodedPayloads` attaches internal attribution only to failures from its
own codec operation using that active lease. `_payload` preserves an encoding
stage only when the private error belongs to that exact invocation. The public
error's mutable `stage` field is not trusted. Every other callback exception,
including a public staged error or an error from a nested observer, receives
the active `format_input`, `format_output`, or `format_state` context.

The identity changes between payload slots, between events, and between
observers. A retained codec exception cannot confer attribution on a subsequent
operation. Delegating to the actual `EncodedPayloads` with the current writer
lease preserves its genuine encoding context; calling it with a separate writer
does not attribute that independent operation to the outer record. Direct public
built-in formatter calls retain their existing codec error stages, short-write
completion, and direct writer-error behavior.

The record stores its first failure's canonical stage and detail independently
of the public exception. `_payload` checks this sticky failure both after callback
success and before classifying a callback exception. An overflow therefore stays
`record_limit` when the formatter swallows or relabels the rejected-write error,
then returns, raises an unrelated exception, raises a forged staged error,
encounters a codec error, or returns an invalid non-`None` callback result.
Record overflow while emitting encoded hex also remains a record failure. This
explicit precedence is the requested native attribution rule; Rust's early `?`
return does not prove bound precedence over a later unrelated callback error.

Formatting failures publish no outer bytes and do not poison the sink. Later
records can recover. Writer-lease revocation still runs in `finally`, before
rendering callback exception text. Process interrupts retain their propagation
behavior. Custom formatters still borrow the supplied model and values without
copying them. The identity is an internal API convention, not a sandbox against
Python reflection or arbitrary callback side effects.

## Source-authored regression coverage

New [test_observation_attribution.py](../../python/tests/test_observation_attribution.py)
covers forged stages at every payload position; nested write, flush, poison and
codec failures; zero outer writes and flushes; healthy Off probes; later recovery;
delegated genuine codec errors and swallowed codec bounds; mutable public stage
fields; wrapped errors; stale codec errors reused across payload slots (including
two outputs in one record) and events; swallowed record overflow with competing
failures; hex-rendering overflow; direct built-in behavior; borrowed values;
process interrupts; and leases expiring before exception-text rendering and after
failures. Literal expected bytes come from source format strings, not
implementation execution.

Existing `test_observation.py`, the consumer, package exports, and README were not
edited by this fix. The parent should include the new module alongside the
existing observation and Recorder integration suites in its frozen-source Kyoto
run. No passing test or installed-package result is claimed here.

## Existing N07 boundaries

The contract was checked against [the N07 plan](../NATIVE-PARITY-PLAN.md),
[observation v1](../../spec/observation-v1.md), pinned
[Rust observation](../../src/observation.rs), [observation tests](../../tests/observation.rs),
[monitor](../../src/monitor.rs), and relevant monitor recovery/retention tests.
The [checked-results audit](CHECKED-RESULTS-AUDIT.md) is historical: current
[checked.py](../../python/src/statelessness/checked.py) already provides sealed
checked results and periodic policy, and [monitor.py](../../python/src/statelessness/monitor.py)
already consumes them through the bounded Recorder. None of those APIs was
reimplemented or removed. Diagnostics introduce no reducer, checker, or effect
execution and confer no continuity, replay, or checked authority. Broader N07
completion remains governed by the parent's integrated qualification gates.
