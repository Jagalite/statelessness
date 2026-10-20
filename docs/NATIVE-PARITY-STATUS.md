# Native parity implementation status

Work began on 2026-10-20 on `docs/native-language-parity`, extending the
[native parity plan](NATIVE-PARITY-PLAN.md) from its pinned Rust baseline
`0876717bee85e335449c2c99ada41f6c6e6112d1`. The first slice bootstraps M0 and adds
the missing Go and Swift candidate oracle APIs from N02. Full native parity,
profile promotion, package publication and performance qualification remain open.

## Source delivered in the first slice

- [Rust inventory](native-parity/INVENTORY.md): 3,327 lexical candidates across
  59 pinned core, macro and debugger source files. Every candidate has a locator,
  provisional N01–N14 mapping and separate language audit cells. The checker rejects
  drift and is integrated into the standard qualifier. This is a source accounting
  aid; semantic exports, reexports, generated APIs, enum variants and per-language
  mappings still require review. **M0 is not complete.**
- [Independent Go oracle adapter](../go/oracle.go) and
  [paired reference adapter](../go/paired.go): explicit independently retained
  history, combined equality, ordered checks/effects, complete dispositions,
  retained observed transitions on callback failure, structured semantic identities
  and ORC1 checkpoint framing. It continues to work without cgo.
- [Independent Swift adapters](../swift/Sources/Statelessness/Oracle.swift): the
  corresponding native model/oracle/projection APIs, exact text identities and
  explicit clone callbacks. Observation errors retain application transitions;
  their generic reference payloads remain confined to synchronous caller ownership.
- [Go regressions](../go/oracle_test.go) and
  [Swift regressions](../swift/Tests/StatelessnessTests/OracleTests.swift) consume
  the same 11 independently authored queue cases as Python, TypeScript and Rust.
  Additional cases cover history equality, errors, snapshots, framing, exact replay
  and semantic identity. Relocated package qualification explicitly supplies the
  shared fixture path through `STATELESS_PAIRED_CORPUS`.

Existing Python and TypeScript candidate APIs are retained. The four established
portable profiles and runner protocol remain unchanged. These oracle APIs are
experimental additions; implementation and passing local tests do not promote
`paired-v1` or establish full N02 qualification.

## Qualification

All build and test commands are submitted through the shared Kyoto queue. Cargo
policy may convert requested tests to `--no-run`; its effective command and policy
message must accompany any result. Memory-pressure admission holds are queue
states, not test failures or passes. Current execution receipts and source hashes
are recorded in the [first-slice receipts](../validation/native-parity/first-slice/README.md).
The first-slice checks have now executed: Go tests, 42 Swift tests, 47 TypeScript
tests, inventory checks, 18 qualifier tests, and the Python run (50 tests, 12 ABI-v2
opt-in skips) passed. Kyoto compiled the Rust paired suite under `--no-run`;
separate execution of its produced binary passed all four tests. The original
Swift test-compilation failure remains preserved. These results identify the
first-slice source snapshot and do not qualify later search additions automatically.

Remaining gates include independent candidate-specification review, the complete
installed-consumer/platform matrix, additional profile-specific mutation controls,
fresh-process candidate interoperability and measured performance. Existing
four-profile cross-engine results cannot qualify the new candidate implicitly.

## Native search candidate source

The next slice implements N03/N04 candidate APIs across all four native engines:
automatic finite generation with transactional RNG state, explicit generators,
seeded mutation fuzzing, cooperative run controls, failure-preserving causal
shrinking and bounded attempt/replay reports. Search observers receive detached
initial/check/transition evidence, including the failing transition and actual
executed prefix. Oracle adapters forward generation over actual model state while
retaining independent history. Python and TypeScript wrappers now forward checks
incrementally so later errors do not discard earlier observations.

The [search contract](../spec/search-v1.md) has 25 independently derived
[golden cases](../conformance/search-v1.tsv) and
[exact shrink histories](../conformance/search-v1-history.json). The
[Rust reference test](../tests/native_search_profile.rs) invokes actual public Rust
algorithms against those expectations. Candidate infrastructure checks passed;
the expanded Python suite passed 74 tests with 12 ABI-v2 skips, Go tests and vet
passed, Swift passed 53 tests, and TypeScript passed all 92 tests. Rust compilation
through Kyoto and separate execution passed all 25 shared golden cases and exact
shrink histories. A TypeScript nullable fixture had inherited an incompatible
numeric membership hook; correcting that fixture required no engine change. Historical intermediate failures and
pre-observer passes are retained separately from final-source receipts.

