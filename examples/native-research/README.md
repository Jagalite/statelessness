# Installed finite research consumers

These independent application adapters call the public opt-in research APIs on
the declared tables in `conformance/research-equivalence-v1.json`. Each consumer
supports `run CORPUS OUTPUT` and writes the complete normalized report, ordered
callback log and qualification parameters for each finite case. The installed
driver compares those results against independently specified fixture goldens
and the actual Rust reference. It does not enable production pruning or infer
equivalence beyond the declared finite fixture and its explicit assumptions.

`scripts/check-native-research-consumers.py` builds an isolated Python wheel,
separate Go module, npm tarball with external strict declaration compilation,
and external SwiftPM application. Submit the entire driver through Kyoto only
after the root agent declares the fixture and all implementations frozen. The
driver preserves failed outputs, bounds every command, and rejects source drift.
Native ownership, exception, cancellation or timing controls are host extensions
and must remain separate from cross-engine finite fixture results.

The driver requires `--typescript-compiler` and
`--typescript-compiler-manifest` from the fresh package preparation. Its pinned
manifest digest must be supplied as `--typescript-compiler-manifest-sha256`,
captured before submission rather than computed at job startup. The
compiler is invoked directly; no checkout dependency or global `tsc` fallback
is accepted. It also requires `--rust-build-receipt` from the separate
`validation/corpus` Cargo build captured by the integration runner. That receipt
must bind the current complete source, tools, environment, Cargo artifact log
and actual supplied/copied executable bytes. An arbitrary older binary with a
matching finite output does not qualify the candidate. All research and baseline
runner identities, including the later Swift baseline executable, are retained
and checked after execution.

Installed package/consumer trees and copied fixtures are checked before and
after use, including Python bytecode. The baseline records exact binary
input/stdout/stderr in `portable-baseline-receipts/`, using shared SHA-256 blobs
and an invocation journal. Retain that directory alongside the partial or final
`portable-baseline.json`; process failures and malformed responses do not erase
earlier comparisons or stream bytes.

Use the [combined qualification plan](../../validation/native-parity/derive-model-v2/integration-oct22/combined-n12/qualification-plan.json)
for exact prerequisite build and consumer commands. The full candidate proof
includes both Rust binaries, all five engines, and the existing portable baseline;
`--without-swift` or omission of `--rust-portable` does not satisfy that gate.
