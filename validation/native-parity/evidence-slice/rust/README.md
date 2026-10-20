# Rust causal-evidence reference qualification

Candidate `causal-evidence-v2`, executed through the shared Kyoto queue on the
local macOS Rust target. This directory retains the real public Rust API reference
receipts; it does not promote a profile or establish the other native engines.

## Jobs

| Job | Requested and effective command | Result |
| --- | --- | --- |
| `6f18cb4dc7c043f78f0072be5e63ac69` | `cargo build --manifest-path validation/corpus/Cargo.toml --bin native-evidence` | Initial wire-reference compilation passed. |
| `deaf3b26721447ef97996825916745d3` | `validation/corpus/target/debug/native-evidence --corpus conformance/causal-evidence-v2.json` | Earlier wire execution failed on an error-substring expectation: actual `trace termination disagrees with recorded checks`, expected `property`. The corpus author reviewed the core invariant and corrected the expectation independently. |
| `7eb0f9c8fbd04005893be6f4144bdd19` | `cargo build --manifest-path validation/corpus/Cargo.toml --bin native-evidence` | Full reference and installed-consumer CLI compilation passed. |
| `cdb6e6b8e66048b18020506caff084b6` | `validation/corpus/target/debug/native-evidence --corpus conformance/causal-evidence-v2.json` | Full shared corpus executed and passed. |
| `d0c807d5bdfd4788b4588f7371e96489` | `python3 /tmp/stateless-rust-evidence-smoke.py` | Independent five-command installed-consumer smoke executed and passed. |

Commands in the table abbreviate the absolute executable and corpus paths for
readability. The exact working directory, absolute arguments, requested command,
effective command, policy, timestamps, exit status and original log path are in
[jobs](jobs). These are separate build and execution jobs: compilation is not
reported as a test pass.

[smoke-result.json](smoke-result.json) and [installed-smoke.py](installed-smoke.py)
retain the independent consumer checks. The five commands exercised `record`,
`replay`, passive `read-v1`, explicit `upgrade`, and `diagnostic`. They verified
main callback counts of zero initial-state calls, three reducers, four state
checks and three transition checks; twelve actual controls; exact v2 byte
roundtrip; preserved producer configuration; empty causal contexts after explicit
upgrade; private file permissions; and rejected overwrite.

The resulting five command receipts and raw artifacts are copied under
[smoke-artifacts](smoke-artifacts). [smoke-artifact-ledger.json](smoke-artifact-ledger.json)
pins each original path, archived path, SHA-256, size and original mode. These
artifacts retain real Rust producer stamps: core version `0.2.0` and build
`source-fnv1a64:0e6fa725c30d4235`. The selected 16,384-byte capture budget reserves
1,024 metadata bytes plus 32 envelope bytes, leaving the recorded inner budget
of 15,328 bytes. No producer labels or configuration were stripped.

## Executed shared coverage

[shared-result.json](shared-result.json) records 11 valid and 41 invalid wire
vectors, 3,150 rejected truncations, 44 chunked reads, 16 replay/upgrade cases,
20 real `EvidenceRecorder` cases and 13 exact diagnostic/export cases.

The reference uses public `Artifact`, `TraceV2`, `EvidenceRecorder`, checked-token,
replay and export APIs. It checks exact independently authored wire/value goldens,
invalid writer preflight without output, reader/writer errors, phase-aware replay,
explicit upgrades, metadata/cause/trace eviction alignment, coherent frozen
prefixes and snapshot ownership. Filesystem controls exercise private exclusive
raw/diagnostic exports, no overwrite and roundtrips on this host. They do not
inject file-sync failures or establish a process-memory bound.

The actual target has 64-bit `usize`. Measured Rust layouts are `String = 24`,
`(String, String) = 48`, `OutputOrigin = 16`, and `StepEvidence = 120` bytes. These
confirm the fixed structural decoder allocation weights used by the candidate
vectors on this target.

## Identity and lossless logs

[source-and-binary.json](source-and-binary.json) pins the reference source, public
core dependencies, final corpus/spec, manifests and executable by SHA-256 and byte
size, with the host and compiler identity. It is a receipt-time snapshot after the
full reference was frozen. Intermediate runs used earlier source/corpus revisions;
their exact intermediate input snapshots are not claimed.

Every original Kyoto log is stored as deterministic gzip under [logs](logs),
including failed and intermediate runs. [log-ledger.json](log-ledger.json) records
the original path, uncompressed SHA-256 and size, compressed SHA-256 and size, and
whether the raw log contains trailing whitespace. Decompression was verified
byte-for-byte against each original. Raw Cargo progress output, carriage returns
and trailing whitespace are preserved inside the archives.
