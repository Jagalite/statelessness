# Native parity: reviewed API and feature mapping

This audit materially advances M0 from the lexical bootstrap into a reviewed
feature/API ledger. [capabilities.json](capabilities.json) groups the pinned Rust
behavior into **36 features** and maps actual Python, Go, TypeScript and Swift
public source. It records exact SHA-256 identities for the reviewed source files
listed in the ledger. It does not close M0 or establish runtime qualification.

Rust baseline remains `0876717bee85e335449c2c99ada41f6c6e6112d1`. The native source
snapshot is based on worktree HEAD `e711bd6694780aba50af0d972fbecccbab30534f`,
including uncommitted native generation/fuzz/shrink, search observers and
oracle/projection additions in all four languages. Authors reported source stable
pending compiler/runtime findings before hash capture; subsequent review fixes
invalidate the affected hashes and require another refresh.
Use the JSON source hashes to identify audited bytes; do not silently treat these
static statuses as observations about later source. The JSON timestamps record
when source metadata was collected, not when tests ran. Later scoped binary and BFS-controls
reviews add or refresh only their listed source/export/contract hashes and each
identify their own worktree HEAD. They do not silently refresh unrelated feature
cells or source hashes from the original audit.

## Status meaning

The ledger separates implementation from qualification for every language:

- `implemented`: source contains the narrowly named behavior and public API.
- `partial`: source contains part of the Rust behavior; `remaining` names omissions.
- `missing`: no independent native engine API exists within the reviewed public
  source surfaces. Manual application workarounds and ABI calls do not satisfy it.
- `qualification-pending`: this audit neither executes tests nor imports old CI
  results as current qualification. All cells have this qualification state.

These are audit states, separate from the plan's milestone states (`not_started`
through `released`). An `implemented` feature is not a closed N01–N14 work item,
and a missing feature is not a claim that applications cannot implement it.

The native public surfaces were inspected directly: Python's `__init__.__all__`,
core/oracle/trace modules; the Go package's exported names and model callback
fields; TypeScript's index reexports and npm entrypoints; Swift's public types and
methods under the root SwiftPM package. Native ABI alternatives such as Python
`native.py`, `bindings/`, and `bindings/swift-v2/` are deliberately separate.

## Reviewed findings across all four languages

All four independently implement the existing four profile foundations:

| Foundation | Python | Go | TypeScript | Swift |
| --- | --- | --- | --- | --- |
| `core-v1` | `Model`, `Check`, `Transition` | `Model[S,I,O]`, `Check`, `Transition` | `Model<S,I,O>`, `Check`, `Transition` | `Model<State,Input,Output>`, `Check`, `Transition` |
| `bfs-v1` | `enumerate_states` | `Enumerate` | `enumerateStates` | `enumerateStates` |
| `rng-splitmix64-v1` | `Rng` | `Rng` | `Rng` | `Rng` |
| `trace-json-v1` | `trace_json.dumps/loads` | `MarshalTrace/ParseTrace` | `stringifyTrace/parseTrace` | `encodeTrace/parseTrace` |

Their source also implements bounded recording and exact replay with
application-owned codecs. The portable `Trace` shape does **not** contain Rust
run configuration or binary v2 evidence. Separate native audit codecs now provide the scoped binary
v1 representation described below. The later BFS-controls slice adds cooperative controls and retained-state-byte
admission. Rich replay observations and bounded replay evidence remain separate
requirements.
Native `Trace` names alone do not establish Rust wire compatibility.

All four now expose `Oracle`/`WithOracle` and `Paired`/`Projection` APIs. Python
and TypeScript already contained them; this worktree adds the Go and Swift forms.
The inspected bodies advance independent reference state using reference state
and input, compare projected state and ordered effects, compare complete
application/reference dispositions, and retain explicit oracle history in the
`ORC1` checkpoint. They compose with native BFS/record/replay. `paired-v1` remains
an experimental profile. Generation, enabled checks and simplification now forward
through Python/TypeScript `WithOracle`, Go `WithOracleGenerate`, and Swift
`Generate.withOracle`. Model/oracle state estimates now forward too, including
checked aggregation and an explicit native wrapper-overhead convention. Reusable
append/encode-buffer behavior remains missing, so the full oracle feature stays
`partial` even though scoped projection comparison is `implemented`.

