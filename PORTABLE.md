# Portable specification and independent native libraries

- [Normative specification and supported profiles](spec/README.md)
- [Native Python library, installation, and application example](python/README.md)
- [Shared JSON corpus and Rust/Python differential runner](conformance/README.md)
- [Qualification evidence and limitations](spec/QUALIFICATION.md)
- [Source capability/version matrix and distribution status](docs/CAPABILITIES.md)

The Python package is independent: it does not call Rust, cgo, or Wasm.
The Rust qualification adapter calls the existing Rust engine. Both execute the
same finite table fixtures and compare against reviewed expectations.

The current source profiles cover core checking/observation, bounded BFS, SplitMix64,
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

All four native packages declare source version 0.1.0 and specification 1.0.0;
these are independent of the Rust core's 0.2.0 package and binary trace version 1.
Use the linked local installation instructions. Source package builds and conformance
results do not establish PyPI/npm/Go module/Swift registry publication. The JSON
trace profile is an observation envelope, not full `.sttrace` audit interoperability.

Run `python scripts/check-native-ports.py --rust PATH_TO_RUST_CORPUS_RUNNER`
after installing the Python library and developer toolchains. It builds source
packages, an npm tarball, independent consumers, and runs the full cross-language
suite against the installed runners. Use `--without-swift` where Swift is absent.
See [native qualification](spec/NATIVE-PORTS-QUALIFICATION.md) for evidence/scope.

## Candidate oracle and paired profile

The [paired-v1 candidate](spec/paired-v1.md) defines separate projected observation
and oracle obligations. Current-source implementations exist in Rust, native Python
and native TypeScript, using the same hand-authored bounded queue corpus and named
negative cases. Go and Swift do not claim this candidate. `PROFILES` remains the
original four; Python/TypeScript list `paired-v1` under `EXPERIMENTAL_PROFILES`.

Python and TypeScript runtime tests and schema checking are distinct from Rust
runtime qualification and independent review. This task runs Rust compilation only
through Kyoto, so authored/compiled Rust cases do not promote the candidate or
establish cross-language runtime conformance. A failed or skipped schema/runtime
job must remain visible. The [capability matrix](docs/CAPABILITIES.md) tracks this
boundary separately from historical native-port receipts.
