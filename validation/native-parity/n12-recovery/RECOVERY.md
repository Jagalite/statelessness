# N12 recovery

Recovery destination: `/Users/jagatranvo/.t3/worktrees/stateless/t3-ccd33c8a`, branch `t3/recover-n12-debugger-parity`.
Base fast-forwarded from `0876717bee85e335449c2c99ada41f6c6e6112d1` to `732578595b16052a8f922f122b41d7091258b476`.

The read-only source was `/Users/jagatranvo/.t3/worktrees/stateless/t3-3f30e40b` at the same base. `recover.py` captured `git diff --binary HEAD` and all regular paths from `git ls-files --others --exclude-standard -z`. Both tracked edits and all 225 regular untracked files were recovered. The first capture stabilized: before/after membership, type, mode, size and SHA-256 matched, and every destination byte hash matched. See `attempt-1/{before,after}.json`, `tracked.patch`, and `stable.json`. No symlink was followed. The old checkout and scheduler were not mutated.

Original job ownership remains with root. The recovered implementation document lists these handles, whose present status is deliberately not inferred here:

- Go full: `1c01a0031baf46a2bc5daedca3cc3799`
- Go telemetry full: `07c80ceb63f54c9bad8185fde6ac8f58`
- Go telemetry installed: `132cf41c17774c1dbf4fd8f1f34a2fbe`
- Python full: `65e66156719e4d70b07b9f634446b5f4`
- Python installed: `af5afbd560db4e279bc1e371ca7fcac2`
- TypeScript full: `73c41975844c4c0fbcbd4b4b688659be`
- TypeScript installed: `6407bd0e8b0c482f976603de65a0e370`
- Swift full: `30b7b8ee3f3840d0abe5c33ef6d3fb57`
- Swift installed: `39c3f6cabd8444b2bc580900591fff93`
- Rust compile only: `47fbcc6ce3c941bf8d74727338f51e08`

All historical source checkpoints, failed logs, deferred attempts, pending receipts and manual conformance fixtures were copied without reinterpretation. Historical passes do not qualify subsequent edits. New candidate source changes require distinct source-bound qualification; observation timeout does not authorize replacement of an original job.
