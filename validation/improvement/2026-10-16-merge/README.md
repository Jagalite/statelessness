# Merge qualification after ABI v2

Recorded for the roadmap branch after merging `origin/main` at
`c8f19c8` (ABI v2). The fix commit `63587a6` addresses two reviewer findings
(retry-counter overflow from a restored reference checkpoint; uncharged network
decode allocation). Rust 1.90.0 (`aarch64-apple-darwin`), clean committed tree.

Every Rust compilation was submitted to Kyoto
(`/Volumes/seed1/Projects/kyoto/kyoto.py`); shared policy was not changed.
Kyoto's policy compiles tests with `--no-run`; the compiled test executables,
examples and reference controller were then **run directly**, as in the
[ABI v2 runtime receipts](../../../docs/ABI-V2-STATUS.md#evidence-and-policy).

## Results

| Check | Result | Source |
| --- | --- | --- |
| `cargo fmt --all --check` | Passed | `63587a6` |
| Strict Clippy, workspace/all targets/all features, `-D warnings` | Passed | `63587a6` |
| Rustdoc, workspace/all features, `RUSTDOCFLAGS=-D warnings` | Passed | `3f216b7` |
| Workspace tests (`--workspace --all-features`), 59 executables | 621 passed, 0 failed ([per-binary results](rust-test-results.tsv)) | `63587a6` |
| Debugger examples, diagnostics compiled out, starter/controller example tests | 30 executables, all passed | `3f216b7` |
| Starter check, find, original/minimized replay, changed-model rejection | Expected exit codes and text | `3f216b7` |
| Reference assurance (exhaustive) and report schema | Adequate; schema valid | `63587a6` |
| Reference fault controls and fresh-process witness replay | All required mutations killed; both witnesses replayed; regressions passed | `63587a6` |
| Reference live host and fixed replay | Exact replay | `63587a6` |
| Macro consumers and all negative fixtures (`check-macros.py --compile-only --kyoto`) | Passed, including all 26 negative diagnostics ([receipt](macros.json)) | `3f216b7` |
| Python tooling / requirements / requirement-change review / schemas / capability docs | 96 tests OK; all gates passed | `63587a6` |
| Native Python, conformance tooling | OK | `3f216b7` |
| Native TypeScript | 47 passed | `63587a6` |

Hosted CI on the pushed branch supplies the Linux/Windows/macOS matrices.

## Still not claimed

Benchmark measurements, adoption of any performance candidate, promotion of
paired-v1, Go/Swift candidate parity, and independent human review of the
reference requirements remain open, as described in the
[implementation ledger](../../../docs/improvement/STATUS.md).
