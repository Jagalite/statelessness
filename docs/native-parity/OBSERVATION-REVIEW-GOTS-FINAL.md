# Go and TypeScript observation final source review — round 3

Reviewed 2026-10-22 in `/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`,
base HEAD `54ba3f32796fc5c71458fb4cda14b2ab9290ee83`. The supplied review brief,
`OBSERVATION-REVIEW-PYGO.md` B1/B2, `OBSERVATION-REVIEW-TSSWIFT.md` R1/R2,
`OBSERVATION-REVIEW-PROOF.md` R6, and the completed Go/TypeScript fix notes
were read against actual current source, including the integration contract.

Only this report was written. No implementation edits, staging, commits, push,
PR, implementation execution, fixture generation, formatter, build, compilation,
test, executable qualification check, or Kyoto submission was performed.
No applicable `AGENTS.md` was found in the checkout or its ancestor chain.
The requested `rust-builds-kyoto/SKILL.md` was read. The parent owns the
integrated source freeze and all later Kyoto qualification. Other agents are
working in this shared checkout; the hashes below identify readings, not a seal.

## Findings

One P2 TypeScript declaration defect and three P3 coverage/documentation gaps
remain. No new Go implementation defect was found. The earlier B1/B2/R1/R2
objections and the TypeScript declaration-binding R6 objection are resolved by
source inspection; their regression assertions remain unexecuted.

### F1 — P2: TypeScript public variance permits encoded observation without codecs

`typescript/src/observation.ts:60-63` declares model-consuming `PayloadFormat`
callbacks as methods. Their parameters are bivariant under TypeScript's method
assignment rules. Combined with `TextObserver`'s default `M = Model` at line 265,
this permits a formatter needing `CodecModel` to inhabit an observer accepting
only a base `Model`. The following path needs no cast:

```ts
new TextObserver<S, I, O>(
  writer, 's', {level: LogLevel.Trace},
  new EncodedPayloads<S, I, O>(16),
).observe(baseModelWithoutCodecs, event);
```

An explicitly codec-typed observer can also widen to `Observer<S,I,O>` because
`Observer.observe` is a method at line 47. A Trace transition then reaches the
missing `encodeInput` at `typescript/src/encoding.ts:169`, discarding the record
with `encode_input` while leaving the sink healthy. This contradicts the public
claim that encoded payloads require codecs at the type boundary. The direct
negative call in `typescript/test/observation.test.mjs:554-556` selects
`CodecModel` explicitly and misses both assignment paths.

**Action:** make model-consuming formatter callbacks and observer entrypoints
function-valued properties, or impose equivalent constraints that preserve the
codec requirement through constructor defaults and observer widening under
`strictFunctionTypes`. Retain the base-model path for `NoPayloads` and custom
formatters. Add negative declaration cases for both paths above. This finding
is derived from source and TypeScript variance rules, not a compiler run.

### F2 — P3: installed TypeScript declaration fixtures lose forbidden decoder traps

At `examples/native-observation/typescript/consumer.ts:51`, declaration schemas
provide a working, uninstrumented `decodeFrom: r => r.take(1)[0]`.
`bindModelCodec` at `typescript/src/value_codec.ts:664-676` installs that decoder
behind public `decodeState` and `decodeInput`, replacing the original forbidden
hooks. An accidental observation decode can therefore leave the forbidden-call
counter and expected output unchanged. No decoder call was found in the actual
observer; this is a consumer coverage gap.

**Action:** use `decodeFrom: forbidden` for these schemas. Binding requires the
function's presence; observation should never execute it. Keep the original
encode-hook traps that close R6.

### F3 — P3: installed TypeScript consumer misses forbidden flush activity

The destination `flush` at
`examples/native-observation/typescript/consumer.ts:62` does not count calls.
Lines 108-110 retain only the first error. Extra flush attempts after poisoning
can therefore be hidden behind that first failure. An implicit flush during
healthy `intoInner()` at line 120 also leaves the receipt unchanged. The final
Off health probe checks health, but does not separately assert destination
inactivity. Existing unit tests cover these behaviors; this finding concerns
the installed-consumer proof, not an observed production regression.

**Action:** count destination write/flush calls and separately assert
post-poison operation failures and health/extraction inactivity. Preserve the
fixture's first-error receipt while adding these consumer invariants.

### F4 — P3: the integration table promises a TypeScript diagnostic cause API that does not exist

At `docs/native-parity/OBSERVATION-INTEGRATION.md:72`, the TypeScript diagnostic
error is described as having “stage/cause.” However,
`typescript/src/observation.ts:67-72` constructs `TextObservationError` with a
stage and rendered detail, calls `super(message)` without an Error cause, and
freezes the instance. `contextual` at lines 82-87 renders a thrown value into the
message; it does not retain that value in `cause`. A caller following the table
cannot recover the original callback exception through this error.

