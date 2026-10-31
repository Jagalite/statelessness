# N12 optional native implementation candidate

Work starts at local commit `7325785` in isolated branch `t3/3f30e40b`.
The source audit and independent audit review remain the contract map; they are
not implementation qualification. No capability/profile promotion or closure of
the 144 broad qualification cells follows from this work.

## Execution status at the source checkpoint

The current implementation is a reviewable candidate, **not a qualified N12
completion**. Shared Kyoto admission is blocked by `OS memory pressure: warning`
with effective new-job concurrency zero. No queue policy was changed. The first
Rust compile job was automatically deferred three times; its attempts and terminal
cancellation are retained. A replacement uses one compiler job and disabled dev
debug information in its own target directory, and remains queued.

Intermediate executed passes are retained: Go full tests
`17f84d9dc8234598abc609ea7ce43ce2`, Python 260 tests/12 ABI skips
`c3c40ca7e4024d32aede69de17c24090`, TypeScript 845 tests
`1537790040f541babaa122fd2279ec01`, and Swift 144 tests
`4f8647ce29b74caabf17a1dcd3b101c7`. These preceded later implementation and review
fixes and **do not qualify the checkpoint's final source**. Setup, fixture,
compilation and shared-write failures are preserved alongside successful runs.

Current full/installed jobs include:

| Scope | Kyoto job |
| --- | --- |
| Go frozen full tests/vet/copied-module consumer | `1c01a0031baf46a2bc5daedca3cc3799` |
| Go telemetry full tests | `07c80ceb63f54c9bad8185fde6ac8f58` |
| Go telemetry installed consumer | `132cf41c17774c1dbf4fd8f1f34a2fbe` |
| Python full baseline | `65e66156719e4d70b07b9f634446b5f4` |
| Python frozen wheel/fresh-process tests | `af5afbd560db4e279bc1e371ca7fcac2` |
| TypeScript full build/tests | `73c41975844c4c0fbcbd4b4b688659be` |
| TypeScript installed consumer | `6407bd0e8b0c482f976603de65a0e370` |
| Swift full suite | `30b7b8ee3f3840d0abe5c33ef6d3fb57` |
| Swift frozen copied-package consumer | `39c3f6cabd8444b2bc580900591fff93` |
| Rust reference compilation only | `47fbcc6ce3c941bf8d74727338f51e08` |

Their actual results, source identities and any required replacement commands
must be inspected before integration. TypeScript's installed consumer must follow
a successful matching build, with the final frozen `--qualify` consumer run still
required. Rust requires separate execution of the matching compiled reference
binary; no Rust public-reference assertion has run at this checkpoint. Real
cross-language protocol comparison remains gated on that reference execution.

`scripts/native-parity/collect-n12-receipts.py` inventories this worktree's job
states and source hashes without changing scheduling. Its inventory is metadata,
not proof that model assertions passed.

Native implementations live in optional packages/modules:

- Go: `github.com/Jagalite/statelessness/go/debug`.
- Python: explicit `statelessness.debugger*` modules.
- TypeScript: explicit `@jagalite/statelessness/debug*` package entries.
- Swift: separate SwiftPM `StatelessDebug` product depending on `Statelessness`.

Core engine imports do not install a transport, start a clock, register a watcher
or dispatch host effects. Exact stored evidence can contain secrets. Redacted
inspection and exact evidence export remain different authorities.

## Review boundaries

Independent expectations were written before executing the real Rust reference
in `conformance/debugger-diagnostics-v1.json` and
`validation/native-parity/debugger-diagnostics/reference/manual-goldens.json`.
The reference uses actual public Rust APIs in
`crates/statelessness-debug/tests/n12_reference.rs`; compilation alone is not
execution evidence.

Source review specifically checks:

- Structural v1 reads versus semantic recording validation; unknown noninitial
  v1 phases versus persisted v2 state/transition splits.
- Separate v2 phase replay, exact workbench replay and original shrink validation,
  all charged to the same overall transition/deadline allowance.
- Restoring each candidate from the retained checkpoint, applying environmental
  admission before Generate causality, and bounded enumeration with one lookahead
  only when no permitted candidate has matched.
- `new` initializer ordering, one-pass checked-token recording, counter behavior
  for ignored/rejected turns, pre-breakpoint retry and observer-after-commit.
- Reentrant mutation through iterators, model clone hooks and observers; stale
  revisions, privately bound candidate/permit authority and failed-boundary forks.
- Full-width quantities, explicit incomplete/unknown projections, redaction that
  skips evaluation, and missing/invalid clock endpoints distinct from observed zero.

Arbitrary callbacks remain cooperative. There is no RSS bound, callback
preemption, OS resource restoration, LLDB/GDB integration, remote authentication,
new engine default or inferred historical output decoder.

## Evidence policy

Every build, test, benchmark and Rust reference execution is submitted through
the shared Kyoto queue from this isolated worktree. Effective commands and
policy are retained with outcomes; Cargo `--no-run` proves compilation only.
Failed and superseded raw logs are retained losslessly with byte sizes and
SHA-256 hashes, rather than replaced by successful reruns.

Language agents retain finite focused/full-baseline and installed fresh-process
receipts under `validation/native-parity/debugger-diagnostics/` (Go also uses
`validation/native-parity/n12-go/`). A queued, deferred, skipped, compile-only or
failed command is never an executed pass. Source changes after a pass require
new qualification of the affected scope. Installed candidate proof is distinct
from a registry release or platform-wide qualification.
