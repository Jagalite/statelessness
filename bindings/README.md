# Language adapters

Status: experimental ABI 1 packages for canonical byte models. Reusable C, Swift,
Go and JavaScript interfaces cover recording, exact replay and finite enumeration.
Rust remains the full-featured interface; generic foreign fuzzing, shrinking and live
recording are not exposed. See [support status](../SUPPORT.md) for tested runtimes.

These initial adapters exercise the same Rust recorder, trace format, invariant
observations, and exact replayer as native Rust models. The application reducer
and property functions remain in Swift or JavaScript. They deliberately use
copied, canonical byte states; opaque application-state handles, fuzz-generation
callbacks, asynchronous workers, and allocation optimizations are future work.
There are no third-party package dependencies.

## Native ABI version 1

The C header is [c/stateless.h](c/stateless.h). Model creation copies the versioned
callback table. A caller-owned context must outlive its model; all calls on a
model are confined to one thread and cannot reenter that model. Callbacks execute
synchronously and must not throw, panic, or unwind across the C boundary.
Context contains configuration only: all mutable logical state, pending work,
and property history must roundtrip in the canonical state bytes.
Native Rust entrypoints catch ordinary Rust unwinds; process aborts, invalid
pointers, foreign exceptions, and Wasm traps cannot be recovered by this API.

Model and buffer handles are opaque. Free each owned handle exactly once. A
callback's response buffer is **borrowed** and must not be freed or retained.
Use `stateless_buffer_assign` to copy a response into that buffer. Data pointers
remain valid until buffer assignment or destruction. Never access a handle
concurrently. Names/build identities and diagnostic strings use UTF-8.

Status codes distinguish success (0), a captured/reproduced property failure (1),
replay divergence (2), incompatible identity (3), bad arguments (10), callback
or model errors (11), trace errors (12), and caught Rust panics (13). The calling
thread's error text can be copied with `stateless_last_error`. An output artifact
is replaced only when recording returns 0 or 1. Always inspect trace termination:
a matching prefix stopped by a step budget does not establish completion.

The callback receives operation, before-state bytes, input bytes, and a response
buffer. Operations and response packets are:

| Operation | Input arguments | Response |
|---|---|---|
| 0: initial | Empty spans | Canonical initial state bytes |
| 1: step | State and action bytes | Transition packet |
| 2: state checks | State; empty input | Checks packet |
| 3: transition checks | Before state; blob(action) + transition packet | Checks packet |
| 4: enumerate inputs | State; empty input | Batch of all permitted next inputs |

All lengths and counts below are unsigned 32-bit little-endian integers.
`blob` is a length followed by that many bytes. Strings are UTF-8 blobs.

```text
transition = u8 disposition, string reason, blob next_state,
             u32 output_count, repeated blob output
checks     = u32 check_count,
             repeated (string property_id, u8 status, string details)
batch      = u32 input_count, repeated blob input
```

Disposition: 0 accepted (empty reason), 1 rejected, 2 ignored.
Check status: 0 passed (empty details), 1 failed, 2 skipped. Empty property IDs,
unknown tags, malformed strings, truncated packets, and trailing bytes fail.
Incoming byte spans are capped at 64 MiB and lists at one million items.
Callbacks are responsible for canonical payload encodings and for rejecting
malformed application states/actions, including restored states. State checks
must validate a restored state before interpreting it. Never execute real
external effects from these simulation callbacks.

The supplied build identity is an application assertion. Supply an actual
content/build fingerprint for real use; the fixtures use explicit example
version strings. Replay enforces exact metadata and compares recorded state,
outputs, dispositions, and property observations.

## Swift fixture

From the repository root on macOS:

```sh
python3 scripts/build-bindings.py --kind native
swift test --package-path bindings --scratch-path target/swift-build \
  -Xlinker -L -Xlinker "$PWD/target/bindings/native/lib" \
  -Xlinker -rpath -Xlinker "$PWD/target/bindings/native/lib"
target/swift-build/debug/swift-counter record target/swift-counter.trace
target/swift-build/debug/swift-counter replay target/swift-counter.trace
target/swift-build/debug/swift-counter changed target/swift-counter.trace
target/swift-build/debug/swift-counter enumerate target/swift-enumerated.trace
```

