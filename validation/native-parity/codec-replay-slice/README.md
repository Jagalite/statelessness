# Native bounded codec and replay candidate receipts

This finite macOS/arm64 slice passed through Kyoto. It implements independent
native bounded codec sinks, incremental adapter forwarding, rich/bounded replay
and direct full-audit recording. It does not close N01/N11, promote profiles,
publish packages, establish allocation/performance or process-RSS bounds, or
qualify other platforms, runtime floors or browser TypeScript.

Public Rust execution matched 46 independent encoding, 54 replay and 17 recording
vectors. Compilation and execution were separate Kyoto jobs. Final native suites
passed: Python 154 tests with 12 ABI opt-in skips, Go uncached tests and vet,
Swift 83 XCTest methods, and TypeScript compilation plus 421 tests. Swift also
passed the expanded 54-case replay corpus after its full suite. Native-only tests
cover revocable leases, sticky failures, synchronous return checks, ownership,
snapshot failure recovery and runtime arguments Rust's types cannot express.

`installed-r1/qualification.json` records packages built and installed outside the
checkout. All five applications use preferred incremental codecs while legacy
encoders fail if invoked. Complete typed/encoded rich and bounded observations
match independent threshold-model expectations. All 25 full-audit replay pairs
and 25 portable replay pairs passed in fresh processes. Producer configuration,
including truthful engine stamps, is retained without restamping. Buffer framing,
lease lifetime, artifact preflight before decoding, oversized actual recovery,
single reducer execution and divergence controls passed.
Python uses an isolated wheel installation, TypeScript an installed npm tarball,
and Go/Swift staged source dependencies. This is not registry qualification.

The unchanged four-profile baseline passed across five engine families: 717
cases per engine (94 golden, 530 generated graphs, 32 metamorphic, 38 portability,
23 transport) plus 550 fresh-process replays, with zero failures.

`summary.json`, `source-inputs-final.json`, package/executable hashes and Kyoto
receipts identify scope and bytes. All installed runtime, fixture, example,
script and reference source hashes matched the final checkout. Documentation and
receipt updates followed staging. All 118 reviewed ledger hashes match; broader
qualification cells remain pending. `review.md` summarizes independent review.

Historical failures remain distinct from final evidence: a Go quota-fixture typo;
a TypeScript module initialization cycle; Rust borrowed check-ID compilation;
and missing initial-quota error context found by the expanded corpus in three
languages. An accidental Go submission from the repository root is retained as
a wrong-directory diagnostic. Intermediate passes do not qualify later edits.
The record-max_steps=0 golden was corrected by inspecting Rust's StepLimit rule
before public-reference execution. Native error text containing invalid Unicode
is escaped only when capturing new diagnostics; supplied binary artifacts stay
strict.

Raw diagnostic logs containing trailing whitespace are stored losslessly as
`.log.gz`; `compressed-logs.json` records original sizes and SHA-256 values.
Rust's smoke driver names an absolute worktree executable despite its queue
submission directory. The installed matrix separately pins and copies that
executable. Existing portable Rust runner bytes are retained separately.
