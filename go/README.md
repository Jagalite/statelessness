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

`checked-results-v1` is an experimental check-once handoff. `CheckInitial(model,
state)` checks the supplied checkpoint without calling InitialState or Step.
`CheckTurn(model, before, input, transition, sequence, policy)` checks an already
executed turn without invoking Step. Positive `uint64` sequences preserve the
full u64 domain. Failed properties still produce a complete token; checker,
snapshot, policy, and validation errors produce no token. Sampled checking and
disabled transition checks retain explicit skipped entries and never claim full
coverage. Existing CheckObserved and append APIs share the same dispatch.

`CheckedInitial` and `CheckedTurn` have private fields and constructors. Their
zero values have no authority. Context snapshots are captured before checking;
checkers receive defensive copies of those snapshots. Checks, context accessors,
and `TurnObservation` diagnostics return detached data. Accessors that clone
application values return errors if cloning fails. Faithful deep clones and pure,
deterministic callbacks remain the model's responsibility; the engine cannot
verify clone fidelity without imposing new equality or codec requirements.
Privacy protects ordinary API use, not hostile reflection or unsafe operations.
Diagnostics are not checked tokens. Tokens require no codec and may feed multiple
independent recorders; they are not globally consumed or persistent authority.

`NewRecorderWithCheckedInitial` and `NewRecorderWithCheckedInitialAt` attach a
runtime Recorder to a checked checkpoint without rechecking. `ObserveCheckedRecorder(recorder, token)`
uses the token's bound model, turn, and complete checks without reducers or
checkers. It requires full coverage, exact host sequence continuity, matching
metadata, and encoded before-state continuity. Ordinary `NewRecorderWithOptions`
and `ObserveRecorder(recorder, model, before, input, transition)` check supplied
application execution once. Recorder storage is independent of Go payload types;
compatible metadata and canonical bytes permit handoffs across distinct payload
representations. RecorderOptions bounds
the retained step window and complete serialized artifact under BinaryLimits.
Eviction promotes only state-phase checks to the new checkpoint. Property or
recording failure freezes the coherent prefix; later errors cannot replace it.
Snapshots are detached AuditTrace values, with local trace indexes and explicit
host sequence origin/counter metadata. Retention measures serialized evidence,
not callback allocations or process memory. EvidenceRecorder causal envelopes
remain a separate scope. Shared tests use `STATELESS_CHECKED_CORPUS` or
`conformance/checked-results-v1.json` in a repository checkout.
Recorder mutation is synchronous and exclusive: nested observation or transfer
from an application callback is rejected (IntoTrace returns nil during capture). A callback may stop recording with an
error; that first frozen prefix remains authoritative when the outer call resumes
or encounters another error. Error formatting runs under the same mutation guard.
Read-only snapshots and counters remain available. Retention admission reports
format total-byte, aggregate-item, and format step limits before recorder-specific
byte and step budgets when multiple global limits are exceeded.
Binary export holds a read borrow over the recorder: writer callbacks cannot
observe, transfer, or stop it. StopWithError returns a rejection error during
export. The borrow is released even if a writer panics; partial I/O remains the
writer's responsibility. Export streams the retained artifact directly.

`causal-evidence-v2` is experimental. `TraceV2` adds owned Provenance, absolute
checkpoint/step sequences, state-check boundaries, and CausalContext output links
to a lossless AuditTrace. `Artifact` distinguishes v1 from v2; passive v1 reading
preserves unknown turn phases. `ReadArtifact`/`ParseArtifact` and `Write`/marshal
APIs use exact little-endian v2 framing, canonical structural metadata, and CRC32.
EvidenceLimits independently bounds all nine trace quotas, metadata bytes,
environment labels, label bytes, causes per step, and aggregate causes. Metadata
parsing additionally charges bytes, elements, allocation, nesting, and work;
requested collection allocation uses fixed Rust64 schema accounting weights,
independently of v1 item limits. These are portable structural charges, not Go
allocator or heap measurements. These bounds do not measure process RSS or host callback memory.

