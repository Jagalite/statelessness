# ABI v2 implementation status

Date: October 14, 2026. The shared Rust engine, additive C/Wasm ABI, four opt-in
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
| P3 | Go v2 cgo adapter and typed TypeScript/Wasm adapter; explicit engine lifetime, snapshots, error containment, recording/reload/replay, fuzz/shrink/ring and corpus adapters. | Corrected Go corpus rerun and remaining browser/platform cells; Go unit, Node and Chromium tests executed. |
| P4 | Python ctypes and separate Swift package; shared automatic finite generation, codec-less witnesses, structural portable codecs and four-profile fixture adapters. | Final all-runner corpus result and installed-consumer/platform coverage; Python and Swift tests executed. |
| P5 | Additive package staging, exact source/artifact receipts, package inspector, compile-only workflow, fail-closed readiness and performance comparison tools. | Executed installation/platform/deployment matrix, target artifact verification and measured budgets. |
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
| Rust workspace | 534 tests passed across 51 binaries; none failed or ignored. | macOS arm64, Rust 1.90.0; application example and doctests tracked separately. |
| C | ABI 1 and ABI v2 smoke consumers passed. | Matched native release library on macOS arm64. |
| Go v2 | 15 top-level tests plus diagnostic subtests passed. | Corrected corpus runner requires compilation and rerun. |
| Swift v2 | 10 XCTest tests passed. | macOS arm64; 25 staged compilation inputs verified unchanged. |
| Node/Wasm | 17 v2 and 8 v1 tests passed. Independent TypeScript: 30 passed. | Exact separate v1/v2 Wasm artifacts; JavaScript diagnostic fix included. |
| Python | 43 tests passed, no skips. | Independent and ctypes adapter with explicit native library. |
| Browser | 717 semantic/transport cases passed; zero retained sessions. | T3 collaborative Chromium browser; not Safari or Firefox. |
| Infrastructure | 13 evidence, 6 readiness and 7 conformance-harness tests passed. | Does not qualify installed packages. |
| Cross-language corpus | Initial run passed all 550 fresh-process cross-producer replays; two Go termination-label mismatches found and patched. | Final five-runner rerun pending rebuilt Go executable; wrappers represent two engine families. |

Runtime tests exposed and fixed JavaScript error-message normalization and the
Go corpus depth-bound label. A packaging test also needed canonical paths on
macOS (`/var` versus `/private/var`). Initial failures are retained in evidence.

Final package restaging/attestation, installed-consumer and deployment matrices,
measured performance, memory accounting and approved numeric budgets remain
open. Native and Wasm release refresh builds succeeded, but historical package
receipts do not cover the latest wrapper edits. No performance improvement or
regression budget is claimed. The readiness/default-switch gate remains false.

The implementation remains a draft PR. Runtime workflows skip draft PRs;
local execution does not replace hosted/platform checks or review. Preserve
established defaults until their adoption gates pass. ABI 1 deprecation and
independent-engine retirement require separate migration decisions.
