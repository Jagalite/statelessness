# ABI v2 opt-in distributions and qualification

The v2 distribution is additive. Existing independent Python/Go/TypeScript/Swift
engines and ABI 1 remain available. No default changes, publication, performance
claim, or completed migration window is established by building these packages.
Local runtime results are recorded in [ABI-V2-STATUS.md](ABI-V2-STATUS.md).
Installed package/platform qualification and benchmarks remain open.

## Package choices

| Consumer | Opt-in delivery | Preserved deployment mode |
| --- | --- | --- |
| C | Matching native static/shared libraries with both C headers. | ABI 1 exports/header remain available. |
| Python | Existing `statelessness` package gains an explicit `statelessness.native` entry point; generated source package under `target/bindings/python`. Supply the matching native library explicitly. | Ordinary imports remain the independent engine; no native library needed. Platform wheels bundling a native engine require their own qualification. |
| JavaScript/TypeScript | Generated `@jagalite/stateless/v2` export with `v2.mjs`, declarations and a separate `stateless_v2.wasm` artifact. | ABI 1 retains `stateless.wasm` without new v2 host imports; the independent `@jagalite/statelessness` remains unchanged. Explicit asynchronous initialization precedes synchronous v2 operations. |
| Go | Opt-in `bindings/go/v2` cgo adapter with an explicitly linked matching engine; generated `go-v2` module bundle includes relocated C headers. | Independent Go module remains usable with `CGO_ENABLED=0`; the v2 structural trace helpers also compile without cgo. Unsupported native/cross-compilation modes retain the independent engine. |
| Swift | Separate Swift 6 `bindings/swift-v2` package exposes `StatelessNativeV2` and a corpus runner; generated `swift-v2` package depends on the adjacent `portable-swift` DTO/validator source package. Supply the matching library explicitly. | Legacy `bindings/Package.swift` retains its Swift 5.9 manifest and `StatelessNative` product; independent root `Statelessness` remains. Apple mobile targets are not v2-qualified from a desktop build. |

Native artifacts are target-specific. The builder selects the actual compiler
host target explicitly for direct builds; cross-built artifacts use an external
runner's target-specific receipt. It supports conventional Darwin, Linux,
Windows MSVC and Windows GNU library filenames. A supported staging layout is not
executed platform qualification. Darwin relocation/signing requires macOS.
The v2 Wasm build explicitly enables `abi-v2-wasm` in a separate Cargo target
directory. Its output must not replace the default ABI 1 Wasm file.
The Swift v2 corpus runner injects ABI operations into the existing fixture and
protocol validator. Its identity is `swift-abi-v2`; it is not another independent
engine. Generated source bundles include a reduced manifest for the existing
Swift sources, so package resolution does not depend on the original checkout.

## Building or staging packages

In an environment where direct compilation is allowed:

```sh
python scripts/build-bindings.py --kind all --abi-v2 --toolchain 1.90.0
```

The command stages explicit public source files, hashes artifacts and source
inputs, and atomically replaces only its selected generated bundles. It never
uploads or publishes. `--kind native` and `--kind browser` remain independent.
Omitting `--abi-v2` retains the existing package entry points. An old optional
source bundle is retired only when a prior generated manifest identifies it.

For the current Kyoto-only build workflow, use the external-build path instead:

1. Capture source before the authorized build:
   `python scripts/binding_evidence.py snapshot --output target/abi-v2/source.json`.
2. Build through Kyoto, retaining the actual toolchain output, command records,
   exit codes and logs. Inspect the effective command and policy message.
   With runtime testing authorized, execute successfully compiled test binaries
   separately and retain their runtime receipts.
3. Use `binding_evidence.py attest` with that snapshot, successful command JSON,
   toolchain-output files and each `--artifact BUNDLE_PATH=FILE`. For example,
   `native/lib/libstateless.dylib` identifies the built Darwin shared library and
   `browser/stateless.wasm` identifies the Wasm artifact. Include every library
   required for the selected native target, including static/import libraries.
4. Stage without invoking Cargo or rustc:
   `python scripts/build-bindings.py --kind native --abi-v2 --build-receipt PATH`.

Attestation records the external runner's result and exact bytes. It does not
independently rerun that build. Source changes or artifact changes invalidate
prebuilt staging. Header, wrapper, compiler and binary identities must match;
Cargo's package version alone cannot identify a candidate. A cancelled or failed
build cannot produce a successful attestation.