`ReplayTraceV2` validates evidence before bounded replay and compares the persisted
state-check boundary only after semantic comparison succeeds. `VerifyUpgrade`
is explicit execution: exact compatible bounded replay derives phases; passive
reading never runs a model or guesses phases. Both preserve full u64 host values.
Snapshots and upgrade provenance are detached; callers must not concurrently
mutate artifacts. Host labels and causal claims are not authenticated.

`NewEvidenceRecorder` attaches to a checked checkpoint, reserves the selected
metadata allowance plus 32 envelope bytes, and captures checked turns through
`ObserveEvidenceRecorder`. Capture runs no reducer or checker. Metadata limits
and trace retention plan a single atomic eviction; causal sources are validated
against the current retained output counts before eviction is considered.
Preparation failures preserve the first frozen coherent prefix. Snapshot phases
and exact trace steps remain aligned, and evicted state-phase prefixes become
the checkpoint. Choose metadata allowance explicitly when selecting small total
retention; defaults are not silently reduced.

`DiagnosticJSON`/`ExportDiagnostic` uses the nonreplayable diagnostic schema,
redacts payloads, identity, labels, and check details, and reveals property IDs
only through exact allowlisting. ExportPolicy applies whole-output byte and step
caps. `ExportDiagnosticNew` and `Artifact.ExportRawNew`/`TraceV2.ExportRawNew`
precompute bounded bytes, create exclusively with Unix mode 0600, and sync the
file. They never overwrite; failure after creation can leave an incomplete new
file. These are direct new-file exports, without rename publication or directory
sync guarantees. Shared tests use `STATELESS_CAUSAL_CORPUS` or the repository's
`conformance/causal-evidence-v2.json`.
Upgrade performs pure structural argument admission at entry before copying
mutable evidence. It defers stored-artifact errors until after identity checks
and provenance errors until after exact replay, preserving replay callback order.
Known-unfit collections are not cloned; metadata is evaluated once by replay.

`canonical-value-codec-v1` is an experimental, opt-in module. `ValueCodec[T]` is
separate from model `Codec[S,I,O]`. Builtin constructors cover exact fixed-width
integers, bool, Unit, strict UTF-8 strings, options, vectors, fixed arrays, tuples
of arity 1–4, and OracleState. Integers use native fixed types; Uint128 and Int128
use exact two-limb representations. There are no float, host-int, map, dynamic
object, or implicit narrowing codecs. Option[T] has explicit Present and Value.
String lengths count exact UTF-8 bytes without normalization or BOM removal.
`ArrayOf(n, element)` uses an exactly sized slice with no wire prefix; application
[N]T adapters can explicitly convert through NewValueCodec callbacks.

`EncodeValue(codec, value, maximum)` owns a bounded result and returns no bytes
on failure. `codec.EncodeRaw(value, buffer)` appends through a fresh revocable
lease, matching cooperative direct TraceEncode semantics: returned validation or
callback errors alone do not poison the sink. Builtin raw composition calls its
children raw. `EncodeStringLength`/`EncodeLengthRaw` and `EncodeVectorLength`/
`EncodeVectorLengthRaw` override only their own 1/2/4-byte length prefix.
`codec.EncodeInto` is a stronger native convenience that makes callback errors
sticky, as existing model encoding helpers do. Failed writes remain sticky under
both paths; escaped child leases expire on every return or contained panic.
`FinishValueEncoding(buffer)` exposes Rust's canonical generic finish category
and wraps the underlying native cause. Existing EncodeBuffer.Finish continues to
return the first native cause unchanged. Top-level EncodeValue propagates a raw
encoder error before checking canonical finish, preserving source order.

`NewDecoder(bytes, limits)` checks byte admission before acquiring owned input.
`ReadValue(decoder, codec)` charges one work unit before checking nesting, runs
the concrete decoder, and records the first error. `codec.DecodeRaw(decoder)`
omits that guard; `DecodeStringLength` and `DecodeVectorLength` are direct length
operations, with only a guarded length integer. Returned raw validation errors
can be caught without poisoning the reader; failed Take, Collection, or nested
guards remain sticky. `FromValueBytes` constructs a reader, enters one guard,
and requires Finish. Public readers remain reusable: Finish's trailing-byte
error does not become sticky. Take returns detached bytes; collection admission
precedes storage allocation. Native callbacks can nest guarded decoding normally.

