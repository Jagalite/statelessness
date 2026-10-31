# Final Go consumer decoder-trap review

Source-only review, 2026-10-22, in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca` at base
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`. This pass owns only this new note.
The scope is the supplemental Go instrumentation finding in
`OBSERVATION-FIXES-TS-FINAL.md`, with the Python decoder instrumentation checked
for consistency. No general feature audit is claimed.

**Result: no remaining issue found in the requested repair.**

- `examples/native-observation/go/main.go:222-226` now assigns both direct
  `DecodeState` and `DecodeInput` to an error-returning callback that increments
  `c.forbidden` before returning. These match the actual callback signatures in
  `go/model.go:102-111`; neither direct decoder is nil. The counted legacy
  encoder traps and bounded encoder callbacks remain intact.
- `main.go:186-198,214-217` retains the counted, error-returning declaration leaf
  decoder. `go/model_codec_bind.go:24-32,52-65` binds state/input decoding through
  their supplied schemas; the consumer has not substituted working leaf decoders.
- `main.go:377-394` retains the earlier zero-counter assertion and adds a final
  assertion after `IntoInner`. It checks `c.forbidden == 0`, unchanged writer
  write/flush counts and payload callback count, and the original writer's
  identity. Counts are captured before `SetLevel(Off)`, so the final assertion
  covers that setter, the Off health probe, health inspection, and extraction.
  A swallowed forbidden-hook error in those operations still leaves a nonzero
  counter. The health probe also rejects unexpected error stages and disagreement
  with `IsPoisoned`. Both consumer modes run these assertions before returning
  results or consulting expected fields (`main.go:417-418`).
- The mirrored Python decoder instrumentation is present at
  `examples/native-observation/python/consumer.py:25-32,59-71`: direct hooks and
  declaration adapters share the counted throwing decoder. The zero-count
  assertion at lines 173-174 follows both the Off probe and writer extraction.

The current Go observer checks health before Off filtering and returns the writer
without flushing (`go/observation.go:287-310,371-376`). This agrees with pinned
`src/observation.rs:334-340,371-375` and the relevant Off/poison and shared-check
cases in `tests/observation.rs:171-201,263-305,308-342`. Reviewed monitor boundaries
include `src/monitor.rs:306-344`, `tests/monitor.rs:269-280`, and
`tests/monitor_review.rs:114-144`. All five Rust reference files are unchanged from
plan baseline `0876717bee85e335449c2c99ada41f6c6e6112d1` by scoped source diff.
Events remain diagnostic and grant neither continuity nor checked authority.

Read N07 in `docs/NATIVE-PARITY-PLAN.md` and the historical
`CHECKED-RESULTS-AUDIT.md`. Actual Go checked tokens, periodic checking, and bounded
Recorder already exist in `go/checked_results.go`, `go/model.go`, and
`go/recorder.go`; these and the model binder are unchanged from the worktree base.
This review neither replaces them nor closes N07.

Read `/Users/jagatranvo/.codex/skills/rust-builds-kyoto/SKILL.md`; no applicable
`AGENTS.md` was found in the worktree or its ancestors. No implementation,
fixture generator, build, test, compiler, executable qualification check, or Kyoto
job was run or submitted. No production or fixture files were changed. Nothing
was staged, committed, pushed, or opened as a PR. The parent's integrated source
freeze and shared Kyoto qualification remain pending; this is source evidence only.

SHA-256 of reviewed sources (not a frozen build):

```text
683c0909f8825d65a28f1907670db1da3aaead1c904f34de0f9f845662ec7565  examples/native-observation/go/main.go
9ebaccc1e0a73266abe17732bed1a3e093d255c787011bacaa219433afa47406  examples/native-observation/python/consumer.py
56b5d71946902ab9ec0266e6eac38d3e4823cac89b6d79229cfca63549773655  go/model.go
07f6571cf2260f7f4d5acdccc4aaa33960a5b781c38bee1b279f6ca7b73ef921  go/model_codec_bind.go
c115f1a83de90378459e615474f891fefbc1030c0225d756a391cc37454f02d9  go/observation.go
c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870  src/observation.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d  tests/observation.rs
c4d9ad75cc0916eb6bd582cf98e22f3194c54df2be3aa52a6505a81b8dd35c0f  tests/monitor.rs
f21ec4778015ad0ace2e54735dee6a6ed37072db918a00ef975854e1bf3042cc  tests/monitor_review.rs
```
