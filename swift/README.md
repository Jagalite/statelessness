# Native Swift Statelessness

The repository-root `Package.swift` exposes the **native** Swift engine. It is
independent of Rust and the older Rust-backed `bindings/Package.swift` package.
Swift tools 6.0+, no third-party package dependencies.

Products: `Statelessness` (library), `StatelessConformance` (fixture adapter), and
`stateless-corpus` (JSONL executable). Add the repository as a SwiftPM dependency,
then depend on `.product(name: "Statelessness", package: "statelessness")`.
Before merge/publication, select the feature branch or use a local path dependency.

From the repository root:

```sh
swift test
swift build -c release
swift run --package-path swift/Examples/Counter Counter
python conformance/run.py --runner '[".build/release/stateless-corpus"]'
```

`Examples/Counter` is an independent SwiftPM consumer importing only the public
library product. Package qualification also copies the distributed sources to a
separate directory and builds a separate consumer against them.

## Application-owned types

`Model<State, Input, Output>` takes native, throwing callback closures, required
snapshot closures, optional finite-domain/equality/hash hooks, and an optional
`Codec`. `enumerateStates` explores a bounded FIFO graph. `checkObserved` checks
an existing transition without executing it again. `record`, `replay`,
`encodeTrace`, and `parseTrace` provide checkpoint/observation persistence.
`Rng` uses UInt64 wrapping arithmetic and the shared SplitMix64 algorithm.

`cloneState`, `cloneInput`, and `cloneOutput` must isolate mutable references.
`{ $0 }` is appropriate for value-only/immutable types; classes or structs holding
mutable class references need explicit copies. Throwing callbacks become
`ModelError`. Swift traps and process exits remain process failures; they cannot
be represented as passing checks or caught as ordinary throws.

Swift's normal String equality uses canonical equivalence. The portable contract
instead requires exact scalar identity. `ExactString` supplies UTF-8-exact
hashing/equality for model keys; `exactText` compares exact text. Check IDs/details,
disposition reasons, metadata, JSON keys, and table state identities use exact
comparisons. Applications that use String in their own state equivalence must
choose an equality that preserves future behavior and properties. Valid leading
U+FEFF inside a string/codec payload is preserved, not silently stripped.

The package declares Apple minimum deployment versions, but only the platforms
listed in qualification reports are tested. Linux/macOS CLI/library testing is
not iOS, tvOS, watchOS, or Windows qualification. There is no binary framework
publication; build the native source package with the consumer's toolchain.

Profiles: `core-v1`, `bfs-v1`, `rng-splitmix64-v1`, `trace-json-v1`.
Experimental candidate: `paired-v1` supplies `WithOracle`, `OracleState`,
`Oracle`, `Paired`, and `Projection`. Construct a `Paired(reference:projection:)`,
pass its `.oracle` to `WithOracle(actual, oracle:)`, then use the adapter's `.model`
with the ordinary native recorder, replay, and finite BFS APIs. Reference history
is independently advanced and participates in snapshots, equality, and ORC1
checkpoint framing. `attach(model:oracle:)` requires explicit saved history;
`observeTransition` wraps completed application execution without running it again.
An adapter failure throws `ObservationError` retaining the actual transition
(a defensive snapshot when copying succeeds, the original value if copying fails).
The adapter and errors are synchronous and must remain confined to the owning
actor/thread. Swift 6 requires errors to conform to `Sendable`, so this generic
error uses unchecked conformance to retain arbitrary application payloads; that
does not make mutable application references safe to transfer across tasks.
Projection equality is application-owned; use `exactText` for exact textual
semantics. Clone closures must isolate mutable history and application values.
The candidate has application tests against `conformance/paired-v1.tsv`; it is
not a qualified portable profile. Relocated test packages may locate that fixture
with `STATELESS_PAIRED_CORPUS`.