**Action:** describe this API as “stage and rendered message/detail” in the table.
Do not conflate it with the preserved oracle `ObservationError`, which does
retain `cause` and the actual transition at `typescript/src/oracle.ts:38-42`.
No implementation change is needed to resolve this documentation finding.

Relevant SHA-256: integration
`213c68572d4e662e86155fd30344c3786dcc02e205c076973fa26ec111355ad9`;
TypeScript observation
`93016a40e258bd5897e836498853ef5d48889b000f066dda8f880a0eb5b912de`.

## Prior finding disposition

- **PYGO B1 / public Go names:** `go/observation.go:143` now declares
  `TextObserverError`; `go/oracle.go:37` retains generic
  `ObservationError[S,O]` and its actual-transition API. New tests and consumers
  use the distinct diagnostic name. The old oracle regression remains intact at
  `go/oracle_test.go:234`.
- **PYGO B2 / typed nil:** `go/observation.go:416-433` creates a nonnil error
  for the current formatter stage and retains a callback's typed nil as an
  opaque cause. The error's own `Error` and `Unwrap` methods tolerate a nil
  receiver. `go/observation_test.go:334` covers every payload kind, typed nil,
  staged public errors, panics, a nested observer, publication/health, writer
  expiry, Off inactivity and recovery, both with and without prior overflow.
- **TSSWIFT R1 / public TypeScript exports:** the explicit diagnostic exports at
  `typescript/src/index.ts:16-17` use `TextObservationError` and
  `TextObservationErrorStage`; the oracle star export retains the original
  `ObservationError`. The actual `WithOracle.observeTransition` regression at
  `typescript/test/observation.test.mjs:57`, strict declaration source at line
  538, unchanged paired regressions, and installed consumer checks at
  `examples/native-observation/typescript/consumer.ts:127` cover the two
  families. Their runtime/type-check success is still pending.
- **TSSWIFT R2 / attribution:** public TypeScript diagnostic construction no
  longer registers authority. Private attribution at
  `typescript/src/observation.ts:76-91` belongs to the active record or payload
  writer operation; the callback lease is revoked before formatting a thrown
  formatter diagnostic at lines 176-194. Foreign, nested and retained errors
  cannot inherit the current operation's attribution. Current sticky record
  errors take precedence. Go uses a private encoding failure bound to the exact
  active writer lease and matching payload kind at `go/observation.go:161-177`;
  the outer payload path checks the sticky record failure before callback error
  or panic. Replaying a prior payload/output/event error cannot preserve its
  encoding attribution. Tests cover these paths; none ran here.
- **Proof R6 / declaration bypass:** TypeScript declaration cases install
  trapping original bounded/legacy codec hooks at
  `examples/native-observation/typescript/consumer.ts:37-52`. The schema callbacks
  alone supply the expected declaration bytes. Omitting the binder reaches
  `forbidden`, and the accumulated counter rejects even `observe` mode at
  line 121. This is a source-derived bypass argument, not an executed mutation
  control. Go constructs the declaration codec directly through
  `OrderedRecord` and `BindModelCodec` at
  `examples/native-observation/go/main.go:186-214`; omitting its installation
  leaves no working codec to satisfy the declaration fixture.

## Additional source review

The Go `preserveErrors` path is limited to observation-created encode batches.
Existing encoding entrypoints retain their old path. The nested declaration,
adapter and double-framing regressions at `go/observation_test.go:663-878`
exercise all payload kinds with opaque errors, panics, typed nil, public stage
spoofing, swallowed or superseded failed writes, swallowed framing failure,
and expired child leases. Copied leases share revocation, and old leases remain
expired during subsequent encodings. The existing distinction between a raw
callback error and a sticky failed write is preserved. The documented exclusion
of legacy `GODEBUG=panicnil=1` is consistent with the unchanged raw value-codec
recovery boundary at `go/value_codec.go:222-230`; it is not a new passing claim.

The observers preserve the pinned Rust ordering: health before Off/filtering,
failure before skip severity, rejected/ignored inputs at ordinary Debug,
payloads and Passed details only at configured Trace, input then outputs then
selected post-state, and explicit checkpoint snapshots under every non-None
policy. Logical record admission includes LF and suppresses later payload work
after failure. Format/codec/bound errors discard the record and remain
recoverable; only destination write/flush failures poison the observer.
No new diagnostic path invokes initial-state, reducer, checker or effect callbacks.

