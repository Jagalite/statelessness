# Go conformance qualification — 2026-10-08

## Review fixes and requalification

This section supersedes the initial qualification below for the reviewed code.
The review fixed aggregate callback packet allocation limits, enforced session
thread/concurrency checks (including closed and zero-value sessions), preserved
the reference's bounded codec hook and memory estimate, and separated independent
property failures from paired disagreements, including initial-state diagnostics.
The example now links one static engine. `otool -L target/go-jobs` confirms no
`libstateless` shared dependency. Standalone SDK tests still use the shared ABI.

The kit disables Go test caching and incorporates the native content identity in
cgo preprocessor flags so Go's build cache cannot silently reuse a link against
an older external library. Its build directories are explicit.

Final kit identity:
`paired-sha256:080cf3924b36b89e5878d8c90409d539c411392f7822fba7dafb1eb14ad9d303`.
Both shared-library and static-bridge Go suites passed uncached: nine SDK tests
and three reducer tests each. Correct reducer: GraphExhausted, 9 states,
135 transitions, zero skipped checks. Faulty reducer: the same named disagreement,
validated 5-event original reduced to 3 events; both witnesses replayed Exact in
fresh processes, with the failure reproduced and matching identities. Crossing
correct/faulty identities was rejected. The original still includes the explicitly
documented demonstration prefix; it is not the raw shortest BFS path.

Commands rerun for this review (all passed):

```sh
scripts/check-go.sh > target/go-review-conformance.log 2>&1
CARGO_INCREMENTAL=0 cargo test --workspace > target/go-review-rust.log 2>&1
CARGO_INCREMENTAL=0 cargo test --manifest-path examples/go-jobs/Cargo.toml
CARGO_INCREMENTAL=0 cargo clippy --workspace --all-targets -- -D warnings
CARGO_INCREMENTAL=0 cargo clippy --manifest-path examples/go-jobs/Cargo.toml --all-targets -- -D warnings
cargo fmt --all --check
cargo fmt --manifest-path examples/go-jobs/Cargo.toml --check
git diff --check
python3 scripts/build-bindings.py --kind native
python3 scripts/check-native.py
export DYLD_LIBRARY_PATH="$PWD/target/debug"
export CGO_LDFLAGS="-L$PWD/target/debug"
(cd bindings/go && go test -race -count=1 ./...)
otool -L target/go-jobs
```

The Rust workspace passed 226 tests including doctests and compile-fail checks.
The reference example has three passing tests, including initial independent
failure classification. Rust conformance tests include a codec that panics if
its unbounded fallback is called; paired bounded encoding passes and rejects an
insufficient buffer. Go regressions exercise wrong-thread and concurrent calls,
reentry, exact/over-limit aggregate packets, sticky codec errors and zero sessions.
Race tests passed on darwin/arm64; Apple ld emitted an `LC_DYSYMTAB` warning while
linking Go's race runtime. This does not instrument Rust/C memory accesses.
Native C smoke and relocation passed. Some native launches were delayed in
`_dyld_start` before application entry; they eventually completed.

Toolchains were reconfirmed: Go 1.24.2, rustc/Cargo 1.99.0, Apple Clang 21.0.0,
macOS arm64. Swift and Wasm checks below belong to the initial implementation
qualification and were not rerun during this review. Linux, Windows, other
architectures and ASan remain untested. No packages were published or pushed.

## Initial implementation qualification (before review fixes)

Local development/testing evidence on macOS 26.5.2, arm64. No motion checkout,
publication, push, deployment, or package release was involved. The initial
worktree was clean; no applicable AGENTS.md was present in this checkout or its
ancestors. Source is `Jagalite/statelessness`, local branch `main`.

## Toolchains

- rustc 1.99.0 (b940084d7 2026-09-28), Cargo 1.99.0
  (5f94df478 2026-08-27), Clippy 0.1.99, rustfmt 1.10.0.
- Go 1.24.2, darwin/arm64, cgo enabled.
- Apple Clang 21.0.0 (clang-2100.1.1.101), Xcode toolchain,
  target arm64-apple-darwin25.5.0.
- Swift 6.3.3 (swiftlang-6.3.3.1.3 clang-2100.1.1.101).
- Node 23.5.0; Python 3.14.6.
- Wasm qualification used installed rustup stable rustc 1.90.0
  (1159e78c4 2025-09-14), Cargo 1.90.0 (840b83a10 2025-07-30).

## Paired results

The actual Go reducer ran through C callbacks against the independent native
Rust reducer using `Auto<WithOracle<...>>`. Correct implementation: GraphExhausted,
9 states, 135 transitions, zero skipped checks. Declared domain: all 15 explicit
events at every reachable state, two job generations. Bounds: 10,000 states,
100,000 transitions, depth 100. Deterministic BFS has no random seed.

Faulty implementation: FailureFound at 4 admitted states / 40 executed edges.
Named failures: `pair.state`, `pair.effects`, `pair.outcome`. Original recorded
sequence has five events (including two deliberately prepended harmless rejected
requests); existing causal shrinking validates that original and reduces it to:

```text
submit(token=0), start(token=0), complete(token=0)
```

The completion token is stale for generation 1. First divergence is input index 2
in the reduced sequence (index 4 in the original): both are running at generation
1 beforehand; Go becomes complete and emits completed, Rust stays running,
emits nothing, and rejects with `stale`. Shrink termination is SearchComplete,
which is not a globally minimal-witness claim.

