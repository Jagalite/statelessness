# N12 service continuation: partial, not accepted

Base: `630feb56f381ce661ee1cdb26d9cb2b72b5ed9f5`.
Behavioral reference: Rust `0876717bee85e335449c2c99ada41f6c6e6112d1`.
This result DOES NOT complete the requested lane, N12, or full native parity.
No push, PR, merge, publication, or release was performed. The old checkout and
its three jobs were not modified or cancelled.

## Concrete source paths added

- Go and TypeScript `inputs`, `select`, `export_trace`; Swift `export_trace`.
  Candidate caches retain bounded owned canonical bytes and revision, decode
  on selection, revalidate the current domain, and retire on reconnect. Exact
  candidate bytes remain separately authorized. Export uses the session's real
  recorder and reports exact bytes, retained/evicted steps and capture outcome.
- Python `ProtocolSession.enable_watches` and `attach_probes`, with dedicated
  registry/hub state and approved profiles/sinks. Probe configuration preserves
  host-owned producer acknowledgements; event serialization preflights before
  queue removal. Watch capture follows committed simulation turns.
- Go `ProtocolSession.EnableWatches` and TypeScript `enableWatches`: real registry
  configuration, observation after delivery, status/current, and removal.
- Swift candidate protocol caching now follows encoded ownership/decode semantics.
- Python/TypeScript marker baseline semantics were reconciled to Rust; Python
  reports actual inspection-failure/baseline-eviction counters. Python exact
  export now includes the recorder counters missing from the previous handler.

All these are **source candidates**, not executed qualification claims. Modes are
local synchronous simulation/explicit out-of-domain injection. None grants effect
delivery, runtime producer acknowledgement, live control, restore, network access,
or cross-process synchronization. Optional module/package boundaries remain.

## Exact correspondence for touched public APIs

Reference file is `crates/statelessness-debug/src/protocol.rs` at the pinned SHA:

| Reference API / command | Native source correspondence |
| --- | --- |
| `Command::Inputs`, `Command::Select` | Go `debug/protocol_native.go:concrete`; TS `debug_protocol.ts:#concrete`, `debug_session.ts:encodeCandidate/stepCanonicalCandidate`; Swift `Protocol.swift:execute`, `Session.swift:stepCanonicalCandidate` |
| `Command::ExportTrace`, `TraceWindow` | Go `protocol_native.go:traceWindow/concrete`; TS `debug_session.ts:traceWindow` plus protocol fields; Swift `Session.swift:traceWindow` plus `Protocol.swift:execute`; Python `debugger_protocol.py:_TraceWindow/_execute` |
| `enable_watches`, `observe_watches`, `Watch/Unwatch/WatchStatus/WatchCurrent` | Python `debugger_protocol.py:enable_watches` and `debugger_protocol_services.py:WatchAttachment`; Go `protocol_watches.go:EnableWatches/watchCommand/observeWatches`; TS `debug_protocol.ts:enableWatches/#watch/#observeWatches` |
| `attach_probes`, `ProbeStatus/ProbeConfigure/ProbeEvents` | Python `debugger_protocol.py:attach_probes`, `debugger_protocol_services.py:ProbeProfile/ProbeAttachment` |
| `append_node` | Python `debugger_protocol_services.py:node_fields`; Go `protocol_watches.go:protocolNode`; TS `debug_protocol_services.ts:nodeProtocolFields` |

This maps source, not behavioral equivalence. Parent owns the global ledger.

## Authored checks and explicit non-results

- New Python service lifecycle tests cover real watches, projection failure after
  committed delivery, hub configuration/producer acknowledgement, authorized sink
  reads, open-turn busy, payload omission and profile bounds.
- New Go/TypeScript tests cover canonical candidate delivery and current-domain
  rejection; attached-watch tests check initial/effective boundaries, unchanged
  current observations, schema/type fields and removal. TS checks projection
  ownership. Swift/TS recording tests check exact trace header and direct ranges.
- Direct installed wire comparison adds three independent candidate scenarios
  beyond the previous 73 cases. Python/Swift hosts now actually enumerate inputs;
  the fixture provides model callbacks, not response reconstruction.
- `service_negative.py` removes producer-ack ownership in a copied Python package.
  Control must pass; mutant must hit the exact pending-producer assertion. Import,
  compilation, timeout and crash failures cannot count as detection.
- Interim Kyoto `05491d141aa74a17939711e9da054741` uses an immutable EARLIER copy
  at `/private/tmp/n12-81e57409-preflight`, with runner `/tmp/n12-preflight.py`.
  It was queued at last observation and cannot qualify the final source.
- Old preparation `6b80f419e40d4554bb0bf634dc18e1b3` FAILED its Go suite:
  `TestMetricsCatalogIsolationAndBoundedHistory` still expected `default` after
  the prior source changed default operation to `unspecified`. This continuation
  corrects that stale expectation; no new passing execution is inferred.

## Remaining work — implementation, not external blockers

> **Update 2026-10-31.** Items 1 and 2 are implemented in the working tree:
> Swift `enableWatches`, Go/TypeScript/Swift probe attachment, and Go/Swift
> observer-backed telemetry, plus Go sink health (`abandoned`) and Rust-order
> telemetry fields. Item 3 is deferred to the verification pass
> ([wire corpus plan](https://claude.ai/artifact/N4qigTcK8bzDn4i7d9RsGs)). Items 4 and 5 remain qualification work.

1. Swift watch registry attachment; Go/TypeScript/Swift probe hub attachment.
2. Go/Swift observer-backed metric/effect/telemetry protocol attachment.
3. Successful attached-service installed five-engine direct wire corpus with all
   requested failure/quota/capture/redaction/lifecycle controls. The new three
   candidate scenarios do NOT satisfy that requirement.
4. Complete exact node/error/service behavior reconciliation and final independent
   acceptance. Static review found blockers and has not granted lane acceptance.
5. Execute final-source suites, installed comparison and semantic mutants, retain
   full receipts, then resolve any failures and re-freeze/revalidate changed source.

Queue admission is only an execution constraint. It does not explain or excuse
these remaining implementable source gaps. This handoff is not fulfillment of the
request to finish the full implementation scope before returning.
