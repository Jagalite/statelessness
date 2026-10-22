# TypeScript / Swift observation independent source review

Review round 1, 2026-10-22. Base HEAD:
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`.
Worktree: `/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`.
This report concerns the working files identified below, not a frozen integrated
commit. Implementers were editing concurrently; changed files were reread before
the final hash collection. Another review of the resolved, frozen sources is
required. No build, compilation, test, implementation execution, fixture
generation, Kyoto submission, commit, push, or PR was performed by this reviewer.
Only this report was written. No applicable AGENTS.md was found; the requested
rust-builds-kyoto skill was read. All executable qualification remains with the
parent after its shared source freeze.

**Blocking findings**

1. **R1 / P1 — The new TypeScript package export replaces the existing oracle
   `ObservationError`.** `typescript/src/index.ts:16` explicitly exports the new
   class from `observation.ts`, while line 2 already exports the existing
   `oracle.ts:38` class under the same public name. An explicit ESM export takes
   precedence over a star export. `WithOracle.observeTransition` still throws
   the old class at `oracle.ts:77`; an application importing `ObservationError`
   from the package now receives the unrelated diagnostic class. Its existing
   `instanceof` handler no longer recognizes oracle-advance failures or their
   retained actual transition. Existing source assertions already expose this:
   `typescript/test/paired.test.mjs:131` and `:148`, imported from the public
   entrypoint at line 4. Generic type/constructor consumers of the old error also
   receive an incompatible declaration. This is a public API regression, not a
   proven compiler error. **Fix:** preserve the existing package export and give
   the diagnostic error a distinct public name, such as `TextObservationError`;
   update only the new diagnostic consumers, tests, and documentation. Keep the
   paired tests intact and qualify both error families from the installed package.
   Source identities: index `a4956790…664329`, observation `2755a585…5315b`,
   oracle `25e7ff8f…9b19ea` (full hashes below).

2. **R2 / P2 — Both observers trust callback-supplied error stages as their own
   failure provenance.** TypeScript `observation.ts:67–84` registers every public
   `new ObservationError(...)` in `errors`; `:338` passes any registered exception
   through unchanged. Swift `Observation.swift:94–104` exposes an unrestricted
   public `TextObservationError` initializer, and `:291` likewise rethrows any
   such error. A custom **input formatter** can throw
   `new ObservationError('sink_write', 'foreign')` or
   `TextObservationError(stage: .write, detail: "foreign")`. The outer observer
   then reports a sink-write failure although it never called its writer, its
   sink is healthy, and the next event can recover. A normal formatter that
   delegates to a different observer and propagates that observer's error has
   the same problem without deliberately inventing an error. It can also
   misattribute input failure as output/state encoding or poison. The existing
   hostile-value tests do not exercise public staged errors originating outside
   the current operation. **Fix:** separate internal, operation-bound record /
   codec error attribution from public error values. Preserve a record or codec
   stage only when it originated in the current formatting operation; contextualize
   other callback failures as the actual `format_input/output/state`. Add direct
   and nested-observer regression cases asserting the returned stage, zero outer
   writer calls, final health, and recovery. Do not mark the outer sink poisoned
   merely to make a forged stage agree with health. Source identities:
   TypeScript `2755a585…5315b`, Swift `d86054c1…8244e3`.

**Nonblocking findings, resolved concerns, and remaining gates**

- **Swift SnapshotPolicy equality: resolved in the inspected candidate.**
  `Observation.swift:27–39` now stores `allTransitions` separately, so synthesized
  Equatable distinguishes `try .every(1)` and `.everyTransition`. The selection
  behavior remains the same for those two policies, as in Rust, while variant
  identity remains distinct. `ObservationTests.swift:87` contains the inequality
  regression. No explicit contract exception is needed with this fix. It has
  not been compiled or executed here.

- **Existing Swift Recorder P2 discrepancies remain wider N07 gates.** These
  are unchanged base code, also recorded in `OBSERVATION-SOURCE-AUDIT.md`, not new
  diagnostic implementation defects. At `Recorder.swift:46–60`, checkpoint
  encoding and ordinary state checking precede final budget validation; Rust
  validates `reserved_end_size` at `src/monitor.rs:213` before those callbacks.
  With `maxFrameBytes = 12`, a throwing encoder can win in Swift where Rust
  rejects the impossible reserved footer first. Validate that footer before
  encoding/checking and test competing errors/callback counts. Separately,
  `Recorder.swift:204` combines `maxOutputsPerStep` and `maxItems` under
  `"outputs per step"`, whereas Rust `monitor.rs:424–428` distinguishes them.
  A supplied 65-output turn with `maxItems = 64` and
  `maxOutputsPerStep = 128` should report aggregate items. Split the checks in
  Rust order. A parent-approved explicit open gate is an alternative to expanding
  this observation patch; neither discrepancy should be hidden by a full N07
  completion claim.

- **Swift arbitrary-byte payload coverage is still narrower than its public
  API.** `ObservationTests.swift:200` covers multiline String formatting; the
  successful non-UTF-8 byte path is not explicitly asserted. The implementation
  appends byte arrays directly by inspection. Add a source-authored case such as
  `[0xff, 0x00, 0x0a]` and compare destination bytes, including exact/one-short
  record bounds. Avoid decoding with replacement into String. This is a P3
  coverage gap, not an observed implementation failure. TypeScript does contain
  a successful raw-0xff test and long UTF-8 scratch-boundary tests.

- **The earlier missing integration-test concern is no longer current.**
  Both `typescript/test/observation-recorder-integration.test.mjs` and
  `swift/Tests/StatelessnessTests/ObservationRecorderIntegrationTests.swift` were
  present by the end of this review. They use the actual checked token, Recorder,
  codec, and replay APIs. Their source includes uninstrumented/Recorder-only/logged
  comparisons, execution and effect counters, long retained windows, checkpoint
  phase boundaries, origins, sampled checking rejection, missing-turn rejection,
  failure freeze, logging failures, and continuation. These are candidate tests,
  not results. TypeScript also contains a fresh-process suffix replay; current
  installed Swift fresh-process replay remains a parent qualification gate.

- **Compilation and platform qualification remain open.** No definite new Swift
  generic-access, exclusivity, or concurrency compilation defect was found by
  inspection. That is not a successful Swift build. `TextObserver` is deliberately
  non-Sendable; its active guard prevents writer access/transfer during callback
  mutation, and each event captures its options. The TypeScript package exports
  and installed consumer need strict compilation after R1 is resolved. Browser
  safety is supported by the observation module's lack of Node imports and use
  of standard TextEncoder/typed arrays; no browser-host execution is established.
  Broader `DebugObservation` mapping and other N07 gates remain as explicitly
  recorded in the source audit.

**Contract and evidence observations**

The historical CHECKED-RESULTS-AUDIT is not a current missing-API inventory.
Current TypeScript `checked.ts` / `runtime_recorder.ts` and Swift
`CheckedResults.swift` / `Recorder.swift` already contain sealed complete results,
periodic policy, and authentic checked handoffs. Their relevant files, and the
pinned Rust observation/monitor sources, have no working diff from this base.
The new observation code neither replaces those APIs nor adds a codec requirement
to the base Model. No reducer, initial-state, check, effect, or Recorder authority
is introduced by diagnostic events.

By source inspection, both implementations retain failure-before-skip severity,
Trace-only payloads/passed-check details, absolute snapshot divisibility,
checkpoint selection independent of divisibility, input/output/post-state order,
and no before-state formatting. The TypeScript paths keep sequences, totals,
seeds, and intervals as bigint through the full u64 range. Its UTF-8 writer
validates scalars, counts/admit bytes before copying, extends a 512-code-unit
slice across a trailing surrogate, and advances by encodeInto's actual read
count when its 1024-byte scratch fills. Source tests now cover the surrogate
boundary, three-byte scalars, and repeated emoji.

Record and encoded-blob admission reuse existing bounded encoders where
appropriate. A swallowed record write failure is still caught before publication
(including the final LF), and the next record can recover. Destination writers
use synchronous consumed-prefix counts with short-write completion, interrupted
retry, zero/invalid-progress rejection, and permanent poison on write/flush
failure. Poison is established before rendering a hostile sink error; health is
checked before Off. These observations do not establish allocator/RSS bounds or
containment of arbitrary callback side effects. The shared spec and current
language docs correctly leave custom bytes/newlines unsanitized and limit exact
Unicode/Rust Debug claims to the declared profile.

The TypeScript consumer constructs `record_hex` from `delivered`, records actual
codec/formatter callback order, retains the first caught error stage with `??=`,
and probes final health using an Off observation. Its final forbidden-callback
counter detects hidden initial/reducer/check/decode/legacy calls. The Swift
consumer similarly uses the extracted writer bytes, Context callbacks, first
caught normalized stage, forbidden-call count, and independent Off health probe.
Both route declaration-bound cases through their real public binder. Their
expected fields are used only for self-test comparison; no inspected path erases
actual sink bytes or substitutes expected output. These are statements about
consumer source, not outputs measured during this review. R2 remains relevant
to broader native callback failures beyond the current fixtures.

**Source identities at the end of this round (SHA-256)**

```text
a4956790e0ae97b757f16b7959d0cd17cf8e1dd7de8e3fec12638c2c9f664329  typescript/src/index.ts
2755a58539168657ca67fadfa43481e81c30d185f0ebf8a66cb271da5e15315b  typescript/src/observation.ts
25e7ff8f56573dd5b4fb187a5e7b057ac9550b8f6c600ef8c5c5fe8d949b19ea  typescript/src/oracle.ts
4dca896f32faf82795e40b07b9ebacbf79d00a6462c2fbff071768d330838396  typescript/test/observation.test.mjs
7f050d784fd584c70607b11e610e7f49e4b80979fee23f30431a288899944f0d  typescript/test/observation-recorder-integration.test.mjs
d9a8f169bc78eabb6c0a565bc47e37ec798b37e3d4b3a600a8da2aa2872ba9c5  typescript/test/paired.test.mjs
5fbf2e4a7351f7025d9f34d60f2f0156ac60545d46f331c3788ad41bbd08d78d  examples/native-observation/typescript/consumer.ts
b9d9bb3c3dc3d9c53a90dbc40b20f05576862d2bab79620a2ae7f2a9c2f826d8  typescript/README.md
4d74b3d46fadb648fb4c501f8056eb4ca2e82e39dc065c366c0d843558f91413  typescript/package.json
d49ecfd6f8b000fbf1395544bc9b9fe5405f5048a45768af0523cda3891ebe34  typescript/tsconfig.json
cdf3f58810813dcb555a9479c2a65962f12ca5a6290659e50a41b6b7b9e7f93b  typescript/src/encoding.ts
ee3df47d3b03c09489343184aa2ce2917f29348119dd605b55003aee727ce45d  typescript/src/checked.ts
5aeda96d9d961d04632361b017d1ab841a1a3f260cf63c36c1a8be88bab7e732  typescript/src/runtime_recorder.ts
d86054c16adce853c54708eef85d3b6c9809152bb65e66dfaf73c6ddd18244e3  swift/Sources/Statelessness/Observation.swift
252c52dd75b5e4b3fe975b0e4fd6f106418e21ddf35474eae8d7c8c03cf8f7dd  swift/Tests/StatelessnessTests/ObservationTests.swift
db145f525e52de24c7b71c0dc799a1cc69b6f1291739bc78389504daf4b85fb0  swift/Tests/StatelessnessTests/ObservationRecorderIntegrationTests.swift
b4116a87bd5bcd2d06058c33e38ff735d549cfe37baeb89ca402b828cd91cf70  examples/native-observation/swift/Sources/ObservationConsumer/main.swift
f75ac4628ffdf7d8df346755bd1fa79217fdb7a3b34bd33aeb9584c4c4791926  examples/native-observation/swift/Package.swift
d2b6cf8ba041f2816fca9c98b67fc34673a7c70ef3975a49bcdacd31b7d0a6c2  swift/README.md
a2b61ffbfbe9d9786adc0f54546352bac5908bfd0e8076a0a0df9681ea887af4  Package.swift
d2f92206250ddbc1368364c8c6cce4100074ce599e26bb722125e1d8c7dc2ccb  swift/Sources/Statelessness/CheckedResults.swift
459c44a770785540e60b0280e3a3f212a683fcbfccd41f6193b8bad790602ec9  swift/Sources/Statelessness/Recorder.swift
da04cfbaa4a268a9d5f7857a5c32b833230b5325b3777a3404d6065c6939d8c7  spec/observation-v1.md
772b3e871abd564d1be221a7b07af3536d1756e8a11bbd7a3ee55caa843a53d8  conformance/observation-v1.json
27bb580488221fbbf8887f3a107b36991648839ffcfa1de47b393d12f19f252c  docs/native-parity/OBSERVATION-INTEGRATION.md
6bc4c281fd3d00223b7261271df41a28f704d2f85e66c7dfd4d408b8c21f54c9  docs/native-parity/OBSERVATION-SOURCE-AUDIT.md
8d9e0fa19796931a2b85368092120e692e9e0f50788b9f36b2f22aa463ab7301  docs/NATIVE-PARITY-PLAN.md
03857afa6f4f320ebd4c2fa95e08a48acb1550432a27020d59ba2d97cc10cdec  docs/native-parity/CHECKED-RESULTS-AUDIT.md
c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870  src/observation.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d  tests/observation.rs
c4d9ad75cc0916eb6bd582cf98e22f3194c54df2be3aa52a6505a81b8dd35c0f  tests/monitor.rs
4a79b2f8fe236c27442a016bf748f254164a96da7d10706c01725ba852b10165  tests/monitor_limits.rs
f21ec4778015ad0ace2e54735dee6a6ed37072db918a00ef975854e1bf3042cc  tests/monitor_review.rs
```

The identity list binds this source review only. It is not a test receipt,
package seal, runtime approval, or N07 completion claim.
