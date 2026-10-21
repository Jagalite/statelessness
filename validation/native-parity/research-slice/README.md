# Native finite research candidate receipts

The independently reviewed candidate implementation is committed as `cdc0a5a`;
its independent specification and expectations are committed as `0b9056d`.
Runtime qualification is **pending**. All requested build/test work is submitted
through Kyoto; compilation alone is not an execution pass.

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
pre-start provenance. Installed-package qualification will be submitted only
after native suites and the real Rust reference pass.

This finite candidate does not promote a profile, change the 144 broad pending
qualification cells, prove arbitrary native heap/RSS bounds or callback
preemption, or establish general reduction/POR/liveness soundness. Platform,
registry, performance, broader N14 tooling and full parity gates remain open.
