# Changelog

## 0.2.0-rc.1

Experimental prerelease; broad performance qualification and M7 language/pruning
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