Experimental native generation and search: `Generate(model:generate:isEnabled:
 simplerInputs:equalInputs:)` adds explicit state-aware generation and causal
 replay hooks. `Auto(model, options:equalInputs:)` supplies a finite reservoir
 generator as `.generated`; it retains one candidate, probes at most cap+1
 entries, and commits RNG only on a complete successful scan. The whole scan is
 one cooperative callback. Duplicate entries retain their sampling weight.
 `I?` is an explicit presence result: for optional-valued inputs return
 `.some(nil)` to generate a present nil, and `.none` to end the case.

`fuzz` uses one seeded SplitMix64 stream and mutates the previous passing
sequence. Its optional `SearchObserver` receives initial checks, every completed
transition including the failing prefix, and complete/incomplete checker-error
batches through defensive snapshots. Observer exceptions abort the run; a
secondary error notification exception retains the checker error in diagnostics.
The existing list-returning check API cannot expose checks assembled internally
before a throw, so that failed batch is empty/incomplete. Adding a streaming
check sink remains a separate N01 gap. `shrink` validates the original failure, preserves exact ID and phase,
then tries chunk deletion and lazy simplification with cycle detection. Inspect
termination, complete attempt history, aggregate replay work, and
`validatedOriginal`; interrupted validation preserves supplied evidence without
claiming reproduction. `RunLimits` provides cooperative cancellation/deadlines;
`ShrinkLimits` bounds aggregate replayed transitions in addition to attempts.
Callbacks are not preemptible, process memory is not bounded, and these APIs
remain synchronous and confined to their owning actor/thread. The shared
`search-v1` corpus and shrink history are experimental candidate tests, not a
qualified profile. Relocated tests may set `STATELESS_SEARCH_CORPUS` and
`STATELESS_SEARCH_HISTORY` to the shared TSV/JSON paths.

Guided campaigns, composition helpers, ring recording, and binary `.sttrace`
interoperability remain unimplemented in this native package.

`generated.withOracle(oracle)` preserves generation, enabled, simplification and
input identity hooks while wrapping state in explicit independent history.
Generation/membership see actual state only; the resulting model retains
combined equality and paired checkpoint codecs. Estimated-state-byte forwarding
remains unavailable until the native engine exposes that capability.

## Candidate binary audit traces

`AuditTrace` independently reads and writes Rust's binary v1 format through
`decodeAuditTrace`/`encodeAuditTrace`, or throwing chunk/sink callbacks through
`readAuditTrace`/`writeAuditTrace`. `BinaryTraceLimits` exposes nine independent
byte/item quotas. The writer validates every frame and total before its first
sink call; a later I/O failure can still leave a prefix. The incremental reader
bounds each frame before requesting its payload and verifies CRC before parsing.

`RunConfig(strategy:seed:parameters:)` retains an optional full-width UInt64 seed
and ordered duplicate `AuditParameter` pairs. Importing portable `Trace` requires
explicit `AuditTrace(portable:config:)`; `audit.portable()` returns both trace and
configuration, preserving the information absent from the older portable shape.
Empty model-error text, empty failed/skipped/rejected/ignored reasons and exact
UTF-8 are retained. Binary v1's flattened successor/transition check phases remain
`unknown_legacy`; no causal/phase data is invented.

`native-binary-v1` is an experimental candidate separate from the established
portable profiles. This API does not qualify binary v2, causal evidence,
resource/performance costs, installed platforms or durable publication. See
[`spec/native-binary-v1.md`](../spec/native-binary-v1.md) for format and boundaries.

## Candidate BFS controls and estimates

`enumerateStates` preserves its existing defaults and adds optional
`limits: SearchLimits` and `observer: SearchObserver`. `enumerateObserved` is the
explicit observed entrypoint. `SearchLimits` reuses `RunLimits` for cooperative
cancellation/deadline and accepts `maxEstimatedStateBytes: UInt64?` for admission.
Checks complete at the current execution boundary; a property failure takes
precedence over a stop requested during that boundary. Callbacks cannot be
interrupted. BFS observers request actual predecessor histories with
`needsHistory: { true }`; false delivers empty history. Fuzz retains its existing
always-present histories.