DecodeLimits defaults are independent: bytes 16 MiB, logical allocation 32 MiB,
elements 1,000,000, depth 64, and work 2,000,000. Collection first subtracts elements,
then checks uint64 multiplication, subtracts requested allocation, and checks
remaining work without subtracting it. Strings additionally consume byte work;
zero-sized elements still consume elements and guarded work. Limits/Depth/Remaining
accessors return diagnostics, not mutable reader internals. Catchable callback
panics become constant errors without formatting panic payloads; guards restore
depth and retain an earlier failure. This is a native extension, not Rust unwind
behavior or a guarantee to recover fatal process OOM.

Logical allocation descriptors use explicit Rust64 schema weights, not wire size
or Go heap/RSS. Layout is required only where collection decoding requests element
storage. Top-level options and tuples can decode with unknown layout. Known finite
builtin layouts are selected by private engine identities, never diagnostic schema
strings. Unknown compositions need a caller-declared NewValueLayout and WithLayout
before serving as collection elements; such declarations are not measurements.
No generic option/tuple alignment or niche inference is attempted. Custom callbacks
remain cooperative: truthful declared layouts, purity, and bounded host work are
application obligations. Schema, Variant, and value-aware VariantFor/WithVariantFunc
are diagnostic labels, not compatibility identities or wire tags.

Adoption into model persistence is explicit: connect descriptor EncodeInto to
EncodeStateInto/EncodeInputInto/EncodeOutputInto and use FromValueBytes for state
and input decoders. Outputs remain encode-only. Shared tests use
STATELESS_VALUE_CORPUS or conformance/canonical-value-codec-v1.json. Arithmetic-only
prefix witnesses and invalid values excluded by Go's static types are reported
separately from public API executions.
NewValueCodec accepts independent encode-only and decode-only descriptors; only
supplying neither operation is invalid. Missing operations report a clear native
configuration error at use, and composite descriptors preserve their children's
capabilities. An encode-only descriptor can serve model outputs without inventing
an output decoder. `AdaptFixedArray` supplies explicit to/from conversions for
application [N]T values, validates both conversion lengths, and preserves the
array decoder's original guards and allocation requests.

`LengthValueCodec[T]` independently declares length encode and/or decode callbacks with `NewLengthValueCodec`; it requires no ordinary codec callbacks. `EncodeLengthRaw` and `DecodeLengthRaw` preserve direct-call error semantics and encoder lease expiry, while `EncodeLengthInto` additionally poisons callback errors. `WithWidth` selects an ordinary `ValueCodec` without adding a raw decoder guard. `StringLengthValueCodec` and `VectorLengthValueCodec` reuse the built-in width algorithms.

The canonical decoder owns built-in `ModelError` payloads in sticky failures and returns detached copies. Opaque application errors retain identity and are never formatted or serialized merely to store a failure.

`CompareEquivalence` is an opt-in finite-fixture research harness. Supply a complete explicit state domain, `ResearchProposal` assumptions and required ID/phase properties, `ResearchConfig`, a typed comparable projection key, and exact `ResearchEquality` input/output callbacks; state equality uses `Model.EqualStates`. Supply a final metadata callback for a non-codec model, or omit it to use existing codec metadata. The default config is disabled, but proposal validation and one metadata call still occur. Enabled experiments materialize every declared edge once, including unreachable states, before projection and exact/quotient traversals. Ordered input labels, outputs, dispositions, checks and successor classes must agree. Reports retain deterministic shortest witnesses, compared-pair counts and explicit incomplete/counterexample outcomes; `QualificationParameters` always records production pruning as false.

