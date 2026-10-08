# statelessness-macros

First-party, zero-third-party-dependency companion macros for the `statelessness`
package (library name `stateless`). Rust 1.90+. This source is a pre-release
implementation; see the repository's `docs/MACRO-STATUS.md` for qualification limits.

Exports: `#[model]`, `input_domain!`, `TraceEncode`, `TraceDecode`, and
`macro_build_id!`. Import the companion explicitly; it is not a default core
 dependency. Generated code requires `stateless::modeling::MACRO_API_V1`.

The modeling guide in `docs/MACROS.md` covers ordinary Rust equivalents, syntax,
wire bytes, reader limits, renamed imports, provenance and migration. Engine
models, reducers and custom trait implementations remain application-owned.