[Search-slice receipts](../validation/native-parity/search-slice/summary.json)
record source inputs and Kyoto commands. An installed/fresh-process application
qualification driver now stages all four packages outside the checkout and checks
16 ordered producer/replayer combinations. All four applications independently
found six-step failing witnesses and reduced them to three steps in 15 attempts.
Both original and minimized artifacts were retained, and all 32 fresh-process
artifact replays passed. This exercises actual generation, fuzzing, shrinking,
recording and exact replay through installed or staged public packages.

Static reviews found and fixed transactional input-domain issues, TypeScript
receiver/proxy edge cases, incomplete observer-error notification, and lost partial
check evidence. Remaining scope includes native eager-return partial-check limits,
cooperative Auto scans, potentially infinite user simplification streams, richer
BFS/replay controls and state-size estimates. Read the contract before interpreting
a bounded search report as a total callback-work or process-memory bound.

## Compiler-resolved API accounting

The [semantic inventory](native-parity/semantic-README.md) uses actual nightly
rustdoc JSON for the core, macro and debugger crates. All 5,864 reachable
declaration/member/source-backed implementation records map to N01–N14, with
14,106 raw compiler index items accounted and no unresolved graph cases for the
recorded native/default target. Parser tests and source/artifact identity checks
passed through Kyoto. This enumerates the compiled surface; individual native
equivalence and other targets/features remain open.

## Native binary trace candidate

The [binary-v1 candidate contract](../spec/native-binary-v1.md) and
[independent 54-case corpus](../conformance/native-binary-v1.json) specify the
actual Rust audit format, configuration preservation, strict decoding and quotas.
The native engines now add separate lossless audit traces with explicit run
configuration, ordered duplicate parameters, exact seeds, strict UTF-8 and CRC32,
and all nine independent read/write limits. Portable conversions return omitted
configuration alongside the narrower trace. Writers preflight the complete trace
before first publication; readers reject malformed framing and trailing bytes.

The final native suites passed through Kyoto: Python ran 85 tests with 12 ABI
skips, Go passed all packages with cgo disabled, Swift passed 59 tests and
TypeScript passed 154 tests. The real Rust reference tool passed all 54 cases,
2,565 proper-prefix truncations, chunked reads, I/O failure and writer preflight
checks. The installed five-engine matrix passed 500 exact fixture roundtrips,
25 fresh-record roundtrips, 25 semantic replays and 10 negative replay controls.
Every replay retained the complete producer configuration, including Rust engine
version/build stamps. A first application comparison mistakenly expected those
stamps to match an unstamped static golden; that failed attempt is preserved.
See the [binary-slice receipts](../validation/native-parity/binary-slice/README.md)
for source/package identities and exact scope. Binary v2 causal evidence, direct
recorder retention/capture policy and the remaining N11 gates are still open.

## BFS control and estimate candidate

The [BFS candidate contract](../spec/bfs-controls-v1.md) now has 47 independently
specified report/callback vectors verified against real public Rust execution.
All four native engines add cooperative controls, nullable retained-state-byte
reports, byte admission caps, newest-first collision comparisons and BFS
observations with optional detached root-to-edge histories. Initial and completed
turn failures retain precedence over control cutoffs; duplicate and unadmitted
edges are still checked and observed. Oracle estimates forward with explicit
wrapper accounting: native inline padding in Go/Swift and caller-declared logical
overhead in Python/TypeScript. Estimates remain application estimates, not RSS.

TypeScript adds exact BigInt BFS budgets/counters and keeps checked Number
compatibility. Skipped-check counts saturate at Rust's u64 maximum. Review also
fixed caller-owned budget aliases across shared Go/TypeScript controls and search
operations; budgets are captured at entry while cancellation state stays live.
The four promoted profiles and their runner response schema remain unchanged.

Final Kyoto runs passed: Python 103 tests with 12 ABI skips, Go uncached tests and
vet, Swift 66 tests, and TypeScript 216 tests. All five installed applications
matched base/cap/pre-cancel counters, failure targets and observer histories;
all 25 fresh-process witness replay pairs passed. The unchanged four-profile
baseline passed across five independent engines, including 717 cases per engine
and 550 fresh-process replays. [BFS receipts](../validation/native-parity/bfs-slice/README.md)
record exact source/package/runtime identities and historical failures.

Append-only check storage is implemented in the following slice; checked-result
handoffs and rich/bounded replay remain open. The [append-check audit](native-parity/APPEND-CHECKS-AUDIT.md)
and earlier [controls audit](native-parity/N01-CONTROLS-AUDIT.md) retain the
source-pinned rationale for these implementations.

## Append-only check storage candidate

All four native engines now implement the [append-check candidate](../spec/append-checks-v1.md).
Optional append callbacks take precedence over legacy collection callbacks and
feed reusable engine storage across observed checking, BFS, fuzz, shrink,
recording, replay and adapters. Transition hooks inspect the preceding state
checks; oracle/reference hooks inspect preceding actual-model checks. Search
errors preserve real partial diagnostic batches, while observed-check-into clears
caller storage on error without executing the reducer again.

