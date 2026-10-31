# Changelog

## Unreleased source improvements

These changes are newer than the published 0.2.0 artifacts. Rust compilation is
permitted locally; runtime and performance qualification must have their own
current-source receipts. Independent requirements review remains pending.

- Add optional assurance catalogs, coverage observations and fail-closed adequacy
  reports integrated with the existing search engines and campaigns.
- Add an application-owned controller, ordered background effect host, independent
  requirement catalog, seeded fault controls, retained regression cases and fresh
  process replay gates; add explicit requirement/domain/bound review reports.
- Add explicit multi-machine scheduling, bounded lifecycle retention and tests for
  stale, duplicate, cancellation and eviction boundaries.
- Add a versioned causal evidence envelope preserving exact v1 traces, phase-aware
  replay/minimization, bounded redacted diagnostics, and passive metric reports.
- Add macro defaults, bounded generation, typed property metadata, opt-in codec
  adapters and expanded renamed-consumer/compile-fail fixtures.
- Add candidate native paired/oracle APIs in Python and TypeScript with a shared
  Rust/Python/TypeScript corpus. Keep the established portable profiles unchanged.
- Extend source-bound qualification and benchmark fixtures. Retain conservative
  production storage/equality defaults until measured experiments qualify a change.
- Bound consecutive already-seen shrink hints per simplifier iterator, so an
  endless hint stream ends with `attempt_limit` while finite streams are
  unaffected. All native ports follow the same rule.
- Reject trace readers that report more bytes than requested, and check
  runtime observation sequence overflow before the model metadata callback.
- Reject debugger branches with a declared policy from a parent recorded with
  unrestricted injection, and parents with partial policy provenance.

### Native port compatibility notes

These native candidate APIs changed shape to match Rust. Update callers and
regenerate any persisted artifacts listed here.

- **Assurance JSON (all ports).** Terminations use Rust Debug spelling
  (`GraphExhausted`, `Panicked`, `MergedJobs`, `Campaign::<termination>`);
  budgets contain only Rust keys (`max_duration_ns` in nanoseconds, snake-case
  `guidance.*`); assessment reasons use Rust wording. Host-only keys such as
  `assurance.*` and `auto.max_candidates` are no longer written. Python
  `from_json` rejects reports exported with the earlier snake-case terminations;
  re-export them. Go additionally drops its "campaign with job errors is failed"
  rule and keeps `nested_jobs`/`campaign` as documented Go-only extensions.
- **Debugger protocol.** `watch_status`, `telemetry_health` and per-sink probe
  health use Rust field order in every port. Go `probe_status` sink health adds
  `abandoned`; Go `telemetry_health` adds the destructor and handle fields (Go and
  TypeScript report `unknown:explicit_disposal_only` for the two destructor
  counts). TypeScript no longer emits
  `telemetry.retained_bytes_are_logical_estimates`.
- **Go diagnostics.** Add `DiagnosticScope.Close` for `defer`, which publishes an
  abandoned turn as incomplete instead of locking the hub. Unscoped
  `Probe`/`Marker` records no longer enter the protocol queue, so `probe_events`
  returns an empty page where it previously returned `unsupported`. Add
  `Diagnostics.Abandon` and a Rust-shaped `SinkHealth` via `Health(sink)`; the
  old `SinkHealth(sink)` method remains. `Dashboard.JSON` key order and nesting
  now match Rust.
- **TypeScript.** `ReferenceHost` takes a fourth `ReferenceHostOptions` argument
  and gains `tick` and `recording`; `close()` now succeeds while a `receive` is
  pending. `CampaignJobHooks` no longer exposes `runJob`/`runGuidedJob`.
  `REFERENCE_SOURCE_ID` changes because identity hashing now length-frames files.
- **Python.** Watches that expire after sampling or evaluation keep their last
  current value, as in Rust. Reference suites are written in Rust's
  `suite_json` layout (`"search":"Some(FailureFound)"`) and no longer record a
  `witness` run parameter. `reference_host.Host` is a context manager.
- **Swift.** Watch paths decode strictly as in Rust (`bool` only `true`/`false`;
  range-checked integers). Watch TTL is checked before selector matching and
  emits an expiry event. `AssuranceReport` gains `validate()` and `outcome`.

## 0.2.0

Broad performance qualification and M7 language/pruning
experiments remain deferred. The companion macro package is explicitly imported.

- Add model adapters, ordered input domains, bounded value codecs and derives.
- Add independent lifecycle monitors and explicit two-machine composition.
- Add offline packaged macro consumers, diagnostics and replay qualification.
- Fix overdue obligation cancellation, restored history checks, macro identifier
  capture, generic parsing and suppressed decoder errors.


- Add opt-in borrowed runtime observations with Off through Trace logging,
  configurable text snapshots, bounded codec payload formatting, explicit sink
  errors, and an example sharing checks with full replay capture. Includes a
  benchmark separating observation/formatting from buffered file writes.

- Harden check composition with append-only `CheckSink` across execution,
  exploration, monitoring, and oracle callbacks. Rust `*_into` overrides must
  accept `&mut CheckSink<'_>` instead of `&mut Vec<Check>`. Vector-returning
  callbacks and the trace format are unchanged.

## 0.1.1

- Complete custom-model regression, failure-shrinking and replay starter.
- Executable crate documentation and isolated packaged/registry consumer checks.
- Manual Rust/platform and binding verification jobs, separate from publication.
- Reusable JavaScript byte-model adapter, SwiftPM model/session API, native C
  header smoke and local binding distributions with file hashes.
- Application adoption runner and measured Playscale production-reducer evidence.
- Fixed-size checksum chunks compatible with the current stable Clippy lint;
  checksum results and the trace format are unchanged.

The Rust engine's algorithms and trace format are unchanged by this readiness
work. New bindings remain experimental ABI 1 interfaces.

## 0.1.0

Initial MIT-licensed `statelessness` crate; Rust import name `stateless`.
