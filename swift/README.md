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
This release does not implement advanced Rust fuzz/shrink/oracle/campaign/
composition helpers, ring recording, or binary `.sttrace` interoperability.
