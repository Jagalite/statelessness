# N08/N09 implementation handoff — acceptance blocked

Baseline `4e84cf1a8bf15b2e6b0698d6801a92dc94a45d00` and pinned Rust behavior
`0876717bee85e335449c2c99ada41f6c6e6112d1` are preserved. No Rust source, optional
package boundary, dependency manifest, shared capability status, PR or publication
was changed. TypeScript adds one public export line; parent resolves combined
exports. This lane is **not qualified or parity-complete**.

## Implemented source

All four engines now have independent two-child composition, explicit networks,
legacy lifecycle and scoped indexed lifecycle candidates. Typed codec recipes
reuse native bounded value codecs; model adapters reuse checks, trace replay and
oracle APIs. Search equality includes queues when the application's input equality
is supplied. Indexed histories use explicit fences, retain violations, reject
stale references and never infer retirement from timeout/cancellation.

See [contract](N08-N09-CONTRACT.md) for pinned-surface correspondence and the
explicit indexed-diagnostics difference, and [examples](N08-N09-EXAMPLES.md) for
native entry points and reproduction. New tests cover two-/three-machine handoff,
FIFO versus independent channels, saturation, bounded cycles, inclusive deadlines,
prior overdue history, duplicate publication, 1,000 fenced epochs, codec goldens,
restart, raw historical strings and selected snapshot/equality regressions.

The source-bound runner builds all four ordinary native consumers, compares
indexed failure-history bytes, attempts 16 fresh-process directed native failure
trace replays, and runs a compiled semantic stale-reference mutant in each engine.
Its existence is not evidence those operations passed.

## Independent review

One allowed native read-only reviewer examined the contract and successive source
changes. Reported issues were corrected: Python None-key overdue detection,
collection allocation layouts and classifier key ownership; Go first-match legacy
keys, deterministic diagnostic text, nil/empty restart equality and value-key
restrictions; TypeScript oracle equality, append-first wiring and callback-free
local-ID decoding; Swift exact UTF-8 key option, metadata and queue equality;
lossless legacy diagnostic strings and source/artifact/trace receipt identities.
The review was static. It did not execute compilation or tests or approve full
acceptance. Parent acceptance review remains required.

## Exact validation receipts

- Early frozen snapshot `/tmp/stateless-n0809-first`: Kyoto
  `480472a504bf45db9ec3e95c0e9bf7d1`, effective command
  `sh /tmp/n08-frozen-check.sh`, policy_message `null`, exit **0**.
  Executed existing Go suites, TypeScript compilation, **252 Swift tests**, and
  **424 Python tests with 12 skipped**. This predates new stateful regressions,
  network/pair implementations in several languages and later codec fixes.
  It is historical compatibility evidence only, **not current-source qualification**.
- Latest immutable source: `/tmp/stateless-n0809-r6`, manifest
  `/tmp/stateless-n0809-r6-source-manifest.json`, **589 files**, SHA-256
  `633163fc3c6375ff316198fc1818db8f88325d631972341bb509336a387c6d79`.
- Current Kyoto job: `216609077a3f4d5ab439d83b95a15ffa`, owner
  `a4f25813-d265-4a9c-b334-5a5e29db8697`, effective command:

  ```text
  python3 /tmp/stateless-n0809-r6/scripts/qualify-native-stateful.py --source /tmp/stateless-n0809-r6 --output /tmp/stateless-n0809-r6-receipts
  ```

  policy_message `null`; **QUEUED / NOT_RUN**, no exit code. At the receipt snapshot,
  Kyoto had zero running jobs and reported `OS memory pressure: warning` for this
  job (42 jobs queued). Earlier admission was blocked by active swapping, with a
  temporary interval admitting other jobs. Shared policy was not changed.
- Superseded queued snapshots were cancellation-requested, never counted as tests.
  The first cancellation attempt lacked its owner ID and was denied; it was then
  repeated with the returned matching owner. No queue failure or compile failure
  has been claimed as semantic-mutant detection.
- Concise immutable receipts: [jobs](receipts/n08-n09-20261029/jobs.json),
  [source identity](receipts/n08-n09-20261029/source.json), and
  [queue state](receipts/n08-n09-20261029/queue.json). Full logs and snapshots remain
  outside tracked source. `git diff --check` passed on the authored edits.

## Remaining blockers and required continuation

1. Current source has **not compiled or executed**. Wait for the existing queued
   job; do not duplicate its frozen source job. Read every command result and fix
   failures in a new source snapshot. Never edit `/tmp/stateless-n0809-r6`.
2. Installed-consumer execution, history interchange, 16 native trace pairs and
   all mutation controls are pending. Compilation failures must remain failures,
   not detected mutants. The runner retains failed attempts and launch errors.
3. Pinned Rust indexed failure diagnostics use generic Debug-formatted violations
   and retention. Native code-list diagnostics advertise a distinct semantic
   identity. This remains an explicit compatibility gap; matching history bytes
   do not close it. The Rust producer/consumer leg is not implemented in this
   lane's runner, so no five-engine matrix is claimed.
4. Required nonlocal desktop/runtime matrices, broader property/ownership
   differentials, resource budgets and equivalent-work performance measurements
   remain unqualified. The early job's scheduler RSS/time concern a different
   source and mixed suites, not an equivalent-work benchmark.
5. Parent must inspect the integrated exports, source mapping, acceptance
   requirements and fresh combined qualification before updating N08/N09 ledger
   cells. No PR merge/publication is authorized by this handoff.

Resume the existing queue item with `kyoto.py show/logs/wait` and retain its exact
source manifest, tool-version logs, packages, executable hashes, mutation receipts
and replay matrix. The queue condition is an external execution blocker; this
handoff does not represent completion of the entire native parity plan.
