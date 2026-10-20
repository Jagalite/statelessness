# Native TypeScript Statelessness

Independent TypeScript implementation of the four portable v1 profiles. It does
not call Rust, Wasm, Python, or another engine. ESM package:
`@jagalite/statelessness`, with declarations and zero runtime dependencies.

```sh
cd typescript
npm ci
npm test
npm pack
# In another application:
npm install /path/to/jagalite-statelessness-0.1.0.tgz
```

Node 20+ runs the CLI/tests. TypeScript 5.8.3 is pinned as the development compiler;
consumers of the built JavaScript do not need it. The library entrypoint imports
no Node modules and uses standard ES2022 APIs (including BigInt, TextEncoder, and
TextDecoder). A real-browser qualification is separate from these Node tests.

## Native API

```ts
import { enumerateStates, record, replay, checkObserved } from '@jagalite/statelessness';
import type { Model, EnumerateModel, CodecModel } from '@jagalite/statelessness';
```

`examples/counter.ts` is a complete typed consumer, compiled against the installed
package's exported declarations during qualification. Applications keep their own
classes/structural types and transition functions. JSON is not a runtime reducer
requirement. `Model` handles ordinary transitions and checks; `EnumerateModel`
adds input/equality hooks; `CodecModel` adds optional persistence hooks.

Models provide `cloneState`, `cloneInput`, and `cloneOutput`. Use identity only for
immutable values; supply copies that preserve application prototypes and isolate
mutable reference graphs. Do not assume `structuredClone` preserves custom class
semantics. Optional hashing accelerates equality buckets but never replaces exact
equality. State, input, and property history must make callbacks deterministic.

`record` preserves codec bytes in new Uint8Arrays, including copies of Node Buffer
results. `parseTrace`/`stringifyTrace` serialize the portable JSON envelope.
`replay` restores the checkpoint, enforces identities/canonical bytes, and compares
all ordered observations. `checkObserved` never calls the reducer. Exceptions from
callbacks are `ModelError`, not property failures. Declared limits bound retained
engine data, not hidden callback allocations.

## Shared qualification

After building, from the repository root:

```sh
python conformance/run.py --runner '["node","typescript/bin/corpus.mjs"]'
```

The installed `stateless-corpus` executable uses the same JSONL protocol. It does
not implement a second engine in its adapter. Strict transport rejects duplicate
keys, floating numbers, invalid scalars/UTF-8, and oversized lines; the next line
remains processable. Full-width random values are BigInt in the native API and
canonical decimal strings on the wire.

Implemented: `core-v1`, `bfs-v1`, `rng-splitmix64-v1`, `trace-json-v1`.
Experimental candidate: `WithOracle`, `Paired`, and typed `Projection` implement
[`paired-v1`](../spec/paired-v1.md) using this native engine. Projections supply
explicit state/effect equality functions; history supplies its own clone and
equality hooks. `EXPERIMENTAL_PROFILES` lists this separately from the four
qualified `PROFILES`. Rust runtime parity and independent specification review
remain pending. `test/paired.test.mjs` exercises independent bounded-queue reducers,
shared golden results, fail-fast recording, checkpoint restart, and exact replay.

Not implemented: Rust's advanced fuzzing/shrinking, guided/campaign/
composition helpers, ring recording, and binary `.sttrace` audit format.
The archive is installable locally; this change does not publish to npm.

### Independent generation, fuzzing and shrinking

`GenerateModel<S, I, O>` adds `generate(state, rng)` and semantic
`equalInputs(left, right)` to the ordinary model. Optional `isEnabled` validates
causality (default true); `simplerInputs` supplies ordered simplification hints.
Return the exported `NO_INPUT` symbol to signal exhausted generation. `null` and
`undefined` remain ordinary valid inputs, including through automatic sampling.
Use `automatic(enumeratedModel, { maxCandidates: 100_000 })` to sample a finite
input domain with transactional reservoir sampling. Generation validates the
whole domain, pulling at most one entry beyond the cap, and commits RNG state
only on success. Membership may stop at its first matching entry.

```ts
import { automatic, fuzz, shrink } from '@jagalite/statelessness';
const generator = automatic(model); // model also supplies equalInputs
const report = fuzz(generator, {
  seed: 42n, cases: 1000, maxSteps: 100,
  maxTransitions: 100_000n, mutationPercent: 50,
});
if (report.failure) {
  const smaller = shrink(generator, report.failure, { maxAttempts: 10_000 }, {
    maxReplayedTransitions: 100_000n,
  });
}
```

