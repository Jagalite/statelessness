# Installed BFS application consumers

These applications use the public independent native FIFO exploration APIs,
cooperative cancellation, estimated retained-state-byte admission, and observer
delivery. The Rust reference uses the actual Rust public exploration engine.
Each application supports `record PATH` and `replay PATH`.

The common model starts at integer zero, enumerates `[0, 1]` in that order, adds
the selected input, and checks `threshold.bound`. State three fails with details
`at least three`. Input zero emits nothing; input one emits `changed`. State and
input codecs use decimal ASCII, with strict input decoding of zero/one. Metadata
is `native-bfs-threshold`, versions 1/1/1, build `application-v1`.

Each retained state estimates five bytes. The normal run finds `[1, 1, 1]` with
three retained states, six checked transitions, and 15 estimated retained bytes.
A 14-byte cap stops admission after two retained states, four checked
transitions, and ten estimated retained bytes. Pre-cancellation stops with no
model or observer-delivery callbacks, zero states/transitions, and unknown
retained byte total. The applications expose callback counts and the full
root-based observer histories; the driver checks all these results explicitly.
These logical byte estimates do not measure process memory or hidden callback
allocations.

The found witness is recorded through each language's public portable JSON
recorder. Every ordered pair of five engines reloads and exactly replays the
failed witness in a separate process, yielding 25 replay pairs. The driver also
runs the four established portable conformance profiles through newly installed
Python/TypeScript packages and staged Go/Swift corpus executables, against the
independent goldens and cross-language replay matrix. An existing Rust portable
runner can additionally participate in that baseline regression.

After all engine sources are frozen, submit through Kyoto:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 scripts/check-native-bfs-consumers.py --rust validation/corpus/target/debug/native-bfs --output /tmp/native-bfs-consumers-unique-run
```

The output directory must be new or empty. The driver builds a wheel with
isolated build tooling, installs it into a separate clean venv, installs a local
npm tarball and compiles against public declarations, builds a separate Go
module with cgo disabled, and builds a separate SwiftPM application. All packages
and consumer execution live outside the checkout. It records commands, logs,
source identities, toolchains, package/executable/artifact hashes, producer
receipts, and baseline conformance results. It runs no cargo commands; Rust
executables must be built separately through Kyoto. `--without-swift` is an
explicit subset, and `--rust-portable PATH` adds the optional Rust baseline
runner. Passing is candidate BFS controls evidence, not profile promotion,
performance, parallel search, external-memory exploration, or complete parity.
