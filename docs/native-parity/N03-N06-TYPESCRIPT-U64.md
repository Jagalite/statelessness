# TypeScript Rust64 counts

The existing public entrypoints now infer count representation from their configs.
`FuzzConfig`, `GuidanceConfig`, `CampaignConfig`, `AutoOptions`, and `ShrinkConfig`
retain `number` as their default type parameter. Their `<bigint>` forms and the
`*Config64` aliases accept the full unsigned 64-bit domain. Every admitted number
is a nonnegative safe integer; larger number values fail instead of rounding.
Seeds and work totals remain bigint. Mutation percent and feedback version retain
their existing byte/u32-shaped numeric representation.

Both representations execute the same kernels. Fuzz case/step loops, automatic
reservoir counts, shrink attempts/alternative ordinals, campaign job ordinals and
lifecycle arithmetic are exact. Report count representation follows fuzz cases,
shrink maxAttempts, or campaign jobs respectively. Guided count representation
follows fuzz cases even when guidance budgets use bounded convenient numbers.
No budget is converted into an allocation. Existing actual arrays have numeric
indexes, checked at growth or after a bound by an existing array length. Worker
counts are narrowed only after 1..256 validation and min with exact jobs.

`fuzz`, `fuzzObserved`, `fuzzGuided`, `fuzzGuidedObserved`, `runCampaign`,
`runGuidedCampaign`, `runNodeCampaign` and `runNodeGuidedCampaign` all accept the
wide config forms. Node structured clone carries bigint through workers and
caller acknowledgements; browser entrypoints still import no Node module.
Legacy explicit generic argument arities remain accepted. An omitted fuzz/shrink
config has a number report type; a bigint report requires a supplied bigint config.

The private `Lifecycle<bigint>` uses the production reducer. Test-only
`LifecycleCodec.encodeExactState/decodeExactState` feed its state directly, using
the unchanged Rust64 v1 bytes and invariants; no number conversion intervenes.
Legacy codec conversion methods still reject values that do not fit numbers.
This does not introduce a public campaign checkpoint API absent in Rust.

Boundary tests live in `typescript/test/u64-counts.test.mjs`; their expectations
come from the pinned source and `N03-N06-U64-CONTRACT.md`. They exercise huge
budgets using cancellation, zero cases, empty domains, initial/one-step failures,
small retained corpora, explicit Node workers, saturation, and lifecycle restart.
Execution receipts are reported separately; source completion is not parity or
release qualification.

## Reviewed source receipt (2026-10-29)

Independent read-only review inspected the completed implementations against the
pinned contract. Findings fixed before final freeze: generic parameter arity,
omitted-config report typing, literal count type widening, metadata-side config
mutation, guided report representation, and checked shrink overflow placement.
Reviewer aggregate over 15 changed/new TypeScript code/test paths is
`d6c54eb63e01e23e4d99159f3f211d2b1bcd96e4f5d080af08cfbcfd1ac72396`
(canonical sorted path-to-SHA256 compact JSON). No executed review is implied.
The existing search observer regression now expects the pinned
`initial check_state` diagnostic instead of the stale `state check` text;
historical r2 output remains preserved.

Final frozen copied inputs: `/tmp/stateless-u64-ts-r3/source`. Its 325-file
`input-manifest.json`, including TypeScript source/tests, package manifests,
TypeScript tool files and conformance corpora, has SHA256
`a87fd4eb8e2faeae2b1217e78b573d3dfef0dbfeb67613a97d8ef146fbfef96e`.
`identities.json`, `jobs.json` and `health.json` retain detailed identities and
queue states outside tracked source. Node executable is
`/Users/jagatranvo/.nvm/versions/node/v23.5.0/bin/node`, SHA256
`8122b9b4f5056b08992ab748fad243fe9eb94d811d74a4172553438ab5041607`.
TypeScript package is 5.8.3; `lib/_tsc.js` SHA256 is
`1f62c69e832fbbbd05a6dac950f152937f594a4d0b88087be5406ace348efcb5`.

- Final compilation: Kyoto `3a963e46c4084469b7c8174eda8354f7`, queued, never started.
- Final API type fixture: `cc5090f32a1f49daacff577e2debdb41`, queued, never started.
- Superseded r1 jobs `613bc3d257c44355b8f9cf181b7a2117` and
  `8e7910d527a04e25b6733d80207198e1`: cancelled before starting after review fixes.
- Superseded r2 jobs `7da522b8a7cc4e149767be5f1034e4f4` and
  `c21916f77cbb4602b1be0a75a229b476`: cancelled before starting after literal-type fix.
- r1/r2 copied inputs and all job records remain preserved. No historical
  N05/N06 job or snapshot was changed or cancelled.

Observed final Kyoto admission: `effective_max_running=0`,
`admission_reason="OS memory pressure: warning"`, sampled_at
1793282320.75366. Shared policy is unchanged. Compilation and execution have
**not** succeeded; no generated JavaScript executable/package identity exists
for this candidate yet. Runtime is **NOT_RUN**, pending successful compilation.

Reproduce queued checks through Kyoto with `node` and these arguments:

```
/tmp/stateless-u64-ts-r3/source/typescript/node_modules/typescript/bin/tsc -p /tmp/stateless-u64-ts-r3/source/typescript/tsconfig.json
/tmp/stateless-u64-ts-r3/source/typescript/node_modules/typescript/bin/tsc --noEmit --strict --target ES2022 --module NodeNext /tmp/stateless-u64-ts-r3/source/typescript/test/u64-types.ts
```

After successful compilation, hash generated `dist/` files before separately
submitting the following runtime (from the frozen TypeScript directory):

```
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- node --test test/u64-counts.test.mjs test/search.test.mjs test/search-corpus.test.mjs test/guided.test.mjs test/campaign.test.mjs test/campaign-lifecycle.test.mjs
```

Installed packages/five-engine interchange, combined-source validation and OS/
runtime/resource matrices remain parent qualification gates. No push, PR, merge,
publication, or release action occurred.