Native callback sinks use revocable nested leases, shared sticky validation,
detached check values and inspection snapshots, and synchronous-return guards.
Expired handles release their storage references. These are deliberate native
counterparts to Rust's static borrowing; contained exception cleanup is stronger
than Rust's uncaught-unwind behavior. Raw caller buffers remain exclusively owned
during a call. Built-in adapters now provide append hooks, so customizations must
respect append-before-legacy priority; package READMEs document override migration.

The real Rust reference passed all 52 shared cases. Final Kyoto suites passed:
Python ran 124 tests with 12 ABI skips, Go ran uncached tests, Swift passed 73
methods, and TypeScript passed 281 tests. All five installed pipelines used zero
legacy callbacks and matched Rust's append callback counts through BFS, fuzz,
shrink, recording and replay. All 25 fresh witness replay pairs passed. The
unchanged four-profile baseline passed 717 cases per engine and 550 fresh-process
replays across five engines. [Append receipts](../validation/native-parity/append-slice/README.md)
identify exact source/package/runtime bytes and preserve historical results.

Review also fixed Python binary variant spoofing and quota-before-copy behavior
for string subclasses; its 54 binary goldens remain intact. Bounded codec buffers
and richer/bounded replay remain open; their [source audit](native-parity/ENCODE-BUFFERS-REPLAY-AUDIT.md)
sets the next dependency and qualification scope. Append sinks alone do not close
N01, binary recorder admission or allocation/performance parity.

## Bounded encoding, rich replay and audit recording candidate

All four independent native engines now expose the
[bounded encoding](../spec/encode-buffers-v1.md) and
[rich replay/audit recording](../spec/replay-evidence-v1.md) candidate APIs.
`EncodeBuffer` checks accepted length before growth, retains sticky failure,
frames nested components under one budget, and prefers incremental codecs exactly
once. Oracle model/history encoding shares that budget and preserves ORC1 bytes.
Legacy allocating codecs remain compatible and can allocate before admission.

Rich replay exposes all four divergence flags, typed actual results, unknown
versus completed check batches, primary and secondary observer errors, and
first-divergence stopping. Bounded replay validates the stored artifact before
model decoding, then independently charges actual evidence against shared frame,
aggregate-byte and item quotas. Direct audit recording retains explicit
`RunConfig`, native engine provenance, coherent committed prefixes and reserved
terminal-error headroom. A per-blob sink alone would not supply these contracts.

Final full Kyoto suites passed: Go tests and vet, Python 154 tests with 12 ABI
skips, Swift 83 methods and TypeScript 421 tests. Public Rust reference runs passed
46 encoding, 54 replay and 17 recording cases; the native suites consume the same
cases. Swift's expanded replay corpus passed separately after its full suite.
The installed five-engine matrix passed all 25 full-audit and 25 portable witness
replay pairs, including complete rich/bounded observations, retained producer
configuration and negative controls. The unchanged four-profile baseline passed
717 cases per engine and 550 fresh-process replays with no failures.
[Codec/replay receipts](../validation/native-parity/codec-replay-slice/README.md)
pin source/package/runtime identities and preserve intermediate failures. These scoped results do
not establish broad native parity or platform qualification. The capability
ledger retains all qualification-pending cells and pins only reviewed source.

Native revocable leases and detached observations replace Rust's synchronous
borrows; recovery transfers an already produced actual result when a snapshot
fails instead of rerunning the model. Generic Swift recovery errors remain
synchronous/actor-confined despite their unchecked `Sendable` conformance. Logical
wire limits exclude callbacks, legacy codec allocation, snapshots, backing
capacity and process RSS; process OOM/traps are not uniformly recoverable.

The four promoted profiles remain unchanged. Checked provenance handoffs are the
[next scoped dependency](native-parity/CHECKED-RESULTS-AUDIT.md). Canonical value
codecs, binary v2 causal evidence, debugger integration and broad allocation,
performance and platform qualification remain separate work.

## Checked results and runtime recording candidate

The [checked-results candidate](../spec/checked-results-v1.md) adds sealed
`CheckedInitial`/`CheckedTurn` handoffs and a runtime `Recorder` in all four native
engines. Supplied checkpoints and already executed turns are checked without
calling initialization or the reducer. Complete batches retain order, phase
boundary, exact sequence and full-checking coverage; property failures can produce
tokens, while checker/snapshot errors cannot. Detached diagnostics do not authorize
recording or change a token's certified context.

The runtime recorder consumes supplied or checked context, verifies metadata,
full coverage, sequence and canonical before-state continuity, and admits complete
prepared evidence into a contiguous bounded window. Eviction promotes only the
state-check prefix into checkpoint checks. Retained-byte accounting covers the
whole wire artifact, prospective counter metadata and terminal footer; admission
errors freeze the preceding coherent prefix. Same-metadata handoffs may use a
different state/input/output representation when canonical bytes agree.

