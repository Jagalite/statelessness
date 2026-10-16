# ABI v2 implementation status

Date: October 15, 2026. The shared Rust engine, additive C/Wasm ABI, four opt-in
language adapters and distribution/gate tooling are implemented in source.
**Runtime tests are now authorized and have executed on macOS arm64, Node and
the collaborative Chromium browser. Benchmarks remain unmeasured.**
No language default has switched, no independent engine or ABI 1 surface has been
removed, and no package has been published.

Work is isolated on `abi/v2-foundation` in
`/Volumes/seed2/Projects/stateless-abi-v2`, based on
`0949e7992f1be34d2052b22f12b7cbb0ce6b7a95`. The original checkout's unrelated
uncommitted changes were not imported. The [baseline receipt](../validation/abi-v2/baseline.json)
records this boundary. Parallel roadmap/evidence work mentioned in the plan is
an integration dependency; it is not included by implication.

See the [contract](ABI-V2.md), [distribution decisions](ABI-V2-DISTRIBUTION.md)
and [complete plan](ABI-V2-PLAN.md). Source implementation, compilation,
execution, measured performance and retirement are separate statuses.

## Phase status

| Phase | Implemented source | Pending exit gates |
| --- | --- | --- |
| P0 | Isolated baseline, compatibility inventory, matched workload fixtures, content identity and measured-budget comparison tooling. | Repeated baseline/candidate execution and approved numeric budgets; no measurements supplied. |
| P1 | Fallible model/cursor/equality/hash/codec/generation operations; shared checking, BFS, recording/replay, fuzz/shrink, automatic generation and ring paths; unchanged native Rust public adapters. | Native performance budgets and remaining platform coverage; workspace regression executed. |
| P2 | Versioned C header, capability negotiation, session/value/result ownership, callback builders/errors, bounded results, cancellation, portable conversion, ABI tests and C consumer fixture. | Broader platform and installed-consumer coverage; native ABI regression and both C smoke consumers executed. |
| P3 | Go v2 cgo adapter and typed TypeScript/Wasm adapter; explicit engine lifetime, snapshots, error containment, recording/reload/replay, fuzz/shrink/ring and corpus adapters. | Remaining browser/platform adoption cells; Go unit, Node, Chromium and full corpus tests executed. |
| P4 | Python ctypes and separate Swift package; shared automatic finite generation, codec-less witnesses, structural portable codecs and four-profile fixture adapters. | Remaining deployment/adoption cells; full corpus and local installed consumers executed. |
| P5 | Additive package staging, exact source/artifact receipts, package inspector, compile-only workflow, fail-closed readiness and performance comparison tools. | Remaining platform/deployment adoption matrix and measured budgets; local package verification/installations executed. |
| P6 | Existing defaults preserved; pending-gate reports prevent retirement claims. | Per-language adoption gates, distinct migration/cutover releases, rollback windows and explicit retirement decision. |

No phase with an execution or measurement exit gate is declared complete.
Guided feedback, debugger/composition foreign APIs and arbitrary asynchronous
callbacks are outside the advertised first-release capability set.

## Consumer and deployment inventory

| Consumer | Candidate entry point | Compatibility boundary |
| --- | --- | --- |
| Rust | Existing public `stateless` APIs, with private shared operations. | Dependency-free core and native static dispatch remain; new ABI does not make Rust callers serialize values. |
| C | `bindings/c/stateless_v2.h`; both v1/v2 exports in native library. | C calling convention implemented in Rust; no Rust-native object layout crosses the boundary. |
| Python | `statelessness.native.NativeEngine(explicit_path).session(model)`. | Ordinary `statelessness` still works without a native library. Explicit close keeps callbacks alive safely. Platform wheels are a later qualification cell. |
| Go | `bindings/go/v2`, matched linked native library. | Owner OS thread, cgo handles and explicit close. Structural helpers compile without cgo; full v2 engine needs cgo. Independent Go remains for unsupported modes. |
| TypeScript | Generated `@jagalite/stateless/v2`, `createEngine(wasm)` then synchronous sessions. | Distinct from independent `@jagalite/statelessness`. ABI 1 `stateless.wasm` and feature-built `stateless_v2.wasm` stay separate. |
| Swift | `bindings/swift-v2`, product `StatelessNativeV2`. | Separate Swift 6 package; old bindings manifest retains its toolchain floor. Desktop compilation does not qualify mobile deployment. |

Each binding provides initial/observed checks, BFS, binary/portable recording and
replay, deterministic RNG, fuzz/shrink and ring recording. Optional capabilities
are explicit. Missing/incompatible engines fail; no silent fallback is used.
The fixture runners identify the common `rust-shared` engine family so several
wrappers do not count as several independent implementations.