All four also implement the candidate `search-v1` slice independently: explicit
generation, bounded finite-domain reservoir sampling and membership, seeded
previous-passing-case fuzz mutation, failure/phase-preserving shrinking, operation
reports, cooperative cancellation/deadline and aggregate replay-transition limits.
Python uses the distinct `EXHAUSTED` marker; Go uses value/present/error;
TypeScript uses `NO_INPUT`; Swift uses an outer optional. These representations
must preserve supported null-like model inputs rather than confuse them with
domain exhaustion. Candidate markers stay separate from the four established
profile declarations and do not promote qualification or release.

Native search observers receive defensive state/input/transition snapshots,
requested initial-rooted histories, ordered checks, and explicit partial/complete
check-error batches. Fuzz always supplies its full executed prefix, including a
failing turn. Python/TypeScript preserve incremental iterator check prefixes;
Go preserves valid returned partial checks. Swift's eager array callbacks cannot
expose work never returned before a throw. A returned array whose later check
fails validation does preserve its valid prefix as incomplete; a preceding
complete state batch remains visible. Native observer errors abort with an engine error;
Rust's observer callbacks return unit. These choices are stated in the candidate
contract, rather than silently claiming identical callback types.

The newer BFS APIs now combine cooperative cancellation/deadline controls,
`SearchLimits` retained-state estimates, and the existing search observer.
Resource settings are captured at entry; callbacks cannot change validated
budgets through aliased option objects or pointers. Only the captured cancellation
channel remains live. Initial and completed-turn check boundaries finish before a following control
poll: a property failure wins over a stop at that boundary, while checker errors
remain errors. Successor checks and observations precede deduplication and
admission caps, so duplicate, failing and unadmitted edges remain observable.
Hash collisions use actual equality in newest-admitted-first order. Depth and
transition exhaustion still require their specified single probe. Callback order,
reported counters and unknown estimates are part of the candidate
[bfs-controls-v1 contract](../../spec/bfs-controls-v1.md).

BFS requests actual initial-rooted histories only when `needs_history` is true;
fuzz retains its always-present executed prefix independently of that flag.
`SearchReport` now includes nullable retained estimated bytes and distinct
estimated-byte/cancellation/deadline outcomes. The initial estimate runs after
successful checks even without a cap. Only novel admitted states are charged;
state-count rejection precedes a successor estimate. Unknown estimates or checked
sum overflow remain unknown without a cap; a capped missing estimate is an
error, and overflow/over-cap rejects admission without changing prior totals.
An initial state over the cap reports zero admitted states and known zero bytes.
TypeScript's additive `enumerateStatesExact` uses BigInt for unsigned 64-bit
transition budgets, executed-transition counts and saturated skipped-check counts.
The compatibility `enumerateStates` API retains safe Number budgets/reports and
rejects a result that cannot convert exactly; it does not round the exact kernel's
counters. State/depth container bounds remain host safe integers.

These application estimates describe retained model representation, excluding
transient successors, predecessor inputs, maps/frontiers, hidden callback
allocations and process RSS. `WithOracle` adds model and history estimates plus
wrapper overhead with checked unsigned arithmetic. Go/Swift expose actual native
inline padding beyond member sizes; Python/TypeScript require caller-supplied
logical overhead, with an absent value remaining unknown. Explicit zero is a
caller assertion, not an inferred object size. `Paired` forwards its reference
estimate. These conventions do not imply equal byte counts across runtimes.

The richer native report remains separate from the four existing qualified
portable wire profiles. Their conformance adapters keep the original report
projection instead of silently adding estimate fields. These controls do not
supply reusable append-only check/encoding storage, checked-initial/checked-turn
provenance tokens, or rich/bounded replay observations. Rust replay uses observer
stop boundaries rather than taking search `RunLimits` directly; new BFS controls
must not be counted as that separate replay API. Shrink attempt limits bound
replay attempts, not total iterator pulls or equality work: an infinite already
seen alternative stream can spend no attempts. Controls remain cooperative and
cannot interrupt one nonreturning callback. Bounded shrinking establishes no
global minimality.

All four now have independent structural Rust binary v1 codecs: Python
`trace_binary.AuditTrace`, Go `AuditTrace`, TypeScript `AuditTrace`, and Swift
`AuditTrace`. Each preserves model metadata, explicit `RunConfig`, initial
checkpoint/checks, ordered steps and the full termination variant including
model-error text. Configuration retains absent versus zero seed, all unsigned
64-bit seed bits, and ordered duplicate parameter pairs; TypeScript uses BigInt.
Lifting a portable trace requires explicit configuration. Narrowing returns that
configuration alongside the portable trace, so it is not silently lost.

