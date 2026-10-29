# Native guided-v2 golden corpus

Status: **78 cases authored; sealing is queued**. Do not compare candidate output
until `FREEZE.json` is present and its hashes match. Successful `--author`
materializes the independently derived expectations and atomically publishes
that seal after manual assertions and JSON round-trip checks. This directory is owned
by the independent specification agent. Consumers may read it; do not rewrite
expected values from a Rust driver or a native implementation.

Authority: Rust commit `a4cc11eea8db62115b452eee3c6610c04fa06b96`, principally
`src/guided.rs`, `src/explore.rs`, `src/model.rs`. This is bare N05; N06 campaigns
and N10 assurance are separate. The corpus is source-derived, not Rust execution
evidence. All executable author/checker work uses the default shared Kyoto queue.

## Schema (version 1; stable consumer contract)

`corpus.json` has `schema: "stateless-guided-v2/1"`, `source_commit`, and `cases`.
Each case contains `id`, `purpose`, `mode` (`guided` or `ordinary`), `config`,
`guidance`, `model`, `feedback`, `control`, and `expected`. All configurations are
fully expanded. `config.seed` and every raw u64 RNG value are unsigned decimal
strings; ordinary bounded counters and input/state integers are JSON numbers.
Enum values use Rust PascalCase names. Absent options are JSON null, never omitted.

The fixture model is **table-walk-v1**:

* For record/replay APIs that query model metadata, use
  `{name:"guided-table-walk",model_version:1,properties_version:1,codec_version:1,
  build:"table-walk-v1"}`. Bare fuzz must not call this metadata callback; do not
  include record/replay callbacks in the fuzz trace. If a codec is required,
  encode state as little-endian u64 depth followed by little-endian i64 total,
  and input/output as little-endian i64. Decode requires exactly 16 or 8 bytes
  respectively. All fixture values fit those domains. Codec wire interchange
  itself is outside this corpus's claim.
* State is `[depth, total]`, initially `[0, 0]`; input is an integer. Every
  successful step returns state `[depth+1, total+input]`, outputs `[total+input]`,
  and Accepted status. States, inputs, outputs and keys are owned snapshots.
* `model.generate` is a list of per-case input lists, indexed by one-based model
  initialization count and current zero-based depth. A missing case repeats the
  last list; a missing depth returns `model.fallback` (integer or null). The
  generator first consumes exactly `model.draws` raw `next_u64()` values, even
  when returning null. `model.rng_mod` non-null replaces a non-null table result
  with `1 + last_draw % rng_mod`. It requires draws >= 1.
* Enabled means `input >= 0`, additionally `input == total+1` when
  `model.enabled: "successor"`; otherwise `model.enabled: "nonnegative"`.
* Checks default to empty. Each `model.checks` rule specifies `phase`
  (`InitialState`, `State`, `Transition`), optional `case` and `depth` selectors
  (null means any), `checks`, and nullable `error`. First matching rule wins.
  Check objects are `{id,status,message}`; Passed has message null, Failed and
  Skipped have their reason string. A checker appends the given batch and then
  returns its error, if any. `model.fail_total` non-null appends a Failed `limit`
  check with message `total reached limit` to successful state-check batches at
  total >= limit. This rule is state-based and replays independently of case.
* `model.fault` is null or `{callback,call,message}`. Callback is `initial_state`,
  `generate`, `is_enabled`, or `step`; `call` is its one-based invocation count.
  The fault occurs after tracing/consuming generate draws but before successful
  completion. The trace describes attempted callback inputs and values.