Fuzzing mutates only the previous passing sequence, uses state-aware generation,
and reports skipped checks separately. Shrinking first validates the supplied
failure, preserves its first failed check ID and phase, tries chunk deletions
then ordered value simplifications, and guards against accepted-sequence cycles.
Its attempt history distinguishes invalid causality, different failures, repeated
prefixes, and resource limits. `search_complete` describes this search strategy;
it does not promise global minimality. Inspect `validated_original` when an
initial replay is interrupted. Transition counters and budgets use exact u64
`bigint`; sequence/attempt counts use safe integer numbers.

Both operations accept cooperative `maxDurationMs` and `cancellation: () => boolean`
limits. Callbacks, iterators, cloning and equality must terminate; limits cannot
interrupt a callback or bound allocations within it. Search is synchronous and
uses no Node imports, Rust library, or Wasm. These APIs are candidates for the
search contract and do not add a qualified entry to `PROFILES`.

`fuzzObserved(model, config, observer, limits?)` (or the optional fourth observer
argument to `fuzz`) observes initial checks and every fully checked transition,
including the failing transition. `transition` receives the actual case prefix,
separate state/transition check batches, and defensive copies of all model data.
`checkError(phase, checks, complete)` reports partial iterable output on checker
failure; a transition-check failure reports the completed state batch first.
Observer exceptions propagate as contextual `ModelError` values.

`WithOracle` forwards generation, enabled checks, simplification hints and input
identity to the actual model (using the actual component of the combined state).
Wrap an enumerated actual model with `automatic` before constructing `WithOracle`
when automatic generation is desired. Missing generation or input identity
capabilities produce explicit unsupported-operation errors.

### Experimental lossless audit binary v1

`readAuditTrace(bytesOrReader, limits?)` and `writeAuditTrace(audit, limits?)`
read/write the existing Rust audit format, including complete run configuration,
ordered duplicate parameters, exact `bigint | null` seeds and model-error text.
`AuditTrace` extends the portable `Trace` with a required `config: RunConfig`.
`auditTraceFromPortable(trace, config, limits?)` requires explicit configuration;
`auditTraceToPortable(audit, limits?)` returns `{ trace, config }` together. Both
conversions own their bytes, including when the source contains Node Buffers.
Conversions apply the default binary quotas unless explicitly overridden.

`BinaryLimits` uses the nine independently enforced snake_case fields from
[`native-binary-v1`](../spec/native-binary-v1.md). The writer captures fields and
preflights all frames, strings, counts and total bytes before publication.
`writeAuditTraceTo(audit, writer, limits?)` passes a complete encoded artifact to
`writer.write(bytes)` only after validation; an I/O error can still leave the
host sink partially written. No flush, close, durability or publication is implied.
An `AuditReader.read(target)` may fill any nonempty prefix and returns zero only
at EOF; reads are synchronous, and footer EOF probing requires bounded input or
host I/O deadlines. Binary v1 step checks have unknown legacy phases. Structural
acceptance and exact re-encoding do not qualify semantic replay or add a profile.

### Experimental BFS controls, estimates and observations

`enumerateStates(model, config, limits?, observer?)` preserves ordinary FIFO
exploration and adds `SearchLimits`: `{ control: { cancellation, maxDurationMs },
maxEstimatedStateBytes }`. Controls reuse fuzz/shrink semantics and cannot
interrupt a model callback. `estimatedStateBytes(state)` is an optional model
hook returning exact unsigned 64-bit `bigint` bytes or `null` for unknown.
`estimated_retained_state_bytes` sums admitted unique states only; unknown or
u64 overflow stays unknown without a cap. A cap requires known estimates and
returns `estimated_state_byte_limit` before admitting an oversized state.
The estimate excludes predecessor inputs, temporary successors, map/frontier
storage and callback allocation; it is not a process RSS limit.

`enumerateStatesObserved(model, config, observer, limits?)` is an equivalent
entrypoint. The existing `SearchObserver` receives initial checks, duplicate and
failing checked edges, and partial check-error batches. Set `needsHistory: () =>
true` for actual initial-rooted edge prefixes; BFS supplies an empty history
otherwise. Observer values are detached through model clone hooks. Fuzz always
supplies full prefixes regardless of this optional history flag.

