# Python causal-evidence candidate qualification

Final full-suite Kyoto job `d6b6feb177f0440f85f08b05335aaadc` executed successfully:
216 tests, 12 ABI skips, exit0. The requested and effective command was
`env PYTHONPATH=python/src python3 -m unittest discover -s python/tests -v` in
`/Volumes/seed2/Projects/stateless-native-parity`. This is actual host CPython
execution, not compilation or installed-package qualification.

[shared-result.json](shared-result.json) records all 52 wire, 16 replay/upgrade,
20 EvidenceRecorder and 13 exact diagnostic cases. The suite rejects all 3,150
prefixes of the 11 valid fixtures and passes 44 chunked-read combinations. Native
regressions cover semantic-before-format error precedence, quota-before-owned-copy,
closed-schema getters and metaclass spoofing, independent metadata decoder budgets,
phase-aware replay, atomic metadata/cause/trace eviction, first-error freezing,
snapshot/transfer ownership, private exclusive exports, fsync failure and complete
file bytes reaching the descriptor before fsync. Existing Python suites also ran.
The twelve ABI skips do not qualify the FFI implementation.

The source was committed as candidate `d8ba663` with qualification explicitly
pending. Source/test edits happened after the final job was submitted but while
it remained queued under normal memory-pressure admission. No claim is made that
the source at submission matched the executed input. The job started at
`1792536787.663701` and finished at `1792536793.781284`.
[source-and-runtime.json](source-and-runtime.json) pins the actual held runtime
input hashes, host/interpreter and modification times. Every recorded input
predates actual job start and matches the committed archive-time hash in
[pending-source-and-runtime.json](pending-source-and-runtime.json). Source stayed
unchanged throughout execution. The earlier pending receipt remains historical.

Intermediate jobs remain under [jobs](jobs):

- `28eaa4a68f2743a08a59087e7933a24e`: failed on two test-adapter mistakes: the
  nested v1 export fixture path and an unforwarded build-mismatch override.
- `947a810dd11f4d9789ab280b88dbc7ea`: failed on eight embedded-v1 quota error
  text mismatches. Error categories were aligned without weakening fixtures.
- `d6d4960d48c6473f9b916943e5897ffd`: intermediate full suite passed 211 tests
  with 12 ABI skips, before later admission/order/export regressions were added.
- `d06b44a4f5c04c1195f1eec8c3fc067c`: intermediate focused recorder run passed
  16 tests, including 20 shared recorder vectors.

Intermediate source snapshots are not claimed. Logs are deterministic gzip with
lossless decompression verified; [log-ledger.json](log-ledger.json) pins original
and stored byte sizes and SHA-256 values, preserving raw trailing whitespace.

The implementation remains experimental. Installed-package and cross-language
qualification are separate gates. This is not profile promotion, ABI qualification,
a hard memory/RSS bound, authenticated provenance, or rename-atomic/directory-synced
publication. Structural allocation accounting uses the explicit measured Rust64
schema request weights, not Python object sizes. New-file exports request Unix
mode0600, write unbuffered bytes before fsync, and may leave an explicitly reported
incomplete new file after an I/O failure. Supported closed evidence values are
immutable; upgrade does not invoke application copy hooks or require speculative
private replay-kernel extensions.
