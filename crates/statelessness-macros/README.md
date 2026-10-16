# statelessness-macros

First-party, zero-third-party-dependency companion macros for the `statelessness`
package (library name `stateless`). Rust 1.90+. Current source additions extend the
published 0.2.0 snapshot; their Rust runtime qualification remains pending. See
the repository's `docs/improvement/STATUS.md` for current limits and
`docs/MACRO-STATUS.md` for the historical 0.2.0 qualification scope.

Exports: `#[model]`, `input_domain!`, `TraceEncode`, `TraceDecode`, `Inspect`,
`ModelCodec`, and
`macro_build_id!`. Import the companion explicitly; it is not a default core
dependency. Current model adapters require `stateless::modeling::MACRO_API_V2`;
codec derives retain `MACRO_API_V1`. Pair this source with a runtime that exports
the required protocol; the published 0.2.0 runtime does not provide the new V2
adapter helpers.

The modeling guide in `docs/MACROS.md` covers ordinary Rust equivalents, syntax,
wire bytes, reader limits, renamed imports, provenance and migration. Engine
models, reducers and custom trait implementations remain application-owned.
