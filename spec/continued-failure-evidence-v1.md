# continued-failure-evidence-v1 (candidate extension)

Status: experimental candidate extension, implemented in the Rust, Swift, Python, Go
and TypeScript engines, implementing the remaining gap of
migration proposal S5: a witness whose selected failure occurs after an earlier
failed check. Targeted terminal-failure selection, bounded recording,
phase-aware evidence and checked results already exist and are not duplicated.
This format does not change trace-json v1, causal-evidence-v2, or the semantics
of `record`, `recordAudit`, `replay`, `validateRecording` or `shrink64`.

API: `recordContinued`, `validateContinuedRecording`, `replayContinued`,
`encodeContinuedTrace`/`parseContinuedTrace`, `shrinkContinued`.

## Recording

**CONT-001.** `recordContinued` uses the same kernel as `record` but continues
after failed checks, until the inputs end (`completed`), the step limit
(`step_limit`), or a model, checker or codec error (`model_error`, error text
kept, every completed step retained). `property_failed` is never a continued
termination. Steps keep their complete ordered check batch, so two failures on
one turn and failures on successive turns are both recorded. `failingSequences`
lists 0 for a failing initial state and the 1-based steps whose checks failed.

## Format and old readers

**CONT-002.** The JSON form is the trace-json v1 object with `format` =
`stateless.continued-trace-json`, `version` = 1, and an added
`failing_sequences` array that MUST equal the recorded checks. Old readers MUST
reject it: `parseTrace` fails on the format or the unknown field, and
`validateRecording` and `replay` reject the trace. The rejection reason depends on
where failures occur: a failure before the last step gives "trace continues after
property failure", while a failure only on the last step gives "trace termination
disagrees with recorded checks", because continued terminations are never
`property_failed`. Object key order is not normative: writers may emit
`failing_sequences` anywhere and readers MUST accept any order. Finally, `parseContinuedTrace` rejects trace-json v1, other versions,
truncated bytes, and mismatched `failing_sequences`.

## Replay

**CONT-003.** `replayContinued` uses the standard replay kernel with continued
validation. It compares every step's disposition, outputs (including order),
post-state and complete check batch, and stops at the first divergence.
`completeReproduction` is true only when the outcome is `exact`, every recorded
step was verified, and every recorded failing sequence reproduced every failed
check ID. A verified prefix, a suppressed later failure, or an incompatible
model, property or codec version is never complete reproduction.

## Shrinking

**CONT-004.** `shrinkContinued(generate, inputs:, target:)` selects the target
by exact `(phase, check ID)`. It reuses the shrink64 driver (validation as
attempt 1, chunk deletion, then simplification hints, seen-sequence suppression,
attempt, transition and simplification budgets). It differs only in candidate
replay:

- Replay continues past failures and records every failing batch.
- A candidate reproduces only when its final sequence fails and that batch
  contains the target. A final batch without the target is `different_failure`;
  a passing final sequence is `no_failure`, even if earlier steps failed.
- Causal validity is unchanged from shrink64: each replayed input must satisfy
  `isEnabled` at its replayed state. Membership in a bounded search alphabet is a
  different question and is not imposed.
- The original must end with the target, otherwise it is an error (wrong
  property target). An interrupted validation keeps the supplied witness with
  `validatedOriginal = false` and no findings.

The report keeps the original and minimized witnesses even under budget
exhaustion, plus `originalFindings` and `minimizedFindings`: every failing
sequence with every violation. Earlier failures are retained, never suppressed,
and the target is in the last entry.

## Relationship to graph-search-v1

A `collect(expandFailingSuccessors: true)` finding with
`requiresContinuedEvidence` has a witness crossing an earlier failure.
stop-at-failure replay and `shrink64` cannot reproduce it. Its witness and
selected violation are inputs to `shrinkContinued`, and `recordContinued` plus
`replayContinued` produce and verify its portable evidence.

## Required cases

Tests cover old-reader rejection, round trip, corrupted and reordered outputs,
suppressed later checks, incompatible versions, truncated and tampered
artifacts, checker errors after an earlier failure, two failures on one turn,
a selected later target on successive turns with invalid-causality and
different-failure attempts, wrong targets, budget exhaustion, and the
graph-finding-to-minimized-witness path.
