# Compiler-resolved Rust API inventory

This inventory enumerates the actual **native/default library API** of the pinned
Rust core, procedural-macro crate and optional debugger. It uses compiler-resolved
rustdoc JSON rather than lexical `pub` scanning. No compiler-graph cases remain
unresolved for this specific target/feature scope. Per-language API equivalence,
contract review and runtime qualification remain open; this is not completion of
M0 or N01–N14.

## Executed scope and evidence

Three explicit `cargo +nightly rustdoc --locked -p PACKAGE --lib -- -Z
unstable-options --output-format json --document-private-items` commands executed
successfully through Kyoto. Their complete command vectors, timestamps, log hashes
and job IDs are retained in [semantic-receipt.json](semantic-receipt.json).
`CARGO_TARGET_DIR=target/native-parity-rustdoc` isolates their artifacts.

The actual toolchain is `rustc/rustdoc 1.97.0-nightly (d3cd04068 2026-05-16)`;
rustdoc JSON format is **57**, and the compiled target is
**aarch64-apple-darwin**, with default Cargo features and ordinary library cfg.
There was no model/test execution, `RUSTC_BOOTSTRAP`, dependency addition, or
compiler policy change. The parser rejects other JSON schemas until reviewed.

The first three submissions encountered the machine's Homebrew Cargo before the
rustup proxy and failed with `error: no such command: +nightly`. Their job IDs are
`70844fb21a984cd2abe82abb8ce7cbbb`, `3bb7c049235d4acfaf9f75323fba1460`, and
`0eaf2b87aba5498ab91f465ba82fd002`. Corrected submissions explicitly put the rustup
proxy directory first in `PATH`; the nightly toolchain itself supports the
requested JSON command. Successful receipt IDs are:

- Core: `363583b2ad8a475a995631ff6f3b2d52`
- Macros: `7b8499d2e2304b55900ab57a96937bea`
- Debugger: `b2f7ff0323274f5d91dc9fb4a92046de`

The receipt pins raw JSON hashes/size/mtime, compiler identities, all source/build
input hashes, source agreement with Rust baseline commit
`0876717bee85e335449c2c99ada41f6c6e6112d1`, the current worktree HEAD/dirty status,
and the parser/ledger hashes. Source/build input modification times predate all
three successful jobs. Raw JSON timestamps lie within the respective job windows,
allowing one second for the external filesystem's timestamp resolution. Source
Rust declarations match the pinned baseline; the receipt separately exposes
manifest/build-input differences instead of pretending this is a clean baseline
checkout.

## Artifacts and counts

| Crate | Included records | Compiler index records | Unresolved graph cases |
| --- | ---: | ---: | ---: |
| Core | 2,886 | 7,350 | 0 |
| Procedural macros | 8 | 211 | 0 |
| Debugger | 2,970 | 6,545 | 0 |

The **5,864 included records are not a feature count**. They include crate/module
nodes, reexport sites, fields, variants, methods, associated items and 1,779
source-backed implementation blocks. Derived standard-trait implementations are
included and labelled separately from handwritten engine algorithms. Reachable
public enums contribute 538 explicit variants; public traits and implementation
members contribute 40 associated-type records.

- [semantic-symbols.jsonl](semantic-symbols.jsonl) contains one included record
  per rustdoc identity, with source locator, kind, public namespace aliases,
  contextual role, N01–N14 mapping and definition hash. Every native symbol mapping
  remains `pending_individual_api_review`; broad capability implementation states
  are not copied onto individual symbols.
- [semantic-receipt.json](semantic-receipt.json) contains the complete disjoint
  partition of every compiler index ID. Excluded groups distinguish private or
  unreachable local items, external crate definitions, synthetic auto-trait
  implementations and external blanket-implementation metadata.
- [semantic-unresolved.json](semantic-unresolved.json) is empty for each crate.
  Unknown public item kinds, missing targets, stripped public members, unsupported
  receivers and leaked nonexported local signature types cannot silently count as
  a completed graph.
- [semantic-summary.md](semantic-summary.md) is the generated count summary.
- [semantic-validation.json](semantic-validation.json) preserves successful Kyoto
  parser regression and complete graph/source/raw identity-check receipts, with
  log hashes and the exact parser/ledger identities. These validate inventory
  tooling, not model execution or native-port parity.

