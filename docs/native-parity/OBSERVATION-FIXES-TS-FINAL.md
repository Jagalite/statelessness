# TypeScript observation final fixes: F1–F3

Source-only handoff, 2026-10-22, in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca` at base
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`. This pass owns only
`typescript/src/observation.ts`, `typescript/test/observation.test.mjs`,
`examples/native-observation/typescript/consumer.ts`, and this note. Other
worktree changes belong to the parent and other agents. F4 documentation belongs
to the parent.

The requested `rust-builds-kyoto/SKILL.md` was read. No applicable `AGENTS.md`
was found. No implementation, fixture generator, build, compiler, test,
executable qualification check, or Kyoto job was run or submitted. Nothing was
staged, committed, pushed, or opened as a PR. The parent owns the integrated
source freeze and subsequent shared Kyoto qualification.

## Changes against the round-three review

- **F1, codec requirements:** `PayloadFormat` callbacks, concrete `NoPayloads`
  callbacks, and `Observer.observe` are readonly function-valued properties.
  `TextObserver.observe` is also a readonly function-valued property, retaining
  its existing operation body.
  Under `strictFunctionTypes`, the model parameter stays contravariant. The
  three-generic constructor still defaults to codec-free `Model`; that default
  now rejects `EncodedPayloads` instead of widening away its `CodecModel`
  requirement. Base `Model` was not changed.
- **F1, declaration regressions:** the strict public declaration consumer has
  independent negative cases for the explicit three-generic encoded constructor,
  encoded-to-base `PayloadFormat` and concrete `NoPayloads` assignments,
  concrete and interface-typed codec-observer widening, and direct base-model
  observation through explicit and inferred encoded observers. Each
  `@ts-expect-error` must be used when the
  parent executes compilation. Positive cases retain explicit/inferred codec
  observation, the default formatter, explicit `NoPayloads`, custom payload
  callbacks, and safe assignment of a base observer to a codec-only consumer.
  The existing oracle/diagnostic error declaration checks remain.
- **F2, forbidden decoders:** declaration schemas in the installed consumer use
  the same counted, throwing `forbidden` callback for decoding as the original
  model hooks. The binder cannot replace a trap with a working decoder. The
  focused declaration-dispatch unit test also traps and counts both decoding
  and decode-limit access. Original bounded/legacy encoder traps remain intact;
  declaration payload bytes still require the public binder.
- **F3, installed inactivity:** destination write and flush calls are counted
  before work or failure. Activity snapshots include these counts, delivered
  bytes, payload callback calls, and forbidden callback calls. The consumer
  asserts that setters, healthy Off observations, the final Off health probe,
  and writer borrowing/extraction do no destination or payload work. After sink
  poison, every subsequent observe/flush operation must fail with `sink_poisoned` and
  leave activity unchanged. Poisoned rows additionally probe an eligible Trace
  transition and flush independently. These assertions run in both `observe`
  and `self-test` modes; they do not rely on the shared expected fields.
  Receipt shape and first-error retention remain unchanged.

## Preserved contracts and reference scope

The source was compared with pinned `src/observation.rs`, `src/monitor.rs`,
`tests/observation.rs`, `tests/monitor.rs`, and `tests/monitor_review.rs`, all
unchanged from the plan baseline `0876717bee85e335449c2c99ada41f6c6e6112d1`.
The relevant contracts are codec-free custom diagnostics, Off inactivity,
health-before-filtering, permanent destination poison, no implicit extraction
flush, and independent bounded recording.

The earlier R1 public oracle error exports and R2 operation attribution,
callback lease revocation, and sticky failure precedence were preserved. The
observation body still performs no initial-state, reducer, checker, or effect
execution. Events remain diagnostic: they grant neither continuity nor checked
authority. `typescript/src/core.ts`, `checked.ts`, and `runtime_recorder.ts`
remain unchanged from this worktree's base; existing periodic checking, sealed
checked tokens, and bounded Recorder are reused. The missing-native-API claims
in the historical `CHECKED-RESULTS-AUDIT.md` do not describe this base.

## Independent review

The first fresh T3 delegated source-only review found a remaining F1 path:
method-style concrete `NoPayloads` callbacks allowed `EncodedPayloads` to
widen through that type. They are now function properties, with an independent
negative declaration case for that assignment. The reviewer also requested
the corrected observe/flush wording above; setters remain allowed and inert
after poison. F2/F3 and preserved R1/R2 behavior received no further objections.

The second fresh TypeScript review returned no new findings. It marked F1–F3,
the concrete `NoPayloads` widening path, and the poisoned-operation wording
source-addressed. It confirmed the positive declaration paths by source
reasoning, preserved R1/R2 behavior, and unchanged checked/periodic/Recorder
sources. No compiler or runtime qualification was performed.

The supplemental read-only review confirmed the parent's mirrored Go/Python declaration decoder
traps and F4 error wording by source inspection. It found one parent-owned P3
instrumentation gap in `examples/native-observation/go/main.go`: non-declaration
`DecodeState`/`DecodeInput` are nil, so attempted decoder use cannot increment
`c.forbidden`; the zero-counter assertion also precedes the final health probe.
The requested follow-up is counted, error-returning direct decoder hooks and a
zero-counter assertion after all final probes. This pass did not edit Go or any
other parent-owned source. No production decoder call was observed.

The fresh reviews use the inherited `codex` / `gpt-6-astra` selection with
`reasoningEffort=ultra` and `serviceTier=default`, verified in the tool returns.
No compiler or runtime result is implied by this note.

T3 review task identities (each round was a new `delegate_task`):

```text
node:delegated-task:command%3Amcp%3A8ca64c4e-5267-4429-a933-06fd23e291e7%3Adelegate-task%3An07-ts-f1-f3-source-review-r1-20261022
node:delegated-task:command%3Amcp%3A8ca64c4e-5267-4429-a933-06fd23e291e7%3Adelegate-task%3An07-ts-f1-f3-source-review-r2-20261022
node:delegated-task:command%3Amcp%3A8ca64c4e-5267-4429-a933-06fd23e291e7%3Adelegate-task%3An07-decoder-trap-consistency-review-r1-20261022
```

## Remaining gates

The parent must freeze the integrated sources, then run package/declaration
compilation, the existing TypeScript suites including the negative declaration
cases and prior oracle/lease regressions, and installed public-package consumer
compilation/execution through Kyoto. Both consumer modes need the source-authored
shared corpus, including declaration cases, Off rows, partial-write poisoning,
flush poisoning, and healthy/poisoned extraction. Compile-only results are not
runtime evidence. Broader cross-language, provenance, checked-recording and
fresh-process suffix gates remain with the parent; this pass does not close N07.
The supplemental Go instrumentation finding above also remains parent-owned.

## Source identities

SHA-256 after these edits; these identify source readings, not a frozen build:

```text
68dd5bacedfd4154b1b7571eebe47be25c3f580ac03adb4f3ffd3dfa3a730ab5  typescript/src/observation.ts
f0290a11167d052890c161b49a2af5c6019cdeeb3e7a1c13f3df31f94eef3bb5  typescript/test/observation.test.mjs
3d2a15e149c53608c8e7be801df5c449d69deced50199cb4cc30e80a655971a1  examples/native-observation/typescript/consumer.ts
c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870  src/observation.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d  tests/observation.rs
c4d9ad75cc0916eb6bd582cf98e22f3194c54df2be3aa52a6505a81b8dd35c0f  tests/monitor.rs
f21ec4778015ad0ace2e54735dee6a6ed37072db918a00ef975854e1bf3042cc  tests/monitor_review.rs
```
