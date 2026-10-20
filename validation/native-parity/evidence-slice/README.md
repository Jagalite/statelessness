# Native causal evidence candidate receipts

This directory records the finite causal-evidence-v2 implementation slice on
macOS/arm64. All four native suites, the Rust reference and the installed
interoperability matrix passed. This is not full native parity or profile promotion.

The shared contract is [causal-evidence-v2](../../../spec/causal-evidence-v2.md).
Its independently authored corpus has 52 wire, 16 replay/upgrade, 20 real
EvidenceRecorder and 13 diagnostic/export cases. The actual public Rust reference
executed all 101 cases, 3,150 truncations and 44 chunked reads. Separate Rust build,
execution and application-smoke receipts are under [rust](rust/README.md).
Final native receipts record Go uncached tests and vet, Python's 216 tests with
12 ABI opt-in skips, TypeScript compilation and 627 tests, and Swift's 115 tests.
The language directories retain exact commands, terminal receipts and source
reconciliation. Compilation alone is not reported as a test pass.

The scope includes exact binary-v2 envelopes, structural v1 reading without model
execution, phase-aware replay, explicit verified upgrade, coordinated trace and
causal journal retention, bounded redacted diagnostics and exclusive private-file
exports. Provenance and causal labels are host assertions. CRC is not
authentication. Logical Rust64 decoder weights are not native heap or RSS
measurements; callback work, fatal OOM, other platforms, browser execution,
registry distribution, debugger integration and reusable canonical value-codec
APIs remain outside this qualification.

Independent review findings and reviewed source identities are retained beside
the language receipts. Failed intermediate runs remain distinguishable from final
source runs. Compressed raw logs preserve original bytes, with SHA-256 and size
ledgers. The [installed-r3](installed-r3) archive records 137 commands, including
ten expected existing-file failures. Five producers passed all 25 ordered pairs
each for exact v2 rewrite/phase replay, verified upgrade, redacted private
diagnostic export and passive v1 reading. The unchanged portable baseline passed
717 cases per engine and 550 fresh-process replays, with zero failures. Local
wheel/npm installation and staged Go/Swift dependencies establish this host's
package boundary; registry, browser and other-platform qualification remain open.

`source-inputs-final.json` reconciles 200 package/runtime/application/reference
inputs and all 171 static ledger hashes with the final checkout. `summary.json`
records exact jobs and counts. All 144 broader qualification cells remain pending.
Installed attempts r1 and r2 are retained:
the first exposed a Swift example helper-name collision; the second exposed a
driver assumption that Rust's producer writes an optional diagnostic sidecar.
The successful fresh r3 run retains all explicit diagnostic exports and overwrite
controls. Its complete logs are losslessly compressed with original/compressed
hash and size ledgers; no intermediate pass is substituted for final execution.
