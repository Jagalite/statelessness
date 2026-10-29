# PR12 N05/N06 continuation receipts

This is an **incomplete implementation/qualification handoff**, not acceptance.
The full source decisions and remaining gates are in
[the continuation](../../../../docs/native-parity/N05-N06-CONTINUATION-20261029.md).

`jobs.json` records exact effective commands, policy messages, logs and log hashes.
`rN-source.json` binds each copied source snapshot. Raw output, build trees and
executables remain outside tracked source under `/tmp/stateless-n0506-rN`.
Do not rerun an active frozen job; refresh these handles first. No queue policy
was changed. The attempted cancellation without owner ID was rejected; cancellation
of the superseded r3 installed attempt then used its recorded matching owner ID.

## Executed results

- `c67d17e9edff47ffa9f790979654d2b6`: N05 author **passed**, 90 cases, exact
  expected corpus and FREEZE hashes. Materialization only, not engine qualification.
- `0a56270d66844dcbac2aa269fc1d56f1`: N06 author **failed** its historical
  absolute-workspace guard. This rejected attempt remains preserved.
- `468609e85cd9483b928399d73635826c`: portable N06 author **passed**, 49 rows;
  expected-row hash exactly matches the supplied historical hash, new seal recorded.
- `00965efe5df5451ab3a678518c99e3de`: r1 native run **failed overall**.
  Python ran 522 tests: two failures, one error, 12 skips. Its campaign tests
  included 54 individually passing methods. Go compiled and executed; 30 campaign
  and 21 guided top-level tests passed, but two other top-level suites failed.
  Go vet passed. TypeScript ran **1181 tests, all passed, no skips**. Swift failed
  compilation with signal 6 in Swift 6.3.3's SendNonSendable pass at
  campaignStartThread. No Swift runtime pass. Later source fixes are not covered
  by those individually passing r1 tests.
- `6fd6621ed44b4718ae6daabd6be097db`: Rust campaign private/public fixture
  compilation **failed** because generated campaign.rs could not resolve lifecycle.
  The subsequent undefined-type diagnostics cascade from that missing module.
  No semantic mutation was detected and no test executed.
- `a627f9513beb4abe92719f58b0202d72`: Rust guided consumer **compiled**.
  Executable `/tmp/stateless-n0506-r1/source/scripts/native-guided-consumers/rust/target/debug/native-guided-rust`,
  SHA256 `ea37788bd822d9c60fb14efcc6ce52f72552d9796db8e9cc4966a5e55bb75614`.
  Every listed r1 source hash was rechecked unchanged after compilation.

r1's child maximum RSS was 467173376 bytes. This aggregates different commands,
includes compilation and process startup, and is **not** an equivalent-work budget.
Its tool/version and individual command timing receipts are in `r1-native.json`.

## Unexecuted follow-ups at receipt capture

Shared Kyoto reports OS memory pressure warning and zero admission capacity.
These are queued, not passed, and their immutable snapshots must remain intact:

- `d84436e2c84f47768abff1c64fa47092`: r2 native suites, corrected admission,
  diagnostic and panic provenance source; stronger identity/timeout recording.
- `2f6e70b3af614400b636bbb9d4dbfe49`: r3 Swift tests with narrow compiler workaround.
- `b0c8d8799b2f483abcc796713e4fccaa`: r3 Rust campaign fixture compile. Effective
  command has `--no-run`; runtime execution must be submitted separately afterward.
- `e37d6793a1e847eb900630b5686c697b`: r4 installed 90-case five-engine comparison,
  strict type/envelope/membership checks, pinned Rust build/executable, three
  fresh-process resource repetitions for each passing engine. Python N06 output
  here is smoke execution only, not 49-row semantic qualification.
- `584c5282fbfa478d9d19f7de0dd0de9c`: r4 comparator negative controls.

Superseded unexecuted installed job `74f63aadc3004124b21c5bbe535c6658` was cancelled
before its strict comparator/provenance review fixes; it supplies no evidence.

## Independent read-only review

One native reviewer (`/root/review`) reviewed the recovered contract, incremental
production fixes and bounded runners. Its Go finding about application As/Unwrap
execution was fixed with direct private type assertions plus a hostile-error test.
Its comparator findings were fixed with type-sensitive equality, duplicate-key
rejection, pinned 90-case corpus/seal and envelope membership checks. The narrow
Swift ownership workaround and Rust generated module layout were source-approved.
Review did not execute tests and did not approve full parity.

Remaining receipt limits include compiler-launcher versus resolved installation
identity, incomplete post-build copied-package identity checks, and coordinator
association of Rust build receipt/source/executable. These runners do not replace
historical R3's unrecovered filesystem/receipt hardening or final integration checks.

Full-width Go/Swift/TypeScript count implementation remains unfinished. Installed
N06 five-engine semantics, current-source executed acceptance, platform matrices,
resource budgets, N10 assurance integration and final parent review remain open.