The top-level package manifest records complete staged-file hashes plus the
source/build receipt. Each native/browser package includes `engine-build.json`
with its target, compiler, declared ABI versions and content hashes. Runtime and
performance statuses remain `not_run`. Darwin's relocated/signed library has its
own final package hash, distinct from the original build-output hash.

## Inspection and pending runtime gates

`check-abi-v2-package.py` defaults to inspection. It verifies hashed files,
required headers/typed exports, and optionally the current source identity:

```sh
python scripts/check-abi-v2-package.py --package target/bindings/native --source-root .
```

Inspection never loads a native library or instantiates Wasm. An explicit
Wasm interface check verifies that the default artifact retains only the legacy
host import and that the v2 artifact has its separate dispatch/release imports
and required exports. This is metadata inspection, not WebAssembly execution.
An explicit
`--mode runtime` explicitly enables bootstrap execution. That mode
checks native version/capability bootstrap and missing-file, wrong-bytes,
wrong-version and wrong-library rejection. Those are bootstrap checks, separate
from SDK loading and actual installed model consumers. It does not assert
portable parity, lifetime safety or performance.

The manual [ABI v2 workflow](../.github/workflows/abi-v2.yml) compiles tests with
`--no-run`, builds distributions, checks declarations and inspects packages.
On macOS it also compiles the relocated Swift v2 package and fixture test binary
using `swift build --build-tests`, without running those fixtures.
Linux/macOS jobs compile relocated Go fixture binaries and the corpus executable
without running them. Windows cgo and mobile Swift remain separate pending cells.
Infrastructure tests use fixture bytes and fake bootstrap functions, never an
engine. Configuration is not a passed hosted run. No runtime or benchmark cells
are enabled by this workflow.

The separate [ABI v2 runtime PR check](../.github/workflows/abi-v2-runtime.yml)
executes Rust, C, relocated Go, Node/Wasm, installed Python/npm consumers and
the portable corpus on Linux/macOS, plus relocated Swift tests on macOS.
`check-abi-v2-installed.py --output /outside/checkout/new-directory` installs
the generated wheel and npm archive, checks their import origins, and runs
typed model tests and fresh-process binary replay. It invokes no Rust compiler.
Local Rust compilation still goes through Kyoto. A green PR check qualifies its
specific merge candidate; default adoption still requires the broader gates below.

`check-abi-v2-gates.py` aggregates explicitly supplied, source/language/target
matched receipts. Missing gates remain pending; `--require-ready` fails until
all required gates pass. Compilation/inspection cannot satisfy runtime gates,
and performance requires recorded budget comparisons. Retirement additionally
requires distinct migration/cutover releases and rollback documentation. The
script reports readiness only and never changes an engine default.

`compare-abi-v2-benchmarks.py` compares previously executed measurements against
an explicitly attributed budget file. It requires matching workload/environment
identities, repeated samples, cheap/large/expensive models and observation, BFS,
record, replay, fuzz, shrink and ring coverage. Every cell records median/p95
time, peak RSS, retained bytes, callbacks and allocations. It produces a measured
performance receipt only when supplied actual completed measurements; this
workstream supplies no measurement files, approved numeric budgets or passes.

The benchmark receipt's language identifies the candidate migration lane. Each
cell separately identifies its measured implementation, runtime/compiler and
`baseline_kind`: `independent`, `abi1`, `rust-native`, or `prior-v2`. Baselines can
combine implementations: compare observation/BFS/record/replay to the independent
language engine where available, and fuzz/shrink/ring to measured native Rust or
an actual prior v2 implementation. The comparator rejects unsupported independent
or ABI 1 baseline operations. Never fabricate zero measurements for absent APIs.
The full baseline artifact inventory must identify all reference implementations;
compare the same model, corpus, bounds and machine environment, and approve budgets
for the chosen reference before collecting candidate results. A shared Rust
reference measures adapter overhead; it does not claim an independent engine
previously exposed that capability. Missing measured references keep the gate
pending. No absolute-only fallback is currently implemented.

Every proposed default still needs executed installed-consumer, SDK negative
loading, ownership/error, semantic, historical fresh-process replay, and measured
performance gates. Unchanged ABI 1 consumers and claimed deployment contracts
have their own required gates. Language replacements also require the full four-profile
portable suite. Browser targets require actual browser evidence separately from
Node. Keep the established backend for any unmet OS, architecture, runtime or
deployment mode. ABI 1 retirement is a separate consumer migration decision.
