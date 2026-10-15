# Typed Go ABI 2 adapter

`github.com/Jagalite/statelessness/bindings/go/v2` keeps typed model values and
callbacks in Go while Rust runs exploration, generation, shrinking, recording,
replay and bounded observation recording. The existing `bindings/go` ABI 1
package and independent `go` module remain available.

Native use requires cgo and an ABI 2 `libstateless`. Supply its directory through
`CGO_LDFLAGS=-L/path/to/library` and configure the platform loader as for ABI 1.
The package's structural trace JSON/packet helpers also compile without cgo.
There is no independent exploration or replay implementation in this package.

```go
err := v2.WithSession(model, v2.DefaultOptions(), func(s *v2.Session[State, Input, Output]) error {
    result, err := s.Enumerate(v2.DefaultBounds())
    if err != nil { return err }
    defer result.Close()
    // Inspect Status, Termination, counts, Checks, CheckPhases, and Inputs.
    return nil
})
```

`WithSession` pins the goroutine to its creating OS thread and closes the engine
before releasing its `cgo.Handle` context. Sessions, rings and live result handles
are confined to that scope. Callbacks are synchronous. Recursive operations and
calls on a different thread fail. `Cancel` is the sole cross-goroutine operation;
`ResetCancellation` is available on the owning thread.

Every model supplies `InitialState`, `Step`, `CheckState` and three explicit
snapshot functions. Use `ValueCopy` only for immutable scalar/value types; maps,
slices and pointers require real deep snapshots. The adapter snapshots borrowed
values before callbacks and admitted values afterward. Equality and hashing are
semantic callbacks; codecs remain optional. Equal states must produce the same
hash when a hash callback is supplied. Constant-hash lookup remains correct.

Optional model hooks enable capabilities:

- `Inputs` and `EqualStates` enable lazy finite enumeration. `Iterator.Close`
  runs when the cursor is released, including early termination and failures.
- `Generate` and `EqualInputs` enable fuzzing and shrinking; `SimplerInputs` and
  `IsEnabled` are optional. `Automatic` selects the shared Rust reservoir sampler
  over `Inputs`, with transactional random state. Choose one generator mode.
- `Codec` enables binary recording, replay and ring snapshots.
- `CheckObserved` checks a supplied transition without invoking `Step`.
- `NewRing`, `Observe`, and `Snapshot` retain bounded observed evidence and never
  invoke `Step`; a violation or recorder error freezes the coherent prefix.

`RecordIterator` consumes inputs lazily. A late callback failure can return both
a result containing the coherent binary prefix and an error. Close that result
as usual. Every other completed result owns its Go values and copied bytes; its
private engine handle remains available to `Shrink` until `Close` or teardown.
Snapshots remain readable after closing the result or session. Callback panics
become model errors. Iterator cleanup errors are returned from `WithSession`.

`RNG` and callback-scoped `CallbackRNG` use Rust's full-width SplitMix64 functions.
A callback RNG expires when its generator returns. `ExportJSON`, `ImportJSON`,
and `ReplayJSON` use the strict portable `trace-json-v1` envelope and the engine's
binary codec. JSON deliberately omits binary run configuration; it is an
observation projection, not a lossless audit archive. `NeutralPacket` and
`BinaryTrace` preserve the complete structural packet when that is needed.

The source regressions cover callbacks, cursor cleanup, errors with coherent
prefixes, observations without reducer execution, collisions, shrink witnesses,
RNG vectors and strict transport parsing. Runtime tests and performance
qualification are tracked in [the status report](../../../docs/ABI-V2-STATUS.md);
compilation alone does not establish runtime conformance.

The optional `v2/cmd/stateless-corpus` executable speaks the existing bounded
newline JSON conformance protocol. Its fixture tables and structural JSON reader
are reused from the independent Go adapter, while every model operation, random
draw and replay runs through ABI 2. The corpus test is present for a later runtime
gate; compiling the command or test does not certify the portable profiles.
`RecordPortable` applies the portable blob, aggregate item, payload, step and JSON
limits through the shared recorder before returning an observation envelope.

Benchmark source `benchmark_test.go` defines matched `Cheap`, `Large`, and
`Expensive` fixtures: a 32-state modulo counter, ordered inputs `[1, 2]`, previous
state as output, and stable state/transition checks. Large carries 64 KiB in every
state; Expensive performs 4096 deterministic check iterations. Each fixture
measures seven separate operations: one observed check, complete 32-state/64-edge
BFS, 64-event recording, 64-event replay, seeded fuzzing, shrinking a failure that
targets state >= 24, and a 64-event ring batch retaining sixteen steps. Replay
artifacts and original shrink failures are prepared outside the timed region, as
are registration and teardown;
all callback dispatch, snapshots, result copying and explicit cleanup inside each
operation are included. Counters report callbacks, reducer calls, snapshots,
snapshot/codec byte volume, and retained roots when results are live, alongside
Go allocations. These are Go allocator counters, not a measurement of native
Rust allocation or process peak RSS. `roots-at-end` verifies explicit cleanup. These sources establish
workloads only; no throughput, latency, memory, or regression budget is claimed.