Raw JSON remains under `target/native-parity-rustdoc/doc/` rather than adding
approximately nine megabytes of compiler-specific raw output to source control.
The receipt preserves its identities; reproducing/checking the raw artifacts
requires the documented compiler and build commands. These are local compiler
receipts, not an authenticity guarantee or a package/release qualification.

## What the graph resolves

The resolver follows public modules and explicit/glob reexports, including
`model::*` at the core root and `ffi_v2::api::*` from a private implementation
module. Alias names remain alternate paths of the same resolved declaration;
reexport edges are recorded explicitly rather than inflating feature counts.
Private `operations` and `campaign::lifecycle` helpers do not become public API
merely because some nested declarations have `pub` or `pub(super)` visibility.

Public enum variants and variant fields inherit visibility even without a `pub`
token. Public trait methods/associated types/constants are included contextually.
Public inherent methods are attached to reachable receiver types; private methods
and private trait implementations stay excluded. Source-backed implementations
of reachable public traits for primitives/containers, and standard/external traits
for reachable local types, are recorded with their receiver, trait, generics and
provided default-method names. Synthetic auto/blanket metadata is accounted
separately. Imported standard-library default method definitions are not invented
as new local declarations.

Macro names are compiler-resolved consumer names: `#[model]`, `TraceEncode`,
`TraceDecode`, `Inspect`, `ModelCodec`, `input_domain!` and `macro_build_id!`.
The core's exported `check!`, and debugger's exported `probe!`/`marker!`, are
included even though lexical function scanning misses `macro_export` surfaces.
The ledger maps macros to idiomatic native adapter outcomes; standard-trait
mechanisms to equivalent behavioral outcomes; and FFI transports to retained
alternatives. It does not require literal Rust syntax or using Rust underneath
an independent native engine.

## Reproduction and checks

All compilation and tests must go through the shared Kyoto queue. For example,
from this worktree (replace the package with each of the three packages above):

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- env PATH=/Users/jagatranvo/.cargo/bin:/opt/homebrew/bin:/usr/bin:/bin CARGO_TARGET_DIR=target/native-parity-rustdoc cargo +nightly rustdoc --locked -p statelessness --lib -- -Z unstable-options --output-format json --document-private-items
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 scripts/native_semantic_inventory.py --self-test
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- python3 scripts/native_semantic_inventory.py --check
```

The parser's self-tests cover private-module named/glob reexports, inherited enum
and trait member visibility, inherent methods, private traits/methods,
source-backed external trait implementations, auto/blanket metadata exclusion,
full index accounting, unsupported schemas and unresolved targets. The checker
requires raw JSON to be present and validates graph/source/raw/parser/ledger
identities; it never launches a build to repair missing or stale evidence.
The final four-test parser run passed in Kyoto job
`7a70bbeb0c944d3d95e410d179218bcc`; the complete identity check passed in
`be95c939e5274d90bf4f253efa5e1ab9`.

`--write` generates only metadata from existing compiler output and successful
Kyoto receipts. The script currently pins the three successful job IDs in `JOBS`;
for a deliberate regeneration update those IDs to the new executed commands and
review the resulting receipt/ledger diff. New source modules without an explicit
N01–N14 mapping fail generation. Rustdoc IDs are compiler-output identities,
not stable wire-format or cross-compiler API IDs.

## Explicit limits

This closes compiler-derived declaration enumeration only for the tested native
host and default features. Wasm `abi-v2-wasm`, debugger
`compiled-out-diagnostics`, Windows/Linux targets, other architectures and
feature combinations require separate compiled snapshots before claiming that
scope. `cfg(test)` items, binaries, examples, benches, integration tests and
application macro invocations are outside these library snapshots. Their N14
fixture/tooling obligations remain tracked in the parity plan.

Compiler resolution establishes which declarations and members are reachable;
it does not specify their complete behavior, establish native language
counterparts, or prove error/resource/compatibility semantics. The reviewed
feature-level ledger remains useful, but individual API decisions and normative
contract cases are still required. General liveness, general partial-order
reduction, a TUI and LLDB/GDB integration remain outside this Rust baseline.
