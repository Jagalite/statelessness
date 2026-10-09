# Portable specification and independent native libraries

- [Normative specification and supported profiles](spec/README.md)
- [Native Python library, installation, and application example](python/README.md)
- [Shared JSON corpus and Rust/Python differential runner](conformance/README.md)
- [Qualification evidence and limitations](spec/QUALIFICATION.md)

The Python package is independent: it does not call Rust, cgo, or Wasm.
The Rust qualification adapter calls the existing Rust engine. Both execute the
same finite table fixtures and compare against reviewed expectations.

This initial release covers core checking/observation, bounded BFS, SplitMix64,
and JSON observation-envelope replay. It is not a claim of complete Rust API or
binary audit-format parity. Native applications keep their own state and code;
JSON is the conformance/interchange boundary, not a mandatory runtime architecture.

## Additional native implementations

- [Go module](go/README.md): no cgo or Rust runtime.
- [TypeScript/npm library](typescript/README.md): typed ESM with zero runtime dependencies.
- [SwiftPM library](swift/README.md): native Swift, using the root `Package.swift`.

All implement the same four v1 profiles as Python. Existing `bindings/` packages
remain Rust-backed and separate. The native ports are not wrappers around each
other or a JSON-only replacement for application-owned models.

Run `python scripts/check-native-ports.py --rust PATH_TO_RUST_CORPUS_RUNNER`
after installing the Python library and developer toolchains. It builds source
packages, an npm tarball, independent consumers, and runs the full cross-language
suite against the installed runners. Use `--without-swift` where Swift is absent.
See [native qualification](spec/NATIVE-PORTS-QUALIFICATION.md) for evidence/scope.