The counter permits incrementing past its bound, then reports `counter_bound`.
Recording and replay occur in separate processes. Its `changed` command replays
with a deliberately changed reducer and expects first-transition divergence.

## Browser / JavaScript fixture

```sh
cargo build --release --lib --target wasm32-unknown-unknown --offline
node --test bindings/browser/test.mjs
node bindings/browser/check.mjs
```

The Node check instantiates the same `.wasm` and handwritten JavaScript adapter
used by [browser/index.html](browser/index.html); Node execution is not itself
browser validation. To inspect in a browser, serve the repository root over HTTP
and open `/bindings/browser/`. The page exposes failure recording, fresh-model
replay, deliberate divergence, and a download of the durable trace.

The Wasm module imports `stateless_host.dispatch` with the same callback
arguments. JavaScript handles refer to Wasm linear memory only. Reacquire typed
array views after calls that can grow memory. Calls and checks are synchronous;
this interface inserts no asynchronous hop into a reducer call. Initial Wasm
fetch/instantiation is asynchronous. This is a correctness fixture; no low
overhead claim or production browser integration is established.

## Local distributions and application integration

With Python 3.11+, `python3 scripts/build-bindings.py` builds native and Wasm distributions under
`target/bindings/`, with source-package license files and `manifest-all.json`
SHA-256 entries for every file copied by that invocation. `--kind native` and
`--kind browser` build independently and write corresponding manifests. Install
the Rust `wasm32-unknown-unknown` target before a browser build. Generated bundles
are host-specific and are not uploaded or published by this command. On macOS,
the distributed dylib uses `@rpath` and an ad-hoc signature so it can be relocated;
application signing/notarization remains the host application's responsibility.
Run `python3 scripts/check-native.py` to compile and execute a relocated consumer.

Builds stage an explicit list of public files before replacing the selected
bundle directories. Keep your own files and packed archives outside those
generated directories; rebuilding removes obsolete contents. The generated npm
version follows `Cargo.toml`. Failed builds preserve the previous bundles, and
replacement errors restore them. Partial rebuilds invalidate the old combined
manifest while preserving the other component's separate manifest. Concurrent
builders are rejected by `target/.bindings-build.lock`.

- C: use `native/include/stateless.h` and link the matching `native/lib` library.
  `bindings/c/smoke.c` verifies the header against the built library.
- Swift: add a local SwiftPM dependency on `target/bindings/swift`, select the
  `StatelessNative` product, and configure the native library search/runtime path
  as above. Implement `ByteModel` with `Metadata`, `Transition`, and named `Check`
  values. `Session(model)` owns the native handle and exposes `record`, `replay`
  and `enumerate`. The updated [counter](swift/Counter.swift) is a complete model.
  Sessions are synchronous and thread-confined; callbacks throw Swift errors
  that are contained at the ABI. Returned artifact arrays are owned copies.
- JavaScript: install the generated `target/bindings/browser` directory locally,
  or run `npm pack --offline` there. Its [guide](browser/README.md) documents the
  generic `createModel` API; `createCounter` is only a fixture on top of that API.
  Serve `target/bindings/` to run the packaged `browser-fixture/` separately from
  the source tree. No npm runtime dependencies are needed.

These are source/local packages, not prebuilt universal binaries, an XCFramework,
a hosted Wasm service, or published npm/Swift registry releases. Native libraries
must be rebuilt for the consumer's target architecture. The Swift package is
currently qualified on macOS only. Node tests and browser tests are distinct:
`browser/self-test.html` exposes pass/fail results for real browser execution.

## Go and paired conformance

See [go/README.md](go/README.md) for the opt-in SDK and the native Rust/Go
paired job example. `scripts/check-go.sh` builds both and reproduces a reduced
disagreement in a fresh process. Generic Go fuzz/shrink APIs remain separate
from the Rust paired harness.
