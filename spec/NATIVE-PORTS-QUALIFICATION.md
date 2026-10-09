# Native Go, TypeScript, and Swift qualification

This change extends the four experimental v1 profiles with independent engines,
not bindings to Rust or Python. Base source is merged PR #1 at
`b97423d2bc01b61eee25b50a44e4b6416851b6b6`. The existing Rust-backed `bindings/`
packages and the dependency-free Rust core remain unchanged.

## Reproduction

Install Python 3.10+, Go 1.23+, Node 20+ with the pinned TypeScript 5.8.3 compiler,
and Swift 6.0+. Install the Python library with `python -m pip install ./python`.
Run `npm ci --prefix typescript`. Build the Rust adapter separately:

```sh
cargo build --locked --manifest-path validation/corpus/Cargo.toml
python scripts/check-native-ports.py \
  --rust validation/corpus/target/debug/stateless-corpus
python conformance/native_mutations.py --report target/native-ports/native-mutations.json
```

On Windows append `.exe` to the Rust path and use `--without-swift` for the package
script. Native fault controls can also use `--without-swift`. Native package CI
runs Linux/macOS with Swift and Windows without Swift. A configured matrix is not
itself evidence of passing jobs; inspect the current commit's CI run and artifacts.

## Observed local evidence, 2026-10-09

Linux x86_64: Go 1.23.2, Node 22.16.0, TypeScript 5.8.3, Swift 6.2.1. Python's exact
version and host string are in `target/native-ports/qualification.json`. Native
source packages are copied out of the checkout before compilation; TypeScript's
packed npm tarball is installed offline into a fresh consumer, and its public
exported declarations are compiled from that consumer. Swift is built in both
debug/test and release modes. Go builds/tests with `CGO_ENABLED=0`; a separate
race-enabled run and `go vet` also pass.

Observed native results: **31 Go tests** (28 library plus 3 strict-parser tests),
**28 TypeScript tests**, **29 Swift tests**. Independent Go, TypeScript, and Swift
counter consumers each find a three-transition witness, record it, and reproduce
the failure through exact replay. The Go and Swift source packages and npm tarball
have no third-party runtime dependencies.

The installed-runner five-engine suite passed with zero failures:

| Check | Per engine / total |
| --- | --- |
| Original reviewed golden corpus | 94 per engine |
| Exhaustive directed topologies on 1–3 labeled states | 530 per engine |
| State-storage-order variations | 32 per engine |
| Additional exact-text/arithmetic cases | 38 per engine |
| Malformed and boundary transport cases | 23 per engine, plus recovery |
| Fresh-process producer/consumer replay | 550 total across five engines |

Original corpus SHA-256 remains
`6c9e4de68bad0b1188f7303ae9fd820bf75a3f6a2cdeeb292f3742aa9f8fbf11`.
`portability.py` has a separate digest in every report; no golden expectations
were replaced with outputs from the newly implemented engines.

All **six** deliberate native engine mutations were detected by structured
conformance mismatches. Build errors, crashes, malformed responses, and timeouts
are not counted as successful fault detection. Controls cover incorrect random
arithmetic, broken deduplication/depth reporting, and Swift canonical-equivalence
substitution. They complement the existing eight Python controls, not exhaustive
mutation coverage.

The local Rust runner was the previously source-qualified CI binary, not a fresh
local Rust compilation. Its SHA-256 is recorded. The new CI workflow compiles the
Rust adapter from the checked-out source and qualifies the installed native
packages against that fresh binary. Do not infer CI qualification from local
results or an earlier commit's artifacts.

## Review findings fixed

A Node Buffer's `slice()` aliases its backing storage. Native codec snapshots now
copy into a new Uint8Array; a regression checks reusable codec buffers.
Swift Foundation's failable UTF-8 conversion can remove a leading BOM. Strict
validation followed by exact decoding preserves U+FEFF inside values and replay
payloads; BOM document prefixes remain invalid. Swift default canonical String
equality is not used for portable identity/check text or table keys.
Go serialization rejects invalid UTF-8 in native metadata/check strings before
`encoding/json` can substitute replacement characters. Native trace limits also
reject values that would overflow hexadecimal expansion/accounting.

## Scope and limits

All new ports implement `core-v1`, `bfs-v1`, `rng-splitmix64-v1`, `trace-json-v1`.
They do **not** implement Rust's fuzzing/shrinking, guided search, independent
oracles, campaigns/composition helpers, runtime ring recording, or binary
`.sttrace` audit interoperability. The JSON envelope is not a lossless conversion
of Rust audit metadata. Exact trace replay does not waive incompatible application
models, codecs, builds, or properties.

The TypeScript library has no Node imports, but Node tests do not establish
browser UI/runtime qualification. Swift deployment declarations do not establish
iOS/tvOS/watchOS or Windows qualification. Performance equivalence is not claimed.
Callbacks must obey deterministic copying/equality/codec contracts, and hidden
allocations or OS effects are outside engine resource guarantees. Finite corpus
agreement is evidence, not universal correctness or equivalence proof.

No npm, PyPI, Go module tag, Swift binary release, deployment, or merge to main is
part of this change. CI artifacts carry exact source commits and package digests.

## PR #2 review follow-up (2026-10-09)

The review fixes validate all TypeScript trace limits before decoding/encoding,
reject invalid Go observed dispositions independently of optional checking, and
retain `ExactString` keys in Swift `objectFields` results. String subscripting
still works and performs exact UTF-8 lookup. Regression tests cover each case.

The Swift Unicode fault control now mutates hashing together with equality, so
it produces structured disagreements without violating `Hashable`. Replay
separates throwing snapshot/encoding expressions to avoid the Swift 6.0.3 Darwin
ownership-verifier failure seen in CI. XCTest calls explicitly qualify
`Statelessness.record` to avoid the Apple XCTest method of the same name.

Local macOS arm64 checks passed with Go 1.24.2, Node 23.5.0, TypeScript 5.8.3,
and Apple Swift 6.3.3:

```sh
(cd go && CGO_ENABLED=0 go test -count=1 ./... && go vet ./...)
(cd go && go test -race -count=1 ./...)
npm ci --prefix typescript
npm test --prefix typescript
swift test --scratch-path target/pr2-swift
(cd go && CGO_ENABLED=0 go build -o ../target/pr2-go-corpus ./cmd/stateless-corpus)
python3 conformance/run.py \
  --runner '["target/pr2-go-corpus"]' \
  --runner '["node","typescript/bin/corpus.mjs"]' \
  --runner '["target/pr2-swift/debug/stateless-corpus"]' \
  --report target/pr2-conformance.json
python3 conformance/native_mutations.py --report target/pr2-native-mutations.json
```

TypeScript: 30 tests; Swift: 32 tests; all Go packages and race checks passed.
The three affected runners passed the shared corpus, generated graph,
portability and transport checks, plus 198 fresh-process replays with no
mismatches. All six mutation controls were detected through structured results.
This local follow-up does not requalify installed packages, other platforms,
Swift 6.0.3, or the unchanged Python/Rust runners; CI records those separately.
