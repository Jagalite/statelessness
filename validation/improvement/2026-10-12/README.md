# M0 local qualification receipt — compile only

Base: `0949e7992f1be34d2052b22f12b7cbb0ce6b7a95` from origin. The implementation
was uncommitted during qualification; the reports bind the actual source bytes,
including new files, rather than treating that base commit as the tested code.

The user required Rust builds/tests through Kyoto and then selected **keep the
shared policy; compile only**. The [read-back policy](kyoto-policy.json) retains
the original two `--no-run` requirements. No Rust test, replay fixture, example,
or benchmark runtime was executed for this increment.

## Results

- [Unified report](m0-msrv-compile.json): all selected compilation/tooling checks
  passed on macOS aarch64, Rust/Cargo 1.90.0. Workspace, debugger examples,
  diagnostics-disabled tests and starter tests compiled. Strict all-target,
  all-feature Clippy, warnings-denied Rustdoc, formatting and fixture builds passed.
- The unified runner's Python tooling stage passed **43 tests**. These cover
  report failure/timeout/partial states, expected negative exits, owned queue
  cancellation, explicit compiler selection, installed-package source identity,
  benchmark sample integrity, and existing packaging/provenance helpers.
- [Benchmark report](benchmark-compile/report.json): engine and observation
  targets compiled in the optimized bench profile. `source_stable: true`,
  `status: compiled_only`, and `measurements: []`.
- The capability generator, local documentation links, `actionlint` on all four
  workflow files, and `git diff --check` passed. Hosted workflow execution is
  not part of this receipt.

The unified report deliberately says `passed: false`, `status: incomplete` and
`qualification_complete: false`. `selected_checks_passed: true` applies to the
allowed compilation/tooling checks, not to the unexecuted Rust assertions.
No native-port runtime, installed Rust consumer, hosted matrix or performance
baseline is newly qualified. M0's runtime and measured-baseline exit gates remain
open; see the [implementation ledger](../../../docs/improvement/STATUS.md).

The unified source fingerprint is
`e409554051a07ac59b8b6124aa9f773d3666e1ab925308856f1f0cf32bbfb6cb`.
Source identity stayed fixed throughout the final run. Evidence archives under
this directory are intentionally outside the source-input fingerprint to avoid
self-referential hashes. [Artifact digests](artifacts.json) bind the copied logs
and reports; keep each report with its sibling artifact directory.

## Toolchain correction during preparation

The initial default-compiler workspace compilation succeeded with Homebrew Rust
1.99. A preliminary run then showed that setting `RUSTUP_TOOLCHAIN=1.90.0` alone
did not select rustup's tools because Homebrew binaries preceded the shims on
PATH; that preliminary run was interrupted and is not MSRV evidence. Similarly,
the benchmark driver's initial `cargo +1.90.0` attempt was rejected by the
standalone Cargo binary.

The final drivers use explicit `rustup run`, resolve the selected toolchain's
binary directory, and pin `RUSTC`/`RUSTDOC`. The actual compiler probes and queue
commands in these final receipts establish Rust 1.90.0. Earlier diagnostic
receipts remain locally under `target/qualification/m0-compile.json` and
`artifacts/benchmarks/m0-compile-only*`; they are not substituted for final results.
