# Native causal evidence candidate receipts

This directory records the finite causal-evidence-v2 implementation slice on
macOS/arm64. Qualification is in progress; the Rust reference and Swift final
suite have passed, while remaining native suites and installed interoperability
are pending. This is not full native parity or profile promotion.

The shared contract is [causal-evidence-v2](../../../spec/causal-evidence-v2.md).
Its independently authored corpus has 52 wire, 16 replay/upgrade, 20 real
EvidenceRecorder and 13 diagnostic/export cases. The actual public Rust reference
executed all 101 cases, 3,150 truncations and 44 chunked reads. Separate Rust build,
execution and application-smoke receipts are under [rust](rust/README.md).
The Swift final suite executed 115 tests; its receipts are under
[swift](swift/README.md). Compilation alone is not reported as a test pass.

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
ledgers. The final installed matrix, source reconciliation and summary will be
added after the remaining gates execute.