The 36-case corpus was inspected with both consumers. Expectations remain
source-authored; consumers collect actual destination bytes, callback values/order,
the first failure and final Off health instead of constructing results from
expected fields. The two added Trace cutoff cases are consistent with the source:
the ordinary Debug prefix is 84 bytes; ` input=hex:01` adds 13, filling the
97-byte bound exactly. The output field then fails before its codec runs.
The later Debug recovery is 85 bytes including LF. The zero-bound case admits
no payload work and asserts health through flush/Off operations. Literal
hex/text equality and actual five-language results still require qualification.

Both new observation test suites and Recorder integration sources reuse the
existing native checked-token and bounded Recorder APIs. Current
`go/checked_results.go`, `go/recorder.go`, `typescript/src/checked.ts` and
`typescript/src/runtime_recorder.ts` are unchanged from the base. Their private
seals/WeakMaps, full-checking policy, exact sequence checks, metadata/encoded
before-state continuity, state-check phase boundary and first-failure retention
remain authoritative. Public events and detached views grant no such authority.
The historical checked-result audit's missing-native-API statements do not
describe this base.

Integration assertions cover application/check/effect counts across levels,
actual supplied outputs, sampled versus full checking, missing handoffs,
forged diagnostics, long sessions beyond retention, small/rejected-zero
capacities, checkpoint phase promotion, first failure, and suffix replay.
Go's local replay helper is in process. TypeScript additionally contains a
fresh-process replay using the test tree's `dist` entrypoint, which alone does
not establish installed-package provenance. The additive Go/TypeScript
`record-suffix` and `record-failing-suffix` consumer branches were also inspected:
they pass authentic tokens to the existing Recorder and use NoPayloads for
diagnostics. The failing branch checks that detached diagnostics and a repeated
authentic token leave the first-failure artifacts unchanged. Binary and portable
JSON suffix replay branches assert application/check counts. They do not
reconstruct evicted execution from diagnostic records. The parent's broader
suffix/proof-driver review and frozen installed-process qualification remain
separate gates.

The parent's late public-discovery update was included: the only changes in
TypeScript `oracle.ts` and Swift `Oracle.swift` add `observation-v1` to
experimental profile arrays. Their oracle classes and qualified profile
declarations are untouched. Swift implementation review beyond that one-line
discovery diff is outside this report.

## Remaining execution gates

This report approves no build, runtime, installed-package, platform or whole-N07
claim. The parent must freeze integrated sources and qualify through Kyoto:
existing oracle compatibility, both public error declarations, all native
observation/Recorder regressions, installed consumers against the independent
36-case corpus, deliberate binding bypass, callback/lease/bound failure cases,
and fresh-process retained-suffix replay. Preserve source/artifact identities
and failed/superseded receipts; compilation-only results are not executed tests.
Logical byte bounds do not establish RSS, callback-allocation, blocking-I/O or
arbitrary-Unicode Debug compatibility.

## SHA-256 readings

Readings were collected on 2026-10-22 after the late TypeScript/Swift profile
addition. Subsequent edits require comparison with these identities. They are
not an integrated freeze or execution receipt.