Set `Model.estimatedStateBytes` to a throwing state-to-`UInt64?` callback.
`SearchReport.estimatedRetainedStateBytes` sums admitted states only; missing
estimates/overflow make an uncapped total unknown, while capped searches require
known estimates. Duplicate states are not charged again; exact equality with a
cap fits. These estimates exclude temporary successors, predecessor inputs,
frontier/maps, callback allocations, allocator overhead and process RSS.

`Oracle.estimatedStateBytes` estimates retained history. `WithOracle` aggregates
actual and history estimates with checked addition and exposes
`estimatedWrapperBytes`: native inline padding from
`MemoryLayout<OracleState<S,H>>.size` minus the sum of member sizes, saturating at
zero. Unknown layout arithmetic/estimates or overflow stay unknown. This is
Swift/type/architecture-specific inline layout, not heap measurement or a promise
of equal byte totals across languages. `Paired` delegates history estimation to
its reference model; outer wrapper padding is still added.

`bfs-controls-v1` remains experimental; the four promoted portable profile
projections are unchanged. Array-returning throwing checkers cannot expose checks
never returned; observed searches retain a validated prefix when a returned
array contains a later invalid check. Rich replay observations remain separate
work. See [`spec/bfs-controls-v1.md`](../spec/bfs-controls-v1.md).

## Candidate append checks

`append-checks-v1` adds optional `Model.checkStateInto` and
`checkTransitionInto` callbacks, preferred exactly once over the legacy
array-returning callbacks. The same optional hooks exist on `Oracle`.
`CheckSink.push`, `extend`, `reserve`, throwing `count`/`isEmpty`, and `snapshot`
append or inspect the complete existing prefix. They expose no mutable backing
storage. Invalid checks or reserve arguments reject the active batch even if the
callback catches the error; only its preceding valid prefix survives.

Each synchronous callback receives a fresh lease. All operations throw after
that callback ends, and expired leases detach their backing owner. Nested actual,
oracle and reference callbacks expire independently; an outer callback's lease
stays active. Callbacks and caller buffers require exclusive synchronous access;
there is no thread-safety, `Sendable`, asynchronous-use or reentrant-buffer
promise. Check values are immutable; inspection and retained evidence use
independent copy-on-write value snapshots.

`checkStateInto(model, state: state, checks: &checks)` and
`checkTransitionInto(model, before: before, input: input, transition: turn,
checks: &checks)` preserve caller prefixes. `checkObservedInto` adds `sequence`
and optional `policy`, clears caller storage at entry and on every error, and
never executes the reducer. It retains array capacity where possible. The
collection-returning `checkObserved` convenience collects through this dispatch.
Legacy state callbacks remain required by `Model`'s initializer; supply the
existing callback as before, or an explicitly unused callback when an append hook
is supplied.

BFS, fuzz, shrink, record and replay use preferred append dispatch for initial and
turn checking. Search error observers receive actually emitted partial batches;
a transition-check error retains the completed state batch followed by an
incomplete edge batch. Both notifications are attempted and checker errors remain
primary. `WithOracle` appends actual checks before oracle checks. `Paired` appends
reference checks before projection comparisons; `Generate.withOracle` preserves
these hooks. Search reuses its boundary buffer, while retained traces and observer
snapshots remain detached. Legacy callbacks may still allocate arrays; this
candidate makes no zero-allocation or RSS claim and does not promote the four
portable conformance profiles. See
[`spec/append-checks-v1.md`](../spec/append-checks-v1.md).

Mutable adapter migration: paired/oracle adapters now supply preferred append
hooks. Replacing only their legacy `checkState` or `checkTransition` closure does
not override an existing append hook. Replace the corresponding `...Into` hook,
or explicitly set it to `nil` to select your legacy replacement. Existing models
that provide only legacy callbacks retain their dispatch behavior.