Agreement qualifies only the supplied finite fixture and caller-established assumptions. This harness does not change BFS, replay equality, fuzzing, or production pruning. Clones must be faithful and detached; equality/projection callbacks must be pure, and pointer/interface-containing comparable keys must not hide mutable equivalence data or unsupported dynamic comparability. State/input/output values passed to callbacks are cloned, but callback execution, iterator termination, allocations and hidden external effects remain cooperative. Limits are exact `uint64` state/edge/pair counters, not execution-time or RSS guarantees.

Projection keys must satisfy equivalence laws corresponding to Rust `Eq`. Go admits comparable values such as floating NaN that violate reflexivity; the harness rejects any key unequal to itself before either traversal. Equality callback laws and faithful cloning remain caller obligations.

Canonical derive-style declarations are opt-in: `OrderedRecord` composes a typed
`FieldProduct` from `OrdinaryField`, `LengthField`, or `AdapterField` descriptors;
`TaggedEnum` selects explicit numeric `TaggedCase` declarations. Record and enum
layouts stay unknown unless `RecordOptions.Layout` or `EnumOptions.Layout`
explicitly supplies a logical Rust64 collection weight. Inference retains every
available direction, including neither; `Require` checks eagerly without
filtering other available directions. `DefaultEnumOptions()` supplies a one-byte
tag, and `DefaultModelCodecOptions()` supplies codec version 1. Their zero option
values intentionally fail validation rather than silently choosing a format.
`BindModelCodec` returns a new model codec for explicit assignment, requires only
encoding for Output, and preserves metadata and engine behavior. It checks the
current metadata version on every hook and evaluates decode limits afresh.

Runtime observation is optional, synchronous diagnostic text. `Event[S,I,O]`
uses `EventStarted`, `EventCheckpoint`, `EventTransition`, `EventMessage`, and
`EventFinished` tags. `Observer[S,I,O]` accepts a model and an application-owned
event. `TextObserver` never calls initial-state, reducer, checker, enumeration,
or effect execution callbacks. Events do not prove continuity or authorize
unchecked recording; use existing `CheckInitial`/`CheckTurn` tokens and Recorder
for exact evidence. `TurnObservation.Event()` is a diagnostic conversion only.

```go
options := statelessness.DefaultObservationOptions()
options.Level = statelessness.LogTrace
options.Snapshots = statelessness.SnapshotEveryTransition()
log, err := statelessness.NewTextObserver[State, Input, Output](
    writer, "application", options,
    statelessness.NewEncodedPayloads[State, Input, Output](4096),
)
if err != nil { return err }
// The application has already executed this turn and obtained these checks.
err = log.Observe(model, statelessness.Event[State, Input, Output]{
    Kind: statelessness.EventTransition, Sequence: sequence,
    Before: before, Input: input, Transition: actual, Checks: checks,
})
```

`DefaultObservationOptions()` is Off / None / 1 MiB. A literal zero options
value retains a zero-byte bound; limits include the final LF and count UTF-8
bytes. Levels are `LogOff`, `LogError`, `LogWarn`, `LogInfo`, `LogDebug`, and
`LogTrace`. Failure checks take Error precedence over skipped checks at Warn.
Ordinary checkpoints are Trace, transitions Debug even when rejected or ignored,
and start/finish Info. Messages carry their own level; Off messages are filtered.
Filtering precedes formatter and codec work. Only configured Trace formats
payloads and Passed check details, even when the record itself is Error or Warn.

`SnapshotNone()`, `SnapshotEvery(nonzero uint64)`, and
`SnapshotEveryTransition()` select post-state payloads by absolute transition
sequence. Explicit Trace checkpoints include state under any non-None policy,
independent of interval. Payload order is input, ordered outputs, then selected
post-state; before-state is unused. `SetLevel` and `SetSnapshots` affect future
events and cannot recover omitted history. Emit a fresh checkpoint when enabling
snapshots mid-run. Sequence and periodic arithmetic retain the full u64 range.

