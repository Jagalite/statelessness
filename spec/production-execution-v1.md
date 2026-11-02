# Production execution v1 (Swift extension candidate)

Status: Swift-only candidate extension, introduced for the Illiquid production
migration. Not advertised in `conformanceProfiles`.

`ExecutionSession<S, I, O>` runs one model as an application's live functional
core. It owns the committed state and the turn sequence and performs no
effects.

## Submission

1. A submission while a turn or read is executing fails with
   `reentrantSubmission`. A committed sequence of `UInt64.max` fails with
   `sequenceExhausted` before the step runs.
2. The prior state and the input are cloned with the model's clone contract.
3. `step` is invoked exactly once. Checks, recording and snapshots never invoke
   it again.
4. The candidate is checked with `checkTurn` at sequence `n + 1` under the
   session's `CheckPolicy`.
5. Only if 2–4 complete without throwing are the state and the sequence
   committed together. Any thrown step, clone or check raises
   `model(message)`; nothing is committed and no outputs are exposed.
6. Detached outputs are returned once in `CommittedTurn`.

A complete check batch that contains failed checks is a committed turn
(`failedChecks`), not a fault. Rejected and ignored dispositions commit the
state the step returned; rejection is not rollback. Safety policy, such as
entering a failed lifecycle, belongs to the model's transition.

## Recording

An optional `Recorder` observes each committed `CheckedTurn`. A recorder
failure after commit is reported in `recorderFault`; the turn stays committed
and nothing is replayed.

## Restoration and concurrency

`init(model:checkpoint:sequence:)` restores a checkpoint and runs its initial
checks without replaying outputs. Sessions are not `Sendable`; confine each to
one isolation domain. Reentrant callbacks become later inputs queued by the
host.

Tests: `swift/Tests/StatelessnessTests/ExecutionSessionTests.swift`.
