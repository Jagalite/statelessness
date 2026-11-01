# Illiquid migration: upstream Statelessness proposals S1–S6

Status of the upstream work requested by Illiquid's
`Documentation/STATELESSNESS_SWIFT_MIGRATION.md`. All of it is additive, opt-in
and Swift-only. Existing `bfs-v1`, `search-v1`, trace-json v1 and causal-evidence
v2 semantics are unchanged. No Rust or other native-port parity is claimed for
these extensions.

| Proposal | API | Contract | Tests |
| --- | --- | --- | --- |
| S1 Admitted graph and complete reporting | `exploreGraph`, `GraphSearchOptions`, `GraphSearchReport`, `GraphEvent`, `GraphRetention` | [graph-search-v1](../spec/graph-search-v1.md) | `GraphSearchTests`, `MigrationFixtureTests` |
| S2 Failure collection and result semantics | `FailurePolicy.collect`, `GraphFinding`, `PropertyFailureCount`, `GraphCoverage`, `GraphSearchError` | [graph-search-v1](../spec/graph-search-v1.md) | `GraphSearchTests`, `MigrationFixtureTests` |
| S3 Generic progress analysis | `ProgressGraph`, `analyzeProgress`, `stronglyConnectedComponents`, `reverseReachable` | [progress-analysis-v1](../spec/progress-analysis-v1.md) | `ProgressAnalysisTests`, `MigrationFixtureTests` |
| S4 Qualified reduction | `EquivalenceReduction`, `ReductionDecision`, `validateDiamonds`, `compareReduction` | [reduction-v1](../spec/reduction-v1.md) | `ReductionTests` |
| S5 Continued-failure replay and shrinking | `recordContinued`, `replayContinued`, `encodeContinuedTrace`/`parseContinuedTrace`, `shrinkContinued` | [continued-failure-evidence-v1](../spec/continued-failure-evidence-v1.md) | `ContinuedEvidenceTests` |
| S6 Versioned qualification and packaging | `swiftExtensionProfiles`; capability matrix rows; external consumer; Kyoto driver | this document, [spec index](../spec/README.md) | `MigrationNegativeControlTests`, [`MigrationConsumer`](../swift/ExternalConsumers/MigrationConsumer), [`qualify.py`](../scripts/swift-migration/qualify.py) |

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

Recorded runs (macOS arm64, Apple Swift 6.3.3, base `b99f4d6` plus the
uncommitted working tree, whose 278 staged files are hashed in each receipt):

| Label | Result | Cause |
| --- | --- | --- |
| `20261031-s1-s5` | consumer build failed | Consumer top-level globals violated Swift 6 strict concurrency; fixed in the consumer. |
| `20261031-s1-s5-r2` | consumer passed; 2 of 731 staged tests failed | Driver did not stage `docs/requirements/`, which pre-existing tests read; fixed in the driver. |
| `20261031-s1-s5-r3` | **passed** | Consumer built and ran (`migration-consumer: ok`); 731 staged tests, 3 skipped, 0 failures. |
| `20261031-s1-s5-r3-linux-6.0.3` | **passed** | Same staged bytes in the `swift:6.0.3` Linux aarch64 image (CI's pinned compiler) via Kyoto: 731 tests, 3 skipped, 0 failures; consumer built and ran. |

Distinct statuses: source review, compiled, executed tests, and external
consumer execution are recorded by the receipts. They do not establish
cross-language parity, x86_64 Linux or Windows buildability, Illiquid
integration, or release qualification. Illiquid's
remaining phases (model adapters, parity against the frozen explorer, CI
wiring, retirement) are Illiquid work. They consume a pinned revision of this
package.