`Oracle.estimatedStateBytes(history)` and `Paired` reference-estimate forwarding
support composition. `new WithOracle(model, oracle, estimatedWrapperOverhead?)`
requires explicit caller-selected logical wrapper overhead for a known combined
estimate. Omission defaults to unknown; explicit `0n` is permitted. The estimate
is the checked u64 sum of model bytes, history bytes and this declared overhead.
It does not infer managed-runtime object layout or claim cross-language byte/RSS
identity. The portable-v1 conformance wire response keeps its existing schema.

For budgets beyond JavaScript's safe Number range,
`enumerateStatesExact(model, { maxStates, maxTransitions: bigint, maxDepth },
limits?, observer?)` uses a u64 transition budget and returns bigint
`transitions` and `skipped_checks`. Ordinary `enumerateStates` retains its safe
Number surface, uses the same exact kernel, and rejects a report whose counters
cannot be represented safely. Skipped-check counts saturate at u64 maximum, matching Rust;
state/depth limits remain checked safe integers reflecting the native array/path
representation. No counter passes through a rounded Number.

### Experimental append-only checks

Optional `checkStateInto(state, sink)` and
`checkTransitionInto(before, input, transition, sink)` hooks take precedence over
legacy iterable checks in execution, BFS, fuzz, shrink, record and replay. Hooks
must synchronously return `undefined`; promises, thenables and other values are
errors. Legacy callbacks remain required/source compatible and run exactly once
when an append hook is absent.

`CheckSink` exposes `push`, `extend`, `length`, detached frozen `snapshot()` and
validated `reserve()` (without a JavaScript capacity guarantee). Use
`withCheckSink(output, sink => ...)` to append within one scoped callback without
clearing preceding checks. Every callback receives a fresh revocable lease;
escaped operations fail after return/error, nested callbacks cannot reopen older
leases, and revoked leases release backing-storage references. Malformed append,
extend or reserve operations have sticky first-failure semantics even if caught.
Stored checks are validated/copied; snapshots cannot alter retained evidence.

`checkObservedInto(model, before, input, transition, sequence, output, policy?)`
reuses caller storage and clears it both at entry and on every error. It never
calls `step`. Search instead retains actual partial check prefixes for diagnostic
observers. State and transition callbacks share preceding checks in one reusable
scratch buffer; returned traces/observer batches detach their observations.
WithOracle/Paired forward actual, oracle/reference and projection checks in order
with independent child leases. Caller buffers are exclusively owned during a
call: aliases, reentrancy and concurrent writes are outside the contract. These
APIs establish no allocation, thread-safety or performance guarantee and remain
`append-checks-v1` candidates in `EXPERIMENTAL_PROFILES`.

### Experimental bounded codecs and replay evidence

`EncodeStorage` owns reusable byte capacity. `withEncodeBuffer(storage, maximum,
callback)` clears its logical length and grants a synchronous `EncodeBuffer`
lease. Maximum accepts a safe integer or exact u64 `bigint`; existing capacity
may exceed the logical limit. `extend`, `extendFromSlice` and `extendFromVec`
validate an entire chunk before copying; rejected writes remain sticky even if
caught. `framed` writes an eight-byte little-endian component length and grants
an independently expiring child lease over the same budget. No mutable backing
view or prefix patch operation is exposed. Lease operations expire on every
return/error, and expired leases release storage references. `finish` cannot
turn a failed write into success. A codec's explicit callback error takes
precedence over a prior caught write failure.

Optional `encodeStateInto`, `encodeInputInto`, `encodeOutputInto` and Oracle
`encodeHistoryInto` hooks synchronously return `undefined` and take precedence
over one allocating legacy call. Legacy allocation happens outside the sink
limit. WithOracle incrementally writes exact ORC1 bytes with model/history
components sharing one budget; Paired and automatic wrappers preserve hooks.
`EncodeStorage.snapshot()` is an owner-held detached diagnostic copy, including
partial accepted bytes after failure; those bytes are not a successful encoding.
Only a completed scope may publish an artifact. Logical limits do not bound
preexisting capacity, application callback allocations, clones or process RSS.