```text
c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870  src/observation.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d  tests/observation.rs
c4d9ad75cc0916eb6bd582cf98e22f3194c54df2be3aa52a6505a81b8dd35c0f  tests/monitor.rs
704e559b3d31c660464fb9412f69b0803f1fb41313343e21b9b88ec5adc3ea96  tests/checked_replay.rs
8d9e0fa19796931a2b85368092120e692e9e0f50788b9f36b2f22aa463ab7301  docs/NATIVE-PARITY-PLAN.md
03857afa6f4f320ebd4c2fa95e08a48acb1550432a27020d59ba2d97cc10cdec  docs/native-parity/CHECKED-RESULTS-AUDIT.md
213c68572d4e662e86155fd30344c3786dcc02e205c076973fa26ec111355ad9  docs/native-parity/OBSERVATION-INTEGRATION.md
c1f9ec50b819e03aaedd6f26cd7a7c27c60bde797f30f711f3ca451d3b8c7000  docs/native-parity/OBSERVATION-REVIEW-PYGO.md
e49c875bd78e05fedffc4519490b9498a9ebfe78c84dda50ae38204f85d71b2f  docs/native-parity/OBSERVATION-REVIEW-TSSWIFT.md
16676c58c03d90bfc539f4b05b3291d5fb62b76a0c5debcbf4c3637b4eabdf94  docs/native-parity/OBSERVATION-REVIEW-PROOF.md
8b5fd012a2fd587a7fd2bbc97ebc6fda2704cbbff73ae2fea1ddc5256de97c30  docs/native-parity/OBSERVATION-FIXES-GO.md
d74c3192b181a0d8df1833605ced83119e3c45957333b98f8cf38dd82be0ca78  docs/native-parity/OBSERVATION-FIXES-TS.md
90dbf8fc2f838231c798b6e938af734d0c9dfe98db137cbb2eb94222a3fa2972  spec/observation-v1.md
20d7002957eb05f1d22c25f4a0be613105323fabcd2a49382588e6bc9ae7e8a8  conformance/observation-v1.json
c115f1a83de90378459e615474f891fefbc1030c0225d756a391cc37454f02d9  go/observation.go
be1e85d7e7b324bb6c1aae32a2fb531d1b58db34d3bf92f357695f22175be23d  go/encode_buffer.go
a137d25177eecca9c80dd66d81e05c8629393410b26f77c752fc3a4b6382ba79  go/observation_test.go
086a452a73331b778db43da932fe7333c6db48cee6314ab560d785fbfde43c9d  go/observation_recorder_integration_test.go
c53267f22ead963b337253219f564053fcd28a08d235eddc11974aa4949a4905  go/oracle.go
f84f4ade9e147036eb7ac74f19d13ca44e91c02b27a3215e85ad923ad73c170c  go/oracle_test.go
07f6571cf2260f7f4d5acdccc4aaa33960a5b781c38bee1b279f6ca7b73ef921  go/model_codec_bind.go
91c7fe2ec9827a0b0cd9274f2f336bb17a2c223951acbcf737a0e473ac1baea3  go/value_codec.go
9f9172b1587a490afb8bcf867b11a2789bc3ae0eb07899d0a4cb5f61d15832c5  go/checked_results.go
910f44bbc0e63830a4867efc57fa7cfc022a8140a050c5b9be5abfea4bee19d7  go/recorder.go
d76a48053d9992e75f0507193bf9f98470d63c4b0aa1241bbd01ad303d6aabbd  go/README.md
168926e5a837a65c7da706525440c876afc6ac066062f8b9d3107e05685a1b1c  examples/native-observation/go/main.go
f495abd9168aa0976047b3760032e13f2fe87a3254f960fdb99c92bb9d307c10  examples/native-observation/go/README.md
93016a40e258bd5897e836498853ef5d48889b000f066dda8f880a0eb5b912de  typescript/src/observation.ts
f6d5650ace4ad88f1a74db6df3f279e7d0904484432e579a82ee7a3a0c0a9b47  typescript/src/index.ts
d559430a9a9802a0f07ffa13b28cf61ff49e74aad12e354d817fb7c3d6f45a1c  typescript/src/oracle.ts
cdf3f58810813dcb555a9479c2a65962f12ca5a6290659e50a41b6b7b9e7f93b  typescript/src/encoding.ts
ee3df47d3b03c09489343184aa2ce2917f29348119dd605b55003aee727ce45d  typescript/src/checked.ts
5aeda96d9d961d04632361b017d1ab841a1a3f260cf63c36c1a8be88bab7e732  typescript/src/runtime_recorder.ts
ec8ec4ed706f546d566409b51af843b15cf3bbb4af0afc085638874dc506c48e  typescript/test/observation.test.mjs
326af3f3b8c83b97dd9638636948eba17842db247aeea507e4048051ff8a092d  typescript/test/observation-recorder-integration.test.mjs
d9a8f169bc78eabb6c0a565bc47e37ec798b37e3d4b3a600a8da2aa2872ba9c5  typescript/test/paired.test.mjs
cadba048ec8f99caccf95769f8621182a619b3c9867397edef5d4a612afe7575  typescript/README.md
17273c32ca9e018727f5a6c05254d792645116c4f241ff9286b0ecaad768d2a4  examples/native-observation/typescript/consumer.ts
899def604a2339aa3e36386b412c1b1c8917b93ededd2383524eb4d44308ad6e  examples/native-observation/typescript/README.md
7990e2fc69e4c38d363eecce2ad300d68967f9b3e4c40a8657844eec5a8d85cd  examples/native-checked/go/main.go
9ed3ead9272b0e0bf4eacdf3888811ce2e1fc2d2760d83c76ff8f52ebb4501ab  examples/native-checked/typescript/consumer.ts
727b637d111bf43ce06345c359923bf23d8398516c7d3745d31aec5f433c5270  swift/Sources/Statelessness/Oracle.swift
f21ec4778015ad0ace2e54735dee6a6ed37072db918a00ef975854e1bf3042cc  tests/monitor_review.rs
d786c37d2d623090750d54495e4eb420ad764b0859108f92eb08db611eb6259c  typescript/src/value_codec.ts
10124e42e1e377a3b4e907e3800b14c6c4f711cf2595805c9a56d72978e9d865  examples/native-observation/suffix-failure-contract.json
```
