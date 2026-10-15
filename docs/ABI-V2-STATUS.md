# ABI v2 implementation status

Date: October 12, 2026. The shared Rust engine, additive C/Wasm ABI, four opt-in
language adapters and distribution/gate tooling are implemented in source.
**Runtime qualification and benchmarks remain pending by explicit user choice.**
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
| P1 | Fallible model/cursor/equality/hash/codec/generation operations; shared checking, BFS, recording/replay, fuzz/shrink, automatic generation and ring paths; unchanged native Rust public adapters. | Executed native regression and independent semantic comparison; native performance budgets. |
| P2 | Versioned C header, capability negotiation, session/value/result ownership, callback builders/errors, bounded results, cancellation, portable conversion, ABI tests and C consumer fixture. | Executed complete C workflow, lifetime accounting and malformed-callback checks. |
| P3 | Go v2 cgo adapter and typed TypeScript/Wasm adapter; explicit engine lifetime, snapshots, error containment, recording/reload/replay, fuzz/shrink/ring and corpus adapters. | Actual runtime pilot workflows, GC/lifetime/negative tests, Node and browser qualification. |
| P4 | Python ctypes and separate Swift package; shared automatic finite generation, codec-less witnesses, structural portable codecs and four-profile fixture adapters. | Full unfiltered corpus, installed typed-model consumers, historical cross-producer replay and runtime mutation detection. |
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

All local builds, formatters, code generators and compiler checks go through
`/Volumes/seed1/Projects/kyoto/kyoto.py`. Cargo tests/benches use `--no-run`;
Go test binaries and Swift test bundles are compiled without execution. Generated
binaries are not run as a workaround. Runtime tests, corpus runs, installed
model consumers and measurements remain pending.

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

The final Go diagnostic-containment follow-up now passes Kyoto formatting and
compilation, including the regression test/benchmark binaries and cgo-disabled
structural helpers. No generated executable was run. Final source/build
attestation and package restaging still need to include that edit; existing
package receipts describe the prior candidate.

| Check | Observed result | Qualification boundary |
| --- | --- | --- |
| Rust | Workspace tests and benches compile; all-target Clippy and formatting checks pass. | Cargo uses `--no-run`; no model tests or benchmarks executed. |
| C | ABI 1 and ABI v2 consumers compile and link against the native release library. | Neither consumer executed. |
| Go | Source and relocated package test/benchmark binaries and corpus executable compile and link. | No tests, corpus requests or benchmarks executed. |
| Swift | Source and relocated package, XCTest bundle, corpus and benchmark executables compile and link. | No XCTest, corpus or benchmark execution; macOS arm64 only. |
| TypeScript/JavaScript | Backend and typed `.mjs` consumer compile; syntax and generated parser drift checks pass. | No Node/Wasm/browser execution. |
| Python | Adapter, tests, benchmark and corpus sources compile; wheel builds. | No engine imports or tests executed; native library supplied separately. |
| Packaging | Native/browser hashes and Wasm interfaces inspected; npm archive and wheel built and inspected. | Static inspection only; no installed model consumer or publication. |

The readiness reports remain `default_switch_ready: false`. Runtime tests,
portable corpus execution, fresh-process replay, ownership behavior, installation,
platform coverage and measured performance are pending. Benchmark fixtures exist;
repeated measurements, complete memory accounting and approved numeric budgets
have not been collected. The worktree changes remain uncommitted.

Even a complete compile pass does not establish semantic parity, cleanup behavior,
memory safety under hostile callbacks, throughput, RSS, browser/mobile behavior
or installed package operation. Keep the established backend for every deployment
cell without the required evidence. ABI 1 deprecation and independent-engine
retirement have separate consumer migration gates.
