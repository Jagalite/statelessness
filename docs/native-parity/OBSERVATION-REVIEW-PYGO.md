# Python and Go observation review — round 1

Source review on 2026-10-22 in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, HEAD
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`. Implementers were editing concurrently;
the hashes below identify the reviewed readings, not an integrated source seal.
The final implementation/test readings were taken after the Go formatting pass
and the formatter-stage test correction.

No implementation, test, build, compiler, package installation, or qualification
script was executed, and no Kyoto job was submitted. No fixtures were generated.
The reviewer changed only this document and made no commit. No applicable
`AGENTS.md` was found in the checkout or its ancestor chain. The requested
`/Users/jagatranvo/.codex/skills/rust-builds-kyoto/SKILL.md` was read; execution
remains with the parent's frozen integrated Kyoto qualification.

## Blocking findings

### B1 — P1: the new Go error type redeclares an existing public package type

At `go/observation.go:143`, `type ObservationError struct` conflicts with
`type ObservationError[S, O any] struct` at `go/oracle.go:37`. Both files belong
to package `statelessness`, with no build constraints separating them. The new
`Error`/`Unwrap` declarations also use the same receiver type name. This is a
static package compilation blocker, affecting existing consumers as well as the
new consumer and tests; no compiler was run to obtain this conclusion.

Preserve the existing generic oracle error and its actual-transition retention
API. Rename the new diagnostics error, for example to `TextObserverError`, and
update its uses in `go/observation.go`, both observation test files,
`examples/native-observation/go/main.go`, and the new Go README section. Existing
uses such as `go/oracle_test.go:250` must continue to refer to the generic oracle
error. Do not resolve the collision by deleting or renaming the existing API.

Relevant SHA256: `go/observation.go`
`76d43ae868ddfebbe2c1f7b85364a6192a494b2a3c0e6d46330e9d8e868a708e`;
`go/oracle.go`
`41ce83c231b85db49569067986eb487c67d83e964cf2344a6fdb2439d54bdcc5`.

### B2 — P2: a typed-nil diagnostics error escapes without safe stage attribution

`go/observation.go:394` returns any `*ObservationError` from a payload callback
unchanged, including a nil pointer stored in a nonnil `error` interface. The
same forwarding occurs at line 369. For a formatter that returns
`var failure *ObservationError; return failure`, the resulting `Observe` error
is itself a typed nil. Its `Error()` and `Unwrap()` methods at lines 148–149
dereference the nil receiver, so ordinary error inspection panics instead of
reporting the active `format input/output/state` stage. A preceding swallowed
record overflow can also lose its meaningful cause through this forwarding path.

This source-derived failure path remains relevant after B1's type rename; it
has not been executed. Forward an attributed error only when the asserted pointer
is nonnil, otherwise retain it as an opaque cause inside a newly allocated error
for the active stage. Preserve a pending record failure when applicable. Add a
typed-nil callback case asserting safe `Error`/`Unwrap`, no publication, lease
expiry, and successful next-event recovery. The current hostile-error tests cover
other error types but do not cover this special forwarding case.

Relevant SHA256: `go/observation.go`
`76d43ae868ddfebbe2c1f7b85364a6192a494b2a3c0e6d46330e9d8e868a708e`.

## Nonblocking findings and remaining gaps

- **Nested Go encoding failures need explicit regression coverage.**
  `go/observation_test.go:429` exercises declaration success and version rejection;
  the ordinary hostile-codec cases at line 377 do not exercise hostile errors,
  panics, swallowed writes, and expired leases through declaration field adapters
  and `EncodeBuffer.Framed` under `preserveErrors`. The shared declaration fixture
  is also a success case. Include these in the eventual integrated regression
  source, keeping raw callback errors distinct from sticky failed writes.
- **Additional Go nil/panic cases remain untested source scenarios.** Nil clone
  callbacks, typed-nil formatter/writer interfaces, and `panic(nil)` are absent
  from the focused observation tests. B2 is a concrete defect; these other
  omissions alone are not findings that the implementation fails. If legacy
  `GODEBUG=panicnil=1` behavior is in scope, the `recover() != nil` wrapper at
  `go/observation.go:317` also needs a completion flag or a documented exclusion;
  the current module declares Go 1.23 and ordinary modern `panic(nil)` behavior
  must not be conflated with that compatibility setting.
- **Python final Off health probe measures only payload callbacks explicitly.**
  `examples/native-observation/python/consumer.py:137` checks the callback list
  and probes the error stage correctly. Unlike the Go consumer, it does not
  snapshot destination write/flush call counts around that probe. Adding these
  counters would detect an erroneous zero-byte write or unnecessary flush even
  when delivered bytes remain unchanged. Current observer source returns before
  such work; this is a measurement gap, not a demonstrated implementation fault.
- **Go profile discovery is incomplete.** `go/oracle.go:11` does not yet include
  `observation-v1` in `ExperimentalProfiles()`, whereas Python appends that
  candidate profile in `python/src/statelessness/__init__.py:97`. Keep it
  experimental if added; this is not authorization to change qualified profiles.
- **All qualification remains open.** Package compilation, test execution,
  installed-package resolution, actual five-language bytes/callbacks/error stages,
  and source-bound receipts require the parent's later integrated run. Source
  assertions, including newly added fresh-process Python replay assertions, are
  not executed evidence.

## Findings resolved while this review was in progress

The initial Go integration test at `go/observation_recorder_integration_test.go:582`
(SHA256 `62afe4f600583093ee051b2fd0fea4f71f7cc0c32ac37bd54babdf79a11cbbd2`)
left `wantStage = "format"` for `format_input`, `format_output`, and `format_state`.
The implementation returns the payload-specific stage. The final inspected file
sets `wantStage = "format " + payloads.fault` at line 594, so this objection is
resolved in source. This is not a passing test result.

Python's final inspected built-in payload formatters use `_append_all`, including
direct public calls. The earlier direct-short-write concern is therefore not an
open finding. Its source regression at `python/tests/test_observation.py:306`
covers short/interrupted direct writes for all three payload kinds. No execution
was performed.

## Reviewed behavior and authority boundaries

No additional Python production blocker was found in the final inspected source.
This statement is limited to source review and is not package/type/runtime
qualification. Python preserves the existing top-level model `Transition` and
oracle `ObservationError`, exporting diagnostics as `TransitionEvent` and
`TextObserverError`. Models remain usable without codecs through `NoPayloads`.

The old checked-result audit is historical. Current Python `checked.py` and
`monitor.py`, and Go `checked_results.go` and `recorder.go`, already provide
authentic tokens, policy coverage, bounded recording, origins, continuity guards,
and coherent failure retention. Their tracked files are unchanged from the base
in the inspected diff. New diagnostic events do not enter the private token
stores/seals. No added observation path invokes reducers, checkers, effects, or
initializers. Custom callbacks remain application code, not sandboxed authority.

Filtering, failed-before-skipped severity, ordinary rejected/ignored Debug
severity, configured-Trace payload/check detail, input/output/post-state order,
explicit checkpoints, and absolute periodic selection match the inspected Rust
control flow and the shared finite fixture contract. Both implementations retain
sticky record failures through the final LF. Bounds are checked before copying
incoming bytes into retained record/codec storage. These are logical bounds,
not callback-allocation, allocator-capacity, RSS, or blocking-I/O limits.

Python `_write_all` at `python/src/statelessness/observation.py:439` snapshots the
record with `bytes(self._record)` before `_append_all` exposes readonly slices.
Retained destination memoryviews consequently refer to immutable per-record bytes,
not the reusable bytearray. The retained-writer regression covers two events.
Revoked formatter leases cannot append to a later record through their public
writer methods. Destination failures set poison before formatting the exception;
Off observation still checks health. Formatting/codec failures remain recoverable.

Python completes valid short writes and retries `InterruptedError`. Go treats a
short nil-error `io.Writer` return as `io.ErrShortWrite`; partial bytes plus an
error are not retried. This is an appropriate native distinction. Custom formatter
bytes may contain raw newlines or invalid UTF-8; neither implementation provides
sanitization for those bytes. The current docs state the narrower shared text and
Unicode compatibility claim.

The Go `encodeBatch.preserveErrors` change at `go/encode_buffer.go:143` is confined
to observation-created batches. Existing constructors leave it false and retain
the original `call` path. Observation model dispatch, child leases and framing
share the batch, its bound, and its first sticky failure. Existing declaration
`EncodeRaw` retains its distinction between a returned callback error and a failed
write; the enclosing observation invocation still checks final batch failure.
No obvious bounds or legacy-caller behavior change was found in that diff apart
from the package-level compilation blocker introduced by the added file.

Both native consumers are now present and were read. They use public APIs and
actual declaration binding, record the supplied payload values inside callbacks,
retain the first error, collect bytes from their destination writer, and probe
final health at Off. Neither constructs its observed result from expected fields
or clears captured output after a failure. `self-test` alone compares expected
results. Go additionally counts forbidden model work and writer calls around its
health probe. Python's fixture model raises on forbidden execution/check paths.
The shared corpus remains 34 source-authored cases; no implementation was run
to generate or validate those expectations during this review.

The integration tests use real checked tokens and Recorder. They compare
application/check/effect counters, actual supplied outputs, authentic versus
forged diagnostics, full versus sampled coverage, retained checkpoints, first
failure preservation, and replay through the existing APIs. Go replay assertions
are in process. Python now additionally invokes a fresh interpreter with serialized
evidence at `python/tests/test_observation_recorder_integration.py:276`; it uses
the test process's package search path, so it is not by itself proof of an
independently installed wheel. The separate installed consumer/qualification
driver must establish package provenance.

## Final source identities

SHA256 readings below were taken on 2026-10-22 at approximately 16:03–16:07 UTC.
Any subsequent source change requires another review against its own hashes.
There is no missing Python or Go consumer implementation at this checkpoint.

```text
c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870  src/observation.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d  tests/observation.rs
c4d9ad75cc0916eb6bd582cf98e22f3194c54df2be3aa52a6505a81b8dd35c0f  tests/monitor.rs
704e559b3d31c660464fb9412f69b0803f1fb41313343e21b9b88ec5adc3ea96  tests/checked_replay.rs
da04cfbaa4a268a9d5f7857a5c32b833230b5325b3777a3404d6065c6939d8c7  spec/observation-v1.md
772b3e871abd564d1be221a7b07af3536d1756e8a11bbd7a3ee55caa843a53d8  conformance/observation-v1.json
fadda826292a5fe55fe1391d4e552d6a13e095bbcfc27a52784f8b581e48d068  python/src/statelessness/observation.py
c0e646b838028d1a8d95a3a1fc172ed1d32810f86c787c8935f723d9b3515b80  python/src/statelessness/__init__.py
d3f49de2cda63b0b6c4031809b74235b773ee59ad9a65be7fc72029e83edf397  python/src/statelessness/core.py
d2763cf6b78423d8abfcab6efe84c68ebced60cacc22a92f5c3556b8fa75a02a  python/src/statelessness/codec.py
7e5c0bc9fb837121b88e6ec9728b6c787ee58c37cc22245bda18d1442e98034f  python/src/statelessness/codec_declarations.py
5a450a24632d2f304d3714cccd1723654d568550d92d47f7ff38d7f8568e0b70  python/src/statelessness/checked.py
034e6f479504860d727a5354245ced7230675f04d10592530eba047eb9120493  python/src/statelessness/monitor.py
48df813911c8de3f20461b874edbdb62c1d4a9f5c093757ecae59c444b1f1ea6  python/tests/test_observation.py
b202ce22f3087c06b40a914b1d81863f1057440927f2611a8670b68e06402ae3  python/tests/test_observation_recorder_integration.py
bfbc211b25ea8f49e29905672030cf11a972a1ce08b408e1fef8cd6edfce7628  python/README.md
efeda2e83efc5dadfb897d5359654c171f92efae0962fca5f86e77a313e97778  examples/native-observation/python/consumer.py
a851648ab94a5c522ea2d3b79e90a22aa6a02ce06e026c375dd72aaacee6521d  examples/native-observation/python/README.md
76d43ae868ddfebbe2c1f7b85364a6192a494b2a3c0e6d46330e9d8e868a708e  go/observation.go
be1e85d7e7b324bb6c1aae32a2fb531d1b58db34d3bf92f357695f22175be23d  go/encode_buffer.go
20227f641acc7916e6d8e410034648562dd1e14fb2d3661999b08a1b7af25280  go/observation_test.go
f7130bd42a6d396d5cb05459f983d7c54f39bf4d5c94127d2c0f8cda46ee0c11  go/observation_recorder_integration_test.go
41ce83c231b85db49569067986eb487c67d83e964cf2344a6fdb2439d54bdcc5  go/oracle.go
56b5d71946902ab9ec0266e6eac38d3e4823cac89b6d79229cfca63549773655  go/model.go
07f6571cf2260f7f4d5acdccc4aaa33960a5b781c38bee1b279f6ca7b73ef921  go/model_codec_bind.go
91c7fe2ec9827a0b0cd9274f2f336bb17a2c223951acbcf737a0e473ac1baea3  go/value_codec.go
9f9172b1587a490afb8bcf867b11a2789bc3ae0eb07899d0a4cb5f61d15832c5  go/checked_results.go
910f44bbc0e63830a4867efc57fa7cfc022a8140a050c5b9be5abfea4bee19d7  go/recorder.go
137925dd33d21c05552c53e052e662e6e1b6d53dba8ac3e94e649140a269861b  go/README.md
a4ddba022ebb504214c5c00f1bfb6600fe0b4325ad8ba28ccf264ee79791ac4c  examples/native-observation/go/main.go
62b8cf9b560b7f0021f48cbdec584c8cb43ebb9fb17a9c0f2d5811e8f4d0dfd8  examples/native-observation/go/README.md
8d9e0fa19796931a2b85368092120e692e9e0f50788b9f36b2f22aa463ab7301  docs/NATIVE-PARITY-PLAN.md
03857afa6f4f320ebd4c2fa95e08a48acb1550432a27020d59ba2d97cc10cdec  docs/native-parity/CHECKED-RESULTS-AUDIT.md
27bb580488221fbbf8887f3a107b36991648839ffcfa1de47b393d12f19f252c  docs/native-parity/OBSERVATION-INTEGRATION.md
```
