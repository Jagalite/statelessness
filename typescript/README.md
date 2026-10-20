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