`recordAudit(model, inputs, runConfig, maxSteps?, binaryLimits?)` performs binary
admission while recording, including all nine independent quotas, shared output
and state headroom, and reserved future-error footer bytes. An error after the
initial checkpoint preserves only admitted steps with a bounded model-error
reason. Capture escapes invalid UTF-16 diagnostic units as literal `\uNNNN`;
supplied binary text remains strictly validated. Caller `stateless.engine.*`
parameters are reserved; recording appends ordered package version and
`native-typescript-v1` build identity parameters. This implementation label is
not a source fingerprint; source qualification receipts remain separate.
The portable `record` keeps its distinct TraceLimits and configuration-free API.

`replayWithObservations(model, trace, options?, observer?)` and
`replayWithObservationsBounded(model, auditTrace, options?, binaryLimits?, observer?)`
share the basic replay kernel. `options.allowBuildMismatch` defaults false.
Observations include sequence-zero initial evidence, every exact or first
divergent turn, typed actual values, complete check boundaries, stored/actual
encoded values, all four difference flags and an already updated report.
Primary difference order is disposition, outputs, state, checks. Reproduced
failure means a recorded failed ID failed again at the corresponding initial or
combined step boundary; details may differ. Exact replay describes the recorded
prefix and does not verify a terminal model error or prove completion.

The bounded variant requires lossless AuditTrace configuration, checks identity
before stored artifact validation, rejects invalid artifacts before decoding,
and independently shares actual RUN/STEP/frame/total/item/footer budgets across
all outputs and successor state, including divergent evidence. Ordinary rich
replay retains its unbounded compatibility behavior. Observer callbacks are
synchronous; there is no separate replay deadline/cancellation algorithm.

`ReplayError` preserves the primary engine error, report and available actual
initial/turn result without repeating execution. Incomplete checks and their
boundary are `null`, rather than an empty passing batch. Error-notification
failure is separately retained as `observer_error`; normal observer failure is
not recursively sent back to the error observer. Observations are detached
through model clone hooks. If a snapshot fails, recovery transfers the retained
actual result to the error owner; an error observation marks the unavailable
snapshot explicitly. These APIs remain `encode-buffers-v1` and
`replay-evidence-v1` experimental candidates; the four promoted profiles are
unchanged. No browser, cross-platform, hard-memory or allocation-performance
qualification is implied.

`checked-results-v1` is an experimental candidate. `checkInitial(model, state)`
checks a supplied checkpoint without calling `initialState`. `checkTurnExact`
checks an already executed transition without calling `step`, with a positive
`bigint` u64 sequence and `{stateEvery: bigint, transitionChecks: boolean}` policy.
`checkTurn` accepts safe integer number sequences and policy periods. Failed or
skipped properties still produce a complete token; callback or snapshot errors
produce no token. Models do not need codecs to use these checking APIs.

`CheckedInitial` and `CheckedTurn` have no public constructors. Engine-private
WeakMaps authenticate tokens; diagnostic `observation()` objects cannot authorize
recording. Tokens are frozen and check arrays are immutable at runtime. Context is
cloned before checking and again before sealing; each public context accessor
returns another detached copy using the original model's clone hooks. Clone hooks
must isolate mutable data, preserve values, and model callbacks must not mutate
checked context. Snapshot failure never reruns a reducer or checker. This is an
API integrity convention, not a sandbox against hostile reflection. Tokens have
no global consumption bit and may feed independent recorders; each recorder
checks its own sequence continuity. `intoChecks()` returns an immutable detached
batch and does not invalidate a JavaScript token.

`Recorder` records application-executed transitions into a bounded binary-v1
`AuditTrace`. `new Recorder(model, checkpoint, config, options)` checks the supplied
checkpoint once. `Recorder.withCheckedInitial(token, config, options)` and
`withCheckedInitialAt(token, origin, config, options)` reuse sealed checks.
`observe(model, before, input, transition)` checks a supplied actual turn;
`observeChecked(token)` reuses its complete full-coverage batch without executing
reducers or checkers. Host origins and counters use exact u64 `bigint`; trace
indices and counters stay local to the attachment. Tokens can feed two recorders,
while duplicate, reordered or gapped sequences freeze the affected recorder.