* `feedback.kind` is `custom`, `state`, or `exact`. Custom feedback has ordered
  `initial` keys and `transitions` rows `{case,depth,keys,error}`. First matching
  row wins, null selectors match all; no match emits no keys. Each key is a JSON
  string, number, or integer array with exact value equality. The initial hook
  is absent when `initial: null` (Rust's default no-op), otherwise emits its
  array. `initial_error` applies only to an explicit initial hook.
* StateFeedback projects `state[feedback.project]` (0 depth, 1 total); exact-state
  feedback projects a cloned full state and uses Rust exact-state metadata.
  `feedback.hash` is `constant` or `value`; constant deliberately collides all
  unequal keys. Both must preserve value equality. Metadata for custom/state is
  `{name:"table-features",version:1,build:"guided-v2-fixture-v1"}`.
* Every attempted **custom feedback** push appears in the trace, including
  duplicates and rejected pushes. Public StateFeedback/exact-state helpers hide
  their push operation; their tracing wrapper logs callback entry only.
  `feedback.ignore_overrun` true ignores push errors and continues;
  false returns the first push error. A row/initial callback error is returned
  after all attempted pushes only when no propagated push error interrupted it.
  Thus ignore-overrun plus a callback error proves callback-error precedence.
* `control` is `{cancelled,zero_duration,cancel_after}`. `cancel_after` is null or
  `{callback,call}`, using the trace callback labels below and one-based counts.
  The cancellation flag is set after that callback body. A zero-duration run
  uses a real zero timeout; no timing-sensitive positive-duration case is shared.
  For feedback callbacks cancellation is set after pushes and before returning.

`expected` always has `report`, `error`, `trace`, `generator_rng`,
`witness_replays`, and `shrink`. Exactly one of report/error is non-null. A report
contains all FuzzReport fields and the complete GuidanceReport (or null for
ordinary fuzz). An error is the exact Rust-contextualized error string. There is
no successful partial report on callback error.

## Trace encoding

Trace entries are arrays; order is significant. `c` is one-based case, `s`/`b`/`a`
are full states, `i` is input, `cs`/`ct` are ordered check batches, `h` is the
concrete case prefix, and `raw` is an array of decimal u64 strings.

```text
["metadata"]
["initial_state", c]
["check_state", c, s, checks, error]
["check_transition", c, b, i, a, checks, error]
["observer_initial", c, s, checks]
["observer_transition", c, b, i, a, [a.total], cs, ct, h]
["observer_check_error", c, phase, checks, complete]
["initial_feedback", c, s]
["transition_feedback", c, b, i, a, [a.total]]
["push", key, success]
["generate", c, s, result, raw]
["is_enabled", c, s, i, enabled]
["step", c, s, i]
```

The callback control label is the first array element. `push` is an operation,
not a callback and cannot set cancellation. All consumers install a full-history
observer. Fuzz supplies full prefix history even when an observer does not request
it. Normal observer callbacks do not return errors in Rust; native throw/async
handling is tracked separately. No feature hash/equals call count is specified.

`generator_rng` duplicates observable generator draws in compact records
`{case, state, values}`. Internal mutation RNG draws are not public callbacks;
the independent oracle records their source-derived values in a separate
`rng_audit` field for explanation only. Consumers compare generator_rng, report,
error and trace, and need not expose internal RNG state.

`witness_replays` contains `{inputs,states,outputs,checks,termination}` for every
retained corpus entry, from a fresh model. These witnesses pass and make **no
shrinking claim**. Use public recording/execution and replay as available, compare
the recorded states/outputs/check batches, and require replay Exact with
failure_reproduced=false. The normalized witness termination is Completed.
`checks` is a flat ordered array: one `{phase:"InitialState",checks:[...]}`
entry, followed by `{phase:"State",checks:[...]}` and
`{phase:"Transition",checks:[...]}` for each executed turn. Empty batches remain
present. `states` has initial plus successors (N+1 elements); `outputs` contains
one output vector per transition (N elements).
`shrink` is null except explicitly selected failure cases; it
contains the full normalized ShrinkReport, independently authored with no input
simplifications. `shrink_config` is null or `{max_attempts}` and applies only to
this separate shrink run; run controls are reset. A shrink operation is an object
with `kind` (`ValidateOriginal`, `Remove`, `Simplify`) and the corresponding Rust
fields: `{kind:"ValidateOriginal"}`, `{kind:"Remove",start:0,end:2}` (exclusive
end), or `{kind:"Simplify",index:0,alternative:0}`. The top-level input
`shrink_config` is the only selector for running shrink, never `expected.shrink`.
Replay also resets control flags and model callback counters. Native-only
ownership, callback lifetime, sync/async, throwable,
hash API and saturation controls will be listed in the source contract; they are
not fabricated cross-Rust golden cases.

## Consumer alignment and freeze

Adapters must call public guided/ordinary APIs and public replay/shrink APIs as
applicable. They implement this model/feedback/observer, not the guided algorithm.
For `state` and `exact`, use the public helper with a transparent Feedback wrapper
to trace metadata/initial/transition callbacks; the wrapper delegates its work.
Compare all expected fields; `rng_audit` is non-required explanatory material.
The independent authoring oracle uses only stdlib and the rules above. It imports
no candidate runtime and accepts no candidate output. `FREEZE.json` pins the
corpus, oracle, README and source contract before any cross-engine comparison.
Once it exists, `--check` recomputes the corpus and verifies every pinned file.
Run both operations only via default shared Kyoto submit. The currently queued
author/seal job is `8dab80e7ba8d43f69e6a5588bc5753f7`; its input is the authored
oracle, never candidate output. Keep raw
Kyoto submission, show/log outputs and correction history under `receipts/`.
