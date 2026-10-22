# Proposed native gate-5 controls — not executed

Complete replacement consumers preserve repository-relative paths below this
directory. Apply after the parent releases frozen source; qualify through shared
Kyoto and independent review. Only these drafts were written. No builds, tests,
application imports or executions ran. Swift `produce`/`transcode` remain
`@MainActor`; libraries, checking flags and the independent oracle are unchanged.

Each app runs fresh Recorder and EvidenceRecorder controls in both CLI modes.
Its actual bound encode-only Output succeeds for checked turn 1. After checking
turn 2, a flag makes the same Output callback count its invocation, write byte
`ee` into the actual sink, then throw the controlled ModelError. No model, binder,
checked token or capture hook is replaced, and no Output decoder is added.

Negative recorders retain at most one step. The failed candidate must preserve
the entire turn-1 prefix: original metadata/config/checkpoint/checks/bytes,
observed count 1 and evicted count 0. Evidence retains sequence 1, boundary 1,
its causal context/provenance and checkpoint 0. Turn 2 has a valid cause `(1, 0)`
but must not commit a journal row. Public phase lookups assert state/transition
for turn 1 and absence for turn 2.

Per-control counter vectors below use the order
`reducer/state-check/transition-check; encode-State/encode-Input/encode-Output`:

- After checked initialization and construction: `0/1/0; 1/0/0`.
- After first checked capture: `1/2/1; 3/1/1`.
- Before failed capture: `2/3/2; 3/1/1`.
- After failed capture: `2/3/2; 4/2/2`.

`initial`, both decode counters and provider calls stay zero throughout, with
a counted provider bound. State encoding comprises one checkpoint, two encodes
on success, then only before-State on failure. Output failure prevents post-State
encoding. Checking happens once per token and never during checked handoff.

Both recorders must throw ModelError with exactly
`runtime encode output: n13 controlled Output encoder failure`, retaining that
same text in model-error termination. This asserts diagnostic ownership by the
Output capture stage, not exception object identity. Disabling the fault and
resubmitting the same token must throw exactly
`runtime recorder is frozen; export its retained trace` or
`runtime evidence recorder is frozen; export its retained trace`, preserving
the original diagnostic/prefix/journal and all counts. Bounded artifact
roundtrips and final ownership transfer add no model callbacks. Negative
artifacts remain in memory, separate from success envelopes and the oracle.

TypeScript replay now binds `application(() => ({}))` and asserts exactly four
provider calls: initial State plus three Inputs. Its separate omitted-provider
control requires zero provider calls and one decode per direction. Successful
production also binds the counted provider and asserts zero encoder calls to it.

Stdout supplies measured normalized `capture_counts` (produce only),
`replay_counts`, per-stage `capture_failure_counts`, and
`capture_failure_controls: true` only after assertions. Python/Swift producers
keep empty replay maps; their transcoders report v1/v2. Swift additionally reports
its existing separate replay as `v2_causal`; TypeScript retains producer
self-replays. Success artifact bytes/configuration are intentionally unchanged.

Base HEAD: `79f7fe5f8428172924a34280a59f5a725ebe586e`. Original input SHA-256s
were checked before drafting and remained unchanged at the intermediate review:

- `examples/native-model/python/consumer.py`:
  `2ff3dea2239a0e9b7a061fa022189c3dfba0924e0473ac56c866af5997f02e19`
- `examples/native-model/typescript/consumer.ts`:
  `9a5d9838f63e6a3b18e79b32dd3574fcd719c86bc958efc80513741feeb5186d`
- `examples/native-model/swift/Sources/ModelConsumer/main.swift`:
  `fde47d40af78f9689cb92acb649c1046f39dbed4d07638f97542253a8526d8e9`

The final read found source consumers matching these proposed draft hashes.
All three originals show mtime `2026-10-22 09:28:36 -0400`; originals and drafts
have different inodes. The writer is unknown, and the parent reports not having
applied drafts. This worker wrote only below the draft directory; final
original-source immutability cannot be claimed:

- Python: `fb8f06bc0f716074a18638a9f44caf8ad6c80ff21c188b0c664bdad08ad9ec09`
- TypeScript: `1e82004e6c9a6c16f4528a3667660ac4097f27b5e512f2feaac6c4d9715f09a3`
- Swift: `61e833c62751c0fc02e26ec4036b83e4fbe73935436dc32465b17f5e2065ca45`

Static review traced production binders, recorders, sink error wrapping and
artifact/phase APIs. Diff whitespace inspection produced no diagnostics.
Compiler/type compatibility and runtime assertions remain unverified, including
Swift local helpers and JSON receipt serialization. The parent must qualify
the final installed packages. These controls do not claim negative-error replay
reproduction, metadata-pressure eviction coverage, or closure of all N13 rows.
