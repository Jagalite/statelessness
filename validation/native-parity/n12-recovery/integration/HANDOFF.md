# N12 integration recovery handoff

Candidate source commit: `f866f7552df77736297fec5bf515481730a23f4c` on `t3/recover-n12-debugger-parity`, worktree `/Users/jagatranvo/.t3/worktrees/stateless/t3-ccd33c8a`. This agent made no commit, push, PR, parent merge, old-worktree mutation, or old-job cancellation.

## Passed finite integration scope

Final Kyoto execution `1ee93dea6b3346b998b3fd44050f23bb` succeeded. `final-r3/qualification.json` records `passed: true`, exact candidate commit, `source_baseline_matched: true`, `source_stable: true`, and zero changed source paths. No integration-owned live handles remain.

- Actual public Rust reference: **11 tests passed**, including original offline/session/inspection/effects/protocol assertions and new watches, probe revision/callback/loss, live safepoint/dispatch, dashboard/causal join, metric window/catalog finite goldens. Exact public protocol status/snapshot/step/cancel emissions remain in `final-r3/00/stdout.log`. Source-derived expected values were written in the two independent integration goldens before execution; original manual fixtures were preserved.
- Actual reference execution was separate from compile-only Kyoto job `7b4d790fcae44e4d94c41f988f9eb679`. `reference-final-compile-before.json` and `reference-final-compile-after.json` bind equal membership/type/hash for 109 Rust source paths. The final reference binary is 4,568,672 bytes, SHA-256 `55b797fdd33ef0b9b07d7adc3b80326a63a50167cba26edb4bbc171b05b83729`; runtime before/after binary hashes match.
- Rust `debug_stdio` OS pipes: six framed requests exercised status discovery, full-width request IDs, snapshot, identical request deduplication, conflicting ID rejection and read-only delivery authority. A separate process rejected truncated-frame EOF with nonzero exit and zero stdout. This host exercised no delivered input callbacks; sequence remained zero. No instrumented callback count is claimed by the pipe fixture.
- The host example build `8599279a7d4a4d1dad89fbd896f55935` succeeded. Runtime host binary: 2,623,056 bytes, SHA-256 `af1d9b08b2abd19a9383985fa13bec0f251fe492beab40f0f8266f3d89fe0a8d`; before/after equal.

Final raw Kyoto log: `jobs/final-observation-1792542873537571000/1ee93dea6b3346b998b3fd44050f23bb.log`, 3,599 bytes, SHA-256 `ec1832ea0a5f23dd6bca010fce0dbff74d162e6e6c9afa11777c31b578209c21`. The `.show.json` and `.receipt.json` beside it preserve requested/effective command and terminal state. Reference raw stdout: 1,969 bytes, SHA-256 `926d0af7db770dfc8ef6e9bb84894e67601a39690b2481b21fa8f8f4ac53b71a`. Pipe raw streams and stderr have independent byte-size/SHA entries in the final qualification report.

## Preserved failed and earlier evidence

`final-r2` and job `f2192f30ded5430bb92bc3a1476c24e5` are intentionally retained as **failed final qualification**: the stale first reference binary ran 10 passing tests while the frozen source expected 11. Required-count checking correctly rejected its zero process exit. The first compile raced the last reference-source addition; no claim that its compile bound the final source is made. The source snapshot wording originally describing that first compile as queued was based on an earlier observation; `reference-final-compile-before.json` explicitly corrects this and proves the same final snapshot predates the second compile submission.

Earlier pipe `016c24ba97844bc0a19761f99dc73841` passed a narrower harness; it is superseded by final-r3, not deleted. Initial helper syntax `89252a37248f4719b9c686d86cace888`, first compile `e869645469424e7387f5a2d6bccc91a0`, and all their observed raw logs/receipts remain in `jobs/`. Queued observations remain distinct from later terminal observations.

## Remaining gates and boundaries

Direct comparison of **native-generated** normalized public wire artifacts against the actual Rust status/snapshot/step/cancel emissions is still unqualified. Native language agents supplied their own real OS-pipe suite fixtures and installed/full-module qualification receipts; those are separate evidence scopes owned by root and their language agents. This integration task did not construct equivalent native emitters or claim full public wire artifact identity across the four languages.

These are finite contracts, not complete N12 closure. Full N12 acceptance review, broader 144-cell qualification and profile/default promotion remain pending. No general RSS/preemption/restoration, LLDB/GDB, remote authentication, or new default claim is made. Original old-worktree jobs and historical receipts remain root-owned.
