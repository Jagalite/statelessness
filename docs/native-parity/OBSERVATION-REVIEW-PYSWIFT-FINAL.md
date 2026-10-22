# Python / Swift observation final source review — round 3

Source review on 2026-10-22 in
`/Users/jagatranvo/.t3/worktrees/stateless/t3-17ecaeca`, base HEAD
`54ba3f32796fc5c71458fb4cda14b2ab9290ee83`. The readings below include the
parent's concurrent STEP-item correction, its new regression source and updated
Swift fix disposition, and the Swift/TypeScript experimental-profile discovery
additions (the TypeScript review here is limited to that one-line diff).
They identify working files, not an integrated source freeze.

**One P2 remains open in the updated Swift budget helper.** The original
Python/Swift formatter-attribution objections and the specified consumer,
raw-byte, footer and output-limit corrections are addressed in source. The
parent's adjacent STEP-item patch fixes the reported item/string case but
introduces the competing frame/item precedence regression described below.
There is also a nonblocking Swift installed-consumer measurement gap.

No implementation, fixture generator, compiler, syntax checker, build, test,
package installation, executable qualification check, or Kyoto job was run or
submitted. No expected fixtures were generated. Applicable ancestor and in-tree
`AGENTS.md` searches found none; the requested
`/Users/jagatranvo/.codex/skills/rust-builds-kyoto/SKILL.md` was read. This reviewer
owns only this new report; the delegated Swift inspection changed no files.
No implementation edits, staging, commits, push, PR, or other-worktree changes
were made. The parent owns the final freeze and shared Kyoto qualification.

## Open findings

### F1 — P2: the STEP correction moves aggregate admission ahead of an earlier frame failure