`PayloadFormat` can format opaque application types without a codec; nil selects
`NoPayloads`, which emits `unavailable`. `EncodedPayloads` uses the existing
incremental-before-legacy dispatch, including declaration bindings, a reusable
bounded EncodeBuffer, and lowercase hex. No codec is required on base Model.
Legacy encoders necessarily allocate their return buffers before admission;
clones and other callback allocations are also outside the logical byte limits.
Encoding uses the existing model clone hooks; borrowed custom formatting does
not copy payloads. Formatters must not mutate application data or execute effects.

Events borrow slices, payload reference fields, and config parameters for the
duration of `Observe`; neither event construction nor observation detaches them.
Applications own ordering and must prevent concurrent mutation. Checked tokens
retain their existing detached ownership and private authority. An event copied
from a detached TurnObservation is still diagnostic data. No automatic observer
hook was added to model execution, checking, or recording.

Library text follows the pinned Rust variant names, field order, quoted strings,
`Some(n)`/`None`, and metadata/config spelling in `observation-v1`. It escapes
quote, backslash, NUL, LF/CR/TAB and nonprintable scalars with Rust-style escapes,
while preserving common printable UTF-8. Native Unicode classification uses Go's
Unicode tables, including grapheme-extending marks; exact bytes across every
Unicode version/category or future Rust Debug format are not promised. Invalid
library-owned UTF-8 is rejected. Custom formatter bytes are unsanitized; valid
UTF-8 and single-line output are the caller's responsibility. This is diagnostic
text, not a replay or persistence format.

Every record is staged before destination I/O. Record/encode buffer failures are
sticky even when a callback swallows an error. Format/codec errors publish no
destination bytes and the next event can recover. `TextObserverError.Stage`
attributes `format`, `format input/output/state`, `encode input/output/state`,
`write`, `flush`, or `poisoned`; `Cause`/`Unwrap` retains opaque host errors without
calling their Error/String/Format methods. Its own Error string is intentionally
constant per stage. Callback panics become constant errors without formatting
the panic value; fatal runtime errors such as unrecoverable OOM remain outside
this contract. Formatter/encoder writer leases expire on return or panic.
The generic oracle `ObservationError[S,O]` remains a separate API retaining the
actual transition. Public diagnostic errors supplied by callbacks, including
typed nil and errors propagated from another observer, are opaque causes under
the active `format input/output/state` stage. Only an internal codec failure from
the same active payload lease and matching payload kind retains its encode
stage. A sticky record bound failure takes precedence over callback errors or
panics. Public errors do not confer failure attribution or checked authority.

Nested declaration/framing encoding reuses the existing batch and revocable
leases. `EncodeRaw` callback errors remain cooperative; failed writes and failed
framed calls remain sticky even if ignored. Panic containment targets the module's
Go 1.23 default panic behavior. The legacy `GODEBUG=panicnil=1` setting is outside
this observation profile: an existing raw value-codec boundary may swallow a nil
panic before it reaches the observer.

Writer/flush failures permanently poison the sink, including subsequent Off
observations, and may leave a delivered prefix. Native `io.Writer` semantics
apply: `n < len(p)` with nil error becomes `io.ErrShortWrite`; partial bytes plus
an error are never retried or duplicated. Go writer errors, including Interrupted,
are terminal here. `Flush` calls an optional `Flush() error` method and otherwise
succeeds without writing. `GetRef` returns the caller's writer; direct external
use is their responsibility. `IntoInner` transfers that reference without flushing
or closing, including after poison, and disables further observer use. Call Flush
explicitly before transfer when required. The sink and payload formatter are
exclusive, nonconcurrent objects; reentrant observation, flush, settings changes,
and ownership transfer during a callback are rejected.
Do not copy TextObserver or EncodedPayloads after first use.

The source-only observation tests include error-family compatibility, active
stage attribution, typed-nil and hostile callback failures, nested declaration
framing, sticky bounds, expired copied leases, and checked Recorder integration.
The installed consumer is in `examples/native-observation/go`. `observation-v1`
is advertised only by `ExperimentalProfiles()`. Compilation and execution remain
deferred to the parent's frozen integrated Kyoto qualification.
