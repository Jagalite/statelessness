# Native finite research candidate receipts

The independently reviewed candidate implementation is committed as `cdc0a5a`;
its independent specification and expectations are committed as `0b9056d`.
Installed qualification is **pending**. Rust actual execution passed all 67 shared
cases; Python passed 275 tests with 12 ABI skips, Swift passed 147 tests,
TypeScript passed all 928 tests, and Go full tests and vet passed. Initial
Go/TypeScript failures remain preserved. All build/test work runs through Kyoto;
compilation alone is not an execution pass.

The frozen corpus contains 67 shared public Rust cases and 10 native-only
admission cases. These are authored expectations, not 77 Rust executions. Native
ports separately classify actual public failures and typed/language exclusions.
The known unsound history projection, exact callback order, ordered observables,
phase-specific shortest witnesses, limits, Unicode identity and qualification
strings are part of the finite contract. Production pruning stays disabled.

[summary.json](summary.json) identifies the pending jobs. Per-language directories
retain pre-submission source manifests and will retain exact terminal commands,
results and lossless raw logs. [source-inputs-candidate.json](source-inputs-candidate.json)
is an aggregate current snapshot captured after submission; it is not retroactive
pre-start provenance. Installed attempt r1 failed on a TypeScript application iterator declaration;
its source reconciliation and complete logs are preserved. The explicit local
iterator annotation fixes only application typing. The user cancelled r2 (`d2ea10c5d4d847d2b4105ed9df5642bf`) on October 20;
its terminal record and remaining Kyoto log are retained. Its temporary output
was unavailable on resumption. All 243 captured inputs and binaries matched
on October 22. Fresh r3 is queued as `85473e6a66014122bd427ec1ba2e6376`,
with durable output under `consumers/installed-r3/output`; no installed pass
is claimed.

This finite candidate does not promote a profile, change the 144 broad pending
qualification cells, prove arbitrary native heap/RSS bounds or callback
preemption, or establish general reduction/POR/liveness soundness. Platform,
registry, performance, broader N14 tooling and full parity gates remain open.