At [ExecutionBudget.swift:31](../../swift/Sources/Statelessness/ExecutionBudget.swift#L31),
`executionStepFixed` now counts the STEP and outputs before measuring the
preceding index/input/disposition fields. The new initial count is correct, but
the existing placement of output admission becomes observably wrong for this
source-derived ordinary Recorder scenario:

- Use a small valid checkpoint and metadata, `maxFrameBytes = 512`,
  `maxItems = 64`, `maxOutputsPerStep = 128`, sufficient total/retained budgets,
  and an input blob limit allowing 512 bytes.
- Supply a continuous accepted turn with 64 outputs, an input encoder returning
  exactly 512 bytes, and empty state/transition checks.
- Recorder's early output checks admit 64 at both limits, and input encoding
  admits the 512-byte blob. The updated helper then rejects `1 + 64` items at
  line 31, reporting `aggregate items`.
- Rust [monitor.rs:491](../../src/monitor.rs#L491) measures the provisional step
  through [trace.rs:278](../../src/trace.rs#L278). Its encoder checks frame space
  while writing the index and framed input, before output counting at line 292.
  The index plus input needs `8 + 4 + 512 = 524` bytes, so Rust reports
  `frame bytes`. Base Swift also reached its frame check first for this scenario;
  the newly early STEP count changes that result.

This changes the returned error and frozen recording-error reason for otherwise
equivalent public Recorder inputs. It is a P2 source correctness regression in
the adjacent patch, not a failure of diagnostic logging, checked authenticity,
or coherent-prefix retention. No counterexample was executed.

**Needed narrow correction:** retain STEP admission before collections, but
measure and enforce the preceding frame fields in Rust wire order before
admitting output items. Merely moving the count without enforcing the earlier
frame bound is insufficient. Preserve the 63-output/item-versus-string regression
and add the 64-output/512-byte-input competing frame regression through public
Recorder, with error category, callback cutoff and frozen-prefix assertions.
The same helper serves other bounded execution paths; its existing recording and
replay regressions belong in the parent's eventual qualification.

### F2 — P3: Swift installed consumer does not count Off-probe or extraction I/O

The [Swift consumer's Sink](../../examples/native-observation/swift/Sources/ObservationConsumer/main.swift#L246)
retains bytes but no write/flush attempt counters. Its
[final Off probe and extraction](../../examples/native-observation/swift/Sources/ObservationConsumer/main.swift#L297)
check poison, forbidden model work and collected callbacks/bytes, but cannot
detect an unnecessary zero-byte write or successful flush. The Python consumer's
new counters detect both.

This is a measurement gap, not an observed production fault. Swift's inspected
`observe` returns before formatting or writer work at Off, and
`ObservationTests.swift:276`, `:357` and `:369` contain separate writer-count,
ownership and poison assertions. Add attempt counters around the installed
consumer's probe/extraction if that consumer is to establish the same no-I/O
claim. Its current fixture result alone does not establish it.

## Prior finding dispositions

**Python operation-bound attribution — addressed in source.**
[observation.py:192](../../python/src/statelessness/observation.py#L192) gives
internally attributed codec failures the active lease's private identity and
canonical stage/detail. The boundary at line 428 revokes the lease in `finally`,
checks the record's sticky failure, and only preserves an internal codec stage
from that exact payload invocation. Public staged errors, nested observers,
wrapped errors and retained errors from another slot/event receive the actual
`format_input/output/state` stage. Mutable public `stage` fields are not trusted.
The rejected record stores canonical failure data separately at lines 275–300;
swallowed overflow remains primary even after a different callback/codec failure
or invalid return. Direct public built-in formatters retain codec attribution and
short-write completion. Exception-text rendering at this outer callback boundary
occurs after revocation. This remains an API convention, not protection against
Python reflection or arbitrary application side effects.

[test_observation_attribution.py](../../python/tests/test_observation_attribution.py)
contains independent assertions for all three payload positions, public/nested
errors, direct versus delegated codecs, stale errors across slots and events,
sticky competing failures, hex overflow, lease expiry, borrowed values, hostile
exception text and process interrupts. These are inspected regression sources,
not passing results.

**Python declaration bypass and Off counters — addressed in source.**
[consumer.py:46](../../examples/native-observation/python/consumer.py#L46)
traps direct declaration-model encoders; the bound field adapters at line 53
independently supply bytes and callback names. Removing `declaration_bound`
cannot produce the successful declaration record. The regression at
[test_observation.py:499](../../python/tests/test_observation.py#L499) explicitly
patches the binder to identity and expects `encode_input`, no bytes/callbacks,
and a healthy sink. Consumer lines 84–100 count every write/flush attempt, and
lines 151–166 compare those counts and payload callbacks across Off and writer
extraction. The fixture output derives from actual destination bytes; expected
fields are used only for self-test comparison.

**Swift R2 formatter provenance — addressed in source.**
[Observation.swift:94](../../swift/Sources/Statelessness/Observation.swift#L94)
introduces a private immutable identity retained by its errors. Each payload
lease has a distinct identity; line 156 preserves only its own failures.
The public error initializer has no origin, so public, nested and stale errors
cannot claim the outer writer failed or was poisoned. Genuine current-operation
record/codec failures keep their stage. Regression sources at
`ObservationTests.swift:183`, `:208` and `:249` cover public stages, nested
observers and stale record errors, including publication, health and recovery.

**Swift arbitrary payload bytes — coverage gap addressed in source.**
[ObservationTests.swift:298](../../swift/Tests/StatelessnessTests/ObservationTests.swift#L298)
compares a literal ASCII prefix plus `[0xff, 0x00, 0x0a, 0x0a]` as bytes. Exact
capacity succeeds in the assertion; one byte short targets final LF, and two
bytes short targets the raw payload write. Both failure branches assert no
publication, healthy Off, and a later smaller record. There is no replacement
UTF-8 decoding in these expectations. The raw-byte append path remains intact.
The earlier `.every(1)` versus `.everyTransition` identity correction also remains.

**Swift declaration bypass — addressed in source.**
[consumer main.swift:201](../../examples/native-observation/swift/Sources/ObservationConsumer/main.swift#L201)
leaves throwing, counted direct hooks for declaration cases; the actual
`ModelCodecBinding` adapters replace them at line 223. The regression at
`ObservationTests.swift:421` first expects unbound input encoding failure, then
real adapter success with literal `11`, `33`, `22` payload bytes and no added
direct-hook calls. Installed omission/mutation execution remains pending.

**Swift Recorder footer/output P2s — addressed in source.**
[Recorder.swift:50](../../swift/Sources/Statelessness/Recorder.swift#L50) checks
the reserved error-footer payload after metadata and before checkpoint encoding
and checking, including failed-checkpoint admission. The required payload is
`13 + executionErrorTextLimit`, matching Rust's footer layout and its prior
reservation. Existing native snapshot ownership is preserved. Lines 207–208
separate outputs-per-step from aggregate-item bounds after continuity and before
input encoding/checking, matching Rust monitor order. Regression sources at
`ObservationRecorderIntegrationTests.swift:402`, `:428`, `:450`, `:483`, `:509`
and `:553` cover competing errors, callback counts, certificate reuse, limits,
eviction and coherent retention.

**Adjacent STEP/item/string P2 — original case corrected; F1 remains open.**
The original discrepancy was genuine and relevant to bounded Recorder error
precedence, even though it originated outside the initial observer ownership.
The parent now initializes `size.items = 1`, admits it, and removes the late
duplicate addition. With 63 outputs and one check, the helper now sees 65 items
before inspecting that check's oversized ID.
[ObservationBudgetPrecedenceTests.swift:5](../../swift/Tests/StatelessnessTests/ObservationBudgetPrecedenceTests.swift#L5)
uses public ordinary Recorder with a 65-byte ID and checks aggregate items at
cap 64 versus string bytes at cap 65, one application reducer call, two state
checks, zero output encodes, zero admitted/retained turns and frozen state.
Those assertions are unexecuted; this new test does not itself compare checkpoint
bytes. The updated parent disposition in `OBSERVATION-FIXES-SWIFT.md` was read.
This specific correction must not be described as untouched, but F1 prevents
closing budget precedence generally. Checked handoffs preflight certified checks
earlier in both engines; the ordinary-turn scenario must not be generalized over
that distinct path.

**Corpus R7/R8 — source changes retained.** The current corpus has 36 case IDs,
including the two new Trace callback cutoffs at zero and 97 bytes. Both consumers
use their normal observer/codec paths for these cases. The zero-bound case reaches
no codec; the 97-byte prefix reaches only input encoding before the output-field
write fails. The later Debug record is the independently authored 85-byte
recovery line including LF. These expectations follow the pinned Rust formatting
order; no cross-language comparison or fixture validator was run here. The spec
now explicitly separates Rust/Python short-write completion from Go's native
short-write contract.

## Public compatibility, authority and syntax inspection

Python's existing package `ObservationError` still comes from `oracle.py`; the
new diagnostics export is `TextObserverError`, and the existing core `Transition`
is distinct from `TransitionEvent`. Swift keeps generic `ObservationError<S,O>`
and new `TextObservationError` separate. The parent's latest
[Oracle.swift:2](../../swift/Sources/Statelessness/Oracle.swift#L2) diff appends
only `observation-v1` to `experimentalProfiles`; it changes no oracle class,
throw path, or qualified profile. The requested in-flight
`typescript/src/oracle.ts:10` diff likewise only appends `observation-v1` to
`EXPERIMENTAL_PROFILES`; its oracle class and throw path are unchanged. Python's
experimental listing likewise adds only the candidate profile. This review does
not otherwise review or requalify TypeScript or Go.

The historical checked-results audit is not a current missing-API inventory.
Python `checked.py` and Swift `CheckedResults.swift` already contain sealed
results and policy coverage, and have no diff from this base. Python's existing
Recorder is unchanged. Swift's Recorder changes are the narrow admission-order
corrections above. Authentic-token construction, full-checking and absolute
sequence/overflow guards, metadata and encoded before-state continuity, and
state-check-prefix-only checkpoint promotion remain present. Logging does not
call these admission paths or mint tokens. Source integrations deliberately use
real certificates, reject forged diagnostics and sampled/missing turns, preserve
first failure, and compare execution/effect counters across verbosity levels.

The new `record-suffix` branches in the Python/Swift checked consumers were also
inspected. They use real checked Recorder attachment at origin 40, two supplied
turns, capacity one, state-only checkpoint promotion, and separate NoPayloads
diagnostics with callback counters. Reading those branches establishes neither
fresh-process replay nor installed-package identity; the parent driver must
execute and bind those results to its frozen sources.

By inspection, severity filtering, failed-before-skipped precedence, Trace-only
payloads/passed details, input/output/post-state order, absolute periodic
selection, explicit checkpoints, raw-byte bounds including LF, swallowed-write
publication prevention, short/interrupted writes, sink poison before Off and
explicit writer ownership match the stated bounded contract. NoPayloads/custom
formatting adds no codec requirement. Diagnostic events confer no continuity or
checked authority and introduce no reducer/check/effect execution. Native codec
dispatch and application callbacks keep their existing ownership/purity limits.

No definite Python syntax/import collision or Swift syntax/access/concurrency
compilation defect was found by source inspection. Swift's private origin is an
immutable final Sendable class, its error carries immutable fields, and mutable
observers/leases are not newly declared Sendable. Package.swift includes the
whole native test directory, including the new budget test. These observations
are not parser, compiler or runtime evidence.

## Remaining qualification limits

- Correct F1, then freeze and rereview the changed helper/regression identity.
  Preserve the current failed-source finding rather than treating the original
  item/string fix as whole-helper parity.
- Run the parent-owned Kyoto compilation and actual native tests, including
  Python attribution/Recorder integration, Swift observation/Recorder integration,
  the new budget suite and affected shared-helper regressions. Check effective
  commands; compilation-only success cannot establish executed behavior.
- Establish installed public-package resolution, declaration bypass failure,
  complete 36-case Rust/native bytes/callbacks/error/health comparison, and
  fresh-process retained-suffix replay. Python's source test imports the consumer
  and its child replay inherits the test process's package search path; their
  names alone do not establish an independently installed wheel.
- The finite profile does not prove arbitrary Unicode/future Rust Debug spelling,
  callback purity, hostile reflection containment, RSS/allocator bounds, blocking
  I/O deadlines, or replay of an evicted prefix. Broader N07 completion remains
  governed by the parent plan and integrated evidence, not this source review.

## Source identities

SHA-256 readings taken during the final source inspection on 2026-10-22,
including the parent's in-flight updates. Later changes require refreshed review;
this inventory is not a runtime receipt or an atomic whole-tree seal.

```text
c0c288b687dbf03a87be44dab322af47ca8c3472997da337544972f5962fd870  src/observation.rs
d2adec51f0c17a985d07b92c15c4e57ee15ea2917b52e75931d45d95736588ac  src/monitor.rs
157ed1c5e070372220c14a23e6ba9e636e0ab94507160e7197f1419052bfc786  src/trace.rs
842e4713c39efae912792ab99f72cc5b669c3d077b11dece9e37bd76c4a1af2d  tests/observation.rs
c4d9ad75cc0916eb6bd582cf98e22f3194c54df2be3aa52a6505a81b8dd35c0f  tests/monitor.rs
f21ec4778015ad0ace2e54735dee6a6ed37072db918a00ef975854e1bf3042cc  tests/monitor_review.rs
4a79b2f8fe236c27442a016bf748f254164a96da7d10706c01725ba852b10165  tests/monitor_limits.rs
8d9e0fa19796931a2b85368092120e692e9e0f50788b9f36b2f22aa463ab7301  docs/NATIVE-PARITY-PLAN.md
03857afa6f4f320ebd4c2fa95e08a48acb1550432a27020d59ba2d97cc10cdec  docs/native-parity/CHECKED-RESULTS-AUDIT.md
c1f9ec50b819e03aaedd6f26cd7a7c27c60bde797f30f711f3ca451d3b8c7000  docs/native-parity/OBSERVATION-REVIEW-PYGO.md
e49c875bd78e05fedffc4519490b9498a9ebfe78c84dda50ae38204f85d71b2f  docs/native-parity/OBSERVATION-REVIEW-TSSWIFT.md
f3f7657c3643bb24cbe8a4a8353904929981bf80817aae1c49295854a1817a1b  docs/native-parity/OBSERVATION-FIXES-PYTHON.md
bbb5db7609abd0fbbe49de69f1203ba74799c25579f619ecf97f119141fdd6e2  docs/native-parity/OBSERVATION-FIXES-SWIFT.md
d6d4d936d2673aa26a64fee7e18931a89375830d7cf949f80cde09492216b272  docs/native-parity/OBSERVATION-FIXES-CORPUS.md
90dbf8fc2f838231c798b6e938af734d0c9dfe98db137cbb2eb94222a3fa2972  spec/observation-v1.md
20d7002957eb05f1d22c25f4a0be613105323fabcd2a49382588e6bc9ae7e8a8  conformance/observation-v1.json
ce7fd84bfd1553cf88701e46880be02cc123f843a41b93d942eb8ba0ca5da36e  python/src/statelessness/observation.py
c0e646b838028d1a8d95a3a1fc172ed1d32810f86c787c8935f723d9b3515b80  python/src/statelessness/__init__.py
5a450a24632d2f304d3714cccd1723654d568550d92d47f7ff38d7f8568e0b70  python/src/statelessness/checked.py
034e6f479504860d727a5354245ced7230675f04d10592530eba047eb9120493  python/src/statelessness/monitor.py
50d26c0430ae2bf67c43d9874817a69cc673d655dd835c9218f1fb31eaa3245f  python/src/statelessness/oracle.py
d2763cf6b78423d8abfcab6efe84c68ebced60cacc22a92f5c3556b8fa75a02a  python/src/statelessness/codec.py
565db14b37211928c263aab59057b10aef1df9dc81821fec77a427123f7841db  python/tests/test_observation.py
2ce14d440c03031ee114d4a6fa9b9ca572ff8eec68b3f93edd6aad0bc2529f0b  python/tests/test_observation_attribution.py
b202ce22f3087c06b40a914b1d81863f1057440927f2611a8670b68e06402ae3  python/tests/test_observation_recorder_integration.py
ab1cda741501de681f767fbb9f9ab22e867555b373f97f5c0458c8b6b47f4d6b  examples/native-observation/python/consumer.py
0a0fe932bc884a36e8aff63bc72116206a2918190fa540d2d22bf0e6ad78c2db  examples/native-checked/python/consumer.py
cbc2a14bca3a09178e8a08dd04f7fc3176404f845704ed8a4e4e14ef550d1b9a  swift/Sources/Statelessness/Observation.swift
0d90f40a48e7580383c0a402a93ad871f9c74031bb83a046e263c102eab94796  swift/Sources/Statelessness/Recorder.swift
37a7b26e6e3a26e0c7e7207c3696c4b1db1efc9e1e76685d9de11851bf7d0186  swift/Sources/Statelessness/ExecutionBudget.swift
de880b933f6a0079e452c9502e5a5226b459598a6275ccb0d6df8976c752c920  swift/Sources/Statelessness/AuditTrace.swift
d2f92206250ddbc1368364c8c6cce4100074ce599e26bb722125e1d8c7dc2ccb  swift/Sources/Statelessness/CheckedResults.swift
727b637d111bf43ce06345c359923bf23d8398516c7d3745d31aec5f433c5270  swift/Sources/Statelessness/Oracle.swift
d559430a9a9802a0f07ffa13b28cf61ff49e74aad12e354d817fb7c3d6f45a1c  typescript/src/oracle.ts
b70b7b507f408adc89878b3c80b90f09a5902b8e80519b547be1844a1329532f  swift/Tests/StatelessnessTests/ObservationTests.swift
8cc6b0f6645f998bf102281f426872d00f69699d3ed1711a0d9af8fd04ca6473  swift/Tests/StatelessnessTests/ObservationRecorderIntegrationTests.swift
b034de6e3c4ce4b1611bdfc49b510b7a4dc30e0cdf31dc7c84fdb2afcc352795  swift/Tests/StatelessnessTests/ObservationBudgetPrecedenceTests.swift
06aaff9d75ebec5abe3c6120d92ffe85e9bd1e4d319d35a493646b997cb2ed29  examples/native-observation/swift/Sources/ObservationConsumer/main.swift
92e83f7e17beae639a47f3a066baf595fea45337c2191321d4a42f4b05552570  examples/native-checked/swift/Sources/CheckedConsumer/main.swift
bfbc211b25ea8f49e29905672030cf11a972a1ce08b408e1fef8cd6edfce7628  python/README.md
990f2ee5a2703afdd2c1442136e637cf3588666a4dff956217528bb4ec4639db  swift/README.md
a2b61ffbfbe9d9786adc0f54546352bac5908bfd0e8076a0a0df9681ea887af4  Package.swift
```
