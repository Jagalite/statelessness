# Illiquid migration: upstream Statelessness proposals S1–S6

Status of the upstream work requested by Illiquid's
`Documentation/STATELESSNESS_SWIFT_MIGRATION.md`. All of it is additive and
opt-in. Existing `bfs-v1`, `search-v1`, trace-json v1 and causal-evidence v2
semantics are unchanged. Swift (PR #14) is the package Illiquid consumes. The
same four contracts are also implemented in the other engines (see
[Other engines](#other-engines)). They remain experimental candidates, not
promoted portable profiles.

| Proposal | API | Contract | Tests |
| --- | --- | --- | --- |
| S1 Admitted graph and complete reporting | `exploreGraph`, `GraphSearchOptions`, `GraphSearchReport`, `GraphEvent`, `GraphRetention` | [graph-search-v1](../spec/graph-search-v1.md) | `GraphSearchTests`, `MigrationFixtureTests` |
| S2 Failure collection and result semantics | `FailurePolicy.collect`, `GraphFinding`, `PropertyFailureCount`, `GraphCoverage`, `GraphSearchError` | [graph-search-v1](../spec/graph-search-v1.md) | `GraphSearchTests`, `MigrationFixtureTests` |
| S3 Generic progress analysis | `ProgressGraph`, `analyzeProgress`, `stronglyConnectedComponents`, `reverseReachable` | [progress-analysis-v1](../spec/progress-analysis-v1.md) | `ProgressAnalysisTests`, `MigrationFixtureTests` |
| S4 Qualified reduction | `EquivalenceReduction`, `ReductionDecision`, `validateDiamonds`, `compareReduction` | [reduction-v1](../spec/reduction-v1.md) | `ReductionTests` |
| S5 Continued-failure replay and shrinking | `recordContinued`, `replayContinued`, `encodeContinuedTrace`/`parseContinuedTrace`, `shrinkContinued` | [continued-failure-evidence-v1](../spec/continued-failure-evidence-v1.md) | `ContinuedEvidenceTests` |
| S6 Versioned qualification and packaging | `swiftExtensionProfiles`; capability matrix rows; external consumer; Kyoto driver | this document, [spec index](../spec/README.md) | `MigrationNegativeControlTests`, [`MigrationConsumer`](../swift/ExternalConsumers/MigrationConsumer), [`qualify.py`](../scripts/swift-migration/qualify.py) |

## Other engines

Each port implements all four contracts from the specs, consumes the unedited
`conformance/graph-search-v1.json` and `conformance/progress-analysis-v1.json`,
runs the whole `bfs-controls-v1` corpus through its existing kernel and its new
graph search with identical callback logs and counters, and ports the negative
controls and the review regressions. Each ran its full suite through Kyoto.

| Engine | PR | Entry points | Notable adaptations |
| --- | --- | --- | --- |
| Rust | #18 | `stateless::graph::explore_graph`, `progress`, `reduction`, `continued` | Identity from `Eq`/`Hash`; continued JSON carries no `RunConfig`; unsigned caps; borrowed event payloads |
| Python | #17 | `explore_graph`, `analyze_progress`, `compare_reduction`, `record_continued` | Identity from `hash`/`==`; `deepcopy` clones; no simplification budget |
| Go | #15 | `ExploreGraph`, `AnalyzeProgress`, `CompareReduction`, `RecordContinued` | `StreamEdgesOnly` (zero value retains edges); pointer optionals |
| TypeScript | #16 | `exploreGraph`, `analyzeProgress`, `compareReduction`, `recordContinued` | `bigint` 64-bit counters; nominal byte estimates |

`estimatedGraphBytes` is an engine estimate and differs between engines, as the
spec allows. Swift's `maxSimplificationCandidates` shrink budget exists only in
Swift. No shared runner protocol yet exercises one engine's evidence in another,
so these results are per-engine fixture conformance, not cross-implementation
qualification.

## Decisions recorded against the migration plan

- **Compatibility.** With default options `exploreGraph` runs exactly the
  bfs-controls-v1 callback sequence. The entire `bfs-controls-v1.json` corpus is
  replayed through both kernels, and the callback logs and counters are compared.
- **Admission is reported, not inferred.** Nodes carry expansion status
  (`complete`, `depthFrontier`, `failurePruned`, `interrupted`, `unexpanded`).
  Edges carry an explicit target, including rejected successors and their
  reason.
- **Coverage.** `exhaustive` is distinct from `bounded(depthCutoff,
  failurePruned)` and `incomplete(reason)`. Caps, cancellation, deadlines,
  retention and errors never yield `exhaustive`.
- **Finding retention.** Witness retention is bounded. Finding counts and failed
  property identities are not. Errors after partial evidence return the partial
  report.
- **Progress.** This preserves Illiquid's analyzer classifications, with
  "depth cutoff" generalized to any unmeasured node. Closed-component claims
  require every member to be measured. SCC uses an explicit stack.
- **Reduction.** Edges are still executed and checked. Collapse requires equal
  disposition, every check, projection bytes, and model equality of successors;
  a digest is never enough. Decisions and rejected equivalences are retained.
  Research-equivalence evidence does not authorize reduction.
- **Continued failures.** These use a distinct format
  (`stateless.continued-trace-json` v1) that old readers reject. `record`,
  `replay` and `shrink64` are unchanged. `shrinkContinued` reuses the shrink64
  driver, and success is determined by the selected target at the final
  sequence. Earlier failures are kept and never suppressed. Causal validity is
  unchanged (`isEnabled`).

## Qualification

Run `python3 scripts/swift-migration/qualify.py --label <label>`. It stages
`Package.swift`, `swift/`, `conformance/` and `docs/requirements/`, hashes every staged file, records
repository and Swift identity, and submits three serialized Kyoto jobs:

1. Build the external consumer against the staged package.
2. Run the consumer.
3. Run the staged package's full Swift test suite.

Receipts go to `validation/swift-migration/<label>/`.

Recorded runs. Each receipt hashes every staged file. r1–r3 staged the then
uncommitted tree on base `b99f4d6`. r4 staged commit `bc912fb`, which adds
spec wording changes found by an independent re-derivation of the fixtures from
the specs alone; that re-derivation matched all 19 cases.

| Label | Result | Cause |
| --- | --- | --- |
| `20261031-s1-s5` | consumer build failed | Consumer top-level globals violated Swift 6 strict concurrency; fixed in the consumer. |
| `20261031-s1-s5-r2` | consumer passed; 2 of 731 staged tests failed | Driver did not stage `docs/requirements/`, which pre-existing tests read; fixed in the driver. |
| `20261031-s1-s5-r3` | **passed** | Consumer built and ran (`migration-consumer: ok`); 731 staged tests, 3 skipped, 0 failures. |
| `20261031-s1-s5-r7-ci-fixes` | **passed** | Fixes for PR CI: the Swift 6.0.3 SIL ownership verifier crash in `exploreGraph`, reproduced locally with `-sil-verify-all` and clean after the fix; and fixture paths for relocated tests. macOS and Linux 6.0.3: 732 tests, 0 failures each; relocated run, 26 tests, 0 failures. |
| `20261031-s1-s5-r5` | **passed** | Commit `9615908` (fixes from the gpt-6.1-sol review: unresolved-edge counting, progress comparison): macOS consumer ok, 732 tests, 0 failures. |
| `20261031-s1-s5-r5-linux-6.0.3` | **passed** | `9615908` in `swift:6.0.3`: aarch64 and x86_64 (Rosetta), 732 tests, 0 failures each, consumer ok. |
| `20261031-s1-s5-r4` | **passed** | macOS arm64, Swift 6.3.3, commit `bc912fb`: consumer ok; 731 tests, 3 skipped, 0 failures. |
| `20261031-s1-s5-r4-linux-6.0.3` | **passed** | `swift:6.0.3` image via Kyoto. aarch64: 731 tests, 0 failures, consumer ok. x86_64 (Rosetta): 731 tests, 0 failures, consumer ok. QEMU user-mode x86_64 attempts crashed with SIGILL inside the compiler and runtime, also on unmodified `main`; they are kept as emulator diagnostics, not package evidence. |
| `20261031-s1-s5-r3-linux-6.0.3` | **passed** | Same staged bytes in the `swift:6.0.3` Linux aarch64 image (CI's pinned compiler) via Kyoto: 731 tests, 3 skipped, 0 failures; consumer built and ran. |

Distinct statuses: source review, compiled, executed tests, and external
consumer execution are recorded by the receipts. They do not establish
cross-language parity, Windows buildability, native (non-translated) x86_64
hardware, Illiquid integration, or release qualification. Illiquid's
remaining phases (model adapters, parity against the frozen explorer, CI
wiring, retirement) are Illiquid work. They consume a pinned revision of this
package.
