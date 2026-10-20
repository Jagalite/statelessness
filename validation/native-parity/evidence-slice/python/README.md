# Python causal-evidence candidate — qualification pending

The independent Python candidate implements the causal v2 envelope, structural
metadata decoder budgets, phase-aware replay and explicit upgrade, coordinated
EvidenceRecorder retention, diagnostic export, and private exclusive new-file
exports. This directory currently preserves source and intermediate receipts;
it does **not** establish final runtime or installed-package qualification.

Final full-suite Kyoto job `d6b6feb177f0440f85f08b05335aaadc` remains queued under
normal memory-pressure admission. The requested/effective command is
`env PYTHONPATH=python/src python3 -m unittest discover -s python/tests -v` in
`/Volumes/seed2/Projects/stateless-native-parity`. Source and test edits occurred
while it was queued. [pending-source-and-runtime.json](pending-source-and-runtime.json)
pins the held archive-time source, not the submission-time or future runtime
inputs. Terminal receipts will separately pin the actual executed source snapshot.

The intended frozen shared corpus contains 52 wire, 16 replay/upgrade, 20 recorder,
and 13 diagnostic cases. Tests also cover all 3,150 prefixes of the 11 valid wire
fixtures and 44 chunked-read combinations. These counts describe intended final
coverage until a terminal passing receipt is added.

Intermediate jobs are preserved under [jobs](jobs):

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

The candidate remains experimental. This is host-scoped Python work, not a profile
promotion, ABI qualification, hard memory/RSS bound, authenticated provenance, or
rename-atomic/directory-synced publication claim. New-file exports request Unix
mode0600, write unbuffered bytes before fsync, and may leave an explicitly reported
incomplete new file after an I/O failure.
