# Native checked-result and runtime recorder receipts

This finite macOS/arm64 candidate slice passed through Kyoto. All four independent
native engines implement sealed check-once initial/turn results and a real runtime
recorder for application-executed transitions. This does not close broad native
parity, promote profiles, publish packages, qualify other platforms or establish
allocation/performance/RSS bounds. Causal EvidenceRecorder and v2 envelopes remain
separate work.

The public Rust reference matched 27 independent token and 36 Recorder vectors,
including real distinct state/input/output types sharing metadata and canonical
bytes. Native suites consume those same cases. Final Kyoto suites passed:
Python ran 180 tests with 12 ABI opt-in skips, Go uncached tests and vet passed,
Swift passed 101 XCTest methods, and TypeScript compilation plus 500 tests passed.
Rust compilation and execution are separate receipts; no compile-only test pass
is inferred.

`installed-r1/qualification.json` identifies five producers and 79 commands.
The main application executes its three reducers itself, with zero initial-state
calls, four state checks and three transition checks. Checked initialization and
handoff add no reducer/checker calls. All 25 audit replay pairs passed complete
rich and bounded observations, and all 25 portable replay pairs passed in fresh
processes. Additional ring artifacts replay from their promoted checkpoint,
retaining only evicted state-check prefixes and truthful local/origin counters.

Controls cover token reuse across two recorders, full-policy requirements,
duplicate/gapped sequences, origin attachment, native accessor/diagnostic
ownership, bounded-error coherent prefixes and no repeated checking on failed
handoff. The installed Swift compiler accepts an authentic token in the same
module-search context, then specifically rejects conversion from TurnObservation
to CheckedTurn. An unrelated compiler failure cannot satisfy the negative gate.

The unchanged four-profile baseline passed 717 cases per engine (94 golden,
530 generated graphs, 32 metamorphic, 38 portability, 23 transport), plus 550
fresh-process replays across five engine families, with zero failures. Python uses
an isolated local wheel, TypeScript an installed npm tarball, and Go/Swift staged
source dependencies. This is not registry or cross-platform qualification.

`summary.json`, `source-inputs-final.json`, package/executable hashes and Kyoto
receipts identify exact source and commands. Qualified runtime, fixture, example,
driver and reference hashes match the final checkout; documentation and receipts
were finalized after staging. All 127 reviewed ledger hashes match. The broader
144 qualification cells remain pending. `review.md` records the independent
findings and native ownership boundaries.

Representative intermediate failures are retained separately: Go's io.WriterTo
signature convention and new TypeScript quota error-category expectations. Their
subsequent fixes were re-executed in final full suites. Earlier fixture drafts
incorrectly used a zero-sequence token in a handoff case; token construction
rejects zero, so the case now uses a real duplicate token after valid turns.
The separate zero-sequence constructor negative remains. No partial token or
fabricated certificate is used to reach a recorder failure.

Logs containing trailing whitespace are stored losslessly as `.log.gz`, with
original sizes and SHA-256 values in `compressed-logs.json`. Native snapshots,
clone callbacks, legacy codecs and application callbacks are outside logical
wire-budget guarantees. Generic Swift payloads remain synchronously owned; no
new Sendable guarantee is established. Clone fidelity and model purity remain
application obligations, and private token state is not a hostile-reflection
security boundary.