The reviewed codec bodies implement the fixed v1 header, little-endian fields,
RUN/STEP/END order, frame CRC32, contiguous step indices, exact footer count,
strict UTF-8, and trailing-data rejection. They expose all nine independent Rust
read/write limit families and preflight the complete trace before the first sink
write. Limits bound encoded bytes and retained collection items, not callback
allocations, allocator overhead or process memory. Stream I/O can still fail
partway through a write; no flush, close, fsync, atomic replacement or private
publication is implied. Error-family parity is incomplete: Python translates
`MemoryError` into allocation failure, while Go/Swift/TypeScript do not expose
the same explicit recoverable `TraceError::AllocationFailed` variant. Swift
stream callback errors propagate as host errors rather than a binary I/O case.
Buffer allocation behavior, adversarial input and installed
native↔Rust/native↔native interoperability still require qualification.

This is a structural codec slice, not a replacement for Rust's recorder. Existing
native `record` APIs still create the portable shape: callers explicitly supply
configuration when converting to audit form. Direct recorder configuration and
Rust provenance stamping, encoded-artifact admission during capture, reserved
bounded model-error footer policy, and reusable bounded encoder sinks remain
open. A codec rejecting a finished oversized artifact does not establish that
recording admitted only a bounded coherent prefix.

Binary v1 has flat step checks and cannot encode successor-state/transition
phase boundaries. Its decoded step phases remain `unknown_legacy`; check order
or IDs cannot manufacture those boundaries. Structural decoding neither invokes
model callbacks nor validates termination/check consistency. Replaying a
compatible restored prefix can establish that prefix's semantics; it does not
reproduce a terminal model error or recover missing causal provenance. Binary v2
`TraceV2`, causal output origins/evidence, canonical value-codec families, richer
replay observations and debugger integration remain missing. Candidate
`native-binary-v1` and its hand-authored corpus do not qualify or close N11.

Periodic checks of an application-executed transition exist in all four native
engines. Rust's `CheckedInitial`/`CheckedTurn` provenance handoffs, observer event
families, logging/snapshot policies and bounded live ring recorder are additional
APIs. A finite `record` limit does not provide a ring recorder or retained-suffix
replay.

## N01–N14 accounting

The JSON contains exact public API names and source locators for every row below,
plus per-language implementation cells and concrete omissions.

| Gap | Reviewed feature groups | Current implementation boundary |
| --- | --- | --- |
| N01 | `core-model`, `append-buffers`, `finite-bfs`, `search-controls`, `fuzz-observers`, `record-replay`, `replay-observations` | Core/BFS/record/replay and cooperative search controls exist. BFS now adds admitted-state estimates and checked observations; reusable sinks, checked provenance tokens and rich/bounded replay evidence remain separate gaps. |
| N02 | `oracle-history`, `paired-projection` | Candidate history/projection/generation and estimate forwarding present in all four; reusable append/encode-buffer forwarding and runtime qualification remain. |
| N03 | `rng`, `generation`, `fuzz`, `fuzz-observers` | Native generation, automatic sampling/membership, seeded fuzz, controls, reports and fuzz observers implemented as candidate `search-v1`; qualification remains pending. |
| N04 | `shrink` | Native target-ID/phase-preserving deletion/simplification, causality checks, histories and cooperative/aggregate replay budgets implemented; qualification remains pending. |
| N05 | `guided` | Feedback, novelty, corpus management and guided fuzz missing. |
| N06 | `campaigns` | Job seed derivation, workers, delivery/acknowledgement and aggregate lifecycle behavior missing. |
| N07 | `observed-checks`, `observer-logging`, `ring-recording` | Basic periodic checking present; checked-token provenance, observers and ring recording missing. |
| N08 | `composition`, `network` | Two-model routing and multi-machine queue/scheduling wrappers missing. |
| N09 | `lifecycle` | Both legacy and indexed lifecycle monitors/retention missing. |
| N10 | `assurance`, `reference-controller` | Manifests, applicability/coverage, aggregation and controller mutation fixture missing. |
| N11 | `append-buffers`, `record-replay`, `json-envelope`, `binary-v1`, `value-codec`, `causal-evidence` | Portable JSON/user model codecs and structural binary v1 read/write/configuration candidate APIs present. Direct binary-budgeted capture/provenance, reusable sinks, canonical value codecs and binary v2 causal evidence remain open. |
| N12 | `replay-observations`, `debug-session`, `debug-inspection`, `debug-live-protocol`, `debug-telemetry` | All optional debugger/diagnostic packages missing; basic native replay remains available. |
| N13 | `core-model`, `generation`, `reference-controller`, `modeling-helpers`, `derive-adapters`, `demo-and-cli` | Handwritten models and generation adapters exist. Bounded Domain/product descriptors, lazy checks, generated codec/inspection helpers and full integration fixtures remain missing. |
| N14 | `reference-controller`, `native-conformance-tooling`, `reduction-research`, `demo-and-cli` | Corpus runners and counter examples present; controller/research/demo/CLI coverage incomplete. |