Final native Kyoto suites passed after ownership, quota-before-copy, reentry and
transfer fixes: Go uncached tests and vet; Python 180 tests with 12 ABI skips;
TypeScript 500 tests; Swift 101 methods. Actual public Rust execution passed all
27 token and 36 recorder cases plus the application smoke. Independent reviews
closed. The installed matrix passed across all five producers: 25 binary audit and
25 JSON fresh-process replay pairs, full/main and retained-ring rich sessions,
Swift positive/negative compiler cases, and the unchanged baseline of 717 cases
per engine plus 550 fresh replays. [Checked-slice receipts](../validation/native-parity/checked-slice/README.md)
pin these finite results; they do not establish broad native parity. The ledger
pins the reviewed frozen source and retains all 144 qualification-pending cells.

Go exposes heterogeneous handoffs through generic `ObserveRecorder` and
`ObserveCheckedRecorder` free functions over a representation-independent
`Recorder`. Swift's `intoTrace` logically consumes mutation/transfer authority:
subsequent writes and a second transfer fail, while read-only snapshots and
counters remain available as a documented native extension. Private native token
storage depends on correct clone hooks and application purity; it is not a
reflection/security sandbox or Rust's static borrow system.

The four promoted profiles remain unchanged. Checked results and runtime recording
alone do not establish causal evidence; the next candidate slice is described below.
Wire quotas do not bound application allocation, snapshots, backing capacity or
RSS, and continuity cannot detect an omitted turn returning to the same state.

## Causal evidence candidate

The [causal-evidence-v2 candidate](../spec/causal-evidence-v2.md) adds native
`Provenance`, causal output origins, persisted check-phase boundaries, `TraceV2`
and version-preserving `Artifact` APIs. Source is present in Python
[`evidence.py`](../python/src/statelessness/evidence.py), Go
[`evidence.go`](../go/evidence.go), TypeScript
[`evidence.ts`](../typescript/src/evidence.ts), and Swift
[`Evidence.swift`](../swift/Sources/Statelessness/Evidence.swift). Exact v2 envelopes
embed unchanged v1 artifacts; passive v1 reading leaves later check phases unknown.
Explicit verified upgrade executes bounded replay to derive phase boundaries.

Native `EvidenceRecorder` implementations coordinate causal metadata retention with
the runtime recorder's actual trace evictions. Context validation uses the current
retained window before any planned eviction, and metadata commits only after trace
admission. Diagnostic exports omit payloads and host labels, include only exactly
allowlisted property IDs, and apply whole-output byte caps. Filesystem exports
create new private files exclusively and sync them; TypeScript exposes that host
operation through its separate Node adapter.

The root-authorized frozen source is now pinned in the capability ledger, with
all four causal-evidence implementations mapped as implemented. Root reports the
Rust reference passed 101 causal cases, 3,150 truncation prefixes, 44 chunked
reads and application smoke. Final native Kyoto suites passed: Go tests and vet,
Python 216 tests with 12 ABI skips, TypeScript 627 tests, and Swift 115 methods.
The installed macOS/arm64 matrix passed across five producers: all 25 v2
rewrite/phase-replay pairs, 25 explicit upgrades, 25 private exact diagnostic
comparisons, 25 passive-v1 pairs and ten sentinel negative checks, plus the
unchanged baseline. The [installed receipts](../validation/native-parity/evidence-slice/installed-r3)
retain this finite proof. Failed first and second attempts remain preserved; they
exposed Swift helper naming and optional diagnostic-file validation defects in
the consumer harness. All 144 broad qualification-pending cells and the four
promoted profiles remain unchanged. Registry, browser and broader platform
qualification remain open. The finite candidate work does not establish
general canonical value-codec APIs, debugger integration, cross-platform/browser
qualification, hard RSS bounds or complete N05/N11/native parity. Metadata decoder
allocation accounting uses the specified Rust64 logical schema weights rather
than managed-runtime heap sizes. CRC and causal/provenance labels provide no
authentication. New-file exports provide neither rename-atomic publication nor
parent-directory fsync durability.

## Next work

1. Finish the semantic export and per-language API audit before closing M0.
2. Complete N02 installed-consumer and cross-language runtime qualification,
   preserving experimental status until promotion is explicitly reviewed.
3. Finish the causal-evidence candidate's final source, installed-consumer and
   interoperability qualification against the [scoped audit](native-parity/CAUSAL-EVIDENCE-AUDIT.md).
   Continue append/BFS/binary-v1 and N03/N04 qualification across platforms.
4. Continue M2–M6 against the plan; retain separate source, qualification and
   release states for every language and capability.
