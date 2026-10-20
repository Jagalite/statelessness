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

## Experimental Rust audit binary v1

`AuditTrace` adds an explicit `RunConfig` to the ordinary portable `Trace` shape.
It preserves strategy, absent versus zero/full-width seeds, ordered duplicate
parameter pairs, all bytes/checks/dispositions, and exact model-error text.
`AuditFromPortable(trace, config)` requires caller-supplied configuration;
`audit.Portable()` returns both the portable trace and the omitted configuration.
Both conversions own their snapshots and reject an unrepresentable error string.

`WriteBinary` / `WriteBinaryWithLimits`, `MarshalAuditTrace`, `ReadAuditTrace`, and
`ParseAuditTrace` implement Rust's existing `.sttrace` binary-v1 framing, strict
UTF-8, CRC32, record order/index/footer, and EOF requirements. `BinaryLimits`
provides all nine independent Rust quotas. Writers preflight the complete artifact
before publishing the first byte and reuse one bounded frame buffer. I/O failure
can leave a partial stream; the codec does not flush, close, sync, or publish files.
Readers require a bounded input stream and host I/O deadlines where appropriate.

`native-binary-v1` is experimental, not a qualified profile. Structural parsing
preserves unusual check/termination combinations without silently repairing them.
It establishes neither semantic replay nor successor-state/transition check phase
boundaries: binary v1 only identifies initial-state phases; subsequent checks have
unknown legacy phases. `conformance/native-binary-v1.json` supplies shared fixed
bytes and malformed fixtures; tests honor `STATELESS_BINARY_CORPUS` for copied
module trees. Binary v2 evidence and durable publication remain separate work.

## Experimental BFS controls and retained-state estimates

`EnumerateWithLimits(model, config, SearchLimits{...})` extends existing FIFO BFS
with the same cooperative `RunLimits` used by fuzz/shrink and an optional
`MaxEstimatedStateBytes *uint64` admission cap. Existing `Enumerate` defaults stay
unchanged. `Model.EstimatedStateBytes(state)` returns `(*uint64, error)`; a nil hook
or nil result means unknown. `SearchReport.EstimatedRetainedStateBytes` is nullable.
Uncapped missing estimates/overflow make the total unknown; a capped missing
estimate is an error. Initial over-cap reports zero admitted states/bytes;
successor overflow/over-cap preserves the prior admitted total. Exact fits are
accepted, duplicate successors are not estimated twice, and the state-count cap
precedes successor estimation. Checks complete before failure-versus-stop decisions.

`EnumerateObserved` accepts the existing `SearchObserver`. Its optional new
`NeedsHistory func() bool` controls BFS histories only (nil means false); fuzz
continues supplying its full case prefix independently. Requested BFS histories
are detached initial-rooted paths including the current edge, including duplicate
and failing turns. Incomplete checker batches use the same diagnostic/error rules
as fuzz observers. Hash collision chains compare actual states newest-first.

`Oracle.EstimatedStateBytes(history)` extends the independent oracle surface.
`Paired` forwards reference estimates. `WithOracle` sums actual/history estimates
and `OracleStateOverhead[S,H]()` using checked unsigned64 addition; missing values
or overflow remain unknown. The exposed overhead is actual architecture-specific
Go inline pair padding (`unsafe.Sizeof(pair)` minus member sizes), excluding heap
headers and RSS. Each application estimate includes its own owned representation.
Generation, enumeration, and persistence continue using the combined native model.

These estimates bound admitted model-state representation only. Temporary
successors, predecessor inputs, frontier/maps, callback allocation, and process RSS
are outside the budget. `bfs-controls-v1` remains an experimental candidate;
qualified profiles and the existing portable runner wire schema stay unchanged.
Shared callback-order/report tests read `conformance/bfs-controls-v1.json`, with
`STATELESS_BFS_CORPUS` override for copied module test trees. Append-only checking
storage and richer/bounded replay are separate remaining work.

## Experimental append-only checking

`Model.CheckStateInto(state, sink)` and `CheckTransitionInto(before, input, turn,
sink)` append to `*CheckSink` and return `error`. They take precedence over legacy
collection-returning callbacks; legacy models still run unchanged. A state model
may supply only the append hook. Missing transition checking remains empty.
The same preferred dispatch is used by execution, BFS, fuzz, shrink, recording,
and replay, including initial checks. Automatic and oracle generation wrappers
retain these hooks.