## Public reachability and reexports reviewed

The [root library](../../src/lib.rs) publicly declares `assurance`, `automatic`,
`campaign`, `demo`, `evidence`, `execution`, `explore`, `ffi`, `ffi_v2`, `guided`,
`model`, `monitor`, `network`, `observation`, `oracle`, `reference`, `research`,
`trace`, `composition`, `lifecycle`, `modeling`, `value_codec` and `conformance`.
All are accounted above except the FFI alternatives addressed below. A declaration
in these modules can still be private/restricted; module visibility is not a
claim that every lexical candidate is an exported API.

- `pub use model::*` exposes the model module's public items at the crate root.
  These are aliases of the same behaviors, not additional parity requirements.
- The explicit root oracle reexport exposes `ObservationError`,
  `ObservationResult`, `Oracle`, `OracleCodec`, `OracleState` and `WithOracle`.
  `ObservationResult` is an alias, not an independent engine algorithm. Associated
  behavior is mapped with `oracle-history`.
- `operations` is a private module. Its kernel traits/adapters and public-looking
  helpers are implementation accounting, not independent public API requirements.
- `campaign::lifecycle` is private and its main types are `pub(super)`. It is not
  a public Rust lifecycle API. Its decisions nevertheless determine externally
  visible campaign delivery/results, and the parity plan explicitly requires
  porting its self-model as a real fixture.
- `lifecycle::indexed` is publicly declared and is a separate required lifecycle
  surface. It cannot be excluded as private implementation.
- `reference::{codec,coverage,evidence,host}` are public modules. Their controller
  codec, coverage, host and evidence helpers belong to `reference-controller`.
- `ffi_v2::{adapter,api,portable}` are private modules, but `pub use api::*`
  makes public API functions in `api` reachable through `ffi_v2`. The root module
  also exposes ABI status/capability constants, callback structs and callback type
  aliases. This exception prevents the lexical heuristic from hiding exports in
  a private module. Target-gated native/Wasm export expansion remains to audit.
- The debugger crate declares `session`, `evidence`, `workbench`, `inspect`,
  `live`, `watches`, `diagnostic`, `protocol`, `dashboard`, `effects` and `metrics`
  publicly. Its root reexports `DebugError`, `DebugSession`, `InputPolicy` and
  `SessionLimits` from `session`; these are aliases of `debug-session` behavior.

Rust FFI v1/v2 remains an optional alternative, already covered by the existing
binding work. Independent engines do not need to recreate a Rust ABI or load a
Rust library to qualify. Their required result is native engine behavior and
ordinary ecosystem packaging. FFI/public transport constants are accounted as
retained alternatives under N14, not silently counted as missing native
algorithms or as completed native gaps. The Rust CLI is a separate binary rooted
at `src/main.rs`; its private functions are not library exports. Equivalent useful
CLI/fixture behavior belongs to `demo-and-cli`/`native-conformance-tooling`.

## Traits, associated types and enum outcomes

Rust `Model::{State,Input,Output}` maps to Python protocol type parameters, Go
model generics, TypeScript generics and Swift model generics. `Clone`/`Eq` bounds
become explicit runtime snapshot/equality contracts: Python deepcopies; Go,
TypeScript and Swift expose clone hooks, with equality hooks where enumeration
needs them. Rust compile-time trait enforcement does not transfer wholesale;
validation, documented application obligations and behavioral tests must establish
the corresponding outcomes. `ModelCodec` maps to Python `ModelCodec`, Go/Swift
`Codec`, and TypeScript `CodecModel`; its caller-owned `*_into` buffers remain a
separate gap. `Enumerate::input_iter` maps to native lazy iterators/iterables,
while `Generate` now maps to Python/TypeScript `GenerateModel`, Go
`GenerateModel[S,I,O]`, and Swift `Generate<S,I,O>`. Their generated-input
presence, enabled checks, optional simplification and input-identity hooks are
explicit native APIs.

The following outcome families were directly checked, rather than inferred from
matching type names:

- Rust `Disposition::{Accepted,Rejected(String),Ignored(String)}` maps to the
  native accepted/rejected/ignored representation including exact reason text.