## Candidate bounded codecs and replay evidence

`encode-buffers-v1` adds optional `Codec.encodeStateInto`, `encodeInputInto` and
`encodeOutputInto`, plus `Oracle.encodeHistoryInto`. Present hooks take precedence
once over legacy allocating encoders. `EncodeBuffer.withBuffer(&bytes,
maximum: cap) { sink in ... }` clears logical bytes while retaining capacity.
`extend` and `extendFromVec` reject whole chunks before growth; rejected writes
and nested callback errors poison successful finish even if caught. `framed`
shares the same budget and writes an eight-byte little-endian component length.
Every callback, including framed children, receives a separately revocable lease;
expired leases detach their storage owner. The enclosing lease remains valid
after a successful child. Incoming arrays use Swift value ownership, and no
mutable backing storage is exposed. These synchronous APIs do not grant thread
safety, `Sendable`, reentrant caller-buffer access or a catchable process-OOM
promise. Legacy encoding and model callbacks may allocate outside these bounds.

The public `encodeStateInto(codec, state: value, into: sink)` and corresponding
input/output functions provide preferred dispatch. `WithOracle` incrementally
writes exact ORC1 framed model/history components into one shared budget.
`Paired` forwards reference history hooks; generated/automatic models preserve
codec hooks while non-codec search models remain supported. Existing allocating
encoder properties are retained. Mutating a legacy encoder on an adapter does
not override a present incremental hook; replace that hook or set it to `nil`.

`replayWithObservations` accepts portable `Trace`; its bounded counterpart
`replayWithObservationsBounded` requires lossless `AuditTrace` and
`BinaryTraceLimits`. They emit `.initial`, `.turn` and `.error` observations.
Initial and turn observations contain typed detached values, encoded expected and
actual evidence, ordered checks, the state-check boundary, and all four difference
flags. Reports update before observers run, stop on the first divergence, and
choose disposition, outputs, state, then checks as primary. Failure reproduction
matches a failed ID at its corresponding initial/combined-turn boundary even if
details differ. Exact replay validates only the recorded prefix.

`ReplayError` preserves the primary engine error, secondary error-notification
failure, updated report and any produced actual initial/turn result. Optional
checks and state-check count distinguish unknown checking from a completed empty
batch. A direct initial/turn observer failure is not recursively reported to the
error observer. Observations use application deep-copy callbacks. If a snapshot
fails after execution, the original produced values transfer only to
`ReplayError.actual`; the error observation has no typed actual and an explicit
`actualUnavailableReason`. No reducer, checker or failed clone is repeated to
reconstruct evidence. If copying a normal engine-error notification fails, its
snapshot error is retained as `observerError`, and the error callback still runs
with unavailable typed evidence. Recovery values remain caller-owned and
synchronous; they must not cross tasks merely because Swift requires errors to
conform to `Sendable`.

Bounded replay checks identity first, then validates all nine binary quotas before
decoding. Actual run/step evidence independently shares exact wire framing,
UTF-8 strings, item counts, output/state payloads and reserved footer headroom.
`recordAudit(model, inputs: iterator, config: RunConfig(...), limits: limits)`
provides bounded encoding while retaining ordered configuration parameters.
It appends reserved `stateless.engine.version` and `stateless.engine.build`
parameters (`native-swift-v1` is an implementation label, not a source fingerprint)
and rejects caller parameters using that reserved prefix. Replay retains the
stored producer configuration without restamping. Recording commits only coherent
steps and reserves space for a bounded model-error footer. Portable `record` keeps
its distinct `TraceLimits` behavior; binary quotas are not substituted for JSON
or portable payload limits. Both new candidate profiles leave the four promoted
portable profiles unchanged, and make no zero-allocation or hard-RSS claim.
