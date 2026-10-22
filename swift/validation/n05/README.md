# Swift N05 qualification records

Candidate source base: `a4cc11eea8db62115b452eee3c6610c04fa06b96`.
Only the native root SwiftPM package is used; `bindings/` is not a dependency.
The root task owns staging/commit. No profile, default, campaign or assurance
promotion is part of this work.

Copied packages and build products are excluded by this directory's narrow
`snapshots/` ignore rule; authored manifests, receipts and logs remain visible to
Git. R1–R3 snapshots were moved only after all their jobs were terminal to
`/tmp/stateless-n05-swift-terminal-o4g_39i6/{r1,r2,r3}`. Each
`rN-snapshot-relocation.json` records the external path, terminal job identity,
original manifest hash, source membership, file types and hashes. Original
submission commands and manifests are unchanged. The relocation's first metadata
attempt stopped before any move because R1 uses `package_path` rather than
`snapshot_path`; the corrected pass understood both formats and verified every
source hash before moving. These are temporary local evidence paths, not vendored
source or portable build caches.

R4 remains at its submitted path while queued or running. Future snapshots use
external `/tmp` staging from the start and a `stateless-swift-source-snapshot/2`
manifest. The installed runner verifies exact source membership, file types and
hashes before and after execution; only SwiftPM's `.build` and `.swiftpm` output
subtrees are excluded. No receipt trees are copied into future source snapshots.

The API is `Feedback<S,I,O,K: Hashable>`, `StateFeedback<S,K>`,
`exactStateFeedback(model)`, `FeatureBuffer<K>`, `GuidanceConfig`, `CorpusEntry`
and `GuidanceReport`. `guidedFuzz` accepts optional controls and observer, covering
plain/controlled/observed combinations through the ordinary fuzz kernel.
`FuzzReport.guidance` is nil for ordinary search.

Source-only review checks admission order, checked/observed transitions before
feedback, source rejection-index draws, FIFO/witnessed accounting and the
ordinary previous-sequence policy. Native tests exercise error/overrun precedence,
reference keys and snapshots, revoked sinks, collision resolution, exact-state
oracle history, count/saturation edges and the existing ordinary-search corpus.
Swift compile-negative fixtures own the synchronous and non-Sendable policies;
these are language decisions, not runtime Rust-equivalence claims.

## R1

Frozen input: `r1-source-manifest.json`; private package snapshot:
`snapshots/r1/package` (ignored generated files). This snapshot predates the
additional OracleState Hashable and ownership tests; it cannot qualify those
later changes. Its 13 GuidedTests and all pre-existing tests are compiled/run by:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  swift test --package-path swift/validation/n05/snapshots/r1/package -j 2
```

Job: `2e32e76699cd4aed89aac585f9341e2b`. Submission and sampled status are retained
in `r1-submit.json` and `r1-show.json`. A queued job is not executed evidence.
Subsequent revisions/results are appended below; failed logs are preserved.

## Shared corpus and installed consumer

`scripts/native-guided-consumers/swift` owns the standalone public consumer.
`conformance/guided-v2/README.md` and `FREEZE.json` are independent specification
authority. No expected values may be generated from candidate output. Shared
comparison requires the independent freeze, a copied source package, exact input
manifest, raw command logs and complete report/trace/RNG/witness/shrink comparison.
The root task's combined five-engine harness owns final installed Swift execution.
The optional `run-installed.py` helper is intentionally unsubmitted to avoid
duplicate execution; the earlier R3 installed submission was cancelled unstarted.
Runtime and consumer sources are frozen pending that combined run. Only receipt
updates and collection of the existing R4 job are currently authorized.

The complete guided example is `swift/Examples/Guided`. Native async/sink transfer
rejections are checked after building a frozen package with:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit -- \
  python3 swift/validation/n05/check-compile-contracts.py \
  --package swift/validation/n05/snapshots/REV/package \
  --output swift/validation/n05/REV-compile-contracts
```

These scripts must themselves be submitted through the default Kyoto queue.
Do not run test binaries or Swift compiler commands outside that queue.

