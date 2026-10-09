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
Not implemented: Rust's advanced fuzzing/shrinking, oracles, guided/campaign/
composition helpers, ring recording, and binary `.sttrace` audit format.
The archive is installable locally; this change does not publish to npm.