## Evidence and policy

Rust compilation continues through `/Volumes/seed1/Projects/kyoto/kyoto.py`.
Kyoto's shared policy adds `--no-run` to Cargo tests. Following the user's later
instruction to proceed with tests, successfully compiled executables are run
directly, with separate runtime receipts. No shared queue policy was changed.

The earlier [foundation qualification](../validation/abi-v2/qualification.json)
and [118-input manifest](../validation/abi-v2/build-inputs.json) describe only the
first BFS/observation increment. They are **historical**, and do not qualify the
expanded candidate. A successful preliminary library check likewise does not
replace final test/link/declaration/package checks after source changes.

The expanded [implementation receipt](../validation/abi-v2/implementation-qualification.json)
records successful compiler checks and static package inspection, with commands,
toolchains, targets, logs, source identity and artifact hashes. The
[final build receipt](../validation/abi-v2/final-build-receipt.json) binds the native
release library and separate ABI 1/v2 Wasm artifacts to the 297-input source
manifest. Earlier receipts and superseded source snapshots remain historical.

## Executed runtime qualification

The [runtime evidence directory](../validation/abi-v2/runtime-qualification/)
contains commands, exit codes, logs, artifact hashes and preserved initial
failure receipts. The runtime summary is authoritative for pending jobs.

| Check | Executed result | Qualification boundary |
| --- | --- | --- |
| Rust workspace | 534 tests passed across 51 binaries; none failed or ignored. | macOS arm64, Rust 1.90.0; 3 application tests and 8 doctests also passed. |
| C | ABI 1 and ABI v2 smoke consumers passed. | Matched native release library on macOS arm64. |
| Go v2 | 15 top-level tests plus diagnostic subtests passed. | Corrected corpus, relocated package tests and ABI 1 compatibility tests passed. |
| Swift v2 | 10 XCTest tests passed, including a fresh relocated package build. | macOS arm64; 25 staged compilation inputs verified unchanged. |
| Node/Wasm | 17 v2 and 8 v1 tests passed. Independent TypeScript: 30 passed. | Exact separate v1/v2 Wasm artifacts; JavaScript diagnostic fix included. |
| Python | 43 tests passed, no skips. | Independent and ctypes adapter with explicit native library. |
| Browser | 717 semantic/transport cases passed; zero retained sessions. | T3 collaborative Chromium browser; not Safari or Firefox. |
| Infrastructure | 13 evidence, 6 readiness and 7 conformance-harness tests passed. | Installed packages have additional consumer receipts. |
| Cross-language corpus | Five runners passed all 717 cases each and 550 fresh-process cross-producer replays, both source and installed-package reruns. | Corrected Go runner included; wrappers represent two engine families. |

Runtime tests exposed and fixed JavaScript error-message normalization and the
Go corpus depth-bound label. A packaging test also needed canonical paths on
macOS (`/var` versus `/private/var`). Initial failures are retained in evidence.

Final native/Wasm release artifacts were rebuilt through Kyoto against the
[current source manifest](../validation/abi-v2/merge-qualification/build-inputs.json).
Packages were restaged, their wheel/npm member hashes checked, and native loading
and negative loading executed. Python wheel and npm consumers run outside the
checkout, including fresh-process replay. Relocated Go and Swift packages compile
and pass runtime tests. Exact input comparison permits reuse after the subsequent
CI-only TypeScript setup correction; only package build metadata changed.

The [final packaging receipt](../validation/abi-v2/final-packaging-qualification.json)
and [runtime summary](../validation/abi-v2/runtime-qualification/summary.json)
record local results. The [PR](https://github.com/Jagalite/statelessness/pull/6)
is ready for review, with ABI v2 Linux/macOS, portable conformance and existing
native-port workflows executing on each updated head. Consult its current check
runs before merging. The new ABI v2 check exercises Rust regression/doctests, C,
installed Python/npm, relocated Go, Node/Wasm and macOS Swift. A first hosted
attempt exposed a missing TypeScript helper build in CI; the workflow now builds
those helpers before running the corpus. The failure log remains in evidence.

Merging the additive opt-in implementation and changing defaults are separate
decisions. The plan's P5/P6 adoption gates still require remaining deployment
cells, measured performance/memory, approved numeric budgets and migration
windows. No performance improvement or budget pass is claimed. No package is
published and the default-switch gate remains false. ABI 1 deprecation and
independent-engine retirement require their own migration decisions.
