# Portable specification and native Python library

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