Options select positive `maxSteps`, positive `maxRetainedBytes: bigint`, and all
nine binary `limits`. The recorder checks metadata identity and canonical encoded
before-state continuity, incrementally bounds codecs, and evicts oldest turns to
fit its retained ring. Eviction promotes only the evicted turn's state-check
prefix into checkpoint checks. Initial/turn property failures freeze the artifact;
recording errors freeze the previously coherent prefix, preserving earlier
failures against later observer errors. Reserved engine and monitor parameters
record implementation identity, origin and local observation/eviction counts.

`snapshot()` returns detached artifact bytes/checks; `intoTrace()` transfers the
artifact and disables that recorder. `writeTo` / `writeWithLimits` serialize through
the strict binary writer. `observedSteps()`, `evictedSteps()`, `retainedSteps()`,
`retainedBytes()` and `isFrozen()` expose runtime status. Limits bound engine
encoding/retention, not arbitrary callback allocations or process RSS.
Causal envelopes and coordinated retention are provided separately by `EvidenceRecorder` below.

Recorder capture and ownership transfer reject reentrant calls while a model
callback is active. This native ownership guard mirrors Rust's exclusive mutable
borrow: callbacks cannot observe another turn or transfer the recorder midway
through capture. A prior terminal failure remains primary. Sealed immutable check
batches are quota-validated before the recorder copies them into retained storage.

`causal-evidence-v2` is an experimental candidate. `TraceV2` wraps an unchanged
binary-v1 `AuditTrace` with exact `bigint` checkpoint/host sequences, ordered
provenance and per-turn state-check boundaries/causes. `readArtifact` accepts
bounded bytes or an `AuditReader`, runs no model and distinguishes original v1
artifacts from v2 evidence. `writeTraceV2` / `writeTraceV2To` preflight the complete
canonical envelope before publication. Evidence metadata has independent byte,
label and causal quotas plus structural decoder allocation/element/depth/work
budgets; those logical budgets describe bounded requests, not JavaScript heap or
RSS. Provenance and CRC are assertions/integrity checks, not authentication.

`checkPhase` preserves unknown legacy turn phases. `replayTraceV2` validates the
whole evidence artifact before bounded replay, then checks persisted phases only
where semantic replay is exact. `verifyUpgrade` explicitly executes compatible
exact replay to derive v2 boundaries; passive reading never fabricates them.
Causal labels remain data and do not execute host effects.

`EvidenceRecorder(initialToken, origin, config, recorderOptions, provenance,
limits)` consumes sealed checking evidence without rerunning reducers/checkers.
It reserves the full selected metadata allowance and envelope overhead, then
coordinates metadata/cause retention with the runtime Recorder's actual ring
commit. Causal output references are checked against the currently retained
prefix before planned eviction. `snapshot()` owns independent evidence;
`intoTrace()` transfers ownership. Errors preserve a coherent aligned prefix and
the first terminal reason. Recorder lifecycle and reentry guards still apply.

`diagnosticJSON` returns bounded, explicitly nonreplayable JSON bytes. It omits
payloads, details, model/build identity and causal labels; property IDs appear
only by exact `propertyIds` allowlisting. Core APIs do not access files or trigger
browser downloads. The explicit Node subpath
`@jagalite/statelessness/node-evidence` exports `writeRawEvidenceNew` and
`exportDiagnosticNew`: precompute bounded bytes, create exclusively (Unix mode
0600), write and fsync the file. Existing paths are never overwritten. A failure
after creation may leave an incomplete new file; this adapter does not claim
rename-atomic publication or directory fsync.

Accepted evidence snapshots capture native accessors once. A known unfit trace
uses an uncached semantic scan followed by ordered v1 sizing to avoid retaining a
journal proportional to rejected input; rejected-input accessors may therefore be
read again. Such inputs cannot become accepted evidence, and immutable/deterministic
source values remain the ownership contract.

`canonical-value-codec-v1` is an experimental candidate. `valueCodecs` provides
fixed-width integers, strict bool/String, `UNIT`, tagged `NONE` / `some(value)`,
vector, fixed array, tuple arities 1–4 and `oracleState` descriptors. Widths through
32 bits use strict integer `number`; 64/128-bit values use `bigint` exclusively.
UTF-8 preserves BOM, NUL and byte-distinct normalization forms and rejects malformed
bytes or unpaired UTF-16 surrogates. There are no built-in floats, chars, native
usize/isize, maps, sets, arbitrary objects or reference graphs.

