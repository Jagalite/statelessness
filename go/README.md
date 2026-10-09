# Native Go Statelessness

An independent Go engine, not the Rust-backed SDK in `../bindings/go`. Go 1.23+
(including generics), no third-party packages, no cgo or Rust toolchain required.
Module: `github.com/Jagalite/statelessness/go`.

```sh
cd go
CGO_ENABLED=0 go test ./...
go vet ./...
go run ./examples/counter
go build -o bin/stateless-corpus ./cmd/stateless-corpus
```

From the repository root, qualify the real Go engine against the shared suite:

```sh
python conformance/run.py --runner '["go/bin/stateless-corpus"]'
```

Append `.exe` on Windows. For a separate consumer before publication, use:

```go
require github.com/Jagalite/statelessness/go v0.0.0
replace github.com/Jagalite/statelessness/go => /path/to/statelessness/go
```

Import it as `s "github.com/Jagalite/statelessness/go"`. The counter in
`examples/counter/main.go` is a complete native model that finds a failure, records
its witness, and replays it. No JSON is used by the transition function.

## API and ownership

`Model[State, Input, Output]` contains application-owned callbacks. `Enumerate`
uses `Inputs` (a lazy, error-aware iterator), `EqualStates`, and optional
`HashState`. Hash collisions always require exact equality; equal states must
hash equally. A missing hash uses one equality bucket and can be slower.

`CloneState`, `CloneInput`, and `CloneOutput` are required snapshot policies.
`ValueCopy` is appropriate only for immutable/value-only types. A struct with
slices, maps, pointers, or mutable nested references needs a real copy. The engine
does not infer deep copying from Go assignment. No retained state may mutate as a
later transition evolves. Callbacks are deterministic and must not execute real
I/O or effects; effects are ordinary output data.

`CheckObserved` checks an already executed transition without calling `Step`.
`Record`/`RecordWithLimits` use optional `Codec` callbacks to save owned byte copies.
`Replay` restores the checkpoint, checks compatibility and canonical encodings,
and compares ordered observations. `ParseTrace`/`MarshalTrace` implement the
JSON observation envelope. `Rng` is full-width SplitMix64, not a fuzzing engine.
Panics in model callbacks become `ModelError`; process termination and data races
are not recoverable model findings. Native trace limits count retained data, not
hidden callback allocations or process RSS.

Implements `core-v1`, `bfs-v1`, `rng-splitmix64-v1`, `trace-json-v1`.
Fuzzing/shrinking, guided search, oracle/campaign/composition helpers, ring recording,
and binary `.sttrace` interoperability are not provided by this release. See
`../spec/README.md`; passing finite tests is not universal correctness proof.

No module release/tag or registry publication is performed by this change. Future
module releases use the repository submodule tagging convention (`go/vX.Y.Z`).
