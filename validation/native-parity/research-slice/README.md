# Native finite research candidate receipts

The independently reviewed candidate implementation is committed as `cdc0a5a`;
its independent specification and expectations are committed as `0b9056d`.
Finite installed qualification **passed** in Kyoto job
`85473e6a66014122bd427ec1ba2e6376`: five real engine applications each matched
all 67 independent shared research cases. The four-profile portable baseline
passed 717 cases per engine and 550 fresh-process replays. All 27 commands
succeeded, and 28 complete logs are preserved losslessly with hashes.

The preceding native suites passed: Python 275 tests with 12 ABI skips, Swift
147 tests, TypeScript 928 tests, and Go full tests and vet. The actual Rust
reference passed all 67 shared cases. Compilation-only jobs are recorded separately.

The frozen corpus contains 67 shared public Rust cases and 10 native-only
admission cases. These are authored expectations, not 77 Rust executions. Native
ports separately classify actual public failures and typed/language exclusions.
The known unsound history projection, exact callback order, ordered observables,
phase-specific shortest witnesses, limits, Unicode identity and qualification
strings are part of the finite contract. Production pruning stays disabled.

[summary.json](summary.json) identifies exact jobs and scopes. Per-language
directories retain their source manifests, commands, results and logs.
[source-inputs-candidate.json](source-inputs-candidate.json) remains the historical
aggregate captured after submission; it is not retroactive pre-start provenance.

Installed r1 failed on a TypeScript application iterator annotation and retains
its full failure evidence. The user cancelled r2 on October 20; its temporary
output was unavailable on resumption, but the terminal record and remaining
Kyoto log are preserved. Fresh r3 ran from source commit `3232590`, with durable
[output](consumers/installed-r3/output/qualification.json). All 243 pre-submit
source/binary inputs matched after execution; the driver's 241 frozen source
files matched in both membership and hashes. Source commit `a4cc11e` adds only
reviewed guided/campaign documents after that execution snapshot. The
[source reconciliation](consumers/installed-r3/source-reconciliation.json)
records that distinction. Package and producer output hashes also matched.

This finite candidate does not promote a profile, change the 144 broad pending
qualification cells, prove arbitrary native heap/RSS bounds or callback
preemption, or establish general reduction/POR/liveness soundness. Platform,
registry, performance, broader N14 tooling and full parity gates remain open.
