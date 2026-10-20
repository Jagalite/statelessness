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
Guided search, campaign/composition helpers, ring recording,
and binary `.sttrace` interoperability are not provided by this release. See
`../spec/README.md`; passing finite tests is not universal correctness proof.

No module release/tag or registry publication is performed by this change. Future
module releases use the repository submodule tagging convention (`go/vX.Y.Z`).

## Experimental paired history

`ExperimentalProfiles()` lists `paired-v1`; the qualified `Profiles()` list remains
unchanged. `Paired(reference, projection)` creates an independent reference oracle,
and `WithOracle(actual, oracle)` returns an `OracleModel` embedding the ordinary
`Model`. Pass its `.Model` to `Enumerate`, `Record`, `Replay`, and `CheckObserved`.
`Projection` callbacks map native state/effects to explicit semantic values and
supply equality. Reference advancement only receives its previous history and
input, never actual next state or effects. Checks retain actual/reference ordering,
then compare projected state, ordered effects, and the full disposition/reason.

`Attach(actualState, referenceHistory)` requires both saved checkpoints and clones
them. `ObserveTransition` wraps a completed actual transition without executing its
reducer; a typed `ObservationError` retains the actual transition if history
advancement fails. `CloneHistory` must deeply copy mutable history; enumeration
requires both actual state equality and `EqualHistory`. Persistence requires both
component codecs and uses the shared `ORC1` framing and semantic/build identities.
This is an experimental candidate, not complete Rust parity or a qualified profile.

The golden tests read `../conformance/paired-v1.tsv`. For copied module test trees,
set `STATELESS_PAIRED_CORPUS` to the shared fixture's absolute path; a missing fixture
is an error rather than a silent skip.

## Experimental generation, fuzzing, and shrinking

`search-v1` is an independent native candidate in `ExperimentalProfiles()`; it does
not expand the qualified portable profile list. `GenerateModel` embeds the base
`Model` without changing existing models. Supply `Generate(state, rng)` returning
`(input, present, error)`, optional `IsEnabled`, optional lazy `SimplerInputs`, and
`InputEqual` for semantic identity. Absence ends the case and does not exclude nil
as a supported input value. Custom enabled checks default to true and simplification
defaults to no alternatives. All callbacks remain pure and receive snapshots.

`Auto(model, inputEqual, DefaultAutoOptions())` creates generation and enabled
membership from finite `Inputs`. Reservoir sampling respects declared order and
duplicate weighting, scans the complete domain, and commits RNG only on success.
The positive candidate cap permits one extra pull to establish overflow; it never
samples a truncated domain. Membership stops at its first matching entry.

```go
generated, err := s.Auto(model, func(a, b Input) (bool, error) {
    return a == b, nil // use semantic equality for reference-containing inputs
}, s.DefaultAutoOptions())
if err != nil { return err }
report, err := s.Fuzz(generated, s.DefaultFuzzConfig())
if err != nil { return err }
if report.Failure != nil {
    minimized, err := s.Shrink(generated, *report.Failure, s.DefaultShrinkConfig())
    if err != nil { return err }
    _ = minimized // inspect termination, validation, budgets, and attempt history
}
```

`FuzzWithLimits` performs seeded state-aware generation and mutation of the previous
passing case. It validates generated inputs, preserves the full first failing batch,
and bounds cases, steps, and aggregate transitions. `ShrinkWithLimits` first validates
the original first failed check ID and phase, deletes chunks, then tries ordered
simplification alternatives. It checks causality, guards accepted sequence cycles,
and records every replay attempt. Search completion promises neither proof nor a
globally minimal witness. Reports include resource termination and whether original
evidence was validated before shrinking; interrupted validation preserves supplied
evidence without asserting reproduction.

`RunLimits` supports optional `*time.Duration` and `*atomic.Bool` cancellation.
Zero duration expires at entry. Controls poll between engine callbacks, never
preempt callbacks or an automatic iterator scan. Callback failures stay errors,
and completed failing check batches win over simultaneous cancellation.
`ShrinkLimits.MaxReplayedTransitions` optionally bounds aggregate replay work,
including original validation. Deadlines use the Go monotonic clock; unconstrained
runs do not read the clock. Separate runs need separate RNGs and mutable models.

Tests consume `../conformance/search-v1.tsv`, or the explicit
`STATELESS_SEARCH_CORPUS` path when testing a copied module tree.

`FuzzObserved` accepts `SearchObserver` hooks for initial checks and every completed
checked transition, including failing turns and their full case history. State,
input, effects, history, and check slices are defensive snapshots. `CheckError`
reports incomplete checker batches; a completed state batch followed by a failed
transition checker emits state-complete then transition-incomplete diagnostics.
Valid partial checks explicitly returned alongside a Go callback error remain
available to diagnostics, while the error aborts the run. Observer errors/panics
also abort; secondary diagnostic errors preserve the original checker error.
The shared attempt-history fixture defaults beside the search TSV, with optional
`STATELESS_SEARCH_HISTORY` override.

`WithOracleGenerate(generated, oracle)` wraps a `GenerateModel` while preserving
its combined history model/codec. Generation and causal membership see only the
actual application's state; input simplification and semantic identity forward
unchanged. The resulting typed `GenerateModel[OracleState[...], ...]` supports the
same native fuzz, shrink, recording, and exact replay pipeline.