Both original and reduced traces replayed in separate processes with Exact,
failure_reproduced=true and build_matches=true (5 and 3 verified steps).
Correct/faulty identity crossing returned Incompatible, status 3. This is not
used as evidence that the correct reducer fixes anything; its separate finite
exploration establishes bounded agreement.

The current command, logs and metadata preserve the content/toolchain identities:

```sh
scripts/check-go.sh > target/go-conformance.log 2>&1
```

The final passing kit identity was
`paired-sha256:02aa534004ee50f15fe0d802043210da8896aa2148c5c2bf7e7a2334c42926cc`.
Both implementation names and their build identities are retained in trace metadata.

This command runs `go test ./...` (six SDK tests, three reducer tests), builds the
matching local native libraries and Go command, and executes:

```sh
target/go-jobs correct target/go-jobs
target/go-jobs faulty target/go-jobs
target/go-jobs replay-faulty target/go-jobs.original.trace
target/go-jobs replay-faulty target/go-jobs.reduced.trace
target/go-jobs replay-correct target/go-jobs.reduced.trace
```

The last invocation must fail with Incompatible and is explicitly checked by the
script. Loader/linker environment is set by the script. Evidence remains under
`target/`: original/reduced traces, diagnostic report, incompatible replay output
and the full conformance log. There are no checked-in binary artifacts.

SDK coverage includes malformed/truncated/trailing callback packets and traces,
invalid tags, callback errors and panics, session cleanup after errors and user
panics, deleted cgo handles, closed-session/reentrant-operation rejection,
finite enumeration, exact replay and incompatible codec/build identities.
The reentry regression invokes Record from an actual native Go callback and
verifies that only one callback ran.
Rust conformance tests compare different internal state/effect types and retain
both independent checks; explicit reference history is not reconstructed from
actual state. Reference job tests cover malformed codecs, stale completion,
cancellation, resubmission and the generation ceiling.

An additional uncached Go test run passed with this exact environment:

```sh
export DYLD_LIBRARY_PATH="$PWD/target/debug"
export LD_LIBRARY_PATH="$PWD/target/debug"
export CGO_LDFLAGS="-L$PWD/examples/go-jobs/target/debug -L$PWD/target/debug -ldl -lm"
(cd bindings/go && go test -count=1 ./...)
```

The final kit command passed after strengthening the callback reentry regression;
the SDK tests reran and the unchanged reducer tests reused their passing cache. macOS emitted harmless duplicate `-ldl`/`-lm`
linker warnings.

## Rust and existing bindings

Commands run:

```sh
CARGO_INCREMENTAL=0 cargo test --workspace
CARGO_INCREMENTAL=0 cargo test --test ffi --test conformance --test automatic --test oracle
CARGO_INCREMENTAL=0 cargo test --manifest-path examples/go-jobs/Cargo.toml
CARGO_INCREMENTAL=0 cargo clippy --workspace --all-targets -- -D warnings
CARGO_INCREMENTAL=0 cargo clippy --manifest-path examples/go-jobs/Cargo.toml --all-targets -- -D warnings
cargo fmt --all --check
cargo fmt --manifest-path examples/go-jobs/Cargo.toml --check
git diff --check
python3 scripts/build-bindings.py --kind native
python3 scripts/check-native.py
swift test --package-path bindings --scratch-path target/swift-build \
  -Xlinker -L -Xlinker "$PWD/target/bindings/native/lib" \
  -Xlinker -rpath -Xlinker "$PWD/target/bindings/native/lib"
CARGO_INCREMENTAL=0 RUSTC="$(rustup which --toolchain stable rustc)" \
  rustup run stable cargo build --release --lib --target wasm32-unknown-unknown --offline
node --test bindings/browser/test.mjs
node bindings/browser/check.mjs
```

The final Rust workspace run passed 225 tests, including doctests and compile-fail
checks. Its complete log is `target/go-kit-rust-tests.log`.

Focused Rust checks passed: 6 automatic, 2 conformance, 6 FFI, 8 oracle tests.
The two reference job tests passed. Both Clippy checks and formatting/diff checks
passed. Native C header/library smoke and relocation passed. Swift: 3 passed.
Node/Wasm adapter tests: 8 passed; fixture check passed recording/replay,
deliberate divergence, truncated-artifact rejection and bounded enumeration.

The Homebrew Rust compiler has no Wasm standard library; the initial plain cargo
Wasm build failed with E0463. `rustup run stable cargo` alone still selected the
Homebrew compiler in this environment. Explicitly setting RUSTC to the installed
rustup compiler resolved it. Native Go qualification uses Rust 1.99.0; Wasm uses
Rust 1.90.0. Native executable launches sometimes stalled before application
entry (`_dyld_start` in a process sample); a tiny C probe also timed out. These
launch delays eventually cleared; all listed final checks passed. They are not
reducer disagreements or performance evidence.

## Limits

Generic Go-facing fuzz/shrink APIs are not exposed. The reusable Rust paired
layer uses the existing engine; the job bridge exposes paired enumeration,
shrinking and replay for this example. ABI version 1 behavior remains unchanged.

Only native macOS arm64 is qualified for Go; Linux, Windows, cross compilation,
other architectures, race/ASan checks and live browser UI were not tested. Node
Wasm checks do not establish browser UI qualification. Independent correctness
properties remain necessary: agreement can preserve a reference bug. Bounded
agreement is not universal equivalence. Database, filesystem and process adapters
remain outside the deterministic core comparison.
