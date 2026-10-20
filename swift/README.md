# Native Swift Statelessness

The repository-root `Package.swift` exposes the **native** Swift engine. It is
independent of Rust and the older Rust-backed `bindings/Package.swift` package.
Swift tools 6.0+, no third-party package dependencies.

Products: `Statelessness` (library), `StatelessConformance` (fixture adapter), and
`stateless-corpus` (JSONL executable). Add the repository as a SwiftPM dependency,
then depend on `.product(name: "Statelessness", package: "statelessness")`.
Before merge/publication, select the feature branch or use a local path dependency.

From the repository root:

```sh
swift test
swift build -c release
swift run --package-path swift/Examples/Counter Counter
python conformance/run.py --runner '[".build/release/stateless-corpus"]'
```

`Examples/Counter` is an independent SwiftPM consumer importing only the public
library product. Package qualification also copies the distributed sources to a
separate directory and builds a separate consumer against them.

## Application-owned types

`Model<State, Input, Output>` takes native, throwing callback closures, required
snapshot closures, optional finite-domain/equality/hash hooks, and an optional
`Codec`. `enumerateStates` explores a bounded FIFO graph. `checkObserved` checks
an existing transition without executing it again. `record`, `replay`,
`encodeTrace`, and `parseTrace` provide checkpoint/observation persistence.
`Rng` uses UInt64 wrapping arithmetic and the shared SplitMix64 algorithm.

`cloneState`, `cloneInput`, and `cloneOutput` must isolate mutable references.
`{ $0 }` is appropriate for value-only/immutable types; classes or structs holding
mutable class references need explicit copies. Throwing callbacks become
`ModelError`. Swift traps and process exits remain process failures; they cannot
be represented as passing checks or caught as ordinary throws.

Swift's normal String equality uses canonical equivalence. The portable contract
instead requires exact scalar identity. `ExactString` supplies UTF-8-exact
hashing/equality for model keys; `exactText` compares exact text. Check IDs/details,
disposition reasons, metadata, JSON keys, and table state identities use exact
comparisons. Applications that use String in their own state equivalence must
choose an equality that preserves future behavior and properties. Valid leading
U+FEFF inside a string/codec payload is preserved, not silently stripped.

The package declares Apple minimum deployment versions, but only the platforms
listed in qualification reports are tested. Linux/macOS CLI/library testing is
not iOS, tvOS, watchOS, or Windows qualification. There is no binary framework
publication; build the native source package with the consumer's toolchain.

Profiles: `core-v1`, `bfs-v1`, `rng-splitmix64-v1`, `trace-json-v1`.
Experimental candidate: `paired-v1` supplies `WithOracle`, `OracleState`,
`Oracle`, `Paired`, and `Projection`. Construct a `Paired(reference:projection:)`,
pass its `.oracle` to `WithOracle(actual, oracle:)`, then use the adapter's `.model`
with the ordinary native recorder, replay, and finite BFS APIs. Reference history
is independently advanced and participates in snapshots, equality, and ORC1
checkpoint framing. `attach(model:oracle:)` requires explicit saved history;
`observeTransition` wraps completed application execution without running it again.
An adapter failure throws `ObservationError` retaining the actual transition
(a defensive snapshot when copying succeeds, the original value if copying fails).
The adapter and errors are synchronous and must remain confined to the owning
actor/thread. Swift 6 requires errors to conform to `Sendable`, so this generic
error uses unchecked conformance to retain arbitrary application payloads; that
does not make mutable application references safe to transfer across tasks.
Projection equality is application-owned; use `exactText` for exact textual
semantics. Clone closures must isolate mutable history and application values.
The candidate has application tests against `conformance/paired-v1.tsv`; it is
not a qualified portable profile. Relocated test packages may locate that fixture
with `STATELESS_PAIRED_CORPUS`.

Experimental native generation and search: `Generate(model:generate:isEnabled:
 simplerInputs:equalInputs:)` adds explicit state-aware generation and causal
 replay hooks. `Auto(model, options:equalInputs:)` supplies a finite reservoir
 generator as `.generated`; it retains one candidate, probes at most cap+1
 entries, and commits RNG only on a complete successful scan. The whole scan is
 one cooperative callback. Duplicate entries retain their sampling weight.
 `I?` is an explicit presence result: for optional-valued inputs return
 `.some(nil)` to generate a present nil, and `.none` to end the case.

`fuzz` uses one seeded SplitMix64 stream and mutates the previous passing
sequence. Its optional `SearchObserver` receives initial checks, every completed
transition including the failing prefix, and complete/incomplete checker-error
batches through defensive snapshots. Observer exceptions abort the run; a
secondary error notification exception retains the checker error in diagnostics.
The existing list-returning check API cannot expose checks assembled internally
before a throw, so that failed batch is empty/incomplete. Adding a streaming
check sink remains a separate N01 gap. `shrink` validates the original failure, preserves exact ID and phase,
then tries chunk deletion and lazy simplification with cycle detection. Inspect
termination, complete attempt history, aggregate replay work, and
`validatedOriginal`; interrupted validation preserves supplied evidence without
claiming reproduction. `RunLimits` provides cooperative cancellation/deadlines;
`ShrinkLimits` bounds aggregate replayed transitions in addition to attempts.
Callbacks are not preemptible, process memory is not bounded, and these APIs
remain synchronous and confined to their owning actor/thread. The shared
`search-v1` corpus and shrink history are experimental candidate tests, not a
qualified profile. Relocated tests may set `STATELESS_SEARCH_CORPUS` and
`STATELESS_SEARCH_HISTORY` to the shared TSV/JSON paths.

Guided campaigns, composition helpers, ring recording, and binary `.sttrace`
interoperability remain unimplemented in this native package.

`generated.withOracle(oracle)` preserves generation, enabled, simplification and
input identity hooks while wrapping state in explicit independent history.
Generation/membership see actual state only; the resulting model retains
combined equality and paired checkpoint codecs. Estimated-state-byte forwarding
remains unavailable until the native engine exposes that capability.
