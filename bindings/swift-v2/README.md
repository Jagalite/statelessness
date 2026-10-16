# Opt-in Swift ABI v2

`StatelessNativeV2` uses the Rust engine with typed Swift closures and values.
It is a separate Swift 6 package, preserving the legacy binding package's Swift
5.9 manifest and `StatelessNative` product. Supply the matching native library
and linker search path explicitly. Local macOS XCTest and corpus execution is
recorded in [the status report](../../docs/ABI-V2-STATUS.md); installed consumers,
other deployment targets and performance remain open.

```swift
import StatelessNativeV2

let model = Model<Int, Int, Int>(
    initialState: { 0 },
    step: { state, input in Transition(state: state + input) },
    checkState: { [.init("bound", $0 < 3 ? .passed : .failed,
                       $0 < 3 ? "" : "reached three")] },
    cloneState: { $0 }, cloneInput: { $0 }, cloneOutput: { $0 },
    inputs: { _ in InputIterator(values: [1]) }, equalStates: { $0 == $1 })
let session = try NativeSession(model)
defer { try? session.close() }
let result = try session.enumerate()
defer { try? result.close() }
let witness = try result.inputs()
```

The local `Statelessness` dependency supplies existing model/check/trace types
and strict portable JSON parsing. Operations never call its independent search,
record or replay drivers. Generated bundles relocate this source dependency to
`portable-swift`; no network dependency or second JSON parser is introduced.

Clone closures must deeply copy mutable values. Host roots remain retained until
Rust releases them. Iterator roots retain their source snapshots. Callback throws
become `NativeError`; Swift traps remain process failures. Check identifiers and
details cross the boundary as exact UTF-8 bytes, including empty strings and NUL.

Sessions, results, rings and callbacks are synchronous and confined to the
creating thread. They are not `Sendable`. `cancel()` alone may be called from
another thread. Nested engine operations fail with a reentry error. Close every
session on its owner thread; doing so invalidates its outstanding results/rings
and releases their roots. Same-thread deinitialization is a fallback. Wrong-thread
deinitialization deliberately retains engine/context resources because invoking
thread-confined cleanup there would be unsafe.

`checkObserved` and ring observation validate supplied transitions without
running the reducer. `recordBinary`, `replayBinary`, `enumerate`, `fuzz` and
`shrink` return disposable Rust result owners. `record`/`replay` use the portable
`Trace` DTO. Portable JSON carries observations and model identity, not Rust run
configuration/audit metadata: importing assigns a manual configuration and
exporting discards that nonportable configuration. A record callback error after
a coherent checkpoint returns the error-terminated prefix rather than leaking
the ABI's nonzero result handle.

Capabilities are derived from provided closures. Enumeration needs inputs and
state equality. Codec-backed record/replay are optional. Generation, shrinking,
state hashing and byte estimates remain explicit. Missing capabilities fail at
the Rust boundary; omitted hashing still supports collision-safe equality.
Set `automaticGeneration: true` with enumeration and input equality to use the
shared Rust reservoir/membership implementation. It cannot be combined with a
custom generator. Portable record limits are enforced incrementally by Rust,
before trace conversion, including blob, item and retained-payload budgets.

The `stateless-v2-corpus` executable uses the existing fixture and protocol
validator with ABI backend injection. It identifies itself as `swift-abi-v2`;
compiling it does not establish a passed conformance run.

`stateless-v2-bench` supplies explicitly invoked workloads for observation, BFS,
record, replay, fuzz, shrink and ring on cheap, large and expensive models. It
checks expected outcomes and emits timing samples/work counts. Samples alone
are not a performance receipt: allocation/RSS profiling, environment and exact
artifact identities, matched reference measurements and approved budgets are
still required. The compile workflow builds this source without invoking it.

For a build-only authorized environment, compile fixtures using `swift build
--build-tests --package-path bindings/swift-v2 -Xlinker -L<library-directory>`.
Do not interpret compilation as executed four-profile conformance, ownership
qualification, mobile-platform support, or permission to switch defaults.
