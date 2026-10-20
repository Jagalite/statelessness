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

## Next work

1. Finish the semantic export and per-language API audit before closing M0.
2. Complete N02 installed-consumer and cross-language runtime qualification,
   preserving experimental status until promotion is explicitly reviewed.
3. Implement the [audited BFS controls/estimates/observers slice](native-parity/N01-CONTROLS-AUDIT.md),
   then append-only check storage and rich replay observations; retain distinct gates.
   Continue binary-v1 and N03/N04 qualification across platforms.
4. Continue M2–M6 against the plan; retain separate source, qualification and
   release states for every language and capability.