A sink exposes `Push`, `Extend`, `Reserve`, `Len`, `IsEmpty`, and detached
`Snapshot` operations. It cannot remove/replace preceding checks or expose the
backing slice. Every callback receives a fresh synchronous lease, revoked on
success, failure, malformed append, and contained panic. Nested adapter callbacks
get child leases; expired child access cannot revoke or corrupt the still-active
outer callback. Invalid writes/reservations poison the active enclosing batch,
even if a callback ignores the returned error. An expired old lease cannot poison
a fresh batch. Checks are validated/copied on append; snapshots are detached.

`CheckObservedInto(model, before, input, turn, sequence, policy, &checks)` checks
an already-completed turn without stepping. It clears logical caller storage at
entry and on every error while retaining capacity. `CheckObserved` is its
collection-returning convenience API. Caller storage is exclusive during the
operation: do not mutate aliases, reenter with that buffer, or use it concurrently.
`WithCheckSink(&checks, callback)` offers scoped direct append access without
clearing an existing valid prefix. `Model.AppendStateChecks` and
`AppendTransitionChecks` select the preferred callback with a fresh child lease.

Search observers preserve actual partial prefixes and completed-state versus
incomplete-transition batches when checking fails; ordinary observed-into checking
exposes no partial success. Recorders retain only fully checked committed turns.
`Oracle` has matching append hooks; `WithOracle` appends actual then oracle checks,
and `Paired` appends reference checks before projection comparisons, all sharing
existing append positions. Child failures remain failures if enclosing code ignores
them. Legacy Go callbacks returning checks plus an error retain only their valid
prefix for diagnostics and preserve the original checker error.

`append-checks-v1` is experimental. Reusable check buffers retain capacity, while
observer/trace snapshots and legacy defaults can allocate; no zero-allocation or
process-memory claim is made. Shared tests read `conformance/append-checks-v1.json`
or `STATELESS_APPEND_CORPUS` in copied package trees. Rich/bounded replay and the
other remaining parity work are separate scopes.

`encode-buffers-v1` and `replay-evidence-v1` are experimental candidates.
`Codec.EncodeStateInto`, `EncodeInputInto`, and `EncodeOutputInto` take precedence
once over allocating encoders. `WithEncodeBuffer(&bytes, maximum, callback)` clears
caller storage at entry/error and retains capacity. Append operations, `Framed`
(u64 little-endian length), and nested callbacks share a sticky byte budget.
Callback leases expire on return, error, and panic; snapshots are detached and
failed buffers cannot publish bytes. `Len` is diagnostic after failure. Snapshot
allocation failures are contained as buffer failures; process-wide allocation
failure is not a recoverability guarantee. Caller buffers require exclusive
synchronous use. ORC1 encoding frames actual state and oracle history incrementally
under the same cap, including `Oracle.EncodeHistoryInto`; generation wrappers
retain the preferred codecs. No zero-allocation claim is made.

`RecordAudit(model, inputs, config, maxSteps, limits)` records lossless AuditTrace
artifacts with all nine binary quotas, shared turn budgets, and reserved error
footer space. It snapshots config and limits before callbacks, rejects caller
`stateless.engine.*` parameters, and appends ordered engine version/build stamps.
The `native-go-v1` build stamp identifies the implementation, not its source hash.
Only complete checked turns are committed; later errors retain a coherent prefix.

`ReplayWithObservations` accepts a portable Trace and `ReplayObserver` callbacks
`Initial`, `Turn`, and `Error`. It retains actual execution evidence, all four
comparison flags, and the updated report, stopping at the first divergence.
`ReplayWithObservationsBounded` accepts AuditTrace and BinaryLimits, validates the
stored artifact after identity compatibility and before decode, then accounts for
actual evidence with shared frame, total-byte, and item quotas. Stored producer
configuration is retained. Basic `Replay` uses the same kernel. A `ReplayError`
retains actual initial/turn evidence and secondary observer errors. When a
snapshot fails, the error observer receives no Actual plus SnapshotError; the
returned error retains recovery evidence without retrying model callbacks.
Shared tests use `STATELESS_ENCODE_CORPUS` and `STATELESS_REPLAY_CORPUS`, with
repository-relative fixture paths by default.