R1 was superseded while still queued (never started), then cancelled using its
recorded owner UUID. `r1-cancel.json`, terminal `r1-show.json`, and empty raw log
remain. No source/test failure is attributed to that unexecuted revision.

## R2

`r2-source-manifest.json` pins the 16 native guided tests, updated source, compile
contracts and example. The frozen runner is submitted as:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit \
  --user-id b03849fd-b39d-40f1-93e8-a7fb6a18d8d3 -- \
  python3 swift/validation/n05/snapshots/r2/run-native.py \
  --snapshot swift/validation/n05/snapshots/r2 \
  --output swift/validation/n05/r2
```

Job: `10982113bd6c4b1589d10a47ef72c088`; submission/status are in
`r2-submit.json`/`r2-show.json`. The wrapper records the exact toolchain/test/compiler/
example commands and raw output separately. It exits at the first failure and
never substitutes a partial success for whole-run success. Submission alone is
not qualification; consult terminal receipts and logs below.

R2 completed successfully on Apple Swift 6.3.3, arm64 macOS: all 163 XCTest tests
(including 16 guided tests), both expected Swift6 compiler rejections, and the
public example. Exact commands/timing are in `r2/results.json`; raw suite,
compiler and example logs are retained. The example printed
`feature-corpus-v2 3 2 failure_found exact`. One later diagnostic correction is
not covered by R2: guided invalid mutation now reports the source's
`mutation_percent must be in 0..=100`; ordinary fuzz keeps its existing
`invalid fuzz configuration`. A unit assertion checks both behaviors.

## R3

`r3-source-manifest.json` pins that diagnostic correction and the first installed
consumer. Native job `b58d5f6e2fc94ec3a672d21be1b8dac6` runs the same native wrapper
against `snapshots/r3`, with output under `r3-native`. Submission/status retained
as `r3-native-submit.json`/`r3-native-show.json`.

The installed job `b4a9da6d99a542c0a4b9c0b4aaf6fb7b` was still queued and unstarted
when the independent specification owner withdrew the 78-case freeze to add
boundary and disposition witnesses. It was cancelled by its recorded owner before
any comparison. Its submission/status/cancellation and raw log remain; it is not
an installed pass or failure. The archived snapshot retains the old freeze for
provenance and must not be reused as the current acceptance corpus.

The first R2 log-retrieval attempt reported `Worker recovery incomplete; saved
job states are not verified. Retry shortly or inspect worker.log.` Retrieval was
retried after recovery and succeeded; `r2-raw.log` retains the full Kyoto wrapper
log, while per-command raw logs were already present. This was a scheduler-status
retrieval failure, not a Swift compilation/test failure.

R3 native remained queued/unstarted when source review found a feature-clone
reentrancy edge. It was owner-cancelled as superseded; `r3-native-cancel.json`,
terminal status and raw log are retained. No R3 native execution is claimed.

## R4: focused sink-boundary correction

The feature sink now rejects reentry during clone with a sticky failure, performs
cloning outside array mutation access, and checks sticky failure before append.
The feedback callback's own error still wins. Revocation releases the clone
closure as well as storage, so escaped expired sinks do not retain clone captures.
Two tests cover reentry/error priority and weak capture release after success/error.

Only the focused 18-test GuidedTests suite is requested on this revision; the R2
163-test aggregate and compiler/example receipts remain scoped to R2 source.
`r4-source-manifest.json` pins the package snapshot. Exact submission:

```sh
python3 /Volumes/seed1/Projects/kyoto/kyoto.py submit \
  --user-id b03849fd-b39d-40f1-93e8-a7fb6a18d8d3 -- \
  swift test --package-path swift/validation/n05/snapshots/r4/package \
  -j 2 --filter GuidedTests
```

Job `50aef69ec07148cc81e9ed6ab82c5521`; submission is `r4-submit.json`.
Status/results must be taken from the terminal receipt and raw log, not submission.
The installed consumer also matches the revision 3 disposition/failing-replay
schema; its comparison is gated on the newly published independent freeze.