`encodeValue(schema, value, maximum)` returns bounded canonical bytes;
`decodeValue(schema, bytes, limits)` requires one guarded value and complete input
consumption. A public `Decoder` independently admits `bytes`, `allocation`,
`elements`, `depth` and `work` as exact u64 `bigint` (safe integer numbers are also
accepted). It copies admitted input using intrinsic typed-array view boundaries.
`take` returns owned bytes, `collection` charges in Rust source order and `value`
accounts for the actual nested guarded call graph. The first structural/callback
failure remains sticky, with an owned diagnostic snapshot; catchable native
exceptions restore depth. `finish` reports trailing bytes without making that
terminal check sticky, so this public reader remains reusable as in Rust.

`schema.decodeFrom(reader)` is raw; `reader.value(schema)` is guarded. String and
vector `decodeLength(width, reader)` directly override only their own length
prefix, while `withLength(schema, width)` creates a separately guarded schema.
Only widths 1, 2 and 4 are supported. `encodeLengthValue` is a small native prefix
primitive; its arithmetic tests do not claim execution of Rust's private helper
or enormous container loops.

`schema.encodeRaw(value, out)` and `encodeLengthRaw(value, width, out)` match the
cooperative direct Rust trait boundary: a caught returned validation/callback
error does not itself poison the buffer. Actual rejected writes and framed
callback failures remain sticky; raw callbacks do not implicitly finish and can
run even on an already failed sink. Every raw child lease expires on return/error.
The existing `encodeInto` / `encodeLength` helpers retain the stronger native
callback-poisoning policy. Built-in raw composition forwards through raw child
encoders. String encoding writes its prefix, admits the entire body against the
shared cap, then uses fixed UTF-8 scratch; it never allocates a full unadmitted
UTF-8 payload. Existing engine codec hooks keep their behavior.

Allocation descriptors use explicit logical Rust64 request weights, not JavaScript
heap/RSS. Built-ins have measured scalar/header weights and a finite option/array/
tuple layout table. Unknown compound shapes remain usable at the top level but
need a caller-supplied `logicalLayout(bytes, name)` before collection decoding. No generic Rust-layout inference, niche arithmetic or wire-size sum is
used. The first qualified convention is the separately measured Rust 1.99
`aarch64-apple-darwin` target; other targets are not implied. Caller-declared custom
weights are cooperative assertions, not compiler-verified layout identities.
`customValueSchema` accepts either or both handwritten encode/raw-decode callbacks.
Missing directions reject at use, including empty composite values; encoding needs
no decoder or collection allocation layout. Direction availability propagates
through options, vectors, arrays, tuples and OracleState. Optional `encodeLength` /
`decodeLength` callbacks have independent directions; a length-only descriptor
works through `withLength` without inventing ordinary encoder/decoder callbacks.
The unified descriptor exposes unavailable methods with clear runtime errors;
schema/variant names are diagnostic only. Arbitrary callback work/allocations and
fatal process OOM remain outside these logical budgets. Model/derive convenience
and version glue are a separate N13 scope; adopting these descriptors is explicit.

Explicit derive-equivalent declarations are opt-in. `codecField<T>()` binds
ordered ordinary, length (width 1/2/4), accessor, or direct adapter fields;
`orderedRecord`, `tupleRecord`, and `unitRecord` add no wire framing.
`enumCases<T, K>(key)` and `taggedEnum` use explicit u8/u16/u32 tags.
Constructors run after all fields succeed. Directions are inferred across every
field and case; `require: 'encode' | 'decode' | 'both'` eagerly checks a required
direction. Supply `logicalLayout` explicitly for collection decoding of new
record/enum types; these builders do not infer Rust layout.

```ts
const field = codecField<{ code: number; note: string }>();
const pair = orderedRecord({
  name: 'Pair',
  fields: [field.value('code', valueCodecs.u8),
           field.length('note', valueCodecs.string, 1)],
  construct: fields => ({ code: fields.code, note: fields.note }),
  require: 'both',
});
```

`bindModelCodec(model, { state, input, output, version: 1, decodeLimits })`
returns a facade that preserves original model receivers, including private
fields and frozen models. State and input require both ordinary directions;
output requires only encoding. Each of its eight hooks checks current model
metadata before inspecting values, sinks, bytes, or decode limits. A limits
provider runs afresh per decode, and encoding never evaluates it. Extra model
methods remain available through the facade. Existing model methods that invoke
other methods on `this` continue to use the original model.
