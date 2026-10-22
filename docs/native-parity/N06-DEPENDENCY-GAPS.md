# N06 exact-parity dependencies

These are source-derived gaps against the public Rust contract, not executed
comparison results. The N05 authority is
`0a26bf5a068e5948d82a3d8bd9eb4063ae85115f`; its final qualification remains
pending. N06 uses the actual native public fuzz/guided APIs. Changes to their
existing behavior need separate N05 review and qualification. This file does not
rewrite the frozen Rust expectations or authorize promotion.

| Dependency | Current native behavior | Required work for exact parity |
| --- | --- | --- |
| Swift feedback-metadata failure provenance | `guidedFuzz` in `swift/Sources/Statelessness/Guided.swift` passes metadata through `call`, which converts a thrown `CampaignPanic` to ModelError. The model metadata is already present; accounting is incomplete. Rust unwinding returns Panicked with the same metadata/accounting boundary. | Preserve a reviewed catchable failure category through the public wrapper, or introduce a separately reviewed campaign adapter before normalization. Swift fatal traps and aborts remain unrecoverable. |
| TypeScript feedback-metadata failure provenance | `guidedCall` in `typescript/src/guided_sync.ts` converts the fixture's ordinary Error to ModelError. Rust returns Panicked. | Define and preserve the corresponding native failure category across public guided calls. Changing the consumer's output discriminant cannot resolve this. |
| Python validation precedence | `FuzzConfig.__post_init__` in `python/src/statelessness/search.py` rejects mutation 101 before CampaignConfig can validate jobs. Rust can construct this configuration and checks jobs first at campaign admission. | Review a public configuration/admission design that can express the invalid input and preserve validation order. Do not mutate frozen objects or use a private unchecked constructor in the consumer. |
| Existing callback diagnostics | Go emits `initial state`, `transition`, and `state check`; Python and Swift emit `transition` and `state check`; TypeScript's check fixture contains two `state check` prefixes. Rust uses `initial_state`, `step`, and `check_state` at the corresponding boundaries. | Review the shared public diagnostic contract and implementation. Keep raw diagnostics in all exact-comparison rows; no aliases or projection repair. |
| Full Rust64 count domain | Go and Swift's existing FuzzConfig case/step counts and FuzzReport case counts are signed native integers. TypeScript uses safe integer numbers. Campaign job/count fields follow those native domains. Rust64 uses usize, admitting wider values even in zero-work/pre-cancelled configurations. | Widen the public fuzz/guided configuration, counters and loop arithmetic, then the campaign API/codec adapters, with separate N05 and N06 boundary regressions. Rejecting a valid wider Rust value is an explicit support gap, not malformed wire or a parity pass. |
| Already-composed Go Auto panic provenance | Existing Auto may catch enumeration/iterator/equality panics inside opaque closures and return ModelError before N06 can observe them. | N06's public CampaignAuto installs tracking before composition. Supporting already-composed Auto requires reviewed provenance preservation in that existing composition path; arbitrary application closures that intentionally catch panics cannot be reconstructed. |

The shared 49-row inventory uses count operands no larger than
9,007,199,254,740,991 and overflow-job operand 2049. These exact operands must
remain visible in each output. Wider host/Rust count assertions are separate;
seed, job ID and work-counter u64 fixtures remain full width. Go/Swift's current
test-only codec rejects otherwise valid wider count states with a representation
error; TypeScript's wire DTO preserves them separately from its runtime state.
None of these policies establishes full Rust64 count support.

The source owners' API notes and installed-consumer READMEs contain the exact
native policies. The independent reviews retain their inspected hashes and
historical findings. A later core/API resolution must preserve failed comparison
logs, bind the new source/package identities, rerun the affected public and native
fixtures, and retain N05's existing owner-held jobs and qualification boundary.