- Rust `CheckStatus::{Passed,Failed(String),Skipped(String)}` maps to native
  status plus details. A skipped check remains distinct from a pass.
- Rust `CheckPhase::{InitialState,State,Transition}` maps to native failure
  phase strings. Native BFS failure reports do not by themselves establish
  phase-aware binary evidence or phase-preserving shrinking.
- Rust `SearchTermination` graph/depth/state/transition/failure outcomes now
  have native counterparts alongside `EstimatedStateByteLimit`, `Deadline` and
  `Cancelled`. Native reports include nullable admitted-state estimated bytes.
  Known, unknown, overflow and rejected-admission states remain distinct; the
  candidate map is not a runtime or process-memory qualification.
- Rust replay exact/diverged/incompatible outcomes have native equivalents.
  Detailed Rust replay differences/observations/error context do not.
- Rust fuzz termination and shrink termination/operation/outcome/report families
  now have native representations including original validation, attempt limits,
  replay-transition limits, causal rejection, different failures, cycle detection
  and resource stops. These are source mappings awaiting executed evidence.
- Rust `Feedback` associated types, composition/network state/input/
  output aliases, lifecycle event variants, campaign outcomes, assurance statuses,
  and debugger protocol/inspection/live outcome families belong to their named
  missing feature groups. Missing a parent feature includes its outcome schema;
  no native enum definition is implied by generic string support.

This accounts for representative public associated-type/enum families at the
feature level. It does not yet replace a declaration-by-declaration audit of every
variant, public method and blanket implementation. In particular all Rust
termination variants, diagnostics/protocol commands, canonical scalar/container
codec implementations and deprecated/historical encodings need explicit
contract cases before their feature can close.

## Macros and language-specific mechanisms

The [proc-macro crate](../../crates/statelessness-macros/src/lib.rs) exports
`#[model]`, `#[derive(TraceEncode)]`, `#[derive(TraceDecode)]`,
`#[derive(Inspect)]`, `input_domain!`, `macro_build_id!` and
`#[derive(ModelCodec)]`. Lexical function names `encode`, `decode`, `inspect` and
`model_codec` are implementation entrypoints; the derive attributes determine
consumer-facing names. Private expansion/parser modules are not additional
procedural-macro exports. Generated application methods and definitions require
per-invocation qualification rather than inventing a fixed list of engine exports.

`src/modeling.rs` additionally exports root `check!` using `#[macro_export]`.
The lexical bootstrap does not list this macro, so the reviewed ledger explicitly
maps it with `check_lazy` under `modeling-helpers`. `Domain`, bounded product and
sampling behavior, macro/runtime protocol constants and build provenance are also
mapped. These cannot be dropped merely because target languages lack Rust macros.

Equivalent outcomes can use Python decorators/dataclasses, Go typed builders or
optional generators, TypeScript builders, and Swift protocols/builders. Native
handwritten model APIs already exist; validated generated adapters, bounded
modeling helpers, lazy failure details and inspection/codecs remain missing. A
literal procedural macro, borrowed `TransitionRef`, Rust lifetime syntax or an
identical compiler diagnostic is not required. Equivalent identity, isolation,
bounds and error behavior is required. Optional generation/compiler tooling must
preserve zero third-party runtime dependencies.

## Remaining M0 work

1. Individually disposition the lexical candidates into reachable public item,
   trait/impl member, reexport alias, private/helper/test item, or generated syntax.
   Expand enum variants and trait implementations where outcomes are not yet
   separately specified. Do not turn the 3,327 lexical candidates into an API count.
2. Add source-backed per-language API/contract decisions for each remaining
   method/variant, including target-specific cfg exports and canonical container
   codecs. The feature grouping is an organizing map, not an exhaustive symbol
   closure assertion.
3. Review normative cross-language contracts independently, then link actual
   qualification receipts with source/package/corpus/runtime/command identities.
   This audit has no executed-test or installed-consumer receipts.
4. Refresh the mapping after implementation slices, retaining source hashes and
   intentional differences. Establish supported package/runtime/platform profiles
   and resource budgets before promoting qualification or release states.

General liveness, general partial-order reduction, a TUI and LLDB/GDB integration
are outside this pinned Rust baseline; their absence is not an invented extra
native parity gap.

No builds or tests were required or run for this static audit. Existing CI may
qualify earlier portable scopes; new pairing/search/binary/BFS-controls implementations and
broader parity features require fresh evidence through the authorized Kyoto
workflow.
All qualification fields intentionally remain pending until the parent work
collects and reviews current receipts.
