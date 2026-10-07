# Language adapters

Status: reference prototypes. Further binding work is deferred while the Rust
library and CLI are completed. Refresh these adapters and their validation against
the settled Rust API in the later binding phase.

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
cargo build --release --lib --offline
swiftc -I bindings/c -L target/release -lstateless \
  -Xlinker -rpath -Xlinker "$PWD/target/release" \
  bindings/swift/Counter.swift -o target/swift-counter
target/swift-counter record target/swift-counter.trace
target/swift-counter replay target/swift-counter.trace
target/swift-counter enumerate target/swift-enumerated.trace
```

The counter permits incrementing past its bound, then reports `counter_bound`.
Recording and replay occur in separate processes. Its `changed` command replays
with a deliberately changed reducer and expects first-transition divergence.

## Browser / JavaScript fixture

```sh
cargo build --release --lib --target wasm32-unknown-unknown --offline
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
