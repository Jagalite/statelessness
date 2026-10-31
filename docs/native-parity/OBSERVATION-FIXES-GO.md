# Go observation corrections — round 2

Source-only changes on 2026-10-22 in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, base HEAD
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`. This is a correction note for
[B1/B2](OBSERVATION-REVIEW-PYGO.md) and the callback-stage provenance issue
[R2](OBSERVATION-REVIEW-TSSWIFT.md). It is not an integrated source seal,
compilation result, test receipt, or N07 completion claim.

No applicable `AGENTS.md` was found. The requested
`/Users/jagatranvo/.codex/skills/rust-builds-kyoto/SKILL.md` was read. No build,
test, compile, formatter, executable qualification check, implementation run,
fixture generation, or Kyoto job submission was performed. No staging, commit,
push or PR was performed. All execution remains with the parent's integrated
frozen-source Kyoto qualification. Unrelated shared edits were preserved.

## Corrections

- **B1:** renamed the diagnostic error to `TextObserverError` in
  [observation.go](../../go/observation.go), both observation test files, the
  installed observation consumer, and documentation. The existing generic
  `ObservationError[S,O]` and its retained actual transition remain unchanged.
  The sole edit to [oracle.go](../../go/oracle.go) adds `observation-v1` to
  `ExperimentalProfiles()`; qualified profile declarations are unchanged.
- **B2 / R2:** callback-supplied public errors are opaque causes under the active
  `format input/output/state` stage. This includes typed-nil errors, fabricated
  encode/write/flush/poisoned stages, and failures propagated from another
  observer. The observer allocates its own nonnil diagnostic wrapper without
  invoking arbitrary error methods. `TextObserverError.Error` and `Unwrap` also
  tolerate a nil receiver.
- Internal encoding provenance uses a private failure value tied to the exact
  active record-writer lease and payload kind. Only the matching lease can
  preserve `encode input/output/state`. A different writer, observer, payload,
  output slot, or earlier event cannot transfer that provenance. Direct public
  `EncodedPayloads` calls still return public diagnostic errors. The internal
  `format`/`payload` return types eliminate forwarding of public callback error
  stages at the outer `Observe` boundary.
- Sticky record failure is checked before considering any callback error or
  recovered panic, so it remains `format` with `ErrObservationRecordLimit`.
  Existing bounded encoding and `preserveErrors` dispatch retain the first batch
  failure. This round does not change `encode_buffer.go` beyond the observation
  work already present at entry.
- The observation callback boundary now detects completion explicitly. Direct
  nil panics are contained without inspecting or formatting the panic value.
  Format/encode errors publish no record and leave the destination recoverable;
  only actual destination write/flush errors poison it.

## Added regression source

[observation_test.go](../../go/observation_test.go) now covers both public error
families, experimental profile discovery, all three payload stages with public
staged errors and typed nil, panic/nil-panic handling, nested-observer failure,
sticky record-limit precedence, callback stop counts, zero destination calls,
lease expiry, an inert Off health probe, and next-event recovery. Internal codec
failure replay tests distinguish the active call from another payload kind,
another writer, a previous payload, the previous output of the same event, and a
previous event.

Nested `BindModelCodec -> OrderedRecord -> OrdinaryField -> OrderedRecord ->
AdapterField -> Framed -> Framed` cases exercise `preserveErrors` for input,
output and state. They return/panic with hostile errors, return typed nil or a
spoofed public stage, swallow a failed append, replace a failed append with
another error/panic, swallow a failed framed call, and return an expired child
lease error. Assertions retain the first cause, stop later payloads, suppress
publication, and recover on the same observer. The two frame lengths and payload
byte in the recovery assertion are source-authored constants, not generated
expected output.

Retained and copied adapter/framing leases reject append, finish, snapshot,
length and framing after return, including while the next encoding is active.
A separate nested raw callback case preserves the existing distinction: ignoring
a returned `EncodeRaw` callback error may succeed; ignoring a failed write may
not. The hostile fixture tracks `Error`, `String`, `Format`, `Unwrap`, `Is` and
`As`, all of which must remain uncalled by the observer.

[observation_recorder_integration_test.go](../../go/observation_recorder_integration_test.go)
was updated only for the diagnostic type name. Its existing authentic-token,
periodic-policy, Recorder, continuity, retention and replay assertions remain in
place. The observation consumer uses the renamed type and rejects a nil asserted
pointer before reading its stage.

## Preserved contracts and remaining gate

The implementation was checked by source inspection against pinned
[observation.rs](../../src/observation.rs), [monitor.rs](../../src/monitor.rs),
[observation tests](../../tests/observation.rs), monitor tests, and
[N07](../NATIVE-PARITY-PLAN.md#n07-runtime-observation-and-bounded-recording).
[CHECKED-RESULTS-AUDIT](CHECKED-RESULTS-AUDIT.md) was treated as historical:
current [checked_results.go](../../go/checked_results.go) and
[recorder.go](../../go/recorder.go) already implement native checked tokens,
periodic coverage and bounded Recorder behavior and remain unchanged. Diagnostic
events do not acquire checked authority or continuity guarantees. No diagnostic
path was added to reducer, initializer, check or effect execution.

One existing compatibility limitation is explicitly documented in both Go
READMEs: with `GODEBUG=panicnil=1`, the raw value-codec callback boundary in
`value_codec.go` can recover a nil panic without reporting an error. That file is
outside this round's ownership. The observation profile targets default Go 1.23
panic behavior and excludes that compatibility setting; the outer observer
cannot detect a panic already swallowed inside a callback. This is a source
finding, not an executed reproduction.

All new assertions are unexecuted. The parent must still qualify package
compilation, existing oracle compatibility, focused and Recorder integration
tests, installed consumer resolution, shared fixture bytes/callbacks/stages and
final sink health against its final frozen integrated source identity.

## Source identities

SHA-256 readings after the corrections below identify this Go source handoff,
not an integrated qualification seal. Subsequent edits invalidate these readings.
`encode_buffer.go` and `value_codec.go` are included to identify the preserved
encoding paths; neither was edited in this correction round.

```text
c115f1a83de90378459e615474f891fefbc1030c0225d756a391cc37454f02d9  go/observation.go
be1e85d7e7b324bb6c1aae32a2fb531d1b58db34d3bf92f357695f22175be23d  go/encode_buffer.go
a137d25177eecca9c80dd66d81e05c8629393410b26f77c752fc3a4b6382ba79  go/observation_test.go
086a452a73331b778db43da932fe7333c6db48cee6314ab560d785fbfde43c9d  go/observation_recorder_integration_test.go
c53267f22ead963b337253219f564053fcd28a08d235eddc11974aa4949a4905  go/oracle.go
d76a48053d9992e75f0507193bf9f98470d63c4b0aa1241bbd01ad303d6aabbd  go/README.md
168926e5a837a65c7da706525440c876afc6ac066062f8b9d3107e05685a1b1c  examples/native-observation/go/main.go
f495abd9168aa0976047b3760032e13f2fe87a3254f960fdb99c92bb9d307c10  examples/native-observation/go/README.md
91c7fe2ec9827a0b0cd9274f2f336bb17a2c223951acbcf737a0e473ac1baea3  go/value_codec.go
c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870  src/observation.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d  tests/observation.rs
9f9172b1587a490afb8bcf867b11a2789bc3ae0eb07899d0a4cb5f61d15832c5  go/checked_results.go
910f44bbc0e63830a4867efc57fa7cfc022a8140a050c5b9be5abfea4bee19d7  go/recorder.go
```
